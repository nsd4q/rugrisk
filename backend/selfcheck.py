"""Live self-check: run this once after setup to see what works on YOUR machine.

  python selfcheck.py                   # config, database lockdown, RPC, data APIs, a full scan of BONK
  python selfcheck.py --address <mint>  # additionally scan a specific token (e.g. a pump.fun graduate)

Exit code 0 = no hard failures. WARN means a feature will run in a reduced mode.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

import requests

from scanner.constants import SOL_ADDR

CONFIG_JS = Path(__file__).resolve().parent.parent / "frontend" / "js" / "config.js"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # authorities revoked, trades everywhere
WSOL = "So11111111111111111111111111111111111111112"
results = {"fail": 0, "warn": 0}


def say(level, name, msg=""):
    tag = {"ok": "PASS", "fail": "FAIL", "warn": "WARN"}[level]
    if level in results:
        results[level] += 1
    print(f"[{tag}] {name}" + (f": {msg}" if msg else ""))


def get(url, **kw):
    return requests.get(url, timeout=20, **kw)


def hdr(key):
    return {"apikey": key, **({"Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {})}


def check_env():
    need = ["SUPABASE_URL", "SUPABASE_SECRET_KEY"]
    missing = [k for k in need if not os.getenv(k)]
    if missing:
        say("fail", "env", f"missing {', '.join(missing)} in backend/.env")
        return False
    if os.environ["SUPABASE_SECRET_KEY"].startswith("sb_publishable_"):
        say("fail", "env", "SUPABASE_SECRET_KEY holds the PUBLISHABLE key: use the secret key here")
        return False
    say("ok", "env", "required variables present")
    custom = os.getenv("HELIUS_API_KEY") or os.getenv("SOLANA_RPC_URL")
    say("ok" if custom else "warn", "rpc config",
        "custom RPC configured" if custom
        else "using the public Solana RPC (slow, rate limited): set HELIUS_API_KEY")
    say("ok" if os.getenv("JUPITER_API_KEY") else "warn", "jupiter key",
        "set" if os.getenv("JUPITER_API_KEY")
        else "not set: sell simulation may be rate limited (free key: portal.jup.ag)")
    return True


def read_frontend_config():
    """Parse frontend/js/config.js (the values the browser will use). Returns (url, key) or None."""
    if not CONFIG_JS.exists():
        say("fail", "frontend config", "frontend/js/config.js not found: copy config.example.js to config.js and fill it in")
        return None
    text = CONFIG_JS.read_text(encoding="utf-8", errors="replace")
    url = re.search(r'SUPABASE_URL\s*=\s*"([^"]*)"', text)
    key = re.search(r'SUPABASE_KEY\s*=\s*"([^"]*)"', text)
    url, key = (url.group(1) if url else ""), (key.group(1) if key else "")
    if not key or key.startswith("YOUR_") or "YOUR-PROJECT" in url:
        say("fail", "frontend config", "config.js still has placeholder values: fill in your URL and publishable key")
        return None
    if key.startswith("sb_secret_"):
        say("fail", "frontend config", "config.js contains a SECRET key. Put the PUBLISHABLE key there and replace the exposed secret key in Supabase")
        return None
    say("ok", "frontend config", "config.js has a project URL and a non-secret key")
    if url.rstrip("/") != os.environ["SUPABASE_URL"].rstrip("/"):
        say("warn", "frontend config", "SUPABASE_URL in config.js differs from backend/.env: they should be the same project")
    return url, key


def check_supabase():
    url = os.environ["SUPABASE_URL"].rstrip("/")
    sec_h = hdr(os.environ["SUPABASE_SECRET_KEY"])

    for t in ("tokens", "token_scans", "worker_state"):
        try:
            r = get(f"{url}/rest/v1/{t}?select=*&limit=1", headers=sec_h)
        except requests.RequestException as e:
            say("fail", "supabase reachable", str(e)[:100])
            return
        if r.status_code != 200:
            say("fail", f"table {t}", f"HTTP {r.status_code}: did you run supabase/migrations/001_init.sql? ({r.text[:80]})")
            return
    say("ok", "supabase tables", "tokens, token_scans and worker_state are readable with the secret key")

    r = get(f"{url}/rest/v1/tokens?select=watchlisted_at&limit=1", headers=sec_h)
    if r.status_code == 200:
        say("ok", "watchlist column", "tokens.watchlisted_at exists")
    else:
        say("fail", "watchlist column", "tokens.watchlisted_at is missing: run the latest supabase/migrations/001_init.sql again (it is safe to re-run)")

    cfg = read_frontend_config()
    if not cfg:
        say("warn", "lockdown test skipped", "needs a valid frontend/js/config.js")
        return
    pub_h = hdr(cfg[1])

    # lockdown: the PUBLISHABLE key (what the browser uses) must be read-only on tokens
    r = get(f"{url}/rest/v1/tokens?select=address&limit=1", headers=pub_h)
    say("ok" if r.status_code == 200 else "fail", "website key can read tokens", f"HTTP {r.status_code}")

    r = requests.post(f"{url}/rest/v1/tokens", headers={**pub_h, "Content-Type": "application/json"},
                      json={"address": WSOL}, timeout=20)
    safe = r.status_code in (401, 403)
    say("ok" if safe else "fail", "website key CANNOT write tokens",
        f"HTTP {r.status_code}" + ("" if safe else "  <- SECURITY PROBLEM: check RLS/grants"))

    r = get(f"{url}/rest/v1/worker_state?select=*&limit=1", headers=pub_h)
    ok = r.status_code in (401, 403) or (r.status_code == 200 and r.json() == [])
    say("ok" if ok else "fail", "website key cannot read worker_state", f"HTTP {r.status_code}")

    r = requests.post(f"{url}/rest/v1/rpc/prune_old_data", headers={**pub_h, "Content-Type": "application/json"},
                      json={}, timeout=20)
    say("ok" if r.status_code in (401, 403, 404) else "fail",
        "website key cannot run maintenance functions", f"HTTP {r.status_code}")


def check_data_apis():
    r = get(f"https://api.dexscreener.com/tokens/v1/solana/{BONK}")
    ok = r.status_code == 200 and isinstance(r.json(), list) and bool(r.json())
    say("ok" if ok else "fail", "DexScreener", "pairs returned" if ok else f"HTTP {r.status_code}")

    r = get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
            headers={"Accept": "application/json;version=20230302"})
    say("ok" if r.status_code == 200 and r.json().get("data") else "fail", "GeckoTerminal", f"HTTP {r.status_code}")

    headers = {"x-api-key": os.environ["JUPITER_API_KEY"]} if os.getenv("JUPITER_API_KEY") else {}
    base = os.getenv("JUPITER_API_URL", "https://api.jup.ag/swap/v2").rstrip("/")
    r = get(f"{base}/order", params={"inputMint": BONK, "outputMint": WSOL, "amount": "1000000"}, headers=headers)
    if r.status_code == 200 and r.json().get("outAmount"):
        say("ok", "Jupiter quote", "route found")
    elif r.status_code in (401, 403, 429):
        say("warn", "Jupiter quote", f"HTTP {r.status_code}: get a free key at portal.jup.ag, set JUPITER_API_KEY")
    else:
        say("warn", "Jupiter quote", f"HTTP {r.status_code}: sell simulation will report 'unknown'")


def check_rpc_and_scan(address):
    from scanner.engine import ScanError, run_scan
    from scanner.rpc import Rpc

    rpc = Rpc()
    try:
        t = time.time()
        slot = rpc.call("getSlot", [])
        say("ok", "Solana RPC", f"slot {slot} ({(time.time() - t) * 1000:.0f} ms, helius={rpc.is_helius})")
    except Exception as e:
        say("fail", "Solana RPC", str(e)[:120])
        return

    t = time.time()
    try:
        res = run_scan(address, rpc)
    except ScanError as e:
        say("fail", f"scan {address[:6]}…", str(e))
        return
    except Exception as e:
        say("fail", f"scan {address[:6]}…", f"{type(e).__name__}: {e}"[:160])
        return

    print(f"\n   scan of {address[:8]}… took {time.time() - t:.1f}s -> score {res['score']}/100 "
          f"{res['level'].upper()}, coverage {int(res['coverage'] * 100)}%")
    for c in res["checks"]:
        print(f"     {c['status']:<8} {c['id']:<20} {c['detail'][:100]}")
    print()

    by = {c["id"]: c for c in res["checks"]}
    if address == BONK:  # known facts about BONK
        for cid in ("mint_authority", "freeze_authority"):
            say("ok" if by[cid]["status"] == "pass" else "fail", f"BONK {cid} is revoked", by[cid]["detail"])
    say("ok", "full scan ran", f"{len(res['checks'])} checks produced")
    for cid in ("lp_status", "honeypot_sim", "metadata_mutable"):
        if by[cid]["status"] == "unknown":
            say("warn", f"{cid} unverified", by[cid]["detail"][:110])
        else:
            say("ok", f"{cid} verified", by[cid]["status"])


def guarded(fn, *args):
    """A network error in one section must be reported, not crash the whole check."""
    try:
        fn(*args)
    except requests.RequestException as e:
        say("fail", fn.__name__, f"network error: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        say("fail", fn.__name__, f"unexpected error: {type(e).__name__}: {str(e)[:100]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", help="also scan this token mint")
    a = ap.parse_args()
    if a.address and not SOL_ADDR.match(a.address):
        sys.exit("not a valid Solana address")

    print("RugRisk self-check\n")
    if check_env():
        guarded(check_supabase)
        guarded(check_data_apis)
        guarded(check_rpc_and_scan, BONK)
        if a.address:
            guarded(check_rpc_and_scan, a.address)
    print(f"\n{results['fail']} failed, {results['warn']} warnings")
    sys.exit(1 if results["fail"] else 0)


if __name__ == "__main__":
    main()
