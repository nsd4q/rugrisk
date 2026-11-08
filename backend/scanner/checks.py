"""Each check takes the scan context and returns a list of result dicts:
   {id, status: pass|warn|fail|unknown, points, detail, [severity]}
A check that can't get its data must return `unknown` (0 points), never `pass`."""
from .constants import (
    HIGH_FEE_BPS,
    LP_SECURE_PCT,
    LP_WALLET_FAIL_PCT,
    LARGEST_HOLDER_PCT,
    MIN_AGE_MINUTES,
    MIN_LIQUIDITY_USD,
    TOP10_FULL_PCT,
    TOP10_START_PCT,
    short,
)


def _r(id_, status, points, detail, severity=None):
    d = {"id": id_, "status": status, "points": points, "detail": detail}
    if severity:
        d["severity"] = severity
    return d


def _unknown(id_, why):
    return _r(id_, "unknown", 0, f"Couldn't verify: {why}")


# ------------------------------------------------------------------ authorities
def check_authorities(ctx):
    m = ctx.mint
    out = []
    if m.get("mintAuthority"):
        out.append(_r("mint_authority", "fail", 25,
                      "Mint authority is still active: the dev can create unlimited new supply", "critical"))
    else:
        out.append(_r("mint_authority", "pass", 0, "Mint authority revoked"))

    if m.get("freezeAuthority"):
        out.append(_r("freeze_authority", "fail", 20,
                      "Freeze authority is still active: the dev can freeze holders' tokens", "critical"))
    else:
        out.append(_r("freeze_authority", "pass", 0, "Freeze authority revoked"))
    return out


# ------------------------------------------------------------------ Token-2022 extensions
def check_extensions(ctx):
    if ctx.token_program != "spl-token-2022":
        return [_r("token_extensions", "pass", 0, "Standard SPL token: no risky extensions possible")]

    findings, points, critical = [], 0, False
    for ext in ctx.mint.get("extensions") or []:
        name = ext.get("extension", "")
        st = ext.get("state") or {}

        if name == "permanentDelegate" and st.get("delegate"):
            points += 15
            critical = True
            findings.append("permanent delegate (can move or burn ANY holder's tokens)")

        elif name == "transferHook" and st.get("programId"):
            points += 15
            findings.append("transfer hook (custom program can block or tax transfers)")

        elif name == "transferFeeConfig":
            bps = int((st.get("newerTransferFee") or {}).get("transferFeeBasisPoints") or 0)
            if bps >= HIGH_FEE_BPS:
                points += 15
                findings.append(f"high transfer fee ({bps / 100:.1f}%)")
            elif bps > 0 or st.get("transferFeeConfigAuthority"):
                points += 5
                findings.append(f"transfer fee set ({bps / 100:.2f}%), fee authority can change it")

        elif name == "defaultAccountState" and st.get("accountState") == "frozen":
            points += 15
            critical = True
            findings.append("new token accounts start frozen")

        elif name in ("pausable", "pausableConfig") and st.get("authority"):
            points += 15
            findings.append("pausable (authority can halt all transfers)")

    points = min(points, 30)
    if not findings:
        return [_r("token_extensions", "pass", 0, "Token-2022 mint with no risky extensions")]
    return [_r("token_extensions", "fail", points, "Risky extensions: " + "; ".join(findings),
               "critical" if critical else None)]


# ------------------------------------------------------------------ holders
def check_holders(ctx):
    if ctx.holders is None:
        why = ctx.errors.get("holders", "holder data unavailable")
        return [_unknown("top10_concentration", why), _unknown("largest_holder", why)]

    wallets = [h for h in ctx.holders if h["kind"] == "wallet"]
    top10 = sum(h["pct"] for h in wallets[:10])

    span = TOP10_FULL_PCT - TOP10_START_PCT
    pts = round(max(0.0, min(1.0, (top10 - TOP10_START_PCT) / span)) * 20)
    status = "fail" if pts >= 10 else "warn" if pts > 0 else "pass"
    out = [_r("top10_concentration", status, pts,
              f"Top 10 wallets hold {top10:.1f}% of supply (pools and burn addresses excluded)")]

    if wallets and wallets[0]["pct"] > LARGEST_HOLDER_PCT:
        out.append(_r("largest_holder", "fail", 10,
                      f"Largest wallet ({short(wallets[0]['owner'])}) holds {wallets[0]['pct']:.1f}% of supply"))
    else:
        big = wallets[0]["pct"] if wallets else 0.0
        out.append(_r("largest_holder", "pass", 0, f"Largest wallet holds {big:.1f}% of supply"))
    return out


