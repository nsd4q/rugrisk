import { sb, configOk } from "./supabase.js";
import { COLS, renderRows, startPolling } from "./common.js";

// ---- Free-tier egress guards: explicit columns, small limit, slow polling ----
const LIMIT = 50;
const STORE_KEY = "rr_filters_v2";   // v2: "age" now means MIN age (v1 meant max age)

const SORTS = {
  new:  "pair_created_at",
  vol:  "volume_1h",
  liq:  "liquidity_usd",
  mcap: "mcap",
  chg:  "price_change_1h",
};

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const tbody = $("rows");

if (!configOk) {
  statusEl.textContent = "Setup needed: copy js/config.example.js to js/config.js and add your Supabase URL and publishable key";
  statusEl.className = "error";
  throw new Error("Missing publishable key");
}

// ---------------------------------------------------------------- filters
const FIELDS = {
  age: "f-age", mcapMin: "f-mcap-min", mcapMax: "f-mcap-max",
  liq: "f-liq", vol: "f-vol", buy: "f-buy", sort: "f-sort",
};

function readFilters() {
  const f = {};
  for (const [k, id] of Object.entries(FIELDS)) f[k] = $(id).value;
  return f;
}

function writeFilters(f) {
  for (const [k, id] of Object.entries(FIELDS)) $(id).value = f[k] ?? "";
  if (!$("f-sort").value) $("f-sort").value = "new";
}

function saveFilters() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(readFilters())); } catch { /* storage may be blocked */ }
}

function loadSaved() {
  try {
    localStorage.removeItem("rr_filters");   // old format with different meaning: discard
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) writeFilters(JSON.parse(raw));
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- data
let inFlight = false;

async function load() {
  if (inFlight) return;
  inFlight = true;
  const f = readFilters();

  let q = sb.from("tokens").select(COLS).limit(LIMIT);
  // MIN age: the pair must have been created at least N minutes ago
  if (f.age)     q = q.lte("pair_created_at", new Date(Date.now() - Number(f.age) * 60000).toISOString());
  if (f.mcapMin) q = q.gte("mcap", Number(f.mcapMin));
  if (f.mcapMax) q = q.lte("mcap", Number(f.mcapMax));
  if (f.liq)     q = q.gte("liquidity_usd", Number(f.liq));
  if (f.vol)     q = q.gte("volume_1h", Number(f.vol));
  if (f.buy)     q = q.gte("buy_ratio_1h", Number(f.buy) / 100);
  q = q.order(SORTS[f.sort] || SORTS.new, { ascending: false, nullsFirst: false });

  const { data, error } = await q;
  inFlight = false;

  if (error) {
    statusEl.textContent = "Error: " + error.message;
    statusEl.className = "error";
    return;
  }
  statusEl.className = "";
  statusEl.textContent = `${data.length} tokens · updated ${new Date().toLocaleTimeString()}`;
  renderRows(tbody, data, {
    onError: (msg) => { statusEl.textContent = "Couldn't update the watchlist: " + msg; statusEl.className = "error"; },
  });
}

// ---------------------------------------------------------------- wiring
let debounce;
for (const id of Object.values(FIELDS)) {
  $(id).addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { saveFilters(); load(); }, 400);
  });
}

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => {
    writeFilters(JSON.parse(chip.dataset.preset));
    saveFilters();
    load();
  });
}

$("refresh").addEventListener("click", load);

const poll = startPolling(load, () => $("auto").checked);
$("auto").addEventListener("change", poll.schedule);

loadSaved();
poll.schedule();
load();
