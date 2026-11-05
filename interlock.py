# interlock.py
"""
Arduino/STM stanowiska: monitor klapy (interlock) + przekaźniki torów testowych.

Bazuje na interlock.py z Hi-Pot PSU (auto-wznawianie połączenia, heartbeat,
synchroniczny odczyt stanu klapy). Dodane sterowanie torem DUT:

  PATH OFF    zasilacz odłączony od wszystkiego
  PATH HIPOT  wejście AC zasilacza (L+N) -> HV Chromy, wyjście DC -> RTN
  PATH LOAD   wejście AC zasilacza -> 230 V, wyjście DC -> OEL8512

PROTOKÓŁ (ASCII, linie zakończone \\n, domyślnie 9600 8N1):
  Arduino -> PC, cyklicznie (min. co 1 s, jak w Hi-Pot PSU):
      OPEN | CLOSED                stan klapy
  PC -> Arduino:
      PATH OFF|HIPOT|LOAD          przełączenie toru
      PATH?                        zapytanie o tor (także keepalive)
  Arduino -> PC, odpowiedź:
      OK PATH <tor>                przekaźniki przełączone i ustalone
      ERR <powód>                  np. ERR LID_OPEN, ERR BUSY
      PATH <tor>                   odpowiedź na PATH? albo zgłoszenie wymuszonego OFF
Wymagania po stronie Arduino/STM (patrz fixture_controller.ino):
  * break-before-make: zawsze najpierw wszystkie przekaźniki OFF, potem nowy tor,
  * HIPOT i LOAD tylko przy zamkniętej klapie; otwarcie klapy = natychmiast PATH OFF,
  * watchdog: brak komend z PC przez 5 s przy torze != OFF -> PATH OFF.
"""
import queue
import threading
import time
from typing import Callable, Optional

STATE_OPEN = False
STATE_CLOSED = True
STATE_UNKNOWN = None

PATH_OFF, PATH_HIPOT, PATH_LOAD = "OFF", "HIPOT", "LOAD"
PATHS = (PATH_OFF, PATH_HIPOT, PATH_LOAD)


