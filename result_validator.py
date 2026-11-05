# result_validator.py
"""
Niezależna ocena wyniku testu po stronie aplikacji.

Dlaczego: aplikacja ufała wyłącznie kodowi oceny z Chromy
(SAFEty:RESult:LAST:JUDG? == 116). W logach są raporty PASS z Vtm = 0.000 kV
i Im = 0.000 mA — urządzenie nie podało napięcia, a wynik i tak był PASS.
"LAST" zwraca ostatni zapamiętany wynik, więc gdy START nie wykonał testu
(albo status był odczytany zanim test ruszył), aplikacja zapisywała wynik
poprzedniej sztuki albo pusty PASS.

Zasada: FAIL z urządzenia jest zawsze FAIL. PASS z urządzenia jest
akceptowany tylko gdy:
  1. jest dowód, że test faktycznie się wykonał w tym cyklu
     (stan RUNNING z urządzenia albo zmierzone napięcie w trakcie testu),
  2. napięcie wyjściowe mieści się w tolerancji profilu,
  3. prąd mieści się w [LOW, HIGH] z profilu (LOW > 0 wykrywa
     niepodłączony zasilacz).
"""
from dataclasses import dataclass

PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"        # test nieważny — nie zapisywać, powtórzyć

# Napięcie zmierzone w trakcie testu, od którego uznajemy, że HV było podane.
HV_EVIDENCE_RATIO = 0.5

# Kody błędów dopisywane przez aplikację (opisy w logger.ERROR_CODES).
CODE_AC_HIGH = "17"
CODE_AC_LOW = "18"
CODE_DC_HIGH = "16"
CODE_DC_LOW = "APP-DCLO"
CODE_VOLTAGE = "APP-V"


@dataclass
class RunEvidence:
    """Co aplikacja zaobserwowała w trakcie TEGO cyklu testu."""
    saw_running: bool = False        # urządzenie odpowiedziało stanem "test trwa"
    peak_voltage: float = 0.0        # [V] maks. napięcie z pomiarów na żywo
    peak_current_ma: float = 0.0     # [mA] maks. prąd z pomiarów na żywo

    def hv_seen(self, setpoint_v: float) -> bool:
        return setpoint_v > 0 and self.peak_voltage >= setpoint_v * HV_EVIDENCE_RATIO

    def executed(self, setpoint_v: float) -> bool:
        return self.saw_running or self.hv_seen(setpoint_v)


@dataclass
class Verdict:
    result: str                 # PASS / FAIL / INVALID
    error_code: str = ""
    reason: str = ""            # komunikat dla operatora / konsoli
    voltage_v: float = 0.0      # wartości użyte do oceny (do raportu)
    current_ma: float = 0.0


def _valid(x) -> bool:
    try:
        return 0 < float(x) < 1e6
    except (TypeError, ValueError):
        return False


def validate(device_result: str, data: dict, params: dict,
             evidence: RunEvidence) -> Verdict:
    data = data or {}
    mode = str(params.get("mode", "AC")).upper()
    setpoint = float(params.get("voltage", 0) or 0)
    tol = float(params.get("voltage_tolerance", 0) or 0)
    low = float(params.get("current_limit_low", 0) or 0)       # [mA]
    high = float(params.get("current_limit_high", 0) or 0)     # [mA]

    # Wartości z wyniku Chromy; jeśli puste/overflow — z pomiarów TEGO cyklu.
    raw_v = data.get("output_voltage", 0.0)
    raw_i = data.get("measured_current_ma", 0.0)
    v = float(raw_v) if _valid(raw_v) else evidence.peak_voltage
    i = float(raw_i) if _valid(raw_i) else evidence.peak_current_ma

    if device_result not in (PASS, FAIL):
        return Verdict(INVALID, "", "Urządzenie nie zwróciło wyniku testu "
                       "(sprawdź kabel RS232 i port COM).", v, i)

    if device_result == FAIL:
        return Verdict(FAIL, str(data.get("error_code", "") or ""),
                       "FAIL zgłoszony przez urządzenie.", v, i)

    # --- urządzenie mówi PASS: weryfikacja --------------------------------
    if not evidence.executed(setpoint):
        return Verdict(INVALID, "",
                       "Urządzenie zgłosiło PASS, ale test nie został wykonany "
                       "(brak stanu RUNNING i brak napięcia w trakcie testu). "
                       "Wynik pochodzi z pamięci Chromy — NIE zapisano.", v, i)

    if mode == "IR":
        return Verdict(PASS, "", "", v, i)

    if v <= 0:
        return Verdict(INVALID, "",
                       "Urządzenie zgłosiło PASS bez pomiaru napięcia — "
                       "nie można ocenić testu. NIE zapisano.", v, i)

    if setpoint > 0 and tol > 0 and abs(v - setpoint) > tol:
        return Verdict(FAIL, CODE_VOLTAGE,
                       f"Napięcie {v:.0f} V poza tolerancją "
                       f"{setpoint:.0f} ± {tol:.0f} V.", v, i)

    if low > 0 and i < low:
        return Verdict(FAIL, CODE_AC_LOW if mode == "AC" else CODE_DC_LOW,
                       f"Prąd {i:.3f} mA poniżej limitu LOW {low:.3f} mA — "
                       f"zasilacz niepodłączony lub brak styku.", v, i)

    if high > 0 and i > high:
        return Verdict(FAIL, CODE_AC_HIGH if mode == "AC" else CODE_DC_HIGH,
                       f"Prąd {i:.3f} mA powyżej limitu HIGH {high:.3f} mA.", v, i)

    return Verdict(PASS, "", "", v, i)