# ------------------------------------------------------------------ liquidity / age
def check_liquidity(ctx):
    if ctx.market is None or ctx.market.get("liquidity_usd") is None:
        return [_unknown("liquidity", ctx.errors.get("market", "no trading pair found"))]
    liq = float(ctx.market["liquidity_usd"])
    if liq < MIN_LIQUIDITY_USD:
        return [_r("liquidity", "fail", 10, f"Very thin liquidity: ${liq:,.0f}")]
    return [_r("liquidity", "pass", 0, f"Liquidity ${liq:,.0f}")]


def check_age(ctx):
    if ctx.market is None or ctx.market.get("age_min") is None:
        return [_unknown("token_age", ctx.errors.get("market", "pair creation time unavailable"))]
    age = ctx.market["age_min"]
    if age < MIN_AGE_MINUTES:
        return [_r("token_age", "warn", 5, f"Brand new: pair created {age:.0f} min ago")]
    return [_r("token_age", "pass", 0, f"Pair age {age:.0f} min")]


# ------------------------------------------------------------------ LP status
def check_lp(ctx):
    r = ctx.lp
    if r is None:
        return [_unknown("lp_status", ctx.errors.get("lp", "LP analysis unavailable"))]

    kind = r["kind"]
    if kind == "bonding_curve":
        return [_r("lp_status", "pass", 0,
                   "Still on the pump.fun bonding curve: there are no LP tokens to pull")]
    if kind != "token_lp":
        return [_unknown("lp_status", r.get("note", "pool type not supported"))]

    burned, locked = r["burned_pct"], r["locked_pct"]
    prog, wallet = r["program_pct"], r["wallet_pct"]
    secure = burned + locked

    if burned >= LP_SECURE_PCT:
        return [_r("lp_status", "pass", 0, f"LP is {burned:.1f}% burned: liquidity can't be pulled")]
    if secure >= LP_SECURE_PCT:
        return [_r("lp_status", "warn", 5,
                   f"LP is {locked:.1f}% locked in a locker program (unlock date not verified)"
                   + (f", {burned:.1f}% burned" if burned else ""))]
    if secure + prog >= LP_SECURE_PCT:
        return [_r("lp_status", "warn", 10,
                   f"{prog:.1f}% of LP sits in program-controlled accounts (possibly a lock, "
                   "not verified)")]
    if not r["recorded_known"]:
        return [_unknown("lp_status", "pool accounting unavailable, so burned LP can't be measured")]
    if wallet >= LP_WALLET_FAIL_PCT:
        return [_r("lp_status", "fail", 20,
                   f"{wallet:.1f}% of LP tokens are held by ordinary wallets: the owner can remove "
                   "liquidity (rug pull)", "critical")]
    return [_r("lp_status", "warn", 12,
               f"Only {secure:.1f}% of LP is burned/locked; {wallet:.1f}% is held by wallets")]


# ------------------------------------------------------------------ sell simulation
def check_honeypot(ctx):
    r = ctx.sell
    if r is None:
        return [_unknown("honeypot_sim", ctx.errors.get("sell", "sell simulation unavailable"))]
    out = {"id": "honeypot_sim", **r}
    return [out]


# ------------------------------------------------------------------ metadata (Helius DAS)
def check_metadata(ctx):
    if ctx.asset is None:
        return [_unknown("metadata_mutable", ctx.errors.get("asset", "needs a Helius RPC key"))]
    mutable = ctx.asset.get("mutable")
    if mutable is None:
        return [_unknown("metadata_mutable", "mutability flag missing")]
    if mutable:
        return [_r("metadata_mutable", "warn", 5, "Metadata is mutable: name/image can be changed later")]
    return [_r("metadata_mutable", "pass", 0, "Metadata is immutable")]