class InterlockMonitor:

    STALE_AFTER = 5.0       # [s] brak danych = utrata interlocka
    RECONNECT_DELAY = 2.0   # [s]
    MAX_ERRORS = 3
    KEEPALIVE = 1.0         # [s] PATH? gdy tor != OFF (watchdog po stronie Arduino)
    OPEN_DELAY = 1.5        # [s] po otwarciu portu

    def __init__(self, port: str, baudrate: int = 9600, relays_enabled: bool = True):
        self.port = port
        self.baudrate = baudrate
        self.relays_enabled = relays_enabled
        self._serial = None
        self._thread = None
        self._running = False
        self._on_change_cb: Optional[Callable[[Optional[bool]], None]] = None
        self._on_path_cb: Optional[Callable[[str], None]] = None
        self._last_state: Optional[bool] = STATE_UNKNOWN
        self._last_rx = 0.0
        self._lock = threading.Lock()          # dostęp do obiektu portu
        self._write_lock = threading.Lock()
        self._replies: "queue.Queue[str]" = queue.Queue()
        self._path = PATH_OFF
        self._last_cmd = 0.0
        self._cmd_lock = threading.Lock()       # jedna komenda PATH naraz (sekwencja vs. STOP)
        # Keepalive (PATH? co 1 s) tylko gdy sekwencja testu go uzbroiła — inaczej
        # zgubione potwierdzenie OFF + keepalive trzymałoby przekaźniki mimo watchdoga.
        self.keepalive = False

    # ------------------------------------------------------------------ #
    # POŁĄCZENIE                                                          #
    # ------------------------------------------------------------------ #
    def _open_port(self):
        import serial
        return serial.Serial(self.port, self.baudrate, timeout=1,
                             write_timeout=2, exclusive=True)

    def connect(self) -> bool:
        try:
            with self._lock:
                self._close_locked()
                self._serial = self._open_port()
            time.sleep(self.OPEN_DELAY)   # Arduino Leonardo resetuje się przy otwarciu portu
            with self._lock:
                if self._serial:
                    self._serial.reset_input_buffer()
            self._last_rx = time.time()
            return True
        except Exception as e:
            print(f"[FIXTURE] Błąd połączenia: {e}")
            with self._lock:
                self._close_locked()
            return False

    def _close_locked(self):
        if self._serial is not None:
            try:
                if self._serial.is_open:
                    try:
                        self._serial.cancel_read()
                    except Exception:
                        pass
                    self._serial.close()
            except Exception:
                pass
            self._serial = None

    def disconnect(self):
        if self.relays_enabled and self.connected:
            try:
                self.set_path(PATH_OFF, timeout=1.0)
            except Exception:
                pass
        self._running = False
        with self._lock:
            self._close_locked()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        self._thread = None

    def set_on_change(self, callback: Callable[[Optional[bool]], None]):
        """Callback(closed) — True=zamknięta, False=otwarta, None=brak łączności."""
        self._on_change_cb = callback

    def set_on_path(self, callback: Callable[[str], None]):
        self._on_path_cb = callback

    def start_monitoring(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.disconnect()

    # ------------------------------------------------------------------ #
    # STAN                                                                #
    # ------------------------------------------------------------------ #
    @property
    def connected(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    @property
    def is_closed(self) -> Optional[bool]:
        """Ostatni znany stan klapy; None = nieznany -> NIE WOLNO startować."""
        if self._last_state is not None and (time.time() - self._last_rx) > self.STALE_AFTER:
            return STATE_UNKNOWN
        return self._last_state

    @property
    def path(self) -> str:
        return self._path

    def _emit(self, state: Optional[bool]):
        self._last_state = state
        cb = self._on_change_cb
        if cb:
            try:
                cb(state)
            except Exception as e:
                print(f"[FIXTURE] Błąd callbacku: {e}")

    def _set_path_state(self, path: str):
        if path != self._path:
            self._path = path
            cb = self._on_path_cb
            if cb:
                try:
                    cb(path)
                except Exception as e:
                    print(f"[FIXTURE] Błąd callbacku toru: {e}")

    # ------------------------------------------------------------------ #
    # PRZEKAŹNIKI                                                         #
    # ------------------------------------------------------------------ #
    def _write_line(self, text: str) -> bool:
        with self._lock:
            ser = self._serial
        if ser is None:
            return False
        try:
            with self._write_lock:
                ser.write((text + "\n").encode("ascii"))
                ser.flush()
            self._last_cmd = time.time()
            return True
        except Exception as e:
            print(f"[FIXTURE] Błąd zapisu '{text}': {e}")
            return False

    def set_path(self, path: str, timeout: float = 3.0):
        """
        Przełącza tor i czeka na potwierdzenie. Zwraca (ok, komunikat).
        OK tylko po odpowiedzi 'OK PATH <tor>' — brak potwierdzenia = błąd.
        """
        path = path.upper()
        if path not in PATHS:
            return False, f"Nieznany tor: {path}"
        if not self.relays_enabled:
            return False, "Sterowanie przekaźnikami wyłączone w konfiguracji"
        if path != PATH_OFF and self.is_closed is not True:
            return False, "Klapa nie jest zamknięta — tor nie zostanie załączony"
        with self._cmd_lock:
            return self._set_path_locked(path, timeout)

    def _set_path_locked(self, path: str, timeout: float):
        while not self._replies.empty():           # stare odpowiedzi
            try:
                self._replies.get_nowait()
            except queue.Empty:
                break
        if not self._write_line(f"PATH {path}"):
            return False, "Brak połączenia z Arduino/STM"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._replies.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if line == f"OK PATH {path}":
                self._set_path_state(path)
                return True, ""
            if line.startswith("ERR"):
                return False, f"Arduino/STM odrzuciło PATH {path}: {line}"
        return False, f"Brak potwierdzenia PATH {path} w {timeout:.1f} s"

    # ------------------------------------------------------------------ #
    # PĘTLA                                                               #
    # ------------------------------------------------------------------ #
    def _monitor_loop(self):
        last_forced = 0.0
        consecutive_errors = 0
        while self._running:
            with self._lock:
                ser = self._serial
            if ser is None or not ser.is_open:
                if self._last_state is not STATE_UNKNOWN:
                    self._handle_link_lost()
                if not self._try_reconnect():
                    continue
                consecutive_errors = 0
                continue

            # keepalive dla watchdoga po stronie Arduino
            if (self.relays_enabled and self.keepalive and self._path != PATH_OFF
                    and time.time() - self._last_cmd >= self.KEEPALIVE):
                self._write_line("PATH?")

            try:
                raw = ser.readline()
            except Exception as e:
                if not self._running:
                    break
                consecutive_errors += 1
                print(f"[FIXTURE] Błąd odczytu ({consecutive_errors}): {e}")
                if consecutive_errors >= self.MAX_ERRORS or not ser.is_open:
                    self._handle_link_lost()
                    consecutive_errors = 0
                else:
                    time.sleep(0.3)
                continue
            if not self._running:
                break
            now = time.time()
            if not raw:
                if self._last_state is not STATE_UNKNOWN and (now - self._last_rx) > self.STALE_AFTER:
                    print("[FIXTURE] Brak danych z Arduino/STM — utrata interlocka")
                    self._handle_link_lost()
                continue

            line = raw.decode("ascii", errors="ignore").strip().upper()
            consecutive_errors = 0
            if line in ("OPEN", "CLOSED"):
                self._last_rx = now
                closed = (line == "CLOSED")
                if not closed:
                    # Arduino samo zdejmuje tory przy otwartej klapie.
                    self._set_path_state(PATH_OFF)
                if closed != self._last_state or (now - last_forced) >= 1.0:
                    last_forced = now
                    self._emit(closed)
            elif line.startswith("OK ") or line.startswith("ERR"):
                self._last_rx = now
                self._replies.put(line)
            elif line.startswith("PATH "):
                self._last_rx = now
                p = line.split(None, 1)[1].strip()
                if p in PATHS:
                    self._set_path_state(p)

    def _handle_link_lost(self):
        with self._lock:
            self._close_locked()
        self._set_path_state(PATH_OFF)
        self._emit(STATE_UNKNOWN)

    def _try_reconnect(self) -> bool:
        deadline = time.time() + self.RECONNECT_DELAY
        while self._running and time.time() < deadline:
            time.sleep(0.1)
        if not self._running:
            return False
        print(f"[FIXTURE] Próba wznowienia połączenia z {self.port}...")
        if self.connect():
            print("[FIXTURE] Połączenie wznowione")
            return True
        return False
