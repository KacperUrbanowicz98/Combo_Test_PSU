# eng_panel.py
"""
Panel inżynieryjny (ENG) — dostęp: Ctrl+Alt+D ×3 albo „⚙ Panel ENG” w stopce + hasło.

Zakładki:
  Chroma Hi-Pot   port/baudrate, test połączenia
  OEL8512         port, VID:PID, baudrate, timeout, przerwa, terminator; test, odczyt, INP OFF
  Arduino/STM     port, baudrate, interlock, przekaźniki; test klapy i torów
  Profile         baza profili: włącz/wyłącz, dodaj, edytuj, usuń, test w pętli
  Logi            katalogi raportów Hi-Pot (IFS), obciążenia, testów ENG
  Ogólne          nazwa stanowiska, wariant widoku, autozapis, limit czasu
  Hasła           (tylko Admin) zmiana haseł ENG/Admin
"""
import copy
import csv
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import auth
from config import decode_term, encode_term
from models import HIPOT_REQUIRED, PowerSupplyModels, check_load_params

C_OK, C_ERR, C_WARN = "#4CAF50", "#f44336", "#FF9800"
BAUDS = ["9600", "19200", "38400", "57600", "115200"]
TERMS = ["\\n", "\\r\\n", "\\r"]


def list_com_ports():
    """[(opis, port, vid:pid)]"""
    try:
        from serial.tools import list_ports
        out = []
        for p in sorted(list_ports.comports(), key=lambda x: x.device):
            vp = f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None else ""
            out.append((f"{p.device} — {p.description or ''}" + (f" [{vp}]" if vp else ""), p.device, vp))
        return out
    except Exception:
        return []


def ask_password(root, cfg) -> int:
    """Okno hasła. Zwraca auth.LEVEL_* (0 = brak dostępu)."""
    result = {"level": auth.LEVEL_NONE}
    w = tk.Toplevel(root)
    w.title("Panel ENG — logowanie")
    w.geometry("400x220")
    w.configure(bg=cfg.COLOR_BG)
    w.resizable(False, False)
    w.transient(root)
    w.grab_set()
    f = tk.Frame(w, bg=cfg.COLOR_WHITE, relief=tk.RAISED, borderwidth=2)
    f.pack(expand=True, fill=tk.BOTH, padx=20, pady=20)
    tk.Label(f, text="Panel inżynieryjny", bg=cfg.COLOR_WHITE, fg=cfg.COLOR_PRIMARY,
             font=("Arial", 14, "bold")).pack(pady=(18, 8))
    tk.Label(f, text="Hasło ENG lub Admin:", bg=cfg.COLOR_WHITE, font=("Arial", 11)).pack()
    e = tk.Entry(f, font=("Arial", 12), width=20, justify="center", show="*", relief=tk.SOLID,
                 borderwidth=2)
    e.pack(pady=8)
    e.focus()
    err = tk.Label(f, text="", bg=cfg.COLOR_WHITE, fg=C_ERR, font=("Arial", 9))
    err.pack()

    def ok(_=None):
        lvl = auth.check_level(cfg, e.get())
        print(f"[AUTH] Logowanie ENG: {'OK' if lvl else 'BŁĘDNE HASŁO'}")
        if lvl:
            result["level"] = lvl
            w.destroy()
        else:
            err.config(text="Nieprawidłowe hasło!")
            e.delete(0, tk.END)

    e.bind("<Return>", ok)
    w.bind("<Escape>", lambda _: w.destroy())
    root.wait_window(w)
    return result["level"]


