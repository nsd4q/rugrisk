from datetime import datetime, timezone

import pytest

from scanner import store

ADDR = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
MARKET = {"pair_address": "So11111111111111111111111111111111111111112", "dex": "raydium", "symbol": "BONK",
          "name": "Bonk", "liquidity_usd": 50000, "price_usd": "0.00002", "age_min": 120.0}


class Resp:
    def __init__(self, data=None): self._d = data if data is not None else []
    def raise_for_status(self): pass
    def json(self): return self._d


class FakeHttp:
    def __init__(self, patch_rows=None): self.calls, self.patch_rows = [], patch_rows if patch_rows is not None else [{"address": ADDR}]
    def patch(self, url, params=None, headers=None, json=None, timeout=None):
        self.calls.append(("PATCH", url, params, json)); return Resp(self.patch_rows)
    def post(self, url, params=None, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, params, json)); self.post_headers = headers; return Resp()


@pytest.fixture
def http(monkeypatch):
    h = FakeHttp(); monkeypatch.setattr(store, "http", h); return h


def test_star_and_unstar(http):
    assert store.set_watch(ADDR, True) is True
    method, url, params, body = http.calls[-1]
    assert method == "PATCH" and url.endswith("/rest/v1/tokens") and params["address"] == f"eq.{ADDR}"
    assert body["watchlisted_at"] and datetime.fromisoformat(body["watchlisted_at"]).tzinfo is not None
    assert store.set_watch(ADDR, False) is True
    assert http.calls[-1][3] == {"watchlisted_at": None}


def test_star_unknown_token_reports_false(monkeypatch):
    monkeypatch.setattr(store, "http", FakeHttp(patch_rows=[]))
    assert store.set_watch(ADDR, True) is False


def test_add_unknown_token_and_watch(http):
    assert store.add_token_and_watch(ADDR, MARKET) is True
    post = next(c for c in http.calls if c[0] == "POST")
    row = post[3][0]
    assert row["address"] == ADDR and row["symbol"] == "BONK" and row["watchlisted_at"]
    assert row["price_usd"] == 0.00002 and row["liquidity_usd"] == 50000
    assert "ignore-duplicates" in http.post_headers["Prefer"]       # insert-if-missing, never overwrites a row
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["pair_created_at"])).total_seconds() / 60
    assert 119 < age < 121


@pytest.mark.parametrize("market", [None, {}, {"symbol": "X"}])
def test_add_without_a_market_does_nothing(http, market):
    assert store.add_token_and_watch(ADDR, market) is False
    assert http.calls == []


def test_ensure_token_still_skips_pairless_scans(http):
    assert store.ensure_token({"address": ADDR, "market": None}) is False
    assert store.ensure_token({"address": ADDR, "market": MARKET}) is True


def test_bad_price_is_tolerated():
    assert store._market_row(ADDR, {**MARKET, "price_usd": "n/a"})["price_usd"] is None
