"""Driver OWON OEL85 (OEL8512) - wysokopoziomowe API do reużycia w stanowisku Hi-Pot + obciążenie.

Składnia wg OEL85 Programming Manual V1.0.2. Używane są krótkie formy komend
(np. CURR, VOLT:PROT). Metody nie sprawdzają odpowiedzi błędów, bo manual nie
dokumentuje kolejki błędów (SYST:ERR?) - weryfikacja = odczyt zwrotny (readback).
"""
import logging

from transport import TransportError

log = logging.getLogger(__name__)

MODES = ("CURR", "VOLT", "POW", "RES", "DYN", "BAT", "OCP", "OPP")
SLEW_SPEEDS = ("FAST", "NORM", "SLOW")


class OEL85Error(Exception):
    pass


def _num(resp):
    if resp is None:
        raise OEL85Error("Brak odpowiedzi")
    try:
        return float(resp.split(",")[0])
    except ValueError:
        raise OEL85Error(f"Nieoczekiwana odpowiedź: {resp!r}")


def _bool(resp):
    if resp is None:
        raise OEL85Error("Brak odpowiedzi")
    r = resp.strip().upper()
    if r in ("1", "ON"):
        return True
    if r in ("0", "OFF"):
        return False
    raise OEL85Error(f"Nieoczekiwana odpowiedź bool: {resp!r}")


class OEL85:
    def __init__(self, transport):
        self.t = transport
        self.idn = ""

    # ---------------------------------------------------------- połączenie
    def connect(self):
        self.t.open()
        self.idn = self.t.query("*IDN?") or ""
        if not self.idn:
            log.warning("Brak odpowiedzi na *IDN? - sprawdź baudrate/terminator/kabel")
        return self.idn

    def disconnect(self, safe_off=True):
        if self.t.is_open and safe_off:
            try:
                self.input_off()
            except Exception:
                pass
        self.t.close()

    @property
    def connected(self):
        return self.t.is_open

    # ---------------------------------------------------------- surowe
    def write(self, cmd):
        self.t.write(cmd)

    def query(self, cmd, timeout_s=None):
        return self.t.query(cmd, timeout_s)

    # ---------------------------------------------------------- system
    def identify(self):
        return self.query("*IDN?")

    def reset(self):
        self.write("*RST")

    def remote(self):
        self.write("SYST:REM")

    def local(self):
        self.write("SYST:LOC")

    def set_remote_sense(self, on):
        self.write(f"SYST:SENS {1 if on else 0}")

    # ---------------------------------------------------------- wejście
    def input_on(self):
        self.write("INP 1")

    def input_off(self):
        """Bezpieczne wyłączenie: zdejmij zwarcie i wyłącz wejście."""
        self.write("INP:SHOR 0")
        self.write("INP 0")

    def input_state(self):
        return _bool(self.query("INP?"))

    def set_short(self, on):
        self.write(f"INP:SHOR {1 if on else 0}")

    # ---------------------------------------------------------- tryby
    def set_mode(self, mode):
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"Nieznany tryb {mode}")
        self.write(f"MODE {mode}")

    def get_mode(self):
        return (self.query("MODE?") or "").strip().lower()

    def set_cc(self, amps):
        self.write(f"CURR {amps:g}")

    def set_cv(self, volts):
        self.write(f"VOLT {volts:g}")

    def set_cr(self, ohms):
        self.write(f"RES {ohms:g}")

    def set_cp(self, watts):
        self.write(f"POW {watts:g}")

    def set_protections(self, ovp=None, ocp=None, opp=None):
        if ovp is not None:
            self.write(f"VOLT:PROT {ovp:g}")
        if ocp is not None:
            self.write(f"CURR:PROT {ocp:g}")
        if opp is not None:
            self.write(f"POW:PROT {opp:g}")

    def set_von(self, volts):
        self.write(f"VOLT:ON {volts:g}")

    # ---------------------------------------------------------- dynamika
    def set_dynamic(self, high_a, high_s, low_a, low_s, slew=None, mode="CONT"):
        self.write(f"DYN:HIGH {high_a:g}")
        self.write(f"DYN:HIGH:DWEL {high_s:g}")
        self.write(f"DYN:LOW {low_a:g}")
        self.write(f"DYN:LOW:DWEL {low_s:g}")
        if slew is not None:
            self.write(f"DYN:SLEW {slew:g}")
        self.write(f"DYN:MODE {mode}")

    # ---------------------------------------------------------- pomiary
    def meas_voltage(self):
        return _num(self.query("MEAS:VOLT?"))

    def meas_current(self):
        return _num(self.query("MEAS:CURR?"))

    def meas_power(self):
        return _num(self.query("MEAS:POW?"))

    def meas_all(self):
        """MEAS:ALL:INFO? -> dict(v, i, p, ovp, ocp, opp)."""
        r = self.query("MEAS:ALL:INFO?")
        if r is None:
            raise OEL85Error("Brak odpowiedzi na MEAS:ALL:INFO?")
        f = [x.strip() for x in r.split(",")]
        if len(f) < 6:
            raise OEL85Error(f"MEAS:ALL:INFO? - za mało pól: {r!r}")
        return {"v": float(f[0]), "i": float(f[1]), "p": float(f[2]),
                "ovp": f[3].upper() == "ON", "ocp": f[4].upper() == "ON", "opp": f[5].upper() == "ON"}


__all__ = ["OEL85", "OEL85Error", "TransportError", "MODES", "SLEW_SPEEDS"]
