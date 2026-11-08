"""selfcheck.py must flag an insecure or misconfigured setup. A tiny fake Supabase server
lets us test both a locked-down project and a dangerously open one."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import selfcheck as S

SECRET, PUBLISHABLE = "sb_secret_test", "sb_publishable_test"


srv_state = type("S", (), {})()


def make_server(open_to_anon: bool):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _send(self, code, body=b"[]"):
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(body)

        def _is_secret(self): return self.headers.get("apikey") == SECRET

        def do_GET(self):
            if self._is_secret():
                if "watchlisted_at" in self.path and getattr(srv_state, "old_schema", False):
                    return self._send(400, b'{"message":"column tokens.watchlisted_at does not exist"}')
                return self._send(200)
            if "/tokens" in self.path: return self._send(200)                 # public read is intended
            return self._send(200 if open_to_anon else 401)                   # worker_state

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            if self._is_secret() or open_to_anon: return self._send(201)      # open project accepts anon writes
            return self._send(401, b'{"message":"permission denied"}')

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    monkeypatch.setattr(S, "results", {"fail": 0, "warn": 0})

    def setup(url, config_text):
        monkeypatch.setenv("SUPABASE_URL", url)
        cfg = tmp_path / "config.js"
        if config_text is not None:
            cfg.write_text(config_text)
        monkeypatch.setattr(S, "CONFIG_JS", cfg)
    return setup


def cfg(url, key=PUBLISHABLE):
    return f'export const SUPABASE_URL = "{url}";\nexport const SUPABASE_KEY = "{key}";\nexport const API_URL = "http://localhost:8080";\n'


def test_locked_down_project_passes(env):
    srv = make_server(open_to_anon=False)
    url = f"http://127.0.0.1:{srv.server_port}"
    env(url, cfg(url))
    S.check_supabase()
    assert S.results == {"fail": 0, "warn": 0}
    srv.shutdown()


def test_open_project_is_reported_as_a_security_problem(env, capsys):
    srv = make_server(open_to_anon=True)
    url = f"http://127.0.0.1:{srv.server_port}"
    env(url, cfg(url))
    S.check_supabase()
    out = capsys.readouterr().out
    assert S.results["fail"] >= 2
    assert "CANNOT write tokens" in out and "SECURITY PROBLEM" in out
    srv.shutdown()


@pytest.mark.parametrize("text,expect", [
    (None, "not found"),
    (cfg("https://x.supabase.co", "YOUR_PUBLISHABLE_KEY"), "placeholder"),
    (cfg("https://YOUR-PROJECT-REF.supabase.co", "sb_publishable_x"), "placeholder"),
    (cfg("https://x.supabase.co", "sb_secret_oops"), "SECRET key"),
])
def test_bad_frontend_config_fails(env, capsys, text, expect):
    env("https://x.supabase.co", text)
    assert S.read_frontend_config() is None
    out = capsys.readouterr().out
    assert "[FAIL] frontend config" in out and expect in out


def test_url_mismatch_warns_but_passes(env, capsys):
    env("https://a.supabase.co", cfg("https://b.supabase.co"))
    assert S.read_frontend_config() == ("https://b.supabase.co", PUBLISHABLE)
    assert "differs from backend/.env" in capsys.readouterr().out and S.results["warn"] == 1


def test_env_check(monkeypatch, capsys):
    monkeypatch.setattr(S, "results", {"fail": 0, "warn": 0})
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert S.check_env() is False and "missing SUPABASE_URL" in capsys.readouterr().out
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_publishable_swapped")
    assert S.check_env() is False and "holds the PUBLISHABLE key" in capsys.readouterr().out
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    assert S.check_env() is True


def test_network_errors_do_not_crash(env, capsys):
    env("http://127.0.0.1:9", cfg("http://127.0.0.1:9"))      # nothing listens here
    S.guarded(S.check_supabase)
    assert S.results["fail"] == 1 and "[FAIL]" in capsys.readouterr().out


def test_old_schema_without_watchlist_column_is_reported(env, capsys):
    srv = make_server(open_to_anon=False)
    srv_state.old_schema = True
    url = f"http://127.0.0.1:{srv.server_port}"
    env(url, cfg(url))
    try:
        S.check_supabase()
    finally:
        srv_state.old_schema = False
    out = capsys.readouterr().out
    assert "[FAIL] watchlist column" in out and "001_init.sql" in out
    srv.shutdown()
