import pytest
from fastapi.testclient import TestClient

import api.main as M
from scanner.engine import ScanError
from tests.fakes import TOKEN


@pytest.fixture
def client(monkeypatch):
    state = {"scans": 0, "saved": 0, "ensure": 0}

    def scan(addr, rpc):
        state["scans"] += 1
        if addr.startswith("Faik"):
            raise ScanError("Address is not a token mint")
        if addr.startswith("Boom"):
            raise RuntimeError("rpc exploded")
        return {"address": addr, "score": 5, "level": "low", "coverage": .9, "unverified": [],
                "checks": [], "market": None, "top_holders": []}

    def ensure(r):
        state["ensure"] += 1
        return True

    monkeypatch.setattr(M, "run_scan", scan)
    monkeypatch.setattr(M.store, "ensure_token", ensure)
    monkeypatch.setattr(M.store, "save_scan", lambda r: state.__setitem__("saved", state["saved"] + 1))
    monkeypatch.setattr(M, "cache", M.TtlCache(ttl=180))
    monkeypatch.setattr(M, "limiter", M.IpLimiter(max_calls=1000))
    c = TestClient(M.app)
    c.state = state
    return c


def post(c, addr):
    return c.post("/scan", json={"address": addr})


def test_health_and_validation(client):
    assert client.get("/health").json() == {"ok": True}
    assert post(client, "nope").status_code == 400
    assert post(client, "").status_code == 400
    assert client.post("/scan", json={}).status_code == 422          # malformed body
    assert client.state["scans"] == 0


def test_no_login_needed_and_no_auth_header_required(client):
    r = post(client, TOKEN)
    assert r.status_code == 200 and r.json()["score"] == 5 and r.json()["cached"] is False
    assert "quota" not in r.json()


def test_result_is_cached_and_stored(client):
    post(client, TOKEN)
    r = post(client, TOKEN)
    assert r.json()["cached"] is True
    assert client.state["scans"] == 1                 # second call cost nothing
    assert client.state["ensure"] == 1 and client.state["saved"] == 1


def test_errors_map_to_http_codes(client):
    r = post(client, "Faik" + "1" * 40)
    assert r.status_code == 422 and "not a token mint" in r.json()["detail"]
    assert post(client, "Boom" + "1" * 40).status_code == 502


def test_storage_failure_does_not_break_the_scan(client, monkeypatch):
    def boom(r): raise RuntimeError("supabase down")
    monkeypatch.setattr(M.store, "ensure_token", boom)
    assert post(client, TOKEN).status_code == 200


def test_busy_scanner_returns_503(client, monkeypatch):
    class Full:
        def acquire(self, timeout=None): return False
        def release(self): raise AssertionError("must not release a slot that was never acquired")
    monkeypatch.setattr(M, "slots", Full())
    assert post(client, TOKEN).status_code == 503


def test_slot_is_released_after_failures(client):
    for _ in range(M.MAX_CONCURRENT + 2):             # more failures than slots: would deadlock on a leak
        assert post(client, "Boom" + "1" * 40).status_code == 502


def test_cors_only_for_local_origins(client):
    ok = client.options("/scan", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST"})
    bad = client.options("/scan", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:8000"
    assert "access-control-allow-origin" not in bad.headers


def test_rate_limit_brake(client, monkeypatch):
    monkeypatch.setattr(M, "limiter", M.IpLimiter(max_calls=2))
    assert [post(client, TOKEN).status_code for _ in range(3)] == [200, 200, 429]


# ---------------------------------------------------------------- watchlist
@pytest.fixture
def wl(monkeypatch):
    calls = {"set": [], "add": [], "market": []}
    state = {"known": True, "market": {"pair_address": "P", "symbol": "T"}, "fail": False}

    def set_watch(addr, on):
        if state["fail"]: raise RuntimeError("supabase down")
        calls["set"].append((addr, on)); return state["known"]

    def add(addr, market):
        calls["add"].append(addr); return bool(market and market.get("pair_address"))

    monkeypatch.setattr(M.store, "set_watch", set_watch)
    monkeypatch.setattr(M.store, "add_token_and_watch", add)
    monkeypatch.setattr(M, "best_pair", lambda a: calls["market"].append(a) or state["market"])
    c = TestClient(M.app); c.calls, c.state = calls, state
    return c


def test_star_a_known_token(wl):
    r = wl.post("/watchlist", json={"address": TOKEN})
    assert r.status_code == 200 and r.json() == {"address": TOKEN, "watched": True}
    assert wl.calls["set"] == [(TOKEN, True)] and wl.calls["market"] == []     # no lookup needed


def test_star_an_unknown_token_looks_it_up_and_adds_it(wl):
    wl.state["known"] = False
    assert wl.post("/watchlist", json={"address": TOKEN}).json()["watched"] is True
    assert wl.calls["market"] == [TOKEN] and wl.calls["add"] == [TOKEN]


def test_star_a_token_without_a_market_is_a_clear_404(wl):
    wl.state["known"], wl.state["market"] = False, None
    r = wl.post("/watchlist", json={"address": TOKEN})
    assert r.status_code == 404 and "No trading pair" in r.json()["detail"]


def test_unstar(wl):
    r = wl.delete(f"/watchlist/{TOKEN}")
    assert r.status_code == 200 and r.json() == {"address": TOKEN, "watched": False}
    assert wl.calls["set"] == [(TOKEN, False)]


def test_watchlist_validation_and_errors(wl):
    assert wl.post("/watchlist", json={"address": "nope"}).status_code == 400
    assert wl.delete("/watchlist/nope").status_code == 400
    wl.state["fail"] = True
    assert wl.post("/watchlist", json={"address": TOKEN}).status_code == 502
    assert wl.delete(f"/watchlist/{TOKEN}").status_code == 502


def test_cors_allows_delete_from_the_local_site(wl):
    r = wl.options(f"/watchlist/{TOKEN}", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "DELETE"})
    assert r.status_code == 200 and "DELETE" in r.headers["access-control-allow-methods"]
    bad = wl.options(f"/watchlist/{TOKEN}", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "DELETE"})
    assert "access-control-allow-origin" not in bad.headers
