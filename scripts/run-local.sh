#!/usr/bin/env bash
# Starts everything locally: ingest worker, API and the static frontend.
#   ./scripts/run-local.sh              then open http://localhost:8000
#   ./scripts/run-local.sh --autoscan   also run the optional background risk scorer
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

[ -f .env ] || { echo "backend/.env is missing: copy backend/.env.example and fill it in"; exit 1; }
[ -f ../frontend/js/config.js ] || { echo "frontend/js/config.js is missing: copy config.example.js and fill it in"; exit 1; }

[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

pids=()
cleanup() { echo; echo "stopping..."; kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

python ingest.py &   pids+=($!)
if [ "${1:-}" = "--autoscan" ]; then python autoscan.py & pids+=($!); fi
python -m uvicorn api.main:app --port 8080 --log-level warning & pids+=($!)
(cd "$ROOT/frontend" && python -m http.server 8000 --bind 127.0.0.1 >/dev/null 2>&1) & pids+=($!)

echo "Frontend:  http://localhost:8000"
echo "API:       http://localhost:8080/health"
echo "Press Ctrl+C to stop everything."
wait
