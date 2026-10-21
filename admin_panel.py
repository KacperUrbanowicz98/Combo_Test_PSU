"""Panel Admin: parametry łącza, ustawienia weryfikacji, zmiana hasła. Zmiany logowane."""
import logging
import tkinter as tk
from tkinter import ttk, messagebox

import auth
from config import decode_term

log = logging.getLogger(__name__)

TERMS = ["\\n", "\\r\\n", "\\r"]


class AdminPanel(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.cfg = app.cfg
        self.title("Panel Admin — ustawienia")
        self.resizable(False, False)
        self.vars = {}
        log.info("Panel Admin otwarty")

        c, t = self.cfg.conn, self.cfg.test
        f1 = ttk.LabelFrame(self, text="Łącze szeregowe / komunikacja", padding=8)
        f1.pack(fill="x", padx=8, pady=6)
        self._row(f1, 0, "Bity danych", ("connection", "bytesize"), c["bytesize"], [7, 8])
        self._row(f1, 1, "Parzystość", ("connection", "parity"), c["parity"], ["N", "E", "O"])
        self._row(f1, 2, "Bity stopu", ("connection", "stopbits"), c["stopbits"], [1, 2])
        self._row(f1, 3, "Terminator TX", ("connection", "tx_terminator"), c["tx_terminator"], TERMS)
        self._row(f1, 4, "Terminator RX", ("connection", "rx_terminator"), c["rx_terminator"], TERMS)
        self._row(f1, 5, "Timeout odpowiedzi [s]", ("connection", "timeout_s"), c["timeout_s"])
        self._row(f1, 6, "Przerwa po komendzie [ms]", ("connection", "inter_cmd_delay_ms"), c["inter_cmd_delay_ms"])
        self._row(f1, 7, "Port po VID:PID (puste = po nr COM)", ("connection", "port_vid_pid"), c["port_vid_pid"])

        f2 = ttk.LabelFrame(self, text="Weryfikacja", padding=8)
        f2.pack(fill="x", padx=8, pady=6)
        self._row(f2, 0, "Tolerancja bezwzględna", ("test", "num_abs_tol"), t["num_abs_tol"])
        self._row(f2, 1, "Tolerancja względna", ("test", "num_rel_tol"), t["num_rel_tol"])
        self._row(f2, 2, "Ponowienia przy timeout", ("test", "retries_on_timeout"), t["retries_on_timeout"])
        self._row(f2, 3, "Folder raportów", ("test", "reports_dir"), t["reports_dir"])
        self._row(f2, 4, "Pauza po *RST [ms]", ("test", "rst_wait_ms"), t["rst_wait_ms"])
        for i, (k, lbl) in enumerate((("remote_before_test", "SYST:REM przed testem"),
                                      ("local_after_test", "SYST:LOC po teście"),
                                      ("rst_after_test", "*RST po teście"),
                                      ("require_input_confirm", "Potwierdzenie przed testami INP/SHORT"))):
            v = tk.BooleanVar(value=bool(t[k]))
            self.vars[("test", k)] = v
            ttk.Checkbutton(f2, text=lbl, variable=v).grid(row=5 + i, column=0, columnspan=2, sticky="w")

        f3 = ttk.LabelFrame(self, text="Zmiana haseł (Admin otwiera też panel ENG)", padding=8)
        f3.pack(fill="x", padx=8, pady=6)
        ttk.Label(f3, text="Poziom").grid(row=0, column=0, sticky="w")
        self.pw_level = tk.StringVar(value="ENG")
        ttk.Combobox(f3, textvariable=self.pw_level, values=["ENG", "Admin"], state="readonly",
                     width=17).grid(row=0, column=1, padx=4, sticky="w")
        self.pw1, self.pw2 = tk.StringVar(), tk.StringVar()
        ttk.Label(f3, text="Nowe hasło").grid(row=1, column=0, sticky="w")
        ttk.Entry(f3, textvariable=self.pw1, show="•", width=20).grid(row=1, column=1, padx=4, pady=2)
        ttk.Label(f3, text="Powtórz").grid(row=2, column=0, sticky="w")
        ttk.Entry(f3, textvariable=self.pw2, show="•", width=20).grid(row=2, column=1, padx=4, pady=2)
        ttk.Button(f3, text="Zmień hasło", command=self.change_pw).grid(row=1, column=2, rowspan=2, padx=8)

        b = ttk.Frame(self, padding=8)
        b.pack(fill="x")
        ttk.Button(b, text="Zapisz", command=self.save).pack(side="right")
        ttk.Button(b, text="Anuluj", command=self.destroy).pack(side="right", padx=6)

    def _row(self, parent, r, label, key, value, choices=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=1)
        v = tk.StringVar(value=str(value))
        if choices:
            w = ttk.Combobox(parent, textvariable=v, values=[str(x) for x in choices], width=22)
        else:
            w = ttk.Entry(parent, textvariable=v, width=25)
        w.grid(row=r, column=1, padx=6, sticky="w")
        self.vars[key] = v

    def save(self):
        changes = []
        try:
            for (sec, k), var in self.vars.items():
                old = self.cfg.data[sec][k]
                raw = var.get()
                if isinstance(old, bool):
                    new = bool(raw)
                elif isinstance(old, int):
                    new = int(raw)
                elif isinstance(old, float):
                    new = float(str(raw).replace(",", "."))
                else:
                    new = str(raw)
                if k.endswith("terminator"):
                    decode_term(new)  # walidacja
                if new != old:
                    changes.append(f"{sec}.{k}: {old!r} -> {new!r}")
                    self.cfg.data[sec][k] = new
        except ValueError as e:
            messagebox.showerror("Błąd", f"Nieprawidłowa wartość: {e}", parent=self)
            return
        self.cfg.save()
        for ch in changes:
            log.warning("ZMIANA KONFIGURACJI: %s", ch)   # ślad audytowy w oel85_verifier.log
        # zastosuj do aktywnego połączenia
        d = self.app.dev
        if d and d.connected:
            c = self.cfg.conn
            d.t.timeout_s = float(c["timeout_s"])
            if d.t.name != "sim":
                d.t.delay_s = int(c["inter_cmd_delay_ms"]) / 1000
            d.t.tx_term = decode_term(c["tx_terminator"])
            d.t.rx_term = decode_term(c["rx_terminator"])
        self.app.log_sys(f"Zapisano ustawienia ({len(changes)} zmian)")
        self.destroy()

    def change_pw(self):
        a, b = self.pw1.get(), self.pw2.get()
        if len(a) < 4:
            messagebox.showwarning("Hasło", "Minimum 4 znaki.", parent=self)
            return
        if a != b:
            messagebox.showwarning("Hasło", "Hasła się różnią.", parent=self)
            return
        level = "admin" if self.pw_level.get() == "Admin" else "eng"
        auth.set_password(self.cfg, level, a)
        self.pw1.set("")
        self.pw2.set("")
        messagebox.showinfo("Hasło", f"Hasło {self.pw_level.get()} zmienione.", parent=self)
