# stats_manager.py
"""
Statystyki dzienne - źródłem prawdy są pliki TXT w LOG_DIR.

Zmiany względem wersji pierwotnej:
  * count_today() nie otwiera już przy każdym wywołaniu wszystkich plików
    z katalogu logów. Wynik parsowania pliku jest zapamiętywany (pliki są
    zapisywane raz i nie zmieniają się), a samo skanowanie katalogu jest
    dławione. Na udziale sieciowym z kilkoma tysiącami plików poprzednia
    wersja blokowała GUI na kilka sekund po KAŻDYM teście;
  * count_today_async() - wersja do wołania z wątku roboczego;
  * konstruktor nie tworzy katalogów (niedostępny dysk sieciowy wywalał
    całą aplikację przy starcie); katalog powstaje przy pierwszym zapisie;
  * set_log_dir() - zmiana ścieżki w panelu administratora działa bez
    restartu aplikacji.
"""
import json
import os
import random
import re
import tempfile
import threading
import time
from datetime import date, datetime
from typing import Dict, Optional

_STATS_SUBDIR = "Daily PSU Combo stats"

# Nazwa pliku: SN_YYYYMMDDHHmmss.txt (opcjonalny sufiks _2 przy kolizji)
_FNAME_RE = re.compile(
    r'^(?P<sn>.+)_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:_\d+)?\.txt$',
    re.IGNORECASE)


