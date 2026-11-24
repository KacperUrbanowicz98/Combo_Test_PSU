/*
  fixture_controller.ino — Arduino (Leonardo) / STM: interlock klapy + przekaźniki torów
  Stanowisko: Reconext Combo Test PSU (Hi-Pot Chroma 19052 + obciążenie OEL8512)

  SZKIC REFERENCYJNY — przed użyciem sprawdź z osobą od elektryki:
  numery pinów, poziom aktywny przekaźników, logikę czujnika klapy
  (w Hi-Pot PSU: czujnik na pinie 6 -> GND).

  PROTOKÓŁ (9600 8N1, linie zakończone \n) — zgodny z interlock.py:
    -> PC co 200 ms:      OPEN | CLOSED
    <- PC:                PATH OFF | PATH HIPOT | PATH LOAD | PATH?
    -> PC (odpowiedź):    OK PATH <tor>  |  ERR LID_OPEN  |  ERR BAD_PATH  |  ERR UNKNOWN
    -> PC (PATH? / wymuszone wyłączenie):  PATH <tor>

  BEZPIECZEŃSTWO (realizowane tutaj, niezależnie od aplikacji PC):
    * break-before-make: zawsze wszystkie przekaźniki OFF, przerwa, potem nowy tor,
    * HIPOT i LOAD tylko przy zamkniętej klapie,
    * otwarcie klapy => natychmiast wszystkie przekaźniki OFF,
    * watchdog: brak komend z PC przez 5 s przy torze != OFF => OFF
      (aplikacja wysyła PATH? co 1 s jako keepalive).
  Uwaga: to NIE zastępuje sprzętowego odcięcia HV przez krańcówkę klapy w torze HV.
*/

// ---------------- KONFIGURACJA SPRZĘTU ----------------
const uint8_t PIN_LID        = 6;     // czujnik klapy (INPUT_PULLUP)
const uint8_t LID_CLOSED_LVL = LOW;   // poziom pinu przy ZAMKNIĘTEJ klapie

// Tor HI-POT: L+N zasilacza -> HV Chromy, wyjście DC -> RTN
const uint8_t PIN_RLY_HIPOT  = 8;
// Tor LOAD: 230 V AC -> wejście zasilacza, wyjście DC -> OEL8512
const uint8_t PIN_RLY_AC     = 9;
const uint8_t PIN_RLY_DC     = 10;
const uint8_t RLY_ON         = HIGH;  // poziom załączający przekaźnik
const uint8_t RLY_OFF        = LOW;

const unsigned long STATUS_MS   = 200;   // co ile wysyłać OPEN/CLOSED
const unsigned long BREAK_MS    = 100;   // przerwa break-before-make
const unsigned long SETTLE_MS   = 50;    // ustalenie styków po załączeniu
const unsigned long WATCHDOG_MS = 5000;  // brak komend z PC -> OFF
const unsigned long DEBOUNCE_MS = 30;

enum Path { P_OFF, P_HIPOT, P_LOAD };
Path path = P_OFF;
unsigned long lastStatus = 0, lastCmd = 0, lidChangeAt = 0;
bool lidClosed = false, lidRaw = false;
String rx;

const char* pathName(Path p) { return p == P_HIPOT ? "HIPOT" : p == P_LOAD ? "LOAD" : "OFF"; }

void allOff() {
  digitalWrite(PIN_RLY_HIPOT, RLY_OFF);
  digitalWrite(PIN_RLY_AC, RLY_OFF);
  digitalWrite(PIN_RLY_DC, RLY_OFF);
}

void applyPath(Path p) {
  allOff();                         // break-before-make
  delay(BREAK_MS);
  if (p == P_HIPOT) {
    digitalWrite(PIN_RLY_HIPOT, RLY_ON);
  } else if (p == P_LOAD) {
    digitalWrite(PIN_RLY_DC, RLY_ON);   // najpierw wyjście na OEL,
    delay(SETTLE_MS);
    digitalWrite(PIN_RLY_AC, RLY_ON);   // potem 230 V na zasilacz
  }
  if (p != P_OFF) delay(SETTLE_MS);
  path = p;
}

void forceOff() {
  if (path != P_OFF) {
    allOff();
    path = P_OFF;
    Serial.println("PATH OFF");
  }
}

void handle(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) return;
  lastCmd = millis();
  if (cmd == "PATH?") {
    Serial.print("PATH "); Serial.println(pathName(path));
    return;
  }
  if (cmd.startsWith("PATH ")) {
    String t = cmd.substring(5);
    Path p;
    if (t == "OFF") p = P_OFF;
    else if (t == "HIPOT") p = P_HIPOT;
    else if (t == "LOAD") p = P_LOAD;
    else { Serial.println("ERR BAD_PATH"); return; }
    if (p != P_OFF && !lidClosed) { allOff(); path = P_OFF; Serial.println("ERR LID_OPEN"); return; }
    applyPath(p);
    Serial.print("OK PATH "); Serial.println(pathName(path));
    return;
  }
  Serial.println("ERR UNKNOWN");
}

void setup() {
  allOff();                                   // przed ustawieniem trybu pinów
  pinMode(PIN_RLY_HIPOT, OUTPUT);
  pinMode(PIN_RLY_AC, OUTPUT);
  pinMode(PIN_RLY_DC, OUTPUT);
  allOff();
  pinMode(PIN_LID, INPUT_PULLUP);
  Serial.begin(9600);
  lidRaw = lidClosed = (digitalRead(PIN_LID) == LID_CLOSED_LVL);
  lastCmd = millis();
}

void loop() {
  unsigned long now = millis();

  // klapa z debouncingiem; otwarcie => natychmiast OFF
  bool raw = (digitalRead(PIN_LID) == LID_CLOSED_LVL);
  if (raw != lidRaw) { lidRaw = raw; lidChangeAt = now; }
  if (!raw) { lidClosed = false; forceOff(); }
  else if (!lidClosed && now - lidChangeAt >= DEBOUNCE_MS) { lidClosed = true; }

  // watchdog PC
  if (path != P_OFF && now - lastCmd > WATCHDOG_MS) forceOff();

  // komendy
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n') { handle(rx); rx = ""; }
    else if (ch != '\r' && rx.length() < 32) rx += ch;
  }

  if (now - lastStatus >= STATUS_MS) {
    lastStatus = now;
    Serial.println(lidClosed ? "CLOSED" : "OPEN");
  }
}
