"""Silnik weryfikacji komend: wykonuje TestCase'y z command_catalog i ocenia odpowiedzi."""
import logging
import re
import threading
import time
from dataclasses import dataclass, field, asdict

from transport import TransportError

log = logging.getLogger(__name__)

PASS, FAIL, NO_RESP, ERROR, SKIP = "PASS", "FAIL", "NO RESP", "ERROR", "SKIP"
INFO_OK, INFO_NOK = "INFO ✓", "INFO ✗"
_SEVERITY = {PASS: 0, SKIP: 0, INFO_OK: 1, INFO_NOK: 2, NO_RESP: 3, FAIL: 4, ERROR: 5}


def worst(statuses):
    return max(statuses, key=lambda s: _SEVERITY.get(s, 0)) if statuses else SKIP


@dataclass
class StepResult:
    case_id: str
    group: str
    title: str
    step_no: int
    sent: str
    query: str
    expect: str
    received: str
    status: str
    ms: float
    detail: str = ""
    page: int = 0
    kind: str = "doc"


@dataclass
class CaseResult:
    case_id: str
    group: str
    title: str
    status: str
    steps: list = field(default_factory=list)
    page: int = 0
    kind: str = "doc"
    note: str = ""


# ------------------------------------------------------------------ porównania
_BOOL = {"1": 1, "ON": 1, "0": 0, "OFF": 0}


def _to_float(s):
    m = re.match(r"^\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", s or "")
    return float(m.group(1)) if m else None


def _enum_match(resp, exp):
    r, e = resp.strip().upper(), str(exp).strip().upper()
    if r == e:
        return True
    # krótka vs długa forma: NORM/NORMAL, PULS/PULSE/PULSE, CONT/CONTINUOUS
    short = re.sub(r"[a-z]+$", "", str(exp).strip()).upper() or e  # 'PULSe' -> 'PULS'
    return (r.startswith(short) and len(r) >= 3) or (e.startswith(r) and len(r) >= 3)


def evaluate(step, resp, prev_resp, tol_abs, tol_rel):
    """Zwraca (status, detail)."""
    cmp = step.cmp
    exp = step.expect
    if resp is None:
        return NO_RESP, "Brak odpowiedzi w czasie timeoutu"
    r = resp.strip()
    if cmp == "any":
        return (PASS, "") if r else (FAIL, "Pusta odpowiedź")
    if cmp in ("num", "num_any", "changed"):
        v = _to_float(r)
        if v is None:
            return FAIL, "Odpowiedź nie jest liczbą"
        if cmp == "num_any":
            return PASS, ""
        if cmp == "changed":
            pv = _to_float(prev_resp or "")
            if pv is None:
                return FAIL, "Brak poprzedniej wartości do porównania"
            return (PASS, f"{pv:g} → {v:g}") if abs(v - pv) > tol_abs else (FAIL, f"Wartość nie zmieniła się ({v:g})")
        e = float(exp)
        tol = max(tol_abs, abs(e) * tol_rel)
        if abs(v - e) <= tol:
            return PASS, ""
        return FAIL, f"Różnica {v - e:+g} (tol ±{tol:g})"
    if cmp == "enum":
        return (PASS, "") if _enum_match(r, exp) else (FAIL, "Inna wartość")
    if cmp == "bool":
        rv = _BOOL.get(r.upper())
        ev = _BOOL.get(str(exp).upper())
        if rv is None:
            return FAIL, "Odpowiedź nie jest 0/1/ON/OFF"
        return (PASS, "") if rv == ev else (FAIL, "Inny stan")
    if cmp == "mode":
        rr, ee = r.lower(), str(exp).lower()
        return (PASS, "") if (rr == ee or ee.startswith(rr) or rr.startswith(ee[:3])) else (FAIL, "Inny tryb")
    if cmp == "fields":
        n = len(r.split(","))
        return (PASS, "") if n == int(exp) else (FAIL, f"{n} pól zamiast {exp}")
    if cmp == "regex":
        return (PASS, "") if re.match(exp, r) else (FAIL, "Format niezgodny")
    if cmp == "int_in":
        v = _to_float(r)
        return (PASS, "") if v is not None and int(v) in exp else (FAIL, "Wartość spoza listy")
    return PASS, ""


