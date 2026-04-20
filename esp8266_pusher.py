# ============================================================
# esp8266_pusher.py — DexcomFuffi per ESP8266 (MicroPython)
# Carica questo file sull'ESP8266 e rinominalo main.py
# per farlo partire automaticamente all'accensione.
# ============================================================

import network
import utime
import ujson
import urequests
import machine

# ── CONFIGURA QUI ────────────────────────────────────────────
WIFI_SSID     = "NomeRete"
WIFI_PASSWORD = "PasswordWifi"

DEXCOM_USERNAME = "tuo_username_dexcom"
DEXCOM_PASSWORD = "tua_password_dexcom"
DEXCOM_REGION   = "ous"   # "ous" = Europa/internazionale, "us" = USA

ULANZI_IP    = "192.168.178.170"
TARGET_LOW   = 70
TARGET_HIGH  = 180

YOUTUBE_API_KEY   = ""   # lascia vuoto per disabilitare YouTube
YOUTUBE_CHANNEL_ID = ""

POLL_INTERVAL  = 150   # secondi tra un ciclo e l'altro
RETRY_INTERVAL = 60    # secondi di attesa dopo un errore
# ─────────────────────────────────────────────────────────────

DEXCOM_APP_ID = "d89443d2-327c-4a6f-89e5-496bbb0317db"

DEXCOM_BASE = {
    "ous": "https://shareous1.dexcom.com",
    "us":  "https://share2.dexcom.com",
}

TREND_ARROWS = {
    "DoubleUp":       "↑↑",
    "SingleUp":       "↑",
    "FortyFiveUp":    "↗",
    "Flat":           "→",
    "FortyFiveDown":  "↘",
    "SingleDown":     "↓",
    "DoubleDown":     "↓↓",
}


# ── WiFi ─────────────────────────────────────────────────────

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True
    print("WiFi: connessione a", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(20):
        if wlan.isconnected():
            print("WiFi OK:", wlan.ifconfig()[0])
            return True
        utime.sleep(1)
    print("WiFi ERRORE: timeout")
    return False


# ── Dexcom Share API ─────────────────────────────────────────

def dexcom_login():
    base = DEXCOM_BASE.get(DEXCOM_REGION, DEXCOM_BASE["ous"])
    url  = base + "/ShareWebServices/Services/General/LoginPublisherAccountByName"
    body = ujson.dumps({
        "accountName":   DEXCOM_USERNAME,
        "password":      DEXCOM_PASSWORD,
        "applicationId": DEXCOM_APP_ID,
    })
    r = urequests.post(url, data=body, headers={"Content-Type": "application/json"})
    session_id = r.text.strip().strip('"')
    r.close()
    if not session_id or session_id == "00000000-0000-0000-0000-000000000000":
        raise Exception("Login Dexcom fallito — credenziali errate?")
    print("Dexcom login OK, session:", session_id[:8], "...")
    return session_id


def dexcom_reading(session_id):
    base = DEXCOM_BASE.get(DEXCOM_REGION, DEXCOM_BASE["ous"])
    url  = (base
            + "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues"
            + "?sessionId=" + session_id
            + "&minutes=1440&maxCount=1")
    r    = urequests.post(url, headers={"Content-Type": "application/json"})
    data = ujson.loads(r.text)
    r.close()
    if not data:
        return None, None
    entry  = data[0]
    value  = entry["Value"]
    trend  = TREND_ARROWS.get(entry.get("Trend", ""), "?")
    return value, trend


# ── Colori e payload glicemia ─────────────────────────────────

def glucose_color(value):
    if value < 55:       return "#FF0000"
    if value < TARGET_LOW:  return "#FF6600"
    if value <= TARGET_HIGH: return "#00FF00"
    if value <= 250:     return "#FFAA00"
    return "#FF0000"


def build_glucose_payload(value, trend):
    color    = glucose_color(value)
    progress = max(0, min(100, int((value - 40) / (400 - 40) * 100)))
    prog_col = "#00FF00" if TARGET_LOW <= value <= TARGET_HIGH else "#FF4444"
    return {
        "text":       str(value) + " " + trend,
        "color":      color,
        "icon":       "27065",
        "progress":   progress,
        "progressC":  prog_col,
        "progressBC": "#333333",
        "duration":   0,
        "lifetime":   660,
        "noScroll":   True,
    }


# ── YouTube ──────────────────────────────────────────────────

def fetch_youtube_subs():
    if not YOUTUBE_API_KEY or not YOUTUBE_CHANNEL_ID:
        return None
    url = ("https://www.googleapis.com/youtube/v3/channels"
           "?part=statistics"
           "&id=" + YOUTUBE_CHANNEL_ID
           + "&key=" + YOUTUBE_API_KEY)
    try:
        r     = urequests.get(url)
        items = ujson.loads(r.text).get("items", [])
        r.close()
        if not items:
            return None
        return int(items[0]["statistics"]["subscriberCount"])
    except Exception as e:
        print("YouTube errore:", e)
        return None


def build_youtube_payload(count):
    return {
        "text":     str(count),
        "color":    "#FF0000",
        "icon":     "5029",
        "duration": 0,
        "lifetime": 660,
        "noScroll": True,
    }


# ── Ulanzi ───────────────────────────────────────────────────

def push_ulanzi(app_name, payload):
    url = "http://" + ULANZI_IP + "/api/custom?name=" + app_name
    try:
        r = urequests.post(url, json=payload)
        r.close()
        return True
    except Exception as e:
        print("Ulanzi errore (" + app_name + "):", e)
        return False


# ── Loop principale ───────────────────────────────────────────

def run():
    if not connect_wifi():
        print("Nessun WiFi — riavvio tra 30s")
        utime.sleep(30)
        machine.reset()

    session_id       = None
    last_value       = None
    consecutive_err  = 0

    print("DexcomFuffi ESP8266 avviato (target", TARGET_LOW, "-", TARGET_HIGH, "mg/dL)")

    while True:
        try:
            # login se necessario
            if session_id is None:
                session_id = dexcom_login()

            # glicemia
            value, trend = dexcom_reading(session_id)
            if value is None:
                print("Nessuna lettura disponibile")
            else:
                payload = build_glucose_payload(value, trend)
                ok      = push_ulanzi("glucose", payload)
                status  = "OK" if ok else "ERR"
                changed = "(nuovo)" if value != last_value else ""
                print("[glucosio", status + "]", value, "mg/dL", trend, changed)
                last_value = value

            # YouTube (opzionale)
            subs = fetch_youtube_subs()
            if subs is not None:
                ok = push_ulanzi("youtube", build_youtube_payload(subs))
                print("[youtube", "OK]" if ok else "ERR]", subs, "iscritti")

            consecutive_err = 0
            utime.sleep(POLL_INTERVAL)

        except OSError as e:
            # WiFi caduto
            print("Errore rete:", e, "— riconnessione...")
            connect_wifi()
            session_id = None
            consecutive_err += 1
            utime.sleep(RETRY_INTERVAL)

        except Exception as e:
            print("Errore:", e)
            session_id = None
            consecutive_err += 1
            if consecutive_err >= 10:
                print("10 errori consecutivi — riavvio hardware")
                utime.sleep(5)
                machine.reset()
            utime.sleep(RETRY_INTERVAL)


run()
