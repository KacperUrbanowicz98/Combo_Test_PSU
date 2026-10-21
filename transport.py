"""Warstwa komunikacji: RS232/USB-VCP (pyserial), VISA (opcjonalnie pyvisa), symulator.

v1.1:
  - write() nasłuchuje przez czas przerwy po komendzie i zwraca ewentualną niezamówioną
    odpowiedź (np. 'ERR' po odrzuconym zapisie),
  - query() pomija puste linie (np. '\\r\\n' przed właściwą odpowiedzią); jeśli przyszły
    tylko puste linie, zwraca '' (a nie None),
  - last_raw: surowe bajty ostatniej odpowiedzi (podgląd HEX w Panelu ENG).
"""
import logging
import threading
import time

log = logging.getLogger(__name__)


class TransportError(Exception):
    pass


def hexdump(b):
    return " ".join(f"{x:02X}" for x in (b or b""))


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
        self.last_raw = b""

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

    def _read_line(self, timeout_s):
        raise NotImplementedError

    def _listen(self, dur_s):
        time.sleep(dur_s)
        return None

    def _flush_input(self):
        pass

    # wspólne
    def write(self, cmd):
        """Zapis. Zwraca niezamówioną odpowiedź odebraną w oknie przerwy (np. 'ERR') albo None."""
        with self.lock:
            if not self.is_open:
                raise TransportError("Port nie jest otwarty")
            self._flush_input()
            self._traffic("TX", cmd)
            self._write_raw(cmd + self.tx_term)
            extra = self._listen(self.delay_s)
            if extra:
                self._traffic("RX", f"{extra}   (niezamówione po zapisie)")
            return extra

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
            elif resp == "":
                self._traffic("RX", f"<pusta linia>  HEX: {hexdump(self.last_raw)}")
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
                parity=self.parity, stopbits=self.stopbits, timeout=0.02,
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

    def _read_chunk(self):
        try:
            data = self.ser.read(self.ser.in_waiting or 1)
        except Exception as e:
            raise TransportError(f"Błąd odczytu: {e}")
        if b"\x00" in data:   # OEL8512 wysyła 0x00 przy restarcie po *RST
            log.debug("Odrzucono %d bajtów 0x00", data.count(b"\x00"))
            data = data.replace(b"\x00", b"")
        return data

    def _listen(self, dur_s):
        buf = b""
        deadline = time.monotonic() + dur_s
        while time.monotonic() < deadline:
            buf += self._read_chunk()
        if not buf:
            return None
        self.last_raw = buf
        txt = " | ".join(x.strip() for x in buf.decode("ascii", errors="replace").splitlines() if x.strip())
        return txt or None

    def _read_line(self, timeout_s):
        term = self.rx_term.encode("ascii")
        buf = b""
        raw = b""
        got_empty = False
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            chunk = self._read_chunk()
            if not chunk:
                continue
            buf += chunk
            raw += chunk
            while term in buf:
                line, buf = buf.split(term, 1)
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    self.last_raw = raw
                    return text
                got_empty = True   # pusta linia - czekaj dalej na właściwą odpowiedź
        self.last_raw = raw
        if buf.strip():
            return buf.decode("ascii", errors="replace").strip() + "  [!brak terminatora]"
        return "" if got_empty else None


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
            raw = self.inst.read_raw()
            self.last_raw = raw
            return raw.decode("ascii", errors="replace").strip()
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

    def _listen(self, dur_s):
        time.sleep(dur_s)
        r, self._pending = self._pending, None
        return r

    def _read_line(self, timeout_s):
        if self._pending is None:
            time.sleep(min(timeout_s, 0.15))  # skrócony timeout w symulatorze
            return None
        r, self._pending = self._pending, None
        self.last_raw = (r + self.rx_term).encode("ascii")
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
    sb = conn_cfg["stopbits"]
    sb = float(sb) if float(sb) == 1.5 else int(float(sb))
    return SerialTransport(conn_cfg["port"], int(conn_cfg["baudrate"]), int(conn_cfg["bytesize"]),
                           conn_cfg["parity"], sb, **kw)
