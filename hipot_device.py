# hipot_device.py
"""
Moduł komunikacji z urządzeniem Hi-Pot Chroma (RS232 / SCPI).

Zmiany względem wersji pierwotnej:
  * cały dostęp do portu jest serializowany blokadą — wcześniej wątek testu
    (odczyt pomiarów) i wątek GUI (przycisk STOP / otwarcie klapy) pisały do
    portu jednocześnie, co mieszało ramki i dawało losowe odpowiedzi;
  * nieudane connect() nie zostawia otwartego portu (wcześniej port zostawał
    zablokowany aż do restartu aplikacji);
  * kod oceny wyniku parsowany liczbowo, nie porównaniem tekstu;
  * licznik błędów komunikacji — pozwala odróżnić "test trwa" od "zerwany kabel".
"""
import threading
import time
from typing import Dict, Optional, Tuple

import serial

OVERFLOW = 9.0e+37     # Chroma zwraca tę wartość gdy brak pomiaru
PASS_JUDGMENT_CODE = 116

# Stany zwracane przez SAFEty:STATus? które oznaczają, że test się zakończył.
_FINISHED_TOKENS = ("STOP", "PASS", "FAIL", "READY", "OFF")

# Zwracane przez get_status(), gdy urządzenie w ogóle nie odpowiedziało.
# Wcześniej był to "TESTING" — przez co brak odpowiedzi wyglądał tak samo
# jak potwierdzony test w toku.
STATUS_NO_RESPONSE = "NO_RESPONSE"


def fmt_num(value) -> str:
    """
    Liczba dla SCPI bez notacji wykładniczej.
    str(0.01 / 1000) daje '1e-05' — nie każdy firmware Chromy to przyjmuje,
    a odrzucona komenda LIMit:LOW zostawiała w urządzeniu stary limit (np. 0).
    """
    s = f"{float(value):.7f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


