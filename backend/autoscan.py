"""Autoscan worker: scans newly ingested tokens and writes risk scores for the screener.

Run alongside ingest.py:   python autoscan.py
Tune with env vars: AUTOSCAN_BATCH, AUTOSCAN_DELAY_S, AUTOSCAN_IDLE_S
"""
import logging
import os
import time

from scanner.engine import run_scan
from scanner.rpc import Rpc
from scanner.store import fetch_unscanned, mark_scanned, save_scan

BATCH = int(os.getenv("AUTOSCAN_BATCH", "5"))
DELAY = float(os.getenv("AUTOSCAN_DELAY_S", "2.0"))   # pause between scans (RPC quota)
IDLE = float(os.getenv("AUTOSCAN_IDLE_S", "5"))
MAX_FAILS = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("autoscan")


def main():
    rpc = Rpc()
    fails: dict[str, int] = {}
    log.info("autoscan started (helius=%s)", rpc.is_helius)

    try:
        while True:
            try:
                batch = fetch_unscanned(BATCH)
            except Exception as e:
                log.error("queue fetch failed: %s", e)
                time.sleep(IDLE)
                continue

            if not batch:
                time.sleep(IDLE)
                continue

            for addr in batch:
                try:
                    res = run_scan(addr, rpc)
                    save_scan(res)
                    log.info("%s  %3d %-8s coverage %d%%", addr[:8], res["score"], res["level"],
                             int(res["coverage"] * 100))
                    fails.pop(addr, None)
                except Exception as e:
                    fails[addr] = fails.get(addr, 0) + 1
                    log.warning("%s scan failed (%d/%d): %s", addr[:8], fails[addr], MAX_FAILS, e)
                    if fails[addr] >= MAX_FAILS:
                        try:
                            mark_scanned(addr)  # give up so the queue doesn't get stuck
                        except Exception as e2:
                            log.error("mark_scanned failed: %s", e2)
                        fails.pop(addr, None)
                time.sleep(DELAY)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
