"""Konfiguracja aplikacji OEL85 Verifier (config.json obok exe/skryptu)."""
import json
import os
import sys
import copy
import logging

log = logging.getLogger(__name__)

APP_NAME = "OEL85 Command Verifier"
APP_VERSION = "1.2.4"


def app_dir():
    """Folder aplikacji - obok .exe (PyInstaller) albo obok skryptu."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")
CONFIG_SCHEMA = 2   # podbijać przy zmianach wymagających migracji starego config.json

DEFAULTS = {
    "connection": {
        "transport": "serial",          # serial | visa | sim
        "port": "",                     # np. COM5
        "port_vid_pid": "",             # np. 1A86:7523 - auto-wybór portu po VID:PID
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout_s": 1.0,               # timeout odczytu odpowiedzi
        "tx_terminator": "\\n",         # zapisane jako escape, patrz decode_term()
        "rx_terminator": "\\n",
        "inter_cmd_delay_ms": 60,       # przerwa po każdej komendzie zapisu (w tym czasie nasłuch na ERR)
        "visa_resource": "",
    },
    "test": {
        "num_abs_tol": 0.0011,          # tolerancja bezwzględna porównania liczb
        "num_rel_tol": 0.002,           # tolerancja względna
        "retries_on_timeout": 1,        # ponowienie zapytania po braku odpowiedzi
        "rst_wait_ms": 20000,           # max czas czekania na stabilną komunikację po *RST
        "remote_before_test": True,     # SYST:REM na starcie
        "local_after_test": True,       # SYST:LOC na końcu
        "rst_after_test": False,        # *RST na końcu
        "require_input_confirm": True,  # potwierdzenie wolnych zacisków przed testami INP
        "reports_dir": "reports",
    },
    "security": {
        # sha256(salt + hasło). Domyślne: ENG = eng123, Admin = admin123 -> ZMIEŃ w panelu Admin.
        # Hasło Admin otwiera też panel ENG.
        "salt": "oel85-reconext",
        "eng_password_hash": "",
        "admin_password_hash": "",
    },
    "ui": {
        "last_groups": [],
    },
}


def decode_term(s):
    """'\\r\\n' z configu -> '\r\n'."""
    return s.encode("utf-8").decode("unicode_escape")


def encode_term(s):
    return s.encode("unicode_escape").decode("utf-8")


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = _merge(DEFAULTS, json.load(f))
            except Exception as e:  # uszkodzony plik -> domyślne
                log.error("Nie można wczytać %s: %s - używam domyślnych", self.path, e)
                self.data = copy.deepcopy(DEFAULTS)
        import auth
        sec = self.data["security"]
        changed = self._migrate()
        if not sec.get("eng_password_hash"):
            # migracja z v1.0 (jedno wspólne hasło) albo domyślne
            sec["eng_password_hash"] = sec.pop("password_hash", "") or auth.hash_password("eng123", sec["salt"])
            changed = True
        if not sec.get("admin_password_hash"):
            sec["admin_password_hash"] = auth.hash_password("admin123", sec["salt"])
            changed = True
        sec.pop("password_hash", None)
        if changed:
            self.save()

    def _migrate(self):
        """Podnosi wartości ze starszych configów, gdy zmieniła się wymagana wartość minimalna.
        (v1.1 zapisało rst_wait_ms=2000; od v1.2.3 potrzeba ~20 s, a stary config nadpisywał nowy default.)"""
        ver = int(self.data.get("config_schema", 1))
        if ver >= CONFIG_SCHEMA:
            return False
        t = self.data["test"]
        if ver < 2 and int(t.get("rst_wait_ms", 0)) < 15000:   # jednorazowo; później decyduje Admin
            log.warning("Migracja configu: rst_wait_ms %s -> 20000", t.get("rst_wait_ms"))
            t["rst_wait_ms"] = 20000
        self.data["config_schema"] = CONFIG_SCHEMA
        return True

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error("Nie można zapisać configu: %s", e)

    # skróty
    @property
    def conn(self):
        return self.data["connection"]

    @property
    def test(self):
        return self.data["test"]

    @property
    def security(self):
        return self.data["security"]

    def reports_path(self):
        p = self.test["reports_dir"]
        if not os.path.isabs(p):
            p = os.path.join(app_dir(), p)
        os.makedirs(p, exist_ok=True)
        return p
