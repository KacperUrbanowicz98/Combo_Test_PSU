# relay.py
"""
Przekaźnik stanowiska - moduł przełączający zasilacz między testem obciążenia
a Hi-Pot. Dwie komendy tekstowe (ENG -> Przekaźnik):

  load    zasilacz w torze testu obciążenia (OEL8512) - stan domyślny modułu
  hipot   zasilacz w torze Hi-Pot (Chroma)

Moduł sam wraca do 'load' po ~30 s bez aktywności (RELAY_REVERT_S). Dlatego:
  * Hi-Pot musi się zmieścić w tym oknie - hipot_time_left() ogranicza czas testu,
    inaczej przekaźnik przełączyłby się na OEL w trakcie wysokiego napięcia,
  * po Hi-Pot aplikacja czeka HV_DISCHARGE_S i dopiero wtedy wysyła 'load'.

Potwierdzenie: jeśli RELAY_ACK jest ustawione, komenda jest uznana dopiero po
linii zawierającej ten tekst; puste = brak potwierdzenia (tylko RELAY_SETTLE_MS).
Port: osobny albo ten sam co interlock (wtedy komendy idą przez InterlockMonitor).
"""
import queue
import threading
import time
from typing import Callable, Optional

from config import decode_term

MODE_LOAD, MODE_HIPOT = "LOAD", "HIPOT"


def same_port(a: str, b: str) -> bool:
    return (a or "").strip().upper() == (b or "").strip().upper() and bool((a or "").strip())


