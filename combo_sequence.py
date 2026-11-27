# combo_sequence.py
"""
Sekwencja testu stanowiska (v1.1.0): OBCIĄŻENIE -> Hi-Pot.

  0. Klapa zamknięta (interlock), nasłuch klapy przez CAŁY test:
     otwarcie / utrata sygnału = natychmiastowe przerwanie (OEL OFF / Hi-Pot STOP)
  1. przekaźnik 'load'  (zasilacz pod 230 V, wyjście na OEL8512)
  2. test obciążenia 2-punktowy (load_tester), w tym kontrola podłączenia
     zasilacza (LOAD-NC)                     raport LOAD zapisywany (PASS/FAIL)
       obciążenie FAIL / NIEWAŻNY -> koniec, Hi-Pot NIE jest wykonywany
  3. OEL wejście OFF, sprawdzenie czy Hi-Pot zmieści się w oknie przekaźnika
  4. przekaźnik 'hipot'
  5. Hi-Pot (hipot_runner) z limitem czasu = okno przekaźnika
     (moduł sam wraca do 'load' po RELAY_REVERT_S)  raport Hi-Pot (PASS/FAIL)
  6. ZAWSZE po Hi-Pot (także przy przerwaniu): STOP, odczekanie HV_DISCHARGE_S,
     przekaźnik 'load'

Wątek przerywający (STOP / klapa) nigdy nie wysyła komend do przekaźnika -
robi to wyłącznie ta sekwencja, po wyłączeniu HV.
Bez Tk - używane przez ekran testu i test w pętli z panelu ENG.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from hipot_runner import run_hipot, total_time
from load_tester import LoadVerdict, run_load_test
from relay import MODE_HIPOT, MODE_LOAD

PASS, FAIL, INVALID, ABORTED, SKIP = "PASS", "FAIL", "INVALID", "ABORTED", "SKIP"

HIPOT_WINDOW_MARGIN_S = 3.0     # zapas między końcem Hi-Pot a powrotem przekaźnika do 'load'

# Tryb stanowiska (ENG -> Ogólne). Tryby awaryjne tylko na bieżącą sesję - po restarcie COMBO.
MODE_COMBO, MODE_LOAD_ONLY, MODE_HIPOT_ONLY = "combo", "load_only", "hipot_only"
MODE_LABELS = {MODE_COMBO: "COMBO (obciążenie + Hi-Pot)",
               MODE_LOAD_ONLY: "TRYB AWARYJNY: tylko test obciążenia",
               MODE_HIPOT_ONLY: "TRYB AWARYJNY: tylko Hi-Pot"}


def station_mode(cfg) -> str:
    m = getattr(cfg, "TEST_MODE", MODE_COMBO)
    return m if m in MODE_LABELS else MODE_COMBO


def plan(cfg, profile: dict):
    """(robić obciążenie, robić Hi-Pot) dla trybu stanowiska i profilu."""
    mode = station_mode(cfg)
    load = bool(profile.get("load_params", {}).get("enabled", False)) and mode != MODE_HIPOT_ONLY
    hipot = mode != MODE_LOAD_ONLY
    return load, hipot


def hipot_fits(cfg, test_params: dict):
    """Czy Hi-Pot z profilu zmieści się w oknie przekaźnika (moduł sam wraca do 'load')."""
    settle = max(0, int(cfg.RELAY_SETTLE_MS)) / 1000.0
    window = float(cfg.RELAY_REVERT_S) - settle - HIPOT_WINDOW_MARGIN_S
    need = total_time(test_params)
    if need > window:
        return False, (f"Hi-Pot w profilu trwa {need:.1f} s, a przekaźnik wraca do 'load' po "
                       f"{float(cfg.RELAY_REVERT_S):.0f} s - skróć czasy Hi-Pot w profilu "
                       f"(maks. {max(0.0, window):.1f} s)")
    return True, ""


@dataclass
class ComboResult:
    overall: str = INVALID
    reason: str = ""
    hipot: object = None            # result_validator.Verdict
    hipot_device_result: str = ""
    hipot_data: dict = field(default_factory=dict)
    hipot_skipped: str = ""         # dlaczego Hi-Pot nie był wykonywany
    load: Optional[LoadVerdict] = None
    load_status: str = SKIP         # PASS/FAIL/INVALID/ABORTED/SKIP/OFF (wyłączony w profilu)
    save_errors: list = field(default_factory=list)
    hipot_report: str = ""
    load_report: str = ""
    relay_alarm: bool = False
    lid_tripped: bool = False
    hipot_off: bool = False         # Hi-Pot wyłączony trybem stanowiska (tylko obciążenie)
    mode: str = MODE_COMBO

    @property
    def hipot_status(self) -> str:
        if self.hipot is not None:
            return self.hipot.result
        return "OFF" if self.hipot_off else SKIP


class ComboSequence:

    def __init__(self, cfg, profile: dict, chroma, oel, relay, interlock, abort: threading.Event,
                 on_phase: Optional[Callable[[str, str], None]] = None,
                 on_hipot_live: Optional[Callable[[float, float, float], None]] = None,
                 on_hipot_done: Optional[Callable[[object], None]] = None,
                 on_load_live: Optional[Callable[[dict], None]] = None,
                 on_load_done: Optional[Callable[[object], None]] = None,
                 save_hipot: Optional[Callable] = None,
                 save_load: Optional[Callable] = None):
        self.cfg = cfg
        self.profile = profile
        self.chroma = chroma
        self.oel = oel
        self.relay = relay
        self.interlock = interlock
        self.abort = abort
        self.on_phase = on_phase
        self.on_hipot_live = on_hipot_live
        self.on_hipot_done = on_hipot_done
        self.on_load_live = on_load_live
        self.on_load_done = on_load_done
        self.save_hipot = save_hipot
        self.save_load = save_load
        self.lid_reason = ""
        self._watch_stop = threading.Event()

    # ------------------------------------------------------------------ #
    @property
    def lid_watch(self) -> bool:
        return bool(self.cfg.INTERLOCK_ENABLED and self.interlock is not None)

    @property
    def load_enabled(self) -> bool:
        """Czy w tym teście wykonujemy obciążenie (profil + tryb stanowiska)."""
        return plan(self.cfg, self.profile)[0]

    @property
    def hipot_enabled(self) -> bool:
        return plan(self.cfg, self.profile)[1]

    def _phase(self, step: str, text: str):
        if self.on_phase:
            try:
                self.on_phase(step, text)
            except Exception:
                pass

    def _cb(self, fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception:
                pass

    def _lid_state_bad(self) -> str:
        if not self.lid_watch:
            return ""
        st = self.interlock.is_closed
        if st is True:
            return ""
        return "Otwarto klapę w trakcie testu" if st is False else "Utracono sygnał klapy (interlock)"

    def _watch_lid(self):
        """Nasłuch klapy przez cały test - otwarcie przerywa test natychmiast."""
        while not self._watch_stop.wait(0.05):
            why = self._lid_state_bad()
            if why:
                self.lid_reason = why
                print(f"[SEQ] {why} - przerwanie testu")
                self.abort.set()
                if self.oel is not None:
                    try:
                        self.oel.safe_off()
                    except Exception:
                        pass
                return

    def _aborted(self, r: ComboResult, where: str):
        r.overall = ABORTED
        if not self.lid_reason:
            self.lid_reason = self._lid_state_bad()     # STOP z ekranu mógł wyprzedzić wątek nasłuchu
        r.lid_tripped = bool(self.lid_reason)
        r.reason = f"{self.lid_reason} ({where})" if self.lid_reason else f"Przerwano ({where})"
        return r

    def _relay(self, mode: str):
        try:
            return self.relay.set_mode(mode)
        except Exception as e:
            return False, f"Przekaźnik: {e}"

    # ------------------------------------------------------------------ #
    def run(self) -> ComboResult:
        r = ComboResult(mode=station_mode(self.cfg))
        r.load_status = SKIP if self.load_enabled else "OFF"
        r.hipot_off = not self.hipot_enabled
        if not self.load_enabled and not self.hipot_enabled:
            r.reason = "Profil nie ma testu obciążenia, a stanowisko jest w trybie 'tylko obciążenie'"
            return r
        if self.relay is None:
            r.reason = "Brak połączenia z przekaźnikiem - test zablokowany"
            return r
        why = self._lid_state_bad()
        if why:
            r.reason = "Klapa otwarta - zamknij klapę" if "Otwarto" in why else why
            return r
        fits, why = hipot_fits(self.cfg, self.profile["test_params"]) if self.hipot_enabled else (True, "")
        if not fits:                    # przed obciążeniem - nie podajemy 230 V na darmo
            r.reason, r.hipot_skipped = why, "za długi"
            return r
        watcher = None
        if self.lid_watch:
            watcher = threading.Thread(target=self._watch_lid, daemon=True)
            watcher.start()
        try:
            self._run(r)
        except Exception as e:
            r.overall = INVALID
            r.reason = f"Błąd sekwencji: {e}"
        finally:
            self._watch_stop.set()
            if self.oel is not None:
                try:
                    self.oel.safe_off()
                except Exception:
                    pass
            self._phase("END", "")
        return r

    def _run(self, r: ComboResult) -> ComboResult:
        tp = self.profile["test_params"]
        ld = self.profile.get("load_params", {})

        # 1-2. OBCIĄŻENIE
        if self.load_enabled:
            self._phase("RELAY", "Przekaźnik: LOAD")
            ok, msg = self._relay(MODE_LOAD)
            if not ok:
                r.overall, r.load_status, r.reason = INVALID, INVALID, msg
                r.hipot_skipped = "przekaźnik"
                return r
            if self.abort.is_set():
                return self._aborted(r, "przed testem obciążenia")

            self._phase("LOAD", "Test obciążenia w toku")
            lv = run_load_test(self.oel, ld, self.abort, self.on_load_live,
                               lambda t: self._phase("LOAD", t))
            if self.abort.is_set():
                r.load_status = ABORTED
                r.hipot_skipped = "przerwano"
                return self._aborted(r, "test obciążenia")
            r.load, r.load_status = lv, lv.result
            self._cb(self.on_load_done, lv)
            if lv.result in (PASS, FAIL) and self.save_load:
                ok, msg, path = self.save_load(lv)
                r.load_report = path
                if not ok:
                    r.save_errors.append(f"Raport obciążenia: {msg}")
            if lv.result != PASS:
                r.overall, r.reason = lv.result, lv.reason
                if self.hipot_enabled:
                    r.hipot_skipped = "obciążenie " + ("FAIL" if lv.result == FAIL else "NIEWAŻNE")
                return r
            if not self.hipot_enabled:  # tryb awaryjny: tylko obciążenie
                r.overall = PASS
                return r

        # 3. Przygotowanie Hi-Pot
        if self.oel is not None:
            self.oel.safe_off()
        if self.abort.is_set():
            return self._aborted(r, "przed Hi-Pot")
        # 4-6. HI-POT
        self._phase("RELAY", "Przekaźnik: HI-POT")
        try:
            ok, msg = self._relay(MODE_HIPOT)
            if not ok:
                r.overall, r.reason = INVALID, msg
                return r
            if self.abort.is_set():
                return self._aborted(r, "przed Hi-Pot")
            self._phase("HIPOT", "Test Hi-Pot w toku")
            max_s = self.relay.hipot_time_left() - 1.0
            verdict, dev_res, data = run_hipot(self.chroma, tp, self.abort,
                                               float(self.cfg.TEST_TIMEOUT), self.on_hipot_live,
                                               max_seconds=max_s)
        finally:
            self._hipot_cleanup(r)

        if verdict.reason == "ABORT" or self.abort.is_set():
            return self._aborted(r, "Hi-Pot")
        r.hipot, r.hipot_device_result, r.hipot_data = verdict, dev_res, data
        self._cb(self.on_hipot_done, verdict)
        if verdict.result in (PASS, FAIL) and self.save_hipot:
            ok, msg, path = self.save_hipot(verdict)
            r.hipot_report = path
            if not ok:
                r.save_errors.append(f"Raport Hi-Pot: {msg}")
        r.overall = verdict.result
        if verdict.result != PASS:
            r.reason = verdict.reason or f"Hi-Pot {verdict.result}"
        if r.relay_alarm:
            r.reason = (r.reason + " | " if r.reason else "") + \
                "UWAGA: przekaźnik nie potwierdził powrotu do 'load'"
        return r

    def _hipot_cleanup(self, r: ComboResult):
        """HV off -> rozładowanie -> 'load'. Wykonywane zawsze po fazie Hi-Pot."""
        try:
            self.chroma.stop_test()
        except Exception:
            pass
        self._phase("RELAY", "Rozładowanie HV, przekaźnik: LOAD")
        time.sleep(max(0.0, float(self.cfg.HV_DISCHARGE_S)))
        ok, msg = self._relay(MODE_LOAD)
        if not ok:
            ok, msg = self._relay(MODE_LOAD)
        if not ok:
            r.relay_alarm = True
            print(f"[SEQ] Przekaźnik 'load' po Hi-Pot: {msg}")
