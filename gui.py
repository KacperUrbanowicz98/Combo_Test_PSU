# gui.py
"""Okno główne: wybór profilu i skanowanie SN (bez logowania HRID)."""
import tkinter as tk
from tkinter import messagebox, ttk

from config import APP_NAME, APP_VERSION
from models import PowerSupplyModels
from stats_manager import StatsManager


class ComboTesterApp:

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.test_screen = None
        self.stats = self._make_stats()
        self._setup_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._build_scan_view()

    def _make_stats(self):
        try:
            return StatsManager(log_dir=self.config.resolve_log_dir())
        except Exception as e:
            print(f"[STATS] Nie udało się zainicjować statystyk: {e}")
            return None

    def _setup_window(self):
        title = self.config.WINDOW_TITLE + (" — SYMULACJA" if self.config.SIMULATION else "")
        self.root.title(title)
        try:
            self.root.state("zoomed")
        except Exception:
            self.root.geometry(f"{self.config.WINDOW_WIDTH}x{self.config.WINDOW_HEIGHT}")
        self.root.configure(bg=self.config.COLOR_BG)

    # ------------------------------------------------------------------ #
    def _on_closing(self):
        if self.test_screen is not None and not getattr(self.test_screen, "_closed", True):
            self.test_screen._on_window_close()
            return
        self.close_application()

    def close_application(self):
        if self.stats:
            try:
                self.stats.flush()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # WIDOK SKANOWANIA                                                     #
    # ------------------------------------------------------------------ #
    def _build_scan_view(self):
        self._create_header()
        main_frame = tk.Frame(self.root, bg=self.config.COLOR_BG)
        main_frame.pack(expand=True, fill=tk.BOTH, padx=20, pady=(20, 60))
        self._create_scan_panel(main_frame)
        self._create_footer()

        self._d_press_count = 0
        self._d_press_timer = None
        self.root.bind("<Control-Alt-d>", self._on_config_shortcut)
        self.root.bind("<Control-Alt-D>", self._on_config_shortcut)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_config_shortcut(self, event=None):
        """Ctrl+Alt+D trzy razy w ciągu sekundy -> panel ENG (jak w Hi-Pot PSU)."""
        self._d_press_count += 1
        if self._d_press_timer:
            try:
                self.root.after_cancel(self._d_press_timer)
            except Exception:
                pass
        self._d_press_timer = self.root.after(1000, self._reset_d_counter)
        if self._d_press_count >= 3:
            self._d_press_count = 0
            self.open_eng_panel()

    def _reset_d_counter(self):
        self._d_press_count = 0
        self._d_press_timer = None

    def open_eng_panel(self):
        from eng_panel import EngPanel, ask_password
        level = ask_password(self.root, self.config)
        if not level:
            return
        panel = EngPanel(self.root, self.config, level)
        try:
            self.root.wait_window(panel.window)
        except Exception:
            pass
        self._after_eng_changes()

    def _after_eng_changes(self):
        if self.stats is None:
            self.stats = self._make_stats()
        else:
            try:
                self.stats.set_log_dir(self.config.resolve_log_dir())
            except Exception as e:
                print(f"[STATS] Nie udało się przełączyć ścieżki: {e}")
        try:
            if self.model_combo.winfo_exists():
                self.model_combo["values"] = PowerSupplyModels.get_all_models(enabled_only=True)
                if self.selected_model.get() not in self.model_combo["values"]:
                    self._reset_scan()
        except Exception:
            pass

    def _create_scan_panel(self, parent):
        c = self.config
        center = tk.Frame(parent, bg=c.COLOR_BG)
        center.pack(expand=True)
        panel = tk.Frame(center, bg=c.COLOR_WHITE, relief=tk.RAISED, borderwidth=2)
        panel.pack(padx=50, pady=50)

        tk.Label(panel, text="Wybór profilu i skanowanie SN", bg=c.COLOR_WHITE,
                 fg=c.COLOR_PRIMARY, font=("Arial", 20, "bold")).pack(pady=(30, 20))
        tk.Label(panel, text="Profil testowy (Hi-Pot + obciążenie):", bg=c.COLOR_WHITE,
                 fg="#333333", font=("Arial", 12)).pack(pady=(10, 5))

        self.selected_model = tk.StringVar()
        self.model_combo = ttk.Combobox(panel, textvariable=self.selected_model,
                                        values=PowerSupplyModels.get_all_models(enabled_only=True),
                                        font=("Arial", 13), width=28, state="readonly")
        self.model_combo.pack(pady=(0, 6), padx=50)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        self.model_desc_label = tk.Label(panel, text="", bg=c.COLOR_WHITE, fg="#666666",
                                         font=("Arial", 10))
        self.model_desc_label.pack()
        self.sn_length_label = tk.Label(panel, text="", bg=c.COLOR_WHITE, fg="#666666",
                                        font=("Arial", 10, "italic"))
        self.sn_length_label.pack(pady=(4, 5))

        tk.Label(panel, text="Zeskanuj lub wprowadź numer seryjny:", bg=c.COLOR_WHITE,
                 fg="#333333", font=("Arial", 12)).pack(pady=(10, 5))
        self.serial_entry = tk.Entry(panel, font=("Arial", 16, "bold"), width=30,
                                     justify="center", relief=tk.SOLID, borderwidth=2,
                                     state="disabled")
        self.serial_entry.pack(pady=15, padx=50)
        self.serial_entry.bind("<Return>", lambda e: self._process_serial())

        self.scan_status_label = tk.Label(panel, text="", bg=c.COLOR_WHITE, font=("Arial", 11))
        self.scan_status_label.pack(pady=5)

        self.confirm_button = tk.Button(panel, text="POTWIERDŹ", bg="#AAAAAA", fg=c.COLOR_WHITE,
                                        font=("Arial", 14, "bold"), width=20, height=2,
                                        relief=tk.FLAT, cursor="hand2",
                                        command=self._process_serial, state="disabled")
        self.confirm_button.pack(pady=(10, 30), padx=50)
        self._transition_pending = False
        if not self.model_combo["values"]:
            self.scan_status_label.config(text="Brak włączonych profili — włącz je w panelu ENG",
                                          fg=c.COLOR_ERROR)

    def _reset_scan(self):
        self.selected_model.set("")
        self.sn_length_label.config(text="")
        self.model_desc_label.config(text="")
        self.scan_status_label.config(text="")
        self.serial_entry.config(state="disabled")
        self.confirm_button.config(state="disabled", bg="#AAAAAA")

    def _on_model_selected(self, event=None):
        key = self.selected_model.get()
        info = PowerSupplyModels.get_model_info(key)
        if not info:
            return
        self.serial_entry.config(state="disabled")
        self.confirm_button.config(state="disabled", bg="#AAAAAA")
        self.sn_length_label.config(text="")
        self.scan_status_label.config(text="")
        self._confirm_model_dialog(key, info)

    def _confirm_model_dialog(self, model_key: str, info: dict):
        c = self.config
        dialog = tk.Toplevel(self.root)
        dialog.title("Potwierdzenie profilu")
        dialog.configure(bg=c.COLOR_WHITE)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        w, h = 560, 380
        x = self.root.winfo_screenwidth() // 2 - w // 2
        y = self.root.winfo_screenheight() // 2 - h // 2
        dialog.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

        tk.Frame(dialog, bg=c.COLOR_PRIMARY, height=8).pack(fill=tk.X)
        tk.Label(dialog, text="Czy wybrano prawidłowy profil?", bg=c.COLOR_WHITE,
                 fg=c.COLOR_PRIMARY, font=("Arial", 16, "bold")).pack(pady=(24, 6))
        tk.Label(dialog, text=model_key, bg=c.COLOR_WHITE, fg=c.COLOR_ACCENT,
                 font=("Arial", 24, "bold")).pack(pady=(0, 4))
        desc = info.get("description", "")
        if desc and desc != model_key:
            tk.Label(dialog, text=desc, bg=c.COLOR_WHITE, fg="#666666",
                     font=("Arial", 10)).pack(pady=(0, 8))

        p, ld = info.get("test_params", {}), info.get("load_params", {})
        tk.Label(dialog, text=f"HI-POT:  {p.get('mode', '?')}  {p.get('voltage', '?')} V   "
                              f"limit {p.get('current_limit_low', '?')}–{p.get('current_limit_high', '?')} mA",
                 bg=c.COLOR_WHITE, fg="#333333", font=("Arial", 11, "bold")).pack(pady=(0, 4))
        if ld.get("enabled"):
            load_txt = (f"OBCIĄŻENIE:  {ld.get('v_min')}–{ld.get('v_max')} V   "
                        f"przy {ld.get('i_min')} A i {ld.get('i_max')} A")
        else:
            load_txt = "OBCIĄŻENIE:  wyłączone w profilu (tylko Hi-Pot)"
        tk.Label(dialog, text=load_txt, bg=c.COLOR_WHITE, fg="#333333",
                 font=("Arial", 11, "bold")).pack(pady=(0, 8))
        tk.Frame(dialog, bg="#dddddd", height=1).pack(fill=tk.X, padx=30, pady=(0, 16))

        bf = tk.Frame(dialog, bg=c.COLOR_WHITE)
        bf.pack()

        def on_yes():
            dialog.destroy()
            length = info.get("serial_length", "?")
            lt = " lub ".join(str(x) for x in sorted(length)) if isinstance(length, list) else str(length)
            self.sn_length_label.config(text=f"Wymagana długość SN: {lt} znaków")
            self.model_desc_label.config(text=desc)
            self.serial_entry.config(state="normal")
            self.confirm_button.config(state="normal", bg=c.COLOR_ACCENT)
            self.serial_entry.delete(0, tk.END)
            self.serial_entry.focus()

        def on_no():
            dialog.destroy()
            self._reset_scan()
            self.model_combo.focus()

        tk.Button(bf, text="✓  TAK", bg=c.COLOR_ACCENT, fg=c.COLOR_WHITE,
                  font=("Arial", 13, "bold"), width=12, height=2, relief=tk.FLAT,
                  cursor="hand2", command=on_yes).pack(side=tk.LEFT, padx=10)
        tk.Button(bf, text="✗  NIE", bg=c.COLOR_ERROR, fg=c.COLOR_WHITE,
                  font=("Arial", 13, "bold"), width=12, height=2, relief=tk.FLAT,
                  cursor="hand2", command=on_no).pack(side=tk.LEFT, padx=10)
        dialog.bind("<Return>", lambda e: on_yes())
        dialog.bind("<Escape>", lambda e: on_no())
        dialog.protocol("WM_DELETE_WINDOW", on_no)

    def _process_serial(self):
        if self._transition_pending:
            return
        c = self.config
        key = self.selected_model.get()
        serial = self.serial_entry.get().strip().upper()
        if not key:
            self.scan_status_label.config(text="Najpierw wybierz profil!", fg=c.COLOR_ERROR)
            return
        if not serial:
            self.scan_status_label.config(text="Wprowadź numer seryjny!", fg=c.COLOR_ERROR)
            return
        valid, message = PowerSupplyModels.validate_serial(key, serial)
        if not valid:
            self.scan_status_label.config(text=f"✗ {message}", fg=c.COLOR_ERROR)
            self.serial_entry.delete(0, tk.END)
            self.serial_entry.focus()
            return
        info = PowerSupplyModels.get_model_info(key)
        if not info:
            self.scan_status_label.config(text="✗ Profil zniknął — odśwież listę", fg=c.COLOR_ERROR)
            return
        self._transition_pending = True
        self.confirm_button.config(state="disabled", bg="#AAAAAA")
        self.serial_entry.config(state="disabled")
        self.scan_status_label.config(text=f"✓ Profil: {key} | SN: {len(serial)} znaków — OK",
                                      fg=c.COLOR_ACCENT)
        self.root.after(500, lambda: self._show_test_screen(serial, {"model_key": key, **info}))

    def _show_test_screen(self, serial, model_info):
        from test_screen import TestScreen
        self._transition_pending = False
        self.test_screen = TestScreen(self.root, self.config, serial, model_info,
                                      app_ref=self, stats=self.stats)
        self.test_screen.show()

    def show_scan_screen(self):
        self.test_screen = None
        for w in self.root.winfo_children():
            w.destroy()
        self._build_scan_view()

    # ------------------------------------------------------------------ #
    def _create_header(self):
        c = self.config
        header = tk.Frame(self.root, bg=c.COLOR_PRIMARY, height=70)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=f"Reconext {APP_NAME}", bg=c.COLOR_PRIMARY, fg=c.COLOR_WHITE,
                 font=("Arial", 22, "bold")).pack(side=tk.LEFT, padx=20, pady=15)
        if c.SIMULATION:
            tk.Label(header, text="SYMULACJA", bg="#FF9800", fg="white",
                     font=("Arial", 11, "bold"), padx=8).pack(side=tk.LEFT, pady=22)
        tk.Label(header, text=f"Stanowisko: {c.station}", bg=c.COLOR_PRIMARY, fg=c.COLOR_WHITE,
                 font=("Arial", 12, "bold")).pack(side=tk.RIGHT, padx=20, pady=15)

    def _create_footer(self):
        c = self.config
        footer = tk.Frame(self.root, bg=c.COLOR_PRIMARY, height=40)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        footer.pack_propagate(False)
        tk.Button(footer, text="⚙ Panel ENG", bg=c.COLOR_PRIMARY, fg="#c5d3ee",
                  font=("Arial", 9), relief=tk.FLAT, cursor="hand2", bd=0,
                  activebackground=c.COLOR_PRIMARY,
                  command=self.open_eng_panel).pack(side=tk.LEFT, padx=12)
        tk.Label(footer, text=f"v{APP_VERSION}   Autor: Kacper Urbanowicz", bg=c.COLOR_PRIMARY,
                 fg=c.COLOR_WHITE, font=("Arial", 10, "bold")).pack(side=tk.RIGHT, padx=20, pady=10)


def info_error(parent, title, text):
    messagebox.showerror(title, text, parent=parent)
