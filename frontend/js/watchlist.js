import { sb, configOk } from "./supabase.js";
import { COLS, renderRows, startPolling } from "./common.js";

const LIMIT = 100;
const EMPTY = "Your watchlist is empty. Click the ☆ next to a token in the Screener, or use “Add to watchlist” on the Scan page.";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const tbody = $("rows");

if (!configOk) {
  statusEl.textContent = "Setup needed: copy js/config.example.js to js/config.js and add your Supabase URL and publishable key";
  statusEl.className = "error";
  throw new Error("Missing publishable key");
}

let inFlight = false;
let count = 0;

function setStatus(extra = "") {
  statusEl.className = "";
  statusEl.textContent = `${count} starred token${count === 1 ? "" : "s"}${extra}`;
}

async function load() {
  if (inFlight) return;
  inFlight = true;
  const { data, error } = await sb.from("tokens").select(COLS)
    .not("watchlisted_at", "is", null)
    .order("watchlisted_at", { ascending: false })
    .limit(LIMIT);
  inFlight = false;

  if (error) {
    statusEl.textContent = "Error: " + error.message;
    statusEl.className = "error";
    return;
  }
  count = data.length;
  setStatus(` · updated ${new Date().toLocaleTimeString()}`);
  renderRows(tbody, data, {
    emptyText: EMPTY,
    onWatch: (t, nowWatched, tr) => {
      if (nowWatched) return;
      tr.remove();                      // unstarred: drop the row straight away
      count -= 1;
      setStatus();
      if (count <= 0) renderRows(tbody, [], { emptyText: EMPTY });
    },
    onError: (msg) => { statusEl.textContent = "Couldn't update the watchlist: " + msg; statusEl.className = "error"; },
  });
}

$("refresh").addEventListener("click", load);
const poll = startPolling(load, () => $("auto").checked);
$("auto").addEventListener("change", poll.schedule);
poll.schedule();
load();