class RelayController:
    OPEN_DELAY = 1.5

    def __init__(self, cfg, interlock=None):
        self.cfg = cfg
        self.shared = interlock                 # InterlockMonitor przy wspólnym porcie
        self._ser = None
        self._cmd_lock = threading.Lock()
        self._rx: "queue.Queue[str]" = queue.Queue()
        self._mode: Optional[str] = None        # None = nieznany (przed pierwszą komendą)
        self._last_cmd = 0.0
        self.last_rx = ""                       # ostatnia odpowiedź modułu (podgląd w ENG)
        self._on_mode: Optional[Callable[[str], None]] = None
        if interlock is not None:
            interlock.set_on_line(self._on_line)

    # ------------------------------------------------------------------ #
    def _open_port(self):
        import serial
        return serial.Serial(self.cfg.RELAY_PORT, int(self.cfg.RELAY_BAUDRATE), timeout=0.05,
                             write_timeout=1, exclusive=True)

    def connect(self) -> bool:
        if self.shared is not None:
            return self.shared.connected
        try:
            self._ser = self._open_port()
            time.sleep(self.OPEN_DELAY)
            self._ser.reset_input_buffer()
            return True
        except Exception as e:
            print(f"[RELAY] Błąd połączenia: {e}")
            self._ser = None
            return False

    def disconnect(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    @property
    def connected(self) -> bool:
        if self.shared is not None:
            return self.shared.connected
        return self._ser is not None and self._ser.is_open

    def set_on_mode(self, cb: Callable[[str], None]):
        self._on_mode = cb

    # ------------------------------------------------------------------ #
    def _on_line(self, line: str):
        self.last_rx = line
        self._rx.put(line)

    def _send(self, text: str) -> bool:
        term = decode_term(self.cfg.RELAY_TERMINATOR)
        if self.shared is not None:
            return self.shared.write_line(text, term)
        if self._ser is None:
            return False
        try:
            self._ser.write((text + term).encode("ascii"))
            self._ser.flush()
            return True
        except Exception as e:
            print(f"[RELAY] Błąd zapisu '{text}': {e}")
            return False

    def _poll_own(self):
        """Własny port: odczyt dostępnych linii do kolejki odpowiedzi."""
        if self._ser is None:
            return
        try:
            raw = self._ser.readline()
        except Exception:
            return
        line = raw.decode("ascii", errors="ignore").strip()
        if line:
            self._on_line(line)

    def _wait_line(self, timeout: float) -> Optional[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.shared is None:
                self._poll_own()
            try:
                return self._rx.get(timeout=0.05)
            except queue.Empty:
                pass
        return None

    # ------------------------------------------------------------------ #
    def set_mode(self, mode: str):
        """Przełącza przekaźnik. Zwraca (ok, komunikat)."""
        mode = mode.upper()
        cmd = self.cfg.RELAY_CMD_HIPOT if mode == MODE_HIPOT else self.cfg.RELAY_CMD_LOAD
        with self._cmd_lock:
            while not self._rx.empty():
                try:
                    self._rx.get_nowait()
                except queue.Empty:
                    break
            if not self._send(cmd):
                return False, "Brak połączenia z przekaźnikiem"
            self._last_cmd = time.time()
            ack = (self.cfg.RELAY_ACK or "").strip().upper()
            if ack:
                deadline = time.time() + float(self.cfg.RELAY_TIMEOUT)
                while True:
                    line = self._wait_line(max(0.0, deadline - time.time()))
                    if line is None:
                        return False, f"Przekaźnik nie potwierdził '{cmd}' (oczekiwano '{self.cfg.RELAY_ACK}')"
                    if ack in line.upper():
                        break
                    if line.upper().startswith("ERR"):
                        return False, f"Przekaźnik odrzucił '{cmd}': {line}"
            else:
                self._wait_line(0.1)            # odpowiedź (jeśli jest) do podglądu w ENG
            time.sleep(max(0, int(self.cfg.RELAY_SETTLE_MS)) / 1000.0)
            if mode == MODE_HIPOT:
                self._last_cmd = time.time()    # okno powrotu liczone od ustalenia przekaźnika
            self._mode = mode
        if self._on_mode:
            try:
                self._on_mode(mode)
            except Exception:
                pass
        return True, ""

    def send_raw(self, text: str, wait: float = 1.0):
        """ENG: wysłanie dowolnej linii i zebranie odpowiedzi (rozpoznanie protokołu modułu)."""
        with self._cmd_lock:
            if not self._send(text):
                return False, []
            low = text.strip().lower()
            if low in (self.cfg.RELAY_CMD_LOAD.strip().lower(), self.cfg.RELAY_CMD_HIPOT.strip().lower()):
                self._mode = MODE_HIPOT if low == self.cfg.RELAY_CMD_HIPOT.strip().lower() else MODE_LOAD
                self._last_cmd = time.time()
            lines, deadline = [], time.time() + wait
            while time.time() < deadline:
                line = self._wait_line(max(0.0, deadline - time.time()))
                if line is None:
                    break
                lines.append(line)
            return True, lines

    @property
    def mode(self) -> Optional[str]:
        if self._mode == MODE_HIPOT and time.time() - self._last_cmd > float(self.cfg.RELAY_REVERT_S):
            return MODE_LOAD                    # moduł sam wrócił do 'load'
        return self._mode

    def hipot_time_left(self) -> float:
        """Ile sekund zostało, zanim moduł sam wróci do 'load' (0 gdy nie jest w HIPOT)."""
        if self._mode != MODE_HIPOT:
            return 0.0
        return max(0.0, float(self.cfg.RELAY_REVERT_S) - (time.time() - self._last_cmd))


def open_fixture(cfg, interlock_needed: bool, relay_needed: bool = True):
    """
    Łączy interlock i przekaźnik (wspólny port, jeśli ten sam COM).
    Zwraca (interlock | None, relay | None, [komunikaty błędów]).
    """
    msgs = []
    il = rl = None
    if cfg.SIMULATION:
        from simulators import SimInterlock as IL, SimRelay as RL
    else:
        from interlock import InterlockMonitor as IL
        RL = RelayController
    if interlock_needed:
        il = IL(cfg.INTERLOCK_PORT, int(cfg.INTERLOCK_BAUDRATE))
        if il.connect():
            il.start_monitoring()
        else:
            msgs.append(f"Interlock ({cfg.INTERLOCK_PORT}): brak połączenia")
            il = None
    if relay_needed:
        if same_port(cfg.RELAY_PORT, cfg.INTERLOCK_PORT) and interlock_needed:
            if il is not None:
                rl = RL(cfg, interlock=il)
            else:
                msgs.append("Przekaźnik: wspólny port z interlockiem niedostępny")
        else:
            rl = RL(cfg)
            if not rl.connect():
                msgs.append(f"Przekaźnik ({cfg.RELAY_PORT}): brak połączenia")
                rl = None
    return il, rl, msgs
