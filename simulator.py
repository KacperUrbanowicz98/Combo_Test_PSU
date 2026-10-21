"""Symulator OWON OEL8512 (150 V / 40 A / 400 W) - do sprawdzenia aplikacji bez urządzenia.

Implementuje zestaw komend z OEL85 Programming Manual V1.0.2 (styczeń 2025),
w formie kanonicznej (z dwukropkami, wg sekcji 'Format'). Nieznane komendy
są ignorowane, a nieznane zapytania dostają 'ERR' (jak prawdziwy OEL8512 FW V2.0.1).
To NIE jest wzorzec zachowania prawdziwego urządzenia - prawdę pokaże dopiero test na OEL.
"""
import random
import re
import threading

# long -> short mnemonic
MNEMONICS = {
    "SOURCE": "SOUR", "FUNCTION": "FUNC", "MODE": "MODE", "INPUT": "INP", "SHORT": "SHOR",
    "VOLTAGE": "VOLT", "RANGE": "RANG", "SLEW": "SLEW", "BOTH": "BOTH", "RISE": "RISE",
    "FALL": "FALL", "PROTECTION": "PROT", "LEVEL": "LEV", "IMMEDIATE": "IMM",
    "AMPLITUDE": "AMPL", "ON": "ON", "CURRENT": "CURR", "LIMIT": "LIM",
    "RESISTANCE": "RES", "POWER": "POW", "DYNAMIC": "DYN", "HIGH": "HIGH", "LOW": "LOW",
    "DWELL": "DWEL", "BATTERY": "BAT", "VSTOP": "VST", "CSTOP": "CST", "TIMESTOP": "TIM",
    "VENABSTOP": "VEN", "CENABSTOP": "CEN", "TENABSTOP": "TEN", "CAPACITY": "CAPA",
    "REAL": "REAL", "ENERGY": "ENERGY", "TIME": "TIME", "ELAPSED": "ELAP", "STATE": "STAT",
    "ALL": "ALL", "OCP": "OCP", "OPP": "OPP", "VON": "VON", "ISET": "ISET", "ISTEP": "IST",
    "IDELAYSTEP": "IDEL", "VOCP": "VOCP", "IMAX": "IMAX", "PSET": "PSET", "PSTEP": "PST",
    "VOPP": "VOPP", "PMAX": "PMAX", "PMIN": "PMIN", "MEASURE": "MEAS", "SCALAR": "SCAL",
    "DC": "DC", "INFO": "INFO", "PARALLEL": "PAR", "IDENTITY": "IDENTITY", "NUMBER": "NUM",
    "SYSTEM": "SYST", "LOCAL": "LOC", "REMOTE": "REM", "SENSE": "SENS",
}


def _short(tok):
    t = tok.upper()
    for long_, short in MNEMONICS.items():      # najpierw dokładne dopasowanie
        if t == short or t == long_:
            return short
    for long_, short in MNEMONICS.items():      # potem formy pośrednie (VOLTA, CURRE...)
        if long_.startswith(t) and len(t) >= len(short) and t.startswith(short):
            return short
    return None


def canonical(header):
    """':SOURce:CURRent:LEVel:IMMediate:AMPLitude' -> 'CURR'. None = nieznana składnia."""
    h = header.strip().lstrip(":")
    if h.startswith("*"):
        return h.upper()
    toks = []
    for t in h.split(":"):
        if not t:
            return None
        s = _short(t)
        if s is None:
            return None
        toks.append(s)
    c = ":".join(toks)
    c = re.sub(r"^SOUR:", "", c)
    c = re.sub(r"^FUNC$", "MODE", c)
    c = c.replace(":LEV", "").replace(":IMM", "").replace(":AMPL", "")
    c = c.replace("SLEW:BOTH", "SLEW")
    c = re.sub(r"^MEAS:SCAL:", "MEAS:", c)
    c = re.sub(r"^(MEAS:.*):DC", r"\1", c)
    c = c.replace("CAPA:REAL", "CAPA").replace("ENERGY:REAL", "ENERGY").replace("TIME:ELAP", "TIME")
    c = c.replace("SYST:SENS:STAT", "SYST:SENS")
    c = re.sub(r"^PAR:STAT$", "PAR", c)
    return c


