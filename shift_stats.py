# shift_stats.py
"""
Statystyki zmiany produkcyjnej — per operator (HRID).

Zmiana wyznaczana z bieżącej godziny:
    I   → 06:00 – 13:59
    II  → 14:00 – 21:59
    III → 22:00 – 05:59 (przez noc)

Stan przechowywany w pliku:
    <LOG_DIR>\\Shift Reports\\<HRID>\\shift_<nr>_<RRRR-MM-DD>.txt

Zmiany względem wersji pierwotnej (klasa mieszkała w test_screen.py):
  * rozdzielenie licznika na "wczytane z pliku" (base) i "dorobione w tej
    sesji" (delta). Wcześniej wątek wczytujący nadpisywał liczniki
    wartościami z pliku — testy wykonane zanim wczytywanie się skończyło
    (typowe na wolnym dysku sieciowym) po prostu znikały;
  * zapis do pliku jest wstrzymany do końca wczytywania — wcześniej
    pierwszy test po starcie potrafił nadpisać plik zmiany wartością "1",
    kasując dorobek całej zmiany;
  * przełom zmiany wczytuje plik nowej zmiany zamiast zerować liczniki
    (drugie stanowisko / druga sesja tego samego HRID nie jest kasowana);
  * zapis atomowy (tmp + replace) — plik nie zostaje ucięty przy zaniku
    sieci;
  * brak dostępu do dysku sieciowego nie wywala ekranu testu.
"""
import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from typing import Optional

SHIFTS = [
    (1, "I",   6,  14),   # (numer, nazwa, start_h, end_h)
    (2, "II",  14, 22),
    (3, "III", 22, 6),    # nocna — end_h < start_h
]

SHIFT_REPORTS_SUBDIR = "Shift Reports"

# Znacznik czasu z nazwy pliku wyniku: SN_YYYYMMDDHHmmss.txt
FNAME_TS_RE = re.compile(
    r'^.+_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:_\d+)?\.txt$',
    re.IGNORECASE)

_SHIFT_HDR_RE = re.compile(r'(\S+)\s*\(([^)]+)\)')


def get_current_shift(now: Optional[datetime] = None):
    """Zwraca (numer, nazwa, start: datetime, koniec: datetime)."""
    if now is None:
        now = datetime.now()

    h = now.hour
    today = now.date()

    for num, name, s, e in SHIFTS:
        if s < e:                                   # zmiana dzienna
            if s <= h < e:
                start = datetime(today.year, today.month, today.day, s)
                end   = datetime(today.year, today.month, today.day, e)
                return num, name, start, end
        else:                                       # zmiana nocna
            if h >= s:                              # 22:00–23:59
                tomorrow = today + timedelta(days=1)
                start = datetime(today.year, today.month, today.day, s)
                end   = datetime(tomorrow.year, tomorrow.month, tomorrow.day, e)
                return num, name, start, end
            if h < e:                               # 00:00–05:59
                yesterday = today - timedelta(days=1)
                start = datetime(yesterday.year, yesterday.month, yesterday.day, s)
                end   = datetime(today.year, today.month, today.day, e)
                return num, name, start, end

    start = datetime(today.year, today.month, today.day, 0)
    return 0, "?", start, start + timedelta(hours=8)


