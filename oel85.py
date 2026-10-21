"""Driver OWON OEL85 (OEL8512) - wysokopoziomowe API do reużycia w stanowisku Hi-Pot + obciążenie.

Składnia wg OEL85 Programming Manual V1.0.2. Używane są krótkie formy komend (np. CURR, VOLT:PROT).

Zachowanie OEL8512 FW V2.0.1 sprawdzone weryfikatorem (2026-10-20):
  - nieznane zapytanie -> 'ERR'; odrzucony zapis -> 'ERR' wysłane zaraz po komendzie,
  - zapis poza zakresem / przy wyłączonym warunku BAT bywa ignorowany BEZ 'ERR' -> zawsze readback,
  - *RST = restart urządzenia + kilka s niestabilnych odpowiedzi - użyj reset(); w stanowisku
    lepiej nie używać *RST, tylko ustawiać komplet parametrów przed testem,
  - MODE? po BAT/OCP/OPP zwraca 'ocp'/'opp'/'continuous',
  - OCP:IDEL? zwraca pustą linię; VOLT:RANG?/CURR:RANG? zawsze '1',
  - zaraz po restarcie część zapytań (np. SYST:SENS?) zwraca pustą linię / milczy, choć *IDN? już działa,
  - BAT:CAPA?/ENERGY?/TIME?/STAT?/ALL? i OPP:IDEL -> ERR,
  - BAT:VST/CST/TIM zapisywalne tylko przy włączonym BAT:VEN/CEN/TEN,
  - DYN:HIGH:DWEL minimum 0.1 s,
  - SYST:ERR? istnieje, ale zawsze zwraca 0x0000 (nie rejestruje błędów) - nie używać do weryfikacji,
  - SYST:VERS? -> wersja FW; zakresy BAT: VST 0-150 V, CST 0-999.999 Ah, TIM 0-999999 s.
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


def wait_stable_after_reset(t, max_s=20.0, rounds=3, stop=lambda: False):
    """Po *RST OEL8512 FW V2.0.1 restartuje się, a potem przez kilka sekund odpowiada niestabilnie:
    *IDN? (a nawet pojedyncze MODE?) już działa, a kolejne zapytania milczą albo dają pustą linię.
    Czekamy na *IDN?, wysyłamy SYST:REM i wymagamy `rounds` kolejnych poprawnych rund MODE? + CURR?.
    Zwraca czas [s] do stabilności albo None (timeout / stop)."""
    import time
    t0 = time.monotonic()
    deadline = t0 + max_s
    time.sleep(1.0)
    while True:                                   # etap 1: interfejs żyje
        if stop() or time.monotonic() > deadline:
            return None
        r = t.query("*IDN?", timeout_s=0.5)
        if r and "," in r:
            break
        time.sleep(0.3)
    t.write("SYST:REM")
    ok_rounds = 0
    while ok_rounds < rounds:                     # etap 2: kilka poprawnych rund pod rząd
        if stop() or time.monotonic() > deadline:
            return None
        m = t.query("MODE?", timeout_s=0.5)
        c = t.query("CURR?", timeout_s=0.5)
        good = bool(m) and not m.upper().startswith("ERR") and bool(c) and _is_number(c)
        if good:
            ok_rounds += 1
        else:
            ok_rounds = 0
            t.write("SYST:REM")
        time.sleep(0.3)
    return time.monotonic() - t0


def _is_number(s):
    try:
        float(s.split(",")[0])
        return True
    except ValueError:
        return False


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
        """Zwraca niezamówioną odpowiedź po zapisie (np. 'ERR') albo None."""
        return self.t.write(cmd)

    def query(self, cmd, timeout_s=None):
        return self.t.query(cmd, timeout_s)

    # ---------------------------------------------------------- system
    def identify(self):
        return self.query("*IDN?")

    def reset(self, wait_s=20.0):
        """*RST. OEL8512 FW V2.0.1 restartuje się (na UART pojawia się 0x00) i przez kilka sekund
        nie odpowiada - czekamy na *IDN?, potem przywracamy tryb zdalny i wyłączamy wejście."""
        self.write("*RST")
        dt = wait_stable_after_reset(self.t, wait_s)
        if dt is None:
            raise OEL85Error("Urządzenie niestabilne po *RST")
        self.input_off()
        return dt

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
        """UWAGA OEL8512 FW V2.0.1: po MODE BAT/OCP/OPP odpowiedź MODE? to 'ocp'/'opp'/'continuous'
        (prawdopodobnie przesunięta tabela nazw w firmware). Nie polegać na MODE? dla tych trybów,
        dopóki nie potwierdzono na wyświetlaczu."""
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
