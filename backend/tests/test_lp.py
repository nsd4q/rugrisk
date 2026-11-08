import pytest

from scanner import lp
from scanner.checks import check_lp
from scanner.constants import (
    BURN_OWNERS, LOCKER_PROGRAMS, PUMPFUN_CURVE, PUMPSWAP, RAYDIUM_AMM_V4, RAYDIUM_CPMM,
)
from scanner.engine import Ctx
from tests.fakes import (
    LOCKER_PDA, LPMINT, POOL, TOKEN, WSOL, FakeRpc, cpmm_pool, pumpswap_pool, raydium_v4_pool, wallet,
)

INCINERATOR = "1nc1nerator11111111111111111111111111111111"


def make_ctx(program, data, current_supply, holders, recorded_decimals=9):
    rpc = FakeRpc()
    rpc.mints[LPMINT] = {"supply": str(current_supply), "decimals": recorded_decimals}
    rpc.raw[POOL] = (program, data)
    rpc.largest[LPMINT] = [(f"TA{i}", owner, amt) for i, (owner, amt) in enumerate(holders)]
    ctx = Ctx(address=TOKEN, market={"pair_address": POOL, "dex": "x"})
    return rpc, ctx


def verdict(rpc, ctx):
    ctx.lp = lp.analyze_pool(rpc, ctx)
    return ctx.lp, check_lp(ctx)[0]


# ---------------- decoders reject wrong layouts instead of guessing
@pytest.mark.parametrize("program,data", [
    (PUMPSWAP, pumpswap_pool(base="So11111111111111111111111111111111111111112", quote=WSOL)),  # our token absent
    (RAYDIUM_AMM_V4, raydium_v4_pool()[:700]),                                                   # wrong size
    (RAYDIUM_CPMM, cpmm_pool(mint0=WSOL, mint1=WSOL)),
])
def test_layout_mismatch_is_unsupported(program, data):
    rpc, ctx = make_ctx(program, data, 1000, [])
    r, c = verdict(rpc, ctx)
    assert r["kind"] == "unsupported" and c["status"] == "unknown" and c["points"] == 0


def test_unknown_pool_program_is_unknown_not_pass():
    rpc, ctx = make_ctx("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", b"\0" * 300, 1, [])
    _, c = verdict(rpc, ctx)
    assert c["status"] == "unknown"


def test_pumpfun_bonding_curve_passes():
    rpc, ctx = make_ctx(PUMPFUN_CURVE, b"\0" * 100, 1, [])
    _, c = verdict(rpc, ctx)
    assert c["status"] == "pass"


# ---------------- PumpSwap / Raydium v4 / CPMM: same economics, different bytes
@pytest.mark.parametrize("program,builder", [
    (PUMPSWAP, lambda rec: pumpswap_pool(lp_supply=rec)),
    (RAYDIUM_AMM_V4, lambda rec: raydium_v4_pool(lp_amount=rec)),
    (RAYDIUM_CPMM, lambda rec: cpmm_pool(lp_supply=rec)),
])
class TestAllPoolTypes:
    def test_lp_burned_by_supply_reduction_passes(self, program, builder):
        # pool recorded 1,000,000 LP; only 100 remain in a wallet -> 99.99% burned
        rpc, ctx = make_ctx(program, builder(1_000_000), 100, [(wallet(1), 100)])
        r, c = verdict(rpc, ctx)
        assert r["burned_pct"] > 99.9 and c["status"] == "pass", (r, c)

    def test_lp_in_incinerator_passes(self, program, builder):
        rpc, ctx = make_ctx(program, builder(1_000_000), 1_000_000, [(INCINERATOR, 1_000_000)])
        _, c = verdict(rpc, ctx)
        assert c["status"] == "pass"

    def test_lp_held_by_dev_wallet_is_a_critical_fail(self, program, builder):
        rpc, ctx = make_ctx(program, builder(1_000_000), 1_000_000, [(wallet(1), 1_000_000)])
        r, c = verdict(rpc, ctx)
        assert r["wallet_pct"] == 100 and c["status"] == "fail" and c["severity"] == "critical"
        assert c["points"] == 20

    def test_lp_in_known_locker_warns(self, program, builder):
        rpc, ctx = make_ctx(program, builder(1_000_000), 1_000_000, [(LOCKER_PDA, 1_000_000)])
        rpc.owner_program[LOCKER_PDA] = next(iter(LOCKER_PROGRAMS))
        _, c = verdict(rpc, ctx)
        assert c["status"] == "warn" and "locked" in c["detail"]

    def test_lp_in_unknown_pda_is_flagged_but_not_trusted(self, program, builder):
        rpc, ctx = make_ctx(program, builder(1_000_000), 1_000_000, [(LOCKER_PDA, 1_000_000)])
        _, c = verdict(rpc, ctx)   # PDA, no owner program known
        assert c["status"] == "warn" and "program-controlled" in c["detail"]

    def test_half_burned_half_wallet_is_a_partial_warning(self, program, builder):
        rpc, ctx = make_ctx(program, builder(1_000_000), 600_000, [(wallet(1), 600_000)])
        r, c = verdict(rpc, ctx)
        assert r["burned_pct"] == 40 and r["wallet_pct"] == 60
        assert c["status"] == "fail"   # >=50% in wallets


def test_cpmm_decimals_mismatch_disables_burn_math():
    # layout sanity byte disagrees with the real LP mint -> don't trust recorded supply
    rpc, ctx = make_ctx(RAYDIUM_CPMM, cpmm_pool(lp_supply=10**12, lp_decimals=6), 100, [(wallet(1), 100)])
    r, c = verdict(rpc, ctx)   # real LP mint has 9 decimals
    assert r["recorded_known"] is False
    assert c["status"] == "unknown"   # must NOT claim "burned"


def test_recorded_below_current_is_ignored():
    rpc, ctx = make_ctx(PUMPSWAP, pumpswap_pool(lp_supply=5), 1000, [(wallet(1), 1000)])
    r, _ = verdict(rpc, ctx)
    assert r["recorded_known"] is False and r["burned_pct"] == 0


def test_lp_outside_top20_counts_as_wallet_held():
    rpc, ctx = make_ctx(PUMPSWAP, pumpswap_pool(lp_supply=1000), 1000, [(INCINERATOR, 100)])
    r, _ = verdict(rpc, ctx)   # 900 LP unaccounted for
    assert r["wallet_pct"] == 90 and r["burned_pct"] == 10
