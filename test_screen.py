# test_screen.py
"""
Ekran testu stanowiska Hi-Pot + obciążenie.

Bazuje na test_screen.py z Hi-Pot PSU 1.1.2 (kolejka zadań UI z wątków,
blokada startu bez potwierdzonej konfiguracji, start tylko przy zamkniętej
klapie, przerwanie bez zapisu wyniku, duplikat SN, kopia awaryjna raportu).

Nowe:
  * sekwencja Hi-Pot -> przekaźniki -> obciążenie (combo_sequence.py),
  * dwa warianty widoku: oba testy na ekranie / jeden po drugim (ENG -> Ogólne),
  * status połączeń: Chroma, OEL8512, Arduino/STM + aktywny tor przekaźników,
  * historia 5 ostatnich wyników z wynikiem Hi-Pot, obciążenia i całkowitym,
  * bez logowania HRID — w statystykach nazwa stanowiska.
"""
import os
import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox

import hipot_runner
from combo_sequence import ABORTED, FAIL, INVALID, PASS, SKIP, ComboSequence
from logger import save_load_report, save_report
from shift_stats import ShiftStats

C_OK, C_ERR, C_WARN, C_IDLE = "#4CAF50", "#f44336", "#FF9800", "#9E9E9E"
STATUS_TXT = {PASS: "PASS", FAIL: "FAIL", INVALID: "NIEWAŻNY", ABORTED: "PRZERWANY",
              SKIP: "—", "OFF": "wył."}


def _status_color(s):
    return {PASS: C_OK, FAIL: C_ERR, INVALID: C_WARN, ABORTED: C_WARN}.get(s, "#666666")


