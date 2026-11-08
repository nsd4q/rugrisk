"""LP burn / lock analysis.

1. Look at the pool account: its owning program tells us the layout.
2. Decode the LP mint (and the pool's own record of LP supply) from the account bytes.
   Every decoder VALIDATES the layout by checking that our token's mint appears where the
   layout says mints live. A mismatch means "unsupported", never a guess.
3. Look at who holds the LP mint: burn addresses, lockers, program-controlled accounts, wallets.
4. LP burned via the SPL `burn` instruction shrinks the mint's supply but not the pool's
   internal record, so (recorded - current) / recorded = share burned by supply reduction.

Concentrated-liquidity pools (Raydium CLMM, Orca Whirlpool, Meteora DLMM/DAMM) have no fungible
LP token, so they are reported as unverifiable.
"""
from . import b58
from .constants import (
    BURN_OWNERS, LOCKER_PROGRAMS, PUMPFUN_CURVE, PUMPSWAP, RAYDIUM_AMM_V4, RAYDIUM_CPMM,
)
from .holders import fetch_owner_balances


def _pk(data: bytes, off: int) -> str:
    return b58.encode(data[off:off + 32])


def _u64(data: bytes, off: int) -> int:
    return int.from_bytes(data[off:off + 8], "little")


# ---------------------------------------------------------------- pool decoders
# Each returns (lp_mint, recorded_lp_supply_or_None) or None if the layout doesn't validate.
def _decode_pumpswap(data: bytes, token: str):
    # disc(8) bump(1) index(2) creator(32) base_mint(32) quote_mint(32) lp_mint(32)
    # base_acct(32) quote_acct(32) lp_supply(u64) coin_creator(32) ...
    if len(data) < 211:
        return None
    if token not in (_pk(data, 43), _pk(data, 75)):
        return None
    return _pk(data, 107), _u64(data, 203)


def _decode_raydium_v4(data: bytes, token: str):
    # AmmInfo, 752 bytes: coinMint@400, pcMint@432, lpMint@464, lpAmount@720
    if len(data) != 752:
        return None
    if token not in (_pk(data, 400), _pk(data, 432)):
        return None
    return _pk(data, 464), _u64(data, 720)


def _decode_cpmm(data: bytes, token: str, lp_decimals: int | None = None):
    # PoolState: disc(8) amm_config creator vault0 vault1 lp_mint@136 mint0@168 mint1@200 ...
    # lp_supply is a u64 after five u8 fields; with alignment it sits at 336.
    if len(data) < 344:
        return None
    if token not in (_pk(data, 168), _pk(data, 200)):
        return None
    recorded = _u64(data, 336)
    # lp_mint_decimals byte (330) must agree with the real LP mint, otherwise our idea of the
    # layout is off and the recorded supply can't be trusted.
    if lp_decimals is not None and data[330] != lp_decimals:
        recorded = None
    return _pk(data, 136), recorded


def locate_lp(rpc, pair_address: str, token: str) -> dict:
    """Identify the pool type and its LP mint. Returns a dict with `kind`:
       bonding_curve | token_lp | unsupported"""
    raw = rpc.get_raw_account(pair_address)
    if raw is None:
        return {"kind": "unsupported", "note": "pool account not found on-chain"}
    program, data = raw

    if program == PUMPFUN_CURVE:
        return {"kind": "bonding_curve"}

    if program == PUMPSWAP:
        dec = _decode_pumpswap(data, token)
        name = "PumpSwap"
    elif program == RAYDIUM_AMM_V4:
        dec = _decode_raydium_v4(data, token)
        name = "Raydium AMM v4"
    elif program == RAYDIUM_CPMM:
        # decimals check needs the LP mint first
        pre = _decode_cpmm(data, token)
        dec = None
        if pre:
            try:
                lp_info, _ = rpc.get_mint(pre[0])
                dec = _decode_cpmm(data, token, int(lp_info.get("decimals", -1)))
            except Exception:
                dec = (pre[0], None)
        name = "Raydium CPMM"
    else:
        return {"kind": "unsupported",
                "note": "this pool type uses position-based liquidity (no LP token) or isn't supported yet"}

    if dec is None:
        return {"kind": "unsupported", "note": f"{name} pool layout didn't match what we expect"}
    return {"kind": "token_lp", "dex": name, "lp_mint": dec[0], "recorded": dec[1]}


# ---------------------------------------------------------------- LP holder analysis
def analyze_pool(rpc, ctx) -> dict:
    pair = (ctx.market or {}).get("pair_address")
    if not pair:
        return {"kind": "unsupported", "note": "no pair address"}

    loc = locate_lp(rpc, pair, ctx.address)
    if loc["kind"] != "token_lp":
        return loc

    lp_info, _ = rpc.get_mint(loc["lp_mint"])  # validates that it really is a mint
    current = int(lp_info.get("supply") or 0)
    recorded = loc["recorded"]
    known = bool(recorded) and recorded >= current
    base = recorded if known else current
    if base <= 0:
        return {"kind": "unsupported", "note": "LP supply is zero"}

    reduction = (recorded - current) if known else 0
    burn_held = locked = program = wallet = 0
    listed = 0
    if current > 0:
        for h in fetch_owner_balances(rpc, loc["lp_mint"]):
            listed += h["amount"]
            if h["owner"] in BURN_OWNERS:
                burn_held += h["amount"]
            elif h["program"] in LOCKER_PROGRAMS:
                locked += h["amount"]
            elif not h["on_curve"]:
                program += h["amount"]   # PDA: some program controls it (maybe a lock)
            else:
                wallet += h["amount"]
        wallet += max(0, current - listed)  # LP outside the top 20 accounts: assume wallets

    pct = lambda x: round(x / base * 100, 2)
    return {
        "kind": "token_lp",
        "dex": loc["dex"],
        "lp_mint": loc["lp_mint"],
        "recorded_known": known,
        "burned_pct": pct(reduction + burn_held),
        "locked_pct": pct(locked),
        "program_pct": pct(program),
        "wallet_pct": pct(wallet),
    }
