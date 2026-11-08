"""Write scan results to Supabase (service/secret key, backend only)."""
import os
from datetime import datetime, timezone

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SECRET_KEY", "")

HEADERS = {"apikey": KEY}
if KEY.startswith("eyJ"):  # legacy JWT-style key also needs Bearer
    HEADERS["Authorization"] = f"Bearer {KEY}"

http = requests.Session()
_JSON = {"Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def save_scan(result: dict) -> bool:
    """Update the token's risk summary and append a history row.
    Returns False if the token isn't in the `tokens` table (screener doesn't track it)."""
    addr = result["address"]
    r = http.patch(
        f"{URL}/rest/v1/tokens",
        params={"address": f"eq.{addr}", "select": "address"},
        headers={**HEADERS, **_JSON, "Prefer": "return=representation"},
        json={"risk_score": result["score"], "risk_level": result["level"], "last_scanned_at": _now()},
        timeout=15,
    )
    r.raise_for_status()
    if not r.json():
        return False

    checks = [{k: c[k] for k in ("id", "status", "points", "detail")} for c in result["checks"]]
    r2 = http.post(
        f"{URL}/rest/v1/token_scans",
        headers={**HEADERS, **_JSON, "Prefer": "return=minimal"},
        json={"token_address": addr, "score": result["score"], "level": result["level"], "checks": checks},
        timeout=15,
    )
    r2.raise_for_status()
    return True


def mark_scanned(addr: str):
    """Stop retrying a token that keeps failing (leaves risk_score empty)."""
    http.patch(
        f"{URL}/rest/v1/tokens",
        params={"address": f"eq.{addr}"},
        headers={**HEADERS, **_JSON, "Prefer": "return=minimal"},
        json={"last_scanned_at": _now()},
        timeout=15,
    ).raise_for_status()


def fetch_unscanned(limit: int = 5) -> list[str]:
    r = http.get(
        f"{URL}/rest/v1/tokens",
        params={
            "select": "address",
            "last_scanned_at": "is.null",
            "order": "first_seen_at.asc",
            "limit": limit,
        },
        headers=HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return [row["address"] for row in r.json()]


def _market_row(address: str, m: dict) -> dict:
    """tokens-table row built from a DexScreener market summary (see data.best_pair)."""
    from datetime import timedelta

    try:
        price = float(m["price_usd"]) if m.get("price_usd") else None
    except (TypeError, ValueError):
        price = None

    row = {
        "address": address, "chain": "solana", "pair_address": m["pair_address"],
        "symbol": m.get("symbol"), "name": m.get("name"), "dex": m.get("dex"),
        "liquidity_usd": m.get("liquidity_usd"), "price_usd": price,
    }
    if m.get("age_min") is not None:
        row["pair_created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=m["age_min"])).isoformat()
    return row


def _insert_ignore(row: dict):
    r = http.post(
        f"{URL}/rest/v1/tokens",
        params={"on_conflict": "address"},
        headers={**HEADERS, **_JSON, "Prefer": "resolution=ignore-duplicates,return=minimal"},
        json=[row],
        timeout=15,
    )
    r.raise_for_status()


def ensure_token(result: dict) -> bool:
    """Insert the token into `tokens` if missing (never overwrites existing rows).
    Needed so scan history can reference it. Skipped when no trading pair exists."""
    m = result.get("market") or {}
    if not m.get("pair_address"):
        return False
    _insert_ignore(_market_row(result["address"], m))
    return True


# ---------------------------------------------------------------- watchlist
def set_watch(address: str, on: bool) -> bool:
    """Star/unstar a token that is already in the table. Returns False if it isn't there."""
    r = http.patch(
        f"{URL}/rest/v1/tokens",
        params={"address": f"eq.{address}", "select": "address"},
        headers={**HEADERS, **_JSON, "Prefer": "return=representation"},
        json={"watchlisted_at": _now() if on else None},
        timeout=15,
    )
    r.raise_for_status()
    return bool(r.json())


def add_token_and_watch(address: str, market: dict) -> bool:
    """Create the token from market data (for an address the screener hasn't seen) and star it."""
    if not market or not market.get("pair_address"):
        return False
    row = _market_row(address, market)
    row["watchlisted_at"] = _now()
    _insert_ignore(row)
    set_watch(address, True)   # in case it appeared between the check and the insert
    return True
