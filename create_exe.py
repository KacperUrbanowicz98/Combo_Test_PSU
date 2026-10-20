"""Budowa OEL85_Verifier.exe (PyInstaller, jeden plik, bez konsoli).

Użycie (Windows, w folderze projektu):
    pip install -r requirements.txt pyinstaller
    python create_exe.py            -> dist/OEL85_Verifier.exe
    python create_exe.py --console  -> wersja z oknem konsoli (debug)

config.json, oel85_verifier.log i folder reports/ powstają obok exe przy pierwszym uruchomieniu.
"""
import os
import shutil
import subprocess
import sys

APP = "OEL85_Verifier"
HERE = os.path.dirname(os.path.abspath(__file__))
HIDDEN = ["eng_panel", "admin_panel", "simulator", "auth", "serial.tools.list_ports"]


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("Brak PyInstaller: pip install pyinstaller")

    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)
    spec = os.path.join(HERE, APP + ".spec")
    if os.path.exists(spec):
        os.remove(spec)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--name", APP]   # pyvisa trafi do exe tylko jeśli jest zainstalowane
    if "--console" not in sys.argv:
        cmd.append("--windowed")
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    icon = os.path.join(HERE, "app.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(HERE, "main.py"))

    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode:
        sys.exit(r.returncode)
    exe = os.path.join(HERE, "dist", APP + (".exe" if os.name == "nt" else ""))
    print(f"\nGotowe: {exe}")


if __name__ == "__main__":
    main()
