# models.py
"""
Wspólna baza profili testowych: Hi-Pot (Chroma 19052) + test obciążenia (OEL8512).

Źródła:
  * test_params - eksport profili z aplikacji Hi-Pot PSU (models.json),
  * load_params - tabele parametrów obciążenia PSU (Min/Max Load, okno napięcia).
Profile tylko dla PN, które są w obu źródłach (11 PN). Format PN:
PSU-000006-00 (zawsze 6 cyfr) - stare klucze PSU-00006-00 są migrowane.

load_params:
  enabled          test obciążenia wykonywany (False = sam Hi-Pot)
  v_nominal        napięcie nominalne [V] (informacyjnie / do raportu)
  v_min, v_max     okno napięcia wyjściowego [V] - kryterium PASS w każdym punkcie
  i_min, i_max     punkty obciążenia CC [A]; i_min = 0 -> pomiar bez obciążenia (INP 0)
  power_on_delay   [s] od przełączenia toru na LOAD (230 V na zasilacz) do pierwszego pomiaru
  settle_time      [s] od ustawienia punktu do pomiaru
  samples          liczba odczytów uśrednianych w punkcie
  i_tol_pct/i_tol_abs  tolerancja pobieranego prądu względem nastawy (OEL faktycznie obciąża)
"""
import copy
import re

from settings_manager import SettingsManager

_sm = SettingsManager()


def _hp(mode="AC", voltage=3000, low=0.3, high=2.5, ramp=0.0, test=1.0, fall=0.0, arc=0.0):
    return {"mode": mode, "voltage": voltage, "voltage_tolerance": 50,
            "current_limit_high": high, "current_limit_low": low,
            "ramp_time": ramp, "test_time": test, "fall_time": fall,
            "frequency": 50, "arc_detection": arc}


def _ld(v_nom, v_min, v_max, i_min, i_max):
    return {"enabled": True, "v_nominal": v_nom, "v_min": v_min, "v_max": v_max,
            "i_min": i_min, "i_max": i_max,
            "power_on_delay": 2.0, "settle_time": 1.0, "samples": 5,
            "i_tol_pct": 3.0, "i_tol_abs": 0.02}


# Na jakie urządzenia wchodzi dany zasilacz (tabela PN -> modele).
_DEVICES = {
    "PSU-000003-00": "ER115UK, ER115UK_ROI",
    "PSU-000005-00": "ESi240, ES240UK",
    "PSU-000006-00": "SR204UK, SR203UKIE, SR203UK",
    "PSU-000007-00": "SE210UK",
    "PSU-000008-00": "SR203IT",
    "PSU-000013-00": "IP061DE, IP061-01-DE, IP061IT, IP061-02-IT, IP061-EC-IT",
    "PSU-000016-00": "IP061UK",
    "PSU-000033-00": "IP061B",
    "PSU-000049-00": "SR213A, SR203UK DEPOP",
    "MPD-PW00476AA": "DPS5002NS, DPS5002NS_VR, DZS3001NS",
    "01403-00516":   "ESD-160S, ESD-160C, ESD-160S/VE, ESD-160C/VE, ESI-160, ESI-160/VE, "
                     "ESI-160/IP, ESI-160/IP_VE",
}


def _p(pn, desc, sn_len, hp, ld):
    return {"name": pn, "identifier": "", "description": desc, "devices": _DEVICES.get(pn, ""),
            "enabled": True, "serial_length": sn_len, "test_params": hp, "load_params": ld}


