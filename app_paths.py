# app_paths.py
"""
Ustalanie ścieżek bazowych aplikacji.

Problem który to rozwiązuje:
    settings_manager używał nazw względnych ("config.json"), które Python
    rozwiązuje względem BIEŻĄCEGO KATALOGU ROBOCZEGO, a nie katalogu programu.
    Uruchomienie EXE ze skrótu z innym "Rozpocznij w:", z zadania
    harmonogramu albo z dysku sieciowego powodowało, że aplikacja czytała
    i tworzyła pliki ustawień w zupełnie innym miejscu — operatorzy,
    profile i ścieżka logów "znikały".
"""
import os
import sys


def app_dir() -> str:
    """Katalog, w którym leży aplikacja (EXE lub main.py)."""
    if getattr(sys, "frozen", False):          # PyInstaller
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir() -> str:
    """
    Katalog z zasobami spakowanymi do EXE (--add-data).
    Dla trybu onedir/onefile PyInstaller rozpakowuje je do sys._MEIPASS.
    """
    return getattr(sys, "_MEIPASS", app_dir())


def data_path(filename: str) -> str:
    """Pełna ścieżka do pliku danych zapisywalnego obok aplikacji."""
    return os.path.join(app_dir(), filename)


def seed_path(filename: str) -> str:
    """
    Pełna ścieżka do wzorcowego pliku dołączonego do EXE.
    Zwraca None jeśli plik nie został spakowany.
    """
    p = os.path.join(bundle_dir(), filename)
    return p if os.path.exists(p) else None
