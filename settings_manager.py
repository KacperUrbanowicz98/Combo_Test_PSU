# settings_manager.py
"""Zapis i odczyt ustawień (config.json) i profili (models.json) obok aplikacji."""
import json
import os
import tempfile

from app_paths import data_path, seed_path

CONFIG_FILE = data_path("config.json")
MODELS_FILE = data_path("models.json")

# Lista profili domyślnych skasowanych świadomie w panelu ENG — bez tego
# usunięty profil fabryczny wracał po restarcie.
_DELETED_KEY = "__deleted_defaults__"

# (sekcja, klucz w JSON, atrybut Config, typ)
_FIELDS = [
    ("hipot",   "com_port",          "DEFAULT_COM_PORT",    str),
    ("hipot",   "baudrate",          "DEFAULT_BAUDRATE",    int),
    ("oel",     "com_port",          "OEL_PORT",            str),
    ("oel",     "vid_pid",           "OEL_VID_PID",         str),
    ("oel",     "baudrate",          "OEL_BAUDRATE",        int),
    ("oel",     "timeout_s",         "OEL_TIMEOUT",         float),
    ("oel",     "inter_cmd_delay_ms", "OEL_DELAY_MS",       int),
    ("oel",     "terminator",        "OEL_TERMINATOR",      str),
    ("interlock", "com_port",        "INTERLOCK_PORT",      str),
    ("interlock", "baudrate",        "INTERLOCK_BAUDRATE",  int),
    ("interlock", "enabled",         "INTERLOCK_ENABLED",   bool),
    ("relay",   "com_port",          "RELAY_PORT",          str),
    ("relay",   "baudrate",          "RELAY_BAUDRATE",      int),
    ("relay",   "cmd_load",          "RELAY_CMD_LOAD",      str),
    ("relay",   "cmd_hipot",         "RELAY_CMD_HIPOT",     str),
    ("relay",   "terminator",        "RELAY_TERMINATOR",    str),
    ("relay",   "ack",               "RELAY_ACK",           str),
    ("relay",   "timeout_s",         "RELAY_TIMEOUT",       float),
    ("relay",   "settle_ms",         "RELAY_SETTLE_MS",     int),
    ("relay",   "revert_s",          "RELAY_REVERT_S",      float),
    ("relay",   "hv_discharge_s",    "HV_DISCHARGE_S",      float),
    ("general", "station_name",      "STATION_NAME",        str),
    ("general", "display_mode",      "DISPLAY_MODE",        str),
    ("general", "auto_save_results", "AUTO_SAVE_RESULTS",   bool),
    ("general", "test_timeout",      "TEST_TIMEOUT",        int),
    ("general", "log_dir",           "LOG_DIR",             str),
    ("general", "load_log_dir",      "LOAD_LOG_DIR",        str),
    ("general", "eng_log_dir",       "ENG_LOG_DIR",         str),
]


