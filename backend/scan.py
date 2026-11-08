"""CLI scanner.

  python scan.py <mint_address>           human-readable report
  python scan.py <mint_address> --json    raw JSON
  python scan.py <mint_address> --save    also write the result to Supabase
"""
import argparse
import json
import sys

from scanner.constants import short
from scanner.engine import ScanError, run_scan

ICON = {"pass": "✔", "warn": "⚠", "fail": "✖", "unknown": "?"}


def report(res: dict):
    m = res["market"] or {}
    liq = m.get("liquidity_usd")
    print(f"\n{m.get('symbol') or '?'}  {m.get('name') or ''}   [{m.get('dex') or 'no pair'}]")
    print(res["address"])
    if liq is not None:
        print(f"Liquidity: ${float(liq):,.0f}")

    print(f"\nRISK {res['score']}/100  ->  {res['level'].upper()}"
          f"     (checked {int(res['coverage'] * 100)}% of the scoring model)\n")

    for c in res["checks"]:
        pts = f"+{c['points']}" if c["points"] else "  "
        print(f" {ICON[c['status']]} {pts:>4}  {c['detail']}")

    if res["unverified"]:
        print(f"\n ? Not verified: {', '.join(res['unverified'])}")

    if res["top_holders"]:
        print("\nTop holders:")
        for h in res["top_holders"]:
            print(f"   {h['pct']:6.2f}%  {h['kind']:<6} {short(h['owner'])}")

    print("\nRisk indicators only. Not financial advice, and not a guarantee of safety.\n")


def main():
    p = argparse.ArgumentParser(description="Scan a Solana token for rug-risk indicators")
    p.add_argument("address")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save", action="store_true")
    a = p.parse_args()

    try:
        res = run_scan(a.address)
    except ScanError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if a.json:
        print(json.dumps(res, indent=2))
    else:
        report(res)

    if a.save:
        from scanner.store import save_scan
        ok = save_scan(res)
        print("Saved to Supabase." if ok else "Not saved: token isn't in the screener table yet.")


if __name__ == "__main__":
    main()
