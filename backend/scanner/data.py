"""Market data for the scanner (DexScreener)."""
import time

import requests


def best_pair(address: str) -> dict | None:
    for attempt in range(2):
        r = requests.get(f"https://api.dexscreener.com/tokens/v1/solana/{address}", timeout=15)
        if r.status_code == 429:
            time.sleep(2)
            continue
        r.raise_for_status()
        break
    else:
        raise RuntimeError("DexScreener rate limited")

    pairs = [p for p in (r.json() or []) if (p.get("baseToken") or {}).get("address") == address]
    if not pairs:
        return None

    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    created_ms = best.get("pairCreatedAt")
    return {
        "pair_address": best.get("pairAddress"),
        "dex": best.get("dexId"),
        "symbol": (best.get("baseToken") or {}).get("symbol"),
        "name": (best.get("baseToken") or {}).get("name"),
        "liquidity_usd": (best.get("liquidity") or {}).get("usd"),
        "price_usd": best.get("priceUsd"),
        "age_min": (time.time() * 1000 - created_ms) / 60000 if created_ms else None,
    }
