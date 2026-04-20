/*
 * esp8266_pusher.ino — DexcomFuffi per ESP8266 (Arduino)
 *
 * Librerie richieste (installabili da Library Manager):
 *   - ArduinoJson  v6.x  (Benoit Blanchon)
 *
 * Board: "Generic ESP8266 Module" oppure "LOLIN(WEMOS) D1 mini"
 * Installa il supporto ESP8266 da: Preferenze → URL aggiuntivi:
 *   https://arduino.esp8266.com/stable/package_esp8266com_index.json
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>

// ── CONFIGURA QUI ─────────────────────────────────────────────
const char* WIFI_SSID     = "NomeRete";
const char* WIFI_PASSWORD = "PasswordWifi";

const char* DEXCOM_USERNAME = "tuo_username_dexcom";
const char* DEXCOM_PASSWORD = "tua_password_dexcom";
const char* DEXCOM_REGION   = "ous";   // "ous" = Europa, "us" = USA

const char* ULANZI_IP = "192.168.178.170";
const int   TARGET_LOW  = 70;
const int   TARGET_HIGH = 180;

// Lascia vuoti per disabilitare YouTube
const char* YOUTUBE_API_KEY    = "";
const char* YOUTUBE_CHANNEL_ID = "";

const unsigned long POLL_INTERVAL  = 150000;  // ms (150s)
const unsigned long RETRY_INTERVAL =  60000;  // ms (60s)
// ──────────────────────────────────────────────────────────────

const char* DEXCOM_APP_ID = "d89443d2-327c-4a6f-89e5-496bbb0317db";

String dexcomBase() {
  return String("ous") == DEXCOM_REGION
    ? "https://shareous1.dexcom.com"
    : "https://share2.dexcom.com";
}

String sessionId = "";
int    lastValue = -1;
int    consecutiveErrors = 0;


// ── WiFi ──────────────────────────────────────────────────────

bool connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.print("WiFi: connessione a ");
  Serial.println(WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 20; i++) {
    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("WiFi OK: ");
      Serial.println(WiFi.localIP());
      return true;
    }
    delay(1000);
  }
  Serial.println("WiFi ERRORE: timeout");
  return false;
}


// ── Dexcom Share API ──────────────────────────────────────────

String dexcomLogin() {
  WiFiClientSecure client;
  client.setInsecure();   // nessuna verifica certificato (uso LAN/home)
  HTTPClient http;

  String url = dexcomBase() + "/ShareWebServices/Services/General/LoginPublisherAccountByName";
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");

  String body = "{\"accountName\":\"" + String(DEXCOM_USERNAME) +
                "\",\"password\":\"" + String(DEXCOM_PASSWORD) +
                "\",\"applicationId\":\"" + String(DEXCOM_APP_ID) + "\"}";

  int code = http.POST(body);
  if (code != 200) {
    Serial.print("Dexcom login HTTP error: ");
    Serial.println(code);
    http.end();
    return "";
  }

  String resp = http.getString();
  http.end();

  // La risposta è "\"session-id\"" — togliamo le virgolette
  resp.replace("\"", "");
  resp.trim();

  if (resp.isEmpty() || resp == "00000000-0000-0000-0000-000000000000") {
    Serial.println("Dexcom login fallito — credenziali errate?");
    return "";
  }

  Serial.print("Dexcom login OK, session: ");
  Serial.println(resp.substring(0, 8) + "...");
  return resp;
}


struct GlucoseReading {
  int    value;
  String trend;
  bool   valid;
};

String trendArrow(const String& t) {
  if (t == "DoubleUp")      return "^^";
  if (t == "SingleUp")      return "^";
  if (t == "FortyFiveUp")   return "/";
  if (t == "Flat")          return "->";
  if (t == "FortyFiveDown") return "\\";
  if (t == "SingleDown")    return "v";
  if (t == "DoubleDown")    return "vv";
  return "?";
}

GlucoseReading dexcomReading() {
  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;

  String url = dexcomBase()
    + "/ShareWebServices/Services/Publisher/ReadPublisherLatestGlucoseValues"
    + "?sessionId=" + sessionId
    + "&minutes=1440&maxCount=1";

  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST("");

  if (code != 200) {
    Serial.print("Dexcom readings HTTP error: ");
    Serial.println(code);
    http.end();
    return {0, "", false};
  }

  String resp = http.getString();
  http.end();

  DynamicJsonDocument doc(512);
  if (deserializeJson(doc, resp) != DeserializationError::Ok || doc.size() == 0) {
    return {0, "", false};
  }

  int    value = doc[0]["Value"].as<int>();
  String trend = trendArrow(doc[0]["Trend"].as<String>());
  return {value, trend, true};
}


// ── Colori glicemia ───────────────────────────────────────────

String glucoseColor(int v) {
  if (v < 55)           return "#FF0000";
  if (v < TARGET_LOW)   return "#FF6600";
  if (v <= TARGET_HIGH) return "#00FF00";
  if (v <= 250)         return "#FFAA00";
  return "#FF0000";
}


// ── Ulanzi ────────────────────────────────────────────────────

bool pushUlanzi(const String& appName, const String& jsonPayload) {
  WiFiClient client;
  HTTPClient http;
  String url = "http://" + String(ULANZI_IP) + "/api/custom?name=" + appName;
  http.begin(client, url);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(jsonPayload);
  http.end();
  return code == 200;
}

bool pushGlucose(int value, const String& trend) {
  String color    = glucoseColor(value);
  int    progress = max(0, min(100, (value - 40) * 100 / (400 - 40)));
  String progCol  = (value >= TARGET_LOW && value <= TARGET_HIGH) ? "#00FF00" : "#FF4444";

  DynamicJsonDocument doc(256);
  doc["text"]       = String(value) + " " + trend;
  doc["color"]      = color;
  doc["icon"]       = "27065";
  doc["progress"]   = progress;
  doc["progressC"]  = progCol;
  doc["progressBC"] = "#333333";
  doc["duration"]   = 0;
  doc["lifetime"]   = 660;
  doc["noScroll"]   = true;

  String payload;
  serializeJson(doc, payload);
  return pushUlanzi("glucose", payload);
}


// ── YouTube ───────────────────────────────────────────────────

int fetchYoutubeSubs() {
  if (strlen(YOUTUBE_API_KEY) == 0 || strlen(YOUTUBE_CHANNEL_ID) == 0)
    return -1;

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;

  String url = "https://www.googleapis.com/youtube/v3/channels"
               "?part=statistics&id=" + String(YOUTUBE_CHANNEL_ID) +
               "&key=" + String(YOUTUBE_API_KEY);

  http.begin(client, url);
  int code = http.GET();
  if (code != 200) { http.end(); return -1; }

  DynamicJsonDocument doc(1024);
  deserializeJson(doc, http.getString());
  http.end();

  if (!doc.containsKey("items") || doc["items"].size() == 0) return -1;
  return doc["items"][0]["statistics"]["subscriberCount"].as<int>();
}

bool pushYoutube(int count) {
  DynamicJsonDocument doc(128);
  doc["text"]     = String(count);
  doc["color"]    = "#FF0000";
  doc["icon"]     = "5029";
  doc["duration"] = 0;
  doc["lifetime"] = 660;
  doc["noScroll"] = true;

  String payload;
  serializeJson(doc, payload);
  return pushUlanzi("youtube", payload);
}


// ── Setup & Loop ──────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\nDexcomFuffi ESP8266 (Arduino) avviato");

  if (!connectWifi()) {
    Serial.println("Nessun WiFi — riavvio tra 30s");
    delay(30000);
    ESP.restart();
  }
}

void loop() {
  // Riconnessione WiFi automatica
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi perso — riconnessione...");
    if (!connectWifi()) {
      delay(RETRY_INTERVAL);
      return;
    }
    sessionId = "";
  }

  // Login Dexcom se necessario
  if (sessionId.isEmpty()) {
    sessionId = dexcomLogin();
    if (sessionId.isEmpty()) {
      consecutiveErrors++;
      if (consecutiveErrors >= 10) {
        Serial.println("10 errori consecutivi — riavvio hardware");
        delay(5000);
        ESP.restart();
      }
      delay(RETRY_INTERVAL);
      return;
    }
  }

  // Glicemia
  GlucoseReading r = dexcomReading();
  if (!r.valid) {
    Serial.println("Lettura Dexcom non valida — nuovo login al prossimo ciclo");
    sessionId = "";
    consecutiveErrors++;
  } else {
    bool ok      = pushGlucose(r.value, r.trend);
    bool changed = r.value != lastValue;
    Serial.print("[glucosio ");
    Serial.print(ok ? "OK" : "ERR");
    Serial.print("] ");
    Serial.print(r.value);
    Serial.print(" mg/dL ");
    Serial.print(r.trend);
    if (changed) Serial.print(" (nuovo)");
    Serial.println();
    lastValue = r.value;
    consecutiveErrors = 0;
  }

  // YouTube (opzionale)
  int subs = fetchYoutubeSubs();
  if (subs >= 0) {
    bool ok = pushYoutube(subs);
    Serial.print("[youtube ");
    Serial.print(ok ? "OK" : "ERR");
    Serial.print("] ");
    Serial.print(subs);
    Serial.println(" iscritti");
  }

  delay(POLL_INTERVAL);
}
