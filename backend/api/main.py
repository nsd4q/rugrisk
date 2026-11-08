"""RugRisk local scan API.   Run:  python -m uvicorn api.main:app --port 8080

POST   /scan            {"address": "<mint>"}
POST   /watchlist       {"address": "<mint>"}   star a token
DELETE /watchlist/<mint>                        unstar a token
GET    /health

There is NO login: this API is meant to run on your own computer and listen on localhost only.
Do not expose it to the internet.
"""
import logging
import os
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from scanner import store
from scanner.constants import SOL_ADDR
from scanner.data import best_pair
from scanner.engine import ScanError, run_scan
from scanner.rpc import Rpc

from .limits import IpLimiter, TtlCache

log = logging.getLogger("api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ORIGINS = [o.strip() for o in os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if o.strip()]
CACHE_TTL = int(os.getenv("SCAN_CACHE_TTL_S", "180"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SCANS", "4"))
REQUESTS_PER_MIN = int(os.getenv("REQUESTS_PER_MIN", "60"))

app = FastAPI(title="RugRisk API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

rpc = Rpc()
cache = TtlCache(ttl=CACHE_TTL)
limiter = IpLimiter(max_calls=REQUESTS_PER_MIN, window=60)
slots = threading.BoundedSemaphore(MAX_CONCURRENT)


class ScanBody(BaseModel):
    address: str = Field(..., max_length=64)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/scan")
def scan(body: ScanBody, request: Request):
    # A global brake that protects your RPC/Jupiter quota from runaway clicking or loops.
    if not limiter.allow("local"):
        raise HTTPException(429, "Too many scans in a minute, please wait a moment")

    address = body.address.strip()
    if not SOL_ADDR.match(address):
        raise HTTPException(400, "That doesn't look like a valid Solana address")

    hit = cache.get(address)   # scanned very recently: no need to spend RPC calls again
    if hit:
        return {**hit, "cached": True}

    if not slots.acquire(timeout=10):
        raise HTTPException(503, "Scanner is busy, try again in a few seconds")
    try:
        result = run_scan(address, rpc)
    except ScanError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.error("scan crashed for %s: %s", address, e)
        raise HTTPException(502, "Scan failed, please try again")
    finally:
        slots.release()

    # Best effort: store it so the token and its score show up in the screener.
    try:
        if store.ensure_token(result):
            store.save_scan(result)
    except Exception as e:
        log.warning("could not store scan for %s: %s", address, e)

    cache.set(address, result)
    return {**result, "cached": False}


# ---------------------------------------------------------------- watchlist
def _valid(address: str) -> str:
    address = address.strip()
    if not SOL_ADDR.match(address):
        raise HTTPException(400, "That doesn't look like a valid Solana address")
    return address


@app.post("/watchlist")
def watchlist_add(body: ScanBody):
    address = _valid(body.address)
    try:
        if store.set_watch(address, True):
            return {"address": address, "watched": True}
        # Not in the table yet (never seen by the screener): look it up and add it.
        market = best_pair(address)
        if not store.add_token_and_watch(address, market):
            raise HTTPException(404, "No trading pair found for this token, so it can't be tracked yet")
        return {"address": address, "watched": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("watchlist add failed for %s: %s", address, e)
        raise HTTPException(502, "Couldn't update the watchlist, please try again")


@app.delete("/watchlist/{address}")
def watchlist_remove(address: str):
    address = _valid(address)
    try:
        store.set_watch(address, False)
    except Exception as e:
        log.error("watchlist remove failed for %s: %s", address, e)
        raise HTTPException(502, "Couldn't update the watchlist, please try again")
    return {"address": address, "watched": False}
