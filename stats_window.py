# stats_window.py
"""
Okno statystyk (zmiany + historia dzienna) - przeniesione z test_screen.py
Hi-Pot PSU 1.1.2. Zamiast HRID operatora: nazwa stanowiska.
Liczony jest WYNIK CAŁKOWITY sztuki (Hi-Pot + obciążenie).
"""
import os
import threading
import tkinter as tk
from datetime import date
from tkinter import ttk

from shift_stats import parse_shift_file


def show_stats_window(parent, config):
    import json as _json
    from datetime import timedelta as td

    log_dir   = config.resolve_log_dir()
    stats_dir = os.path.join(log_dir, "Daily PSU Combo stats")
    shift_dir = os.path.join(log_dir, "Shift Reports")

    win = tk.Toplevel(parent)
    win.title("Statystyki")
    win.configure(bg=config.COLOR_BG)
    win.geometry(f"960x720+{max(0, win.winfo_screenwidth() // 2 - 480)}+"
                 f"{max(0, win.winfo_screenheight() // 2 - 360)}")
    win.transient(parent)
    win.grab_set()

    hdr = tk.Frame(win, bg=config.COLOR_PRIMARY, height=55)
    hdr.pack(fill=tk.X)
    hdr.pack_propagate(False)
    tk.Label(hdr, text="Statystyki", bg=config.COLOR_PRIMARY,
             fg="white", font=("Arial", 17, "bold")).pack(side=tk.LEFT, padx=20)
    tk.Button(hdr, text="Zamknij", bg=config.COLOR_ERROR,
              fg="white", font=("Arial", 10, "bold"), relief=tk.FLAT,
              cursor="hand2", command=win.destroy
              ).pack(side=tk.RIGHT, padx=15, pady=12)

    nb = ttk.Notebook(win)
    nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

    def make_scroll(parent):
        c = tk.Canvas(parent, bg=config.COLOR_BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=c.yview)
        c.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        c.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sf = tk.Frame(c, bg=config.COLOR_BG)
        cw = c.create_window(0, 0, window=sf, anchor="nw")
        sf.bind("<Configure>", lambda e: c.configure(scrollregion=c.bbox("all")))
        c.bind("<Configure>", lambda e: c.itemconfig(cw, width=e.width))

        # POPRAWKA: było bind_all("<MouseWheel>") - globalna podpinka,
        # która po zamknięciu okna wskazywała na zniszczony Canvas
        # i przy każdym ruchu rolki sypała TclError w całej aplikacji.
        def _wheel(event):
            try:
                c.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass
        for widget in (c, sf):
            widget.bind("<MouseWheel>", _wheel)
            widget.bind("<Button-4>", lambda e: c.yview_scroll(-1, "units"))
            widget.bind("<Button-5>", lambda e: c.yview_scroll(1, "units"))
        return sf

    # ---------------- ZAKŁADKA 1 - ZMIANY ---------------- #
    tab_shifts = tk.Frame(nb, bg=config.COLOR_BG)
    nb.add(tab_shifts, text="  Zmiany  ")

    nav_f = tk.Frame(tab_shifts, bg=config.COLOR_BG)
    nav_f.pack(fill=tk.X, padx=10, pady=(8, 0))
    day_lbl = tk.Label(nav_f, text="", bg=config.COLOR_BG,
                       fg=config.COLOR_PRIMARY, font=("Arial", 12, "bold"))
    day_lbl.pack(side=tk.LEFT, padx=6)

    state = {"day": date.today(), "busy": False}
    scroll_shifts = make_scroll(tab_shifts)

    def collect_shifts(day):
        """Czyta pliki zmian - wołane z wątku tła (dysk sieciowy)."""
        out = []
        try:
            if not os.path.isdir(shift_dir):
                return out
            for hrid in sorted(os.listdir(shift_dir)):
                hrid_path = os.path.join(shift_dir, hrid)
                if not os.path.isdir(hrid_path):
                    continue
                files = [os.path.join(hrid_path, f)
                         for f in sorted(os.listdir(hrid_path))
                         if f.endswith(".txt") and day.strftime("%Y-%m-%d") in f]
                rows = [d for d in (parse_shift_file(f) for f in files) if d]
                if rows:
                    out.append((hrid, rows))
        except Exception as e:
            print(f"[STATS] Błąd czytania zmian: {e}")
        return out

    def render_shifts(day, data):
        if not win.winfo_exists():
            return
        for w in scroll_shifts.winfo_children():
            w.destroy()
        day_lbl.config(text="Dzisiaj" if day == date.today()
                       else day.strftime("%d.%m.%Y"))

        if not data:
            tk.Label(scroll_shifts,
                     text=f"Brak danych zmianowych dla {day.strftime('%d.%m.%Y')}.",
                     bg=config.COLOR_BG, fg="#888",
                     font=("Arial", 11)).pack(pady=30)
            return

        for hrid, rows in data:
            hf = tk.Frame(scroll_shifts, bg=config.COLOR_PRIMARY)
            hf.pack(fill=tk.X, pady=(12, 0))
            tk.Label(hf, text=f"  Stanowisko: {hrid}", bg=config.COLOR_PRIMARY,
                     fg="white", font=("Arial", 12, "bold")
                     ).pack(side=tk.LEFT, padx=10, pady=6)

            for d in rows:
                sf2 = tk.Frame(scroll_shifts, bg="#37474F")
                sf2.pack(fill=tk.X, pady=(2, 0))
                total = d["passed"] + d["failed"]
                tk.Label(sf2,
                         text=f"  Zmiana {d['shift_name']}  ({d['hours']})    "
                              f"Razem: {total}   PASS: {d['passed']}   "
                              f"FAIL: {d['failed']}   RETEST: {d['retests']}",
                         bg="#37474F", fg="white",
                         font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=10, pady=5)

                if not d["models"]:
                    continue

                col_hdr = tk.Frame(scroll_shifts, bg="#ECEFF1")
                col_hdr.pack(fill=tk.X)
                for txt, w in [("Model", 280), ("PASS", 80), ("FAIL", 80),
                               ("RETEST", 80), ("Razem", 80)]:
                    tk.Label(col_hdr, text=txt, bg="#ECEFF1", fg="#333",
                             font=("Arial", 9, "bold"), width=w // 8,
                             anchor="center").pack(side=tk.LEFT, padx=2, pady=3)

                for i, (mk, cnt) in enumerate(sorted(d["models"].items())):
                    bg = "#ffffff" if i % 2 == 0 else "#f5f5f5"
                    row = tk.Frame(scroll_shifts, bg=bg)
                    row.pack(fill=tk.X)
                    for val, w, fg in [
                        (mk, 280, "#333"),
                        (cnt["pass"], 80, config.COLOR_ACCENT),
                        (cnt["fail"], 80,
                         config.COLOR_ERROR if cnt["fail"] > 0 else "#333"),
                        (cnt["retest"], 80,
                         "#FF9800" if cnt["retest"] > 0 else "#aaa"),
                        (cnt["pass"] + cnt["fail"], 80, "#222"),
                    ]:
                        tk.Label(row, text=str(val), bg=bg, fg=fg,
                                 font=("Arial", 10), width=w // 8,
                                 anchor="center").pack(side=tk.LEFT, padx=2, pady=2)

    def load_shifts(day):
        if state["busy"]:
            return
        state["busy"] = True
        for w in scroll_shifts.winfo_children():
            w.destroy()
        tk.Label(scroll_shifts, text="Wczytywanie...", bg=config.COLOR_BG,
                 fg="#888", font=("Arial", 11)).pack(pady=30)

        def worker():
            data = collect_shifts(day)
            state["busy"] = False
            try:
                if win.winfo_exists():
                    win.after(0, lambda: render_shifts(day, data))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def change_shift_day(delta):
        state["day"] = state["day"] + td(days=delta)
        load_shifts(state["day"])

    tk.Button(nav_f, text="◀ Poprzedni", bg="#607D8B", fg="white",
              font=("Arial", 9), relief=tk.FLAT, cursor="hand2",
              command=lambda: change_shift_day(-1)).pack(side=tk.LEFT, padx=4)
    tk.Button(nav_f, text="Następny ▶", bg="#607D8B", fg="white",
              font=("Arial", 9), relief=tk.FLAT, cursor="hand2",
              command=lambda: change_shift_day(1)).pack(side=tk.LEFT, padx=4)

    # ---------------- ZAKŁADKA 2 - HISTORIA DZIENNA ---------------- #
    tab_daily = tk.Frame(nb, bg=config.COLOR_BG)
    nb.add(tab_daily, text="  Historia dzienna  ")

    nav2 = tk.Frame(tab_daily, bg=config.COLOR_BG)
    nav2.pack(fill=tk.X, padx=10, pady=(8, 0))
    day2_lbl = tk.Label(nav2, text="", bg=config.COLOR_BG,
                        fg=config.COLOR_PRIMARY, font=("Arial", 12, "bold"))
    day2_lbl.pack(side=tk.LEFT, padx=6)

    state2 = {"day": date.today(), "busy": False}
    scroll_daily = make_scroll(tab_daily)

    def load_daily(day):
        path = os.path.join(stats_dir, f"stats_{day.strftime('%Y-%m-%d')}.json")
        try:
            if not os.path.exists(path):
                return {}
            with open(path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
            result = {}
            for op, rows in raw.items():
                if not isinstance(rows, dict):
                    continue
                result[op] = [{"model": e.get("model", "?"),
                               "mode": e.get("mode", "AC"),
                               "pass": e.get("pass", 0),
                               "fail": e.get("fail", 0),
                               "retest": e.get("retest", 0),
                               "total": e.get("pass", 0) + e.get("fail", 0)}
                              for e in rows.values() if isinstance(e, dict)]
            return result
        except Exception as e:
            print(f"[STATS] Błąd odczytu {path}: {e}")
            return {}

    def render_daily(day, data):
        if not win.winfo_exists():
            return
        for w in scroll_daily.winfo_children():
            w.destroy()
        day2_lbl.config(text="Dzisiaj" if day == date.today()
                        else day.strftime("%d.%m.%Y"))
        if not data:
            tk.Label(scroll_daily, text="Brak danych dla wybranego dnia.",
                     bg=config.COLOR_BG, fg="#888",
                     font=("Arial", 12)).pack(pady=40)
            return

        grand = {"total": 0, "pass": 0, "fail": 0}
        for op, rows in data.items():
            op_total = sum(r["total"] for r in rows)
            op_pass  = sum(r["pass"] for r in rows)
            op_fail  = sum(r["fail"] for r in rows)
            grand["total"] += op_total
            grand["pass"]  += op_pass
            grand["fail"]  += op_fail

            oh = tk.Frame(scroll_daily, bg=config.COLOR_PRIMARY)
            oh.pack(fill=tk.X, pady=(10, 0))
            tk.Label(oh, text=f"  Stanowisko: {op}   Razem: {op_total}   "
                              f"PASS: {op_pass}   FAIL: {op_fail}",
                     bg=config.COLOR_PRIMARY, fg="white",
                     font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=10, pady=5)

            ch = tk.Frame(scroll_daily, bg="#ECEFF1")
            ch.pack(fill=tk.X)
            for txt, w in [("Model", 260), ("Tryb", 70), ("PASS", 70),
                           ("FAIL", 70), ("RETEST", 70), ("Razem", 70)]:
                tk.Label(ch, text=txt, bg="#ECEFF1", fg="#333",
                         font=("Arial", 9, "bold"), width=w // 8,
                         anchor="center").pack(side=tk.LEFT, padx=2, pady=3)

            for i, row in enumerate(rows):
                bg = "#fff" if i % 2 == 0 else "#f5f5f5"
                rf = tk.Frame(scroll_daily, bg=bg)
                rf.pack(fill=tk.X)
                for val, w, fg in [
                    (row["model"], 260, "#333"),
                    (row["mode"], 70, "#555"),
                    (row["pass"], 70, config.COLOR_ACCENT),
                    (row["fail"], 70,
                     config.COLOR_ERROR if row["fail"] > 0 else "#333"),
                    (row["retest"], 70,
                     "#FF9800" if row["retest"] > 0 else "#aaa"),
                    (row["total"], 70, "#222"),
                ]:
                    tk.Label(rf, text=str(val), bg=bg, fg=fg,
                             font=("Arial", 10), width=w // 8,
                             anchor="center").pack(side=tk.LEFT, padx=2, pady=2)

        sm = tk.Frame(scroll_daily, bg="#263238")
        sm.pack(fill=tk.X, pady=(14, 4))
        tk.Label(sm, text=f"  SUMA DNIA:  Razem {grand['total']}   "
                          f"PASS {grand['pass']}   FAIL {grand['fail']}",
                 bg="#263238", fg="white",
                 font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=15, pady=8)

    def load_daily_async(day):
        if state2["busy"]:
            return
        state2["busy"] = True
        for w in scroll_daily.winfo_children():
            w.destroy()
        tk.Label(scroll_daily, text="Wczytywanie...", bg=config.COLOR_BG,
                 fg="#888", font=("Arial", 11)).pack(pady=30)

        def worker():
            data = load_daily(day)
            state2["busy"] = False
            try:
                if win.winfo_exists():
                    win.after(0, lambda: render_daily(day, data))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def change_daily(delta):
        state2["day"] = state2["day"] + td(days=delta)
        load_daily_async(state2["day"])

    tk.Button(nav2, text="◀ Poprzedni", bg="#607D8B", fg="white",
              font=("Arial", 9), relief=tk.FLAT, cursor="hand2",
              command=lambda: change_daily(-1)).pack(side=tk.LEFT, padx=4)
    tk.Button(nav2, text="Następny ▶", bg="#607D8B", fg="white",
              font=("Arial", 9), relief=tk.FLAT, cursor="hand2",
              command=lambda: change_daily(1)).pack(side=tk.LEFT, padx=4)

    load_shifts(state["day"])
    load_daily_async(state2["day"])
