# instance_lock.py
"""
Blokada drugiej kopii aplikacji na tym samym komputerze.
Dwie kopie walczyłyby o porty COM (Chroma, OEL8512, interlock, przekaźnik) i dublowały
wpisy statystyk stanowiska.

Windows: nazwany mutex (zwalniany przez system także po awarii procesu).
Inne systemy: blokada pliku (flock) obok aplikacji.
"""
import os
import sys

_handle = None          # trzymane do końca procesu


def acquire(name: str = "ReconextComboTestPSU") -> bool:
    """True = jesteśmy jedyną kopią. False = aplikacja już działa."""
    global _handle
    if sys.platform.startswith("win"):
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.CreateMutexW.restype = ctypes.c_void_p
            h = k32.CreateMutexW(None, False, f"Local\\{name}")
            if not h:
                return True                     # nie da się sprawdzić - nie blokujemy pracy
            if k32.GetLastError() == 183:       # ERROR_ALREADY_EXISTS
                k32.CloseHandle(ctypes.c_void_p(h))
                return False
            _handle = h
            return True
        except Exception as e:
            print(f"[LOCK] Mutex niedostępny: {e}")
            return True
    try:
        import fcntl
        from app_paths import app_dir
        f = open(os.path.join(app_dir(), f".{name}.lock"), "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        _handle = f
        return True
    except Exception as e:
        print(f"[LOCK] Blokada pliku niedostępna: {e}")
        return True