class TestScreen:

    def __init__(self, parent, config, serial_number, model_info, app_ref=None, stats=None):
        self.parent = parent
        self.config = config
        self.serial_number = serial_number
        self.model_info = model_info
        self.app_ref = app_ref
        self.stats = stats
        self.station = config.station

        self.chroma = None
        self.oel = None
        self.fixture = None
        self._chroma_ok = False
        self._oel_ok = False
        self._fixture_ok = False

        self.test_running = False
        self._abort = threading.Event()
        self._serial_consumed = False
        self._current_sn_is_duplicate = False
        self._prev_interlock_closed = None
        self._pending_start = False
        self._closed = False
        self._ui_queue = queue.Queue()
        self._pump_id = None
        self._recent_results = []
        self._history_frame = None
        self.shift_stats = None
        self.sn_dialog = None
        self.sn_entry = None
        self.sn_result_label = None
        self.sn_status_lbl = None
        self._prev_close_handler = None
        self._last_overall = None
        self._t0 = 0.0
        self._abort_thread = None

    # ------------------------------------------------------------------ #
    # NARZĘDZIA                                                            #
    # ------------------------------------------------------------------ #
    @property
    def tp(self):
        return self.model_info["test_params"]

    @property
    def ld(self):
        return self.model_info.get("load_params", {})

    @property
    def load_enabled(self) -> bool:
        return bool(self.ld.get("enabled"))

    def _alive(self) -> bool:
        if self._closed:
            return False
        if threading.current_thread() is not threading.main_thread():
            return True
        try:
            return bool(self.parent.winfo_exists())
        except Exception:
            return False

    def _ui(self, func, *args):
        """Zadanie dla wątku GUI (wątki robocze nie dotykają Tk)."""
        if self._closed:
            return
        if threading.current_thread() is threading.main_thread():
            if self._alive():
                try:
                    func(*args)
                except Exception as e:
                    print(f"[UI] Błąd callbacku: {e}")
            return
        self._ui_queue.put((func, args))

    def _pump_ui(self):
        self._pump_id = None
        if self._closed:
            return
        while True:
            try:
                func, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if self._alive():
                    func(*args)
            except Exception as e:
                print(f"[UI] Błąd callbacku z kolejki: {e}")
        if not self._closed:
            try:
                self._pump_id = self.parent.after(40, self._pump_ui)
            except Exception:
                pass

    @staticmethod
    def _cfg(widget, **kw):
        try:
            if widget is not None and widget.winfo_exists():
                widget.config(**kw)
        except Exception:
            pass

    def _interlock_active(self) -> bool:
        return bool(self.fixture is not None and self._fixture_ok and self.config.INTERLOCK_ENABLED)

    def _fixture_needed(self) -> bool:
        return bool(self.config.INTERLOCK_ENABLED or self.config.RELAYS_ENABLED)

    # ------------------------------------------------------------------ #
    # BUDOWA EKRANU                                                        #
    # ------------------------------------------------------------------ #
    def show(self):
        for w in self.parent.winfo_children():
            w.destroy()
        for seq in ("<Control-Alt-d>", "<Control-Alt-D>"):
            try:
                self.parent.unbind(seq)
            except Exception:
                pass

        self._create_header()
        self._create_footer()
        self.main = tk.Frame(self.parent, bg=self.config.COLOR_BG)
        self.main.pack(expand=True, fill=tk.BOTH, padx=16, pady=(10, 8))

        self._create_info_bar()
        self._create_params()
        self._create_live_area()
        self._create_progress()
        self._create_interlock_bar()
        self._create_buttons()
        self._create_history()

        try:
            self._prev_close_handler = self.parent.protocol("WM_DELETE_WINDOW")
        except Exception:
            self._prev_close_handler = None
        self.parent.protocol("WM_DELETE_WINDOW", self._on_window_close)

        self._cfg(self.start_button, state="disabled")
        self._cfg(self.stop_button, state="disabled")
        self._pump_ui()

        try:
            self.shift_stats = ShiftStats(log_dir=self.config.resolve_log_dir(), operator=self.station)
            self.shift_stats.on_rebuilt = lambda: self._ui(self._update_counter)
        except Exception as e:
            self.shift_stats = None
            print(f"[SHIFT] Nie udało się uruchomić licznika zmiany: {e}")
        self._update_counter()

        if self.config.SIMULATION:
            import simulators
            simulators.configure_psu(self.model_info)
            simulators.set_fault_from_serial(self.serial_number)
        self._connect_all()

    def _create_header(self):
        c = self.config
        hf = tk.Frame(self.parent, bg=c.COLOR_PRIMARY, height=64)
        hf.pack(fill=tk.X)
        hf.pack_propagate(False)
        tk.Label(hf, text="Reconext Combo Test PSU", bg=c.COLOR_PRIMARY, fg=c.COLOR_WHITE,
                 font=("Arial", 21, "bold")).pack(side=tk.LEFT, padx=20)
        if c.SIMULATION:
            tk.Label(hf, text="SYMULACJA", bg=C_WARN, fg="white", font=("Arial", 11, "bold"),
                     padx=8).pack(side=tk.LEFT)
        tk.Label(hf, text=f"Stanowisko: {self.station}", bg=c.COLOR_PRIMARY, fg=c.COLOR_WHITE,
                 font=("Arial", 12, "bold")).pack(side=tk.RIGHT, padx=20)
        bb = tk.Frame(hf, bg=c.COLOR_WHITE, padx=1, pady=1)
        bb.pack(side=tk.RIGHT, padx=6)
        self.back_button = tk.Button(bb, text="← Powrót do menu", bg=c.COLOR_PRIMARY,
                                     fg=c.COLOR_WHITE, font=("Arial", 10, "bold"), relief=tk.FLAT,
                                     cursor="hand2", padx=10, pady=4, command=self.go_back)
        self.back_button.pack()
        rb = tk.Frame(hf, bg=c.COLOR_WHITE, padx=1, pady=1)
        rb.pack(side=tk.RIGHT, padx=6)
        self.reconnect_button = tk.Button(rb, text="⟳ Połącz ponownie", bg=c.COLOR_PRIMARY,
                                          fg=c.COLOR_WHITE, font=("Arial", 10, "bold"),
                                          relief=tk.FLAT, cursor="hand2", padx=10, pady=4,
                                          command=self._reconnect)
        self.reconnect_button.pack()

    def _create_footer(self):
        c = self.config
        ff = tk.Frame(self.parent, bg=c.COLOR_PRIMARY, height=34)
        ff.pack(side=tk.BOTTOM, fill=tk.X)
        ff.pack_propagate(False)
        tk.Label(ff, text="Autor: Kacper Urbanowicz", bg=c.COLOR_PRIMARY, fg=c.COLOR_WHITE,
                 font=("Arial", 10, "bold")).pack(side=tk.RIGHT, padx=20)

    def _box(self, parent, **pack):
        f = tk.Frame(parent, bg=self.config.COLOR_WHITE, relief=tk.RAISED, borderwidth=2)
        f.pack(**pack)
        return f

    def _create_info_bar(self):
        c = self.config
        box = self._box(self.main, fill=tk.X, pady=(0, 8))
        inner = tk.Frame(box, bg=c.COLOR_WHITE)
        inner.pack(fill=tk.X, padx=16, pady=8)
        left = tk.Frame(inner, bg=c.COLOR_WHITE)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="Profil:", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(left, text=self.model_info.get("model_key", "?"), bg=c.COLOR_WHITE, fg="#333",
                 font=("Arial", 13, "bold")).grid(row=0, column=1, sticky="w", padx=(6, 24))
        tk.Label(left, text="S/N:", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 11, "bold")).grid(row=0, column=2, sticky="w")
        self.sn_display_label = tk.Label(left, text=self.serial_number, bg="#E3F2FD",
                                         fg="#0D47A1", font=("Consolas", 16, "bold"), padx=8)
        self.sn_display_label.grid(row=0, column=3, sticky="w", padx=6)
        desc = self.model_info.get("description", "")
        tk.Label(left, text=desc, bg=c.COLOR_WHITE, fg="#777", font=("Arial", 9)
                 ).grid(row=1, column=0, columnspan=4, sticky="w")

        right = tk.Frame(inner, bg=c.COLOR_WHITE)
        right.pack(side=tk.RIGHT)
        self.badges = {}
        for key, label in (("chroma", "Chroma 19052"), ("oel", "OEL8512"), ("fixture", "Arduino/STM")):
            lb = tk.Label(right, text=f"● {label}", bg=c.COLOR_WHITE, fg=C_IDLE,
                          font=("Arial", 10, "bold"), padx=6)
            lb.pack(side=tk.LEFT)
            self.badges[key] = (lb, label)
        self.path_label = tk.Label(right, text="Tor: —", bg="#ECEFF1", fg="#333",
                                   font=("Arial", 10, "bold"), padx=8, pady=2)
        self.path_label.pack(side=tk.LEFT, padx=(10, 0))

    def _badge(self, key, state, extra=""):
        lb, label = self.badges[key]
        color = {"ok": C_OK, "err": C_ERR, "busy": C_WARN, "off": C_IDLE}[state]
        suffix = {"ok": "", "err": " ✗", "busy": " …", "off": " (wył.)"}[state]
        self._cfg(lb, text=f"● {label}{suffix}{extra}", fg=color)

    def _create_params(self):
        c = self.config
        box = self._box(self.main, fill=tk.X, pady=(0, 8))
        g = tk.Frame(box, bg=c.COLOR_WHITE)
        g.pack(padx=16, pady=6, fill=tk.X)
        p, ld = self.tp, self.ld

        def item(r, col, lab, val):
            tk.Label(g, text=lab, bg=c.COLOR_WHITE, fg="#666", font=("Arial", 10)
                     ).grid(row=r, column=col, sticky="w", padx=(0, 4))
            tk.Label(g, text=val, bg=c.COLOR_WHITE, fg="#333", font=("Arial", 10, "bold")
                     ).grid(row=r, column=col + 1, sticky="w", padx=(0, 26))

        tk.Label(g, text="HI-POT", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        item(0, 1, "Tryb:", p["mode"])
        item(0, 3, "Napięcie:", f"{p['voltage']} V ±{p.get('voltage_tolerance', 0)} V")
        item(0, 5, "Prąd:", f"{p['current_limit_low']}–{p['current_limit_high']} mA")
        item(0, 7, "Czas:", f"{hipot_runner.total_time(p):g} s")
        tk.Label(g, text="OBCIĄŻENIE", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 10, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 12))
        if self.load_enabled:
            item(1, 1, "Okno V:", f"{ld['v_min']}–{ld['v_max']} V")
            item(1, 3, "Punkty CC:", f"{ld['i_min']} A / {ld['i_max']} A")
            item(1, 5, "Nominal:", f"{ld.get('v_nominal', '?')} V")
            item(1, 7, "Ustalanie:", f"{ld.get('power_on_delay', 2)} s + {ld.get('settle_time', 1)} s/pkt")
        else:
            item(1, 1, "", "wyłączone w profilu — tylko Hi-Pot")

    # ---------------------------- panele na żywo ----------------------- #
    def _create_live_area(self):
        c = self.config
        self.live_area = tk.Frame(self.main, bg=c.COLOR_BG)
        self.live_area.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.seq_mode = self.config.DISPLAY_MODE == "sequential"

        self.step_label = tk.Label(self.live_area, text="", bg=c.COLOR_BG, fg=c.COLOR_PRIMARY,
                                   font=("Arial", 12, "bold"))
        if self.seq_mode:
            self.step_label.pack(anchor="w")

        self.hp_panel = self._make_panel("1. HI-POT", [("NAPIĘCIE", "hp_v", "0 V"),
                                                       ("PRĄD", "hp_i", "0.000 mA"),
                                                       ("CZAS", "hp_t", "0.0 s")])
        self.ld_panel = self._make_panel("2. OBCIĄŻENIE", [("PUNKT", "ld_pt", "—"),
                                                           ("NAPIĘCIE", "ld_v", "0.000 V"),
                                                           ("PRĄD", "ld_i", "0.000 A"),
                                                           ("MOC", "ld_p", "0.00 W")])
        pt = tk.Frame(self.ld_panel, bg=c.COLOR_WHITE)
        pt.pack(pady=(0, 8))
        self.point_labels = {}
        for col, name in enumerate(("MIN", "MAX")):
            lb = tk.Label(pt, text=f"{name}: —", bg="#ECEFF1", fg="#555", font=("Arial", 10, "bold"),
                          padx=10, pady=3, width=34)
            lb.grid(row=0, column=col, padx=4)
            self.point_labels[name] = lb
        if not self.load_enabled:
            self._cfg(self.ld_panel.verdict, text="WYŁĄCZONY", bg="#ECEFF1", fg="#888")
        self._layout_panels(active="hipot")

    def _make_panel(self, title, fields):
        c = self.config
        f = tk.Frame(self.live_area, bg=c.COLOR_WHITE, relief=tk.RAISED, borderwidth=2)
        top = tk.Frame(f, bg=c.COLOR_WHITE)
        top.pack(fill=tk.X, padx=12, pady=(8, 0))
        tk.Label(top, text=title, bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 13, "bold")).pack(side=tk.LEFT)
        f.verdict = tk.Label(top, text="OCZEKUJE", bg="#ECEFF1", fg="#666",
                             font=("Arial", 12, "bold"), padx=12, pady=2)
        f.verdict.pack(side=tk.RIGHT)
        grid = tk.Frame(f, bg=c.COLOR_WHITE)
        grid.pack(expand=True, pady=10)
        big = len(fields) <= 3
        for col, (lab, attr, init) in enumerate(fields):
            cell = tk.Frame(grid, bg=c.COLOR_WHITE)
            cell.grid(row=0, column=col, padx=22 if big else 12)
            tk.Label(cell, text=lab, bg=c.COLOR_WHITE, fg="#666", font=("Arial", 10)).pack()
            lb = tk.Label(cell, text=init, bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                          font=("Arial", 26 if big else 22, "bold"))
            lb.pack(pady=3)
            setattr(self, attr, lb)
        f.reason = tk.Label(f, text="", bg=c.COLOR_WHITE, fg=C_ERR, font=("Arial", 10),
                            wraplength=560, justify="center")
        f.reason.pack(pady=(0, 6))
        return f

    def _layout_panels(self, active: str):
        """Wariant 'both' — obok siebie; 'sequential' — tylko aktywny test."""
        for p in (self.hp_panel, self.ld_panel):
            p.pack_forget()
        if not self.seq_mode:
            self.hp_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
            self.ld_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))
            return
        panel = self.ld_panel if active == "load" else self.hp_panel
        panel.pack(fill=tk.BOTH, expand=True)
        steps = 2 if self.load_enabled else 1
        txt = (f"Krok 2/{steps}: OBCIĄŻENIE" if active == "load"
               else f"Krok 1/{steps}: HI-POT")
        self._cfg(self.step_label, text=txt)

    def _set_verdict(self, panel, status, reason=""):
        colors = {PASS: ("#e8f5e9", C_OK), FAIL: ("#ffebee", C_ERR), INVALID: ("#fff8e1", C_WARN),
                  ABORTED: ("#fff8e1", C_WARN), "RUN": ("#e3f2fd", "#1565C0"),
                  SKIP: ("#ECEFF1", "#888"), "WAIT": ("#ECEFF1", "#666")}
        bg, fg = colors.get(status, ("#ECEFF1", "#666"))
        text = {"RUN": "W TOKU", "WAIT": "OCZEKUJE", SKIP: "POMINIĘTY"}.get(status, STATUS_TXT.get(status, status))
        self._cfg(panel.verdict, text=text, bg=bg, fg=fg)
        self._cfg(panel.reason, text=reason or "")

    def _create_progress(self):
        c = self.config
        pf = tk.Frame(self.main, bg=c.COLOR_BG)
        pf.pack(fill=tk.X, pady=(0, 6))
        self.status_label = tk.Label(pf, text="Gotowy", bg=c.COLOR_BG, fg="#666",
                                     font=("Arial", 12, "bold"))
        self.status_label.pack(pady=(0, 4))
        self.progress_canvas = tk.Canvas(pf, height=22, bg=c.COLOR_WHITE, highlightthickness=1,
                                         highlightbackground="#ccc")
        self.progress_canvas.pack(fill=tk.X)
        self.progress_rect = self.progress_canvas.create_rectangle(0, 0, 0, 22, fill=C_OK, outline="")

    def _progress(self, frac):
        try:
            w = self.progress_canvas.winfo_width()
            self.progress_canvas.coords(self.progress_rect, 0, 0, w * max(0.0, min(1.0, frac)), 22)
        except Exception:
            pass

    def _expected_duration(self):
        t = hipot_runner.total_time(self.tp) + 1.0
        if self.load_enabled:
            ld = self.ld
            t += float(ld.get("power_on_delay", 2)) + 2 * (float(ld.get("settle_time", 1))
                                                           + 0.15 * int(ld.get("samples", 5))) + 2.5
        return max(t, 1.0)

    def _create_interlock_bar(self):
        self.interlock_frame = tk.Frame(self.main, bg="#fff8e1", relief=tk.RAISED, borderwidth=2)
        self.interlock_frame.pack(fill=tk.X, pady=(0, 6))
        self.interlock_label = tk.Label(self.interlock_frame, text="⏳ Łączenie z Arduino/STM...",
                                        bg="#fff8e1", fg=C_WARN, font=("Arial", 11, "bold"))
        self.interlock_label.pack(pady=6)

    def _create_buttons(self):
        c = self.config
        bf = tk.Frame(self.main, bg=c.COLOR_BG)
        bf.pack(fill=tk.X)
        self.start_button = tk.Button(bf, text="START TEST", bg=c.COLOR_ACCENT, fg=c.COLOR_WHITE,
                                      font=("Arial", 16, "bold"), height=2, relief=tk.FLAT,
                                      cursor="hand2", command=self._maybe_start_test)
        self.start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        self.stop_button = tk.Button(bf, text="STOP", bg=c.COLOR_ERROR, fg=c.COLOR_WHITE,
                                     font=("Arial", 16, "bold"), height=2, relief=tk.FLAT,
                                     cursor="hand2", state="disabled", command=self.stop_test)
        self.stop_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)
        self.next_sn_button = tk.Button(bf, text="➜ Następny SN", bg="#607D8B", fg=c.COLOR_WHITE,
                                        font=("Arial", 16, "bold"), height=2, relief=tk.FLAT,
                                        cursor="hand2", state="disabled",
                                        command=self._open_sn_dialog_manually)
        self.next_sn_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))
        if c.SIMULATION:
            self.sim_lid_button = tk.Button(bf, text="SIM: klapa", bg="#795548", fg="white",
                                            font=("Arial", 11, "bold"), height=2, relief=tk.FLAT,
                                            command=self._sim_toggle_lid)
            self.sim_lid_button.pack(side=tk.LEFT, padx=(5, 0))

    def _sim_toggle_lid(self):
        import simulators
        simulators.set_lid(not simulators.SIM["lid"])

    # ---------------------------- historia ----------------------------- #
    def _create_history(self):
        c = self.config
        outer = self._box(self.main, fill=tk.X, pady=(6, 0))
        top = tk.Frame(outer, bg=c.COLOR_WHITE)
        top.pack(fill=tk.X, padx=10, pady=(4, 2))
        tk.Label(top, text="Ostatnie 5 wyników", bg=c.COLOR_WHITE, fg=c.COLOR_PRIMARY,
                 font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        bar = tk.Frame(top, bg=c.COLOR_WHITE)
        bar.pack(side=tk.RIGHT)
        self.lbl_shift = tk.Label(bar, text="Zmiana ?", bg=c.COLOR_WHITE, fg="#888", font=("Arial", 9))
        self.lbl_shift.pack(side=tk.LEFT, padx=(0, 10))
        for txt, attr, fg in (("Razem:", "lbl_total", "#222"), ("✓ PASS:", "lbl_pass", C_OK),
                              ("✗ FAIL:", "lbl_fail", C_ERR), ("RETEST:", "lbl_retests", C_WARN)):
            tk.Label(bar, text=txt, bg=c.COLOR_WHITE, fg="#555", font=("Arial", 9)).pack(side=tk.LEFT)
            lb = tk.Label(bar, text="0", bg=c.COLOR_WHITE, fg=fg, font=("Arial", 9, "bold"))
            lb.pack(side=tk.LEFT, padx=(2, 10))
            setattr(self, attr, lb)
        tk.Button(bar, text="📊 Statystyki", bg="#eeeeee", fg="#555", font=("Arial", 8),
                  relief=tk.FLAT, cursor="hand2", padx=4, pady=1,
                  command=self._show_stats).pack(side=tk.LEFT)
        hdr = tk.Frame(outer, bg=c.COLOR_PRIMARY)
        hdr.pack(fill=tk.X, padx=10)
        self._hist_cols = [("Czas", 9), ("Numer seryjny", 24), ("Profil", 16), ("Hi-Pot", 10),
                           ("Obciążenie", 11), ("Wynik", 10)]
        for text, w in self._hist_cols:
            tk.Label(hdr, text=text, bg=c.COLOR_PRIMARY, fg="white", font=("Arial", 8, "bold"),
                     width=w, pady=2).pack(side=tk.LEFT)
        self._history_frame = tk.Frame(outer, bg=c.COLOR_WHITE)
        self._history_frame.pack(fill=tk.X, padx=10, pady=(1, 5))
        self._refresh_history()

    def _refresh_history(self):
        hf = self._history_frame
        try:
            if not hf or not hf.winfo_exists():
                return
        except Exception:
            return
        for w in hf.winfo_children():
            w.destroy()
        if not self._recent_results:
            tk.Label(hf, text="Brak wyników w tej sesji", bg=self.config.COLOR_WHITE, fg="#aaa",
                     font=("Arial", 8, "italic")).pack(pady=3)
            return
        for idx, e in enumerate(reversed(self._recent_results)):
            bg = "#f5f5f5" if idx % 2 == 0 else self.config.COLOR_WHITE
            row = tk.Frame(hf, bg=bg)
            row.pack(fill=tk.X)
            vals = [(e["time"], "#666"), (e["serial"] + (" ⚠" if e["duplicate"] else ""),
                                          C_WARN if e["duplicate"] else "#333"),
                    (e["model"], "#333"), (STATUS_TXT.get(e["hipot"], e["hipot"]), _status_color(e["hipot"])),
                    (STATUS_TXT.get(e["load"], e["load"]), _status_color(e["load"])),
                    (STATUS_TXT.get(e["result"], e["result"]), _status_color(e["result"]))]
            for (text, fg), (_, w) in zip(vals, self._hist_cols):
                tk.Label(row, text=text, bg=bg, fg=fg, font=("Arial", 8, "bold"), width=w,
                         pady=2).pack(side=tk.LEFT)

    def _add_recent(self, res):
        self._recent_results.append({
            "time": datetime.now().strftime("%H:%M:%S"), "serial": self.serial_number,
            "model": self.model_info["model_key"], "hipot": res.hipot_status,
            "load": res.load_status, "result": res.overall,
            "duplicate": self._current_sn_is_duplicate})
        self._recent_results = self._recent_results[-5:]
        self._refresh_history()

    def _update_counter(self):
        if not self.shift_stats:
            return
        s = self.shift_stats.get_snapshot()
        hours = (f" {s['shift_start']:%H:%M}–{s['shift_end']:%H:%M}"
                 if s.get("shift_start") and s.get("shift_end") else "")
        self._cfg(self.lbl_shift, text=f"Zmiana {s['shift_name']}{hours}")
        self._cfg(self.lbl_total, text=str(s["total"]))
        self._cfg(self.lbl_pass, text=str(s["passed"]))
        self._cfg(self.lbl_fail, text=str(s["failed"]))
        self._cfg(self.lbl_retests, text=str(s["retests"]))

    def _show_stats(self):
        from stats_window import show_stats_window
        show_stats_window(self.parent, self.config)

    # ------------------------------------------------------------------ #
    # POŁĄCZENIA (w tle)                                                   #
    # ------------------------------------------------------------------ #
    def _connect_all(self):
        self._cfg(self.status_label, text="⏳ Łączenie z urządzeniami...", fg=C_WARN)
        self._badge("chroma", "busy")
        self._badge("oel", "busy" if self.load_enabled else "off")
        self._badge("fixture", "busy" if self._fixture_needed() else "off")
        self._cfg(self.reconnect_button, state="disabled")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        cfg = self.config
        # --- Chroma
        if cfg.SIMULATION:
            from simulators import SimChroma
            chroma = SimChroma()
        else:
            from hipot_device import ChromaHiPotDevice
            chroma = ChromaHiPotDevice(port=cfg.DEFAULT_COM_PORT, baudrate=cfg.DEFAULT_BAUDRATE)
        ok = cfg_ok = False
        try:
            ok = chroma.connect()
            if ok:
                cfg_ok = hipot_runner.configure(chroma, self.tp)
        except Exception as e:
            print(f"[HIPOT] Wyjątek przy łączeniu: {e}")
        if not self._alive():
            chroma.disconnect()
            return
        self.chroma = chroma
        self._chroma_ok = bool(ok and cfg_ok)
        self._ui(self._on_chroma_ready, ok, cfg_ok)

        # --- OEL8512
        if self.load_enabled:
            from load_tester import OELStation
            try:
                st = OELStation(cfg)
                oel_ok, info = st.connect()
            except Exception as e:
                st, oel_ok, info = None, False, str(e)
            if not self._alive():
                if st:
                    st.disconnect()
                return
            self.oel = st if oel_ok else None
            self._oel_ok = oel_ok
            self._ui(self._on_oel_ready, oel_ok, info)

        # --- Arduino / STM
        if self._fixture_needed():
            if cfg.SIMULATION:
                from simulators import SimFixture
                fx = SimFixture("SIM", cfg.INTERLOCK_BAUDRATE, relays_enabled=cfg.RELAYS_ENABLED)
            else:
                from interlock import InterlockMonitor
                fx = InterlockMonitor(cfg.INTERLOCK_PORT, cfg.INTERLOCK_BAUDRATE,
                                      relays_enabled=cfg.RELAYS_ENABLED)
            fx_ok = fx.connect()
            if not self._alive():
                fx.disconnect()
                return
            if fx_ok:
                self.fixture = fx
                self._fixture_ok = True
                fx.set_on_change(self._on_fixture_change)
                fx.set_on_path(lambda p: self._ui(self._apply_path, p))
                fx.start_monitoring()
                if cfg.RELAYS_ENABLED:
                    fx.set_path("OFF", timeout=1.0)
            self._ui(self._on_fixture_ready, fx_ok)
        else:
            self._ui(self._interlock_manual_mode, "⚠ Interlock i przekaźniki wyłączone — tryb ręczny")
        self._ui(self._after_connect)

    def _on_chroma_ready(self, ok, cfg_ok):
        if ok and cfg_ok:
            self._badge("chroma", "ok")
        else:
            self._badge("chroma", "err")
            msg = ("✗ Konfiguracja Hi-Pot nie powiodła się — test ZABLOKOWANY" if ok
                   else f"✗ Brak połączenia z Chroma ({self.config.DEFAULT_COM_PORT})")
            self._cfg(self.status_label, text=msg, fg=C_ERR)

    def _on_oel_ready(self, ok, info):
        if ok:
            self._badge("oel", "ok")
        else:
            self._badge("oel", "err")
            self._cfg(self.status_label, text=f"✗ OEL8512 ({self.config.OEL_PORT}): {info}", fg=C_ERR)

    def _on_fixture_ready(self, ok):
        if ok:
            self._badge("fixture", "ok")
            if self.config.INTERLOCK_ENABLED:
                self._cfg(self.interlock_label, text="⏳ Oczekiwanie na stan klapy...", fg=C_WARN,
                          bg="#fff8e1")
            else:
                self._interlock_manual_mode("⚠ Interlock wyłączony — przekaźniki aktywne, start ręczny")
        else:
            self._badge("fixture", "err")
            self._interlock_manual_mode(f"✗ Brak połączenia z Arduino/STM ({self.config.INTERLOCK_PORT})",
                                        True)

    def _after_connect(self):
        self._cfg(self.reconnect_button, state="normal")
        problems = self._start_blockers(ignore_lid=True)
        if not problems:
            self._cfg(self.status_label, text="✓ Urządzenia gotowe — zamknij klapę lub naciśnij START",
                      fg=C_OK)
            if not self._interlock_active():
                self._cfg(self.start_button, state="normal")
            # Pierwszy SN: tylko sprawdzenie duplikatu — start przyciskiem lub zamknięciem klapy.
            self._check_duplicate_bg(self.serial_number, autostart=False)
        elif self.status_label.cget("fg") != C_ERR:
            self._cfg(self.status_label, text="✗ " + problems[0], fg=C_ERR)

    def _reconnect(self):
        if self.test_running:
            return
        self._disconnect_devices()
        self._chroma_ok = self._oel_ok = self._fixture_ok = False
        self._connect_all()

    def _disconnect_devices(self):
        fx, oel, ch = self.fixture, self.oel, self.chroma
        self.fixture = self.oel = self.chroma = None
        for d in (fx, oel, ch):
            if d is not None:
                try:
                    d.disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # INTERLOCK / TOR                                                      #
    # ------------------------------------------------------------------ #
    def _interlock_manual_mode(self, text, is_error=False):
        bg = "#ffebee" if is_error else "#fff8e1"
        self._cfg(self.interlock_label, text=text, fg=C_ERR if is_error else C_WARN, bg=bg)
        self._cfg(self.interlock_frame, bg=bg)

    def _on_fixture_change(self, state):
        """Wątek monitora Arduino: przerwanie sekwencji od razu, bez czekania na pętlę Tk."""
        if self.test_running and (state is None or (state is False and self.config.INTERLOCK_ENABLED)):
            self._abort.set()
        self._ui(self._apply_interlock_state, state)

    def _apply_path(self, path):
        colors = {"OFF": ("#ECEFF1", "#333"), "HIPOT": ("#ffebee", C_ERR), "LOAD": ("#e3f2fd", "#1565C0")}
        bg, fg = colors.get(path, ("#ECEFF1", "#333"))
        txt = {"OFF": "Tor: OFF", "HIPOT": "Tor: HI-POT ⚡", "LOAD": "Tor: OBCIĄŻENIE 230V"}.get(path, path)
        self._cfg(self.path_label, text=txt, bg=bg, fg=fg)

    def _apply_interlock_state(self, closed):
        if closed is None:
            self._cfg(self.interlock_label, text="⚠ Utracono połączenie z Arduino/STM — próba wznowienia",
                      fg=C_WARN, bg="#fff8e1")
            self._cfg(self.interlock_frame, bg="#fff8e1")
            self._badge("fixture", "err")
            self._pending_start = False
            if self.test_running:
                self._abort_test("Utrata interlocka w trakcie testu")
            self._prev_interlock_closed = None
            return
        self._badge("fixture", "ok")
        if not self.config.INTERLOCK_ENABLED:
            return
        if closed:
            self._cfg(self.interlock_label, text="🔒 Klapa ZAMKNIĘTA", fg=C_OK, bg="#e8f5e9")
            self._cfg(self.interlock_frame, bg="#e8f5e9")
            if not self.test_running and self._prev_interlock_closed is False:
                if self._dialog_open():
                    self._arm_next_test_from_dialog()
                else:
                    self._maybe_start_test()
            elif self._pending_start and not self.test_running:
                self._maybe_start_test()
            elif not self.test_running and not self._serial_consumed:
                self._cfg(self.start_button, state="normal")
        else:
            self._cfg(self.interlock_label, text="🔓 Klapa OTWARTA — wyjmij zasilacz i zamknij klapę",
                      fg=C_ERR, bg="#ffebee")
            self._cfg(self.interlock_frame, bg="#ffebee")
            if self.test_running:
                self._abort_test("Klapa otwarta w trakcie testu")
                try:
                    self.parent.bell()
                except Exception:
                    pass
            else:
                self._cfg(self.start_button, state="disabled")
        self._prev_interlock_closed = closed

    # ------------------------------------------------------------------ #
    # START                                                                #
    # ------------------------------------------------------------------ #
    def _start_blockers(self, ignore_lid=False):
        p = []
        if not (self.chroma and self._chroma_ok):
            p.append("Hi-Pot niepołączony lub nieskonfigurowany")
        if self.load_enabled:
            if not (self.oel and self._oel_ok and self.oel.connected):
                p.append("OEL8512 niepołączony — test obciążenia niemożliwy")
            if not self.config.RELAYS_ENABLED:
                p.append("Test obciążenia wymaga przekaźników (ENG → Arduino/STM)")
        if self.config.RELAYS_ENABLED and not (self.fixture and self._fixture_ok):
            p.append("Brak połączenia z Arduino/STM (przekaźniki)")
        if not ignore_lid and self._interlock_active() and self.fixture.is_closed is not True:
            p.append("Zamknij klapę, aby rozpocząć test")
        return p

    def _maybe_start_test(self):
        if self._closed or self.test_running:
            return
        if self._serial_consumed:
            self._cfg(self.status_label, text="➜ Wynik tego SN jest zapisany — kliknij „Następny SN”",
                      fg=C_WARN)
            self._cfg(self.start_button, state="disabled")
            self._pending_start = False
            return
        problems = self._start_blockers()
        if problems:
            lid_only = problems == ["Zamknij klapę, aby rozpocząć test"]
            self._pending_start = lid_only
            self._cfg(self.status_label, text=("🔓 " if lid_only else "✗ ") + problems[0],
                      fg=C_WARN if lid_only else C_ERR)
            if lid_only:
                self._cfg(self.start_button, state="disabled")
            return
        self._pending_start = False
        self.start_test()

    def start_test(self):
        if self._closed or self.test_running:
            return
        t = self._abort_thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)              # poprzednie STOP musi skończyć PATH OFF / INP 0
            if t.is_alive():
                self._cfg(self.status_label, text="⏳ Kończenie poprzedniego zatrzymania — spróbuj ponownie",
                          fg=C_WARN)
                return
        self.test_running = True
        self._abort.clear()
        self._t0 = time.time()
        self._reset_live()
        self._set_verdict(self.hp_panel, "RUN")
        self._set_verdict(self.ld_panel, "WAIT" if self.load_enabled else SKIP)
        self._layout_panels("hipot")
        for w, st in ((self.start_button, "disabled"), (self.stop_button, "normal"),
                      (self.back_button, "disabled"), (self.next_sn_button, "disabled"),
                      (self.reconnect_button, "disabled")):
            self._cfg(w, state=st)
        self._cfg(self.status_label, text="🔄 Test w toku...", fg=C_WARN)
        if self.config.SIMULATION:
            import simulators
            simulators.set_fault_from_serial(self.serial_number)
        threading.Thread(target=self._sequence_worker, daemon=True).start()
        self._tick_progress()

    def _tick_progress(self):
        if not self.test_running or self._closed:
            return
        self._progress((time.time() - self._t0) / self._expected_duration())
        self.parent.after(200, self._tick_progress)

    def _reset_live(self):
        for lb, txt in ((self.hp_v, "0 V"), (self.hp_i, "0.000 mA"), (self.hp_t, "0.0 s"),
                        (self.ld_pt, "—"), (self.ld_v, "0.000 V"), (self.ld_i, "0.000 A"),
                        (self.ld_p, "0.00 W")):
            self._cfg(lb, text=txt)
        for name, lb in self.point_labels.items():
            self._cfg(lb, text=f"{name}: —", bg="#ECEFF1", fg="#555")
        self._progress(0)

    # ------------------------------------------------------------------ #
    # SEKWENCJA (wątek roboczy)                                            #
    # ------------------------------------------------------------------ #
    def _sequence_worker(self):
        seq = ComboSequence(
            self.config, self.model_info, self.chroma, self.oel, self.fixture, self._abort,
            on_phase=lambda s, t: self._ui(self._on_phase, s, t),
            on_hipot_live=lambda v, i, t: self._ui(self._on_hipot_live, v, i, t),
            on_hipot_done=lambda v: self._ui(self._on_hipot_done, v),
            on_load_live=lambda d: self._ui(self._on_load_live, d),
            save_hipot=self._save_hipot, save_load=self._save_load)
        try:
            res = seq.run()
        except Exception as e:
            from combo_sequence import ComboResult
            res = ComboResult(INVALID, f"Błąd sekwencji: {e}")
        self._ui(self._on_sequence_done, res)

    def _on_phase(self, step, text):
        if step == "LOAD":
            self._layout_panels("load")
            self._set_verdict(self.ld_panel, "RUN")
        if step != "END" and text:
            self._cfg(self.status_label, text=f"🔄 {text}", fg=C_WARN)

    def _on_hipot_live(self, v, i, t):
        self._cfg(self.hp_v, text=f"{int(v)} V")
        self._cfg(self.hp_i, text=f"{i:.3f} mA")
        self._cfg(self.hp_t, text=f"{t:.1f} s")

    def _on_hipot_done(self, verdict):
        self._cfg(self.hp_v, text=f"{int(verdict.voltage_v)} V")
        self._cfg(self.hp_i, text=f"{verdict.current_ma:.3f} mA")
        self._set_verdict(self.hp_panel, verdict.result,
                          "" if verdict.result == PASS else verdict.reason)

    def _on_load_live(self, d):
        self._cfg(self.ld_pt, text=f"{d['point']} {d['i_set']:.2f}A" if d["point"] not in ("", "START")
                  else "START")
        self._cfg(self.ld_v, text=f"{d['v']:.3f} V")
        self._cfg(self.ld_i, text=f"{d['i']:.3f} A")
        self._cfg(self.ld_p, text=f"{d['p']:.2f} W")

    # ------------------------------------------------------------------ #
    # RAPORTY (wołane z wątku roboczego)                                   #
    # ------------------------------------------------------------------ #
    def _save_with_fallback(self, fn, kwargs, dir_kw, target_dir):
        try:
            path = fn(**{dir_kw: target_dir}, **kwargs)
            return True, "", path
        except Exception as e:
            print(f"[LOG] Błąd zapisu raportu: {e}")
            try:
                from app_paths import data_path
                fb = data_path("logs_awaryjne")
                path = fn(**{dir_kw: fb}, **kwargs)
                return False, f"{e}\nKopia awaryjna: {fb}", path
            except Exception as e2:
                return False, f"{e} / kopia awaryjna: {e2}", ""

    def _save_hipot(self, verdict):
        if not self.config.AUTO_SAVE_RESULTS:
            return True, "", ""
        p = self.tp
        return self._save_with_fallback(save_report, dict(
            operator=self.station, program=self.model_info["model_key"], serial=self.serial_number,
            mode=p.get("mode", "AC"), vtm=verdict.voltage_v / 1000.0, im=verdict.current_ma,
            low=float(p["current_limit_low"]), high=float(p["current_limit_high"]),
            result=verdict.result, error_code=verdict.error_code),
            "log_dir", self.config.resolve_log_dir())

    def _save_load(self, lv):
        if not self.config.AUTO_SAVE_RESULTS:
            return True, "", ""
        return self._save_with_fallback(save_load_report, dict(
            program=self.model_info["model_key"], serial=self.serial_number, station=self.station,
            load_params=self.ld, points=[p.as_dict() for p in lv.points], result=lv.result,
            error_code=lv.error_code, oel_idn=getattr(self.oel, "idn", "")),
            "log_dir", self.config.resolve_load_log_dir())

    # ------------------------------------------------------------------ #
    # KONIEC SEKWENCJI                                                     #
    # ------------------------------------------------------------------ #
    def _on_sequence_done(self, res):
        self.test_running = False
        self._last_overall = res.overall
        self._progress(1.0 if res.overall in (PASS, FAIL) else 0)
        for w, st in ((self.stop_button, "disabled"), (self.back_button, "normal"),
                      (self.next_sn_button, "normal"), (self.reconnect_button, "normal"),
                      (self.start_button, "disabled")):
            self._cfg(w, state=st)

        # Panele
        if res.hipot is None:
            self._set_verdict(self.hp_panel, res.overall if res.overall in (ABORTED, INVALID) else "WAIT",
                              res.reason)
        if res.load is not None:
            for pr in res.load.points:
                ok = pr.result == PASS
                self._cfg(self.point_labels[pr.name],
                          text=f"{pr.name} {pr.i_set:.2f} A:  {pr.v:.3f} V / {pr.i:.3f} A  "
                               f"{'✓' if ok else '✗'}",
                          bg="#e8f5e9" if ok else "#ffebee", fg=C_OK if ok else C_ERR)
            self._set_verdict(self.ld_panel, res.load.result,
                              "" if res.load.result == PASS else res.load.reason)
        elif self.load_enabled:
            if res.load_status in (INVALID, ABORTED):
                self._set_verdict(self.ld_panel, res.load_status, res.reason)
            elif res.hipot_status in (FAIL, INVALID):
                self._set_verdict(self.ld_panel, SKIP, "Pominięty — Hi-Pot nie przeszedł")
        if self.seq_mode:
            self._layout_panels("load" if res.load is not None else "hipot")
            self._cfg(self.step_label,
                      text=f"Hi-Pot: {STATUS_TXT.get(res.hipot_status)}   |   "
                           f"Obciążenie: {STATUS_TXT.get(res.load_status, res.load_status)}")

        if res.relay_alarm:
            messagebox.showerror("ALARM — przekaźniki", res.reason, parent=self.parent)
        if res.overall == ABORTED:
            saved = " (raport Hi-Pot już zapisany)" if res.hipot_report else ""
            self._cfg(self.status_label, text=f"⛔ {res.reason} — wynik całkowity NIE zapisany{saved}",
                      fg=C_ERR)
            self._cfg(self.start_button, state="normal" if not self._interlock_active() else "disabled")
            return

        if res.overall == INVALID:
            saved_hp = " (raport Hi-Pot zapisany)" if res.hipot_report else ""
            self._cfg(self.status_label, text=f"✗ TEST NIEWAŻNY — powtórz{saved_hp}", fg=C_ERR)
            messagebox.showerror("Test nieważny",
                                 f"{res.reason}\n\nWynik całkowity NIE został zapisany ani policzony"
                                 f"{saved_hp}.\nSprawdź podłączenie i powtórz test.",
                                 parent=self.parent)
            self.parent.after(200, lambda: self.show_result_and_next_serial("NIEWAŻNY"))
            return

        # PASS / FAIL — liczone w statystykach
        self._serial_consumed = True
        is_dup = bool(self._current_sn_is_duplicate)
        if res.overall == PASS:
            self._cfg(self.status_label, text="✓ TEST ZALICZONY (PASS)", fg=C_OK)
        else:
            self._cfg(self.status_label, text=f"✗ TEST NIEZALICZONY (FAIL) — {res.reason}", fg=C_ERR)
        if self.shift_stats:
            try:
                self.shift_stats.add_result(res.overall, is_duplicate=is_dup,
                                            model_key=self.model_info["model_key"])
            except Exception as e:
                print(f"[SHIFT] Błąd zapisu: {e}")
        if self.stats:
            try:
                self.stats.add_result(operator=self.station, model_key=self.model_info["model_key"],
                                      mode=self.tp.get("mode", "AC") + ("+LOAD" if self.load_enabled else ""),
                                      result=res.overall, is_retest=is_dup)
            except Exception as e:
                print(f"[STATS] Błąd zapisu: {e}")
        self._add_recent(res)
        self._update_counter()
        if res.save_errors:
            messagebox.showerror("Błąd zapisu raportu",
                                 "Nie udało się zapisać raportu w docelowej lokalizacji:\n\n"
                                 + "\n\n".join(res.save_errors)
                                 + "\n\nSprawdź dostęp do dysku sieciowego (IFS).", parent=self.parent)
        self.parent.after(1200, lambda: self.show_result_and_next_serial(res.overall))

    # ------------------------------------------------------------------ #
    # PRZERWANIE                                                           #
    # ------------------------------------------------------------------ #
    def stop_test(self):
        self._abort_test("Test przerwany przez operatora")

    def _abort_test(self, reason):
        """Wysokie napięcie OFF, wejście OEL OFF, tor OFF — natychmiast, w tle."""
        was = self.test_running
        self._abort.set()
        self._cfg(self.stop_button, state="disabled")
        if was:
            self._cfg(self.status_label, text=f"⛔ {reason} — zatrzymywanie...", fg=C_ERR)
        ch, oel, fx = self.chroma, self.oel, self.fixture

        def worker():
            for fn in (lambda: ch and ch.stop_test(), lambda: oel and oel.safe_off(),
                       lambda: fx and self.config.RELAYS_ENABLED and fx.set_path("OFF", timeout=1.0)):
                try:
                    fn()
                except Exception as e:
                    print(f"[ABORT] {e}")
        self._abort_thread = threading.Thread(target=worker, daemon=True)
        self._abort_thread.start()

    # ------------------------------------------------------------------ #
    # DUPLIKAT SN                                                          #
    # ------------------------------------------------------------------ #
    def _check_duplicate_bg(self, serial, autostart=True):
        def worker():
            try:
                dup = self.check_serial_duplicate(serial)
            except Exception as e:
                print(f"[DUP] {e}")
                dup = {"found": False}
            self._current_sn_is_duplicate = bool(dup.get("found"))
            self._ui(self._after_duplicate_check, serial, dup, autostart)
        threading.Thread(target=worker, daemon=True).start()

    def check_serial_duplicate(self, serial):
        empty = {"found": False}
        for e in self._recent_results:
            if e["serial"].upper() == serial.upper():
                return {"found": True, "where": "session", "last_time": e["time"],
                        "last_result": e["result"]}
        log_dir = self.config.resolve_log_dir()
        if not os.path.isdir(log_dir):
            return empty
        ss = self.shift_stats
        start, end = (ss.shift_start, ss.shift_end) if ss else (None, None)
        pat = re.compile(r'^' + re.escape(serial.upper()) + r'_(\d{14})(?:_\d+)?\.txt$', re.IGNORECASE)
        matches = []
        try:
            for fn in os.listdir(log_dir):
                m = pat.match(fn)
                if not m:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
                except ValueError:
                    continue
                if start and end and not (start <= ts < end):
                    continue
                matches.append(ts)
        except Exception as e:
            print(f"[DUP] Błąd przeglądania logów: {e}")
            return empty
        if not matches:
            return empty
        return {"found": True, "where": "logs", "last_time": max(matches).strftime("%d.%m.%Y %H:%M"),
                "last_result": None}

    def _after_duplicate_check(self, serial, dup, autostart=True):
        if self._closed or self.test_running:
            return
        if not dup.get("found"):
            if autostart:
                self._maybe_start_test()
            return
        where = "tej sesji" if dup.get("where") == "session" else f"logów Hi-Pot ({dup.get('last_time')})"
        last = f" (wynik: {STATUS_TXT.get(dup.get('last_result'), dup.get('last_result'))})" \
            if dup.get("last_result") else ""
        self._cfg(self.status_label, text=f"⚠ DUPLIKAT: SN {serial} był już testowany — {where}{last}",
                  fg="#E65100")
        if messagebox.askyesno("Duplikat SN!", f"SN {serial} był już testowany w tej zmianie!\n"
                                               f"{where}{last}\n\nNa pewno kontynuować?",
                               parent=self.parent):
            if autostart:
                self._maybe_start_test()
        else:
            self._current_sn_is_duplicate = False
            self._serial_consumed = True
            self._cfg(self.status_label, text="Test anulowany — zeskanuj inny numer seryjny", fg="#666")
            self._open_sn_dialog_manually()

    # ------------------------------------------------------------------ #
    # OKNO NASTĘPNEGO SN                                                   #
    # ------------------------------------------------------------------ #
    def _dialog_open(self):
        try:
            return self.sn_dialog is not None and self.sn_dialog.winfo_exists()
        except Exception:
            return False

    def _open_sn_dialog_manually(self):
        if self._dialog_open():
            self.sn_dialog.lift()
            return
        self.show_result_and_next_serial(STATUS_TXT.get(self._last_overall, "—")
                                         if self._last_overall else "—")

    def _arm_next_test_from_dialog(self):
        if not self._dialog_open():
            return False
        from models import PowerSupplyModels
        new_sn = self.sn_entry.get().strip().upper()
        valid, msg = PowerSupplyModels.validate_serial(self.model_info["model_key"], new_sn)
        if not valid:
            self._cfg(self.sn_status_lbl, text=f"✗ {msg}", fg=C_ERR)
            self.sn_entry.focus()
            return False
        self._current_sn_is_duplicate = False
        self._serial_consumed = False
        self.serial_number = new_sn
        self._cfg(self.sn_display_label, text=new_sn)
        self._reset_live()
        self._set_verdict(self.hp_panel, "WAIT")
        self._set_verdict(self.ld_panel, "WAIT" if self.load_enabled else SKIP)
        self._layout_panels("hipot")
        self._cfg(self.status_label, text="Sprawdzanie numeru seryjnego...", fg="#666")
        try:
            self.sn_dialog.grab_release()
            self.sn_dialog.destroy()
        except Exception:
            pass
        self.sn_dialog = None
        self._check_duplicate_bg(new_sn)
        return True

    def show_result_and_next_serial(self, result_text):
        if self._closed:
            return
        c = self.config
        color = C_OK if result_text == "PASS" else C_ERR if result_text == "FAIL" else "#666"
        if self._dialog_open():
            self._cfg(self.sn_result_label, text=f"Ostatni wynik: {result_text}", fg=color)
            self.sn_entry.delete(0, tk.END)
            self.sn_entry.focus()
            return
        d = tk.Toplevel(self.parent)
        d.title("Następny numer seryjny")
        d.geometry("460x370")
        d.configure(bg=c.COLOR_WHITE)
        d.transient(self.parent)
        d.grab_set()
        d.resizable(False, False)
        d.protocol("WM_DELETE_WINDOW", self._close_sn_dialog)
        d.update_idletasks()
        d.geometry(f"+{max(0, d.winfo_screenwidth() // 2 - 230)}+{max(0, d.winfo_screenheight() // 2 - 185)}")
        self.sn_dialog = d
        self.sn_result_label = tk.Label(d, text=f"Ostatni wynik: {result_text}", bg=c.COLOR_WHITE,
                                        fg=color, font=("Arial", 13, "bold"))
        self.sn_result_label.pack(pady=(15, 5))
        tk.Frame(d, bg="#ccc", height=1).pack(fill=tk.X, padx=20, pady=(0, 15))
        tk.Label(d, text="Zeskanuj kolejny numer seryjny:", bg=c.COLOR_WHITE, fg="#333",
                 font=("Arial", 11, "bold")).pack(pady=(0, 5))
        self.sn_entry = tk.Entry(d, font=("Arial", 14, "bold"), width=28, justify="center",
                                 relief=tk.SOLID, borderwidth=2)
        self.sn_entry.pack(pady=5, padx=30)
        self.sn_entry.focus()
        hint = ("⬆ Zeskanuj SN i zamknij klapę aby rozpocząć test" if self._interlock_active()
                else "⬆ Zeskanuj SN i naciśnij ENTER aby rozpocząć test")
        self.sn_status_lbl = tk.Label(d, text=hint, bg=c.COLOR_WHITE, fg="#888", font=("Arial", 9))
        self.sn_status_lbl.pack()
        row = tk.Frame(d, bg=c.COLOR_WHITE)
        row.pack(pady=15)
        tk.Button(row, text="▶ Zatwierdź SN", bg=c.COLOR_ACCENT, fg="white", font=("Arial", 11, "bold"),
                  width=16, relief=tk.FLAT, cursor="hand2",
                  command=self._arm_next_test_from_dialog).pack(side=tk.LEFT, padx=5)
        tk.Button(row, text="Powrót do menu", bg=c.COLOR_PRIMARY, fg="white", font=("Arial", 11, "bold"),
                  width=16, relief=tk.FLAT, cursor="hand2",
                  command=self._back_to_menu_from_dialog).pack(side=tk.LEFT, padx=5)
        tk.Button(d, text="📊 Statystyki", bg="#607D8B", fg="white", font=("Arial", 10, "bold"),
                  width=16, relief=tk.FLAT, cursor="hand2",
                  command=self._stats_from_dialog).pack(pady=(0, 10))
        self.sn_entry.bind("<Return>", lambda e: self._arm_next_test_from_dialog())

    def _close_sn_dialog(self):
        if self._dialog_open():
            try:
                self.sn_dialog.grab_release()
                self.sn_dialog.destroy()
            except Exception:
                pass
        self.sn_dialog = None
        if not self.test_running:
            self._cfg(self.next_sn_button, state="normal")
            self._cfg(self.back_button, state="normal")
            if self._serial_consumed:
                self._cfg(self.start_button, state="disabled")
                self._cfg(self.status_label, text="➜ Kliknij „Następny SN”, aby zeskanować kolejną sztukę",
                          fg="#666")

    def _stats_from_dialog(self):
        self._close_sn_dialog()
        self._show_stats()

    def _back_to_menu_from_dialog(self):
        self._close_sn_dialog()
        self._cleanup_and_go_back()

    # ------------------------------------------------------------------ #
    # ZAMYKANIE                                                            #
    # ------------------------------------------------------------------ #
    def go_back(self):
        if self.test_running:
            messagebox.showwarning("Test w toku", "Nie można wrócić do menu podczas testu!\n"
                                                  "Zatrzymaj test przyciskiem STOP.", parent=self.parent)
            return
        self._cleanup_and_go_back()

    def _on_window_close(self):
        if self.test_running:
            if not messagebox.askyesno("Test w toku", "Test jest w toku. Zamknąć aplikację?\n"
                                                      "HV, obciążenie i tory zostaną wyłączone.",
                                       parent=self.parent):
                return
            self._abort_test("Zamknięcie aplikacji")
            time.sleep(0.3)
        self._shutdown_hardware()
        if self.app_ref is not None:
            self.app_ref.close_application()
        else:
            self.parent.destroy()

    def _shutdown_hardware(self):
        self._closed = True
        self._abort.set()
        if self._pump_id is not None:
            try:
                self.parent.after_cancel(self._pump_id)
            except Exception:
                pass
        if self.shift_stats:
            try:
                self.shift_stats.stop()
            except Exception:
                pass
        self._disconnect_devices()

    def _cleanup_and_go_back(self):
        try:
            if self._prev_close_handler:
                self.parent.protocol("WM_DELETE_WINDOW", self._prev_close_handler)
        except Exception:
            pass
        self._shutdown_hardware()
        if self.app_ref:
            self.app_ref.show_scan_screen()
