# combo_sequence.py
"""
Sekwencja testu stanowiska: Hi-Pot -> przełączenie toru -> obciążenie.

  1. PATH HIPOT (Arduino/STM potwierdza)        brak potwierdzenia -> NIEWAŻNY
  2. Hi-Pot (hipot_runner)                      raport Hi-Pot zapisywany od razu (PASS/FAIL)
  3. PATH OFF
       Hi-Pot NIEWAŻNY -> koniec, nic nie zapisane
       Hi-Pot FAIL     -> koniec, obciążenie pominięte (zasilacz z FAIL nie dostaje 230 V)
  4. PATH LOAD (230 V na zasilacz, wyjście na OEL)
  5. Test obciążenia 2-punktowy (load_tester)   raport LOAD zapisywany (PASS/FAIL)
  6. PATH OFF, wejście OEL OFF — zawsze, także po przerwaniu/błędzie

Bez Tk — używane przez ekran testu i test w pętli z panelu ENG.
"""
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from hipot_runner import run_hipot
from interlock import PATH_HIPOT, PATH_LOAD, PATH_OFF
from load_tester import LoadVerdict, run_load_test

PASS, FAIL, INVALID, ABORTED, SKIP = "PASS", "FAIL", "INVALID", "ABORTED", "SKIP"


@dataclass
class ComboResult:
    overall: str = INVALID
    reason: str = ""
    hipot: object = None            # result_validator.Verdict
    hipot_device_result: str = ""
    hipot_data: dict = field(default_factory=dict)
    load: Optional[LoadVerdict] = None
    load_status: str = SKIP         # PASS/FAIL/INVALID/SKIP/OFF (wyłączony w profilu)
    save_errors: list = field(default_factory=list)
    hipot_report: str = ""
    load_report: str = ""
    relay_alarm: bool = False

    @property
    def hipot_status(self) -> str:
        return self.hipot.result if self.hipot is not None else SKIP


