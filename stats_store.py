# stats_store.py
"""
Statystyki stanowiska - jedno źródło prawdy dla ekranu testu, okna statystyk
i dashboardu (dashboard_server.py).

Zasada: każdy zakończony test = JEDEN wpis (linia JSON) w pliku
    <STATS_DIR>\\<stanowisko>\\results_<dzień produkcyjny>.jsonl
Liczniki dzienne i zmianowe NIE są przechowywane - zawsze są liczone z wpisów.
Dzięki temu nie ma czego "przestawiać" na przełomie zmiany i nic się nie rozjeżdża:
  * zmiana i dzień produkcyjny są wyliczane z godziny testu i zapisane we wpisie,
  * dzień produkcyjny = 06:00 -> 06:00 następnego dnia (zmiana III po północy
    należy do dnia, w którym się zaczęła), więc dzień = I + II + III,
  * każde stanowisko pisze tylko do własnego pliku (bez kolizji przy wspólnym udziale),
  * zapis w osobnym wątku (GUI nie czeka na sieć); gdy udział jest niedostępny,
    wpis trafia do lokalnej skrzynki (stats_outbox.jsonl) i jest dosyłany później,
  * każdy wpis ma unikalne id - czytanie odrzuca duplikaty (np. po ponownym dosłaniu),
  * niedokończona linia (zapis w toku / zanik sieci) jest pomijana przy czytaniu.

Liczone osobno: COMBO (wynik całkowity), OBCIĄŻENIE, HI-POT - każdy: testy / PASS / FAIL.
Test wykonany = wynik PASS albo FAIL. NIEWAŻNY / PRZERWANY liczone tylko informacyjnie.
Retest (powtórny test tego samego SN) wchodzi do liczby testów i jest liczony osobno.
"""
import json
import os
import queue
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

SHIFTS = [(1, "I", 6, 14), (2, "II", 14, 22), (3, "III", 22, 6)]   # (nr, nazwa, od, do)
DAY_START_H = 6
RESULTS = ("PASS", "FAIL")
OUTBOX_NAME = "stats_outbox.jsonl"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FILE_RE = re.compile(r"^results_(\d{4}-\d{2}-\d{2})\.jsonl$")


# ====================================================================== #
# ZMIANY / DZIEŃ PRODUKCYJNY                                              #
# ====================================================================== #
def shift_info(dt: Optional[datetime] = None) -> dict:
    """Zmiana dla podanej chwili: nr, nazwa, początek, koniec, dzień produkcyjny."""
    dt = dt or datetime.now()
    h = dt.hour
    for num, name, s, e in SHIFTS:
        if s < e and s <= h < e:
            start = dt.replace(hour=s, minute=0, second=0, microsecond=0)
            return {"num": num, "name": name, "start": start,
                    "end": start + timedelta(hours=e - s), "day": start.date()}
    # zmiana III (przez północ)
    base = dt if h >= 22 else dt - timedelta(days=1)
    start = base.replace(hour=22, minute=0, second=0, microsecond=0)
    return {"num": 3, "name": "III", "start": start, "end": start + timedelta(hours=8),
            "day": start.date()}


def production_day(dt: Optional[datetime] = None) -> date:
    return shift_info(dt)["day"]


def shift_name(num) -> str:
    return {1: "I", 2: "II", 3: "III"}.get(num, "?")


def shift_hours(num) -> str:
    for n, _, s, e in SHIFTS:
        if n == num:
            return f"{s:02d}:00-{e:02d}:00"
    return ""


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "UNKNOWN")).strip()[:64] or "UNKNOWN"


def day_file(stats_dir: str, station: str, day) -> str:
    d = day if isinstance(day, str) else day.strftime("%Y-%m-%d")
    return os.path.join(stats_dir, safe_name(station), f"results_{d}.jsonl")


# ====================================================================== #
# WPIS                                                                    #
# ====================================================================== #
def make_record(station: str, pn: str, sn: str, combo: str, load: str, hipot: str,
                retest: bool = False, load_code: str = "", hipot_code: str = "",
                reason: str = "", app_version: str = "", sim: bool = False,
                when: Optional[datetime] = None, mode: str = "combo") -> dict:
    when = when or datetime.now()
    sh = shift_info(when)
    return {
        "id": uuid.uuid4().hex,
        "ts": when.strftime("%Y-%m-%dT%H:%M:%S"),
        "day": sh["day"].strftime("%Y-%m-%d"),
        "shift": sh["num"],
        "station": station,
        "pn": pn,
        "sn": sn,
        "retest": bool(retest),
        "mode": mode or "combo",       # combo / load_only / hipot_only (tryb awaryjny)
        "combo": combo,
        "load": load,
        "hipot": hipot,
        "load_code": load_code or "",
        "hipot_code": hipot_code or "",
        "reason": (reason or "")[:200],
        "app": app_version,
        "sim": bool(sim),
    }


