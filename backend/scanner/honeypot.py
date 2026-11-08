"""Sell simulation ("honeypot" check).

Idea: take a REAL holder wallet, ask Jupiter for an assembled swap that sells a small slice of
the token for SOL from that wallet, then run it through the RPC's simulateTransaction (no
signature needed, nothing is sent). If the chain says the sell would fail because the token
account is frozen, a hook/tax blocks it, etc., we know before anyone loses money.

Limits (also shown to users): it proves ONE wallet could sell right now. It cannot see
blacklists aimed at other wallets or rules that change later. A simulation that fails for
unrelated reasons (no route yet, rate limit, holder out of SOL) is reported as `unknown`.
"""
import os

import requests

from .constants import SYSTEM_PROGRAM, WSOL, short

JUP_URL = os.getenv("JUPITER_API_URL", "https://api.jup.ag/swap/v2").rstrip("/")
MIN_LAMPORTS = 20_000_000   # holder needs ~0.02 SOL for fees / temp accounts
MAX_CANDIDATES = 3

_INFRA_ERRORS = (
    "BlockhashNotFound", "AccountNotFound", "InsufficientFunds", "InvalidAccountForFee",
    "AccountInUse", "ProgramAccountNotFound", "TransactionTooLarge", "AccountLoadedTwice",
    "MissingSignatureForFee", "InvalidWritableAccount",
)


class _Stop(Exception):
    """Environment problem (rate limit, missing key): further candidates won't help."""


def _res(status, points, detail, severity=None):
    d = {"status": status, "points": points, "detail": detail}
    if severity:
        d["severity"] = severity
    return d


def _unknown(why):
    return _res("unknown", 0, f"Couldn't verify: {why}")


def _brief(err) -> str:
    return str(err).replace("'", "")[:120]


def classify(sim: dict) -> dict:
    """Turn a simulateTransaction result into a check verdict."""
    err = sim.get("err")
    logs = sim.get("logs") or []

    if err is None:
        return _res("pass", 0, "Simulated selling a real holder's tokens: the sell went through")

    low = "\n".join(logs).lower()
    err_s = str(err)

    if "account is frozen" in low:
        return _res("fail", 40, "Sells are BLOCKED: token accounts are frozen by the freeze authority "
                                "(honeypot)", "blocker")

    if any(k in err_s for k in _INFRA_ERRORS) or ("insufficient" in low and "lamports" in low):
        return _unknown(f"simulation couldn't run ({_brief(err)})")

    if "slippage" in low or "0x1771" in low:
        return _res("warn", 15, "Sell simulation reverted on slippage even for a tiny amount: "
                                "possible high sell tax / transfer fee")

    if "InstructionError" in err_s:
        return _res("warn", 10, f"Sell simulation failed ({_brief(err)}): could be a honeypot "
                                "or a routing problem, treat with caution")

    return _unknown(f"unrecognised simulation error ({_brief(err)})")


def _candidates(ctx):
    out = []
    for h in ctx.holders or []:
        # ordinary wallet (owned by the system program), not a pool/PDA, with a balance
        if h["kind"] == "wallet" and h.get("program") == SYSTEM_PROGRAM and h.get("raw", 0) > 0:
            out.append(h)
    return out[:MAX_CANDIDATES]


def _order(owner: str, amount: int, token: str) -> dict:
    headers = {}
    key = os.getenv("JUPITER_API_KEY")
    if key:
        headers["x-api-key"] = key
    r = requests.get(
        f"{JUP_URL}/order",
        params={"inputMint": token, "outputMint": WSOL, "amount": str(amount), "taker": owner},
        headers=headers, timeout=20,
    )
    if r.status_code in (401, 403):
        raise _Stop("Jupiter rejected the request: set JUPITER_API_KEY (free at portal.jup.ag)")
    if r.status_code == 429:
        raise _Stop("Jupiter rate limited the request")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 and not body:
        body = {"error": f"HTTP {r.status_code}"}
    return body if isinstance(body, dict) else {}


def simulate_sell(rpc, ctx) -> dict:
    cands = _candidates(ctx)
    if not cands:
        return _unknown("no ordinary holder wallet available to simulate from")

    supply = int(ctx.mint.get("supply") or 0)
    reasons = []
    for h in cands:
        owner = h["owner"]
        try:
            if rpc.get_balance(owner) < MIN_LAMPORTS:
                reasons.append(f"holder {short(owner)} has too little SOL")
                continue
            amount = max(1, min(h["raw"] // 2, supply // 10_000))
            order = _order(owner, amount, ctx.address)
            tx = order.get("transaction")
            if not tx:
                why = order.get("errorMessage") or order.get("error") or order.get("errorCode") or "no route"
                reasons.append(f"Jupiter built no sell transaction ({why})")
                continue
            verdict = classify(rpc.simulate(tx))
        except _Stop as e:
            return _unknown(str(e))
        except Exception as e:
            reasons.append(f"{type(e).__name__}: {str(e)[:80]}")
            continue

        if verdict["status"] != "unknown":
            verdict["detail"] += f" (simulated from holder {short(owner)}; tests one wallet only)"
            return verdict
        reasons.append(verdict["detail"].replace("Couldn't verify: ", ""))

    return _unknown("; ".join(reasons[:3]) or "no usable simulation")
