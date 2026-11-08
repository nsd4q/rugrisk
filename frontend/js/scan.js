import { sb, configOk, API_URL } from "./supabase.js";
import { setWatch } from "./common.js";

const ADDR_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const LEVELS = new Set(["low", "medium", "high", "critical"]);
const STATUSES = new Set(["pass", "warn", "fail", "unknown"]);
const KINDS = new Set(["wallet", "lp", "burn"]);
const ICON = { pass: "✔", warn: "⚠", fail: "✖", unknown: "?" };
const ARC_LEN = 157.08;

// Keep in sync with backend/scanner/scoring.py
const MAX_POINTS = {
  mint_authority: 25, freeze_authority: 20, lp_status: 20, honeypot_sim: 40, top10_concentration: 20,
  largest_holder: 10, token_extensions: 30, liquidity: 10, metadata_mutable: 5, token_age: 5,
};
const LABELS = {
  mint_authority: "Mint authority",
  freeze_authority: "Freeze authority",
  token_extensions: "Token extensions",
  top10_concentration: "Top-10 holder concentration",
  largest_holder: "Largest holder",
  liquidity: "Liquidity",
  lp_status: "Liquidity pool (LP)",
  honeypot_sim: "Sell simulation (honeypot)",
  token_age: "Token age",
  metadata_mutable: "Metadata",
};
const ORDER = Object.keys(LABELS);

const $ = (id) => document.getElementById(id);
const msg = $("msg");
const result = $("result");
const actions = $("actions");
const scanBtn = $("scan-btn");

if (!configOk) {
  msg.textContent = "Setup needed: copy js/config.example.js to js/config.js and add your Supabase URL and publishable key";
  msg.className = "msg error";
  throw new Error("Missing publishable key");
}

let currentAddress = null;
let watched = false;
let autoScan = new URLSearchParams(location.search).get("scan") === "1";   // arrived via a screener "Scan" link
const STALE_MIN = 10;   // with auto-scan, a stored report older than this is replaced by a fresh scan

function consumeAutoScan() {
  autoScan = false;
  if (currentAddress) history.replaceState(null, "", "?a=" + encodeURIComponent(currentAddress));
}

// ---- watchlist button
const watchBtn = $("watch-btn");
const watchMsg = $("watch-msg");
function renderWatch() {
  watchBtn.textContent = watched ? "★ On your watchlist (click to remove)" : "☆ Add to watchlist";
  watchBtn.classList.toggle("on", watched);
}
watchBtn.addEventListener("click", async () => {
  if (!currentAddress) return;
  watchBtn.disabled = true;
  watchMsg.textContent = "";
  const r = await setWatch(currentAddress, !watched);
  watchBtn.disabled = false;
  if (!r.ok) { watchMsg.textContent = r.error; return; }
  watched = !watched;
  renderWatch();
  watchMsg.textContent = watched ? "Added. You can find it on the Watchlist page." : "Removed from your watchlist.";
});


// ---------------------------------------------------------------- helpers
// All untrusted text goes through textContent.
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return Math.floor(s) + "s ago";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  return Math.round(s / 3600) + "h ago";
}
function money(v) {
  v = Number(v);
  if (!Number.isFinite(v)) return "—";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
  return "$" + v.toFixed(0);
}
function short(a) { return a.length < 12 ? a : a.slice(0, 4) + "…" + a.slice(-4); }

function showMsg(text, isError = false) {
  result.hidden = true;
  msg.hidden = false;
  msg.textContent = text;
  msg.className = "msg" + (isError ? " error" : "");
}
function setActions(visible, label) {
  actions.hidden = !visible;
  if (label) scanBtn.textContent = label;
  scanBtn.disabled = false;
  $("note").textContent = "";
}