def _atomic_write_json(path: str, data) -> None:
    """Zapis przez plik tymczasowy + os.replace (odporny na zanik zasilania/sieci)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _read_json(path: str):
    """Czyta JSON obok aplikacji; jeśli brak — wzorzec spakowany w EXE."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    seed = seed_path(os.path.basename(path))
    if seed:
        with open(seed, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


class SettingsManager:

    # ------------------------------------------------------------------ #
    # KONFIGURACJA                                                        #
    # ------------------------------------------------------------------ #
    def load_config(self, cfg) -> None:
        try:
            data = _read_json(CONFIG_FILE)
        except Exception as e:
            print(f"[SETTINGS] Błąd odczytu {CONFIG_FILE}: {e}")
            return
        if data is None:
            self.save_config(cfg)
            return

        # config.json z Hi-Pot PSU (sekcja "rs232") — przejmij porty Chromy
        # i Arduino, żeby stanowisko ruszyło bez ponownego wpisywania.
        rs = data.get("rs232")
        if isinstance(rs, dict) and "hipot" not in data:
            data.setdefault("hipot", {"com_port": rs.get("com_port"),
                                      "baudrate": rs.get("baudrate")})
            data.setdefault("interlock", {"com_port": rs.get("interlock_port"),
                                          "baudrate": rs.get("interlock_baudrate"),
                                          "enabled": rs.get("interlock_enabled")})
            gen = data.get("general") or {}
            data["general"] = gen
        fx = data.get("fixture")              # config.json z Combo Test PSU 1.0.0
        if isinstance(fx, dict) and "interlock" not in data:
            data["interlock"] = {"com_port": fx.get("com_port"), "baudrate": fx.get("baudrate"),
                                 "enabled": fx.get("interlock_enabled")}

        for section, key, attr, typ in _FIELDS:
            sec = data.get(section)
            if not isinstance(sec, dict) or sec.get(key) is None:
                continue
            try:
                setattr(cfg, attr, typ(sec[key]))
            except (TypeError, ValueError) as e:
                print(f"[SETTINGS] {section}.{key}: nieprawidłowa wartość ({e}) — domyślna")
        if cfg.DISPLAY_MODE not in ("both", "sequential"):
            cfg.DISPLAY_MODE = "both"

    def save_config(self, cfg) -> bool:
        data = {}
        for section, key, attr, typ in _FIELDS:
            data.setdefault(section, {})[key] = getattr(cfg, attr)
        try:
            _atomic_write_json(CONFIG_FILE, data)
            return True
        except Exception as e:
            print(f"[SETTINGS] Błąd zapisu {CONFIG_FILE}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # PROFILE                                                             #
    # ------------------------------------------------------------------ #
    def load_models(self, default_models: dict, migrate=None, is_valid=None) -> dict:
        """
        migrate(dict) -> (dict, changed)  — normalizacja kluczy/pól starszych plików
        is_valid(key, value) -> bool      — profil kompletny (Hi-Pot + obciążenie)
        """
        try:
            saved = _read_json(MODELS_FILE)
        except Exception as e:
            print(f"[SETTINGS] Błąd odczytu {MODELS_FILE}: {e}")
            return {k: json.loads(json.dumps(v)) for k, v in default_models.items()}

        if saved is None:
            self.save_models(default_models, set())
            return {k: json.loads(json.dumps(v)) for k, v in default_models.items()}
        if not isinstance(saved, dict):
            print(f"[SETTINGS] {MODELS_FILE}: zły format — używam domyślnych")
            return {k: json.loads(json.dumps(v)) for k, v in default_models.items()}

        deleted = set(saved.pop(_DELETED_KEY, []) or [])
        updated = False
        if migrate:
            saved, changed = migrate(saved)
            updated |= changed

        for key, value in default_models.items():
            if key in saved or key in deleted:
                continue
            saved[key] = json.loads(json.dumps(value))
            updated = True

        clean = {}
        for key, value in saved.items():
            if is_valid is None or is_valid(key, value):
                clean[key] = value
            else:
                print(f"[SETTINGS] Pomijam niekompletny profil: {key}")
                updated = True

        if updated:
            self.save_models(clean, deleted)
        return clean

    def save_models(self, models: dict, deleted_defaults=None) -> bool:
        try:
            payload = {k: v for k, v in models.items() if k != _DELETED_KEY}
            if deleted_defaults is None:
                deleted_defaults = self._read_deleted_defaults()
            if deleted_defaults:
                payload[_DELETED_KEY] = sorted(deleted_defaults)
            _atomic_write_json(MODELS_FILE, payload)
            return True
        except Exception as e:
            print(f"[SETTINGS] Błąd zapisu {MODELS_FILE}: {e}")
            return False

    def _read_deleted_defaults(self) -> set:
        try:
            data = _read_json(MODELS_FILE)
            if isinstance(data, dict):
                return set(data.get(_DELETED_KEY, []) or [])
        except Exception:
            pass
        return set()

    def mark_model_deleted(self, model_key: str, models: dict) -> bool:
        deleted = self._read_deleted_defaults()
        deleted.add(model_key)
        return self.save_models(models, deleted)
