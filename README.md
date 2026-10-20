# OEL85 Command Verifier

Narzędzie do sprawdzenia, które komendy SCPI z **OEL85 Programming Manual V1.0.2 (01.2025)** faktycznie działają na obciążeniu **OWON OEL8512** — przed integracją obciążenia ze stanowiskiem Hi-Pot.
Zasada testu: **zapis → odczyt zwrotny → porównanie**. Manual nie dokumentuje kolejki błędów (`SYST:ERR?`), więc odczyt zwrotny jest jedyną pewną metodą weryfikacji.

Testy działają **bez niczego podłączonego do zacisków** obciążenia. Wymagane jest tylko połączenie OEL ↔ PC (RS232 albo USB).

## Uruchomienie

```
pip install -r requirements.txt
python main.py                 # GUI
python main.py --sim           # GUI z symulatorem (bez urządzenia)
python main.py --cli --sim     # konsola, symulator
python main.py --cli --port COM5 --baud 115200 --probes -v
python create_exe.py           # -> dist/OEL85_Verifier.exe
```

## Pliki (płaska struktura)

| Plik | Rola |
|---|---|
| `main.py` | start GUI / tryb CLI, logowanie do `oel85_verifier.log` |
| `gui.py` | okno główne: połączenie, lista testów, wyniki, log TX/RX |
| `eng_panel.py` | **Panel ENG**: konsola SCPI + sekwencje, sterowanie ręczne, pomiar na żywo, diagnostyka łącza (wykrywanie baudrate, terminatora, opóźnienie) |
| `admin_panel.py` | **Panel Admin**: parametry łącza, tolerancje, opcje testu, zmiana hasła (zmiany logowane) |
| `auth.py` | hasło ENG/Admin (hash SHA-256, nie plaintext) |
| `command_catalog.py` | katalog testów — wszystkie komendy z manuala + sondy niejasności |
| `verifier.py` | silnik testu (wątek roboczy, STOP, bezpieczne wyłączenie na końcu) |
| `oel85.py` | **driver OEL85** — API do reużycia w docelowym stanowisku Hi-Pot + obciążenie |
| `transport.py` | RS232/USB-COM (pyserial), VISA (opcjonalnie), symulator |
| `simulator.py` | symulator OEL8512 do testu aplikacji bez sprzętu |
| `report.py` | raport CSV (Excel PL, `;`) + podsumowanie TXT do `reports/` |
| `config.py` | `config.json` obok exe |
| `create_exe.py` | budowa exe (PyInstaller) |

**Hasło domyślne ENG/Admin: `eng123` — zmień w Panelu Admin.**

## Pierwsze połączenie z prawdziwym OEL

1. Manual **nie podaje parametrów RS232**. Domyślnie 115200 8N1, terminator `\n`. Jeśli `*IDN?` nie odpowiada: rozłącz → Panel ENG → *Diagnostyka łącza* → **Wykryj baudrate**.
2. Port USB: jeśli zgłasza się jako wirtualny COM — interfejs „RS232 / USB-COM”. Jeśli jako USBTMC — zainstaluj `pyvisa pyvisa-py` i wybierz „VISA”, w polu port wpisz zasób (np. `USB0::0x5345::...::INSTR`).
3. Po wyborze portu zapamiętywany jest też jego **VID:PID**, więc zmiana numeru COM nie psuje konfiguracji.

## Wyniki

| Status | Znaczenie |
|---|---|
| PASS | odczyt zgodny z zapisaną wartością |
| FAIL | odpowiedź inna niż oczekiwana (wartość/format) |
| NO RESP | brak odpowiedzi na zapytanie w czasie timeoutu |
| ERROR | wyjątek / błąd komunikacji |
| INFO ✓ / ✗ | **sonda** — sprawdzenie niejasności manuala lub komendy spoza manuala; nie wpływa na wynik |

Każdy przebieg zapisuje automatycznie `reports/OEL85_verify_<data>.csv` + `.txt`.

## Bezpieczeństwo

- Na starcie i na końcu testu (także po STOP / błędzie) wysyłane jest `INP:SHOR 0`, `INP 0`, `SYST:SENS 0`.
- Grupa *4.1 Wejście* (INP 1 / INP:SHOR 1) wymaga potwierdzenia: zaciski wolne, Hi-Pot wyłączony.
- Panel ENG ma stały przycisk **WEJŚCIE OFF (INP 0)** działający także w trakcie testu.

## Niejasności manuala sprawdzane sondami

- `SYST:REM?` / `SYST:LOC?` w przykładach, choć to komendy bez zapytania.
- `VOLT:SLEW RISE FAST` (spacja zamiast `:`) w przykładzie.
- `MODE`: lista parametrów zawiera OVP, opis mówi OPP; `MODE?` nie wymienia `battery`.
- `CURR:SLEW:FALL`: parametr opisany jako FAST/NORM/SLOW i jednocześnie A/µs.
- `CURR:PROT:DEL`, `POW:PROT:DEL` — wspomniane, nieopisane.
- `DYN:HIGH:DEWLl` — literówka w spisie treści.
- Prefiks `:[:SOURce]:BATtery…` zapisany niestandardowo.
- Sondy spoza manuala (grupa domyślnie wyłączona): `SYST:ERR?`, `*OPC?`, `*ESR?`, `*STB?`, `SYST:VERS?`, `*CLS`, nieistniejąca komenda.

Wartości testowe dobrane dla OEL8512 (150 V / 40 A / 400 W). Symulator nie jest wzorcem zachowania prawdziwego urządzenia — dopiero przebieg na OEL pokaże faktyczny stan komend.
