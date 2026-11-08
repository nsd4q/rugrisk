import dotenv
import pytest

import ingest as I


class Resp:
    status_code = 200
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


class FakeHttp:
    """Answers the three queries pick_refresh_addresses() makes, and records them."""
    def __init__(self, watched=(), recent=(), old=()):
        self.watched, self.recent, self.old, self.calls = watched, recent, old, []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        if params.get("watchlisted_at") == "not.is.null": rows = self.watched
        elif str(params.get("pair_created_at", "")).startswith("gte."): rows = self.recent
        elif str(params.get("pair_created_at", "")).startswith("lt."): rows = self.old
        else: raise AssertionError(f"unexpected query {params}")
        return Resp([{"address": a} for a in rows])


def test_selection_covers_starred_new_and_stale_old(monkeypatch):
    h = FakeHttp(watched=["W1", "W2"], recent=["N1", "W1", "N2"], old=["O1", "O2"])
    monkeypatch.setattr(I, "http", h)
    addrs, old_only = I.pick_refresh_addresses()
    assert addrs == ["W1", "W2", "N1", "N2", "O1", "O2"]          # starred first, de-duplicated
    assert old_only == {"O1", "O2"}

    star_q = next(c for c in h.calls if c.get("watchlisted_at") == "not.is.null")
    new_q = next(c for c in h.calls if str(c.get("pair_created_at", "")).startswith("gte."))
    old_q = next(c for c in h.calls if str(c.get("pair_created_at", "")).startswith("lt."))
    assert star_q["limit"] == I.WATCH_REFRESH_MAX and new_q["limit"] == I.REFRESH_MAX_TOKENS
    assert old_q["order"] == "updated_at.asc" and old_q["watchlisted_at"] == "is.null"   # stalest first, never starred ones
    assert old_q["limit"] == I.REFRESH_OLD_PER_LOOP


def test_a_starred_old_token_is_never_treated_as_dead_candidate(monkeypatch):
    monkeypatch.setattr(I, "http", FakeHttp(watched=["W"], old=["W", "O"]))
    _, old_only = I.pick_refresh_addresses()
    assert old_only == {"O"}


def test_old_refresh_can_be_switched_off(monkeypatch):
    h = FakeHttp(recent=["N"], old=["O"])
    monkeypatch.setattr(I, "http", h); monkeypatch.setattr(I, "REFRESH_OLD_PER_LOOP", 0)
    addrs, old_only = I.pick_refresh_addresses()
    assert addrs == ["N"] and old_only == set()
    assert not any(str(c.get("pair_created_at", "")).startswith("lt.") for c in h.calls)


def pair(addr, liq=1000):
    return {"baseToken": {"address": addr}, "pairAddress": "P" + addr, "priceUsd": "1.5", "marketCap": 2e6, "fdv": 3e6,
            "liquidity": {"usd": liq}, "volume": {"m5": 1, "h1": 2, "h24": 3}, "txns": {"h1": {"buys": 4, "sells": 5}},
            "priceChange": {"m5": 1.1, "h1": 2.2, "h24": 3.3}}


def run_refresh(monkeypatch, addrs, old_only, listed):
    upserts = []
    monkeypatch.setattr(I, "pick_refresh_addresses", lambda: (addrs, old_only))
    monkeypatch.setattr(I, "get_json", lambda url: [pair(a) for a in listed if a in url])
    monkeypatch.setattr(I, "sb_upsert", lambda table, rows, conflict: upserts.append(rows))
    monkeypatch.setattr(I.time, "sleep", lambda s: None)
    I.refresh_once()
    return upserts


def test_refresh_updates_live_tokens_and_marks_vanished_old_ones_dead(monkeypatch):
    live, dead = run_refresh(monkeypatch, ["N1", "N2", "O1", "O2", "W1"], {"O1", "O2"}, listed=["N1", "O1", "W1"])
    assert {r["address"] for r in live} == {"N1", "O1", "W1"}
    assert [r["address"] for r in dead] == ["O2"]                       # old + vanished -> dead
    assert dead[0]["liquidity_usd"] == 0 and dead[0]["volume_1h"] == 0
    # N2 is NEW and not listed yet: DexScreener lags behind fresh pools, so it must be left alone
    assert "N2" not in {r["address"] for r in live + dead}


def test_each_upsert_call_has_uniform_keys(monkeypatch):
    live, dead = run_refresh(monkeypatch, ["A", "B", "C"], {"B", "C"}, listed=["A"])
    for batch in (live, dead):
        assert len({tuple(sorted(r)) for r in batch}) <= 1           # PostgREST bulk upserts need identical keys
    assert live[0]["price_usd"] == 1.5 and live[0]["mcap"] == 2e6 and live[0]["buys_1h"] == 4


def test_refresh_batches_of_30(monkeypatch):
    urls = []
    monkeypatch.setattr(I, "pick_refresh_addresses", lambda: ([f"T{i}" for i in range(65)], set()))
    monkeypatch.setattr(I, "get_json", lambda url: urls.append(url) or [])
    monkeypatch.setattr(I, "sb_upsert", lambda *a: None); monkeypatch.setattr(I.time, "sleep", lambda s: None)
    I.refresh_once()
    assert [u.count(",") + 1 for u in urls] == [30, 30, 5]


def test_nothing_to_refresh_is_fine(monkeypatch):
    assert run_refresh(monkeypatch, [], set(), []) == []


def test_env_example_is_parseable_with_inline_comments(tmp_path):
    """The template has trailing '# comments' on values; make sure python-dotenv strips them."""
    env = dotenv.dotenv_values("/".join(__file__.split("/")[:-2]) + "/.env.example")
    assert env["REFRESH_MAX_TOKENS"] == "150" and env["REFRESH_OLD_PER_LOOP"] == "60"
    assert int(env["MIN_LIQUIDITY_USD"]) == 1000 and env["ENABLE_HONEYPOT_SIM"] == "1"
