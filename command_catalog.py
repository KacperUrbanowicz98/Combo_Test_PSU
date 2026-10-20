"""Katalog testów komend - 1:1 z OEL85 Programming Manual V1.0.2 (styczeń 2025).

Każdy test = lista kroków. Krok: opcjonalny zapis (send) + opcjonalne zapytanie (query)
+ oczekiwanie (expect) + sposób porównania (cmp).

Typy porównań (cmp):
  num      - liczba w tolerancji (config: num_abs_tol / num_rel_tol)
  num_any  - dowolna poprawna liczba (np. odczyty pomiarów)
  changed  - liczba różna od odpowiedzi z poprzedniego kroku (np. RANG MIN -> MAX)
  enum     - tekst, dopuszczalna krótka/długa forma (NORM == NORMAL, PULS == PULSE)
  bool     - 0/1/ON/OFF (równoważne)
  mode     - odpowiedź MODE? (np. 'current' dla CURR)
  any      - jakakolwiek niepusta odpowiedź
  fields   - odpowiedź CSV z dokładnie `expect` polami
  regex    - dopasowanie wyrażenia regularnego
  int_in   - liczba całkowita z listy `expect`
  alive    - tylko zapis; potem *IDN? musi odpowiedzieć (urządzenie nie zawiesiło się)
  none     - zapis bez weryfikacji (krok pomocniczy)

kind:
  doc    - komenda udokumentowana -> PASS/FAIL
  probe  - sprawdzenie niejasności/literówek manuala lub komend nieudokumentowanych -> INFO
"""
from dataclasses import dataclass, field
from typing import Any, List, Optional

# Wartości testowe dobrane dla OEL8512 (150 V / 40 A / 400 W) - bezpieczne przy wolnych zaciskach
# i różne od wartości domyślnych, żeby odczyt zwrotny faktycznie dowodził zapisu.


@dataclass
class Step:
    send: Optional[str] = None
    query: Optional[str] = None
    expect: Any = None
    cmp: str = "none"
    note: str = ""


@dataclass
class TestCase:
    id: str
    group: str
    title: str
    steps: List[Step]
    page: int = 0
    kind: str = "doc"           # doc | probe
    note: str = ""
    needs_input_confirm: bool = False

    @property
    def command(self):
        for s in self.steps:
            if s.send:
                return s.send.split()[0]
            if s.query:
                return s.query
        return ""


@dataclass
class Group:
    key: str
    name: str
    pre: List[str] = field(default_factory=list)   # komendy przygotowujące (np. MODE CURR)
    cases: List[TestCase] = field(default_factory=list)
    needs_input_confirm: bool = False
    default_on: bool = True


def rw(cid, title, setfmt, query, values, cmp="num", page=0, note="", kind="doc"):
    steps = [Step(send=setfmt.format(v), query=query, expect=v, cmp=cmp) for v in values]
    return TestCase(cid, "", title, steps, page=page, kind=kind, note=note)


def q(cid, title, query, cmp="any", expect=None, page=0, note="", kind="doc"):
    return TestCase(cid, "", title, [Step(query=query, expect=expect, cmp=cmp)], page=page, kind=kind, note=note)


def w(cid, title, cmd, page=0, note="", kind="doc"):
    return TestCase(cid, "", title, [Step(send=cmd, cmp="alive")], page=page, kind=kind, note=note)


SLEW = ["FAST", "SLOW", "NORM"]