def parse_result_from_file(filepath: str) -> Optional[str]:
    """Zwraca 'PASS'/'FAIL' czytając linię 'Total result:' z raportu TXT."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.strip().lower().startswith("total result:"):
                    val = line.split(":", 1)[-1].strip().upper()
                    return "PASS" if val == "PASS" else "FAIL"
    except Exception:
        pass
    return None


def parse_shift_file(filepath: str) -> dict:
    """Parsuje plik shift_N_RRRR-MM-DD.txt → słownik z danymi zmiany."""
    data = {"shift_name": "?", "hours": "",
            "passed": 0, "failed": 0, "retests": 0, "models": {}}
    try:
        in_models = False
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("Zmiana:"):
                    m = _SHIFT_HDR_RE.match(line.split(":", 1)[1].strip())
                    if m:
                        data["shift_name"] = m.group(1)
                        data["hours"]      = m.group(2)
                elif line.startswith("PASS:"):
                    data["passed"] = _safe_int(line)
                elif line.startswith("FAIL:"):
                    data["failed"] = _safe_int(line)
                elif line.startswith("RETEST:"):
                    data["retests"] = _safe_int(line)
                elif line.startswith("[MODELE]"):
                    in_models = True
                elif line.startswith("---"):
                    in_models = False
                elif in_models and "|" in line and not line.startswith("Model"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4 and parts[0] and not parts[0].startswith("-"):
                        try:
                            data["models"][parts[0]] = {
                                "pass":   int(parts[1]),
                                "fail":   int(parts[2]),
                                "retest": int(parts[3]),
                            }
                        except ValueError:
                            pass
        return data
    except Exception as e:
        print(f"[SHIFT] Błąd parsowania {filepath}: {e}")
        return {}


def _safe_int(line: str) -> int:
    try:
        return int(line.split(":", 1)[1].strip())
    except (ValueError, IndexError):
        return 0


def _merge_models(base: dict, delta: dict) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for mk, counts in delta.items():
        row = out.setdefault(mk, {"pass": 0, "fail": 0, "retest": 0})
        for field in ("pass", "fail", "retest"):
            row[field] = row.get(field, 0) + counts.get(field, 0)
    return out


class ShiftStats:
    """Licznik PASS/FAIL/RETEST bieżącej zmiany dla jednego operatora."""

    def __init__(self, log_dir: str, operator: str = "UNKNOWN"):
        self.log_dir  = log_dir
        self.operator = str(operator or "UNKNOWN").strip() or "UNKNOWN"
        self.on_rebuilt = None
        self.storage_error: Optional[str] = None

        self._lock = threading.Lock()
        self._stopped = False

        num, name, start, end = get_current_shift()
        self.shift_num   = num
        self.shift_name  = name
        self.shift_start = start
        self.shift_end   = end

        # base = stan wczytany z pliku, delta = to co dorobiła ta sesja.
        self._base  = {"pass": 0, "fail": 0, "retest": 0}
        self._delta = {"pass": 0, "fail": 0, "retest": 0}
        self._base_models: dict = {}
        self._delta_models: dict = {}
        self._loaded = False

        self._shift_dir = os.path.join(
            log_dir, SHIFT_REPORTS_SUBDIR, self._safe_name(self.operator))
        self._shift_file = self._make_filepath()

        threading.Thread(target=self._load_from_file, daemon=True).start()

    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_name(name: str) -> str:
        """HRID trafia do nazwy katalogu — odetnij znaki niedozwolone w NTFS."""
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)[:64] or "UNKNOWN"

    def _make_filepath(self) -> str:
        date_str = self.shift_start.strftime("%Y-%m-%d") if self.shift_start else "unknown"
        return os.path.join(self._shift_dir, f"shift_{self.shift_num}_{date_str}.txt")

    def _ensure_dir(self) -> bool:
        try:
            os.makedirs(self._shift_dir, exist_ok=True)
            self.storage_error = None
            return True
        except Exception as e:
            self.storage_error = str(e)
            print(f"[SHIFT] Brak dostępu do {self._shift_dir}: {e}")
            return False

    # ------------------------------------------------------------------ #
    # WCZYTANIE                                                           #
    # ------------------------------------------------------------------ #
    def _load_from_file(self):
        base = {"pass": 0, "fail": 0, "retest": 0}
        models = {}
        try:
            self._ensure_dir()
            if os.path.exists(self._shift_file):
                data = parse_shift_file(self._shift_file)
                if data:
                    base = {"pass":   data.get("passed", 0),
                            "fail":   data.get("failed", 0),
                            "retest": data.get("retests", 0)}
                    models = data.get("models", {})
                    print(f"[SHIFT] Wczytano {self._shift_file}: "
                          f"PASS={base['pass']} FAIL={base['fail']} "
                          f"RETEST={base['retest']}")
        except Exception as e:
            print(f"[SHIFT] Błąd wczytywania: {e}")

        with self._lock:
            self._base = base
            self._base_models = models
            self._loaded = True

        # Jeśli w międzyczasie doszły wyniki — dopiero teraz można zapisać.
        if self._delta["pass"] or self._delta["fail"] or self._delta["retest"]:
            self._save_to_file()

        cb = self.on_rebuilt
        if cb and not self._stopped:
            try:
                cb()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # ZAPIS                                                               #
    # ------------------------------------------------------------------ #
    def _save_to_file(self):
        with self._lock:
            if not self._loaded:
                # Zapis przed wczytaniem skasowałby dorobek zmiany.
                return
            snapshot = self._snapshot_locked()
            path = self._shift_file

        if not self._ensure_dir():
            return

        try:
            hours = ""
            if snapshot["shift_start"] and snapshot["shift_end"]:
                hours = (f"{snapshot['shift_start'].strftime('%H:%M')}"
                         f"–{snapshot['shift_end'].strftime('%H:%M')}")

            lines = [
                f"HRID:       {self.operator}",
                f"Zmiana:     {snapshot['shift_name']}  ({hours})",
                f"Data:       {snapshot['shift_start'].strftime('%Y-%m-%d') if snapshot['shift_start'] else '?'}",
                "",
                f"PASS:       {snapshot['passed']}",
                f"FAIL:       {snapshot['failed']}",
                f"RETEST:     {snapshot['retests']}",
                "",
                "[MODELE]",
                f"{'Model':<25} | {'PASS':>5} | {'FAIL':>5} | {'RETEST':>6}",
                f"{'-' * 25}-+-{'-' * 5}-+-{'-' * 5}-+-{'-' * 6}",
            ]
            for mk, counts in sorted(snapshot["models"].items()):
                lines.append(
                    f"{mk:<25} | {counts['pass']:>5} | "
                    f"{counts['fail']:>5} | {counts['retest']:>6}")
            lines += ["---", "",
                      f"Ostatnia aktu.: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]

            self._atomic_write(path, "\n".join(lines))
            self.storage_error = None
        except Exception as e:
            self.storage_error = str(e)
            print(f"[SHIFT] Błąd zapisu: {e}")

    @staticmethod
    def _atomic_write(path: str, text: str):
        folder = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".shift_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
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

    # ------------------------------------------------------------------ #
    # API                                                                 #
    # ------------------------------------------------------------------ #
    def add_result(self, result: str, is_duplicate: bool = False,
                   model_key: str = ""):
        rollover = False
        with self._lock:
            num, name, start, end = get_current_shift()
            if num != self.shift_num:
                self.shift_num   = num
                self.shift_name  = name
                self.shift_start = start
                self.shift_end   = end
                self._delta = {"pass": 0, "fail": 0, "retest": 0}
                self._delta_models = {}
                self._base = {"pass": 0, "fail": 0, "retest": 0}
                self._base_models = {}
                self._loaded = False
                self._shift_file = self._make_filepath()
                rollover = True
                print(f"[SHIFT] Przełom zmiany: {name}")

            mk = model_key or "?"
            row = self._delta_models.setdefault(
                mk, {"pass": 0, "fail": 0, "retest": 0})

            if is_duplicate:
                self._delta["retest"] += 1
                row["retest"] += 1
            elif str(result).upper() == "PASS":
                self._delta["pass"] += 1
                row["pass"] += 1
            else:
                self._delta["fail"] += 1
                row["fail"] += 1

        if rollover:
            # Wczytaj plik nowej zmiany (może już istnieć), potem zapisz.
            threading.Thread(target=self._load_from_file, daemon=True).start()
            return

        self._save_to_file()

    def _snapshot_locked(self) -> dict:
        passed  = self._base["pass"]   + self._delta["pass"]
        failed  = self._base["fail"]   + self._delta["fail"]
        retests = self._base["retest"] + self._delta["retest"]
        return {
            "shift_num":   self.shift_num,
            "shift_name":  self.shift_name,
            "shift_start": self.shift_start,
            "shift_end":   self.shift_end,
            "total":       passed + failed,
            "passed":      passed,
            "failed":      failed,
            "retests":     retests,
            "models":      _merge_models(self._base_models, self._delta_models),
            "loaded":      self._loaded,
            "storage_error": self.storage_error,
        }

    def get_snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    def stop(self):
        """Odpina callback — zapobiega odwołaniom do zniszczonych widgetów."""
        self._stopped = True
        self.on_rebuilt = None
