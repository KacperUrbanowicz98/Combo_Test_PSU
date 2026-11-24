# create_exe.py
"""
Budowa "Combo Test PSU.exe" (PyInstaller, --onedir --windowed) - jak w Hi-Pot PSU.

  pip install pyserial pyinstaller
  python create_exe.py
Wynik: dist/Combo Test PSU/  (kopiować CAŁY folder - config.json i models.json
powstają obok .exe).
"""
import os
import subprocess
import sys

APP = "Combo Test PSU"
VERSION = (1, 1, 1, 0)
MODULES = ["app_paths", "config", "settings_manager", "auth", "models", "gui", "test_screen",
           "stats_window", "eng_panel", "hipot_device", "hipot_runner", "result_validator",
           "interlock", "relay", "load_tester", "combo_sequence", "oel85", "transport", "simulator",
           "simulators", "logger", "stats_manager", "shift_stats"]


def create_version_file():
    v = ".".join(str(x) for x in VERSION)
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={VERSION}, prodvers={VERSION}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'Reconext'),
        StringStruct('FileDescription', 'Combo Test PSU (Hi-Pot + Load)'),
        StringStruct('FileVersion', '{v}'),
        StringStruct('InternalName', '{APP}'),
        StringStruct('LegalCopyright', 'Reconext 2026'),
        StringStruct('OriginalFilename', '{APP}.exe'),
        StringStruct('ProductName', 'Reconext {APP}'),
        StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open("version_info.txt", "w", encoding="utf-8") as f:
        f.write(content)
    print("[+] Utworzono version_info.txt")


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[!] PyInstaller nie znaleziony. Instaluję...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    if not os.path.exists("main.py"):
        print("[!] BRAK PLIKU: main.py - uruchom w folderze projektu")
        input("\nNaciśnij Enter aby wyjść...")
        sys.exit(1)

    sep = ";" if os.name == "nt" else ":"
    extra = []
    for f in ("models.json", "config.json"):
        if os.path.exists(f):
            extra += ["--add-data", f"{os.path.abspath(f)}{sep}."]
            print(f"[+] {f} - spakowany jako wzorzec")
        else:
            print(f"[~] Brak {f} - powstanie przy pierwszym uruchomieniu")

    create_version_file()
    print("\n" + "=" * 55 + f"\n  {APP} - Builder EXE\n" + "=" * 55)
    cmd = [sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--clean", "--noconfirm",
           "--name", APP, "--version-file", "version_info.txt",
           "--hidden-import=tkinter", "--hidden-import=tkinter.messagebox",
           "--hidden-import=tkinter.filedialog", "--hidden-import=tkinter.ttk",
           "--hidden-import=serial", "--hidden-import=serial.tools.list_ports",
           "--hidden-import=serial.tools.list_ports_windows"]
    cmd += [f"--hidden-import={m}" for m in MODULES]
    if os.path.exists("app.ico"):
        cmd += ["--icon", "app.ico"]
    cmd += extra + ["main.py"]          # opcje przed plikiem wejściowym

    r = subprocess.run(cmd)
    print("\n" + "=" * 55)
    exe = os.path.join("dist", APP, f"{APP}.exe")
    if r.returncode == 0 and os.path.exists(exe):
        print(f"[+] SUKCES: {os.path.abspath(exe)}")
        print(f"[!] Kopiuj cały folder 'dist/{APP}', nie sam .exe!")
    elif r.returncode == 0:
        print(f"[+] Build zakończony - szukaj w dist/{APP}/")
    else:
        print(f"[!] BŁĄD budowania (kod {r.returncode}) - zobacz build/{APP}/warn-{APP}.txt")
    print("=" * 55)
    input("\nNaciśnij Enter aby zamknąć...")


if __name__ == "__main__":
    main()
