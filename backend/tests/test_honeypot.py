import pytest

from scanner import honeypot
from scanner.engine import Ctx
from tests.fakes import TOKEN, FakeRpc, wallet


def test_classify_table():
    c = honeypot.classify
    assert c({"err": None})["status"] == "pass"

    frozen = c({"err": {"InstructionError": [2, {"Custom": 17}]},
                "logs": ["Program log: Error: Account is frozen"]})
    assert frozen["status"] == "fail" and frozen["severity"] == "blocker" and frozen["points"] == 40

    slip = c({"err": {"InstructionError": [3, {"Custom": 6001}]}, "logs": ["Program log: SlippageToleranceExceeded 0x1771"]})
    assert slip["status"] == "warn" and slip["points"] == 15

    other = c({"err": {"InstructionError": [1, {"Custom": 7}]}, "logs": ["Program log: whatever"]})
    assert other["status"] == "warn" and other["points"] == 10

    for infra in ("BlockhashNotFound", "AccountNotFound", {"InsufficientFundsForRent": {"account_index": 0}}):
        assert c({"err": infra, "logs": []})["status"] == "unknown"
    assert c({"err": "SomethingNew", "logs": []})["status"] == "unknown"


def ctx_with_holders(n=3):
    ctx = Ctx(address=TOKEN, mint={"supply": "1000000000"})
    ctx.holders = [{"owner": wallet(i), "raw": 100_000_000 - i, "pct": 10, "kind": "wallet",
                    "program": "11111111111111111111111111111111"} for i in range(n)]
    ctx.holders.insert(0, {"owner": "poolpda", "raw": 5, "pct": 1, "kind": "lp", "program": "x"})
    return ctx


def test_simulates_from_a_wallet_never_a_pool(monkeypatch):
    seen = []
    monkeypatch.setattr(honeypot, "_order", lambda owner, amount, token: seen.append((owner, amount)) or {"transaction": "AAAA"})
    rpc = FakeRpc()
    r = honeypot.simulate_sell(rpc, ctx_with_holders())
    assert r["status"] == "pass" and rpc.sim_calls == 1
    assert seen[0][0] == wallet(0)                      # largest ordinary wallet, not the pool
    assert seen[0][1] == 100_000          # supply // 10_000


def test_frozen_account_is_blocker(monkeypatch):
    monkeypatch.setattr(honeypot, "_order", lambda *a: {"transaction": "AAAA"})
    rpc = FakeRpc()
    rpc.sim = {"err": {"InstructionError": [0, {"Custom": 17}]}, "logs": ["Program log: Error: Account is frozen"]}
    r = honeypot.simulate_sell(rpc, ctx_with_holders())
    assert r["status"] == "fail" and r["severity"] == "blocker"


def test_no_route_tries_next_holder_then_unknown(monkeypatch):
    calls = []
    monkeypatch.setattr(honeypot, "_order", lambda o, a, t: calls.append(o) or {"transaction": "", "errorMessage": "no route"})
    r = honeypot.simulate_sell(FakeRpc(), ctx_with_holders())
    assert r["status"] == "unknown" and len(calls) == 3 and "no route" in r["detail"]


def test_poor_holder_is_skipped(monkeypatch):
    monkeypatch.setattr(honeypot, "_order", lambda *a: {"transaction": "AAAA"})
    rpc = FakeRpc()
    rpc.balances[wallet(0)] = 1000        # too little SOL
    r = honeypot.simulate_sell(rpc, ctx_with_holders())
    assert r["status"] == "pass" and wallet(1)[:4] in r["detail"]


def test_rate_limit_stops_immediately(monkeypatch):
    def boom(*a): raise honeypot._Stop("Jupiter rate limited the request")
    monkeypatch.setattr(honeypot, "_order", boom)
    r = honeypot.simulate_sell(FakeRpc(), ctx_with_holders())
    assert r["status"] == "unknown" and "rate limited" in r["detail"]


def test_no_wallet_candidates():
    ctx = Ctx(address=TOKEN, mint={"supply": "10"})
    ctx.holders = [{"owner": "p", "raw": 5, "pct": 50, "kind": "lp", "program": "x"}]
    assert honeypot.simulate_sell(FakeRpc(), ctx)["status"] == "unknown"


def test_jupiter_http_errors(monkeypatch):
    class R:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self):
            if self._b is None: raise ValueError
            return self._b
    for code, expect in ((401, "API_KEY"), (429, "rate limited")):
        monkeypatch.setattr(honeypot.requests, "get", lambda *a, **k: R(code, None))
        with pytest.raises(honeypot._Stop) as e:
            honeypot._order("w", 1, "t")
        assert expect.lower() in str(e.value).lower()
    monkeypatch.setattr(honeypot.requests, "get", lambda *a, **k: R(500, None))
    assert honeypot._order("w", 1, "t") == {"error": "HTTP 500"}