// ---------------------------------------------------------------- rendering
function renderHeader(address, tok) {
  $("tok-title").textContent = tok ? `${tok.symbol || "?"}  ${tok.name || ""}`.trim() : "Unknown token";
  $("tok-addr").textContent = address;

  const meta = [];
  if (tok?.dex) meta.push(tok.dex);
  if (tok?.liquidity_usd != null) meta.push("Liquidity " + money(tok.liquidity_usd));
  $("tok-meta").textContent = meta.join(" · ");

  const links = $("tok-links");
  links.replaceChildren();
  const add = (label, href) => {
    const a = el("a", label);
    a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
    links.appendChild(a);
  };
  if (tok && ADDR_RE.test(tok.pair_address || "")) add("DexScreener ↗", "https://dexscreener.com/solana/" + tok.pair_address);
  add("Solscan ↗", "https://solscan.io/token/" + address);
}

function renderGauge(score, level) {
  const lvl = LEVELS.has(level) ? level : "low";
  const arc = $("gauge-arc");
  arc.style.strokeDashoffset = String(ARC_LEN * (1 - Math.min(100, Math.max(0, score)) / 100));
  arc.setAttribute("class", "gauge-arc " + lvl);
  $("gauge-score").textContent = String(score);
  const lv = $("gauge-level");
  lv.textContent = lvl.toUpperCase();
  lv.className = "gauge-level " + lvl;
}

function renderChecks(checks) {
  const list = $("checks");
  list.replaceChildren();
  const sorted = [...checks].sort((a, b) => ORDER.indexOf(a.id) - ORDER.indexOf(b.id));

  for (const c of sorted) {
    const status = STATUSES.has(c.status) ? c.status : "unknown";
    const li = el("li", null, "check " + status);
    li.appendChild(el("span", ICON[status], "ic"));

    const body = el("div", null, "body");
    body.appendChild(el("div", LABELS[c.id] || c.id, "t"));
    body.appendChild(el("div", c.detail || "", "d"));
    li.appendChild(body);

    if (c.points > 0) li.appendChild(el("span", "+" + c.points, "pts"));
    list.appendChild(li);
  }

  const total = Object.values(MAX_POINTS).reduce((a, b) => a + b, 0);
  const verified = checks.reduce((s, c) => s + (c.status !== "unknown" ? MAX_POINTS[c.id] || 0 : 0), 0);
  const unknown = checks.filter((c) => c.status === "unknown").map((c) => LABELS[c.id] || c.id);
  let text = `Checked ${Math.round((verified / total) * 100)}% of the scoring model.`;
  if (unknown.length) text += ` Not verified: ${unknown.join(", ")}.`;
  $("coverage").textContent = text;
}

function renderHistory(scans) {
  const box = $("history");
  box.replaceChildren();
  if (scans.length < 2) { box.hidden = true; return; }
  box.hidden = false;
  box.appendChild(el("h3", "Scan history"));
  for (const s of scans) {
    box.appendChild(el("div", `${s.score}/100 ${String(s.level).toUpperCase()} · ${ago(s.created_at)}`));
  }
}

function renderHolders(holders) {
  const card = $("holders-card");
  const box = $("holders");
  box.replaceChildren();
  if (!holders || !holders.length) { card.hidden = true; return; }
  card.hidden = false;
  for (const h of holders) {
    const kind = KINDS.has(h.kind) ? h.kind : "wallet";
    const row = el("div", null, "hrow");
    row.appendChild(el("span", Number(h.pct).toFixed(2) + "%", "pct"));
    row.appendChild(el("span", kind === "lp" ? "pool" : kind, "kind " + kind));
    if (ADDR_RE.test(h.owner || "")) {
      const a = el("a", short(h.owner));
      a.href = "https://solscan.io/account/" + h.owner;
      a.target = "_blank"; a.rel = "noopener noreferrer";
      row.appendChild(a);
    } else {
      row.appendChild(el("span", "?"));
    }
    box.appendChild(row);
  }
}