# ------------------------------------------------------------------ runner
class Verifier:
    """Uruchamiać w wątku roboczym. Zdarzenia przez callback on_event(typ, dane)."""

    def __init__(self, transport, test_cfg, on_event=None):
        self.t = transport
        self.cfg = test_cfg
        self.on_event = on_event or (lambda *a: None)
        self._stop = threading.Event()
        self.results = []

    def stop(self):
        self._stop.set()

    @property
    def stopped(self):
        return self._stop.is_set()

    def _emit(self, typ, data=None):
        try:
            self.on_event(typ, data)
        except Exception:
            log.exception("on_event")

    def _query(self, cmd):
        resp = self.t.query(cmd)
        retries = int(self.cfg.get("retries_on_timeout", 0))
        while resp is None and retries > 0:
            retries -= 1
            resp = self.t.query(cmd)
        return resp

    # ---------------------------------------------------------------- setup / teardown
    def _setup(self):
        if self.cfg.get("remote_before_test", True):
            self.t.write("SYST:REM")
        self.t.write("INP:SHOR 0")
        self.t.write("INP 0")

    def _teardown(self):
        """Zawsze - także po STOP/błędzie: wejście OFF, zwarcie OFF, sense OFF."""
        for cmd in ("INP:SHOR 0", "INP 0", "SYST:SENS 0"):
            try:
                self.t.write(cmd)
            except Exception as e:
                log.error("Teardown %s: %s", cmd, e)
        try:
            if self.cfg.get("rst_after_test"):
                self.t.write("*RST")
                self.t.write("INP 0")
            if self.cfg.get("local_after_test", True):
                self.t.write("SYST:LOC")
        except Exception as e:
            log.error("Teardown: %s", e)

    # ---------------------------------------------------------------- główna pętla
    def run(self, selection):
        """selection: lista (Group, [TestCase,...])."""
        self._stop.clear()
        self.results = []
        t0 = time.time()
        total = sum(len(cs) for _, cs in selection)
        done = 0
        self._emit("start", {"total": total})
        aborted = None
        try:
            self._setup()
            for group, cases in selection:
                if self.stopped or not cases:
                    continue
                self._emit("group", group.name)
                for cmd in group.pre:
                    self.t.write(cmd)
                for case in cases:
                    if self.stopped:
                        break
                    res = self._run_case(case)
                    self.results.append(res)
                    done += 1
                    self._emit("case", {"result": res, "done": done, "total": total})
        except TransportError as e:
            aborted = f"Błąd komunikacji: {e}"
            log.error(aborted)
        except Exception as e:
            aborted = f"Błąd: {e}"
            log.exception("Verifier")
        finally:
            try:
                self._teardown()
            except Exception:
                pass
        summary = self.summary()
        summary["duration_s"] = round(time.time() - t0, 1)
        summary["aborted"] = aborted or ("Zatrzymano przez operatora" if self.stopped else "")
        self._emit("done", summary)
        return self.results

    def _run_case(self, case):
        self._emit("case_start", case)
        steps = []
        prev_resp = None
        tol_abs = float(self.cfg.get("num_abs_tol", 0.0011))
        tol_rel = float(self.cfg.get("num_rel_tol", 0.002))
        for i, st in enumerate(case.steps, 1):
            if self.stopped:
                break
            t0 = time.perf_counter()
            resp = None
            status, detail = PASS, ""
            try:
                if st.send:
                    self.t.write(st.send)
                if st.cmp == "alive":
                    resp = self._query("*IDN?")
                    status, detail = (PASS, "Urządzenie odpowiada po komendzie") if resp else \
                        (NO_RESP, "Brak odpowiedzi na *IDN? po komendzie")
                elif st.query:
                    resp = self._query(st.query)
                    status, detail = evaluate(st, resp, prev_resp, tol_abs, tol_rel)
                else:
                    status, detail = PASS, "krok pomocniczy"
            except TransportError:
                raise
            except Exception as e:
                status, detail = ERROR, str(e)
            ms = (time.perf_counter() - t0) * 1000
            if case.kind == "probe":
                status = INFO_OK if status == PASS else INFO_NOK
            exp_txt = "" if st.expect is None else (str(st.expect) if st.cmp != "changed" else "≠ poprzednia")
            if st.cmp == "alive":
                exp_txt = "odpowiedź na *IDN?"
            sr = StepResult(case.id, case.group, case.title, i, st.send or "", st.query or
                            ("*IDN?" if st.cmp == "alive" else ""), exp_txt,
                            "" if resp is None else resp, status, round(ms, 1),
                            (st.note + " | " if st.note else "") + detail, case.page, case.kind)
            steps.append(sr)
            if resp is not None:
                prev_resp = resp
        status = worst([s.status for s in steps]) if steps else SKIP
        return CaseResult(case.id, case.group, case.title, status, steps, case.page, case.kind, case.note)

    def summary(self):
        c = {}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return {"counts": c, "total": len(self.results)}


def result_rows(results):
    """Spłaszczenie wyników do wierszy (raport/tabela)."""
    for r in results:
        for s in r.steps:
            yield asdict(s)
