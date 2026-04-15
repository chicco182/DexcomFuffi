"""
poller.py — Continuous Dexcom glucose poller.

Reads from Dexcom Share API every 5 minutes and stores to SQLite.
Run with: venv/bin/python poller.py
"""

import os
import time
import logging
from datetime import datetime, timezone

from pydexcom import Dexcom, errors as dex_errors
from dotenv import dotenv_values

from db import init_db, insert_reading, get_latest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("poller.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dexcom")

POLL_INTERVAL = 300  # seconds (Dexcom updates every 5 min)
RETRY_INTERVAL = 60  # retry after error


def load_config() -> dict:
    cfg = dotenv_values(".env")
    required = ["DEXCOM_USERNAME", "DEXCOM_PASSWORD"]
    for k in required:
        if not cfg.get(k):
            raise RuntimeError(f"Missing {k} in .env")
    return cfg


def connect(cfg: dict) -> Dexcom:
    region = cfg.get("DEXCOM_REGION", "ous")  # ous = Europe/international
    return Dexcom(
        username=cfg["DEXCOM_USERNAME"],
        password=cfg["DEXCOM_PASSWORD"],
        region=region,
    )


def poll_once(dex: Dexcom) -> bool:
    """Fetch current reading and store it. Returns True on success."""
    bg = dex.get_current_glucose_reading()
    if bg is None:
        log.warning("No reading returned (sensor warming up or unavailable)")
        return False

    inserted = insert_reading(
        timestamp=bg.datetime.replace(tzinfo=timezone.utc) if bg.datetime.tzinfo is None else bg.datetime,
        value=bg.value,
        trend_code=bg.trend,
        trend_arrow=bg.trend_arrow,
        trend_desc=bg.trend_description,
    )

    status = "NEW" if inserted else "DUP"
    log.info(f"[{status}] {bg.datetime} | {bg.value} mg/dL {bg.trend_arrow} ({bg.trend_description})")
    return True


def run():
    cfg = load_config()
    init_db()
    log.info("DexcomFuffi poller started")

    dex = None
    consecutive_errors = 0

    while True:
        try:
            if dex is None:
                log.info("Connecting to Dexcom Share...")
                dex = connect(cfg)
                log.info("Connected")

            poll_once(dex)
            consecutive_errors = 0
            time.sleep(POLL_INTERVAL)

        except dex_errors.DexcomError as e:
            log.warning(f"Dexcom API error: {e} — reconnecting in {RETRY_INTERVAL}s")
            dex = None
            consecutive_errors += 1
            time.sleep(RETRY_INTERVAL)

        except Exception as e:
            log.error(f"Unexpected error: {e}")
            dex = None
            consecutive_errors += 1
            if consecutive_errors >= 10:
                log.critical("10 consecutive errors — stopping")
                break
            time.sleep(RETRY_INTERVAL)


if __name__ == "__main__":
    run()
