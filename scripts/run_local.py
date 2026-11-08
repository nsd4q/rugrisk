#!/usr/bin/env python3
"""Start RugRisk locally on Windows, macOS or Linux (no bash needed).

    python scripts/run_local.py             # ingest worker + API + website
    python scripts/run_local.py --autoscan  # ...plus the optional background risk scorer

Starts: ingest worker, API (port 8080) and the website (port 8000). The risk scorer ("autoscan")
is optional because the screener no longer shows risk; scans run on demand from the Scan page.
Uses backend/.venv if it exists, otherwise the Python you launched this with.
Press Ctrl+C to stop everything.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def venv_python() -> str:
    for rel in ("Scripts/python.exe", "bin/python"):
        p = BACKEND / ".venv" / rel
        if p.exists():
            return str(p)
    return sys.executable


def main() -> int:
    if not (BACKEND / ".env").exists():
        print("backend/.env is missing. Copy backend/.env.example to backend/.env and fill it in.")
        return 1
    if not (FRONTEND / "js" / "config.js").exists():
        print("frontend/js/config.js is missing. Copy config.example.js to config.js and fill it in.")
        return 1

    py = venv_python()
    jobs = [
        ("ingest", [py, "ingest.py"], BACKEND, False),
        ("api", [py, "-m", "uvicorn", "api.main:app", "--port", "8080", "--log-level", "warning"], BACKEND, False),
        # bind to localhost only: don't expose the site to your network
        ("web", [py, "-m", "http.server", "8000", "--bind", "127.0.0.1", "--directory", str(FRONTEND)], ROOT, True),
    ]

    if "--autoscan" in sys.argv[1:]:
        jobs.insert(1, ("autoscan", [py, "autoscan.py"], BACKEND, False))

    procs = []
    try:
        for name, cmd, cwd, quiet in jobs:
            kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
            procs.append((name, subprocess.Popen(cmd, cwd=cwd, **kw)))

        print("\nRugRisk is starting...")
        print("  Website:  http://localhost:8000")
        print("  API:      http://localhost:8080/health")
        print("  Press Ctrl+C to stop everything.\n")

        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"\n'{name}' stopped unexpectedly (exit code {p.returncode}). "
                          "Scroll up for the error message. Stopping everything.")
                    return 1
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()
        for _, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