_DEFAULT_MODELS = {
    "PSU-000003-00": _p("PSU-000003-00", "ER115 - Power Supply Unit - External (ER115)", [10],
                        _hp(), _ld(12.0, 11.4, 12.6, 0.0, 2.0)),
    "PSU-000005-00": _p("PSU-000005-00", "SP171-Delta PSU (ES240UK/IT)", [12],
                        _hp(voltage=3300, low=0.01, high=10.0, ramp=0.1, test=3.0, arc=10.0),
                        _ld(12.25, 11.65, 12.85, 0.0, 3.7)),
    "PSU-000006-00": _p("PSU-000006-00", "SP210-UK-K - PSU black 17W (SR203UK)", [9, 10],
                        _hp(), _ld(12.0, 11.4, 12.6, 0.0, 1.42)),
    "PSU-000007-00": _p("PSU-000007-00", "SP210-UK-K - PSU white 17W (SE210UK)", [9, 10],
                        _hp(), _ld(12.0, 11.4, 12.6, 0.0, 1.42)),
    "PSU-000008-00": _p("PSU-000008-00", "SP210EU-K Ext PSU IP Box REF HUB 4 (SR203IT)", [10],
                        _hp(), _ld(12.0, 11.4, 12.6, 0.0, 1.42)),
    "PSU-000013-00": _p("PSU-000013-00", "SPA0506EU-K IP061 EU PSU (IP061DE/IT)", [10, 21],
                        _hp(low=0.15, ramp=0.5, fall=0.5), _ld(5.0, 4.50, 5.50, 0.0, 1.12)),
    "PSU-000016-00": _p("PSU-000016-00", "SSW-3344UK IP061 UK PSU (IP061UK)", [10, 21],
                        _hp(low=0.15, ramp=0.5, fall=0.5), _ld(5.0, 4.50, 5.50, 0.0, 1.12)),
    "PSU-000033-00": _p("PSU-000033-00", "GPA075-01-G-SKY (IP061 Dhruv PSU)", [21],
                        _hp(low=0.15, ramp=0.5, fall=0.5), _ld(5.0, 4.75, 5.25, 0.0, 1.5)),
    "PSU-000049-00": _p("PSU-000049-00", "SPA360-21-C-SKY-gry (SR213 Depop)", [10, 21],
                        _hp(mode="DC", voltage=4242, low=0.0, high=3.5),
                        _ld(12.0, 11.4, 12.6, 0.0, 3.0)),
    "MPD-PW00476AA": _p("MPD-PW00476AA", "Adapter External 12V PSU (DPS5002NS)", [22],
                        _hp(voltage=3750, low=0.23, high=10.0, test=3.0),
                        _ld(12.0, 11.4, 12.6, 0.2, 3.33)),
    "01403-00516":   _p("01403-00516", "Power Adapter NB1233EIC81A_T (ESD-160S/C ESI-160)",
                        [17, 18, 19],
                        _hp(voltage=3750, low=0.23, high=10.0, test=3.0),
                        _ld(12.0, 11.4, 12.6, 0.2, 3.33)),
}

_LOAD_DEFAULTS = _ld(0, 0, 0, 0, 0)
_OLD_PN = re.compile(r"^PSU-(\d{5})-(\d{2})$")


def normalize_pn(pn: str) -> str:
    """PSU-00006-00 -> PSU-000006-00 (format z 6 cyframi)."""
    pn = (pn or "").strip().upper()
    m = _OLD_PN.match(pn)
    return f"PSU-0{m.group(1)}-{m.group(2)}" if m else pn


def _migrate(saved: dict):
    changed = False
    out = {}
    for key, val in saved.items():
        new_key = normalize_pn(key)
        if not isinstance(val, dict):
            out[new_key] = val
            continue
        if new_key != key:
            changed = True
            val = dict(val, name=new_key)
        # Opis z Hi-Pot PSU "Zasilacz PSU-0000X-00" -> opis z tabeli produktów
        desc = str(val.get("description", ""))
        if new_key in _DEFAULT_MODELS and (not desc or desc.startswith("Zasilacz ")):
            new_desc = _DEFAULT_MODELS[new_key]["description"]
            if desc != new_desc:
                val["description"] = new_desc
                changed = True
        if "enabled" not in val:
            val["enabled"] = True
            changed = True
        # v1.1.1: lista urządzeń, na które wchodzi zasilacz
        if "devices" not in val:
            val["devices"] = _DEVICES.get(new_key, "")
            changed = True
        if isinstance(val.get("serial_length"), int):
            val["serial_length"] = [val["serial_length"]]
            changed = True
        # Profil z Hi-Pot PSU bez parametrów obciążenia - dopisz z tabeli, jeśli PN jest znany.
        if "load_params" not in val and new_key in _DEFAULT_MODELS:
            val["load_params"] = copy.deepcopy(_DEFAULT_MODELS[new_key]["load_params"])
            changed = True
        if isinstance(val.get("load_params"), dict):
            for k, v in _LOAD_DEFAULTS.items():
                if k not in val["load_params"]:
                    val["load_params"][k] = v
                    changed = True
        if new_key in out:          # stary i nowy klucz naraz - zostaje nowszy (6 cyfr)
            changed = True
            if key != new_key:
                continue
        out[new_key] = val
    return out, changed


HIPOT_REQUIRED = ("mode", "voltage", "current_limit_high", "current_limit_low",
                  "ramp_time", "test_time", "fall_time")