class EngPanel:

    def __init__(self, root, cfg, level):
        self.root, self.cfg, self.level = root, cfg, level
        self.window = tk.Toplevel(root)
        self.window.title("Panel inżynieryjny" + (" (Admin)" if level == auth.LEVEL_ADMIN else ""))
        self.window.configure(bg=cfg.COLOR_BG)
        w, h = 1060, 720
        self.window.geometry(f"{w}x{h}+{max(0, root.winfo_screenwidth() // 2 - w // 2)}+"
                             f"{max(0, root.winfo_screenheight() // 2 - h // 2)}")
        self.window.transient(root)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.vars = {}
        self._fixture = None            # połączenie testowe z Arduino/STM
        self._ports = list_com_ports()
        import queue as _q
        self._q = _q.Queue()            # zadania z wątków tła -> wątek GUI
        self._pump()

        hdr = tk.Frame(self.window, bg=cfg.COLOR_PRIMARY, height=56)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Panel inżynieryjny", bg=cfg.COLOR_PRIMARY, fg="white",
                 font=("Arial", 17, "bold")).pack(side=tk.LEFT, padx=20)
        tk.Label(hdr, text=f"Stanowisko: {cfg.station}", bg=cfg.COLOR_PRIMARY, fg="white",
                 font=("Arial", 11)).pack(side=tk.RIGHT, padx=20)

        self.nb = ttk.Notebook(self.window)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self._tab_chroma()
        self._tab_oel()
        self._tab_fixture()
        self._tab_profiles()
        self._tab_logs()
        self._tab_general()
        if level == auth.LEVEL_ADMIN:
            self._tab_passwords()

        bf = tk.Frame(self.window, bg=cfg.COLOR_BG)
        bf.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Button(bf, text="💾 Zapisz ustawienia", bg=C_OK, fg="white", font=("Arial", 11, "bold"),
                  relief=tk.FLAT, padx=14, pady=4, command=self._save).pack(side=tk.RIGHT, padx=4)
        tk.Button(bf, text="Zamknij", bg="#999", fg="white", font=("Arial", 11, "bold"),
                  relief=tk.FLAT, padx=14, pady=4, command=self._close).pack(side=tk.RIGHT)
        self.save_status = tk.Label(bf, text="", bg=cfg.COLOR_BG, font=("Arial", 10))
        self.save_status.pack(side=tk.LEFT)

    # ------------------------------------------------------------------ #
    # POMOCNICZE                                                           #
    # ------------------------------------------------------------------ #
    def _page(self, title, header):
        outer = tk.Frame(self.nb, bg=self.cfg.COLOR_WHITE)
        self.nb.add(outer, text=f"  {title}  ")
        tk.Label(outer, text=header, bg=self.cfg.COLOR_WHITE, fg=self.cfg.COLOR_PRIMARY,
                 font=("Arial", 13, "bold")).pack(anchor="w", padx=16, pady=(12, 6))
        body = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        body.pack(fill=tk.X, padx=16)
        return outer, body

    def _field(self, body, row, label, attr, kind="entry", values=None, width=28, conv=str):
        tk.Label(body, text=label, bg=self.cfg.COLOR_WHITE, font=("Arial", 10)
                 ).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
        val = getattr(self.cfg, attr)
        if kind == "check":
            var = tk.BooleanVar(value=bool(val))
            tk.Checkbutton(body, variable=var, bg=self.cfg.COLOR_WHITE).grid(row=row, column=1, sticky="w")
        else:
            var = tk.StringVar(value=str(val))
            if kind == "combo":
                ttk.Combobox(body, textvariable=var, values=values or [], width=width
                             ).grid(row=row, column=1, sticky="w")
            else:
                tk.Entry(body, textvariable=var, width=width + 2, relief=tk.SOLID, borderwidth=1
                         ).grid(row=row, column=1, sticky="w")
        self.vars[attr] = (var, conv)
        return var

    def _status(self, parent):
        lb = tk.Label(parent, text="", bg=self.cfg.COLOR_WHITE, font=("Arial", 10), justify="left",
                      wraplength=900)
        lb.pack(anchor="w", padx=16, pady=6)
        return lb

    def _pump(self):
        try:
            if not self.window.winfo_exists():
                return
        except Exception:
            return
        while not self._q.empty():
            fn, args = self._q.get_nowait()
            try:
                fn(*args)
            except Exception as e:
                print(f"[ENG] Błąd callbacku: {e}")
        self.window.after(50, self._pump)

    def _bg(self, fn, done):
        """fn() w wątku tła, done(wynik) w wątku GUI (przez kolejkę — wątki nie dotykają Tk)."""
        def worker():
            try:
                r = fn()
            except Exception as e:
                r = e
            self._q.put((done, (r,)))
        threading.Thread(target=worker, daemon=True).start()

    def _port_values(self):
        return [p[1] for p in self._ports]

    def _btn(self, parent, text, cmd, color=None):
        b = tk.Button(parent, text=text, bg=color or self.cfg.COLOR_PRIMARY, fg="white",
                      font=("Arial", 10, "bold"), relief=tk.FLAT, padx=10, pady=3, cursor="hand2",
                      command=cmd)
        b.pack(side=tk.LEFT, padx=(0, 6))
        return b

    def _apply_vars(self):
        """Zmienne z formularzy -> obiekt Config. Zwraca listę błędów."""
        errors = []
        for attr, (var, conv) in self.vars.items():
            try:
                raw = var.get()
                setattr(self.cfg, attr, conv(raw) if not isinstance(raw, bool) else raw)
            except (TypeError, ValueError):
                errors.append(attr)
        try:
            decode_term(self.cfg.OEL_TERMINATOR)
        except Exception:
            errors.append("OEL_TERMINATOR")
        return errors

    # ------------------------------------------------------------------ #
    # CHROMA                                                               #
    # ------------------------------------------------------------------ #
    def _tab_chroma(self):
        outer, b = self._page("Chroma Hi-Pot", "Połączenie RS232 — Chroma 19052")
        self._field(b, 0, "Port COM:", "DEFAULT_COM_PORT", "combo", self._port_values())
        self._field(b, 1, "Baudrate:", "DEFAULT_BAUDRATE", "combo", BAUDS, conv=int)
        tk.Label(b, text="8N1, bez kontroli przepływu (sterownik Chromy).", bg=self.cfg.COLOR_WHITE,
                 fg="#777", font=("Arial", 9)).grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(anchor="w", padx=16, pady=8)
        st = self._status(outer)
        self._btn(bar, "🔌 Testuj połączenie", lambda: self._test_chroma(st))

    def _test_chroma(self, st):
        self._apply_vars()
        st.config(text="⏳ Łączenie...", fg=C_WARN)

        def work():
            if self.cfg.SIMULATION:
                from simulators import SimChroma
                d = SimChroma()
            else:
                from hipot_device import ChromaHiPotDevice
                d = ChromaHiPotDevice(self.cfg.DEFAULT_COM_PORT, int(self.cfg.DEFAULT_BAUDRATE))
            ok = d.connect()
            idn = d.idn
            d.disconnect()
            return ok, idn

        self._bg(work, lambda r: st.config(
            text=(f"✗ {r}" if isinstance(r, Exception) else
                  f"✓ Połączono: {r[1]}" if r[0] else "✗ Brak odpowiedzi na *IDN?"),
            fg=C_OK if not isinstance(r, Exception) and r[0] else C_ERR))

    # ------------------------------------------------------------------ #
    # OEL8512                                                              #
    # ------------------------------------------------------------------ #
    def _tab_oel(self):
        outer, b = self._page("OEL8512", "Połączenie — OWON OEL8512 (baseline FW V2.0.1)")
        pv = self._field(b, 0, "Port COM:", "OEL_PORT", "combo", self._port_values())
        vv = self._field(b, 1, "VID:PID (auto-wybór portu):", "OEL_VID_PID")
        self._field(b, 2, "Baudrate:", "OEL_BAUDRATE", "combo", BAUDS, conv=int)
        self._field(b, 3, "Timeout odpowiedzi [s]:", "OEL_TIMEOUT", conv=float)
        self._field(b, 4, "Przerwa po zapisie [ms]:", "OEL_DELAY_MS", conv=int)
        self._field(b, 5, "Terminator TX/RX:", "OEL_TERMINATOR", "combo", TERMS)

        def pick_vid(*_):
            for _, dev, vp in self._ports:
                if dev == pv.get() and vp:
                    vv.set(vp)
        pv.trace_add("write", pick_vid)
        tk.Label(b, text="Domyślnie 115200 8N1, terminator \\n, przerwa 60 ms — jak w przebiegu "
                         "referencyjnym weryfikatora.", bg=self.cfg.COLOR_WHITE, fg="#777",
                 font=("Arial", 9)).grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(anchor="w", padx=16, pady=8)
        st = self._status(outer)
        self._btn(bar, "🔌 Testuj (*IDN?)", lambda: self._oel_action(st, "idn"))
        self._btn(bar, "📏 Odczyt V / I", lambda: self._oel_action(st, "meas"))
        self._btn(bar, "⏻ Wejście OFF", lambda: self._oel_action(st, "off"), C_ERR)

    def _resolve_oel_port(self):
        if self.cfg.OEL_VID_PID:
            for _, dev, vp in list_com_ports():
                if vp == self.cfg.OEL_VID_PID:
                    self.cfg.OEL_PORT = dev
                    return

    def _oel_action(self, st, what):
        self._apply_vars()
        self._resolve_oel_port()
        st.config(text="⏳ ...", fg=C_WARN)

        def work():
            from load_tester import OELStation
            s = OELStation(self.cfg)
            ok, info = s.connect()
            if not ok:
                return False, info
            try:
                if what == "meas":
                    v, i, flags = s.measure()
                    info = f"{s.idn}\nV = {v:.3f} V   I = {i:.4f} A   zabezpieczenia: {', '.join(flags) or 'brak'}"
                elif what == "off":
                    info = "INP 0 wysłane, wejście wyłączone"
            finally:
                s.disconnect()
            return True, info

        self._bg(work, lambda r: st.config(
            text=f"✗ {r}" if isinstance(r, Exception) else ("✓ " if r[0] else "✗ ") + str(r[1]),
            fg=C_OK if not isinstance(r, Exception) and r[0] else C_ERR))

    # ------------------------------------------------------------------ #
    # ARDUINO / STM                                                        #
    # ------------------------------------------------------------------ #
    def _tab_fixture(self):
        outer, b = self._page("Arduino/STM", "Interlock klapy + przekaźniki torów HI-POT / OBCIĄŻENIE")
        self._field(b, 0, "Port COM:", "INTERLOCK_PORT", "combo", self._port_values())
        self._field(b, 1, "Baudrate:", "INTERLOCK_BAUDRATE", "combo", BAUDS, conv=int)
        self._field(b, 2, "Interlock klapy aktywny:", "INTERLOCK_ENABLED", "check")
        self._field(b, 3, "Sterowanie przekaźnikami:", "RELAYS_ENABLED", "check")
        self._field(b, 4, "Timeout potwierdzenia PATH [s]:", "RELAY_TIMEOUT", conv=float)
        tk.Label(b, text="UWAGA: wyłączenie interlocka zdejmuje blokadę klapy (start ręczny). "
                         "Bez przekaźników test obciążenia jest zablokowany.\n"
                         "Ręcznie załączony tor wyłącza się po 5 s (watchdog Arduino/STM).",
                 bg=self.cfg.COLOR_WHITE, fg=C_ERR, font=("Arial", 9, "bold")
                 ).grid(row=5, column=0, columnspan=2, sticky="w", pady=4)
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(anchor="w", padx=16, pady=8)
        self.fx_status = self._status(outer)
        self._btn(bar, "🔌 Połącz / rozłącz", self._fixture_toggle)
        self._btn(bar, "Tor OFF", lambda: self._fixture_path("OFF"), "#607D8B")
        self._btn(bar, "Tor HI-POT", lambda: self._fixture_path("HIPOT"), C_ERR)
        self._btn(bar, "Tor OBCIĄŻENIE", lambda: self._fixture_path("LOAD"), "#1565C0")
        self.fx_live = tk.Label(outer, text="Klapa: —   Tor: —", bg="#ECEFF1",
                                font=("Arial", 12, "bold"), padx=10, pady=6)
        self.fx_live.pack(anchor="w", padx=16, pady=6)
        tk.Label(outer, text="Protokół i szkic referencyjny: fixture_controller.ino / README.md.",
                 bg=self.cfg.COLOR_WHITE, fg="#777", font=("Arial", 9)).pack(anchor="w", padx=16)

    def _fixture_live(self):
        fx = self._fixture
        if fx is None or not self.window.winfo_exists():
            return
        lid = {True: "ZAMKNIĘTA", False: "OTWARTA", None: "nieznana"}[fx.is_closed]
        self.fx_live.config(text=f"Klapa: {lid}   Tor: {fx.path}   Port: {'OK' if fx.connected else '✗'}")
        self.window.after(300, self._fixture_live)

    def _fixture_toggle(self):
        if self._fixture is not None:
            fx, self._fixture = self._fixture, None
            threading.Thread(target=fx.disconnect, daemon=True).start()
            self.fx_status.config(text="Rozłączono", fg="#666")
            self.fx_live.config(text="Klapa: —   Tor: —")
            return
        self._apply_vars()
        self.fx_status.config(text="⏳ Łączenie...", fg=C_WARN)

        def work():
            if self.cfg.SIMULATION:
                from simulators import SimFixture
                fx = SimFixture("SIM", self.cfg.INTERLOCK_BAUDRATE, relays_enabled=True)
            else:
                from interlock import InterlockMonitor
                fx = InterlockMonitor(self.cfg.INTERLOCK_PORT, int(self.cfg.INTERLOCK_BAUDRATE),
                                      relays_enabled=True)
            if not fx.connect():
                return None
            fx.start_monitoring()
            return fx

        def done(r):
            if isinstance(r, Exception) or r is None:
                self.fx_status.config(text=f"✗ Brak połączenia z {self.cfg.INTERLOCK_PORT} {r or ''}", fg=C_ERR)
                return
            self._fixture = r
            self.fx_status.config(text="✓ Połączono — stan klapy poniżej", fg=C_OK)
            self._fixture_live()
        self._bg(work, done)

    def _fixture_path(self, path):
        fx = self._fixture
        if fx is None:
            self.fx_status.config(text="Najpierw połącz z Arduino/STM", fg=C_ERR)
            return
        if path == "HIPOT" and not messagebox.askyesno(
                "Tor HI-POT", "Przełączyć zasilacz na tor Hi-Pot?\nTo NIE załącza wysokiego napięcia "
                              "(robi to tylko START w Chromie).", parent=self.window):
            return
        if path == "LOAD" and not messagebox.askyesno(
                "Tor OBCIĄŻENIE", "Przełączyć na tor obciążenia?\nZasilacz w gnieździe dostanie 230 V AC.",
                icon="warning", parent=self.window):
            return
        self.fx_status.config(text=f"⏳ PATH {path}...", fg=C_WARN)
        self._bg(lambda: fx.set_path(path, float(self.cfg.RELAY_TIMEOUT)),
                 lambda r: self.fx_status.config(
                     text=f"✗ {r}" if isinstance(r, Exception) else
                     (f"✓ Potwierdzono: OK PATH {path}" if r[0] else f"✗ {r[1]}"),
                     fg=C_OK if not isinstance(r, Exception) and r[0] else C_ERR))

    # ------------------------------------------------------------------ #
    # PROFILE                                                              #
    # ------------------------------------------------------------------ #
    def _tab_profiles(self):
        outer = tk.Frame(self.nb, bg=self.cfg.COLOR_WHITE)
        self.nb.add(outer, text="  Profile  ")
        top = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        top.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(top, text="Baza profili (Hi-Pot + obciążenie)", bg=self.cfg.COLOR_WHITE,
                 fg=self.cfg.COLOR_PRIMARY, font=("Arial", 13, "bold")).pack(side=tk.LEFT)
        cols = ("en", "hipot", "load", "sn")
        tf = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        tf.pack(fill=tk.BOTH, expand=True, padx=12)
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings", height=14, selectmode="browse")
        self.tree.heading("#0", text="PN")
        self.tree.column("#0", width=150)
        for c, t, w in (("en", "Aktywny", 70), ("hipot", "Hi-Pot", 270), ("load", "Obciążenie", 330),
                        ("sn", "Długość SN", 100)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c in ("en", "sn") else "w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("off", foreground="#999")
        self.tree.bind("<Double-1>", lambda e: self._profile_form("edit"))
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(fill=tk.X, padx=12, pady=8)
        self._btn(bar, "➕ Dodaj", lambda: self._profile_form("add"), C_OK)
        self._btn(bar, "✎ Edytuj", lambda: self._profile_form("edit"))
        self._btn(bar, "📄 Duplikuj", lambda: self._profile_form("copy"))
        self._btn(bar, "⏻ Włącz / wyłącz", self._toggle_profile, "#607D8B")
        self._btn(bar, "🗑 Usuń", self._delete_profile, C_ERR)
        self._btn(bar, "🔁 Test w pętli", self._loop_test, "#6A1B9A")
        self.prof_status = self._status(outer)
        self._load_tree()

    def _load_tree(self):
        self.tree.delete(*self.tree.get_children())
        for k in PowerSupplyModels.get_all_models():
            m = PowerSupplyModels.get_model_info(k)
            p, ld = m["test_params"], m["load_params"]
            hp = (f"{p['mode']} {p['voltage']} V, {p['current_limit_low']}–{p['current_limit_high']} mA, "
                  f"{p['test_time']} s")
            lt = (f"{ld['v_min']}–{ld['v_max']} V @ {ld['i_min']} A / {ld['i_max']} A"
                  if ld.get("enabled") else "wyłączone (tylko Hi-Pot)")
            sn = m.get("serial_length", [])
            sn = ", ".join(str(x) for x in sorted(sn)) if isinstance(sn, list) else str(sn)
            en = m.get("enabled", True)
            self.tree.insert("", "end", iid=k, text=k, values=("✓" if en else "—", hp, lt, sn),
                             tags=() if en else ("off",))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            self.prof_status.config(text="Zaznacz profil na liście", fg=C_ERR)
            return None
        return sel[0]

    def _toggle_profile(self):
        k = self._selected()
        if not k:
            return
        en = not PowerSupplyModels.get_model_info(k).get("enabled", True)
        ok = PowerSupplyModels.set_enabled(k, en)
        self._load_tree()
        self.tree.selection_set(k)
        print(f"[ENG] Profil {k}: {'włączony' if en else 'wyłączony'}")
        self.prof_status.config(text=(f"✓ {k} {'włączony' if en else 'wyłączony'}" if ok
                                      else "✗ Nie udało się zapisać models.json"), fg=C_OK if ok else C_ERR)

    def _delete_profile(self):
        k = self._selected()
        if not k:
            return
        if not messagebox.askyesno("Usuń profil", f"Usunąć profil {k}?\nTej operacji nie można cofnąć.",
                                   icon="warning", parent=self.window):
            return
        ok = PowerSupplyModels.delete_model(k)
        print(f"[ENG] Usunięto profil {k}")
        self._load_tree()
        self.prof_status.config(text=f"✓ Usunięto {k}" if ok else "✗ Błąd zapisu models.json",
                                fg=C_OK if ok else C_ERR)

    def _profile_form(self, mode):
        key = None
        if mode in ("edit", "copy"):
            key = self._selected()
            if not key:
                return
        src = copy.deepcopy(PowerSupplyModels.get_model_info(key)) if key else {
            "name": "", "description": "", "enabled": True, "serial_length": [10],
            "test_params": {"mode": "AC", "voltage": 3000, "voltage_tolerance": 50,
                            "current_limit_high": 2.5, "current_limit_low": 0.3, "ramp_time": 0.0,
                            "test_time": 1.0, "fall_time": 0.0, "frequency": 50, "arc_detection": 0.0},
            "load_params": {"enabled": True, "v_nominal": 12.0, "v_min": 11.4, "v_max": 12.6,
                            "i_min": 0.0, "i_max": 1.0, "power_on_delay": 2.0, "settle_time": 1.0,
                            "samples": 5, "i_tol_pct": 3.0, "i_tol_abs": 0.02}}
        c = self.cfg
        d = tk.Toplevel(self.window)
        d.title({"add": "Nowy profil", "edit": f"Edycja: {key}", "copy": f"Kopia: {key}"}[mode])
        d.configure(bg=c.COLOR_WHITE)
        d.transient(self.window)
        d.grab_set()
        d.geometry(f"+{max(0, self.window.winfo_rootx() + 60)}+{max(0, self.window.winfo_rooty() + 40)}")
        f = tk.Frame(d, bg=c.COLOR_WHITE)
        f.pack(padx=16, pady=12)
        v = {}

        def row(r, col, label, name, value, width=14):
            tk.Label(f, text=label, bg=c.COLOR_WHITE, font=("Arial", 10)).grid(
                row=r, column=col, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=str(value))
            tk.Entry(f, textvariable=var, width=width, relief=tk.SOLID, borderwidth=1).grid(
                row=r, column=col + 1, sticky="w", padx=(0, 18))
            v[name] = var

        def sec(r, text):
            tk.Label(f, text=text, bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY, font=("Arial", 11, "bold")
                     ).grid(row=r, column=0, columnspan=4, sticky="w", pady=(10, 2))

        p, ld = src["test_params"], src["load_params"]
        sn = src.get("serial_length", [])
        sec(0, "Profil")
        row(1, 0, "PN (nazwa):", "name", "" if mode != "edit" else key, 18)
        row(1, 2, "Długość SN (np. 10,21):", "sn",
            ",".join(str(x) for x in (sn if isinstance(sn, list) else [sn])))
        row(2, 0, "Opis:", "description", src.get("description", ""), 40)
        en_var = tk.BooleanVar(value=src.get("enabled", True))
        tk.Checkbutton(f, text="Profil aktywny", variable=en_var, bg=c.COLOR_WHITE).grid(
            row=2, column=2, columnspan=2, sticky="w")
        sec(3, "Hi-Pot (Chroma 19052)")
        tk.Label(f, text="Tryb:", bg=c.COLOR_WHITE).grid(row=4, column=0, sticky="w")
        mode_var = tk.StringVar(value=p.get("mode", "AC"))
        ttk.Combobox(f, textvariable=mode_var, values=["AC", "DC"], width=11, state="readonly"
                     ).grid(row=4, column=1, sticky="w")
        row(4, 2, "Napięcie [V]:", "voltage", p["voltage"])
        row(5, 0, "Tolerancja [V]:", "voltage_tolerance", p.get("voltage_tolerance", 50))
        row(5, 2, "Częstotliwość [Hz]:", "frequency", p.get("frequency", 50))
        row(6, 0, "Prąd LOW [mA]:", "current_limit_low", p["current_limit_low"])
        row(6, 2, "Prąd HIGH [mA]:", "current_limit_high", p["current_limit_high"])
        row(7, 0, "Ramp [s]:", "ramp_time", p["ramp_time"])
        row(7, 2, "Test [s]:", "test_time", p["test_time"])
        row(8, 0, "Fall [s]:", "fall_time", p["fall_time"])
        row(8, 2, "ARC:", "arc_detection", p.get("arc_detection", 0.0))
        sec(9, "Obciążenie (OEL8512, CC — punkty MIN i MAX)")
        ld_en = tk.BooleanVar(value=ld.get("enabled", True))
        tk.Checkbutton(f, text="Test obciążenia włączony", variable=ld_en, bg=c.COLOR_WHITE).grid(
            row=10, column=0, columnspan=2, sticky="w")
        row(10, 2, "V nominal [V]:", "v_nominal", ld.get("v_nominal", 0))
        row(11, 0, "V min [V]:", "v_min", ld["v_min"])
        row(11, 2, "V max [V]:", "v_max", ld["v_max"])
        row(12, 0, "I min [A]:", "i_min", ld["i_min"])
        row(12, 2, "I max [A]:", "i_max", ld["i_max"])
        row(13, 0, "Załączenie [s]:", "power_on_delay", ld.get("power_on_delay", 2.0))
        row(13, 2, "Ustalanie [s]:", "settle_time", ld.get("settle_time", 1.0))
        row(14, 0, "Próbki:", "samples", ld.get("samples", 5))
        row(14, 2, "Tol. prądu [%]:", "i_tol_pct", ld.get("i_tol_pct", 3.0))
        row(15, 0, "Tol. prądu [A]:", "i_tol_abs", ld.get("i_tol_abs", 0.02))
        err = tk.Label(d, text="", bg=c.COLOR_WHITE, fg=C_ERR, font=("Arial", 10), wraplength=640)
        err.pack()

        def save():
            from models import normalize_pn
            name = normalize_pn(v["name"].get())
            if not name:
                err.config(text="PN nie może być pusty")
                return
            if name != key or mode != "edit":
                if name in PowerSupplyModels.MODELS:
                    err.config(text=f"Profil {name} już istnieje")
                    return
            try:
                lengths = sorted({int(x) for x in v["sn"].get().replace(" ", "").split(",") if x})
                assert lengths and all(1 <= x <= 64 for x in lengths)
            except Exception:
                err.config(text="Długość SN: liczby 1–64 rozdzielone przecinkiem (np. 10,21)")
                return
            try:
                hp = {k_: float(v[k_].get().replace(",", ".")) for k_ in
                      ("voltage", "voltage_tolerance", "current_limit_high", "current_limit_low",
                       "ramp_time", "test_time", "fall_time", "frequency", "arc_detection")}
                newld = {k_: float(v[k_].get().replace(",", ".")) for k_ in
                         ("v_nominal", "v_min", "v_max", "i_min", "i_max", "power_on_delay",
                          "settle_time", "i_tol_pct", "i_tol_abs")}
                newld["samples"] = int(float(v["samples"].get()))
            except ValueError:
                err.config(text="Nieprawidłowe wartości liczbowe")
                return
            hp["mode"] = mode_var.get()
            newld["enabled"] = bool(ld_en.get())
            probs = []
            if not (0 < hp["voltage"] <= 6000):
                probs.append("napięcie Hi-Pot 1–6000 V")
            if hp["current_limit_low"] >= hp["current_limit_high"]:
                probs.append("prąd LOW musi być < HIGH")
            if hp["test_time"] <= 0:
                probs.append("czas testu > 0")
            if newld["enabled"]:
                probs += check_load_params(newld)
            if probs:
                err.config(text="Popraw: " + "; ".join(probs))
                return
            if hp["current_limit_low"] == 0 and not messagebox.askyesno(
                    "Limit LOW = 0 mA", "Limit LOW = 0 mA nie wykryje niepodłączonego zasilacza.\n"
                                        "Zapisać mimo to?", parent=d):
                return
            assert all(r in hp for r in HIPOT_REQUIRED)
            prof = {"name": name, "identifier": src.get("identifier", ""),
                    "description": v["description"].get().strip() or name,
                    "enabled": bool(en_var.get()), "serial_length": lengths,
                    "test_params": hp, "load_params": newld}
            if mode == "edit" and name != key:
                PowerSupplyModels.MODELS.pop(key, None)
            PowerSupplyModels.MODELS[name] = prof
            ok = PowerSupplyModels.save()
            print(f"[ENG] Zapis profilu {name} ({mode})")
            d.destroy()
            self._load_tree()
            self.tree.selection_set(name)
            self.prof_status.config(text=f"✓ Zapisano profil {name}" if ok else "✗ Błąd zapisu models.json",
                                    fg=C_OK if ok else C_ERR)

        bf = tk.Frame(d, bg=c.COLOR_WHITE)
        bf.pack(pady=10)
        tk.Button(bf, text="Zapisz", bg=C_OK, fg="white", font=("Arial", 11, "bold"), width=12,
                  relief=tk.FLAT, command=save).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text="Anuluj", bg="#999", fg="white", font=("Arial", 11, "bold"), width=12,
                  relief=tk.FLAT, command=d.destroy).pack(side=tk.LEFT, padx=6)

    def _loop_test(self):
        k = self._selected()
        if not k:
            return
        if self._fixture is not None:     # zwolnij port Arduino (exclusive) przed testem w pętli
            fx, self._fixture = self._fixture, None
            self.window.config(cursor="watch")
            self.window.update_idletasks()
            fx.disconnect()
            self.window.config(cursor="")
            self.fx_status.config(text="Rozłączono (test w pętli)", fg="#666")
        self._apply_vars()
        LoopTestWindow(self.window, self.cfg, k)

    # ------------------------------------------------------------------ #
    # LOGI / OGÓLNE / HASŁA                                                #
    # ------------------------------------------------------------------ #
    def _tab_logs(self):
        outer, b = self._page("Logi", "Miejsca zapisu raportów")
        rows = (("Raporty Hi-Pot (IFS):", "LOG_DIR"), ("Raporty obciążenia:", "LOAD_LOG_DIR"),
                ("Testy w pętli (ENG):", "ENG_LOG_DIR"))
        for r, (label, attr) in enumerate(rows):
            var = self._field(b, r, label, attr, width=70)
            tk.Button(b, text="…", width=3, command=lambda v=var: self._browse(v)).grid(row=r, column=2, padx=4)
        tk.Label(b, text="Raport Hi-Pot ma format Chroma 19052 (bez zmian względem Hi-Pot PSU — odbiór "
                         "przez IFS).\nRaport obciążenia to osobny plik SN_RRRRMMDDggmmss_LOAD.txt — "
                         "domyślnie w osobnym katalogu, żeby IFS go nie pobierał.",
                 bg=self.cfg.COLOR_WHITE, fg="#777", font=("Arial", 9), justify="left"
                 ).grid(row=3, column=0, columnspan=3, sticky="w", pady=6)
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(anchor="w", padx=16, pady=8)
        st = self._status(outer)
        self._btn(bar, "Sprawdź dostępność", lambda: self._check_dirs(st), C_WARN)

    def _browse(self, var):
        p = filedialog.askdirectory(parent=self.window)
        if p:
            var.set(p)

    def _check_dirs(self, st):
        self._apply_vars()
        dirs = [self.cfg.resolve_log_dir(), self.cfg.resolve_load_log_dir(), self.cfg.resolve_eng_log_dir()]

        def work():
            out = []
            for d in dirs:
                try:
                    os.makedirs(d, exist_ok=True)
                    t = os.path.join(d, ".write_test")
                    with open(t, "w") as f:
                        f.write("ok")
                    os.remove(t)
                    out.append(f"✓ {d}")
                except Exception as e:
                    out.append(f"✗ {d}: {e}")
            return out
        self._bg(work, lambda r: st.config(text="\n".join(r) if isinstance(r, list) else f"✗ {r}",
                                           fg=C_OK if isinstance(r, list) and all(x.startswith("✓") for x in r)
                                           else C_ERR))

    def _tab_general(self):
        outer, b = self._page("Ogólne", "Ustawienia ogólne")
        self._field(b, 0, "Nazwa stanowiska (pusto = nazwa PC):", "STATION_NAME")
        tk.Label(b, text="Widok ekranu testu:", bg=self.cfg.COLOR_WHITE).grid(row=1, column=0, sticky="w", pady=3)
        dm = tk.StringVar(value=self.cfg.DISPLAY_MODE)
        fr = tk.Frame(b, bg=self.cfg.COLOR_WHITE)
        fr.grid(row=1, column=1, sticky="w")
        tk.Radiobutton(fr, text="oba testy na ekranie", value="both", variable=dm,
                       bg=self.cfg.COLOR_WHITE).pack(side=tk.LEFT)
        tk.Radiobutton(fr, text="jeden po drugim", value="sequential", variable=dm,
                       bg=self.cfg.COLOR_WHITE).pack(side=tk.LEFT)
        self.vars["DISPLAY_MODE"] = (dm, str)
        self._field(b, 2, "Automatyczny zapis raportów:", "AUTO_SAVE_RESULTS", "check")
        self._field(b, 3, "Limit czasu testu Hi-Pot [s]:", "TEST_TIMEOUT", conv=int)

    def _tab_passwords(self):
        outer, b = self._page("Hasła", "Zmiana haseł (wymaga hasła Admin)")
        lvl = tk.StringVar(value="ENG")
        tk.Label(b, text="Poziom:", bg=self.cfg.COLOR_WHITE).grid(row=0, column=0, sticky="w")
        ttk.Combobox(b, textvariable=lvl, values=["ENG", "Admin"], state="readonly", width=12
                     ).grid(row=0, column=1, sticky="w")
        p1, p2 = tk.StringVar(), tk.StringVar()
        for r, (lab, var) in enumerate((("Nowe hasło:", p1), ("Powtórz:", p2)), 1):
            tk.Label(b, text=lab, bg=self.cfg.COLOR_WHITE).grid(row=r, column=0, sticky="w", pady=3)
            tk.Entry(b, textvariable=var, show="*", width=22).grid(row=r, column=1, sticky="w")
        st = self._status(outer)

        def change():
            if len(p1.get()) < 4:
                st.config(text="✗ Minimum 4 znaki", fg=C_ERR)
                return
            if p1.get() != p2.get():
                st.config(text="✗ Hasła się różnią", fg=C_ERR)
                return
            level = auth.LEVEL_ADMIN if lvl.get() == "Admin" else auth.LEVEL_ENG
            ok = auth.set_password(self.cfg, level, p1.get())
            p1.set("")
            p2.set("")
            st.config(text=f"✓ Hasło {lvl.get()} zmienione" if ok else "✗ Błąd zapisu config.json",
                      fg=C_OK if ok else C_ERR)
        bar = tk.Frame(outer, bg=self.cfg.COLOR_WHITE)
        bar.pack(anchor="w", padx=16, pady=8)
        self._btn(bar, "Zmień hasło", change, C_OK)

    # ------------------------------------------------------------------ #
    def _save(self):
        before = {a: getattr(self.cfg, a) for a in self.vars}
        errors = self._apply_vars()
        if errors:
            self.save_status.config(text=f"✗ Nieprawidłowe wartości: {', '.join(errors)}", fg=C_ERR)
            return
        if self.cfg.DISPLAY_MODE not in ("both", "sequential"):
            self.cfg.DISPLAY_MODE = "both"
        for a, old in before.items():
            new = getattr(self.cfg, a)
            if new != old:
                print(f"[ENG] Zmiana konfiguracji: {a}: {old!r} -> {new!r}")
        ok = self.cfg.save()
        self.save_status.config(text="✓ Zapisano config.json" if ok else "✗ Błąd zapisu config.json",
                                fg=C_OK if ok else C_ERR)

    def _close(self):
        if self._fixture is not None:
            fx, self._fixture = self._fixture, None
            threading.Thread(target=fx.disconnect, daemon=True).start()
        try:
            self.window.grab_release()
            self.window.destroy()
        except Exception:
            pass


