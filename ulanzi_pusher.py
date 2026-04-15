"""
ulanzi_pusher.py — Legge glicemia da Dexcom Share e la manda all'Ulanzi ogni 5 minuti.

Configurazione in .env:
    DEXCOM_USERNAME=...
    DEXCOM_PASSWORD=...
    DEXCOM_REGION=ous          # ous = Europa/internazionale, us = USA
    ULANZI_IP=192.168.178.170
    TARGET_LOW=70              # soglia bassa target (default 70)
    TARGET_HIGH=180            # soglia alta target (default 180)

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


# ── Colori in base al valore glicemico ────────────────────────────────────────

def glucose_color(value: int, low: int, high: int) -> str:
    if value < 55:
        return "#FF0000"   # rosso acceso — ipoglicemia grave
    if value < low:
        return "#FF6600"   # arancione — sotto target
    if value <= high:
        return "#00FF00"   # verde — in target
    if value <= 250:
        return "#FFAA00"   # giallo — sopra target
    return "#FF0000"       # rosso — iperglicemia grave


def build_payload(value: int, trend_arrow: str, low: int, high: int) -> dict:
    color = glucose_color(value, low, high)

    # Barra di progresso: scala 40–400 mg/dL → 0–100%
    progress = max(0, min(100, int((value - 40) / (400 - 40) * 100)))

    # Colore barra: verde se in range, rosso se fuori
    progress_color = "#00FF00" if low <= value <= high else "#FF4444"

    return {
        "text": f"{value} {trend_arrow}",
        "color": color,
        "progress": progress,
        "progressC": progress_color,
        "progressBC": "#333333",
        "duration": 0,        # rimane fisso finché non arriva aggiornamento
        "lifetime": 660,      # sparisce dopo 11 min se non aggiornato
        "noScroll": True,
    }


# ── Invio all'Ulanzi ──────────────────────────────────────────────────────────

def push_to_ulanzi(ip: str, payload: dict) -> bool:
    url = f"http://{ip}/api/custom?name=glucose"
    try:
        r = requests.post(url, json=payload, timeout=5)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning(f"Ulanzi non raggiungibile: {e}")
        return False


def clear_ulanzi(ip: str):
    """Rimuove l'app glucose dall'Ulanzi (manda payload vuoto)."""
    try:
        requests.post(f"http://{ip}/api/custom?name=glucose", json={}, timeout=5)
    except requests.RequestException:
        pass


# ── Loop principale ───────────────────────────────────────────────────────────

def run():
    cfg = dotenv_values(".env")
    ulanzi_ip   = cfg.get("ULANZI_IP", "192.168.178.170")
    target_low  = int(cfg.get("TARGET_LOW",  70))
    target_high = int(cfg.get("TARGET_HIGH", 180))

    region = cfg.get("DEXCOM_REGION", "ous")
    dex = None
    last_ts = None
    consecutive_errors = 0

    log.info(f"DexcomFuffi -> Ulanzi avviato (target {target_low}-{target_high} mg/dL)")

    while True:
        try:
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
                time.sleep(RETRY_INTERVAL)
                continue

            # Invia solo se la lettura è nuova
            if bg.datetime != last_ts:
                last_ts = bg.datetime
                payload = build_payload(bg.value, bg.trend_arrow, target_low, target_high)
                ok = push_to_ulanzi(ulanzi_ip, payload)
                status = "OK" if ok else "ERR Ulanzi"
                log.info(f"[{status}] {bg.datetime} | {bg.value} mg/dL {bg.trend_arrow} ({bg.trend_description})")
            else:
                log.debug(f"Lettura invariata: {bg.value} mg/dL")

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