LOAD_REQUIRED = ("enabled", "v_min", "v_max", "i_min", "i_max")


def _is_valid(key, value) -> bool:
    if not isinstance(value, dict) or "serial_length" not in value:
        return False
    hp, ld = value.get("test_params"), value.get("load_params")
    if not isinstance(hp, dict) or any(r not in hp for r in HIPOT_REQUIRED):
        return False
    if not isinstance(ld, dict) or any(r not in ld for r in LOAD_REQUIRED):
        return False
    return True


def check_load_params(ld: dict) -> list:
    """Spójność parametrów obciążenia - lista problemów (pusta = OK)."""
    problems = []
    try:
        vmin, vmax = float(ld["v_min"]), float(ld["v_max"])
        imin, imax = float(ld["i_min"]), float(ld["i_max"])
    except (KeyError, TypeError, ValueError):
        return ["nieprawidłowe wartości liczbowe"]
    if not (0 < vmin < vmax <= 150):
        problems.append("okno napięcia: 0 < Vmin < Vmax ≤ 150 V (OEL8512)")
    if not (0 <= imin < imax <= 40):
        problems.append("prąd: 0 ≤ Imin < Imax ≤ 40 A (OEL8512)")
    if vmax * imax > 400:
        problems.append("moc Vmax·Imax > 400 W (OEL8512)")
    for k, lo, hi in (("power_on_delay", 0, 30), ("settle_time", 0.1, 30), ("samples", 1, 50)):
        try:
            if not (lo <= float(ld.get(k, lo)) <= hi):
                problems.append(f"{k} poza zakresem {lo}-{hi}")
        except (TypeError, ValueError):
            problems.append(f"{k}: nieprawidłowa wartość")
    return problems


class PowerSupplyModels:
    """Baza profili - wczytywana z models.json przy starcie."""

    MODELS: dict = _sm.load_models(_DEFAULT_MODELS, _migrate, _is_valid)

    @staticmethod
    def reload():
        PowerSupplyModels.MODELS = _sm.load_models(_DEFAULT_MODELS, _migrate, _is_valid)
        return PowerSupplyModels.MODELS

    @staticmethod
    def save():
        return _sm.save_models(PowerSupplyModels.MODELS)

    @staticmethod
    def delete_model(model_key: str) -> bool:
        PowerSupplyModels.MODELS.pop(model_key, None)
        if model_key in _DEFAULT_MODELS:
            return _sm.mark_model_deleted(model_key, PowerSupplyModels.MODELS)
        return _sm.save_models(PowerSupplyModels.MODELS)

    @staticmethod
    def set_enabled(model_key: str, enabled: bool) -> bool:
        m = PowerSupplyModels.MODELS.get(model_key)
        if not m:
            return False
        m["enabled"] = bool(enabled)
        return PowerSupplyModels.save()

    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_serial(model_key: str, serial_number: str):
        model = PowerSupplyModels.MODELS.get(model_key)
        if not model:
            return False, "Nieznany model"
        serial_number = (serial_number or "").strip().upper()
        if not serial_number:
            return False, "Pusty numer seryjny"
        # Pojedyncza spacja w środku dozwolona (etykiety PSU-000013-00 / -000019-00,
        # np. "SSW-3344EU 24BC301844"). Odrzucamy tabulatory, znaki sterujące
        # i podwójne spacje - typowe dla źle skonfigurowanego skanera.
        if any(ch.isspace() and ch != " " for ch in serial_number) \
                or any(not ch.isprintable() for ch in serial_number):
            return False, "SN zawiera niedozwolone znaki - zeskanuj ponownie"
        if "  " in serial_number:
            return False, "SN zawiera podwójną spację - zeskanuj ponownie"

        expected = model.get("serial_length", [])
        if isinstance(expected, int):
            expected = [expected]
        if not expected:
            return True, "OK"
        actual = len(serial_number)
        if actual not in expected:
            if len(expected) == 1:
                return False, f"Zły SN! Długość {actual} znaków, wymagana {expected[0]}"
            return False, (f"Zły SN! Długość {actual} znaków, wymagana "
                           f"{' lub '.join(str(x) for x in sorted(expected))}")
        return True, "OK"

    @staticmethod
    def get_all_models(enabled_only: bool = False):
        return sorted(k for k, v in PowerSupplyModels.MODELS.items()
                      if not enabled_only or v.get("enabled", True))

    @staticmethod
    def get_model_info(model_key: str):
        return PowerSupplyModels.MODELS.get(model_key)

    @staticmethod
    def is_default(model_key: str) -> bool:
        return model_key in _DEFAULT_MODELS


