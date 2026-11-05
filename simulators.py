# simulators.py
"""
Symulatory sprzętu do pracy bez stanowiska (python main.py --sim):
  * SimChroma   — Chroma 19052 (API jak ChromaHiPotDevice),
  * SimFixture  — Arduino/STM: klapa + przekaźniki (prawdziwy InterlockMonitor
                  na fałszywym porcie szeregowym — testuje też protokół),
  * OEL8512 z zasilaczem — baseline simulator.py + model zasilacza na wyjściu.

Wymuszanie usterek (tylko symulacja) — końcówka SN:
  ...HF  Hi-Pot FAIL (prąd ponad HIGH)
  ...LV  obciążenie: napięcie za niskie
  ...LI  obciążenie: zasilacz nie daje prądu
  ...RF  przekaźniki nie potwierdzają PATH
"""
import queue
import random
import threading
import time

from hipot_device import ChromaHiPotDevice
from interlock import InterlockMonitor
from simulator import OEL85Simulator
from transport import SimTransport

SIM = {"lid": True, "path": "OFF", "v_nom": 12.0, "fault": None}
_sim_lock = threading.Lock()


def set_fault_from_serial(serial: str):
    s = (serial or "").upper()
    SIM["fault"] = next((f for f in ("HF", "LV", "LI", "RF") if s.endswith(f)), None)


def configure_psu(profile: dict):
    SIM["v_nom"] = float(profile.get("load_params", {}).get("v_nominal", 12.0) or 12.0)


def set_lid(closed: bool):
    SIM["lid"] = bool(closed)


# ====================================================================== #
# CHROMA 19052                                                            #
# ====================================================================== #
class SimChroma(ChromaHiPotDevice):

    def __init__(self, port="SIM", baudrate=9600):
        super().__init__(port, baudrate)
        self._p = {}
        self._mode = "AC"
        self._t0 = None
        self._stopped = False

    def connect(self) -> bool:
        self.connected = True
        self.idn = "Chroma,19052,SIM,1.00"
        return True

    def disconnect(self):
        self.connected = False

    @property
    def is_open(self) -> bool:
        return self.connected

    def query(self, command):
        return self.idn if command.strip() == "*IDN?" else "0,\"No error\""

    def send_command(self, command):
        pass

    def clear_steps(self) -> bool:
        return True

    def configure_test(self, step, mode, params) -> bool:
        self._mode, self._p = mode.upper(), dict(params)
        return True

    def _total(self):
        return float(self._p.get("ramp_time", 0)) + float(self._p.get("duration", 1)) \
            + float(self._p.get("fall_time", 0))

    def start_test(self):
        self._t0, self._stopped = time.time(), False
        return True, ""

    def stop_test(self) -> bool:
        self._stopped = True
        return True

    def _current_a(self):
        lo, hi = float(self._p.get("current_limit_low", 0)), float(self._p.get("current_limit_high", 0.0025))
        if SIM["fault"] == "HF":
            return hi * 1.3
        return lo + 0.35 * (hi - lo) if hi > lo else 0.0005

    def get_status(self) -> str:
        if self._t0 is None or self._stopped:
            return "STOP"
        if time.time() - self._t0 < self._total():
            return "TESTING"
        return "FAIL" if SIM["fault"] == "HF" else "PASS"

    def read_measurements(self):
        if self._t0 is None:
            return None
        t = time.time() - self._t0
        ramp = float(self._p.get("ramp_time", 0)) or 0.05
        v = float(self._p.get("voltage", 0)) * min(1.0, t / ramp) * random.uniform(0.995, 1.005)
        return {"step": "1", "mode": self._mode, "output_voltage": v,
                "measure_current": self._current_a() * min(1.0, t / ramp), "real_current": 0.0}

    def get_test_result(self):
        fail = SIM["fault"] == "HF"
        i_ma = self._current_a() * 1000.0
        jc = ("17" if self._mode == "AC" else "16") if fail else "116"
        return ("FAIL" if fail else "PASS",
                {"judgment_code": jc, "error_code": jc if fail else "", "comm_ok": True,
                 "output_voltage": float(self._p.get("voltage", 0)),
                 "measured_current_ma": i_ma, "real_current_ma": i_ma * 0.9})