# klucz: (typ, domyślna, min, max)  typ: num | enum | bool01 | boolOO
NUM, ENUM, B01, BOO = "num", "enum", "b01", "boo"
SLEW_ENUM = ("FAST", "NORM", "SLOW")

PARAMS = {
    "SYST:SENS": (B01, 0, 0, 1),
    "INP": (B01, 0, 0, 1),
    "INP:SHOR": (B01, 0, 0, 1),
    "VOLT:RANG": (NUM, 150.0, 15.0, 150.0),
    "VOLT:SLEW": (ENUM, "NORM", SLEW_ENUM, None),
    "VOLT:SLEW:RISE": (ENUM, "NORM", SLEW_ENUM, None),
    "VOLT:SLEW:FALL": (ENUM, "NORM", SLEW_ENUM, None),
    "VOLT:PROT": (NUM, 155.0, 0.0, 155.0),
    "VOLT:ON": (NUM, 0.5, 0.0, 150.0),
    "VOLT": (NUM, 0.0, 0.0, 150.0),
    "CURR:RANG": (NUM, 40.0, 4.0, 40.0),
    "CURR:SLEW": (NUM, 1.0, 0.001, 2.5),
    "CURR:SLEW:RISE": (NUM, 1.0, 0.001, 2.5),
    "CURR:SLEW:FALL": (NUM, 1.0, 0.001, 2.5),
    "CURR:PROT": (NUM, 42.0, 0.0, 42.0),
    "CURR": (NUM, 0.0, 0.0, 40.0),
    "CURR:LIM": (NUM, 40.0, 0.0, 40.0),
    "RES": (NUM, 1000.0, 0.05, 7500.0),
    "RES:SLEW:RISE": (ENUM, "NORM", SLEW_ENUM, None),
    "RES:SLEW:FALL": (ENUM, "NORM", SLEW_ENUM, None),
    "POW:PROT": (NUM, 420.0, 0.0, 420.0),
    "POW": (NUM, 0.0, 0.0, 400.0),
    "POW:SLEW:RISE": (ENUM, "NORM", SLEW_ENUM, None),
    "POW:SLEW:FALL": (ENUM, "NORM", SLEW_ENUM, None),
    "DYN:HIGH": (NUM, 0.0, 0.0, 40.0),
    "DYN:HIGH:DWEL": (NUM, 0.001, 0.00002, 3600.0),
    "DYN:LOW": (NUM, 0.0, 0.0, 40.0),
    "DYN:LOW:DWEL": (NUM, 0.001, 0.00002, 3600.0),
    "DYN:SLEW": (NUM, 1.0, 0.001, 2.5),
    "DYN:SLEW:RISE": (NUM, 1.0, 0.001, 2.5),
    "DYN:SLEW:FALL": (NUM, 1.0, 0.001, 2.5),
    "DYN:MODE": (ENUM, "CONT", ("CONT", "PULS"), None),
    "BAT": (NUM, 0.0, 0.0, 40.0),
    "BAT:VST": (NUM, 0.0, 0.0, 150.0),
    "BAT:CST": (NUM, 0.0, 0.0, 999.0),
    "BAT:TIM": (NUM, 0.0, 0.0, 99999.0),
    "BAT:VEN": (BOO, 0, 0, 1),
    "BAT:CEN": (BOO, 0, 0, 1),
    "BAT:TEN": (BOO, 0, 0, 1),
    "OCP:VON": (NUM, 0.5, 0.0, 150.0),
    "OCP:ISET": (NUM, 0.0, 0.0, 40.0),
    "OCP:IST": (NUM, 0.1, 0.0, 40.0),
    "OCP:IDEL": (NUM, 100.0, 1.0, 99999.0),
    "OCP:VOCP": (NUM, 0.0, 0.0, 150.0),
    "OCP:IMAX": (NUM, 40.0, 0.0, 40.0),
    "OPP:VON": (NUM, 0.5, 0.0, 150.0),
    "OPP:PSET": (NUM, 0.0, 0.0, 400.0),
    "OPP:PST": (NUM, 1.0, 0.0, 400.0),
    "OPP:IDEL": (NUM, 100.0, 1.0, 99999.0),
    "OPP:VOPP": (NUM, 0.0, 0.0, 150.0),
    "OPP:PMAX": (NUM, 400.0, 0.0, 400.0),
    "OPP:PMIN": (NUM, 0.0, 0.0, 400.0),
}