// ---------------------------------------------------------------- free lookup (database)
async function lookup(address) {
  currentAddress = address;
  watched = false;
  watchMsg.textContent = "";
  setActions(false);
  showMsg("Looking up…");

  const [tokRes, scanRes] = await Promise.all([
    sb.from("tokens")
      .select("symbol,name,dex,pair_address,liquidity_usd,last_scanned_at,watchlisted_at")
      .eq("address", address).maybeSingle(),
    sb.from("token_scans")
      .select("score,level,checks,created_at")
      .eq("token_address", address)
      .order("created_at", { ascending: false }).limit(5),
  ]);

  if (tokRes.error || scanRes.error) {
    showMsg("Error: " + (tokRes.error || scanRes.error).message, true);
    return;
  }

  const tok = tokRes.data;
  const scans = scanRes.data || [];
  watched = Boolean(tok?.watchlisted_at);

  if (!tok || !scans.length) {
    showMsg(!tok
      ? "We haven't scanned this token yet. Run a scan to get its risk report."
      : "No scan result stored for this token yet. Run a scan to get its risk report.");
    setActions(true, "Scan now");
    if (autoScan) { consumeAutoScan(); apiScan(); }
    return;
  }

  const latest = scans[0];
  const storedAgeMin = (Date.now() - new Date(latest.created_at).getTime()) / 60000;
  if (autoScan) {
    const stale = storedAgeMin > STALE_MIN;
    consumeAutoScan();
    if (stale) { setActions(true, "Re-scan now"); apiScan(); return; }
  }
  msg.hidden = true;
  result.hidden = false;
  renderHeader(address, tok);
  renderGauge(latest.score, latest.level);
  renderChecks(Array.isArray(latest.checks) ? latest.checks : []);
  renderHolders(null);
  renderHistory(scans);
  renderWatch();
  setActions(true, "Re-scan now");
  $("note").textContent = `Last scanned ${ago(latest.created_at)}.`;
}

// ---------------------------------------------------------------- on-demand scan (local API)
async function apiScan() {
  const address = currentAddress;
  if (!address) return;

  scanBtn.disabled = true;
  showMsg("Scanning… this usually takes 5-15 seconds.");

  let res, body = null;
  try {
    res = await fetch(`${API_URL}/scan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    });
    try { body = await res.json(); } catch { /* non-JSON error */ }
  } catch {
    showMsg("Can't reach the local scan service. Is RugRisk running? (python scripts/run_local.py)", true);
    scanBtn.disabled = false;
    return;
  }

  if (!res.ok) {
    showMsg(typeof body?.detail === "string" ? body.detail : `Scan failed (${res.status}).`, true);
    scanBtn.disabled = false;
    return;
  }

  const m = body.market || {};
  msg.hidden = true;
  result.hidden = false;
  renderHeader(address, {
    symbol: m.symbol, name: m.name, dex: m.dex,
    liquidity_usd: m.liquidity_usd, pair_address: m.pair_address,
  });
  renderGauge(body.score, body.level);
  renderChecks(Array.isArray(body.checks) ? body.checks : []);
  renderHolders(body.top_holders);
  $("history").hidden = true;
  renderWatch();

  scanBtn.disabled = false;
  scanBtn.textContent = "Re-scan now";
  $("note").textContent = body.cached ? "Recent result reused (scanned within the last few minutes)." : "";
}

// ---------------------------------------------------------------- wiring
scanBtn.addEventListener("click", apiScan);

$("scan-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const address = $("addr").value.trim();
  if (!ADDR_RE.test(address)) {
    setActions(false);
    showMsg("That doesn't look like a valid Solana address.", true);
    return;
  }
  autoScan = false;
  history.replaceState(null, "", "?a=" + encodeURIComponent(address));
  lookup(address);
});

const initial = new URLSearchParams(location.search).get("a");
if (initial && ADDR_RE.test(initial)) {
  $("addr").value = initial;
  lookup(initial);
}