# ====================================================================== #
# ARDUINO / STM                                                           #
# ====================================================================== #
class FakeFixtureSerial:
    """Emulacja oprogramowania Arduino/STM z fixture_controller.ino."""

    def __init__(self):
        self.is_open = True
        self._out = queue.Queue()
        self._last_status = 0.0
        self._last_cmd = time.time()
        self._buf = b""

    def _emit(self, line):
        self._out.put(line)

    def _watchdog(self):
        with _sim_lock:
            if not SIM["lid"] and SIM["path"] != "OFF":
                SIM["path"] = "OFF"
                self._emit("PATH OFF")
            if SIM["path"] != "OFF" and time.time() - self._last_cmd > 5.0:
                SIM["path"] = "OFF"
                self._emit("PATH OFF")

    def readline(self):
        deadline = time.time() + 0.3
        while time.time() < deadline:
            self._watchdog()
            if time.time() - self._last_status >= 0.3:
                self._last_status = time.time()
                return (("CLOSED" if SIM["lid"] else "OPEN") + "\n").encode()
            try:
                return (self._out.get(timeout=0.05) + "\n").encode()
            except queue.Empty:
                pass
        return b""

    def write(self, data: bytes):
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._handle(line.decode().strip().upper())
        return len(data)

    def _handle(self, cmd):
        self._last_cmd = time.time()
        if cmd == "PATH?":
            self._emit(f"PATH {SIM['path']}")
            return
        if cmd.startswith("PATH "):
            target = cmd.split(None, 1)[1]
            if target not in ("OFF", "HIPOT", "LOAD"):
                self._emit("ERR BAD_PATH")
                return
            if SIM["fault"] == "RF" and target != "OFF":
                return                                  # brak potwierdzenia
            if target != "OFF" and not SIM["lid"]:
                self._emit("ERR LID_OPEN")
                return

            def switch():
                time.sleep(0.12)                        # break-before-make + settle
                with _sim_lock:
                    SIM["path"] = target
                self._emit(f"OK PATH {target}")
            threading.Thread(target=switch, daemon=True).start()
            return
        self._emit("ERR UNKNOWN")

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def cancel_read(self):
        pass

    def close(self):
        self.is_open = False


class SimFixture(InterlockMonitor):
    OPEN_DELAY = 0.1

    def _open_port(self):
        return FakeFixtureSerial()


# ====================================================================== #
# OEL8512 + ZASILACZ                                                      #
# ====================================================================== #
class PsuOEL85Simulator(OEL85Simulator):
    """OEL8512 z podłączonym zasilaczem (tylko gdy przekaźniki są w torze LOAD)."""

    def _psu(self):
        if SIM["path"] != "LOAD":
            return 0.0, 0.0
        vnom = SIM["v_nom"]
        iset = float(self.state.get("CURR", 0.0)) if self.state.get("INP") and self.mode == "CURR" else 0.0
        von = float(self.state.get("VOLT:ON", 0.0))
        if SIM["fault"] == "LI" and iset > 0:
            return vnom * 0.55, iset * 0.4
        v = vnom + 0.06 - 0.05 * iset + random.gauss(0, 0.002)
        if SIM["fault"] == "LV":
            v -= vnom * 0.12
        i = iset if v > von else 0.0
        return max(0.0, v), i + (random.gauss(0, 0.0005) if i else 0.0)

    def _meas(self, c):
        v, i = self._psu()
        p = v * i
        if c == "MEAS:VOLT":
            return f"{v:.3f}"
        if c == "MEAS:CURR":
            return f"{i:.4f}"
        if c == "MEAS:POW":
            return f"{p:.3f}"
        if c == "MEAS:ALL:INFO":
            return f"{v:.3f},{i:.4f},{p:.3f},OFF,OFF,OFF"
        return None


class PsuSimTransport(SimTransport):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.sim = PsuOEL85Simulator()


def make_oel_transport(cfg):
    from config import decode_term
    return PsuSimTransport(tx_term=decode_term(cfg.OEL_TERMINATOR),
                           rx_term=decode_term(cfg.OEL_TERMINATOR),
                           timeout_s=0.3, inter_cmd_delay_ms=2)
