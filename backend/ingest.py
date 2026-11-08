"""
MemeScope ingest worker (local MVP, Solana).

Two loops in one process:
  * ingest  - every ~10s: GeckoTerminal "new pools" -> insert unseen tokens
  * refresh - every ~20s: DexScreener batch lookup -> update market data for
              tokens created in the last 24h

Run:  python ingest.py
Stop: Ctrl-C
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SECRET_KEY = os.environ["SUPABASE_SECRET_KEY"]

MIN_LIQ = float(os.getenv("MIN_LIQUIDITY_USD", "1000"))
INGEST_EVERY = int(os.getenv("INGEST_INTERVAL_S", "10"))
REFRESH_EVERY = int(os.getenv("REFRESH_INTERVAL_S", "20"))
REFRESH_MAX_TOKENS = int(os.getenv("REFRESH_MAX_TOKENS", "150"))      # tokens younger than 24h
REFRESH_OLD_PER_LOOP = int(os.getenv("REFRESH_OLD_PER_LOOP", "60"))    # older tokens, stalest first
WATCH_REFRESH_MAX = 100                                                # starred tokens, any age

GECKO = "https://api.geckoterminal.com/api/v2"
DEXSCREENER = "https://api.dexscreener.com"
SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

http = requests.Session()

# New-style sb_secret_ keys go in `apikey` only; legacy JWT keys also need Bearer.
SB_HEADERS = {"apikey": SECRET_KEY}
if SECRET_KEY.startswith("eyJ"):
    SB_HEADERS["Authorization"] = f"Bearer {SECRET_KEY}"

seen: set[str] = set()  # addresses already inserted this session


# ---------------------------------------------------------------- helpers
def num(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def get_json(url, **kw):
    """GET with one retry on 429."""
    for _ in range(2):
        r = http.get(url, timeout=15, **kw)
        if r.status_code == 429:
            try:
                wait = int(r.headers.get("Retry-After", 20))
            except ValueError:
                wait = 20
            log.warning("rate limited by %s, sleeping %ss", url.split("/")[2], min(wait, 60))
            time.sleep(min(wait, 60))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("still rate limited")


def sb_upsert(table, rows, on_conflict):
    """Batch upsert via PostgREST. Every row in a call must have the same keys."""
    for i in range(0, len(rows), 100):
        chunk = rows[i : i + 100]
        r = http.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            headers={
                **SB_HEADERS,
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=chunk,
            timeout=20,
        )
        if r.status_code >= 300:
            log.error("upsert %s failed %s: %s", table, r.status_code, r.text[:300])


# ---------------------------------------------------------------- ingest
def ingest_once():
    body = get_json(
        f"{GECKO}/networks/solana/new_pools",
        params={"include": "base_token,dex", "page": 1},
        headers={"Accept": "application/json;version=20230302"},
    )
    included = {(i["type"], i["id"]): i.get("attributes", {}) for i in body.get("included", [])}

    rows, by_addr, low_liq = [], {}, 0
    for p in body.get("data", []):
        try:
            a = p["attributes"]
            base_id = p["relationships"]["base_token"]["data"]["id"]  # "solana_<addr>"
            addr = base_id.split("_", 1)[1]
            dex_id = (p["relationships"].get("dex") or {}).get("data", {}).get("id")
        except (KeyError, IndexError, TypeError):
            continue

        # a bad address would make the whole batch fail the DB check constraint
        if not SOL_ADDR.match(addr) or addr in seen or addr in by_addr:
            continue

        liq = num(a.get("reserve_in_usd"))
        if liq is None or liq < MIN_LIQ:
            low_liq += 1
            continue

        tok = included.get(("token", base_id), {})
        vol = a.get("volume_usd") or {}
        chg = a.get("price_change_percentage") or {}
        h1 = (a.get("transactions") or {}).get("h1") or {}
        fdv = num(a.get("fdv_usd"))
        img = tok.get("image_url")

        by_addr[addr] = {
            "address": addr,
            "chain": "solana",
            "pair_address": a.get("address"),
            "symbol": tok.get("symbol"),
            "name": tok.get("name"),
            "dex": dex_id,
            "image_url": img if isinstance(img, str) and img.startswith("http") else None,
            "pair_created_at": a.get("pool_created_at"),
            "price_usd": num(a.get("base_token_price_usd")),
            "mcap": num(a.get("market_cap_usd")) or fdv,
            "fdv": fdv,
            "liquidity_usd": liq,
            "volume_5m": num(vol.get("m5")),
            "volume_1h": num(vol.get("h1")),
            "volume_24h": num(vol.get("h24")),
            "buys_1h": h1.get("buys"),
            "sells_1h": h1.get("sells"),
            "price_change_5m": num(chg.get("m5")),
            "price_change_1h": num(chg.get("h1")),
            "price_change_24h": num(chg.get("h24")),
        }

    rows = list(by_addr.values())
    sb_upsert("tokens", rows, "address")
    seen.update(by_addr)
    log.info("ingest: +%d new, %d skipped (liq < $%d)", len(rows), low_liq, MIN_LIQ)


# ---------------------------------------------------------------- refresh
def _addresses(params: dict, limit: int) -> list[str]:
    r = http.get(
        f"{SUPABASE_URL}/rest/v1/tokens",
        params={"select": "address", "limit": limit, **params},
        headers=SB_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return [row["address"] for row in r.json()]


def pick_refresh_addresses() -> tuple[list[str], set[str]]:
    """Which tokens to refresh this round.
      1. watchlisted tokens (any age): always kept current
      2. tokens younger than 24h: the busy part of the screener
      3. a rotating slice of OLDER tokens, stalest first, so the 24h / 1-week filters show
         current prices without hammering DexScreener
    Returns (ordered unique addresses, the subset from group 3 where 'not found' means dead)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    watched = _addresses({"watchlisted_at": "not.is.null", "order": "watchlisted_at.desc"}, WATCH_REFRESH_MAX)
    recent = _addresses({"pair_created_at": f"gte.{since}", "order": "pair_created_at.desc"}, REFRESH_MAX_TOKENS)
    old = []
    if REFRESH_OLD_PER_LOOP > 0:
        old = _addresses({"pair_created_at": f"lt.{since}", "watchlisted_at": "is.null",
                          "order": "updated_at.asc"}, REFRESH_OLD_PER_LOOP)
    ordered, seen = [], set()
    for a in watched + recent + old:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered, set(old) - set(watched) - set(recent)