class ChromaHiPotDevice:
    """Klasa obsługująca komunikację z testerem Hi-Pot Chroma przez RS232"""

    CMD_DELAY = 0.08          # przerwa po każdej komendzie (wymóg Chromy)
    READ_TIMEOUT = 2.0

    def __init__(self, port: str = "COM6", baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.serial = None
        self.connected = False
        self.idn = ""
        self.comm_errors = 0          # kolejne nieudane transakcje
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # POŁĄCZENIE                                                          #
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        with self._lock:
            self._force_close()
            try:
                self.serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.READ_TIMEOUT,
                    write_timeout=2,
                )
                time.sleep(0.5)
                self.serial.reset_input_buffer()
                self.serial.reset_output_buffer()
                self.connected = True
                self.comm_errors = 0

                response = self.query("*IDN?")
                if not response:
                    print("[HIPOT] Brak odpowiedzi na *IDN? — rozłączam")
                    self._force_close()
                    return False

                self.idn = response
                print(f"[HIPOT] Połączono z: {response}")

                # Odblokowanie klawiatury (keylock)
                self.send_command("SYST:KLOC OFF")
                time.sleep(0.3)
                err = self.query("SYST:ERR?")
                print(f"[HIPOT] SYST:KLOC OFF → '{err}'")
                return True

            except Exception as e:
                print(f"[HIPOT] Błąd połączenia: {e}")
                self._force_close()
                return False

    def _force_close(self):
        """Zamyka port bezwarunkowo — bez wysyłania czegokolwiek."""
        if self.serial is not None:
            try:
                if self.serial.is_open:
                    self.serial.close()
            except Exception:
                pass
        self.serial = None
        self.connected = False

    def disconnect(self):
        """Bezpieczne rozłączenie: najpierw wyłącz wysokie napięcie."""
        with self._lock:
            if self.serial is not None and self.connected:
                try:
                    if self.serial.is_open:
                        self.stop_test()
                except Exception:
                    pass
            self._force_close()

    @property
    def is_open(self) -> bool:
        s = self.serial
        return bool(s is not None and s.is_open and self.connected)

    # ------------------------------------------------------------------ #
    # NISKI POZIOM                                                        #
    # ------------------------------------------------------------------ #
    def send_command(self, command: str):
        with self._lock:
            if not self.is_open:
                raise IOError("Urządzenie Hi-Pot nie jest połączone!")
            if not command.endswith("\n"):
                command += "\n"
            self.serial.write(command.encode("ascii"))
            time.sleep(self.CMD_DELAY)

    def read_response(self, timeout: Optional[float] = None) -> Optional[str]:
        timeout = self.READ_TIMEOUT if timeout is None else timeout
        with self._lock:
            if not self.is_open:
                return None
            start = time.time()
            response = ""
            while (time.time() - start) < timeout:
                try:
                    waiting = self.serial.in_waiting
                except Exception as e:
                    print(f"[HIPOT] Port przerwany przy odczycie: {e}")
                    return None
                if waiting > 0:
                    chunk = self.serial.read(waiting).decode("ascii", errors="ignore")
                    response += chunk
                    if "\n" in response or "\r" in response:
                        break
                time.sleep(0.02)
            return response.strip() if response.strip() else None

    def query(self, command: str) -> Optional[str]:
        """Jedna transakcja zapytanie→odpowiedź, atomowa względem innych wątków."""
        with self._lock:
            try:
                self.serial.reset_input_buffer()
            except Exception:
                pass
            try:
                self.send_command(command)
            except Exception as e:
                self.comm_errors += 1
                print(f"[HIPOT] Błąd wysyłki '{command}': {e}")
                return None
            resp = self.read_response()
            if resp is None:
                self.comm_errors += 1
            else:
                self.comm_errors = 0
            return resp

    @staticmethod
    def _parse(response: Optional[str]) -> list:
        """Parsuje odpowiedź Chromy — separator może być ; lub ,"""
        if not response:
            return []
        sep = ";" if ";" in response else ","
        return [p.strip() for p in response.split(sep)]

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            f = float(str(value).strip())
        except (TypeError, ValueError):
            return default
        if f != f or abs(f) >= OVERFLOW:      # NaN albo overflow Chromy
            return default
        return f

    # ------------------------------------------------------------------ #
    # KONFIGURACJA KROKU                                                  #
    # ------------------------------------------------------------------ #
    def configure_test(self, step: int, mode: str, params: Dict) -> bool:
        """Zwraca True gdy wszystkie komendy poszły bez wyjątku."""
        mode = (mode or "AC").upper()
        try:
            # Opróżnij kolejkę błędów, żeby SYST:ERR? po konfiguracji
            # pokazywał błędy TEJ konfiguracji, a nie stare.
            self._drain_errors()

            if mode in ("AC", "DC"):
                self.send_command(f"SAFEty:STEP{step}:{mode}:LEVel {fmt_num(params.get('voltage', 1000))}")
                self.send_command(f"SAFEty:STEP{step}:{mode}:LIMit:HIGH {fmt_num(params.get('current_limit_high', 0.005))}")
                self.send_command(f"SAFEty:STEP{step}:{mode}:LIMit:LOW {fmt_num(params.get('current_limit_low', 0.0))}")
                self.send_command(f"SAFEty:STEP{step}:{mode}:TIME:TEST {fmt_num(params.get('duration', 60))}")
                self.send_command(f"SAFEty:STEP{step}:{mode}:TIME:RAMP {fmt_num(params.get('ramp_time', 0))}")
                self.send_command(f"SAFEty:STEP{step}:{mode}:TIME:FALL {fmt_num(params.get('fall_time', 0))}")
            elif mode == "IR":
                self.send_command(f"SAFEty:STEP{step}:IR:LEVel {fmt_num(params.get('voltage', 500))}")
                self.send_command(f"SAFEty:STEP{step}:IR:LIMit {fmt_num(params.get('resistance_limit', 300000))}")
                self.send_command(f"SAFEty:STEP{step}:IR:TIME:TEST {fmt_num(params.get('duration', 3))}")
            else:
                raise ValueError(f"Nieobsługiwany tryb testu: {mode}")

            # Sprawdzamy CAŁĄ kolejkę błędów — wcześniej czytany był tylko
            # pierwszy wpis, więc odrzucony LIMit:LOW mógł przejść niezauważony.
            errors = self._drain_errors()
            if errors:
                print(f"[HIPOT] Konfiguracja zgłosiła błędy: {errors}")
                return False

            if mode in ("AC", "DC") and not self._verify_limits(step, mode, params):
                return False
            return True
        except Exception as e:
            print(f"[HIPOT] Błąd konfiguracji kroku: {e}")
            return False

    def _drain_errors(self, max_reads: int = 10) -> list:
        """Czyta SYST:ERR? aż do '0,No error'. Zwraca listę błędów."""
        errors = []
        for _ in range(max_reads):
            err = self.query("SYST:ERR?")
            if err is None or self._error_is_ok(err):
                break
            errors.append(err)
        return errors

    def _verify_limits(self, step: int, mode: str, params: Dict) -> bool:
        """
        Odczyt zwrotny napięcia i limitów prądu z Chromy.
        Rozbieżność = konfiguracja nieudana (test zostanie zablokowany).
        Brak odpowiedzi na zapytanie (firmware go nie obsługuje) — tylko
        ostrzeżenie w konsoli; wtedy chroni nas kontrola wyniku w aplikacji.
        """
        checks = (
            ("LEVel",      params.get("voltage", 0)),
            ("LIMit:HIGH", params.get("current_limit_high", 0)),
            ("LIMit:LOW",  params.get("current_limit_low", 0)),
        )
        ok = True
        for key, expected in checks:
            resp = self.query(f"SAFEty:STEP{step}:{mode}:{key}?")
            parts = self._parse(resp)
            if not parts:
                print(f"[HIPOT] Odczyt zwrotny {key}: brak odpowiedzi — pomijam")
                continue
            try:
                actual = float(parts[0])
            except ValueError:
                print(f"[HIPOT] Odczyt zwrotny {key}: nieczytelne '{resp}' — pomijam")
                continue
            expected = float(expected)
            # Firmware może odpowiadać w innej jednostce (V/kV, A/mA) —
            # wartość przeskalowana o 1000 też potwierdza poprawne ustawienie.
            matched = any(
                abs(actual - expected * k) <= max(abs(expected * k) * 0.02, 1e-7)
                for k in (1.0, 1000.0, 0.001))
            if not matched:
                print(f"[HIPOT] Odczyt zwrotny {key}: ustawiono {expected}, "
                      f"urządzenie ma {actual} — BŁĄD KONFIGURACJI")
                ok = False
            else:
                print(f"[HIPOT] Odczyt zwrotny {key}: {actual} OK")
        return ok

    @staticmethod
    def _error_is_ok(err: str) -> bool:
        """SCPI: '0,"No error"' oznacza brak błędu."""
        first = str(err).split(",")[0].strip()
        try:
            return int(float(first)) == 0
        except (TypeError, ValueError):
            return True     # nieznany format — nie blokuj pracy

    # ------------------------------------------------------------------ #
    # STEROWANIE TESTEM                                                   #
    # ------------------------------------------------------------------ #
    def start_test(self) -> Tuple[bool, str]:
        """
        Zwraca (ok, komunikat). Wcześniej metoda zwracała samo True/False,
        a wynik i tak był ignorowany przez wywołującego — test "startował"
        nawet gdy Chroma odrzuciła komendę i GUI wisiało do timeoutu.
        """
        try:
            with self._lock:
                self.send_command("SYST:KLOC OFF")
                self.send_command("SAFEty:STARt")
                time.sleep(0.3)
                err = self.query("SYST:ERR?")
            print(f"[HIPOT] START → SYST:ERR? = '{err}'")
            if err and "-203" in str(err):
                return False, "Urządzenie zablokowane (błąd -203) — sprawdź klapę/keylock."
            if err and not self._error_is_ok(err):
                return False, f"Urządzenie odrzuciło START: {err}"
            return True, ""
        except Exception as e:
            print(f"[HIPOT] Błąd rozpoczęcia testu: {e}")
            return False, str(e)

    def stop_test(self) -> bool:
        try:
            with self._lock:
                if not self.is_open:
                    return False
                self.send_command("SAFEty:STOP")
            return True
        except Exception as e:
            print(f"[HIPOT] Błąd zatrzymania testu: {e}")
            return False

    def get_status(self) -> str:
        """
        Zwraca znormalizowany stan.
        Brak odpowiedzi → STATUS_NO_RESPONSE (nie kończy testu, ale też NIE
        jest dowodem, że test trwa). Zerwaną komunikację wykrywa comm_errors.
        Odpowiedź spoza listy stanów końcowych → 'TESTING' (urządzenie
        odpowiedziało czymś innym niż STOP/PASS/FAIL, więc test trwa).
        """
        try:
            response = self.query("SAFEty:STATus?")
            if not response:
                return STATUS_NO_RESPONSE
            s = response.strip().upper()
            for token in _FINISHED_TOKENS + ("TESTING", "RUNNING", "WAIT"):
                if token in s:
                    return token
            print(f"[HIPOT] Nieznany status: '{response}' — traktuję jako TESTING")
            return "TESTING"
        except Exception as e:
            print(f"[HIPOT] Błąd pobierania statusu: {e}")
            return STATUS_NO_RESPONSE

    @staticmethod
    def status_is_running(status: str) -> bool:
        """Potwierdzona odpowiedź urządzenia, że test trwa."""
        if not status or status == STATUS_NO_RESPONSE:
            return False
        return not ChromaHiPotDevice.status_is_finished(status)

    @staticmethod
    def status_is_finished(status: str) -> bool:
        """
        Poprzednio kod porównywał status == 'STOPPED'. Chroma zwraca 'STOP',
        więc warunek nigdy się nie spełniał i każdy test kończył się dopiero
        awaryjnym limitem czasu (total_time + 5 s).
        """
        return any(tok in (status or "").upper() for tok in _FINISHED_TOKENS)

    # ------------------------------------------------------------------ #
    # POMIARY I WYNIK                                                     #
    # ------------------------------------------------------------------ #
    def read_measurements(self) -> Optional[Dict]:
        try:
            response = self.query("SAFEty:FETCh? STEP,MODE,OMET,MMET,RMET")
            parts = self._parse(response)
            if len(parts) >= 5:
                return {
                    "step":            parts[0],
                    "mode":            parts[1],
                    "output_voltage":  self._to_float(parts[2]),   # [V]
                    "measure_current": self._to_float(parts[3]),   # [A]
                    "real_current":    self._to_float(parts[4]),   # [A]
                }
        except Exception as e:
            print(f"[HIPOT] Błąd odczytu pomiarów: {e}")
        return None

    def get_test_result(self) -> Tuple[str, Dict]:
        """
        Zwraca ('PASS'|'FAIL'|'UNKNOWN', dane).
        UNKNOWN = urządzenie nie odpowiedziało. Wcześniej taki przypadek
        był raportowany i logowany jako FAIL, czyli usterka łącza RS232
        wyglądała jak wadliwy zasilacz.
        Prądy zwracane są w mA (jedno, jednoznaczne miejsce przeliczenia).
        """
        try:
            judgment_code = self.query("SAFEty:RESult:LAST:JUDG?")
            output_v      = self.query("SAFEty:RESult:LAST:OMET?")
            measured_i    = self.query("SAFEty:RESult:LAST:MMET?")
            real_i        = self.query("SAFEty:RESult:LAST:RMET?")

            print(f"[HIPOT][WYNIK] judg='{judgment_code}' omet='{output_v}' "
                  f"mmet='{measured_i}' rmet='{real_i}'")

            if judgment_code is None:
                return "UNKNOWN", {"judgment_code": "", "error_code": "",
                                   "comm_ok": False,
                                   "output_voltage": 0.0,
                                   "measured_current_ma": 0.0,
                                   "real_current_ma": 0.0}

            jc_raw = judgment_code.strip()
            try:
                jc_num = int(float(self._parse(jc_raw)[0]))
                jc = str(jc_num)
            except (ValueError, IndexError):
                jc, jc_num = jc_raw, None

            if jc_num is None:
                result = "UNKNOWN"
            elif jc_num == PASS_JUDGMENT_CODE:
                result = "PASS"
            else:
                result = "FAIL"

            data = {
                "judgment_code": jc,
                "error_code": "" if result == "PASS" else jc,
                "comm_ok": True,
                "output_voltage":      self._to_float(output_v),          # [V]
                "measured_current_ma": self._to_float(measured_i) * 1000, # [A]→[mA]
                "real_current_ma":     self._to_float(real_i) * 1000,     # [A]→[mA]
            }
            return result, data

        except Exception as e:
            print(f"[HIPOT] Błąd pobierania wyniku: {e}")
            return "UNKNOWN", {"judgment_code": "", "error_code": "",
                               "comm_ok": False,
                               "output_voltage": 0.0,
                               "measured_current_ma": 0.0,
                               "real_current_ma": 0.0}

    def clear_steps(self) -> bool:
        try:
            num_steps = self.query("SAFEty:SNUMber?")
            parts = self._parse(num_steps)
            if not parts:
                return False
            n = int(float(parts[0]))
            for i in range(n, 0, -1):
                self.send_command(f"SAFEty:STEP{i}:DELete")
            return True
        except Exception as e:
            print(f"[HIPOT] Błąd czyszczenia kroków: {e}")
            return False

    # ------------------------------------------------------------------ #
    # DEBUG                                                               #
    # ------------------------------------------------------------------ #
    def debug_test(self, port: str = "COM6"):
        print("=" * 55)
        print("DEBUG HI-POT")
        print("=" * 55)

        print(f"\n[1] Łączenie z {port}...")
        self.port = port
        if not self.connect():
            print("    STOP — brak połączenia")
            return

        print(f"\n[2] *IDN?           '{self.query('*IDN?')}'")
        print(f"[3] SYST:ERR?       '{self.query('SYST:ERR?')}'")
        print(f"[4] SAFEty:SNUMber? '{self.query('SAFEty:SNUMber?')}'")

        print("\n[5] clear_steps()")
        self.clear_steps()
        print(f"    SNUMber? po czyszczeniu: '{self.query('SAFEty:SNUMber?')}'")

        print("\n[6] Konfiguracja...")
        ok = self.configure_test(1, "AC", {
            "voltage": 3000, "current_limit_high": 0.0025,
            "current_limit_low": 0.0003, "duration": 1.0,
            "ramp_time": 0.5, "fall_time": 0.5})
        print(f"    configure_test() = {ok}")

        print("\n[7] start_test()")
        ok, msg = self.start_test()
        print(f"    start_test() = {ok} {msg}")
        if not ok:
            self.disconnect()
            return

        print("\n[8] Monitorowanie przez 15 s...")
        result = "UNKNOWN"
        for i in range(30):
            time.sleep(0.5)
            stat = self.get_status()
            meas = self.read_measurements()
            print(f"    t={i * 0.5:.1f}s  stat='{stat}'  meas={meas}")
            if i > 0 and self.status_is_finished(stat):
                print("    >>> ZAKOŃCZONY")
                break

        print("\n[9] Wynik...")
        result, data = self.get_test_result()
        print(f"    wynik: {result}\n    dane:  {data}")

        self.disconnect()
        print("\n" + "=" * 55)
        print(f"KOŃCOWY WYNIK: {result}")
        print("=" * 55)


if __name__ == "__main__":
    ChromaHiPotDevice().debug_test("COM6")
