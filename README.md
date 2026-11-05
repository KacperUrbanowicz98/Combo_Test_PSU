# Reconext Combo Test PSU 1.0.0

Stanowisko testowe zasilaczy: **Hi-Pot (Chroma 19052) + test obciążenia (OWON OEL8512)** w jednym cyklu,
z przełączaniem toru przez przekaźniki sterowane z Arduino/STM.
Bazą jest Hi-Pot PSU 1.1.2 (wygląd, logika Chromy, interlock, raporty IFS, statystyki).
Sterowanie OEL8512 wg baseline **OEL85 Verifier 1.2.4** (`oel85.py`, `transport.py`, `simulator.py`
skopiowane bez zmian — sumy SHA-256 zgodne z `BASELINE_OEL8512_FW201.md`).

## Uruchomienie

```
pip install pyserial
python main.py          # sprzęt
python main.py --sim    # symulacja Chromy, OEL8512 i Arduino/STM
python create_exe.py    # -> dist/Combo Test PSU/  (kopiować cały folder)
```

Bez logowania HRID — w statystykach i nazwach katalogów zmian jest **nazwa stanowiska**
(ENG → Ogólne; domyślnie nazwa komputera).

## Przebieg testu

1. Wybór profilu z listy (tylko aktywne profile) → potwierdzenie parametrów → skan SN (walidacja długości).
2. Ekran testu: łączenie w tle z Chromą (konfiguracja kroku + odczyt zwrotny), OEL8512 i Arduino/STM.
3. START (przycisk albo zamknięcie klapy):
   1. `PATH HIPOT` — przekaźniki, Arduino/STM musi odpowiedzieć `OK PATH HIPOT`,
   2. **Hi-Pot** — logika z Hi-Pot PSU 1.1.2 (`result_validator.py`: PASS z Chromy tylko z dowodem
      wykonania testu, napięcie i prąd w limitach). Raport Hi-Pot zapisywany od razu,
   3. `PATH OFF`. Hi-Pot **FAIL → koniec**, obciążenie pominięte (zasilacz z FAIL nie dostaje 230 V),
   4. `PATH LOAD` — 230 V na zasilacz, wyjście na OEL8512,
   5. **Obciążenie, 2 punkty CC**: MIN (`i_min`; 0 A = pomiar bez obciążenia, wejście OEL wyłączone)
      i MAX (`i_max`). Po każdym punkcie średnia z `samples` odczytów. PASS punktu: `v_min ≤ V ≤ v_max`,
      prąd pobierany zgodny z nastawą (tolerancja `i_tol_pct` / `i_tol_abs`), brak zadziałania OVP/OCP/OPP,
   6. `PATH OFF`, wejście OEL OFF — zawsze, także po STOP / otwarciu klapy / błędzie.
4. Wynik całkowity: PASS tylko gdy oba testy PASS. NIEWAŻNY (brak komunikacji, brak potwierdzenia
   przekaźników, błąd konfiguracji OEL) i PRZERWANY nie są liczone w statystykach.

Konfiguracja OEL przed **każdym** testem: `INP 0`, `MODE CURR` (+ `MODE?`), `VOLT:PROT`, `CURR:PROT`,
`POW:PROT`, `VOLT:ON`, `CURR` — każdy zapis sprawdzany: `ERR` po komendzie + odczyt zwrotny.
Zabezpieczenia wyliczane z profilu (`load_tester.protection_levels`): OVP = 1.2·Vmax + 1 V,
OCP = 1.25·Imax + 0.2 A, OPP = 1.3·Vmax·Imax + 2 W, Von = 0.5·Vmin. Bez `*RST` w cyklu.

## Widok ekranu testu (ENG → Ogólne)

* **oba testy na ekranie** — panele HI-POT i OBCIĄŻENIE obok siebie,
* **jeden po drugim** — widoczny tylko aktywny test, nad nim „Krok 1/2 … 2/2”, na końcu podsumowanie.

Na ekranie: aktualny SN, status połączeń (Chroma / OEL8512 / Arduino/STM), aktywny tor przekaźników,
stan klapy, pomiary na żywo, historia 5 ostatnich wyników (Hi-Pot / obciążenie / wynik), licznik zmiany.

## Raporty (osobny plik na test)

| Test | Plik | Katalog (ENG → Logi) |
|---|---|---|
| Hi-Pot | `SN_RRRRMMDDggmmss.txt`, format Chroma 19052 **bez zmian** | `log_dir` (IFS) |
| Obciążenie | `SN_RRRRMMDDggmmss_LOAD.txt` | `load_log_dir` (domyślnie osobny, żeby IFS go nie pobierał) |
| Test w pętli ENG | `loop_<PN>_<data>.csv` | `eng_log_dir` |

Przy błędzie zapisu — kopia w `logs_awaryjne` obok aplikacji i komunikat dla operatora.

## Panel ENG

Wejście: **Ctrl+Alt+D ×3** albo „⚙ Panel ENG” w stopce ekranu wyboru profilu.

| Hasło domyślne | Dostęp |
|---|---|
| ENG: `eng123` | wszystkie zakładki poza „Hasła” |
| Admin: `admin123` | wszystko + zmiana haseł |

