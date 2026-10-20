"""Główne okno: połączenie, wybór testów, wyniki, log TX/RX."""
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import auth
from command_catalog import build_catalog
from config import APP_NAME, APP_VERSION
from oel85 import OEL85
from report import save_reports
from transport import build_transport, list_serial_ports
from verifier import Verifier, PASS, FAIL, NO_RESP, ERROR, INFO_OK, INFO_NOK

log = logging.getLogger(__name__)

TRANSPORTS = [("serial", "RS232 / USB-COM"), ("visa", "VISA (USBTMC)"), ("sim", "Symulator OEL8512")]
BAUDS = [9600, 19200, 38400, 57600, 115200]
STATUS_COLORS = {
    PASS: "#d7f5dd", FAIL: "#f8d0d0", NO_RESP: "#fde7c4", ERROR: "#f3b4b4",
    INFO_OK: "#dde9fb", INFO_NOK: "#ececec",
}
CHK, UNCHK = "☑", "☐"


class App:
    def __init__(self, root, cfg, start_sim=False):
        self.root = root
        self.cfg = cfg
        self.q = queue.Queue()
        self.dev = None              # OEL85
        self.verifier = None
        self.busy = False
        self.last_results = []
        self.last_summary = {}
        self.groups = build_catalog()
        self.case_by_id = {c.id: c for g in self.groups for c in g.cases}
        self.checked = set()
        self.eng_win = None
        self.admin_win = None

        root.title(f"{APP_NAME} {APP_VERSION} — OWON OEL85")
        root.geometry("1360x820")
        root.minsize(1000, 640)
        self._style()
        self._menu()
        self._build()
        self._fill_tests()
        self.refresh_ports()
        if start_sim:
            self.var_transport.set(dict(TRANSPORTS)["sim"])
            self._on_transport()
        self._update_buttons()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._poll)

    # ================================================================== UI
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", rowheight=22)
        st.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        st.configure("Stop.TButton", font=("Segoe UI", 10, "bold"), padding=6, foreground="#b00000")

    def _menu(self):
        m = tk.Menu(self.root)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="Zapisz raport ponownie", command=self.save_report)
        f.add_command(label="Otwórz folder raportów", command=self.open_reports)
        f.add_separator()
        f.add_command(label="Wyjście", command=self.on_close)
        m.add_cascade(label="Plik", menu=f)
        t = tk.Menu(m, tearoff=0)
        t.add_command(label="Panel ENG…", command=self.open_eng)
        t.add_command(label="Panel Admin…", command=self.open_admin)
        m.add_cascade(label="Narzędzia", menu=t)
        h = tk.Menu(m, tearoff=0)
        h.add_command(label="O programie", command=lambda: messagebox.showinfo(
            "O programie", f"{APP_NAME} {APP_VERSION}\n\nWeryfikacja komend SCPI obciążenia OWON OEL85 "
                           "wg Programming Manual V1.0.2 (01.2025).\nReconext", parent=self.root))
        m.add_cascade(label="Pomoc", menu=h)
        self.root.config(menu=m)

    def _build(self):
        # ---- połączenie
        top = ttk.LabelFrame(self.root, text="Połączenie", padding=6)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="Interfejs:").pack(side="left")
        self.var_transport = tk.StringVar(value=dict(TRANSPORTS).get(self.cfg.conn["transport"], TRANSPORTS[0][1]))
        cb = ttk.Combobox(top, textvariable=self.var_transport, values=[t[1] for t in TRANSPORTS],
                          state="readonly", width=18)
        cb.pack(side="left", padx=(4, 10))
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_transport())
        ttk.Label(top, text="Port:").pack(side="left")
        self.var_port = tk.StringVar()
        self.cb_port = ttk.Combobox(top, textvariable=self.var_port, width=42)
        self.cb_port.pack(side="left", padx=4)
        self.btn_refresh = ttk.Button(top, text="⟳", width=3, command=self.refresh_ports)
        self.btn_refresh.pack(side="left")
        ttk.Label(top, text="Baud:").pack(side="left", padx=(10, 0))
        self.var_baud = tk.StringVar(value=str(self.cfg.conn["baudrate"]))
        self.cb_baud = ttk.Combobox(top, textvariable=self.var_baud, values=BAUDS, width=8)
        self.cb_baud.pack(side="left", padx=4)
        self.btn_conn = ttk.Button(top, text="Połącz", style="Big.TButton", command=self.toggle_connect)
        self.btn_conn.pack(side="left", padx=10)
        self.lbl_status = tk.Label(top, text="● Rozłączony", fg="#a00000", font=("Segoe UI", 10, "bold"))
        self.lbl_status.pack(side="left", padx=6)
        self.lbl_idn = ttk.Label(top, text="", foreground="#333")
        self.lbl_idn.pack(side="left", padx=6)

        # ---- środek
        pw = ttk.PanedWindow(self.root, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        ttk.Label(left, text="Testy komend (klik = zaznacz, 2×klik = szczegóły)").pack(anchor="w")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree_tests = ttk.Treeview(tf, columns=("page", "kind"), show="tree headings", selectmode="browse")
        self.tree_tests.heading("#0", text="Test")
        self.tree_tests.heading("page", text="Str.")
        self.tree_tests.heading("kind", text="Rodzaj")
        self.tree_tests.column("#0", width=300)
        self.tree_tests.column("page", width=40, anchor="center")
        self.tree_tests.column("kind", width=60, anchor="center")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree_tests.yview)
        self.tree_tests.configure(yscrollcommand=sb.set)
        self.tree_tests.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree_tests.tag_configure("probe", foreground="#555")
        self.tree_tests.tag_configure("danger", foreground="#b05000")
        self.tree_tests.bind("<Button-1>", self._on_test_click)
        self.tree_tests.bind("<Double-1>", self._on_test_dbl)

        sel = ttk.Frame(left)
        sel.pack(fill="x", pady=4)
        for i, (txt, cmd) in enumerate((("Wszystkie", lambda: self._check_all(True)),
                                        ("Żadne", lambda: self._check_all(False)),
                                        ("Domyślne", self._check_default), ("Tylko FAIL", self._check_failed))):
            sel.columnconfigure(i, weight=1)
            ttk.Button(sel, text=txt, command=cmd).grid(row=0, column=i, sticky="ew", padx=1)

        act = ttk.Frame(left)
        act.pack(fill="x", pady=(2, 0))
        self.btn_run = ttk.Button(act, text="▶  Uruchom zaznaczone", style="Big.TButton", command=self.run_tests)
        self.btn_run.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(act, text="■  STOP", style="Stop.TButton", command=self.stop_tests)
        self.btn_stop.pack(side="left", padx=(6, 0))

        right = ttk.Frame(pw)
        pw.add(right, weight=3)
        vp = ttk.PanedWindow(right, orient="vertical")
        vp.pack(fill="both", expand=True)

        rf = ttk.Frame(vp)
        vp.add(rf, weight=3)
        cols = ("sent", "query", "expect", "recv", "status", "ms", "detail")
        self.tree_res = ttk.Treeview(rf, columns=cols, show="tree headings")
        heads = {"sent": ("Wysłano", 140), "query": ("Zapytanie", 120), "expect": ("Oczekiwano", 80),
                 "recv": ("Odebrano", 160), "status": ("Status", 66), "ms": ("ms", 48), "detail": ("Szczegóły", 240)}
        self.tree_res.heading("#0", text="Test / krok")
        self.tree_res.column("#0", width=230, minwidth=120, stretch=False)
        for c, (h, wdt) in heads.items():
            self.tree_res.heading(c, text=h)
            self.tree_res.column(c, width=wdt, minwidth=40, stretch=(c == "detail"),
                                 anchor="center" if c in ("status", "ms") else "w")
        for st, col in STATUS_COLORS.items():
            self.tree_res.tag_configure(st, background=col)
        sb2 = ttk.Scrollbar(rf, orient="vertical", command=self.tree_res.yview)
        sbx = ttk.Scrollbar(rf, orient="horizontal", command=self.tree_res.xview)
        self.tree_res.configure(yscrollcommand=sb2.set, xscrollcommand=sbx.set)
        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)
        self.tree_res.grid(row=0, column=0, sticky="nsew")
        sb2.grid(row=0, column=1, sticky="ns")
        sbx.grid(row=1, column=0, sticky="ew")

        lf = ttk.LabelFrame(vp, text="Log komunikacji (TX/RX)", padding=2)
        vp.add(lf, weight=1)
        self.txt_log = tk.Text(lf, height=9, font=("Consolas", 9), wrap="none", state="disabled")
        sb3 = ttk.Scrollbar(lf, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb3.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")
        self.txt_log.tag_configure("TX", foreground="#0050b0")
        self.txt_log.tag_configure("RX", foreground="#007a30")
        self.txt_log.tag_configure("ERR", foreground="#c00000")
        self.txt_log.tag_configure("SYS", foreground="#666")

        # ---- dół
        bot = ttk.Frame(self.root)
        bot.pack(fill="x", padx=8, pady=(0, 8))
        self.progress = ttk.Progressbar(bot, length=300, mode="determinate")
        self.progress.pack(side="left")
        self.lbl_progress = ttk.Label(bot, text="")
        self.lbl_progress.pack(side="left", padx=8)
        self.lbl_summary = tk.Label(bot, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_summary.pack(side="right")

    # ================================================================== lista testów
    def _fill_tests(self):
        t = self.tree_tests
        t.delete(*t.get_children())
        last = set(self.cfg.data["ui"].get("last_groups") or [])
        for g in self.groups:
            gid = "g:" + g.key
            t.insert("", "end", iid=gid, text=g.name, open=False, tags=("danger",) if g.needs_input_confirm else ())
            for c in g.cases:
                tags = []
                if c.kind == "probe":
                    tags.append("probe")
                if c.needs_input_confirm:
                    tags.append("danger")
                t.insert(gid, "end", iid="c:" + c.id, text=c.title, values=(c.page or "", c.kind), tags=tags)
            if (last and g.key in last) or (not last and g.default_on):
                self.checked.update(c.id for c in g.cases)
        self._redraw_checks()

    def _redraw_checks(self):
        t = self.tree_tests
        for g in self.groups:
            n = sum(1 for c in g.cases if c.id in self.checked)
            mark = CHK if n == len(g.cases) else (UNCHK if n == 0 else "◩")
            t.item("g:" + g.key, text=f"{mark} {g.name}  ({n}/{len(g.cases)})")
            for c in g.cases:
                t.item("c:" + c.id, text=f"{CHK if c.id in self.checked else UNCHK} {c.title}")

    def _on_test_click(self, ev):
        if self.busy:
            return
        iid = self.tree_tests.identify_row(ev.y)
        elem = self.tree_tests.identify_element(ev.x, ev.y)
        if not iid or "indicator" in elem:
            return
        if iid.startswith("g:"):
            g = next(x for x in self.groups if "g:" + x.key == iid)
            ids = {c.id for c in g.cases}
            if ids <= self.checked:
                self.checked -= ids
            else:
                self.checked |= ids
        else:
            cid = iid[2:]
            self.checked ^= {cid}
        self._redraw_checks()
        self._update_buttons()

    def _on_test_dbl(self, ev):
        iid = self.tree_tests.identify_row(ev.y)
        if not iid or not iid.startswith("c:"):
            return
        c = self.case_by_id[iid[2:]]
        lines = [f"{c.title}", f"ID: {c.id}   Rodzaj: {c.kind}   Str. manuala: {c.page}", ""]
        if c.note:
            lines += [c.note, ""]
        for i, s in enumerate(c.steps, 1):
            lines.append(f"{i}. wyślij: {s.send or '-'}   zapytaj: {s.query or ('*IDN?' if s.cmp == 'alive' else '-')}"
                         f"   oczekuj: {s.expect if s.expect is not None else '-'} [{s.cmp}]")
        messagebox.showinfo("Szczegóły testu", "\n".join(lines), parent=self.root)
        self.checked ^= {c.id}  # pierwsze kliknięcie dwukliku przełączyło zaznaczenie -> cofnij
        self._redraw_checks()

    def _check_all(self, on):
        self.checked = {c.id for g in self.groups for c in g.cases} if on else set()
        self._redraw_checks()
        self._update_buttons()

    def _check_default(self):
        self.checked = {c.id for g in self.groups if g.default_on for c in g.cases}
        self._redraw_checks()
        self._update_buttons()

    def _check_failed(self):
        bad = {r.case_id for r in self.last_results if r.status in (FAIL, NO_RESP, ERROR)}
        if not bad:
            messagebox.showinfo("Info", "Brak niezaliczonych testów z ostatniego przebiegu.", parent=self.root)
            return
        self.checked = bad
        self._redraw_checks()
        self._update_buttons()

    # ================================================================== połączenie
    def _transport_key(self):
        rev = {v: k for k, v in TRANSPORTS}
        return rev.get(self.var_transport.get(), "serial")

    def _on_transport(self):
        k = self._transport_key()
        st = "disabled" if k == "sim" else "normal"
        self.cb_port.configure(state=st)
        self.cb_baud.configure(state="disabled" if k != "serial" else "normal")
        self.btn_refresh.configure(state="normal" if k == "serial" else "disabled")
        if k == "visa":
            self.var_port.set(self.cfg.conn.get("visa_resource", ""))
        elif k == "serial":
            self.refresh_ports()

    def refresh_ports(self):
        ports = list_serial_ports()
        self._ports = ports
        vals = [f"{d} — {desc} [{vp}]" if vp else f"{d} — {desc}" for d, desc, vp in ports]
        self.cb_port.configure(values=vals)
        want_vp = self.cfg.conn.get("port_vid_pid", "")
        want_dev = self.cfg.conn.get("port", "")
        chosen = ""
        for (d, desc, vp), v in zip(ports, vals):
            if want_vp and vp == want_vp:   # najpierw po VID:PID (numer COM potrafi się zmienić)
                chosen = v
                break
            if d == want_dev:
                chosen = v
        if chosen:
            self.var_port.set(chosen)
        elif vals and not self.var_port.get():
            self.var_port.set(vals[0])
        elif not vals and self._transport_key() == "serial":
            self.var_port.set("")

    def _selected_port(self):
        v = self.var_port.get().strip()
        return v.split(" — ")[0].strip()

    def toggle_connect(self):
        if self.dev and self.dev.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        k = self._transport_key()
        conn = dict(self.cfg.conn)
        conn["transport"] = k
        if k == "serial":
            port = self._selected_port()
            if not port:
                messagebox.showwarning("Port", "Wybierz port COM.", parent=self.root)
                return
            conn["port"] = port
            try:
                conn["baudrate"] = int(self.var_baud.get())
            except ValueError:
                messagebox.showwarning("Baud", "Nieprawidłowy baudrate.", parent=self.root)
                return
            vp = next((x[2] for x in getattr(self, "_ports", []) if x[0] == port), "")
            self.cfg.conn.update(port=port, baudrate=conn["baudrate"], port_vid_pid=vp)
        elif k == "visa":
            conn["visa_resource"] = self.var_port.get().strip()
            self.cfg.conn["visa_resource"] = conn["visa_resource"]
        self.cfg.conn["transport"] = k
        self.cfg.save()

        t = build_transport(conn)
        t.traffic_cb = lambda d, s: self.q.put(("traffic", (d, s)))
        dev = OEL85(t)
        self.lbl_status.configure(text="● Łączenie…", fg="#b07000")
        self.btn_conn.configure(state="disabled")

        def work():  # w wątku - nie blokuje GUI
            try:
                idn = dev.connect()
                self.q.put(("connected", (dev, idn)))
            except Exception as e:
                try:
                    dev.t.close()
                except Exception:
                    pass
                self.q.put(("connect_fail", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def disconnect(self):
        if self.busy:
            messagebox.showwarning("Test w toku", "Najpierw zatrzymaj test.", parent=self.root)
            return
        dev, self.dev = self.dev, None
        if dev:
            threading.Thread(target=lambda: dev.disconnect(safe_off=True), daemon=True).start()
        self.lbl_status.configure(text="● Rozłączony", fg="#a00000")
        self.lbl_idn.configure(text="")
        self.log_sys("Rozłączono")
        self._update_buttons()

    # ================================================================== testy
    def run_tests(self):
        if not self.dev or not self.dev.connected or self.busy:
            return
        sel = [(g, [c for c in g.cases if c.id in self.checked]) for g in self.groups]
        sel = [(g, cs) for g, cs in sel if cs]
        if not sel:
            messagebox.showinfo("Brak testów", "Zaznacz co najmniej jeden test.", parent=self.root)
            return
        if self.cfg.test.get("require_input_confirm", True) and any(c.needs_input_confirm for _, cs in sel for c in cs):
            if not messagebox.askyesno(
                    "Potwierdzenie - wejście obciążenia",
                    "Zaznaczone testy włączają wejście obciążenia (INP 1) i zwarcie (INP:SHOR 1).\n\n"
                    "Potwierdź, że:\n"
                    "  • zaciski wejściowe OEL są WOLNE (nic nie podłączone),\n"
                    "  • tester Hi-Pot jest wyłączony / HV odłączone od toru obciążenia.\n\n"
                    "Kontynuować?", icon="warning", parent=self.root):
                return
        self.cfg.data["ui"]["last_groups"] = [g.key for g, _ in sel]
        self.cfg.save()
        self.tree_res.delete(*self.tree_res.get_children())
        self.lbl_summary.configure(text="")
        self.busy = True
        self._update_buttons()
        self.verifier = Verifier(self.dev.t, self.cfg.test, on_event=lambda t, d: self.q.put(("ev", (t, d))))
        threading.Thread(target=self.verifier.run, args=(sel,), daemon=True).start()

    def stop_tests(self):
        if self.verifier and self.busy:
            self.verifier.stop()
            self.log_sys("STOP - kończę bieżący krok i wyłączam wejście…")

    def _on_event(self, typ, d):
        if typ == "start":
            self.progress.configure(maximum=max(1, d["total"]), value=0)
            self.log_sys(f"Start weryfikacji: {d['total']} testów")
        elif typ == "group":
            self.log_sys(f"--- {d}")
        elif typ == "case_start":
            self.lbl_progress.configure(text=f"{d.id}: {d.title}")
        elif typ == "case":
            self._add_result(d["result"])
            self.progress.configure(value=d["done"])
        elif typ == "done":
            self.busy = False
            self.last_results = list(self.verifier.results)
            self.last_summary = d
            self._show_summary(d)
            self._update_buttons()
            self.save_report(auto=True)
            if d.get("aborted"):
                self.log_sys(f"Przerwano: {d['aborted']}", err=True)
                if "komunikacji" in d["aborted"]:
                    messagebox.showerror("Błąd komunikacji", d["aborted"], parent=self.root)

    def _add_result(self, r):
        pid = self.tree_res.insert("", "end", text=f"{r.case_id}  {r.title}",
                                   values=("", "", "", "", r.status, "", r.note), tags=(r.status,),
                                   open=r.status not in (PASS, INFO_OK))
        for s in r.steps:
            self.tree_res.insert(pid, "end", text=f"   krok {s.step_no}",
                                 values=(s.sent, s.query, s.expect, s.received, s.status, s.ms, s.detail),
                                 tags=(s.status,))
        self.tree_res.see(pid)

    def _show_summary(self, d):
        c = d["counts"]
        bad = c.get(FAIL, 0) + c.get(NO_RESP, 0) + c.get(ERROR, 0)
        txt = (f"PASS {c.get(PASS, 0)}   FAIL {c.get(FAIL, 0)}   NO RESP {c.get(NO_RESP, 0)}   "
               f"ERROR {c.get(ERROR, 0)}   INFO {c.get(INFO_OK, 0) + c.get(INFO_NOK, 0)}   "
               f"({d.get('duration_s', '')} s)")
        self.lbl_summary.configure(text=txt, fg="#007a30" if not bad else "#b00000")
        self.lbl_progress.configure(text="Zakończono" + (f" - {d['aborted']}" if d.get("aborted") else ""))

    # ================================================================== raporty
    def save_report(self, auto=False):
        if not self.last_results:
            if not auto:
                messagebox.showinfo("Raport", "Brak wyników do zapisania.", parent=self.root)
            return
        try:
            csv_p, txt_p = save_reports(self.last_results, self.cfg.reports_path(), {
                "idn": self.dev.idn if self.dev else "", "transport": self.cfg.conn["transport"],
                "port": self.cfg.conn.get("port", ""), "summary": self.last_summary})
            self.log_sys(f"Raport zapisany: {csv_p}")
            if not auto:
                messagebox.showinfo("Raport", f"Zapisano:\n{csv_p}\n{txt_p}", parent=self.root)
        except Exception as e:
            self.log_sys(f"Błąd zapisu raportu: {e}", err=True)

    def open_reports(self):
        p = self.cfg.reports_path()
        try:
            if sys.platform.startswith("win"):
                os.startfile(p)  # noqa
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("Błąd", str(e), parent=self.root)

    # ================================================================== panele
    def open_eng(self):
        if self.eng_win and self.eng_win.winfo_exists():
            self.eng_win.lift()
            return
        if auth.ask_password(self.root, self.cfg, "Logowanie - Panel ENG"):
            import eng_panel
            self.eng_win = eng_panel.EngPanel(self)

    def open_admin(self):
        if self.admin_win and self.admin_win.winfo_exists():
            self.admin_win.lift()
            return
        if auth.ask_password(self.root, self.cfg, "Logowanie - Panel Admin"):
            import admin_panel
            self.admin_win = admin_panel.AdminPanel(self)

    # ================================================================== pomocnicze
    def log_sys(self, text, err=False):
        self._log_line("ERR" if err else "SYS", text)

    def _log_line(self, tag, text):
        import datetime as dt
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", f"{dt.datetime.now():%H:%M:%S.%f}"[:-3] + f"  {tag:3s} {text}\n", tag)
        lines = int(self.txt_log.index("end-1c").split(".")[0])
        if lines > 5000:
            self.txt_log.delete("1.0", f"{lines - 4000}.0")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _update_buttons(self):
        conn = bool(self.dev and self.dev.connected)
        self.btn_conn.configure(text="Rozłącz" if conn else "Połącz", state="disabled" if self.busy else "normal")
        self.btn_run.configure(state="normal" if conn and not self.busy and self.checked else "disabled")
        self.btn_stop.configure(state="normal" if self.busy else "disabled")

    def _poll(self):
        try:
            for _ in range(500):
                typ, d = self.q.get_nowait()
                if typ == "traffic":
                    self._log_line("ERR" if d[1].startswith("<brak") else d[0], d[1])
                elif typ == "ev":
                    self._on_event(*d)
                elif typ == "connected":
                    dev, idn = d
                    self.dev = dev
                    if idn:
                        self.lbl_status.configure(text="● Połączony", fg="#007a30")
                        self.lbl_idn.configure(text=idn)
                    else:
                        self.lbl_status.configure(text="● Port otwarty - brak *IDN?", fg="#b07000")
                        self.lbl_idn.configure(text="sprawdź baudrate / terminator / kabel (Panel ENG → Diagnostyka)")
                    self.log_sys(f"Połączono ({dev.t.name}) IDN: {idn or '-'}")
                    self._update_buttons()
                elif typ == "connect_fail":
                    self.lbl_status.configure(text="● Błąd połączenia", fg="#a00000")
                    self.log_sys(d, err=True)
                    messagebox.showerror("Połączenie", d, parent=self.root)
                    self._update_buttons()
                elif typ == "call":
                    d()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def call_in_ui(self, fn):
        """Do użytku z wątków: wykonaj fn w wątku GUI."""
        self.q.put(("call", fn))

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno("Test w toku", "Zatrzymać test i zamknąć?", parent=self.root):
                return
            self.verifier.stop()
        if self.dev:
            try:
                self.dev.disconnect(safe_off=True)
            except Exception:
                pass
        self.root.destroy()


def run(cfg, start_sim=False):
    root = tk.Tk()
    App(root, cfg, start_sim=start_sim)
    root.mainloop()
