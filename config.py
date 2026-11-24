# config.py
"""Konfiguracja aplikacji Combo Test PSU (Hi-Pot Chroma 19052 + obciążenie OEL8512)."""
import os
import socket

from app_paths import app_dir

APP_NAME = "Combo Test PSU"
APP_VERSION = "1.1.1"


def decode_term(s: str) -> str:
    """'\\r\\n' zapisane w config.json -> '\r\n'. Wymagane przez transport.py (baseline OEL)."""
    return s.encode("utf-8").decode("unicode_escape")


def encode_term(s: str) -> str:
    return s.encode("unicode_escape").decode("utf-8")


class Config:
    # Kolory (jak w Hi-Pot PSU)
    COLOR_PRIMARY = "#375ea9"
    COLOR_ACCENT = "#4CAF50"
    COLOR_BG = "#f5f5f5"
    COLOR_WHITE = "#FFFFFF"
    COLOR_ERROR = "#f44336"
    COLOR_WARN = "#FF9800"

    # Okno
    WINDOW_WIDTH = 1280
    WINDOW_HEIGHT = 860
    WINDOW_TITLE = f"Reconext {APP_NAME}"

    # RS232 - Chroma Hi-Pot
    DEFAULT_COM_PORT = "COM6"
    DEFAULT_BAUDRATE = 9600

    # RS232 / USB-COM - OWON OEL8512 (parametry z baseline OEL85 Verifier 1.2.4)
    OEL_PORT = "COM3"
    OEL_VID_PID = ""                 # np. 1A86:7523 - auto-wybór portu po VID:PID
    OEL_BAUDRATE = 115200
    OEL_TIMEOUT = 1.0                # [s]
    OEL_DELAY_MS = 60                # przerwa po zapisie; w tym oknie OEL odsyła ERR
    OEL_TERMINATOR = "\\n"           # TX i RX

    # Interlock klapy - Arduino (linie OPEN / CLOSED, jak w Hi-Pot PSU)
    INTERLOCK_PORT = "COM5"
    INTERLOCK_BAUDRATE = 9600
    INTERLOCK_ENABLED = True

    # Przekaźnik (moduł przełączający zasilacz między testem obciążenia a Hi-Pot)
    RELAY_PORT = "COM7"              # ten sam port co interlock = wspólne połączenie
    RELAY_BAUDRATE = 9600
    RELAY_CMD_LOAD = "load"          # komenda: test obciążenia (stan domyślny modułu)
    RELAY_CMD_HIPOT = "hipot"        # komenda: Hi-Pot
    RELAY_TERMINATOR = "\\n"
    RELAY_ACK = ""                   # oczekiwana odpowiedź (fragment); pusto = bez potwierdzenia
    RELAY_TIMEOUT = 2.0              # [s] czas na odpowiedź, gdy RELAY_ACK ustawione
    RELAY_SETTLE_MS = 500            # [ms] po komendzie, zanim ruszy test
    RELAY_REVERT_S = 30.0            # [s] moduł sam wraca do LOAD po braku aktywności
    HV_DISCHARGE_S = 1.0             # [s] po Hi-Pot, zanim przekaźnik wróci na LOAD

    # Ogólne
    STATION_NAME = ""                # pusto = nazwa komputera
    DISPLAY_MODE = "both"            # both = oba testy na ekranie | sequential = jeden po drugim
    AUTO_SAVE_RESULTS = True
    TEST_TIMEOUT = 300               # [s] twardy limit jednego testu Hi-Pot
    LOG_DIR = "logs"                 # raporty Hi-Pot (format Chroma 19052, odbiór IFS)
    LOAD_LOG_DIR = "logs_load"       # raporty testu obciążenia (osobny plik na test)
    ENG_LOG_DIR = "logs_eng"         # testy w pętli z panelu ENG (nie trafiają do IFS)

    # Tryb symulacji (tylko z linii poleceń: --sim) - nie zapisywany
    SIMULATION = False

    def __init__(self, simulation: bool = False):
        from settings_manager import SettingsManager
        self.SIMULATION = simulation
        self._sm = SettingsManager()
        self._sm.load_config(self)

    def save(self) -> bool:
        return self._sm.save_config(self)

    @property
    def station(self) -> str:
        return (self.STATION_NAME or "").strip() or socket.gethostname().upper()

    @staticmethod
    def _resolve(path: str, default: str) -> str:
        path = (path or default).strip()
        if not os.path.isabs(path) and not path.startswith("\\\\"):
            path = os.path.join(app_dir(), path)
        return path

    def resolve_log_dir(self) -> str:
        return self._resolve(self.LOG_DIR, "logs")

    def resolve_load_log_dir(self) -> str:
        return self._resolve(self.LOAD_LOG_DIR, "logs_load")

    def resolve_eng_log_dir(self) -> str:
        return self._resolve(self.ENG_LOG_DIR, "logs_eng")

    def oel_conn(self) -> dict:
        """Słownik połączenia w formacie transport.build_transport() (baseline)."""
        return {
            "transport": "sim" if self.SIMULATION else "serial",
            "port": self.OEL_PORT,
            "baudrate": int(self.OEL_BAUDRATE),
            "bytesize": 8, "parity": "N", "stopbits": 1,
            "timeout_s": float(self.OEL_TIMEOUT),
            "tx_terminator": self.OEL_TERMINATOR,
            "rx_terminator": self.OEL_TERMINATOR,
            "inter_cmd_delay_ms": int(self.OEL_DELAY_MS),
            "visa_resource": "",
        }
