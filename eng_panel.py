"""Panel ENG: konsola SCPI, ręczne sterowanie, pomiar na żywo, diagnostyka łącza."""
import logging
import statistics
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from oel85 import MODES
from transport import SerialTransport, TransportError, hexdump

log = logging.getLogger(__name__)

QUICK = ["*IDN?", "SYST:REM", "SYST:LOC", "INP?", "INP 0", "MODE?", "SYST:SENS?", "MEAS:ALL:INFO?", "*RST"]
MODE_UNITS = {"CURR": ("CURR", "A"), "VOLT": ("VOLT", "V"), "POW": ("POW", "W"), "RES": ("RES", "Ω"),
              "DYN": ("DYN:HIGH", "A"), "BAT": ("BAT", "A"), "OCP": ("OCP:ISET", "A"), "OPP": ("OPP:PSET", "W")}


class EngPanel(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Panel ENG — OEL85")
        self.geometry("1040x660")
        self.history, self.hist_pos = [], 0
        self._live_stop = threading.Event()
        self._live_thread = None
        log.info("Panel ENG otwarty")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self._tab_console(nb)
        self._tab_control(nb)
        self._tab_live(nb)
        self._tab_diag(nb)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(bar, text="WEJŚCIE OFF (INP 0)", bg="#c00000", fg="white", font=("Segoe UI", 10, "bold"),
                  command=self.input_off).pack(side="right")
        self.lbl_info = ttk.Label(bar, text="")
        self.lbl_info.pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ helpers
    def _dev(self, need_idle=True):
        d = self.app.dev
        if not d or not d.connected:
            messagebox.showwarning("Brak połączenia", "Najpierw połącz się z urządzeniem.", parent=self)
            return None
        if need_idle and self.app.busy:
            messagebox.showwarning("Test w toku", "Trwa weryfikacja - poczekaj albo zatrzymaj test.", parent=self)
            return None
        return d

    def bg(self, fn, done=None):
        """fn() w wątku, done(wynik|wyjątek) w GUI."""
        def work():
            try:
                r = fn()
            except Exception as e:
                r = e
            if done:
                self.app.call_in_ui(lambda: self.winfo_exists() and done(r))
        threading.Thread(target=work, daemon=True).start()

    def _out(self, text, tag=None):
        self.txt_out.configure(state="normal")
        self.txt_out.insert("end", text + "\n", tag)
        self.txt_out.see("end")
        self.txt_out.configure(state="disabled")

    # ------------------------------------------------------------------ konsola
    def _tab_console(self, nb):
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="Konsola SCPI")
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.var_cmd = tk.StringVar()
        ent = ttk.Entry(row, textvariable=self.var_cmd, font=("Consolas", 11))
        ent.pack(side="left", fill="x", expand=True)
        ent.bind("<Return>", lambda e: self.send_auto())
        ent.bind("<Up>", lambda e: self._hist(-1))
        ent.bind("<Down>", lambda e: self._hist(1))
        ent.focus_set()
        ttk.Button(row, text="Auto (Enter)", command=self.send_auto).pack(side="left", padx=3)
        ttk.Button(row, text="Write", command=lambda: self.send(False)).pack(side="left")
        ttk.Button(row, text="Query", command=lambda: self.send(True)).pack(side="left", padx=3)
        self.var_hex = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="HEX", variable=self.var_hex).pack(side="left", padx=(6, 0))

        qb = ttk.Frame(f)
        qb.pack(fill="x", pady=4)
        for i, c in enumerate(QUICK):
            qb.columnconfigure(i, weight=1)
            ttk.Button(qb, text=c, command=lambda c=c: (self.var_cmd.set(c), self.send_auto())).grid(
                row=0, column=i, sticky="ew", padx=1)

        pw = ttk.PanedWindow(f, orient="horizontal")
        pw.pack(fill="both", expand=True)
        o = ttk.Frame(pw)
        pw.add(o, weight=3)
        self.txt_out = tk.Text(o, font=("Consolas", 10), state="disabled", wrap="word")
        self.txt_out.pack(fill="both", expand=True)
        self.txt_out.tag_configure("tx", foreground="#0050b0")
        self.txt_out.tag_configure("rx", foreground="#007a30")
        self.txt_out.tag_configure("err", foreground="#c00000")
        self.txt_out.tag_configure("hex", foreground="#888888")

        s = ttk.LabelFrame(pw, text="Sekwencja (1 komenda / linia, # komentarz)", padding=3)
        pw.add(s, weight=2)
        self.txt_seq = tk.Text(s, font=("Consolas", 10), height=10, width=34)
        self.txt_seq.pack(fill="both", expand=True)
        self.txt_seq.insert("1.0", "# przykład\nSYST:REM\nMODE CURR\nCURR 1.5\nCURR?\nMEAS:ALL:INFO?\n")
        sb = ttk.Frame(s)
        sb.pack(fill="x")
        ttk.Button(sb, text="Wykonaj", command=self.run_sequence).pack(side="left")
        ttk.Button(sb, text="Wczytaj…", command=self._load_seq).pack(side="left", padx=3)
        ttk.Button(sb, text="Zapisz…", command=self._save_seq).pack(side="left")

    def _hist(self, d):
        if not self.history:
            return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + d))
        self.var_cmd.set(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")
        return "break"

    def send_auto(self):
        self.send("?" in self.var_cmd.get())

    def send(self, is_query):
        cmd = self.var_cmd.get().strip()
        if not cmd:
            return
        dev = self._dev()
        if not dev:
            return
        if (cmd.upper().startswith("INP") and ("1" in cmd or "ON" in cmd.upper()) and "?" not in cmd):
            if not messagebox.askyesno("Wejście ON", "Włączyć wejście/zwarcie? Zaciski muszą być wolne, Hi-Pot wyłączony.",
                                       icon="warning", parent=self):
                return
        self.history.append(cmd)
        self.hist_pos = len(self.history)
        self.var_cmd.set("")
        self._out(f">> {cmd}", "tx")
        t0 = time.perf_counter()

        def done(res):
            ms = (time.perf_counter() - t0) * 1000
            if isinstance(res, Exception):
                self._out(f"!! {res}", "err")
                return
            r, raw = res
            bad = r is None or r == "" or r.upper().startswith("ERR")
            if is_query:
                txt = "<brak odpowiedzi - timeout>" if r is None else (r if r != "" else "<pusta linia>")
                self._out(f"<< {txt}   ({ms:.0f} ms)", "err" if bad else "rx")
            elif r:   # niezamówiona odpowiedź po zapisie (np. ERR)
                self._out(f"<< {r}   (po zapisie)", "err" if r.upper().startswith("ERR") else "rx")
            if self.var_hex.get() and (is_query or r) and raw:
                self._out(f"   HEX: {hexdump(raw)}", "hex")

        def work():
            dev.t.last_raw = b""
            r = dev.query(cmd) if is_query else dev.t.write(cmd)
            return r, dev.t.last_raw
        self.bg(work, done)

    def run_sequence(self):
        dev = self._dev()
        if not dev:
            return
        cmds = [ln.strip() for ln in self.txt_seq.get("1.0", "end").splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        if any(c.upper().startswith("INP") and ("1" in c or "ON" in c.upper()) and "?" not in c for c in cmds):
            if not messagebox.askyesno("Wejście ON", "Sekwencja włącza wejście/zwarcie. Zaciski wolne, Hi-Pot wyłączony?",
                                       icon="warning", parent=self):
                return

        def work():
            out = []
            for c in cmds:
                if c.upper().startswith("WAIT"):
                    time.sleep(float(c.split()[1]) / 1000)
                    out.append((c, "(pauza)"))
                    continue
                if "?" in c:
                    out.append((c, dev.query(c)))
                else:
                    dev.write(c)
                    out.append((c, None))
            return out

        def done(r):
            if isinstance(r, Exception):
                self._out(f"!! {r}", "err")
                return
            for c, resp in r:
                self._out(f">> {c}", "tx")
                if "?" in c:
                    self._out(f"<< {resp if resp is not None else '<brak odpowiedzi>'}", "rx" if resp else "err")
        self._out(f"-- sekwencja: {len(cmds)} komend (WAIT <ms> = pauza)")
        self.bg(work, done)

    def _load_seq(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Tekst", "*.txt"), ("Wszystkie", "*.*")])
        if p:
            with open(p, encoding="utf-8") as f:
                self.txt_seq.delete("1.0", "end")
                self.txt_seq.insert("1.0", f.read())

    def _save_seq(self):
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", filetypes=[("Tekst", "*.txt")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.txt_seq.get("1.0", "end"))

    # ------------------------------------------------------------------ sterowanie
    def _tab_control(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="Sterowanie")
        m = ttk.LabelFrame(f, text="Tryb i wartość zadana", padding=8)
        m.pack(fill="x")
        self.var_mode = tk.StringVar(value="CURR")
        ttk.Label(m, text="Tryb:").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(m, textvariable=self.var_mode, values=MODES, state="readonly", width=8)
        cb.grid(row=0, column=1, padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.lbl_unit.configure(text=MODE_UNITS[self.var_mode.get()][1]))
        ttk.Label(m, text="Wartość:").grid(row=0, column=2, padx=(12, 0))
        self.var_set = tk.StringVar(value="0")
        ttk.Entry(m, textvariable=self.var_set, width=10).grid(row=0, column=3, padx=4)
        self.lbl_unit = ttk.Label(m, text="A", width=3)
        self.lbl_unit.grid(row=0, column=4)
        ttk.Button(m, text="Ustaw", command=self.apply_mode).grid(row=0, column=5, padx=8)

        p = ttk.LabelFrame(f, text="Zabezpieczenia", padding=8)
        p.pack(fill="x", pady=8)
        self.var_ovp, self.var_ocp, self.var_opp, self.var_von = (tk.StringVar() for _ in range(4))
        for i, (lbl, var, unit) in enumerate([("OVP", self.var_ovp, "V"), ("OCP", self.var_ocp, "A"),
                                              ("OPP", self.var_opp, "W"), ("Von", self.var_von, "V")]):
            ttk.Label(p, text=lbl).grid(row=0, column=i * 3, padx=(10 if i else 0, 2))
            ttk.Entry(p, textvariable=var, width=8).grid(row=0, column=i * 3 + 1)
            ttk.Label(p, text=unit).grid(row=0, column=i * 3 + 2, padx=(2, 0))
        ttk.Button(p, text="Ustaw", command=self.apply_prot).grid(row=0, column=12, padx=10)

        io = ttk.LabelFrame(f, text="Wejście", padding=8)
        io.pack(fill="x")
        tk.Button(io, text="INP ON", bg="#2e7d32", fg="white", width=12, command=lambda: self.set_input(True)).pack(side="left")
        tk.Button(io, text="INP OFF", bg="#c00000", fg="white", width=12, command=self.input_off).pack(side="left", padx=6)
        ttk.Button(io, text="SHORT ON", command=lambda: self.set_short(True)).pack(side="left", padx=(20, 0))
        ttk.Button(io, text="SHORT OFF", command=lambda: self.set_short(False)).pack(side="left", padx=4)

        rb = ttk.LabelFrame(f, text="Odczyt ustawień (readback)", padding=8)
        rb.pack(fill="both", expand=True, pady=8)
        ttk.Button(rb, text="Odczytaj wszystko", command=self.readback).pack(anchor="w")
        self.txt_rb = tk.Text(rb, height=10, font=("Consolas", 10), state="disabled")
        self.txt_rb.pack(fill="both", expand=True, pady=4)

    def apply_mode(self):
        dev = self._dev()
        if not dev:
            return
        mode = self.var_mode.get()
        try:
            val = float(self.var_set.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Wartość", "Nieprawidłowa liczba", parent=self)
            return
        cmd, _ = MODE_UNITS[mode]
        self.bg(lambda: (dev.set_mode(mode), dev.write(f"{cmd} {val:g}"), dev.query(f"{cmd}?"))[2],
                lambda r: self.lbl_info.configure(text=f"{mode}: {cmd}? -> {r}"))

    def apply_prot(self):
        dev = self._dev()
        if not dev:
            return
        vals = {}
        for k, var in (("VOLT:PROT", self.var_ovp), ("CURR:PROT", self.var_ocp),
                       ("POW:PROT", self.var_opp), ("VOLT:ON", self.var_von)):
            s = var.get().strip().replace(",", ".")
            if s:
                try:
                    vals[k] = float(s)
                except ValueError:
                    messagebox.showwarning("Wartość", f"{k}: nieprawidłowa liczba", parent=self)
                    return

        def work():
            out = []
            for k, v in vals.items():
                dev.write(f"{k} {v:g}")
                out.append(f"{k}? -> {dev.query(k + '?')}")
            return "  ".join(out)
        self.bg(work, lambda r: self.lbl_info.configure(text=str(r)))

    def set_input(self, on):
        dev = self._dev()
        if not dev:
            return
        if on and not messagebox.askyesno("Wejście ON", "Włączyć wejście obciążenia?\nZaciski wolne / DUT gotowy, "
                                                        "Hi-Pot wyłączony?", icon="warning", parent=self):
            return
        self.bg(lambda: (dev.input_on() if on else dev.input_off(), dev.query("INP?"))[1],
                lambda r: self.lbl_info.configure(text=f"INP? -> {r}"))

    def set_short(self, on):
        dev = self._dev()
        if not dev:
            return
        if on and not messagebox.askyesno("SHORT ON", "Zewrzeć wejście? Tylko przy wolnych zaciskach!",
                                          icon="warning", parent=self):
            return
        self.bg(lambda: (dev.set_short(on), dev.query("INP:SHOR?"))[1],
                lambda r: self.lbl_info.configure(text=f"INP:SHOR? -> {r}"))

    def input_off(self):
        d = self.app.dev
        if d and d.connected:  # zawsze dozwolone, także w trakcie testu
            self.bg(lambda: (d.input_off(), d.query("INP?"))[1],
                    lambda r: self.lbl_info.configure(text=f"INP 0 wysłane, INP? -> {r}"))

    def readback(self):
        dev = self._dev()
        if not dev:
            return
        qs = ["MODE?", "INP?", "INP:SHOR?", "SYST:SENS?", "CURR?", "VOLT?", "RES?", "POW?", "CURR:LIM?",
              "VOLT:PROT?", "CURR:PROT?", "POW:PROT?", "VOLT:ON?", "VOLT:RANG?", "CURR:RANG?",
              "DYN:HIGH?", "DYN:LOW?", "DYN:MODE?", "MEAS:ALL:INFO?"]

        def done(r):
            self.txt_rb.configure(state="normal")
            self.txt_rb.delete("1.0", "end")
            if isinstance(r, Exception):
                self.txt_rb.insert("end", str(r))
            else:
                for qq, a in r:
                    self.txt_rb.insert("end", f"{qq:18s} {a if a is not None else '<brak>'}\n")
            self.txt_rb.configure(state="disabled")
        self.bg(lambda: [(qq, dev.query(qq)) for qq in qs], done)

    # ------------------------------------------------------------------ pomiar live
    def _tab_live(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="Pomiar na żywo")
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Interwał [ms]:").pack(side="left")
        self.var_iv = tk.StringVar(value="500")
        ttk.Entry(top, textvariable=self.var_iv, width=6).pack(side="left", padx=4)
        self.btn_live = ttk.Button(top, text="Start", command=self.toggle_live)
        self.btn_live.pack(side="left", padx=8)
        self.lbl_live_err = ttk.Label(top, text="", foreground="#c00000")
        self.lbl_live_err.pack(side="left")

        g = ttk.Frame(f)
        g.pack(pady=20)
        self.live = {}
        for i, (k, unit) in enumerate((("v", "V"), ("i", "A"), ("p", "W"))):
            tk.Label(g, text=unit, font=("Segoe UI", 14)).grid(row=0, column=i, padx=30)
            lbl = tk.Label(g, text="---", font=("Consolas", 36, "bold"), fg="#0050b0", width=9)
            lbl.grid(row=1, column=i, padx=10)
            self.live[k] = lbl
        flags = ttk.Frame(f)
        flags.pack()
        for k in ("ovp", "ocp", "opp"):
            lbl = tk.Label(flags, text=k.upper(), width=8, bg="#ddd", font=("Segoe UI", 11, "bold"))
            lbl.pack(side="left", padx=6)
            self.live[k] = lbl

    def toggle_live(self):
        if self._live_thread and self._live_thread.is_alive():
            self._live_stop.set()
            self.btn_live.configure(text="Start")
            return
        dev = self._dev()
        if not dev:
            return
        try:
            iv = max(100, int(self.var_iv.get())) / 1000
        except ValueError:
            iv = 0.5
        self._live_stop.clear()
        self.btn_live.configure(text="Stop")

        def loop():
            while not self._live_stop.is_set() and dev.connected:
                if self.app.busy:  # nie przeszkadzaj weryfikacji
                    time.sleep(iv)
                    continue
                try:
                    m = dev.meas_all()
                    self.app.call_in_ui(lambda m=m: self._show_live(m, ""))
                except Exception as e:
                    self.app.call_in_ui(lambda e=e: self._show_live(None, str(e)))
                time.sleep(iv)
        self._live_thread = threading.Thread(target=loop, daemon=True)
        self._live_thread.start()

    def _show_live(self, m, err):
        if not self.winfo_exists():
            return
        self.lbl_live_err.configure(text=err)
        if not m:
            return
        self.live["v"].configure(text=f"{m['v']:.3f}")
        self.live["i"].configure(text=f"{m['i']:.4f}")
        self.live["p"].configure(text=f"{m['p']:.3f}")
        for k in ("ovp", "ocp", "opp"):
            self.live[k].configure(bg="#e53935" if m[k] else "#9ccc65")

    # ------------------------------------------------------------------ diagnostyka
    def _tab_diag(self, nb):
        f = ttk.Frame(nb, padding=10)
        nb.add(f, text="Diagnostyka łącza")
        b = ttk.Frame(f)
        b.pack(fill="x")
        ttk.Button(b, text="Wykryj baudrate (rozłączone)", command=self.detect_baud).pack(side="left")
        ttk.Button(b, text="Test terminatora TX", command=self.test_term).pack(side="left", padx=6)
        ttk.Button(b, text="Opóźnienie (20× *IDN?)", command=self.latency).pack(side="left")
        self.txt_diag = tk.Text(f, font=("Consolas", 10), state="disabled")
        self.txt_diag.pack(fill="both", expand=True, pady=6)
        ttk.Label(f, text="Manual nie podaje parametrów RS232 - jeśli *IDN? nie odpowiada, zacznij od wykrycia "
                          "baudrate. Znalezione ustawienia wpisz w Panelu Admin.", wraplength=820).pack(anchor="w")

    def _diag(self, text):
        self.txt_diag.configure(state="normal")
        self.txt_diag.insert("end", text + "\n")
        self.txt_diag.see("end")
        self.txt_diag.configure(state="disabled")

    def detect_baud(self):
        if self.app.dev and self.app.dev.connected:
            messagebox.showinfo("Rozłącz", "Najpierw rozłącz urządzenie w oknie głównym.", parent=self)
            return
        if self.app._transport_key() != "serial":
            messagebox.showinfo("Interfejs", "Wykrywanie dotyczy tylko RS232 / USB-COM.", parent=self)
            return
        port = self.app._selected_port()
        if not port:
            return
        self._diag(f"Wykrywanie baudrate na {port}…")

        def work():
            found = []
            for baud in (115200, 9600, 19200, 38400, 57600):
                for term in ("\n", "\r\n"):
                    t = SerialTransport(port, baud, tx_term=term, rx_term="\n", timeout_s=0.6,
                                        inter_cmd_delay_ms=20)
                    try:
                        t.open()
                        r = t.query("*IDN?")
                    except TransportError as e:
                        return [("ERR", str(e))]
                    finally:
                        t.close()
                    self.app.call_in_ui(lambda b=baud, tm=term, r=r: self._diag(
                        f"  {b:>6} tx={tm!r:6} -> {r if r else '-'}"))
                    if r:
                        found.append((baud, term, r))
                        return found
            return found

        def done(r):
            if isinstance(r, Exception):
                self._diag(f"Błąd: {r}")
            elif r and r[0][0] == "ERR":
                self._diag(f"Błąd: {r[0][1]}")
            elif r:
                b, term, idn = r[0]
                self._diag(f"ZNALEZIONO: {b} baud, terminator {term!r}: {idn}")
                self.app.var_baud.set(str(b))
            else:
                self._diag("Brak odpowiedzi na żadnej prędkości - sprawdź kabel (prosty/krosowany), port, zasilanie.")
        self.bg(work, done)

    def test_term(self):
        dev = self._dev()
        if not dev:
            return
        t = dev.t

        def work():
            out = []
            orig = t.tx_term
            try:
                for term in ("\n", "\r\n", "\r"):
                    t.tx_term = term
                    out.append((term, t.query("*IDN?")))
            finally:
                t.tx_term = orig
            return out

        def done(r):
            if isinstance(r, Exception):
                self._diag(f"Błąd: {r}")
                return
            for term, resp in r:
                self._diag(f"  TX {term!r:8} -> {resp if resp else '<brak>'}")
        self._diag("Test terminatora TX (*IDN?):")
        self.bg(work, done)

    def latency(self):
        dev = self._dev()
        if not dev:
            return

        def work():
            times, miss = [], 0
            for _ in range(20):
                t0 = time.perf_counter()
                r = dev.query("*IDN?")
                if r is None:
                    miss += 1
                else:
                    times.append((time.perf_counter() - t0) * 1000)
            return times, miss

        def done(r):
            if isinstance(r, Exception):
                self._diag(f"Błąd: {r}")
                return
            times, miss = r
            if times:
                self._diag(f"Opóźnienie *IDN?: min {min(times):.1f} / śr {statistics.mean(times):.1f} / "
                           f"max {max(times):.1f} ms, brak odp.: {miss}/20")
            else:
                self._diag("Brak odpowiedzi na wszystkie zapytania")
        self.bg(work, done)

    def on_close(self):
        self._live_stop.set()
        log.info("Panel ENG zamknięty")
        self.destroy()
