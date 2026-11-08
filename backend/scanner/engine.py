"""Builds the scan context (fetching each data source once) and runs all checks."""
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import checks as C
from . import honeypot, lp
from .constants import BURN_OWNERS, DEX_PROGRAMS, LP_AUTHORITIES, SOL_ADDR
from .holders import fetch_owner_balances
from .data import best_pair
from .rpc import Rpc
from .scoring import summarize


class ScanError(Exception):
    pass


@dataclass
class Ctx:
    address: str
    mint: dict = field(default_factory=dict)
    token_program: str = "spl-token"
    market: dict | None = None
    asset: dict | None = None
    holders: list | None = None
    lp: dict | None = None
    sell: dict | None = None
    errors: dict = field(default_factory=dict)


# (check function, ids it produces: used to emit `unknown` if the function itself crashes)
CHECKS = [
    (C.check_authorities, ["mint_authority", "freeze_authority"]),
    (C.check_extensions, ["token_extensions"]),
    (C.check_holders, ["top10_concentration", "largest_holder"]),
    (C.check_liquidity, ["liquidity"]),
    (C.check_age, ["token_age"]),
    (C.check_lp, ["lp_status"]),
    (C.check_honeypot, ["honeypot_sim"]),
    (C.check_metadata, ["metadata_mutable"]),
]


def _classify(owner, owner_program, pair_address):
    if owner in BURN_OWNERS:
        return "burn"
    if owner in LP_AUTHORITIES or owner == pair_address or owner_program in DEX_PROGRAMS:
        return "lp"
    return "wallet"


def _load_holders(rpc: Rpc, ctx: Ctx):
    supply = int(ctx.mint.get("supply") or 0)
    if supply == 0:
        return []
    pair = (ctx.market or {}).get("pair_address")
    return [
        {
            "owner": b["owner"],
            "raw": b["amount"],
            "pct": b["amount"] / supply * 100,
            "kind": _classify(b["owner"], b["program"], pair),
            "program": b["program"],
        }
        for b in fetch_owner_balances(rpc, ctx.address)
    ]


def build_context(address: str, rpc: Rpc) -> Ctx:
    ctx = Ctx(address=address)

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_mint = ex.submit(rpc.get_mint, address)
        f_market = ex.submit(best_pair, address)
        f_asset = ex.submit(rpc.get_asset, address) if rpc.is_helius else None

        try:
            ctx.mint, ctx.token_program = f_mint.result()
        except Exception as e:
            raise ScanError(str(e)) from e

        try:
            ctx.market = f_market.result()
            if ctx.market is None:
                ctx.errors["market"] = "no trading pair found on DexScreener"
        except Exception as e:
            ctx.errors["market"] = f"DexScreener error ({e})"

        if f_asset is not None:
            try:
                ctx.asset = f_asset.result()
            except Exception as e:
                ctx.errors["asset"] = f"metadata lookup failed ({e})"
        else:
            ctx.errors["asset"] = "needs a Helius RPC key (DAS API)"

    try:
        ctx.holders = _load_holders(rpc, ctx)
    except Exception as e:
        ctx.errors["holders"] = f"holder lookup failed ({e})"

    # Stage 3: LP burn/lock analysis and the sell simulation are independent: run together.
    want_lp = os.getenv("ENABLE_LP_CHECK", "1") == "1"
    want_sim = os.getenv("ENABLE_HONEYPOT_SIM", "1") == "1"
    if not want_lp:
        ctx.errors["lp"] = "disabled (ENABLE_LP_CHECK=0)"
    elif not (ctx.market or {}).get("pair_address"):
        ctx.errors["lp"] = ctx.errors.get("market", "no trading pair found")
    if not want_sim:
        ctx.errors["sell"] = "disabled (ENABLE_HONEYPOT_SIM=0)"
    elif not ctx.holders:
        ctx.errors["sell"] = ctx.errors.get("holders", "no holders to simulate from")

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_lp = ex.submit(lp.analyze_pool, rpc, ctx) if "lp" not in ctx.errors else None
        f_sim = ex.submit(honeypot.simulate_sell, rpc, ctx) if "sell" not in ctx.errors else None
        if f_lp:
            try:
                ctx.lp = f_lp.result()
            except Exception as e:
                ctx.errors["lp"] = f"LP lookup failed ({type(e).__name__}: {str(e)[:80]})"
        if f_sim:
            try:
                ctx.sell = f_sim.result()
            except Exception as e:
                ctx.errors["sell"] = f"simulation failed ({type(e).__name__}: {str(e)[:80]})"

    return ctx


def run_scan(address: str, rpc: Rpc | None = None) -> dict:
    address = address.strip()
    if not SOL_ADDR.match(address):
        raise ScanError("Not a valid Solana address")

    rpc = rpc or Rpc()
    ctx = build_context(address, rpc)

    checks: list[dict] = []
    for fn, ids in CHECKS:
        try:
            checks.extend(fn(ctx))
        except Exception as e:  # a buggy check must never kill the scan or pass silently
            checks.extend(C._unknown(i, f"check crashed ({type(e).__name__})") for i in ids)

    result = summarize(checks)
    result.update({
        "address": address,
        "checks": checks,
        "market": ctx.market,
        "top_holders": [
            {"owner": h["owner"], "pct": round(h["pct"], 2), "kind": h["kind"]}
            for h in (ctx.holders or [])[:8]
        ],
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    })
    return result
