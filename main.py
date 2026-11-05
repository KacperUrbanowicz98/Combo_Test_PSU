# main.py
"""
Reconext Combo Test PSU — stanowisko Hi-Pot (Chroma 19052) + obciążenie (OEL8512).

  python main.py          praca na sprzęcie
  python main.py --sim    symulacja Chromy, OEL8512 i Arduino/STM (bez sprzętu)
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


def main():
    ap = argparse.ArgumentParser(description="Reconext Combo Test PSU")
    ap.add_argument("--sim", action="store_true", help="symulacja sprzętu")
    args, _ = ap.parse_known_args()

    sys.excepthook = _log_crash
    root = tk.Tk()
    root.report_callback_exception = _log_crash

    from config import Config
    cfg = Config(simulation=args.sim)
    from gui import ComboTesterApp
    app = ComboTesterApp(root, cfg)      # noqa: F841
    root.mainloop()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