def _append_lines(path: str, lines: List[str]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            f.write(ln + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_jsonl(path: str) -> List[dict]:
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue                 # niedokończona / uszkodzona linia
                if isinstance(rec, dict) and rec.get("id") and rec.get("day"):
                    out.append(rec)
    except FileNotFoundError:
        pass
    except OSError as e:                         # udział niedostępny - nie może zabić wątku
        print(f"[STATS] Odczyt {path}: {e}")
    return out


CLOCK_TOLERANCE_S = 120          # [s] większa różnica = ostrzeżenie na ekranie
CLOCK_CHECK_S = 600              # [s] co ile sprawdzać


def check_clock(stats_dir: str, station: str = "") -> Optional[float]:
    """
    Różnica (czas serwera plików - czas PC) w sekundach, albo None gdy nie da się sprawdzić.
    Zmiana i dzień produkcyjny są liczone z zegara PC - przestawiony zegar = test w złej zmianie.
    Metoda: plik tymczasowy w katalogu statystyk; datę modyfikacji nadaje serwer plików.
    """
    path = os.path.join(stats_dir, f".clock_{safe_name(station)}_{os.getpid()}.tmp")
    try:
        os.makedirs(stats_dir, exist_ok=True)
        t0 = time.time()
        with open(path, "w") as f:
            f.write("clock")
            f.flush()
            os.fsync(f.fileno())
        t1 = time.time()
        mtime = os.stat(path).st_mtime
        os.remove(path)
        return mtime - (t0 + t1) / 2.0
    except Exception as e:
        print(f"[STATS] Kontrola zegara niemożliwa: {e}")
        try:
            os.remove(path)
        except Exception:
            pass
        return None


# ====================================================================== #
# ZAPIS (stanowisko)                                                      #
# ====================================================================== #
class StatsRecorder:
    """
    Zapis wyników stanowiska + bieżące liczniki (z wpisów, nie z licznika).
    record() wraca od razu; zapis na udział robi wątek w tle.
    """
    RETRY_S = 30.0

    def __init__(self, stats_dir: str, station: str, outbox_dir: str):
        self.stats_dir = stats_dir
        self.station = station
        self.outbox = os.path.join(outbox_dir, OUTBOX_NAME)
        self.last_error: Optional[str] = None
        self.pending = 0                      # wpisy czekające w lokalnej skrzynce
        self.clock_offset: Optional[float] = None   # [s] zegar serwera plików - zegar PC
        self.on_change = None                 # callback() z wątku tła po wczytaniu/zapisie
        self._lock = threading.Lock()
        self._day = None
        self._records: "OrderedDict[str, dict]" = OrderedDict()   # bieżący dzień, to stanowisko
        self._q: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._loaded = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ #
    def record(self, rec: dict):
        with self._lock:
            self._records[rec["id"]] = rec            # od razu w licznikach (bez czekania na sieć)
            new_day = self._day is not None and rec["day"] != self._day_str()
        if new_day:                                    # pierwszy test nowego dnia produkcyjnego
            self._q.put(("load", datetime.strptime(rec["day"], "%Y-%m-%d").date()))
        self._q.put(("rec", rec))
        self._notify()

    def stop(self, timeout: float = 3.0):
        self._q.put(("stop", None))
        self._thread.join(timeout=timeout)

    def _day_str(self) -> str:
        return self._day.strftime("%Y-%m-%d") if self._day else ""

    def _notify(self):
        cb = self.on_change
        if cb:
            try:
                cb()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def refresh_day(self) -> bool:
        """Przełom dnia produkcyjnego -> wczytanie nowego pliku. Zwraca True przy zmianie."""
        today = production_day()
        if today == self._day:
            return False
        self._q.put(("load", today))
        return True

    def _load_day(self, day: date):
        recs = _read_jsonl(day_file(self.stats_dir, self.station, day))
        d = day.strftime("%Y-%m-%d")
        recs += [r for r in _read_jsonl(self.outbox) if r.get("day") == d
                 and r.get("station") == self.station]
        with self._lock:
            self._day = day
            fresh = OrderedDict((r["id"], r) for r in sorted(recs, key=lambda r: r.get("ts", "")))
            # wpisy dodane w trakcie wczytywania nie mogą zniknąć
            for k, r in self._records.items():
                if r.get("day") == d:
                    fresh.setdefault(k, r)
            self._records = fresh
        self._loaded.set()
        self._notify()

    def _write(self, rec: dict):
        line = json.dumps(rec, ensure_ascii=False)
        try:
            _append_lines(day_file(self.stats_dir, rec["station"], rec["day"]), [line])
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"[STATS] Udział niedostępny ({e}) - wpis do lokalnej skrzynki")
            try:
                _append_lines(self.outbox, [line])
            except Exception as e2:
                print(f"[STATS] BŁĄD zapisu lokalnej skrzynki: {e2}")
            return False

    def _flush_outbox(self):
        if not os.path.exists(self.outbox):
            return
        recs = _read_jsonl(self.outbox)
        if not recs:
            try:
                os.remove(self.outbox)
            except Exception:
                pass
            return
        by_file: Dict[str, List[str]] = {}
        for r in recs:
            by_file.setdefault(day_file(self.stats_dir, r["station"], r["day"]), []).append(
                json.dumps(r, ensure_ascii=False))
        try:
            for path, lines in by_file.items():
                _append_lines(path, lines)
            os.remove(self.outbox)      # przy błędzie w połowie: duplikaty odrzuci czytanie po id
            print(f"[STATS] Dosłano {len(recs)} wpisów z lokalnej skrzynki")
            self.last_error = None
        except Exception as e:
            self.last_error = str(e)

    def _check_clock(self):
        self.clock_offset = check_clock(self.stats_dir, self.station)
        if self.clock_offset is not None and abs(self.clock_offset) > CLOCK_TOLERANCE_S:
            print(f"[STATS] Zegar PC różni się od serwera plików o {self.clock_offset:+.0f} s")

    def _count_pending(self):
        self.pending = len(_read_jsonl(self.outbox)) if os.path.exists(self.outbox) else 0

    def _worker(self):
        try:
            self._load_day(production_day())
            self._flush_outbox()
            self._count_pending()
        except Exception as e:
            print(f"[STATS] Start: {e}")
            self._loaded.set()
        self._check_clock()
        self._notify()
        last_retry = last_clock = time.time()
        while True:
            try:
                kind, arg = self._q.get(timeout=1.0)
            except queue.Empty:
                kind, arg = None, None
            if kind == "stop":
                break
            try:
                last_retry = self._step(kind, arg, last_retry)
                if time.time() - last_clock > CLOCK_CHECK_S:
                    last_clock = time.time()
                    self._check_clock()
            except Exception as e:                # wątek zapisu nie może umrzeć
                print(f"[STATS] Błąd wątku zapisu: {e}")

    def _step(self, kind, arg, last_retry):
        if kind == "rec":
            if os.path.exists(self.outbox):
                self._flush_outbox()          # najpierw zaległe - zachowanie kolejności
            if os.path.exists(self.outbox):
                try:
                    _append_lines(self.outbox, [json.dumps(arg, ensure_ascii=False)])
                except Exception as e:
                    print(f"[STATS] BŁĄD zapisu lokalnej skrzynki: {e}")
            else:
                self._write(arg)              # przy błędzie sam odkłada do skrzynki
            self._count_pending()
            self._notify()
        elif kind == "load":
            self._load_day(arg)
        if time.time() - last_retry > self.RETRY_S:
            last_retry = time.time()
            if os.path.exists(self.outbox):
                self._flush_outbox()
                self._count_pending()
                self._notify()
        return last_retry

    # ------------------------------------------------------------------ #
    def records(self) -> List[dict]:
        with self._lock:
            return list(self._records.values())

    def current(self) -> dict:
        """Liczniki bieżącej zmiany tego stanowiska."""
        sh = shift_info()
        d = sh["day"].strftime("%Y-%m-%d")
        recs = [r for r in self.records() if r.get("day") == d and r.get("shift") == sh["num"]]
        s = summarize(recs)
        s["shift"] = sh
        s["loaded"] = self._loaded.is_set()
        return s

    def seen_in_shift(self, sn: str) -> Optional[dict]:
        """Ostatni wpis tego SN w bieżącej zmianie (kontrola duplikatu)."""
        sh = shift_info()
        d = sh["day"].strftime("%Y-%m-%d")
        sn = (sn or "").upper()
        hit = None
        for r in self.records():
            if r.get("day") == d and r.get("shift") == sh["num"] and str(r.get("sn", "")).upper() == sn \
                    and r.get("combo") in RESULTS:
                hit = r
        return hit


# ====================================================================== #
# ODCZYT I AGREGACJA (okno statystyk, dashboard)                          #
# ====================================================================== #
_cache: Dict[str, tuple] = {}
_cache_lock = threading.Lock()


def read_day(stats_dir: str, day, include_sim: bool = False) -> List[dict]:
    """Wszystkie wpisy dnia produkcyjnego ze wszystkich stanowisk (bez duplikatów id)."""
    d = day if isinstance(day, str) else day.strftime("%Y-%m-%d")
    if not _DAY_RE.match(d):
        return []
    seen, out = set(), []
    try:
        stations = sorted(os.listdir(stats_dir))
    except Exception:
        return []
    for st in stations:
        path = os.path.join(stats_dir, st, f"results_{d}.jsonl")
        try:
            stt = os.stat(path)
        except OSError:
            continue
        key = (stt.st_mtime_ns, stt.st_size)
        with _cache_lock:
            hit = _cache.get(path)
        if hit and hit[0] == key:
            recs = hit[1]
        else:
            recs = _read_jsonl(path)
            with _cache_lock:
                _cache[path] = (key, recs)
        for r in recs:
            if r["id"] in seen or (r.get("sim") and not include_sim):
                continue
            seen.add(r["id"])
            out.append(r)
    out.sort(key=lambda r: r.get("ts", ""))
    return out


def list_days(stats_dir: str) -> List[str]:
    days = set()
    try:
        for st in os.listdir(stats_dir):
            p = os.path.join(stats_dir, st)
            if os.path.isdir(p):
                for fn in os.listdir(p):
                    m = _FILE_RE.match(fn)
                    if m:
                        days.add(m.group(1))
    except Exception:
        pass
    return sorted(days, reverse=True)


def _blank():
    return {"tests": 0, "pass": 0, "fail": 0}


def _add(c: dict, result: str):
    if result in RESULTS:
        c["tests"] += 1
        c["pass" if result == "PASS" else "fail"] += 1


def summarize(records: List[dict]) -> dict:
    """
    COMBO / OBCIĄŻENIE / HI-POT: testy, PASS, FAIL + retesty, nieważne, przerwane, FPY.
    Testy w trybie awaryjnym (tylko obciążenie / tylko Hi-Pot) liczą się do swojego testu,
    ale NIE do COMBO ani FPY - sztuka nie przeszła pełnego testu. Liczone osobno w 'single'.
    """
    s = {"combo": _blank(), "load": _blank(), "hipot": _blank(), "single": 0,
         "retest": 0, "invalid": 0, "aborted": 0, "first_tests": 0, "first_pass": 0}
    first_seen = set()
    for r in records:
        combo = r.get("combo")
        full = r.get("mode", "combo") == "combo"
        if full:
            _add(s["combo"], combo)
        elif combo in RESULTS:
            s["single"] += 1
        _add(s["load"], r.get("load"))
        _add(s["hipot"], r.get("hipot"))
        if combo == "INVALID":
            s["invalid"] += 1
        elif combo == "ABORTED":
            s["aborted"] += 1
        if combo in RESULTS:
            if r.get("retest"):
                s["retest"] += 1
            if not full:
                continue
            sn = str(r.get("sn", "")).upper()
            if sn not in first_seen:
                first_seen.add(sn)
                s["first_tests"] += 1
                s["first_pass"] += combo == "PASS"
    return s


def build_report(records: List[dict], last_n: int = 25) -> dict:
    """Pełne zestawienie dnia dla okna statystyk i dashboardu."""
    rep = {"total": summarize(records), "shifts": {}, "stations": {}, "profiles": {},
           "hours": [], "fail_codes": [], "last": []}
    for num, name, _, _ in SHIFTS:
        rep["shifts"][name] = summarize([r for r in records if r.get("shift") == num])
    for key, field in (("stations", "station"), ("profiles", "pn")):
        groups: Dict[str, list] = {}
        for r in records:
            groups.setdefault(str(r.get(field) or "?"), []).append(r)
        rep[key] = {k: summarize(v) for k, v in sorted(groups.items())}
    # godziny dnia produkcyjnego 06..05
    hours = OrderedDict(((DAY_START_H + i) % 24, {"pass": 0, "fail": 0}) for i in range(24))
    codes = Counter()
    for r in records:
        if r.get("combo") not in RESULTS:
            continue
        if r["combo"] == "FAIL":              # przyczyny FAIL - także z trybu awaryjnego
            if r.get("load") == "FAIL":
                codes[f"Obciążenie {r.get('load_code') or '?'}"] += 1
            elif r.get("hipot") == "FAIL":
                codes[f"Hi-Pot {r.get('hipot_code') or '?'}"] += 1
        if r.get("mode", "combo") != "combo":
            continue                           # wykres godzinowy = testy COMBO
        try:
            h = int(r["ts"][11:13])
        except (KeyError, ValueError):
            continue
        hours[h]["pass" if r["combo"] == "PASS" else "fail"] += 1
    rep["hours"] = [{"hour": h, **v} for h, v in hours.items()]
    rep["fail_codes"] = [{"code": c, "count": n} for c, n in codes.most_common(10)]
    rep["last"] = [{k: r.get(k) for k in ("ts", "shift", "station", "pn", "sn", "combo", "load",
                                           "hipot", "retest", "load_code", "hipot_code")}
                   for r in records[-last_n:][::-1]]
    return rep
