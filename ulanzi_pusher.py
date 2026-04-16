"""
ulanzi_pusher.py — Legge glicemia da Dexcom Share e iscritti YouTube,
                   li manda all'Ulanzi (AWTRIX3) ogni 5 minuti.

Configurazione in .env:
    DEXCOM_USERNAME=...
    DEXCOM_PASSWORD=...
    DEXCOM_REGION=ous          # ous = Europa/internazionale, us = USA
    ULANZI_IP=192.168.178.170
    TARGET_LOW=70              # soglia bassa target (default 70)
    TARGET_HIGH=180            # soglia alta target (default 180)
    YOUTUBE_API_KEY=AIza...
    YOUTUBE_CHANNEL_ID=UCxxxx...

Avvio:
    pip install pydexcom python-dotenv requests
    python ulanzi_pusher.py
"""

import sys
import time
import logging
import requests
from dotenv import dotenv_values
from pydexcom import Dexcom, errors as dex_errors

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("ulanzi_pusher.log", encoding="utf-8"),
        logging.StreamHandler(stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)),
    ],
)
log = logging.getLogger("ulanzi")

POLL_INTERVAL = 300   # secondi tra un check e l'altro
RETRY_INTERVAL = 60   # secondi di attesa dopo un errore


# ── Glucosio ──────────────────────────────────────────────────────────────────

def glucose_color(value: int, low: int, high: int) -> str:
    if value < 55:
        return "#FF0000"
    if value < low:
        return "#FF6600"
    if value <= high:
        return "#00FF00"
    if value <= 250:
        return "#FFAA00"
    return "#FF0000"


def build_glucose_payload(value: int, trend_arrow: str, low: int, high: int) -> dict:
    color = glucose_color(value, low, high)
    progress = max(0, min(100, int((value - 40) / (400 - 40) * 100)))
    progress_color = "#00FF00" if low <= value <= high else "#FF4444"
    return {
        "text": f"{value} {trend_arrow}",
        "color": color,
        "progress": progress,
        "progressC": progress_color,
        "progressBC": "#333333",
        "duration": 0,
        "lifetime": 660,
        "noScroll": True,
    }


# ── YouTube ───────────────────────────────────────────────────────────────────

def format_subscribers(count: int) -> str:
    """Formatta il numero iscritti: 4940 → '4.9K', 1200000 → '1.2M'"""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def fetch_youtube_subscribers(api_key: str, channel_id: str) -> int | None:
    """Ritorna il numero di iscritti del canale, o None in caso di errore."""
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "part": "statistics",
        "id": channel_id,
        "key": api_key,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            log.warning("YouTube: canale non trovato")
            return None
        return int(items[0]["statistics"]["subscriberCount"])
    except Exception as e:
        log.warning(f"YouTube fetch error: {e}")
        return None


def build_youtube_payload(count: int) -> dict:
    return {
        "text": format_subscribers(count),
        "color": "#FFFFFF",
        "icon": "1003",          # icona YouTube su AWTRIX3 (ID LaMetric icon store)
        "duration": 0,
        "lifetime": 660,
        "noScroll": True,
    }


# ── Ulanzi ────────────────────────────────────────────────────────────────────

def push_to_ulanzi(ip: str, app_name: str, payload: dict) -> bool:
    url = f"http://{ip}/api/custom?name={app_name}"
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"Ulanzi ({app_name}) non raggiungibile: {e}")
        return False


def clear_ulanzi(ip: str):
    for app in ("glucose", "youtube"):
        try:
            requests.post(f"http://{ip}/api/custom?name={app}", json={}, timeout=5)
        except requests.RequestException:
            pass


# ── Loop principale ───────────────────────────────────────────────────────────

def run():
    cfg = dotenv_values(".env")
    ulanzi_ip    = cfg.get("ULANZI_IP", "192.168.178.170")
    target_low   = int(cfg.get("TARGET_LOW", 70))
    target_high  = int(cfg.get("TARGET_HIGH", 180))
    yt_api_key   = cfg.get("YOUTUBE_API_KEY", "")
    yt_channel   = cfg.get("YOUTUBE_CHANNEL_ID", "")

    region = cfg.get("DEXCOM_REGION", "ous")
    dex = None
    last_ts = None
    consecutive_errors = 0

    yt_enabled = bool(yt_api_key and yt_channel)
    log.info(f"DexcomFuffi avviato (target {target_low}-{target_high} mg/dL | YouTube: {'ON' if yt_enabled else 'OFF'})")

    while True:
        try:
            # ── Glucosio ──
            if dex is None:
                log.info("Connessione a Dexcom Share...")
                dex = Dexcom(
                    username=cfg["DEXCOM_USERNAME"],
                    password=cfg["DEXCOM_PASSWORD"],
                    region=region,
                )
                log.info("Connesso")

            bg = dex.get_current_glucose_reading()

            if bg is None:
                log.warning("Nessuna lettura (sensore in riscaldamento o non disponibile)")
            elif bg.datetime != last_ts:
                last_ts = bg.datetime
                payload = build_glucose_payload(bg.value, bg.trend_arrow, target_low, target_high)
                ok = push_to_ulanzi(ulanzi_ip, "glucose", payload)
                status = "OK" if ok else "ERR Ulanzi"
                log.info(f"[glucosio {status}] {bg.datetime} | {bg.value} mg/dL {bg.trend_arrow} ({bg.trend_description})")
            else:
                log.debug(f"Lettura glucosio invariata: {bg.value} mg/dL")

            # ── YouTube ──
            if yt_enabled:
                subs = fetch_youtube_subscribers(yt_api_key, yt_channel)
                if subs is not None:
                    yt_payload = build_youtube_payload(subs)
                    ok = push_to_ulanzi(ulanzi_ip, "youtube", yt_payload)
                    status = "OK" if ok else "ERR Ulanzi"
                    log.info(f"[youtube {status}] {format_subscribers(subs)} iscritti ({subs})")

            consecutive_errors = 0
            time.sleep(POLL_INTERVAL)

        except dex_errors.DexcomError as e:
            log.warning(f"Errore Dexcom: {e} — riprovo in {RETRY_INTERVAL}s")
            dex = None
            consecutive_errors += 1
            time.sleep(RETRY_INTERVAL)

        except KeyboardInterrupt:
            log.info("Interrotto dall'utente")
            clear_ulanzi(ulanzi_ip)
            break

        except Exception as e:
            log.error(f"Errore imprevisto: {e}")
            dex = None
            consecutive_errors += 1
            if consecutive_errors >= 10:
                log.critical("10 errori consecutivi — arresto")
                break
            time.sleep(RETRY_INTERVAL)


if __name__ == "__main__":
    run()
