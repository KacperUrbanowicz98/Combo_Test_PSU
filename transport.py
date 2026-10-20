"""Warstwa komunikacji: RS232/USB-VCP (pyserial), VISA (opcjonalnie pyvisa), symulator."""
import logging
import threading
import time

log = logging.getLogger(__name__)


class TransportError(Exception):
    pass


class BaseTransport:
    """Wspólny interfejs. query() zwraca str albo None przy braku odpowiedzi (timeout)."""
    name = "base"

    def __init__(self, tx_term="\n", rx_term="\n", timeout_s=1.0, inter_cmd_delay_ms=60):
        self.tx_term = tx_term
        self.rx_term = rx_term
        self.timeout_s = timeout_s
        self.delay_s = inter_cmd_delay_ms / 1000.0
        self.lock = threading.RLock()
        self.traffic_cb = None   # callback(direction, text) -> log TX/RX w GUI
        self.is_open = False

    def _traffic(self, direction, text):
        log.debug("%s %s", direction, text)
        if self.traffic_cb:
            try:
                self.traffic_cb(direction, text)
            except Exception:
                pass

    # do nadpisania
    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def _write_raw(self, data):
        raise NotImplementedError

    def _read_line(self):
        raise NotImplementedError

    def _flush_input(self):
        pass

    # wspólne
    def write(self, cmd):
        with self.lock:
            if not self.is_open:
                raise TransportError("Port nie jest otwarty")
            self._flush_input()
            self._traffic("TX", cmd)
            self._write_raw(cmd + self.tx_term)
            time.sleep(self.delay_s)

    def query(self, cmd, timeout_s=None):
        """Wysyła zapytanie i czeka na linię odpowiedzi. None = brak odpowiedzi."""
        with self.lock:
            if not self.is_open:
                raise TransportError("Port nie jest otwarty")
            self._flush_input()
            self._traffic("TX", cmd)
            self._write_raw(cmd + self.tx_term)
            resp = self._read_line(timeout_s or self.timeout_s)
            if resp is None:
                self._traffic("RX", "<brak odpowiedzi - timeout>")
            else:
                self._traffic("RX", resp)
            return resp


class SerialTransport(BaseTransport):
    name = "serial"

    def __init__(self, port, baudrate=115200, bytesize=8, parity="N", stopbits=1, **kw):
        super().__init__(**kw)
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.ser = None

    def open(self):
        import serial
        try:
            self.ser = serial.Serial(
                port=self.port, baudrate=self.baudrate, bytesize=self.bytesize,
                parity=self.parity, stopbits=self.stopbits, timeout=0.05,
                write_timeout=1.0)
        except Exception as e:
            raise TransportError(f"Nie można otworzyć {self.port}: {e}")
        self.is_open = True
        log.info("Otwarto %s @ %s", self.port, self.baudrate)

    def close(self):
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.ser = None
            self.is_open = False

    def _flush_input(self):
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

    def _write_raw(self, data):
        try:
            self.ser.write(data.encode("ascii", errors="replace"))
            self.ser.flush()
        except Exception as e:
            raise TransportError(f"Błąd zapisu: {e}")

    def _read_line(self, timeout_s):
        term = self.rx_term.encode("ascii")
        buf = b""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                chunk = self.ser.read(self.ser.in_waiting or 1)
            except Exception as e:
                raise TransportError(f"Błąd odczytu: {e}")
            if chunk:
                buf += chunk
                if term in buf:
                    line = buf.split(term, 1)[0]
                    return line.decode("ascii", errors="replace").strip()
        if buf:  # coś przyszło, ale bez terminatora
            return buf.decode("ascii", errors="replace").strip() + "  [!brak terminatora]"
        return None


class VisaTransport(BaseTransport):
    """Gdyby port USB zgłosił się jako USBTMC zamiast wirtualnego COM."""
    name = "visa"

    def __init__(self, resource, **kw):
        super().__init__(**kw)
        self.resource = resource
        self.inst = None

    def open(self):
        try:
            import pyvisa
        except ImportError:
            raise TransportError("Brak pyvisa (pip install pyvisa pyvisa-py)")
        try:
            rm = pyvisa.ResourceManager()
            self.inst = rm.open_resource(self.resource)
            self.inst.write_termination = self.tx_term
            self.inst.read_termination = self.rx_term
            self.inst.timeout = int(self.timeout_s * 1000)
        except Exception as e:
            raise TransportError(f"VISA: {e}")
        self.is_open = True

    def close(self):
        with self.lock:
            if self.inst:
                try:
                    self.inst.close()
                except Exception:
                    pass
            self.inst = None
            self.is_open = False

    def _write_raw(self, data):
        self.inst.write_raw(data.encode("ascii"))

    def _read_line(self, timeout_s):
        try:
            self.inst.timeout = int(timeout_s * 1000)
            return self.inst.read().strip()
        except Exception:
            return None


class SimTransport(BaseTransport):
    name = "sim"

    def __init__(self, **kw):
        super().__init__(**kw)
        from simulator import OEL85Simulator
        self.sim = OEL85Simulator()
        self._pending = None

    def open(self):
        self.is_open = True
        log.info("Symulator OEL8512 aktywny")

    def close(self):
        self.is_open = False

    def _write_raw(self, data):
        time.sleep(0.003)
        self._pending = self.sim.handle(data.strip())

    def _read_line(self, timeout_s):
        if self._pending is None:
            time.sleep(min(timeout_s, 0.15))  # skrócony timeout w symulatorze
            return None
        r, self._pending = self._pending, None
        return r


def list_serial_ports():
    """[(device, opis, 'VID:PID'|'')]"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        vp = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else ""
        out.append((p.device, p.description or "", vp))
    return out


def build_transport(conn_cfg):
    from config import decode_term
    kw = dict(
        tx_term=decode_term(conn_cfg["tx_terminator"]),
        rx_term=decode_term(conn_cfg["rx_terminator"]),
        timeout_s=float(conn_cfg["timeout_s"]),
        inter_cmd_delay_ms=int(conn_cfg["inter_cmd_delay_ms"]),
    )
    t = conn_cfg["transport"]
    if t == "sim":
        kw["inter_cmd_delay_ms"] = min(kw["inter_cmd_delay_ms"], 5)
        return SimTransport(**kw)
    if t == "visa":
        return VisaTransport(conn_cfg["visa_resource"], **kw)
    return SerialTransport(conn_cfg["port"], int(conn_cfg["baudrate"]), int(conn_cfg["bytesize"]),
                           conn_cfg["parity"], float(conn_cfg["stopbits"]) if conn_cfg["stopbits"] == 1.5
                           else int(conn_cfg["stopbits"]), **kw)