# ====================================================================== #
# EKSPORT DO PRZEGLĄDU (ENG -> Profile -> Eksport CSV)                    #
# ====================================================================== #
_EXPORT_COLS = [
    ("PN", lambda k, m: k), ("Aktywny", lambda k, m: "TAK" if m.get("enabled", True) else "NIE"),
    ("Opis", lambda k, m: m.get("description", "")), ("Urządzenia", lambda k, m: m.get("devices", "")),
    ("Długość SN", lambda k, m: " lub ".join(str(x) for x in (m.get("serial_length") or []))
     if isinstance(m.get("serial_length"), list) else m.get("serial_length", "")),
    ("Hi-Pot tryb", lambda k, m: m["test_params"].get("mode")),
    ("Hi-Pot napięcie [V]", lambda k, m: m["test_params"].get("voltage")),
    ("Tolerancja [V]", lambda k, m: m["test_params"].get("voltage_tolerance")),
    ("Prąd LOW [mA]", lambda k, m: m["test_params"].get("current_limit_low")),
    ("Prąd HIGH [mA]", lambda k, m: m["test_params"].get("current_limit_high")),
    ("Ramp [s]", lambda k, m: m["test_params"].get("ramp_time")),
    ("Test [s]", lambda k, m: m["test_params"].get("test_time")),
    ("Fall [s]", lambda k, m: m["test_params"].get("fall_time")),
    ("Częstotliwość [Hz]", lambda k, m: m["test_params"].get("frequency")),
    ("ARC", lambda k, m: m["test_params"].get("arc_detection")),
    ("Obciążenie", lambda k, m: "TAK" if m.get("load_params", {}).get("enabled") else "NIE"),
    ("V nominal [V]", lambda k, m: m.get("load_params", {}).get("v_nominal")),
    ("V min [V]", lambda k, m: m.get("load_params", {}).get("v_min")),
    ("V max [V]", lambda k, m: m.get("load_params", {}).get("v_max")),
    ("I min [A]", lambda k, m: m.get("load_params", {}).get("i_min")),
    ("I max [A]", lambda k, m: m.get("load_params", {}).get("i_max")),
    ("Załączenie [s]", lambda k, m: m.get("load_params", {}).get("power_on_delay")),
    ("Ustalanie [s]", lambda k, m: m.get("load_params", {}).get("settle_time")),
    ("Próbki", lambda k, m: m.get("load_params", {}).get("samples")),
    ("Tol. prądu [%]", lambda k, m: m.get("load_params", {}).get("i_tol_pct")),
    ("Tol. prądu [A]", lambda k, m: m.get("load_params", {}).get("i_tol_abs")),
]


def export_csv(path: str, cfg=None) -> int:
    """
    Wszystkie profile do CSV dla Excela (średnik, przecinek dziesiętny, UTF-8 z BOM).
    Dodatkowe kolumny: moc MAX, czas Hi-Pot i czy mieści się w oknie przekaźnika.
    Zwraca liczbę profili.
    """
    import csv

    def cell(v):
        if isinstance(v, float):
            return f"{v:g}".replace(".", ",")
        return "" if v is None else str(v)

    fits = None
    if cfg is not None:
        from combo_sequence import hipot_fits
        fits = lambda tp: "TAK" if hipot_fits(cfg, tp)[0] else "NIE"   # noqa: E731
    keys = PowerSupplyModels.get_all_models()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        head = [h for h, _ in _EXPORT_COLS] + ["Moc przy I max [W]", "Czas Hi-Pot [s]"]
        if fits:
            head.append("Hi-Pot mieści się w oknie przekaźnika")
        w.writerow(head)
        for k in keys:
            m = PowerSupplyModels.get_model_info(k)
            tp, ld = m["test_params"], m.get("load_params", {})
            row = [cell(fn(k, m)) for _, fn in _EXPORT_COLS]
            try:
                row.append(cell(round(float(ld.get("v_nominal", 0)) * float(ld.get("i_max", 0)), 2)))
            except (TypeError, ValueError):
                row.append("")
            row.append(cell(float(tp.get("ramp_time", 0)) + float(tp.get("test_time", 0))
                            + float(tp.get("fall_time", 0))))
            if fits:
                row.append(fits(tp))
            w.writerow(row)
    return len(keys)