class ComboSequence:

    def __init__(self, cfg, profile: dict, chroma, oel, fixture, abort: threading.Event,
                 on_phase: Optional[Callable[[str, str], None]] = None,
                 on_hipot_live: Optional[Callable[[float, float, float], None]] = None,
                 on_hipot_done: Optional[Callable[[object], None]] = None,
                 on_load_live: Optional[Callable[[dict], None]] = None,
                 save_hipot: Optional[Callable] = None,
                 save_load: Optional[Callable] = None):
        self.cfg = cfg
        self.profile = profile
        self.chroma = chroma
        self.oel = oel
        self.fixture = fixture
        self.abort = abort
        self.on_phase = on_phase
        self.on_hipot_live = on_hipot_live
        self.on_hipot_done = on_hipot_done
        self.on_load_live = on_load_live
        self.save_hipot = save_hipot
        self.save_load = save_load

    # ------------------------------------------------------------------ #
    @property
    def relays(self) -> bool:
        return bool(self.cfg.RELAYS_ENABLED and self.fixture is not None)

    @property
    def load_enabled(self) -> bool:
        return bool(self.profile.get("load_params", {}).get("enabled", False))

    def _phase(self, step: str, text: str):
        if self.on_phase:
            try:
                self.on_phase(step, text)
            except Exception:
                pass

    def _path(self, path: str):
        if not self.relays:
            return True, ""
        if path != PATH_OFF and self.abort.is_set():
            return False, "Przerwano — tor nie zostanie załączony"
        return self.fixture.set_path(path, timeout=float(self.cfg.RELAY_TIMEOUT))

    def _path_held(self, path: str):
        """Czy tor i klapa wytrzymały całą fazę (Arduino zdejmuje tor przy klapie/watchdogu)."""
        if not self.relays:
            return True, ""
        if self.fixture.path != path:
            return False, f"Tor {path} rozłączony w trakcie testu (klapa / watchdog / reset Arduino)"
        if self.fixture.is_closed is not True:
            return False, "Klapa otwarta lub nieznany stan klapy w trakcie testu"
        return True, ""

    def _final_off(self) -> bool:
        if not self.relays:
            return True
        for _ in range(3):
            try:
                ok, msg = self.fixture.set_path(PATH_OFF, timeout=1.5)
            except Exception as e:
                ok, msg = False, str(e)
            if ok:
                return True
            print(f"[SEQ] PATH OFF nie potwierdzony: {msg}")
        return False

    # ------------------------------------------------------------------ #
    def run(self) -> ComboResult:
        r = ComboResult()
        r.load_status = SKIP if self.load_enabled else "OFF"
        if self.load_enabled and not self.relays:
            r.reason = "Test obciążenia wymaga sterowania przekaźnikami (Arduino/STM)"
            return r
        if self.relays:
            self.fixture.keepalive = True
        try:
            self._run(r)
        except Exception as e:
            r.overall = INVALID
            r.reason = f"Błąd sekwencji: {e}"
        finally:
            if self.oel is not None:
                try:
                    self.oel.safe_off()
                except Exception:
                    pass
            off_ok = self._final_off()
            if self.relays:
                self.fixture.keepalive = False
            if not off_ok:
                r.relay_alarm = True
                if r.overall in (PASS, FAIL):
                    r.overall = INVALID
                r.reason = ("ALARM: Arduino/STM nie potwierdziło rozłączenia torów (PATH OFF) — "
                            "sprawdź przekaźniki przed kolejnym testem! " + (r.reason or ""))
            self._phase("END", "")
        return r

    def _run(self, r: ComboResult) -> ComboResult:
        tp = self.profile["test_params"]
        ld = self.profile.get("load_params", {})

        # 1. Tor HI-POT
        self._phase("PATH", "Przełączanie toru: HI-POT")
        ok, msg = self._path(PATH_HIPOT)
        if not ok:
            r.overall, r.reason = INVALID, msg
            return r
        if self.abort.is_set():
            r.overall, r.reason = ABORTED, "Przerwano"
            return r

        # 2. Hi-Pot
        self._phase("HIPOT", "Test Hi-Pot w toku")
        verdict, dev_res, data = run_hipot(self.chroma, tp, self.abort,
                                           float(self.cfg.TEST_TIMEOUT), self.on_hipot_live)
        if verdict.reason == "ABORT" or self.abort.is_set():
            r.overall, r.reason = ABORTED, "Przerwano w trakcie Hi-Pot"
            return r
        held, why = self._path_held(PATH_HIPOT)
        if not held and verdict.result in (PASS, FAIL):
            from result_validator import Verdict
            verdict = Verdict(INVALID, "", why, verdict.voltage_v, verdict.current_ma)
        r.hipot, r.hipot_device_result, r.hipot_data = verdict, dev_res, data
        if self.on_hipot_done:
            try:
                self.on_hipot_done(verdict)
            except Exception:
                pass
        if verdict.result in (PASS, FAIL) and self.save_hipot:
            ok, msg, path = self.save_hipot(verdict)
            r.hipot_report = path
            if not ok:
                r.save_errors.append(f"Raport Hi-Pot: {msg}")

        # 3. Tor OFF
        self._phase("PATH", "Przełączanie toru: OFF")
        off_ok, off_msg = self._path(PATH_OFF)

        if verdict.result == INVALID:
            r.overall, r.reason = INVALID, verdict.reason
            return r
        if verdict.result == FAIL:
            r.overall = FAIL
            r.reason = verdict.reason or "Hi-Pot FAIL"
            return r
        if not off_ok:
            r.overall = INVALID
            r.load_status = INVALID if self.load_enabled else r.load_status
            r.reason = f"Nie udało się rozłączyć toru HI-POT: {off_msg}"
            return r
        if not self.load_enabled:
            r.overall = PASS
            return r
        if self.abort.is_set():
            r.overall, r.load_status, r.reason = ABORTED, ABORTED, "Przerwano przed testem obciążenia"
            return r

        # 4. Tor LOAD
        self._phase("PATH", "Przełączanie toru: OBCIĄŻENIE (230 V na zasilacz)")
        ok, msg = self._path(PATH_LOAD)
        if not ok:
            r.overall, r.load_status, r.reason = INVALID, INVALID, msg
            return r

        # 5. Obciążenie
        self._phase("LOAD", "Test obciążenia w toku")
        lv = run_load_test(self.oel, ld, self.abort, self.on_load_live,
                           lambda t: self._phase("LOAD", t))
        if self.abort.is_set():
            r.overall, r.reason = ABORTED, "Przerwano w trakcie testu obciążenia"
            r.load_status = ABORTED
            return r
        held, why = self._path_held(PATH_LOAD)
        if not held and lv.result in (PASS, FAIL):
            lv = LoadVerdict(INVALID, "", why, lv.points)
        r.load, r.load_status = lv, lv.result
        if lv.result in (PASS, FAIL) and self.save_load:
            ok, msg, path = self.save_load(lv)
            r.load_report = path
            if not ok:
                r.save_errors.append(f"Raport obciążenia: {msg}")
        r.overall = lv.result
        r.reason = lv.reason
        return r
