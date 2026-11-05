# logger.py
"""
Zapis raportów testów — osobny plik na każdy test:
  * Hi-Pot:      SN_YYYYMMDDHHmmss.txt       format Chroma 19052 (bez zmian — odbiór IFS)
  * obciążenie:  SN_YYYYMMDDHHmmss_LOAD.txt  osobny katalog (LOAD_LOG_DIR)
"""
import os
from datetime import datetime

_DEFAULT_LOG_DIR = "logs"

ERROR_CODES = {
    "1":   "Hardware Fail",
    "2":   "GFI Trip",
    "4":   "ARC Fail",
    "8":   "Check Low Fail",
    "16":  "DC Mode High Fail",
    "17":  "AC Mode High Fail",
    "18":  "AC Mode Low Fail",
    "32":  "Ground Continuity Fail",
    "64":  "IR Low Fail",
    "116": "Pass",
    "128": "IR High Fail",
    "256": "ADV Over",
    "512": "ADI Over",
    # Kody nadawane przez kontrolę wyniku w aplikacji (result_validator.py)
    "APP-V":    "Output voltage out of tolerance (app check)",
    "APP-DCLO": "DC Mode Low Fail (app check)",
}


def _get_error_description(error_code: str) -> str:
    if not error_code:
        return ""
    return ERROR_CODES.get(str(error_code).strip(), f"Error code {error_code}")


def _safe_sn(serial: str) -> str:
    """SN trafia do nazwy pliku — znaki niedozwolone w NTFS zamieniane na '_'."""
    import re
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(serial).strip()) or "UNKNOWN"


def _unique_path(log_dir: str, serial: str, now: datetime, suffix: str = "") -> str:
    """
    Nazwa pliku SN_YYYYMMDDHHmmss.txt.
    Dwa testy tego samego SN w tej samej sekundzie nadpisywały się nawzajem
    — dokładany jest sufiks _2, _3, ... żeby żaden wynik nie zniknął.
    """
    base = f"{_safe_sn(serial)}_{now.strftime('%Y%m%d%H%M%S')}"
    path = os.path.join(log_dir, base + suffix + ".txt")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(log_dir, f"{base}_{counter}{suffix}.txt")
        counter += 1
    return path


def _write_atomic(filepath: str, lines) -> None:
    """Zapis przez plik tymczasowy — IFS nigdy nie zobaczy pliku uciętego w połowie."""
    tmp = filepath + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def save_report(operator: str, program: str, serial: str,
                mode: str, vtm: float, im: float,
                low: float, high: float,
                result: str, error_code: str = "",
                log_dir: str = _DEFAULT_LOG_DIR) -> str:
    """
    Zapisuje raport TXT w formacie Chroma 19052.
    Zwraca ścieżkę do pliku.

    UWAGA: w razie niepowodzenia metoda PODNOSI wyjątek. Wcześniej błąd
    zapisu (np. zerwany dysk sieciowy IFS) trafiał tylko na konsolę —
    operator widział PASS, a raport nigdy nie powstawał.

    Jednostki:
        vtm — [kV]
        im, low, high — [mA]
    """
    os.makedirs(log_dir, exist_ok=True)

    now          = datetime.now()
    datetime_str = now.strftime("%Y/%m/%d %H:%M:%S")
    error_desc   = _get_error_description(error_code)
    result_cap   = "Pass" if str(result).upper() == "PASS" else "Fail"

    lines = [
        "Chroma 19052 Test report",
        "",
        f"Program:\t{program}",
        f"S/N:\t\t{serial}",
        f"TIME:\t\t{datetime_str}",
        f"Total result:\t{result_cap}",
        "",
        "STEP:\t\t1",
        f"MODE:\t\t{mode}",
        "EXT Name:\t",
        f"Vtm:\t\t{vtm:.3f}\tKV",
        f"Im:\t\t{im:.3f}\tmA",
        f"Low:\t\t{low:.3f}\tmA",
        f"High:\t\t{high:.3f}\tmA",
        f"Result:\t\t{result_cap}",
        f"Error Code:\t{error_code}",
        "",
        f"Error Description: {error_desc}",
    ]

    filepath = _unique_path(log_dir, serial, now)

    # Zapis przez plik tymczasowy: IFS nigdy nie zobaczy pliku uciętego
    # w połowie, jeśli sieć padnie w trakcie zapisu.
    tmp = filepath + ".part"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise

    print(f"[LOG] Zapisano: {filepath}")
    return filepath


LOAD_ERROR_CODES = {
    "LOAD-VLO":  "Output voltage below limit",
    "LOAD-VHI":  "Output voltage above limit",
    "LOAD-I":    "Load current not reached (PSU cannot deliver set current)",
    "LOAD-PROT": "OEL8512 protection tripped (OVP/OCP/OPP)",
}


def save_load_report(program: str, serial: str, station: str, load_params: dict,
                     points: list, result: str, error_code: str = "",
                     log_dir: str = "logs_load", oel_idn: str = "") -> str:
    """
    Raport testu obciążenia (osobny plik, układ jak raport Chroma).
    points: lista dict(name, i_set, v, i, result, reason)
    """
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.now()
    result_cap = "Pass" if str(result).upper() == "PASS" else "Fail"
    ld = load_params or {}
    lines = [
        "OEL8512 Load Test report",
        "",
        f"Program:\t{program}",
        f"S/N:\t\t{serial}",
        f"TIME:\t\t{now.strftime('%Y/%m/%d %H:%M:%S')}",
        f"Total result:\t{result_cap}",
        f"Station:\t{station}",
        f"Load:\t\t{oel_idn}",
        "",
        f"Vnom:\t\t{float(ld.get('v_nominal', 0)):.3f}\tV",
        f"Vmin:\t\t{float(ld.get('v_min', 0)):.3f}\tV",
        f"Vmax:\t\t{float(ld.get('v_max', 0)):.3f}\tV",
    ]
    for n, p in enumerate(points, 1):
        lines += [
            "",
            f"STEP:\t\t{n}",
            f"MODE:\t\tCC {p.get('name', '')}",
            f"Iset:\t\t{float(p.get('i_set', 0)):.3f}\tA",
            f"Vm:\t\t{float(p.get('v', 0)):.3f}\tV",
            f"Im:\t\t{float(p.get('i', 0)):.3f}\tA",
            f"Result:\t\t{'Pass' if p.get('result') == 'PASS' else 'Fail'}",
        ]
    lines += ["", f"Error Code:\t{error_code}", "",
              f"Error Description: {LOAD_ERROR_CODES.get(error_code, '')}"]
    filepath = _unique_path(log_dir, serial, now, "_LOAD")
    _write_atomic(filepath, lines)
    print(f"[LOG] Zapisano: {filepath}")
    return filepath
