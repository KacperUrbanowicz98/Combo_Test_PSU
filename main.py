"""OEL85 Command Verifier - punkt startowy.

  python main.py                  -> GUI
  python main.py --cli --sim      -> test wszystkich komend na symulatorze, wynik w konsoli
  python main.py --cli --port COM5 [--baud 115200] [--groups volt,curr] [--probes]
"""
import argparse
import logging
import logging.handlers
import os
import sys

from config import Config, APP_NAME, APP_VERSION, app_dir


def setup_logging():
    path = os.path.join(app_dir(), "oel85_verifier.log")
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    try:
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass


def run_cli(args, cfg):
    from command_catalog import build_catalog
    from transport import build_transport
    from verifier import Verifier
    from report import save_reports

    conn = dict(cfg.conn)
    if args.sim:
        conn["transport"] = "sim"
    if args.port:
        conn["transport"] = "serial"
        conn["port"] = args.port
    if args.baud:
        conn["baudrate"] = args.baud
    t = build_transport(conn)
    if args.verbose:
        t.traffic_cb = lambda d, s: print(f"    {d} {s}")
    t.open()
    idn = t.query("*IDN?")
    print(f"{APP_NAME} {APP_VERSION}\nIDN: {idn or '<brak odpowiedzi>'}\n")
    if not idn and not args.force:
        print("Brak odpowiedzi na *IDN? - sprawdź port/baud/terminator (lub użyj --force).")
        return 2

    groups = build_catalog()
    want = set(args.groups.split(",")) if args.groups else None
    sel = [(g, list(g.cases)) for g in groups
           if (want is None and (g.default_on or args.probes)) or (want and g.key in want)]

    if any(c.needs_input_confirm for _, cs in sel for c in cs) and not args.sim and not args.yes:
        print("UWAGA: testy włączają wejście (INP 1) i zwarcie (INP:SHOR 1).\n"
              "Zaciski OEL muszą być WOLNE, Hi-Pot wyłączony / HV odłączone.")
        if input("Kontynuować? [t/N]: ").strip().lower() not in ("t", "tak", "y", "yes"):
            return 3

    def ev(typ, d):
        if typ == "group":
            print(f"\n== {d}")
        elif typ == "case":
            r = d["result"]
            rx = " | ".join(s.received or "-" for s in r.steps)
            print(f"  [{r.status:8s}] {r.case_id:22s} {rx[:70]}")
            for s in r.steps:
                if s.status not in ("PASS", "INFO ✓") and s.detail:
                    print(f"             krok {s.step_no}: {s.sent or s.query} -> {s.detail}")

    v = Verifier(t, cfg.test, ev)
    res = v.run(sel)
    s = v.summary()
    print("\nPodsumowanie:", s["counts"])
    csv_p, txt_p = save_reports(res, cfg.reports_path(), {
        "idn": idn, "transport": conn["transport"], "port": conn.get("port", ""),
        "summary": {**s, "duration_s": "", "aborted": ""}})
    print("Raport:", csv_p)
    t.close()
    from verifier import BAD_STATUSES
    fails = sum(n for k, n in s["counts"].items() if k in BAD_STATUSES)
    return 1 if fails else 0


def main():
    p = argparse.ArgumentParser(description=APP_NAME)
    p.add_argument("--cli", action="store_true", help="tryb konsolowy (bez GUI)")
    p.add_argument("--sim", action="store_true", help="użyj symulatora OEL8512")
    p.add_argument("--port", help="port COM")
    p.add_argument("--baud", type=int)
    p.add_argument("--groups", help="lista grup, np. ieee,syst,volt (domyślnie wszystkie)")
    p.add_argument("--probes", action="store_true", help="dołącz sondy komend spoza manuala")
    p.add_argument("--force", action="store_true", help="kontynuuj mimo braku *IDN?")
    p.add_argument("--yes", action="store_true", help="bez pytania o wolne zaciski (testy INP)")
    p.add_argument("-v", "--verbose", action="store_true", help="pokaż ruch TX/RX")
    args = p.parse_args()

    setup_logging()
    cfg = Config()
    logging.getLogger(__name__).info("Start %s %s", APP_NAME, APP_VERSION)
    if args.cli:
        sys.exit(run_cli(args, cfg))
    import gui
    gui.run(cfg, start_sim=args.sim)


if __name__ == "__main__":
    main()