def _parse_result_from_file(filepath: str) -> Optional[str]:
    """Czyta 'Total result: Pass/Fail' z pliku TXT."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("total result:"):
                    val = line.split(":", 1)[-1].strip().upper()
                    return "PASS" if val == "PASS" else "FAIL"
    except Exception:
        pass
    return None


def _parse_fname(fname: str):
    """Zwraca (SN, datetime) albo None."""
    m = _FNAME_RE.match(fname)
    if not m:
        return None
    try:
        ts = datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)),
                      int(m.group(5)), int(m.group(6)), int(m.group(7)))
    except ValueError:
        return None
    return m.group("sn").upper(), ts


class StatsManager:

    SCAN_INTERVAL = 3.0        # [s] minimalny odstęp między skanami katalogu

    def __init__(self, log_dir: str):
        self.log_dir   = log_dir
        self.stats_dir = os.path.join(log_dir, _STATS_SUBDIR)

        # Liczniki sesji (używane przez test_screen.py)
        self.session_pass   = 0
        self.session_fail   = 0
        self.session_retest = 0

        self.last_error: Optional[str] = None

        self._cache_lock  = threading.Lock()
        self._file_cache: Dict[str, Optional[str]] = {}   # fname → PASS/FAIL
        self._last_scan   = 0.0
        self._last_result = {"total": 0, "passed": 0, "failed": 0,
                             "duplicates": 0, "stale": True}
        self._cache_day: Optional[date] = None
        self._scanning = False

    # ------------------------------------------------------------------ #
    def set_log_dir(self, log_dir: str):
        """Przełącza katalog logów w locie (po zmianie w panelu admina)."""
        if log_dir == self.log_dir:
            return
        self.log_dir   = log_dir
        self.stats_dir = os.path.join(log_dir, _STATS_SUBDIR)
        with self._cache_lock:
            self._file_cache.clear()
            self._last_scan = 0.0
            self._cache_day = None

    def _ensure_stats_dir(self) -> bool:
        try:
            os.makedirs(self.stats_dir, exist_ok=True)
            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"[STATS] Brak dostępu do {self.stats_dir}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # PLIK JSON PER DZIEŃ                                                 #
    # ------------------------------------------------------------------ #
    def _stats_path(self, for_date: Optional[date] = None) -> str:
        d = (for_date or date.today()).strftime("%Y-%m-%d")
        return os.path.join(self.stats_dir, f"stats_{d}.json")

    def _load(self, for_date: Optional[date] = None) -> dict:
        path = self._stats_path(for_date)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[STATS] Błąd odczytu {path}: {e}")
        return {}

    def _save(self, data: dict, for_date: Optional[date] = None) -> bool:
        """Atomowy zapis - bezpieczny przy kilku stanowiskach na tym samym udziale."""
        if not self._ensure_stats_dir():
            return False
        path = self._stats_path(for_date)
        folder = os.path.dirname(path)
        for attempt in range(3):
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(dir=folder, prefix=".stats_", suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
                self.last_error = None
                return True
            except Exception as e:
                self.last_error = str(e)
                print(f"[STATS] Retry zapisu ({attempt + 1}/3): {e}")
                try:
                    if tmp and os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                time.sleep(random.uniform(0.05, 0.2))
        print(f"[STATS] NIE UDAŁO SIĘ zapisać {path}")
        return False

    # ------------------------------------------------------------------ #
    # DODAJ WYNIK                                                         #
    # ------------------------------------------------------------------ #
    def add_result(self, operator: str, model_key: str, mode: str,
                   result: str, is_retest: bool = False) -> bool:
        """Zapisuje wynik do JSON per-operator i aktualizuje liczniki sesji."""
        if is_retest:
            self.session_retest += 1
        elif str(result).upper() == "PASS":
            self.session_pass += 1
        else:
            self.session_fail += 1

        target_date = date.today()
        data = self._load(target_date)
        operator = str(operator or "UNKNOWN")
        rows = data.setdefault(operator, {})
        key = f"{model_key}|{mode}"
        entry = rows.setdefault(key, {"model": model_key, "mode": mode,
                                      "pass": 0, "fail": 0, "retest": 0})
        entry.setdefault("retest", 0)

        if is_retest:
            entry["retest"] += 1
        elif str(result).upper() == "PASS":
            entry["pass"] += 1
        else:
            entry["fail"] += 1

        ok = self._save(data, target_date)

        # Nowy plik wyniku pojawi się w katalogu - wymuś świeży skan.
        with self._cache_lock:
            self._last_scan = 0.0
        return ok

    # ------------------------------------------------------------------ #
    # RESET SESJI                                                         #
    # ------------------------------------------------------------------ #
    def reset_session(self):
        self.session_pass   = 0
        self.session_fail   = 0
        self.session_retest = 0

    @property
    def session_total(self) -> int:
        return self.session_pass + self.session_fail

    # ------------------------------------------------------------------ #
    # GŁÓWNY LICZNIK - czyta z plików TXT                                 #
    # ------------------------------------------------------------------ #
    def count_today(self, force: bool = False) -> dict:
        """
        Liczy PASS/FAIL/duplikaty dla dzisiejszego dnia z plików TXT.
        Duplikat = ten sam SN pojawia się dziś więcej niż raz.
        Zwraca zapamiętany wynik jeśli ostatni skan był < SCAN_INTERVAL temu.
        """
        today = date.today()
        now = time.time()

        with self._cache_lock:
            fresh = (not force
                     and self._cache_day == today
                     and (now - self._last_scan) < self.SCAN_INTERVAL)
            if fresh:
                return dict(self._last_result)
            if self._cache_day != today:
                self._file_cache.clear()
            self._cache_day = today

        result = self._scan_today(today)

        with self._cache_lock:
            self._last_scan = time.time()
            self._last_result = result
        return dict(result)

    def count_today_async(self, callback):
        """
        Skanuje w wątku tła i woła callback(dict).
        Callback NIE jest wołany z wątku GUI - użyj root.after().
        """
        with self._cache_lock:
            if self._scanning:
                return
            self._scanning = True

        def worker():
            try:
                res = self.count_today(force=True)
            except Exception as e:
                print(f"[STATS] Błąd skanu w tle: {e}")
                res = dict(self._last_result)
            finally:
                with self._cache_lock:
                    self._scanning = False
            try:
                callback(res)
            except Exception as e:
                print(f"[STATS] Błąd callbacku statystyk: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _scan_today(self, today: date) -> dict:
        out = {"total": 0, "passed": 0, "failed": 0,
               "duplicates": 0, "stale": False}
        try:
            if not os.path.isdir(self.log_dir):
                out["stale"] = True
                return out

            entries = []
            for fname in os.listdir(self.log_dir):
                parsed = _parse_fname(fname)
                if not parsed:
                    continue
                sn, ts = parsed
                if ts.date() != today:
                    continue
                entries.append((ts, fname, sn))

            entries.sort(key=lambda x: (x[0], x[1]))

            seen_sns: Dict[str, int] = {}
            for ts, fname, sn in entries:
                with self._cache_lock:
                    cached = self._file_cache.get(fname, "___MISS___")
                if cached == "___MISS___":
                    cached = _parse_result_from_file(
                        os.path.join(self.log_dir, fname))
                    with self._cache_lock:
                        self._file_cache[fname] = cached
                if cached is None:
                    continue

                seen_sns[sn] = seen_sns.get(sn, 0) + 1
                if seen_sns[sn] > 1:
                    out["duplicates"] += 1
                    continue          # duplikat nie wchodzi do totalu

                out["total"] += 1
                if cached == "PASS":
                    out["passed"] += 1
                else:
                    out["failed"] += 1

        except Exception as e:
            self.last_error = str(e)
            print(f"[STATS] Błąd count_today: {e}")
            out["stale"] = True

        return out

    # ------------------------------------------------------------------ #
    # WIDOK PER-OPERATOR (z JSON)                                         #
    # ------------------------------------------------------------------ #
    def get_daily_stats(self, for_date: Optional[date] = None) -> Dict[str, list]:
        data = self._load(for_date)
        result = {}
        for operator, rows in data.items():
            if not isinstance(rows, dict):
                continue
            result[operator] = []
            for entry in rows.values():
                try:
                    result[operator].append({
                        "model":  entry["model"],
                        "mode":   entry.get("mode", "AC"),
                        "pass":   entry["pass"],
                        "fail":   entry["fail"],
                        "retest": entry.get("retest", 0),
                        "total":  entry["pass"] + entry["fail"],
                    })
                except (KeyError, TypeError):
                    continue
        return result

    def flush(self):
        print("[STATS] Statystyki zsynchronizowane.")
