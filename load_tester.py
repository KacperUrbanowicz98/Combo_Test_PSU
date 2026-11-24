# load_tester.py
"""
Test obciążenia zasilacza na OWON OEL8512.

Zasady z baseline OEL85 Verifier 1.2.4 (BASELINE_OEL8512_FW201.md):
  * tylko zatwierdzone komendy: SYST:REM/LOC, INP, INP:SHOR, MODE CURR, CURR,
    VOLT:PROT, CURR:PROT, POW:PROT, VOLT:ON, MEAS:VOLT?/CURR?/ALL:INFO?, *IDN?;
  * weryfikacja zapisu = 'ERR' odesłany po komendzie + odczyt zwrotny
    (SYST:ERR? nie służy do weryfikacji); błąd zapisu blokuje test;
  * przed każdym testem ustawiany jest komplet parametrów;
  * bez *RST w cyklu testu.

Test 2-punktowy (CC), wykonywany jako PIERWSZY (przed Hi-Pot):
  NC:  po power_on_delay przy wejściu OFF — brak napięcia = zasilacz niepodłączony
       (LOAD-NC, FAIL; Hi-Pot nie jest wykonywany)
  MIN: i_min (0 A -> wejście OEL wyłączone, pomiar napięcia bez obciążenia)
  MAX: i_max
PASS punktu: v_min <= V <= v_max, prąd pobierany zgodny z nastawą (tolerancja),
brak zadziałania zabezpieczeń OEL (OVP/OCP/OPP z MEAS:ALL:INFO?).
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from oel85 import OEL85, OEL85Error
from transport import TransportError, build_transport

PASS, FAIL, INVALID = "PASS", "FAIL", "INVALID"


class LoadConfigError(Exception):
    """Zapis odrzucony (ERR) albo odczyt zwrotny niezgodny — test zablokowany."""


@dataclass
class PointResult:
    name: str
    i_set: float
    v: float = 0.0
    i: float = 0.0
    result: str = FAIL
    reason: str = ""
    error_code: str = ""

    def as_dict(self):
        return {"name": self.name, "i_set": self.i_set, "v": self.v, "i": self.i,
                "result": self.result, "reason": self.reason}


@dataclass
class LoadVerdict:
    result: str                         # PASS / FAIL / INVALID / SKIP
    error_code: str = ""
    reason: str = ""
    points: List[PointResult] = field(default_factory=list)


def _is_err(resp) -> bool:
    return bool(resp) and str(resp).strip().upper().startswith("ERR")


def _num(resp) -> Optional[float]:
    try:
        return float(str(resp).split(",")[0])
    except (TypeError, ValueError):
        return None


def protection_levels(ld: dict) -> dict:
    """Zabezpieczenia OEL wyliczane z profilu (z zapasem nad punktami pracy)."""
    vmax, imax = float(ld["v_max"]), float(ld["i_max"])
    return {
        "VOLT:PROT": round(min(150.0, vmax * 1.2 + 1.0), 2),
        "CURR:PROT": round(min(42.0, imax * 1.25 + 0.2), 3),
        "POW:PROT": round(min(420.0, vmax * imax * 1.3 + 2.0), 1),
        "VOLT:ON": round(max(0.5, float(ld["v_min"]) * 0.5), 2),
    }


class OELStation:
    """Połączenie z OEL8512 + operacje z weryfikacją odczytem zwrotnym."""

    def __init__(self, cfg, transport=None):
        self.cfg = cfg
        if transport is None:
            if cfg.SIMULATION:
                from simulators import make_oel_transport
                transport = make_oel_transport(cfg)
            else:
                transport = build_transport(cfg.oel_conn())
        self.t = transport
        self.dev = OEL85(self.t)
        self.idn = ""
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool:
        return self.t.is_open

    def connect(self):
        """Zwraca (ok, komunikat). Po połączeniu: tryb zdalny, wejście OFF."""
        with self.lock:
            try:
                self.t.open()
                self.idn = self.t.query("*IDN?") or ""
                if not self.idn or _is_err(self.idn):
                    self.t.close()
                    return False, "Brak odpowiedzi na *IDN? — sprawdź port/baudrate OEL"
                self.t.write("SYST:REM")
                self.safe_off()
                return True, self.idn
            except Exception as e:
                try:
                    self.t.close()
                except Exception:
                    pass
                return False, str(e)

    def disconnect(self):
        with self.lock:
            if self.t.is_open:
                self.safe_off()
                try:
                    self.t.write("SYST:LOC")
                except Exception:
                    pass
            try:
                self.t.close()
            except Exception:
                pass

    def safe_off(self):
        """Zwarcie OFF, wejście OFF — zawsze, bez weryfikacji (ścieżka awaryjna)."""
        for cmd in ("INP:SHOR 0", "INP 0"):
            try:
                self.t.write(cmd)
            except Exception as e:
                print(f"[OEL] safe_off {cmd}: {e}")

    # ------------------------------------------------------------------ #
    def _write_checked(self, cmd: str):
        extra = self.t.write(cmd)
        if _is_err(extra):
            raise LoadConfigError(f"OEL odrzucił '{cmd}' (ERR)")

    def set_verified(self, header: str, value: float):
        """Zapis wartości + ERR + odczyt zwrotny (tolerancja jak w weryfikatorze)."""
        with self.lock:
            self._write_checked(f"{header} {value:g}")
            resp = self.t.query(f"{header}?")
            got = _num(resp)
            if got is None:
                raise LoadConfigError(f"{header}? -> brak/nieczytelna odpowiedź ({resp!r})")
            tol = max(0.002, abs(value) * 0.003)
            if abs(got - value) > tol:
                raise LoadConfigError(f"{header}: ustawiono {value:g}, OEL ma {got:g}")

    def set_input(self, on: bool):
        with self.lock:
            self._write_checked(f"INP {1 if on else 0}")
            resp = (self.t.query("INP?") or "").strip().upper()
            state = {"1": True, "ON": True, "0": False, "OFF": False}.get(resp)
            if state is None or state != on:
                raise LoadConfigError(f"INP?: oczekiwano {int(on)}, OEL zwrócił {resp!r}")

    def set_short_off(self):
        """INP:SHOR 0 z weryfikacją — przy zwarciu INP 1 zwarłoby wyjście zasilacza."""
        with self.lock:
            self._write_checked("INP:SHOR 0")
            resp = (self.t.query("INP:SHOR?") or "").strip().upper()
            if resp not in ("0", "OFF"):
                raise LoadConfigError(f"INP:SHOR?: oczekiwano 0, OEL zwrócił {resp!r}")

    def prepare(self, ld: dict):
        """Komplet parametrów przed testem (zwarcie OFF, wejście OFF, CC 0 A, zabezpieczenia, Von)."""
        with self.lock:
            self.safe_off()
            self.set_short_off()
            self.set_input(False)
            self._write_checked("MODE CURR")
            mode = (self.t.query("MODE?") or "").strip().lower()
            if not mode.startswith("curr"):           # MODE? wiarygodne dla CURR (baseline)
                raise LoadConfigError(f"MODE?: oczekiwano 'current', OEL zwrócił {mode!r}")
            for header, val in protection_levels(ld).items():
                self.set_verified(header, val)
            self.set_verified("CURR", 0.0)

    def measure(self):
        """(V, I, flagi) — jeden odczyt. Flagi = lista zadziałanych zabezpieczeń."""
        with self.lock:
            r = self.t.query("MEAS:ALL:INFO?")
        if r is None or _is_err(r):
            raise TransportError("Brak odpowiedzi na MEAS:ALL:INFO?")
        f = [x.strip() for x in r.split(",")]
        if len(f) < 6:
            raise TransportError(f"MEAS:ALL:INFO? — nieoczekiwana odpowiedź {r!r}")
        try:
            v, i = float(f[0]), float(f[1])
        except ValueError:
            raise TransportError(f"MEAS:ALL:INFO? — nieczytelne wartości {r!r}")
        flags = [n for n, x in zip(("OVP", "OCP", "OPP"), f[3:6]) if x.upper() in ("ON", "1")]
        return v, i, flags


def _sleep_abortable(seconds: float, abort: threading.Event, tick=None, step=0.25):
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if abort.is_set():
            return False
        if tick:
            tick()
        time.sleep(min(step, max(0.0, end - time.time())))
    return not abort.is_set()


def nc_threshold(ld: dict) -> float:
    """Poniżej tego napięcia (bez obciążenia) zasilacz uznajemy za niepodłączony."""
    try:
        v = float(ld.get("nc_v", 0) or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else max(0.5, float(ld["v_min"]) * 0.3)


def run_load_test(st: OELStation, ld: dict, abort: threading.Event,
                  on_live: Optional[Callable[[dict], None]] = None,
                  on_phase: Optional[Callable[[str], None]] = None) -> LoadVerdict:
    """
    Wywoływać PO przełączeniu przekaźnika na 'load' (zasilacz dostaje 230 V).
    Zawsze kończy z wejściem OEL wyłączonym.
    """
    vmin, vmax = float(ld["v_min"]), float(ld["v_max"])
    i_tol_pct, i_tol_abs = float(ld.get("i_tol_pct", 3.0)), float(ld.get("i_tol_abs", 0.02))
    samples = max(1, int(ld.get("samples", 5)))
    points = [("MIN", float(ld["i_min"])), ("MAX", float(ld["i_max"]))]
    results: List[PointResult] = []
    live = {"point": "", "i_set": 0.0, "v": 0.0, "i": 0.0, "p": 0.0}

    def push(**kw):
        live.update(kw)
        if on_live:
            try:
                on_live(dict(live))
            except Exception:
                pass

    def tick():
        try:
            v, i, _ = st.measure()
            push(v=v, i=i, p=v * i)
        except Exception:
            pass

    try:
        if on_phase:
            on_phase("Konfiguracja OEL8512 (odczyt zwrotny)")
        st.prepare(ld)

        if on_phase:
            on_phase("Załączanie zasilacza — oczekiwanie na napięcie")
        push(point="START", i_set=0.0)
        if not _sleep_abortable(float(ld.get("power_on_delay", 2.0)), abort, tick):
            return LoadVerdict(INVALID, "", "Test obciążenia przerwany")

        # Zasilacz podłączony? (wejście OEL OFF, samo napięcie wyjściowe)
        if on_phase:
            on_phase("Sprawdzanie podłączenia zasilacza")
        v0s = []
        for _ in range(3):
            v0, i0, _ = st.measure()
            v0s.append(v0)
            push(point="NC", v=v0, i=i0, p=v0 * i0)
            time.sleep(0.1)
        v0 = max(v0s)
        thr = nc_threshold(ld)
        if v0 < thr:
            nc = PointResult("NC", 0.0, round(v0, 4), 0.0, FAIL,
                             f"Brak napięcia z zasilacza ({v0:.2f} V < {thr:.2f} V) — "
                             "zasilacz niepodłączony lub nie działa", "LOAD-NC")
            return LoadVerdict(FAIL, "LOAD-NC", nc.reason, [nc])

        for name, i_set in points:
            if abort.is_set():
                return LoadVerdict(INVALID, "", "Test obciążenia przerwany", results)
            if on_phase:
                on_phase(f"Punkt {name}: {i_set:.3f} A")
            push(point=name, i_set=i_set)
            if i_set <= 0:
                st.set_input(False)
            else:
                st.set_verified("CURR", i_set)
                if abort.is_set():
                    return LoadVerdict(INVALID, "", "Test obciążenia przerwany", results)
                st.set_input(True)
            if not _sleep_abortable(float(ld.get("settle_time", 1.0)), abort, tick):
                return LoadVerdict(INVALID, "", "Test obciążenia przerwany", results)

            vs, is_, flags = [], [], set()
            for _ in range(samples):
                if abort.is_set():
                    return LoadVerdict(INVALID, "", "Test obciążenia przerwany", results)
                v, i, fl = st.measure()
                vs.append(v)
                is_.append(i)
                flags.update(fl)
                push(v=v, i=i, p=v * i)
                time.sleep(0.1)
            v_avg, i_avg = sum(vs) / len(vs), sum(is_) / len(is_)
            pr = PointResult(name, i_set, round(v_avg, 4), round(i_avg, 4), PASS)
            if flags:
                pr.result, pr.error_code = FAIL, "LOAD-PROT"
                pr.reason = f"Zadziałało zabezpieczenie OEL: {', '.join(sorted(flags))}"
            elif v_avg < vmin:
                pr.result, pr.error_code = FAIL, "LOAD-VLO"
                pr.reason = f"{name}: {v_avg:.3f} V < Vmin {vmin:.3f} V"
            elif v_avg > vmax:
                pr.result, pr.error_code = FAIL, "LOAD-VHI"
                pr.reason = f"{name}: {v_avg:.3f} V > Vmax {vmax:.3f} V"
            elif i_set > 0 and abs(i_avg - i_set) > max(i_tol_abs, i_set * i_tol_pct / 100.0):
                pr.result, pr.error_code = FAIL, "LOAD-I"
                pr.reason = f"{name}: pobór {i_avg:.3f} A zamiast {i_set:.3f} A"
            results.append(pr)
            st.set_input(False)
            if pr.result == FAIL:
                break                     # dalsze punkty bez sensu — zasilacz odpada

        failed = [p for p in results if p.result != PASS]
        if failed:
            return LoadVerdict(FAIL, failed[0].error_code, failed[0].reason, results)
        return LoadVerdict(PASS, "", "", results)

    except LoadConfigError as e:
        return LoadVerdict(INVALID, "", f"Konfiguracja OEL nie powiodła się: {e}", results)
    except (TransportError, OEL85Error) as e:
        return LoadVerdict(INVALID, "", f"Utracono komunikację z OEL8512: {e}", results)
    finally:
        st.safe_off()
