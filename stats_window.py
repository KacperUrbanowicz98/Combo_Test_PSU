# stats_window.py
"""
Okno statystyk - dzień produkcyjny (06:00-06:00) albo jedna zmiana, wszystkie stanowiska
zapisujące do tego samego katalogu statystyk. Dane: stats_store (te same co dashboard).

Liczone osobno: COMBO (wynik całkowity), OBCIĄŻENIE, HI-POT - testy / PASS / FAIL / yield.
"""
import threading
import tkinter as tk
from datetime import timedelta
from tkinter import ttk

import stats_store

C_OK, C_ERR, C_WARN = "#2E7D32", "#C62828", "#E65100"
TYPES = (("combo", "COMBO (całość)"), ("load", "OBCIĄŻENIE"), ("hipot", "HI-POT"))
COLS = (("tests", "Testy", 60), ("pass", "PASS", 60), ("fail", "FAIL", 60), ("yield", "Yield", 70),
        ("l_fail", "Obc. FAIL", 80), ("h_fail", "Hi-Pot FAIL", 85), ("retest", "Retest", 65),
        ("fpy", "FPY", 70))


def _pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "-"


def _row_values(s):
    cb = s["combo"]
    return (cb["tests"], cb["pass"], cb["fail"], _pct(cb["pass"], cb["tests"]),
            s["load"]["fail"], s["hipot"]["fail"], s["retest"],
            _pct(s["first_pass"], s["first_tests"]))