# ====================================================================== #
# TEST W PĘTLI                                                            #
# ====================================================================== #
class LoopTestWindow:
    """
    Pełna sekwencja (Hi-Pot + obciążenie) powtarzana N razy na tej samej sztuce
    (np. złoty wzorzec). Wyniki tylko do CSV w katalogu testów ENG — nie do IFS
    i nie do statystyk zmiany.
    """

    def __init__(self, parent, cfg, model_key):
        self.cfg, self.key = cfg, model_key
        self.profile = dict(PowerSupplyModels.get_model_info(model_key), model_key=model_key)
        self.abort = threading.Event()
        self.running = False
        self.rows = []
        import queue as _q
        self._q = _q.Queue()
        c = cfg
        self.w = tk.Toplevel(parent)
        self.w.title(f"Test w pętli — {model_key}")
        self.w.configure(bg=c.COLOR_WHITE)
        self.w.geometry("980x600")
        self.w.transient(parent)
        self.w.grab_set()
        self.w.protocol("WM_DELETE_WINDOW", self._close)

        top = tk.Frame(self.w, bg=c.COLOR_WHITE)
        top.pack(fill=tk.X, padx=12, pady=10)
        tk.Label(top, text=f"Profil {model_key}", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT)
        self.n_var, self.pause_var = tk.StringVar(value="10"), tk.StringVar(value="2")
        for lab, var in (("Cykle:", self.n_var), ("Przerwa [s]:", self.pause_var)):
            tk.Label(top, text=lab, bg=c.COLOR_WHITE).pack(side=tk.LEFT, padx=(18, 4))
            tk.Entry(top, textvariable=var, width=6).pack(side=tk.LEFT)
        self.start_b = tk.Button(top, text="▶ Start", bg=C_OK, fg="white", font=("Arial", 10, "bold"),
                                 relief=tk.FLAT, padx=12, command=self._start)
        self.start_b.pack(side=tk.LEFT, padx=(18, 4))
        self.stop_b = tk.Button(top, text="■ Stop", bg=C_ERR, fg="white", font=("Arial", 10, "bold"),
                                relief=tk.FLAT, padx=12, state="disabled", command=self.abort.set)
        self.stop_b.pack(side=tk.LEFT)
        tk.Label(self.w, text="Sztuka zostaje w gnieździe przez cały test — klapa musi być zamknięta. "
                              "Otwarcie klapy przerywa pętlę. Wyniki nie trafiają do IFS.",
                 bg=c.COLOR_WHITE, fg="#777", font=("Arial", 9)).pack(anchor="w", padx=12)

        cols = ("n", "hp", "hpv", "hpi", "ld", "vmin", "vmax", "imax", "res", "reason")
        heads = ("#", "Hi-Pot", "Vtm [V]", "Im [mA]", "Obciąż.", "V@MIN", "V@MAX", "I@MAX", "Wynik", "Uwagi")
        widths = (40, 70, 80, 80, 70, 80, 80, 80, 70, 280)
        tf = tk.Frame(self.w)
        tf.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings")
        for col, h, wd in zip(cols, heads, widths):
            self.tree.heading(col, text=h)
            self.tree.column(col, width=wd, anchor="center" if col != "reason" else "w")
        self.tree.tag_configure("fail", foreground=C_ERR)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.status = tk.Label(self.w, text="Gotowy", bg=c.COLOR_WHITE, font=("Arial", 10, "bold"),
                               justify="left")
        self.status.pack(anchor="w", padx=12, pady=(0, 10))
        self._pump()

    def _pump(self):
        try:
            if not self.w.winfo_exists():
                return
        except Exception:
            return
        while not self._q.empty():
            fn, a = self._q.get_nowait()
            try:
                fn(*a)
            except Exception as e:
                print(f"[LOOP] Błąd callbacku: {e}")
        self.w.after(50, self._pump)

    def _ui(self, fn, *a):
        self._q.put((fn, a))

    def _start(self):
        try:
            n = max(1, min(1000, int(self.n_var.get())))
            pause = max(0.0, float(self.pause_var.get()))
        except ValueError:
            self.status.config(text="✗ Nieprawidłowa liczba cykli/przerwa", fg=C_ERR)
            return
        self.abort.clear()
        self.running = True
        self.rows = []
        self.tree.delete(*self.tree.get_children())
        self.start_b.config(state="disabled")
        self.stop_b.config(state="normal")
        threading.Thread(target=self._worker, args=(n, pause), daemon=True).start()

    def _worker(self, n, pause):
        from combo_sequence import ComboSequence
        import hipot_runner
        cfg = self.cfg
        ld_on = self.profile["load_params"].get("enabled")
        ch = oel = fx = None
        try:
            self._ui(self.status.config, {"text": "⏳ Łączenie z urządzeniami...", "fg": C_WARN})
            if cfg.SIMULATION:
                import simulators
                ch = simulators.SimChroma()
                simulators.configure_psu(self.profile)
                simulators.SIM["fault"] = None
            else:
                from hipot_device import ChromaHiPotDevice
                ch = ChromaHiPotDevice(cfg.DEFAULT_COM_PORT, int(cfg.DEFAULT_BAUDRATE))
            if not ch.connect() or not hipot_runner.configure(ch, self.profile["test_params"]):
                raise RuntimeError("Chroma: brak połączenia lub konfiguracji")
            if ld_on:
                from load_tester import OELStation
                oel = OELStation(cfg)
                ok, info = oel.connect()
                if not ok:
                    raise RuntimeError(f"OEL8512: {info}")
            if cfg.INTERLOCK_ENABLED or cfg.RELAYS_ENABLED:
                if cfg.SIMULATION:
                    from simulators import SimFixture
                    fx = SimFixture("SIM", cfg.INTERLOCK_BAUDRATE, relays_enabled=cfg.RELAYS_ENABLED)
                else:
                    from interlock import InterlockMonitor
                    fx = InterlockMonitor(cfg.INTERLOCK_PORT, int(cfg.INTERLOCK_BAUDRATE),
                                          relays_enabled=cfg.RELAYS_ENABLED)
                if not fx.connect():
                    raise RuntimeError(f"Arduino/STM ({cfg.INTERLOCK_PORT}): brak połączenia")
                fx.set_on_change(lambda st: st is not True and self.abort.set())
                fx.start_monitoring()
                time.sleep(1.2)
                if cfg.INTERLOCK_ENABLED and fx.is_closed is not True:
                    raise RuntimeError("Klapa nie jest zamknięta")

            for i in range(1, n + 1):
                if self.abort.is_set():
                    break
                self._ui(self.status.config, {"text": f"🔄 Cykl {i}/{n}...", "fg": C_WARN})
                seq = ComboSequence(cfg, self.profile, ch, oel, fx, self.abort)
                r = seq.run()
                self._ui(self._add_row, i, r)
                if r.hipot_status != "PASS" or r.relay_alarm:
                    # Ta sama sztuka w każdym cyklu — po FAIL/NIEWAŻNYM Hi-Pot nie wolno
                    # podawać jej 230 V w kolejnym cyklu.
                    self._ui(self._finish, f"Zatrzymano po cyklu {i}: Hi-Pot {r.hipot_status} — {r.reason}")
                    return
                if i < n and pause:
                    self.abort.wait(pause)
            self._ui(self._finish, "Zatrzymano" if self.abort.is_set() else "Zakończono")
        except Exception as e:
            self._ui(self._finish, f"✗ {e}")
        finally:
            for d in (fx, oel, ch):
                if d is not None:
                    try:
                        d.disconnect()
                    except Exception:
                        pass

    def _add_row(self, i, r):
        pts = {p.name: p for p in (r.load.points if r.load else [])}
        hp = r.hipot
        row = {"cycle": i, "hipot": r.hipot_status, "vtm_v": f"{hp.voltage_v:.0f}" if hp else "",
               "im_ma": f"{hp.current_ma:.3f}" if hp else "", "load": r.load_status,
               "v_min": f"{pts['MIN'].v:.3f}" if "MIN" in pts else "",
               "v_max": f"{pts['MAX'].v:.3f}" if "MAX" in pts else "",
               "i_max": f"{pts['MAX'].i:.3f}" if "MAX" in pts else "",
               "result": r.overall, "reason": r.reason}
        self.rows.append(row)
        self.tree.insert("", "end", values=tuple(row.values()),
                         tags=() if r.overall == "PASS" else ("fail",))
        self.tree.yview_moveto(1.0)

    def _finish(self, text):
        self.running = False
        self.start_b.config(state="normal")
        self.stop_b.config(state="disabled")
        ok = sum(1 for r in self.rows if r["result"] == "PASS")
        summary = f"{text}. Cykle: {len(self.rows)}, PASS: {ok}, inne: {len(self.rows) - ok}"

        def stat(key):
            vals = [float(r[key]) for r in self.rows if r[key] not in ("", None)]
            if not vals:
                return ""
            return f"{key}: min {min(vals):.3f} / śr {sum(vals) / len(vals):.3f} / max {max(vals):.3f}"
        extra = "   ".join(s for s in (stat("im_ma"), stat("v_min"), stat("v_max")) if s)
        path = self._save_csv()
        self.status.config(text=summary + ("\n" + extra if extra else "") +
                           (f"\nCSV: {path}" if path else ""),
                           fg=C_OK if self.rows and ok == len(self.rows) else C_ERR if self.rows else "#666")

    def _save_csv(self):
        if not self.rows:
            return ""
        try:
            d = self.cfg.resolve_eng_log_dir()
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"loop_{self.key}_{datetime.now():%Y%m%d_%H%M%S}.csv")
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                wr = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()), delimiter=";")
                wr.writeheader()
                wr.writerows(self.rows)
            return path
        except Exception as e:
            return f"błąd zapisu CSV: {e}"

    def _close(self):
        if self.running:
            if not messagebox.askyesno("Test w toku", "Przerwać test w pętli?", parent=self.w):
                return
            self.abort.set()
        try:
            self.w.grab_release()
            self.w.destroy()
        except Exception:
            pass


__all__ = ["EngPanel", "ask_password", "encode_term"]