MODES = {
    "CURR": "current", "VOLT": "voltage", "POW": "power", "RES": "resistance",
    "DYN": "dynamic", "BAT": "battery", "OCP": "ocp", "OPP": "opp",
}


class OEL85Simulator:
    def __init__(self, idn="OWON,OEL8512,SIM0000001,V1.0.2.0.1"):
        self.idn = idn
        self.lock = threading.Lock()
        self.reset()
        self.remote = False

    def reset(self):
        self.state = {k: v[1] for k, v in PARAMS.items()}
        self.mode = "CURR"

    # -------------------------------------------------- API transportu
    def handle(self, line):
        """Przetwarza jedną linię; zwraca odpowiedź (str) lub None."""
        with self.lock:
            line = line.strip()
            if not line:
                return None
            parts = line.split(None, 1)
            header, arg = parts[0], (parts[1].strip() if len(parts) > 1 else None)
            is_query = header.endswith("?")
            c = canonical(header.rstrip("?"))
            if c is None:
                return "ERR" if is_query else None   # jak OEL8512 FW V2.0.1: nieznane zapytanie -> ERR
            return self._query(c, arg) if is_query else self._write(c, arg)

    # -------------------------------------------------- zapytania
    def _query(self, c, arg):
        if c == "*IDN":
            return self.idn
        if c == "MODE":
            return MODES[self.mode]
        if c.startswith("MEAS:"):
            return self._meas(c)
        if c in PARAMS:
            typ = PARAMS[c][0]
            v = self.state[c]
            if typ == NUM:
                return self._fmt(v)
            if typ == ENUM:
                return v
            if typ == B01:
                return str(int(v))
            if typ == BOO:
                return "ON" if v else "OFF"
        if c == "BAT:CAPA":
            return "0.000"
        if c == "BAT:ENERGY":
            return "0.000"
        if c == "BAT:TIME":
            return "0"
        if c == "BAT:STAT":
            return "0"
        if c == "BAT:ALL":
            return "0.000,0.0000,0.000,0.000,0"
        if c == "PAR":
            return "0"
        if c == "PAR:IDENTITY":
            return "Master"
        if c == "PAR:NUM":
            return "1"
        return "ERR"  # nieznane zapytanie -> ERR (zachowanie zaobserwowane na OEL8512 FW V2.0.1)

    @staticmethod
    def _fmt(v):
        s = f"{v:.4f}".rstrip("0")
        return s + "0" if s.endswith(".") else s

    def _meas(self, c):
        # Wolne zaciski: ~0 V, 0 A
        v = abs(random.gauss(0.0, 0.002))
        i = 0.0
        p = v * i
        if c == "MEAS:VOLT":
            return f"{v:.3f}"
        if c == "MEAS:CURR":
            return f"{i:.4f}"
        if c == "MEAS:POW":
            return f"{p:.3f}"
        if c == "MEAS:ALL:INFO":
            return f"{v:.3f},{i:.3f},{p:.3f},OFF,OFF,OFF"
        return None

    # -------------------------------------------------- zapis
    def _write(self, c, arg):
        if c == "*RST":
            self.reset()
            return None
        if c in ("*CLS",):
            return None
        if c == "SYST:REM":
            self.remote = True
            return None
        if c == "SYST:LOC":
            self.remote = False
            return None
        if c == "MODE":
            if arg is None:
                return None
            m = _short(arg)
            if m in MODES:
                self.mode = m
            return None
        if c in PARAMS and arg is not None:
            typ, _d, lo, hi = PARAMS[c]
            a = arg.strip().upper()
            if typ == NUM:
                if a in ("MIN", "MINIMUM"):
                    val = lo
                elif a in ("MAX", "MAXIMUM"):
                    val = hi
                else:
                    try:
                        val = float(re.match(r"^[-+0-9.eE]+", a).group(0))
                    except Exception:
                        return None
                if val < lo or val > hi:
                    return None  # poza zakresem - ignoruj
                self.state[c] = val
            elif typ == ENUM:
                for opt in lo:
                    if a == opt or (opt.startswith(a) and len(a) >= 3) or a.startswith(opt):
                        self.state[c] = opt
                        break
            elif typ in (B01, BOO):
                if a in ("1", "ON"):
                    self.state[c] = 1
                elif a in ("0", "OFF"):
                    self.state[c] = 0
        return None
