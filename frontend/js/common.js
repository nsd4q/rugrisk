// Shared by the Screener and Watchlist pages: formatting, safe DOM helpers, the token table,
// and the watchlist call to the local API. (All untrusted text goes through textContent.)
import { API_URL } from "./supabase.js";

export const ADDR_RE = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

// Named columns only: keeps Supabase egress small.
export const COLS = [
  "address", "symbol", "name", "pair_address", "pair_created_at",
  "price_usd", "mcap", "liquidity_usd", "volume_1h", "buys_1h", "sells_1h",
  "price_change_5m", "price_change_1h", "price_change_24h", "watchlisted_at",
].join(",");

// ---------------------------------------------------------------- formatting
export const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

export function money(v) {
  v = num(v);
  if (v === null || Number.isNaN(v)) return "—";
  if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
  return "$" + v.toFixed(0);
}

export function price(v) {
  v = num(v);
  if (v === null || Number.isNaN(v)) return "—";
  if (v >= 1) return "$" + v.toFixed(2);
  if (v >= 0.001) return "$" + v.toFixed(5);
  return "$" + v.toFixed(10).replace(/0+$/, "");
}

export function age(iso) {
  if (!iso) return "—";
  const m = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 60) return Math.floor(m) + "m";
  if (m < 1440) return Math.floor(m / 60) + "h " + Math.floor(m % 60) + "m";
  return Math.floor(m / 1440) + "d " + Math.floor((m % 1440) / 60) + "h";
}

// ---------------------------------------------------------------- DOM helpers
export function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
const cell = (text, cls) => el("td", text, cls);

function pctCell(v) {
  v = num(v);
  if (v === null || Number.isNaN(v)) return cell("—", "r muted");
  return cell((v > 0 ? "+" : "") + v.toFixed(1) + "%", "r " + (v > 0 ? "pos" : v < 0 ? "neg" : ""));
}

// ---------------------------------------------------------------- watchlist API (local, no login)
export async function setWatch(address, on) {
  let res, body = null;
  try {
    res = await fetch(on ? `${API_URL}/watchlist` : `${API_URL}/watchlist/${encodeURIComponent(address)}`, {
      method: on ? "POST" : "DELETE",
      headers: on ? { "Content-Type": "application/json" } : {},
      body: on ? JSON.stringify({ address }) : undefined,
    });
    try { body = await res.json(); } catch { /* non-JSON error body */ }
  } catch {
    return { ok: false, error: "Can't reach the local RugRisk service. Is it running? (python scripts/run_local.py)" };
  }
  if (!res.ok) return { ok: false, error: typeof body?.detail === "string" ? body.detail : `Request failed (${res.status})` };
  return { ok: true };
}

export function setStar(btn, on) {
  btn.textContent = on ? "★" : "☆";
  btn.classList.toggle("on", on);
  btn.title = on ? "On your watchlist (click to remove)" : "Add to watchlist";
  btn.setAttribute("aria-pressed", String(on));
}

// ---------------------------------------------------------------- token table
function tokenCell(t) {
  const td = el("td");
  const wrap = el("div", null, "tok");

  const sym = t.symbol || "?";
  if (ADDR_RE.test(t.pair_address || "")) {
    const a = el("a", sym, "sym");
    a.href = "https://dexscreener.com/solana/" + t.pair_address;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    wrap.appendChild(a);
  } else {
    wrap.appendChild(el("span", sym, "sym"));
  }
  wrap.appendChild(el("span", t.name || "", "nm"));

  const btn = el("button", "copy", "copy");
  btn.title = t.address;
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(t.address);
      btn.textContent = "ok";
    } catch {
      btn.textContent = "err";
    }
    setTimeout(() => (btn.textContent = "copy"), 1200);
  });
  wrap.appendChild(btn);

  td.appendChild(wrap);
  return td;
}

export const TABLE_COLS = 12;

/** Render token rows. opts: { emptyText, onWatch(token, nowWatched, tr), onError(message) } */
export function renderRows(tbody, rows, opts = {}) {
  tbody.replaceChildren();
  if (!rows.length) {
    const tr = el("tr");
    const td = el("td", opts.emptyText || "No tokens match these filters.", "empty");
    td.colSpan = TABLE_COLS;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const t of rows) {
    const tr = el("tr");

    const starTd = el("td", null, "starcell");
    const star = el("button", "", "star");
    setStar(star, Boolean(t.watchlisted_at));
    star.addEventListener("click", async () => {
      const want = !star.classList.contains("on");
      star.disabled = true;
      const r = await setWatch(t.address, want);
      star.disabled = false;
      if (!r.ok) { opts.onError?.(r.error); return; }
      setStar(star, want);
      t.watchlisted_at = want ? new Date().toISOString() : null;
      opts.onWatch?.(t, want, tr);
    });
    starTd.appendChild(star);
    tr.appendChild(starTd);

    tr.appendChild(tokenCell(t));
    tr.appendChild(cell(age(t.pair_created_at)));
    tr.appendChild(cell(price(t.price_usd), "r"));
    tr.appendChild(pctCell(t.price_change_5m));
    tr.appendChild(pctCell(t.price_change_1h));
    tr.appendChild(pctCell(t.price_change_24h));
    tr.appendChild(cell(money(t.liquidity_usd), "r"));
    tr.appendChild(cell(money(t.volume_1h), "r"));
    tr.appendChild(cell((t.buys_1h ?? "—") + " / " + (t.sells_1h ?? "—"), "r"));
    tr.appendChild(cell(money(t.mcap), "r"));

    const scanTd = el("td");
    const a = el("a", "Scan", "scanlink");
    a.href = "scan.html?a=" + encodeURIComponent(t.address) + "&scan=1";
    a.title = "Run a risk scan on this token";
    scanTd.appendChild(a);
    tr.appendChild(scanTd);

    tbody.appendChild(tr);
  }
}

// ---------------------------------------------------------------- polling helper
/** Calls load() every `ms` while the tab is visible; catches up when you come back. */
export function startPolling(load, isOn, ms = 30000) {
  let last = Date.now();
  const run = async () => { await load(); last = Date.now(); };
  let timer = null;
  const schedule = () => {
    clearInterval(timer);
    if (isOn()) timer = setInterval(() => { if (!document.hidden) run(); }, ms);
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && isOn() && Date.now() - last > ms) run();
  });
  return { schedule, run };
}
