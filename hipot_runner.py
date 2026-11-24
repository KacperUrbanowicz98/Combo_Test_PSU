# hipot_runner.py
"""
Jeden cykl testu Hi-Pot na Chromie 19052 — logika wydzielona z test_screen.py
aplikacji Hi-Pot PSU 1.1.2 (bez zmian merytorycznych):
  * odpytywanie statusu od razu po START, koniec dopiero po min_run_time,
  * dowody wykonania testu (RUNNING / napięcie w trakcie) -> result_validator,
  * zerwana komunikacja -> INVALID (nie FAIL), przerwanie -> brak wyniku.
"""
import threading
import time
from typing import Callable, Optional, Tuple

from result_validator import INVALID, RunEvidence, Verdict, validate

MAX_COMM_ERRORS = 15
TEST_GRACE_SECONDS = 5.0


def chroma_params(test_params: dict) -> dict:
    """Profil (limity w mA) -> parametry dla ChromaHiPotDevice.configure_test (A)."""
    p = test_params
    return {
        "voltage": p["voltage"],
        "current_limit_high": float(p["current_limit_high"]) / 1000.0,
        "current_limit_low": float(p["current_limit_low"]) / 1000.0,
        "duration": p["test_time"],
        "ramp_time": p["ramp_time"],
        "fall_time": p["fall_time"],
    }


def total_time(test_params: dict) -> float:
    try:
        return (float(test_params["ramp_time"]) + float(test_params["test_time"])
                + float(test_params["fall_time"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def configure(device, test_params: dict) -> bool:
    """Czyści kroki i konfiguruje STEP1 z odczytem zwrotnym (hipot_device)."""
    device.clear_steps()
    return device.configure_test(step=1, mode=test_params["mode"],
                                 params=chroma_params(test_params))


def run_hipot(device, test_params: dict, abort: threading.Event,
              test_timeout: float = 300,
              on_live: Optional[Callable[[float, float, float], None]] = None,
              max_seconds: Optional[float] = None
              ) -> Tuple[Verdict, str, dict]:
    """
    Zwraca (verdict, wynik_z_chromy, dane). verdict.result: PASS/FAIL/INVALID.
    Przy przerwaniu (abort) zwraca INVALID z reason='ABORT' — wywołujący nie zapisuje wyniku.
    on_live(V, I_mA, elapsed_s).
    max_seconds — twardy limit z okna przekaźnika (moduł sam wraca do 'load'):
    po jego przekroczeniu STOP (HV off) i wynik NIEWAŻNY.
    """
    ok, msg = device.start_test()
    if not ok:
        return Verdict(INVALID, "", msg or "Urządzenie odrzuciło START"), "UNKNOWN", {}

    tt = total_time(test_params)
    hard_limit = max(tt + TEST_GRACE_SECONDS, min(float(test_timeout), tt + 60))
    if max_seconds is not None:
        hard_limit = min(hard_limit, max(0.5, float(max_seconds)))
    min_run_time = min(2.0, max(0.5, tt))
    ev = RunEvidence()
    t0 = time.time()

    time.sleep(0.15)
    while True:
        if abort.is_set():
            device.stop_test()
            return Verdict(INVALID, "", "ABORT"), "UNKNOWN", {}
        status = device.get_status()
        elapsed = time.time() - t0
        if device.status_is_running(status):
            ev.saw_running = True
        if device.comm_errors >= MAX_COMM_ERRORS:
            device.stop_test()
            return (Verdict(INVALID, "", "Utracono komunikację z Hi-Pot (RS232) w trakcie testu"),
                    "UNKNOWN", {})
        if elapsed >= min_run_time and device.status_is_finished(status):
            break
        m = device.read_measurements()
        if m:
            v = m["output_voltage"]
            i = m["measure_current"] * 1000.0
            if 0 < v < 1e6:
                ev.peak_voltage = max(ev.peak_voltage, v)
            if 0 < i < 9999:
                ev.peak_current_ma = max(ev.peak_current_ma, i)
            if on_live:
                try:
                    on_live(v, i, time.time() - t0)
                except Exception:
                    pass
        if time.time() - t0 > hard_limit:
            # Nie czytamy RESult:LAST — to może być wynik poprzedniej sztuki.
            device.stop_test()
            print("[HIPOT] Przekroczono limit czasu — STOP, test nieważny")
            return (Verdict(INVALID, "", f"Przekroczono limit czasu testu Hi-Pot ({hard_limit:.1f} s) "
                                         "— HV wyłączone, wynik nieważny"), "UNKNOWN", {})
        time.sleep(0.1)

    if abort.is_set():
        device.stop_test()
        return Verdict(INVALID, "", "ABORT"), "UNKNOWN", {}
    device_result, data = device.get_test_result()
    verdict = validate(device_result, data, test_params, ev)
    print(f"[HIPOT] Chroma={device_result} -> aplikacja={verdict.result} "
          f"(running={ev.saw_running}, peakV={ev.peak_voltage:.0f}, "
          f"peakI={ev.peak_current_ma:.3f} mA) {verdict.reason}")
    return verdict, device_result, data