def show_stats_window(parent, config):
    c = config
    stats_dir = config.resolve_stats_dir()
    state = {"day": stats_store.production_day(), "shift": "Dzień", "result": None}

    win = tk.Toplevel(parent)
    win.title("Statystyki")
    win.configure(bg=c.COLOR_BG)
    win.geometry(f"1000x760+{max(0, win.winfo_screenwidth() // 2 - 500)}+"
                 f"{max(0, win.winfo_screenheight() // 2 - 380)}")
    win.transient(parent)
    win.grab_set()

    hdr = tk.Frame(win, bg=c.COLOR_PRIMARY, height=55)
    hdr.pack(fill=tk.X)
    hdr.pack_propagate(False)
    tk.Label(hdr, text="Statystyki", bg=c.COLOR_PRIMARY, fg="white",
             font=("Arial", 17, "bold")).pack(side=tk.LEFT, padx=20)
    tk.Button(hdr, text="Zamknij", bg=c.COLOR_ERROR, fg="white", font=("Arial", 10, "bold"),
              relief=tk.FLAT, cursor="hand2", command=win.destroy).pack(side=tk.RIGHT, padx=15, pady=12)

    # ---- filtry: dzień + zmiana
    bar = tk.Frame(win, bg=c.COLOR_BG)
    bar.pack(fill=tk.X, padx=12, pady=(10, 4))
    day_lbl = tk.Label(bar, text="", bg=c.COLOR_BG, fg=c.COLOR_PRIMARY, font=("Arial", 13, "bold"),
                       width=26)

    def nav(delta):
        state["day"] = state["day"] + timedelta(days=delta) if delta else stats_store.production_day()
        load()

    for txt, d in (("◀", -1),):
        tk.Button(bar, text=txt, width=3, command=lambda d=d: nav(d)).pack(side=tk.LEFT)
    day_lbl.pack(side=tk.LEFT, padx=6)
    tk.Button(bar, text="▶", width=3, command=lambda: nav(1)).pack(side=tk.LEFT)
    tk.Button(bar, text="Dziś", command=lambda: nav(0)).pack(side=tk.LEFT, padx=(8, 20))
    shift_var = tk.StringVar(value="Dzień")
    for name in ("Dzień", "I", "II", "III"):
        tk.Radiobutton(bar, text="Cały dzień" if name == "Dzień" else f"Zmiana {name}",
                       value=name, variable=shift_var, indicatoron=False, padx=10, pady=3,
                       bg="white", selectcolor="#cfe0f7", font=("Arial", 10),
                       command=lambda: (state.update(shift=shift_var.get()), render())
                       ).pack(side=tk.LEFT, padx=2)
    tk.Button(bar, text="⟳ Odśwież", command=lambda: load()).pack(side=tk.RIGHT)
    status = tk.Label(win, text="", bg=c.COLOR_BG, fg="#666", font=("Arial", 9))
    status.pack(anchor="w", padx=14)

    # ---- kafelki COMBO / OBCIĄŻENIE / HI-POT
    tiles_f = tk.Frame(win, bg=c.COLOR_BG)
    tiles_f.pack(fill=tk.X, padx=10, pady=4)
    tiles = {}
    for i, (key, title) in enumerate(TYPES):
        f = tk.Frame(tiles_f, bg="white", relief=tk.RAISED, borderwidth=1)
        f.grid(row=0, column=i, sticky="nsew", padx=4)
        tiles_f.columnconfigure(i, weight=1)
        tk.Label(f, text=title, bg="white", fg=c.COLOR_PRIMARY, font=("Arial", 11, "bold")
                 ).pack(anchor="w", padx=12, pady=(8, 0))
        big = tk.Label(f, text="0", bg="white", fg="#222", font=("Arial", 26, "bold"))
        big.pack(anchor="w", padx=12)
        sub = tk.Label(f, text="", bg="white", fg="#444", font=("Arial", 10), justify="left")
        sub.pack(anchor="w", padx=12, pady=(0, 10))
        tiles[key] = (big, sub)
    extra = tk.Label(win, text="", bg=c.COLOR_BG, fg="#444", font=("Arial", 10))
    extra.pack(anchor="w", padx=14, pady=(2, 6))

    # ---- tabele
    nb = ttk.Notebook(win)
    nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
    trees = {}
    for key, title in (("shifts", "Zmiany"), ("stations", "Stanowiska"), ("profiles", "Profile"),
                       ("fails", "Przyczyny FAIL")):
        frame = tk.Frame(nb, bg="white")
        nb.add(frame, text=f"  {title}  ")
        if key == "fails":
            t = ttk.Treeview(frame, columns=("n",), show="tree headings")
            t.heading("#0", text="Przyczyna (test + kod)")
            t.column("#0", width=380)
            t.heading("n", text="Ilość")
            t.column("n", width=90, anchor="center")
        else:
            t = ttk.Treeview(frame, columns=[k for k, _, _ in COLS], show="tree headings")
            t.heading("#0", text={"shifts": "Zmiana", "stations": "Stanowisko", "profiles": "PN"}[key])
            t.column("#0", width=190)
            for k, h, w in COLS:
                t.heading(k, text=h)
                t.column(k, width=w, anchor="center")
        t.pack(fill=tk.BOTH, expand=True)
        trees[key] = t

    # ------------------------------------------------------------------ #
    def render():
        res = state["result"]
        if res is None:
            return
        recs = res
        if state["shift"] != "Dzień":
            num = {"I": 1, "II": 2, "III": 3}[state["shift"]]
            recs = [r for r in res if r.get("shift") == num]
        rep = stats_store.build_report(recs)
        tot = rep["total"]
        for key, _ in TYPES:
            big, sub = tiles[key]
            s = tot[key]
            big.config(text=str(s["tests"]))
            sub.config(text=f"✓ PASS {s['pass']}     ✗ FAIL {s['fail']}     "
                            f"Yield {_pct(s['pass'], s['tests'])}")
        extra.config(text=f"Retesty: {tot['retest']}     FPY (pierwszy test SN): "
                          f"{_pct(tot['first_pass'], tot['first_tests'])}     "
                          f"Nieważne: {tot['invalid']}     Przerwane: {tot['aborted']}")
        for t in trees.values():
            t.delete(*t.get_children())
        full = stats_store.build_report(res)
        for num, name, _, _ in stats_store.SHIFTS:
            trees["shifts"].insert("", "end", text=f"Zmiana {name}  ({stats_store.shift_hours(num)})",
                                   values=_row_values(full["shifts"][name]))
        trees["shifts"].insert("", "end", text="CAŁY DZIEŃ", values=_row_values(full["total"]))
        for key in ("stations", "profiles"):
            for name, s in rep[key].items():
                trees[key].insert("", "end", text=name, values=_row_values(s))
        for fc in rep["fail_codes"]:
            trees["fails"].insert("", "end", text=fc["code"], values=(fc["count"],))

    def load():
        d = state["day"]
        day_lbl.config(text=f"Dzień {d:%Y-%m-%d}  (06:00-06:00)")
        status.config(text="Wczytywanie...")
        holder = {}

        def worker():
            try:
                holder["r"] = stats_store.read_day(stats_dir, d, include_sim=bool(config.SIMULATION))
            except Exception as e:
                holder["e"] = e
        th = threading.Thread(target=worker, daemon=True)
        th.start()

        def poll():
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            if th.is_alive():
                win.after(100, poll)
                return
            if "e" in holder:
                status.config(text=f"✗ Brak dostępu do statystyk: {holder['e']}", fg=C_ERR)
                return
            state["result"] = holder["r"]
            status.config(text=f"Katalog: {stats_dir}   |   wpisów: {len(holder['r'])}", fg="#666")
            render()
        poll()

    load()
