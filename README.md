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

## Hasła (domyślne — zmień w Panelu Admin → „Zmiana haseł”)

| Panel | Hasło domyślne | Uwagi |
|---|---|---|
| ENG | `eng123` | konsola SCPI, sterowanie, pomiar, diagnostyka |
| Admin | `admin123` | ustawienia + zmiana obu haseł; hasło Admin otwiera też panel ENG |

Hasła są zapisane w `config.json` jako hash SHA-256 z solą. Przy aktualizacji z v1.0 dotychczasowe wspólne hasło staje się hasłem ENG, a hasło Admin dostaje wartość domyślną.

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
| ODRZUCONA | urządzenie odpowiedziało `ERR` (na zapytanie albo zaraz po zapisie) |
| ZNANY FW | niezgodność już potwierdzona na OEL8512 FW V2.0.1 — nie liczy się jako nowy błąd (lista w `command_catalog.KNOWN_FW`) |
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

## Zmiany w v1.1 (po pierwszym teście na OEL8512, FW V2.0.1)

- Odpowiedź `ERR` ma osobny status **ODRZUCONA**. Po każdym zapisie aplikacja nasłuchuje przez czas „przerwy po komendzie”, czy urządzenie nie odesłało `ERR` — wtedy zapis też jest oznaczany jako odrzucony.
- Puste linie w odpowiedzi są pomijane (czekamy na właściwą wartość); jeśli przyszła tylko pusta linia — status FAIL „Pusta linia” + HEX w logu.
- `*RST`: pauza `rst_wait_ms` (domyślnie 2000 ms) i ponowne `SYST:REM`.
- Panel ENG → konsola: podgląd **HEX** surowych bajtów odpowiedzi, szybki przycisk `SYST:SENS?`.
- `DYN:HIGH:DWEL`: wartości testowe 0.5 / 1.0 s (0.01 z manuala było poniżej minimum) + sondy MIN i skan minimum.
- `BAT:CST` / `BAT:TIM` testowane po włączeniu `BAT:CEN ON` / `BAT:TEN ON` + sondy zakresu MIN/MAX.
- Nowa grupa **4.7b** (wymaga potwierdzenia wolnych zacisków): odczyty `BAT:STAT?/ALL?/CAPA?/ENERGY?/TIME?` w trakcie uruchomionego testu baterii.
- Sondy `VOLT:RANG` / `CURR:RANG` z wartościami liczbowymi (MIN/MAX zwracało `1`).
- Sondy pełnych nazw `OCP:IDELaystep` / `OPP:IDELaystep`.
- Osobne hasła ENG i Admin.
- CLI na prawdziwym urządzeniu pyta o wolne zaciski przed testami INP (pominięcie: `--yes`).

## Zmiany w v1.2 (po drugim przebiegu)

- `*RST` restartuje OEL8512 (na UART pojawia się bajt 0x00). Aplikacja odrzuca bajty 0x00 i po `*RST` czeka, aż urządzenie znów odpowie na `*IDN?` (max `rst_wait_ms` = 10 s), potem `SYST:REM`. Driver: `OEL85.reset()` robi to samo.
- `BAT:VST` testowane przy `BAT:VEN ON` (bez tego zapis jest cicho ignorowany), sondy zakresu MIN/MAX przy włączonych warunkach.
- Status **ZNANY FW** dla potwierdzonych niezgodności — raport pokazuje je osobno, więc nowy FAIL = nowy problem.
- Sonda `P_ERR_QUEUE`: czy nieudokumentowane `SYST:ERR?` rejestruje błędy (nieznana komenda, wartość poza zakresem).

## OEL8512 FW V2.0.1 — co ustaliliśmy

| Obszar | Wynik |
|---|---|
| CC/CV/CR/CP, INP/SHORT, OVP/OCP/OPP, Von, slew, DYN, OCP/OPP (bez IDEL), MEAS, PAR | działa zgodnie z manualem |
| Odrzucony zapis | urządzenie wysyła `ERR` zaraz po komendzie (np. `CURR:SLEW:FALL FAST`, `DYN:HIGH:DWEL 0.05`) |
| Zapis cicho ignorowany | `BAT:VST/CST/TIM` przy wyłączonym `BAT:VEN/CEN/TEN` — bez `ERR`, tylko readback to wykryje |
| `*RST` | restart urządzenia, kilka sekund bez odpowiedzi |
| `MODE?` | po BAT/OCP/OPP zwraca `ocp` / `opp` / `continuous` |
| `OCP:IDEL?` | pusta linia |
| Zaraz po `*RST` | `*IDN?` odpowiada wcześniej niż reszta; `SYST:SENS?` dawało pustą linię, `CURR?` milczało — po pełnym starcie działają |
| `VOLT:RANG?`, `CURR:RANG?` | zawsze `1` |
| `BAT:CAPA?/ENERGY?/TIME?/STAT?/ALL?`, `OPP:IDEL` | `ERR` (także w trakcie testu baterii) |
| `DYN:HIGH:DWEL` | minimum 0.1 s |
| Spoza manuala | `SYST:ERR?` → zawsze `0x0000` (nie rejestruje błędów), `SYST:VERS?` → `V2.0.1`, `*OPC?` → ciąg wersji, `*ESR?`/`*STB?` → `N/A` |
| Zakresy BAT | `VST` 0–150 V, `CST` 0–999.999 Ah, `TIM` 0–999999 s |
| Weryfikacja zapisu w stanowisku | `ERR` po komendzie + readback (SYST:ERR? bezużyteczne) |

## Zmiany w v1.2.1

- Po `*RST` czekamy najpierw na `*IDN?`, potem na `MODE?` (część sterująca startuje później), dopiero wtedy `SYST:REM`.
- `SYST:SENS` usunięte z listy ZNANY FW — działa; pusta linia była skutkiem testu tuż po restarcie.

## Zmiany w v1.2.3

- Raport v1.2.2 pokazał, że po `*RST` urządzenie odpowiada na `*IDN?` i na jedno `MODE?`, a potem jeszcze przez chwilę milczy albo zwraca puste linie. Teraz po `*RST` wymagane są **3 kolejne poprawne rundy `MODE?` + `CURR?`** (max `rst_wait_ms` = 20 s). Czas do ustabilizowania jest zapisywany w raporcie przy kroku `*RST`.
- Ta sama logika w driverze: `oel85.wait_stable_after_reset()` / `OEL85.reset()`.
- Zalecenie dla stanowiska: nie używać `*RST` w cyklu testu — ustawiać komplet parametrów jawnie.