Hasła w `config.json` jako SHA-256 z solą. **Zmień je po pierwszym uruchomieniu.**

Zakładki: Chroma Hi-Pot · OEL8512 (test `*IDN?`, odczyt V/I, wejście OFF) · Arduino/STM (stan klapy,
ręczne przełączanie torów z potwierdzeniem) · Profile (włącz/wyłącz, dodaj, edytuj, duplikuj, usuń,
**test w pętli**) · Logi · Ogólne · Hasła. Zmiany konfiguracji i profili są logowane na konsolę
(`[ENG] Zmiana konfiguracji: …`).

**Test w pętli**: pełna sekwencja N razy na jednej sztuce (np. złoty wzorzec), klapa zamknięta przez
cały czas, otwarcie przerywa. Tabela cykli + min/śr/max (Im Hi-Pot, V@MIN, V@MAX), zapis do CSV.
Nie trafia do IFS ani do statystyk zmiany.

## Profile (`models.py` / `models.json`)

11 PN występujących i w profilach Hi-Pot, i w tabelach obciążenia. Format PN: `PSU-000006-00`
(stare `PSU-00006-00` z Hi-Pot PSU są automatycznie migrowane). Profile bez danych obciążenia
(`PSU-000019-00`, `01403-00307`, `01403-00244`) i bez profilu Hi-Pot (`PSU-000050-00`) pominięte.

| PN | Hi-Pot | Okno V | MIN / MAX |
|---|---|---|---|
| PSU-000003-00 | AC 3000 V, 0.3–2.5 mA, 1 s | 11.4–12.6 V | 0 / 2.0 A |
| PSU-000005-00 | AC 3300 V, 0.01–10 mA, 3 s | 11.65–12.85 V | 0 / 3.7 A |
| PSU-000006-00 | AC 3000 V, 0.3–2.5 mA, 1 s | 11.4–12.6 V | 0 / 1.42 A |
| PSU-000007-00 | AC 3000 V, 0.3–2.5 mA, 1 s | 11.4–12.6 V | 0 / 1.42 A |
| PSU-000008-00 | AC 3000 V, 0.3–2.5 mA, 1 s | 11.4–12.6 V | 0 / 1.42 A |
| PSU-000013-00 | AC 3000 V, 0.15–2.5 mA, 1 s | 4.50–5.50 V | 0 / 1.12 A |
| PSU-000016-00 | AC 3000 V, 0.15–2.5 mA, 1 s | 4.50–5.50 V | 0 / 1.12 A |
| PSU-000033-00 | AC 3000 V, 0.15–2.5 mA, 1 s | 4.75–5.25 V | 0 / 1.5 A |
| PSU-000049-00 | DC 4242 V, 0–3.5 mA, 1 s | 11.4–12.6 V | 0 / 3.0 A |
| MPD-PW00476AA | AC 3750 V, 0.23–10 mA, 3 s | 11.4–12.6 V | 0.2 / 3.33 A |
| 01403-00516 | AC 3750 V, 0.23–10 mA, 3 s | 11.4–12.6 V | 0.2 / 3.33 A |

Czasy domyślne obciążenia: załączenie 2 s, ustalanie 1 s/punkt, 5 próbek, tolerancja prądu 3 % / 20 mA.

## Arduino/STM — protokół (`interlock.py`, `fixture_controller.ino`)

9600 8N1, linie z `\n`. Arduino → PC co ≤1 s: `OPEN` / `CLOSED` (jak w Hi-Pot PSU).
PC → Arduino: `PATH OFF|HIPOT|LOAD`, `PATH?` (keepalive co 1 s, gdy tor ≠ OFF).
Odpowiedzi: `OK PATH <tor>`, `ERR LID_OPEN`, `ERR BAD_PATH`, `PATH <tor>`.
Wymagania po stronie Arduino/STM: break-before-make, tory tylko przy zamkniętej klapie, otwarcie klapy =
natychmiast OFF, watchdog 5 s bez komend = OFF. `fixture_controller.ino` to **szkic referencyjny** —
piny i poziomy przekaźników do potwierdzenia ze schematem (Arduino IDE wymaga, żeby szkic leżał
w folderze o tej samej nazwie).

## Symulacja (`--sim`)

Końcówka SN wymusza usterkę: `…HF` Hi-Pot FAIL, `…LV` za niskie napięcie, `…LI` zasilacz nie daje prądu,
`…RF` brak potwierdzenia przekaźników. Przycisk „SIM: klapa” otwiera/zamyka klapę.

## Do potwierdzenia / otwarte

* `MPD-PW00476AA`, `01403-00516`: w tabeli 3.33 A i 45 W (12 V × 3.33 A = 40 W) — w profilu 3.33 A.
* `PSU-000005-00`: „Max = -12.85 V” w tabeli potraktowane jako 12.85 V.
* Wariant widoku (oba testy / jeden po drugim) — do wyboru po testach na żywo.
* Skrypt testowy dla High Runnera — odłożony.
* Przekaźniki/szkic Arduino — do weryfikacji na sprzęcie przed pierwszym HV.