def row_from_pair(addr: str, p: dict, liq: float) -> dict:
    tx = (p.get("txns") or {}).get("h1") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    fdv = num(p.get("fdv"))
    return {
        "address": addr,
        "pair_address": p.get("pairAddress"),
        "price_usd": num(p.get("priceUsd")),
        "mcap": num(p.get("marketCap")) or fdv,
        "fdv": fdv,
        "liquidity_usd": liq,
        "volume_5m": num(vol.get("m5")),
        "volume_1h": num(vol.get("h1")),
        "volume_24h": num(vol.get("h24")),
        "buys_1h": tx.get("buys"),
        "sells_1h": tx.get("sells"),
        "price_change_5m": num(chg.get("m5")),
        "price_change_1h": num(chg.get("h1")),
        "price_change_24h": num(chg.get("h24")),
    }


def refresh_once():
    addrs, old_only = pick_refresh_addresses()
    if not addrs:
        log.info("refresh: nothing to refresh yet")
        return

    rows, dead = [], []
    for i in range(0, len(addrs), 30):  # DexScreener allows 30 addresses per call
        batch = addrs[i : i + 30]
        pairs = get_json(f"{DEXSCREENER}/tokens/v1/solana/{','.join(batch)}")

        best = {}  # highest-liquidity pair per base token
        for p in pairs or []:
            addr = (p.get("baseToken") or {}).get("address")
            liq = num((p.get("liquidity") or {}).get("usd")) or 0
            if addr in batch and (addr not in best or liq > best[addr][0]):
                best[addr] = (liq, p)

        rows.extend(row_from_pair(addr, p, liq) for addr, (liq, p) in best.items())

        # An OLD token that DexScreener no longer lists has no market any more: record that
        # (liquidity 0) so it stops looking alive, and so it rotates to the back of the queue.
        # Never done for new tokens: DexScreener can lag a few minutes behind a fresh pool.
        dead.extend({"address": a, "liquidity_usd": 0, "volume_5m": 0, "volume_1h": 0}
                    for a in batch if a not in best and a in old_only)
        time.sleep(0.3)

    sb_upsert("tokens", rows, "address")
    sb_upsert("tokens", dead, "address")   # separate call: bulk upserts need identical keys
    log.info("refresh: updated %d, marked %d dead, of %d tokens", len(rows), len(dead), len(addrs))


def heartbeat():
    sb_upsert(
        "worker_state",
        [{"key": "ingest", "value": {"seen_this_session": len(seen)},
          "updated_at": datetime.now(timezone.utc).isoformat()}],
        "key",
    )


# ---------------------------------------------------------------- main
def safe(fn):
    try:
        fn()
    except Exception as e:  # keep the worker alive on any single failure
        log.error("%s failed: %s", fn.__name__, e)


def main():
    log.info("starting; min liquidity $%d, ingest %ss, refresh %ss", MIN_LIQ, INGEST_EVERY, REFRESH_EVERY)
    next_ing = next_ref = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_ing:
                safe(ingest_once)
                next_ing = time.monotonic() + INGEST_EVERY
            if now >= next_ref:
                safe(refresh_once)
                safe(heartbeat)
                next_ref = time.monotonic() + REFRESH_EVERY
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
