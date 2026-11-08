"""End-to-end: fake chain -> full scan -> score. Checks the whole pipeline wiring."""
import pytest

import scanner.engine as E
from scanner import honeypot
from scanner.constants import PUMPSWAP
from scanner.scoring import MAX_POINTS, summarize
from tests.fakes import LPMINT, POOL, RAYDIUM_AUTH, TOKEN, WSOL, FakeRpc, pumpswap_pool, wallet

INCINERATOR = "1nc1nerator11111111111111111111111111111111"


def chain(mint_auth=None, freeze=None, lp_holder=INCINERATOR, extensions=None, program="spl-token"):
    rpc = FakeRpc()
    info = {"supply": "1000000", "decimals": 6, "mintAuthority": mint_auth, "freezeAuthority": freeze}
    if extensions:
        info["extensions"] = extensions
    rpc.mints[TOKEN] = info
    rpc.mints[LPMINT] = {"supply": "1000000", "decimals": 9}
    rpc.raw[POOL] = (PUMPSWAP, pumpswap_pool(lp_supply=1_000_000))
    rpc.largest[TOKEN] = [("T0", RAYDIUM_AUTH, 700_000), ("T1", wallet(1), 120_000),
                          ("T2", wallet(2), 80_000), ("T3", wallet(3), 50_000)]
    rpc.largest[LPMINT] = [("L0", lp_holder, 1_000_000)]
    rpc.mints[TOKEN]["_program"] = program
    return rpc


@pytest.fixture(autouse=True)
def patch_market(monkeypatch):
    monkeypatch.setattr(E, "best_pair", lambda a: {
        "pair_address": POOL, "dex": "pumpswap", "symbol": "TST", "name": "Test",
        "liquidity_usd": 50_000, "age_min": 600.0, "price_usd": "0.001"})
    monkeypatch.setattr(honeypot, "_order", lambda *a: {"transaction": "AAAA"})


def by_id(res):
    return {c["id"]: c for c in res["checks"]}


def test_clean_token_scores_low_with_every_check_run():
    res = E.run_scan(TOKEN, chain())
    c = by_id(res)
    assert set(c) == set(MAX_POINTS), set(MAX_POINTS) ^ set(c)
    assert res["score"] == 0 and res["level"] == "low"
    assert c["lp_status"]["status"] == "pass" and c["honeypot_sim"]["status"] == "pass"
    assert c["mint_authority"]["status"] == "pass"
    assert res["unverified"] == ["metadata_mutable"]       # no Helius in the fake
    # the pool PDA is excluded from concentration; real wallets hold 25%
    assert "25.0%" in c["top10_concentration"]["detail"]


def test_rug_profile_is_critical():
    rpc = chain(mint_auth="Dev", freeze="Dev", lp_holder=wallet(9))
    rpc.sim = {"err": {"InstructionError": [0, {"Custom": 17}]}, "logs": ["Program log: Error: Account is frozen"]}
    res = E.run_scan(TOKEN, rpc)
    c = by_id(res)
    assert c["lp_status"]["status"] == "fail" and c["honeypot_sim"]["status"] == "fail"
    assert res["level"] == "critical" and res["score"] >= 75


def test_active_mint_authority_alone_forces_high():
    res = E.run_scan(TOKEN, chain(mint_auth="Dev"))
    assert res["score"] == 25 and res["level"] == "high"     # 25 pts would be "medium"


def test_token2022_permanent_delegate():
    ext = [{"extension": "permanentDelegate", "state": {"delegate": "Dev"}}]
    rpc = chain(extensions=ext)
    orig = rpc.get_mint
    rpc.get_mint = lambda a: (rpc.mints[a], "spl-token-2022") if a == TOKEN else orig(a)
    res = E.run_scan(TOKEN, rpc)
    assert by_id(res)["token_extensions"]["status"] == "fail" and res["level"] == "high"


def test_dead_data_sources_become_unknown_never_pass(monkeypatch):
    def broken_market(a): raise RuntimeError("dexscreener down")
    monkeypatch.setattr(E, "best_pair", broken_market)
    rpc = chain()
    rpc.largest_accounts = lambda m: (_ for _ in ()).throw(RuntimeError("rpc down"))
    res = E.run_scan(TOKEN, rpc)
    c = by_id(res)
    for cid in ("liquidity", "token_age", "lp_status", "honeypot_sim", "top10_concentration", "largest_holder"):
        assert c[cid]["status"] == "unknown" and c[cid]["points"] == 0, cid
    assert res["coverage"] < 0.5


def test_crashing_check_is_contained(monkeypatch):
    monkeypatch.setattr(E.C, "check_liquidity", lambda ctx: 1 / 0)
    E.CHECKS[3] = (E.C.check_liquidity, ["liquidity"])
    res = E.run_scan(TOKEN, chain())
    assert by_id(res)["liquidity"]["status"] == "unknown"


def test_env_switches_disable_expensive_checks(monkeypatch):
    monkeypatch.setenv("ENABLE_LP_CHECK", "0")
    monkeypatch.setenv("ENABLE_HONEYPOT_SIM", "0")
    rpc = chain()
    res = E.run_scan(TOKEN, rpc)
    c = by_id(res)
    assert c["lp_status"]["status"] == "unknown" and c["honeypot_sim"]["status"] == "unknown"
    assert rpc.sim_calls == 0


def test_invalid_input():
    with pytest.raises(E.ScanError):
        E.run_scan("nope", chain())
    rpc = FakeRpc()   # mint missing on-chain
    with pytest.raises(E.ScanError):
        E.run_scan(TOKEN, rpc)


def test_scoring_summary_rules():
    chk = lambda i, st, pts, sev=None: {"id": i, "status": st, "points": pts, **({"severity": sev} if sev else {})}
    assert summarize([chk("liquidity", "fail", 10)])["level"] == "low"
    assert summarize([chk("honeypot_sim", "fail", 40, "blocker")])["level"] == "critical"
    assert summarize([chk("honeypot_sim", "fail", 40, "blocker")])["score"] == 75
    assert summarize([chk("freeze_authority", "fail", 20, "critical")])["level"] == "high"
    full = [chk(i, "pass", 0) for i in MAX_POINTS]
    assert summarize(full)["coverage"] == 1.0
    full[2] = chk("lp_status", "unknown", 0)
    r = summarize(full)
    assert r["unverified"] == ["lp_status"] and r["coverage"] == round(1 - 20 / sum(MAX_POINTS.values()), 2)
    assert summarize([chk("x", "fail", 90), chk("y", "fail", 90)])["score"] == 100
