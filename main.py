# main.py
"""
Reconext Combo Test PSU - stanowisko Hi-Pot (Chroma 19052) + obciążenie (OEL8512).

  python main.py          praca na sprzęcie
  python main.py --sim    symulacja Chromy, OEL8512, interlocka i przekaźnika (bez sprzętu)
"""
import argparse
import os
import sys
import tkinter as tk
import traceback
from datetime import datetime

from app_paths import data_path


def _log_crash(exc_type, exc_value, exc_tb):
    """Nieobsłużony wyjątek -> combo_errors.log (w EXE --windowed nie ma konsoli)."""
    if issubclass(exc_type, KeyboardInterrupt):     # Ctrl+C / Stop w PyCharm - to nie awaria
        print("[MAIN] Przerwano (Ctrl+C)")
        return
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(text, file=sys.stderr)
    try:
        with open(data_path("combo_errors.log"), "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{text}")
    except Exception:
        pass
    try:
        from tkinter import messagebox
        messagebox.showerror("Błąd aplikacji", f"Wystąpił nieoczekiwany błąd:\n\n{exc_value}\n\n"
                                               f"Szczegóły zapisano w pliku combo_errors.log")
    except Exception:
        pass


def _setup_traffic_log():
    """Ruch OEL8512 (TX/RX z czasem w ms) -> oel_traffic.log obok aplikacji, max 3 x 2 MB."""
    import logging
    from logging.handlers import RotatingFileHandler
    try:
        h = RotatingFileHandler(data_path("oel_traffic.log"), maxBytes=2_000_000, backupCount=2,
                                encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        lg = logging.getLogger("transport")
        lg.setLevel(logging.DEBUG)
        lg.addHandler(h)
        lg.propagate = False
    except Exception as e:
        print(f"[MAIN] Log ruchu OEL niedostępny: {e}")


def main():
    ap = argparse.ArgumentParser(description="Reconext Combo Test PSU")
    ap.add_argument("--sim", action="store_true", help="symulacja sprzętu")
    args, _ = ap.parse_known_args()

    sys.excepthook = _log_crash
    import instance_lock
    if not instance_lock.acquire("ReconextComboTestPSU" + ("_SIM" if args.sim else "")):
        r = tk.Tk()
        r.withdraw()
        from tkinter import messagebox
        messagebox.showwarning("Combo Test PSU", "Aplikacja jest już uruchomiona na tym komputerze.\n\n"
                                                 "Druga kopia zablokowałaby porty urządzeń - "
                                                 "przełącz się na otwarte okno.")
        r.destroy()
        return
    root = tk.Tk()
    root.report_callback_exception = _log_crash

    _setup_traffic_log()
    from config import Config
    cfg = Config(simulation=args.sim)
    from gui import ComboTesterApp
    app = ComboTesterApp(root, cfg)      # noqa: F841
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("[MAIN] Przerwano (Ctrl+C)")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
