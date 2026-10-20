"""Hasło do panelu ENG / Admin (hash SHA-256 z solą, bez plaintextu w configu)."""
import hashlib
import hmac
import logging

log = logging.getLogger(__name__)


def hash_password(password, salt):
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify_password(cfg, password):
    sec = cfg.security
    ok = hmac.compare_digest(hash_password(password, sec["salt"]), sec["password_hash"])
    log.info("Logowanie ENG/Admin: %s", "OK" if ok else "BŁĘDNE HASŁO")
    return ok


def ask_password(parent, cfg, title="Logowanie ENG"):
    """Modalne okno hasła. Zwraca True jeśli hasło poprawne."""
    import tkinter as tk
    from tkinter import ttk, messagebox

    result = {"ok": False}
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(parent)

    frm = ttk.Frame(dlg, padding=14)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Hasło:").grid(row=0, column=0, sticky="w")
    var = tk.StringVar()
    ent = ttk.Entry(frm, textvariable=var, show="•", width=24)
    ent.grid(row=0, column=1, padx=(8, 0))
    ent.focus_set()

    def ok(_=None):
        if verify_password(cfg, var.get()):
            result["ok"] = True
            dlg.destroy()
        else:
            messagebox.showerror("Błąd", "Nieprawidłowe hasło", parent=dlg)
            var.set("")

    btns = ttk.Frame(frm)
    btns.grid(row=1, column=0, columnspan=2, pady=(12, 0), sticky="e")
    ttk.Button(btns, text="OK", command=ok).pack(side="left", padx=4)
    ttk.Button(btns, text="Anuluj", command=dlg.destroy).pack(side="left")
    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", lambda e: dlg.destroy())
    dlg.grab_set()
    parent.wait_window(dlg)
    return result["ok"]