def build_catalog():
    G = []

    # ------------------------------------------------------------ 2. IEEE488.2
    G.append(Group("ieee", "2. IEEE488.2", cases=[
        q("IDN", "*IDN? - identyfikacja", "*IDN?", cmp="regex", expect=r"^[^,]+,[^,]+,[^,]+,[^,]+$", page=7,
          note="Format: <Factory>,<model>,<serial>,<wersja>"),
        TestCase("RST", "", "*RST - przywrócenie domyślnych", [
            Step(send="CURR 3.21", query="CURR?", expect=3.21, cmp="num", note="ustaw wartość niedomyślną"),
            Step(send="*RST", query="CURR?", expect=3.21, cmp="changed", note="po *RST wartość ma się zmienić"),
        ], page=7, note="Weryfikacja: wartość CURR po *RST różni się od ustawionej"),
    ]))

    # ------------------------------------------------------------ 3. System
    G.append(Group("syst", "3. System", cases=[
        w("SYST_REM", "SYSTem:REMote", "SYST:REM", page=9),
        rw("SYST_SENS", "SYSTem:SENSe[:STATe] - remote sense", "SYST:SENS {}", "SYST:SENS?", ["ON", "OFF"],
           cmp="bool", page=9),
        rw("SYST_SENS_01", "SYSTem:SENSe - parametr 1/0", "SYST:SENS {}", "SYST:SENS?", [1, 0], cmp="bool", page=9),
        rw("SYST_SENS_LONG", "SYSTem:SENSe:STATe - pełna forma", "SYSTem:SENSe:STATe {}", "SYSTem:SENSe:STATe?",
           [1, 0], cmp="bool", page=9),
        TestCase("SYST_LOC", "", "SYSTem:LOCal (+ powrót do REM)", [
            Step(send="SYST:LOC", cmp="alive"),
            Step(send="SYST:REM", cmp="alive", note="przywrócenie trybu zdalnego"),
        ], page=9),
        q("SYST_REM_Q", "Przykład z manuala: SYST:REM? (komenda bez zapytania)", "SYST:REM?", page=9, kind="probe",
          note="Manual w przykładzie podaje 'SYST:REM?' choć to komenda - sprawdzamy reakcję"),
    ]))

    # ------------------------------------------------------------ 4. Mode / Function
    modes = [("CURR", "current"), ("VOLT", "voltage"), ("POW", "power"), ("RES", "resistance"),
             ("DYN", "dynamic"), ("BAT", "battery"), ("OCP", "ocp"), ("OPP", "opp")]
    G.append(Group("mode", "4. Tryb (FUNCtion / MODE)", cases=[
        TestCase("MODE_ALL", "", "MODE <tryb> - wszystkie tryby", [
            Step(send=f"MODE {m}", query="MODE?", expect=r, cmp="mode") for m, r in modes], page=14),
        TestCase("FUNC_ALL", "", "FUNCtion <tryb> - alias MODE", [
            Step(send=f"FUNC {m}", query="FUNC?", expect=r, cmp="mode") for m, r in modes[:4]], page=14),
        TestCase("MODE_LONG", "", "SOURce:FUNCtion - pełna forma", [
            Step(send="SOURce:FUNCtion RESistance", query="SOURce:FUNCtion?", expect="resistance", cmp="mode"),
            Step(send="SOURce:FUNCtion CURRent", query="SOURce:FUNCtion?", expect="current", cmp="mode"),
        ], page=14),
        TestCase("MODE_OVP", "", "MODE OVP (niespójność manuala OVP/OPP)", [
            Step(send="MODE CURR", cmp="none"),
            Step(send="MODE OVP", query="MODE?", expect="ovp", cmp="mode")], page=14, kind="probe",
            note="Lista parametrów zawiera OVP, opis mówi OPP - sprawdzamy, czy OVP istnieje"),
    ]))

    # ------------------------------------------------------------ 4.1 Input (wymaga wolnych zacisków)
    G.append(Group("inp", "4.1 Wejście (INPut / SHORt)", pre=["MODE CURR", "CURR 0"], needs_input_confirm=True, cases=[
        rw("INP", "INPut <bool> - 1/0", "INP {}", "INP?", [1, 0], cmp="bool", page=15),
        rw("INP_ONOFF", "INPut <bool> - ON/OFF", "INP {}", "INP?", ["ON", "OFF"], cmp="bool", page=15),
        rw("INP_SHOR", "INPut:SHORt <bool>", "INP:SHOR {}", "INP:SHOR?", [1, 0], cmp="bool", page=15,
           note="Zwarcie wejścia - tylko przy wolnych zaciskach!"),
    ]))
    for c in G[-1].cases:
        c.needs_input_confirm = True

    # ------------------------------------------------------------ 4.2 Voltage
    G.append(Group("volt", "4.2 Napięcie (VOLTage)", pre=["MODE VOLT"], cases=[
        TestCase("VOLT_RANG", "", "VOLTage:RANGe MIN/MAX", [
            Step(send="VOLT:RANG MIN", query="VOLT:RANG?", cmp="num_any"),
            Step(send="VOLT:RANG MAX", query="VOLT:RANG?", cmp="changed", note="MAX musi dać inną wartość niż MIN"),
        ], page=17),
        rw("VOLT_RANG_NUM", "VOLTage:RANGe <liczba> (150 V)", "VOLT:RANG {}", "VOLT:RANG?", [150], cmp="num_any",
           page=17, note="Odczyt może zwrócić granicę zakresu - sprawdzamy tylko format"),
        rw("VOLT_SLEW", "VOLTage:SLEW[:BOTH]", "VOLT:SLEW {}", "VOLT:SLEW?", SLEW, cmp="enum", page=17),
        rw("VOLT_SLEW_BOTH", "VOLTage:SLEW:BOTH - pełna forma", "VOLT:SLEW:BOTH {}", "VOLT:SLEW:BOTH?", ["SLOW", "NORM"],
           cmp="enum", page=17),
        rw("VOLT_SLEW_RISE", "VOLTage:SLEW:RISE", "VOLT:SLEW:RISE {}", "VOLT:SLEW:RISE?", SLEW, cmp="enum", page=18),
        rw("VOLT_SLEW_FALL", "VOLTage:SLEW:FALL", "VOLT:SLEW:FALL {}", "VOLT:SLEW:FALL?", SLEW, cmp="enum", page=18),
        TestCase("VOLT_SLEW_RISE_SPACE", "", "Przykład z manuala: 'VOLT:SLEW RISE FAST' (spacja zamiast ':')", [
            Step(send="VOLT:SLEW:RISE SLOW", cmp="none"),
            Step(send="VOLT:SLEW RISE FAST", query="VOLT:SLEW:RISE?", expect="FAST", cmp="enum")], page=18, kind="probe"),
        rw("VOLT_PROT", "VOLTage:PROTection[:LEVel]", "VOLT:PROT {}", "VOLT:PROT?", [120.5, 100], page=19),
        TestCase("VOLT_PROT_MINMAX", "", "VOLTage:PROTection MIN/MAX", [
            Step(send="VOLT:PROT MIN", query="VOLT:PROT?", cmp="num_any"),
            Step(send="VOLT:PROT MAX", query="VOLT:PROT?", cmp="changed")], page=19,
            note="Kończy na MAX - OVP nie przeszkadza w dalszych testach"),
        rw("VOLT_ON", "VOLTage[:LEVel]:ON (Von)", "VOLT:ON {}", "VOLT:ON?", [3.5, 1.25], page=19),
        rw("VOLT_LEV_ON", "VOLTage:LEVel:ON - pełna forma", "VOLT:LEV:ON {}", "VOLT:LEV:ON?", [2.0], page=19),
        rw("VOLT", "VOLTage (CV setpoint)", "VOLT {}", "VOLT?", [12.5, 5.25], page=20),
        rw("VOLT_LONG", "SOURce:VOLTage:LEVel:IMMediate:AMPLitude", "SOURce:VOLTage:LEVel:IMMediate:AMPLitude {}",
           "SOURce:VOLTage:LEVel:IMMediate:AMPLitude?", [7.5], page=20),
    ]))

    # ------------------------------------------------------------ 4.3 Current
    G.append(Group("curr", "4.3 Prąd (CURRent)", pre=["MODE CURR"], cases=[
        TestCase("CURR_RANG", "", "CURRent:RANGe MIN/MAX", [
            Step(send="CURR:RANG MIN", query="CURR:RANG?", cmp="num_any"),
            Step(send="CURR:RANG MAX", query="CURR:RANG?", cmp="changed")], page=21),
        rw("CURR_SLEW", "CURRent:SLEW[:BOTH] (A/µs)", "CURR:SLEW {}", "CURR:SLEW?", [1.0, 0.5], page=21),
        rw("CURR_SLEW_RISE", "CURRent:SLEW:RISE (A/µs)", "CURR:SLEW:RISE {}", "CURR:SLEW:RISE?", [1.0, 0.5], page=22),
        rw("CURR_SLEW_FALL", "CURRent:SLEW:FALL (A/µs)", "CURR:SLEW:FALL {}", "CURR:SLEW:FALL?", [0.5, 1.0], page=22),
        rw("CURR_SLEW_FALL_ENUM", "CURRent:SLEW:FALL FAST (manual: parametr FAST/NORM/SLOW?)", "CURR:SLEW:FALL {}",
           "CURR:SLEW:FALL?", ["FAST"], cmp="any", page=22, kind="probe",
           note="Manual podaje sprzecznie FAST/NORM/SLOW i A/µs dla tej komendy"),
        rw("CURR_PROT", "CURRent:PROTection[:LEVel]", "CURR:PROT {}", "CURR:PROT?", [20.5, 10], page=23),
        TestCase("CURR_PROT_MINMAX", "", "CURRent:PROTection MIN/MAX", [
            Step(send="CURR:PROT MIN", query="CURR:PROT?", cmp="num_any"),
            Step(send="CURR:PROT MAX", query="CURR:PROT?", cmp="changed")], page=23),
        rw("CURR_PROT_DEL", "CURRent:PROTection:DELay (wspomniane, nieudokumentowane)", "CURR:PROT:DEL {}",
           "CURR:PROT:DEL?", [1], cmp="num", page=23, kind="probe"),
        rw("CURR", "CURRent (CC setpoint)", "CURR {}", "CURR?", [1.5, 2.75], page=23),
        rw("CURR_LONG", "SOURce:CURRent:LEVel:IMMediate:AMPLitude", "SOURce:CURRent:LEVel:IMMediate:AMPLitude {}",
           "SOURce:CURRent:LEVel:IMMediate:AMPLitude?", [0.75], page=23),
        rw("CURR_LIM", "CURRent:LIMit (CV current limit)", "CURR:LIM {}", "CURR:LIM?", [5, 3.5], page=24),
    ]))

    # ------------------------------------------------------------ 4.4 Resistance
    G.append(Group("res", "4.4 Rezystancja (RESistance)", pre=["MODE RES"], cases=[
        rw("RES", "RESistance (CR setpoint)", "RES {}", "RES?", [10, 47.5], page=25),
        rw("RES_SLEW_RISE", "RESistance:SLEW:RISE", "RES:SLEW:RISE {}", "RES:SLEW:RISE?", SLEW, cmp="enum", page=25),
        rw("RES_SLEW_FALL", "RESistance:SLEW:FALL", "RES:SLEW:FALL {}", "RES:SLEW:FALL?", SLEW, cmp="enum", page=26),
    ]))

    # ------------------------------------------------------------ 4.5 Power
    G.append(Group("pow", "4.5 Moc (POWer)", pre=["MODE POW"], cases=[
        rw("POW_PROT", "POWer:PROTection[:LEVel]", "POW:PROT {}", "POW:PROT?", [350, 200], page=27),
        rw("POW_PROT_DEL", "POWer:PROTection:DELay (wspomniane, nieudokumentowane)", "POW:PROT:DEL {}",
           "POW:PROT:DEL?", [1], page=27, kind="probe"),
        rw("POW", "POWer (CP setpoint)", "POW {}", "POW?", [50, 25.5], page=27),
        rw("POW_SLEW_RISE", "POWer:SLEW:RISE", "POW:SLEW:RISE {}", "POW:SLEW:RISE?", SLEW, cmp="enum", page=28),
        rw("POW_SLEW_FALL", "POWer:SLEW:FALL", "POW:SLEW:FALL {}", "POW:SLEW:FALL?", SLEW, cmp="enum", page=28),
        TestCase("POW_PROT_MAX", "", "POWer:PROTection MAX (przywrócenie)", [
            Step(send="POW:PROT MAX", query="POW:PROT?", cmp="num_any")], page=27),
    ]))

    # ------------------------------------------------------------ 4.6 Dynamic
    G.append(Group("dyn", "4.6 Dynamika (DYNamic)", pre=["MODE DYN"], cases=[
        rw("DYN_HIGH", "DYNamic:HIGH[:LEVel]", "DYN:HIGH {}", "DYN:HIGH?", [10, 5], page=30),
        rw("DYN_HIGH_DWEL", "DYNamic:HIGH:DWELl", "DYN:HIGH:DWEL {}", "DYN:HIGH:DWEL?", [0.01, 0.5], page=30),
        rw("DYN_LOW", "DYNamic:LOW[:LEVel]", "DYN:LOW {}", "DYN:LOW?", [1, 0.5], page=31),
        rw("DYN_LOW_DWEL", "DYNamic:LOW:DWELl", "DYN:LOW:DWEL {}", "DYN:LOW:DWEL?", [1, 0.2], page=31),
        rw("DYN_SLEW", "DYNamic:SLEW (A/µs)", "DYN:SLEW {}", "DYN:SLEW?", [1.0, 0.5], page=32),
        rw("DYN_SLEW_RISE", "DYNamic:SLEW:RISE", "DYN:SLEW:RISE {}", "DYN:SLEW:RISE?", [1.0, 0.5], page=32),
        rw("DYN_SLEW_FALL", "DYNamic:SLEW:FALL", "DYN:SLEW:FALL {}", "DYN:SLEW:FALL?", [0.5, 1.0], page=33),
        rw("DYN_MODE", "DYNamic:MODE CONT/PULS", "DYN:MODE {}", "DYN:MODE?", ["PULS", "CONT"], cmp="enum", page=33),
        rw("DYN_MODE_LONG", "DYNamic:MODE - pełne nazwy", "DYN:MODE {}", "DYN:MODE?", ["PULSe", "CONTinuous"],
           cmp="enum", page=33),
        rw("DYN_HIGH_DEWL", "Literówka ze spisu treści: DYN:HIGH:DEWLl", "DYN:HIGH:DEWL {}", "DYN:HIGH:DWEL?",
           [0.02], page=12, kind="probe"),
    ]))

    # ------------------------------------------------------------ 4.7 Battery
    G.append(Group("bat", "4.7 Test baterii (BATtery)", pre=["MODE BAT"], cases=[
        rw("BAT", "BATtery[:LEVel] - prąd rozładowania", "BAT {}", "BAT?", [2, 1.5], page=35),
        rw("BAT_VST", "BATtery:VSTop - napięcie odcięcia", "BAT:VST {}", "BAT:VST?", [5, 10.5], page=35),
        rw("BAT_CST", "BATtery:CSTop - pojemność stop", "BAT:CST {}", "BAT:CST?", [5, 2.5], page=36),
        rw("BAT_TIM", "BATtery:TIMestop - czas stop", "BAT:TIM {}", "BAT:TIM?", [50, 120], page=36),
        rw("BAT_VEN", "BATtery:VENabstop", "BAT:VEN {}", "BAT:VEN?", ["ON", "OFF"], cmp="bool", page=37),
        rw("BAT_CEN", "BATtery:CENabstop", "BAT:CEN {}", "BAT:CEN?", ["ON", "OFF"], cmp="bool", page=37),
        rw("BAT_TEN", "BATtery:TENabstop", "BAT:TEN {}", "BAT:TEN?", ["ON", "OFF"], cmp="bool", page=38),
        q("BAT_CAPA", "BATtery:CAPAcity? - zmierzona pojemność", "BAT:CAPA?", cmp="num_any", page=38),
        q("BAT_ENERGY", "BATtery:ENERGY? - zmierzona energia", "BAT:ENERGY?", cmp="num_any", page=39),
        q("BAT_TIME", "BATtery:TIME? - czas testu", "BAT:TIME?", cmp="num_any", page=39),
        q("BAT_STAT", "BATtery:STATe? - stan (0..3)", "BAT:STAT?", cmp="int_in", expect=[0, 1, 2, 3], page=40),
        q("BAT_ALL", "BATtery:ALL? - 5 pól (V,I,Ah,Wh,s)", "BAT:ALL?", cmp="fields", expect=5, page=40),
        q("BAT_LONG", "Pełna forma: SOURce:BATtery:VSTop?", "SOURce:BATtery:VSTop?", cmp="num_any", page=35,
          kind="probe", note="Manual zapisuje prefiks niestandardowo ':[:SOURce]:BAT...'"),
    ]))

    # ------------------------------------------------------------ 4.8 OCP
    G.append(Group("ocp", "4.8 Test OCP", pre=["MODE OCP"], cases=[
        rw("OCP_VON", "OCP:VON", "OCP:VON {}", "OCP:VON?", [5, 3], page=41),
        rw("OCP_IMAX", "OCP:IMAX", "OCP:IMAX {}", "OCP:IMAX?", [10, 8], page=43),
        rw("OCP_ISET", "OCP:ISET", "OCP:ISET {}", "OCP:ISET?", [1, 2], page=41),
        rw("OCP_IST", "OCP:ISTep", "OCP:IST {}", "OCP:IST?", [0.5, 0.1], page=42),
        rw("OCP_IDEL", "OCP:IDELaystep (ms)", "OCP:IDEL {}", "OCP:IDEL?", [100, 200], page=42),
        rw("OCP_VOCP", "OCP:VOCP", "OCP:VOCP {}", "OCP:VOCP?", [5, 4], page=43),
    ]))

    # ------------------------------------------------------------ 4.9 OPP
    G.append(Group("opp", "4.9 Test OPP", pre=["MODE OPP"], cases=[
        rw("OPP_VON", "OPP:VON", "OPP:VON {}", "OPP:VON?", [5, 3], page=45),
        rw("OPP_PMAX", "OPP:PMAX", "OPP:PMAX {}", "OPP:PMAX?", [100, 80], page=47),
        rw("OPP_PMIN", "OPP:PMIN", "OPP:PMIN {}", "OPP:PMIN?", [10, 5], page=48),
        rw("OPP_PSET", "OPP:PSET", "OPP:PSET {}", "OPP:PSET?", [20, 30], page=45),
        rw("OPP_PST", "OPP:PSTep", "OPP:PST {}", "OPP:PST?", [5, 2], page=46),
        rw("OPP_IDEL", "OPP:IDELaystep (ms)", "OPP:IDEL {}", "OPP:IDEL?", [100, 200], page=46),
        rw("OPP_VOPP", "OPP:VOPP", "OPP:VOPP {}", "OPP:VOPP?", [5, 4], page=47),
    ]))

    # ------------------------------------------------------------ 5. Measurement
    G.append(Group("meas", "5. Pomiary (MEASure)", pre=["MODE CURR"], cases=[
        q("MEAS_VOLT", "MEASure:VOLTage?", "MEAS:VOLT?", cmp="num_any", page=50,
          note="Przy wolnych zaciskach oczekiwane ~0 V"),
        q("MEAS_CURR", "MEASure:CURRent?", "MEAS:CURR?", cmp="num_any", page=50),
        q("MEAS_POW", "MEASure:POWer?", "MEAS:POW?", cmp="num_any", page=50),
        q("MEAS_LONG", "MEASure:SCALar:VOLTage:DC? - pełna forma", "MEASure:SCALar:VOLTage:DC?", cmp="num_any", page=50),
        q("MEAS_ALL", "MEASure:ALL:INFO? - V,I,P,OVP,OCP,OPP", "MEAS:ALL:INFO?", cmp="regex",
          expect=r"^\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*,\s*(ON|OFF|0|1)\s*,\s*(ON|OFF|0|1)\s*,\s*(ON|OFF|0|1)\s*$",
          page=51),
    ]))

    # ------------------------------------------------------------ 6. Parallel
    G.append(Group("par", "6. Praca równoległa (PARallel)", cases=[
        q("PAR", ":PARallel[:STATe]?", ":PAR?", cmp="any", page=53),
        q("PAR_STAT", ":PARallel:STATe? - pełna forma", ":PARallel:STATe?", cmp="any", page=53),
        q("PAR_IDEN", ":PARallel:Identity?", ":PARallel:Identity?", cmp="any", page=53),
        q("PAR_NUM", ":PARallel:NUMber?", ":PAR:NUM?", cmp="num_any", page=53),
    ]))

    # ------------------------------------------------------------ Sondy - komendy spoza manuala
    G.append(Group("probe", "Sondy: komendy SCPI spoza manuala", default_on=False, cases=[
        q("P_ERR", "SYSTem:ERRor? - kolejka błędów", "SYST:ERR?", kind="probe",
          note="Gdyby działało - dałoby pewniejszą weryfikację niż readback"),
        q("P_OPC", "*OPC?", "*OPC?", kind="probe"),
        q("P_ESR", "*ESR?", "*ESR?", kind="probe"),
        q("P_STB", "*STB?", "*STB?", kind="probe"),
        q("P_VERS", "SYSTem:VERSion?", "SYST:VERS?", kind="probe"),
        w("P_CLS", "*CLS", "*CLS", kind="probe"),
        q("P_BAD", "Nieistniejąca komenda (reakcja na błąd)", "FOO:BAR?", kind="probe",
          note="Oczekiwany brak odpowiedzi; jeśli przychodzi tekst błędu - warto wiedzieć"),
    ]))

    for g in G:
        for c in g.cases:
            c.group = g.key
            if g.needs_input_confirm:
                c.needs_input_confirm = True
    return G


def all_cases(groups=None):
    for g in groups or build_catalog():
        for c in g.cases:
            yield g, c
