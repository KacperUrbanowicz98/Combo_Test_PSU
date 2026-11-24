# Reconext Combo Test PSU 1.1.1

Stanowisko testowe zasilaczy: **test obciążenia (OWON OEL8512) + Hi-Pot (Chroma 19052)** w jednym cyklu,
z przełączaniem zasilacza modułem przekaźników (komendy `load` / `hipot`) i interlockiem klapy.
Bazą jest Hi-Pot PSU 1.1.2 (wygląd, logika Chromy, interlock, raporty IFS, statystyki).
Sterowanie OEL8512 wg baseline **OEL85 Verifier 1.2.4** (`oel85.py`, `transport.py`, `simulator.py`
skopiowane bez zmian - sumy SHA-256 zgodne z `BASELINE_OEL8512_FW201.md`).

## Uruchomienie

```
pip install pyserial
python main.py          # sprzęt
python main.py --sim    # symulacja Chromy, OEL8512, interlocka i przekaźnika
python create_exe.py    # -> dist/Combo Test PSU/  (kopiować cały folder)
```

Bez logowania HRID - w statystykach i nazwach katalogów zmian jest **nazwa stanowiska**
(ENG → Ogólne; domyślnie nazwa komputera).

## Przebieg testu

1. Wybór profilu z listy (tylko aktywne profile) → potwierdzenie parametrów (PN, urządzenia, na które
   wchodzi zasilacz, parametry obciążenia i Hi-Pot) → skan SN (walidacja długości).
2. Ekran testu: łączenie w tle z Chromą (konfiguracja kroku + odczyt zwrotny), OEL8512, interlockiem
   i przekaźnikiem (na starcie wysyłane `load` - stan spoczynkowy modułu).
3. START (przycisk albo zamknięcie klapy). **Klapa jest nasłuchiwana przez cały test** - otwarcie albo
   utrata sygnału interlocka natychmiast przerywa test (wejście OEL OFF / Hi-Pot STOP), wynik PRZERWANY:
   1. przekaźnik `load` - 230 V na zasilacz, wyjście na OEL8512,
   2. **Obciążenie**: najpierw kontrola podłączenia - przy wyłączonym wejściu OEL musi być napięcie
      ≥ 0.3·`v_min` (min. 0.5 V). Brak napięcia = **FAIL `LOAD-NC`** (zasilacz niepodłączony / martwy).
      Potem 2 punkty CC: MIN (`i_min`; 0 A = pomiar bez obciążenia) i MAX (`i_max`), średnia z `samples`
      odczytów. PASS punktu: `v_min ≤ V ≤ v_max`, prąd zgodny z nastawą (`i_tol_pct` / `i_tol_abs`),
      brak OVP/OCP/OPP. Raport obciążenia zapisywany od razu,
   3. obciążenie **FAIL / NIEWAŻNY → koniec, Hi-Pot nie jest wykonywany**,
   4. wejście OEL OFF, przekaźnik `hipot`,
   5. **Hi-Pot** - logika z Hi-Pot PSU 1.1.2 (`result_validator.py`: PASS z Chromy tylko z dowodem
      wykonania testu). Limit czasu = okno przekaźnika (patrz niżej). Raport Hi-Pot (IFS),
   6. zawsze po Hi-Pot (także po STOP / klapie): STOP Chromy → odczekanie `hv_discharge_s` → `load`.
4. Wynik całkowity: PASS tylko gdy oba testy PASS. NIEWAŻNY (brak komunikacji, przekaźnik nie
   odpowiada, błąd konfiguracji OEL) i PRZERWANY nie są liczone w statystykach.

**Okno przekaźnika.** Moduł sam wraca do `load` po ~30 s bez aktywności (`revert_s`). Żeby przekaźnik
nie przełączył się na OEL w trakcie wysokiego napięcia:
* profil, którego Hi-Pot (rampa + test + opadanie) nie mieści się w `revert_s − settle − 3 s`, jest
  blokowany **przed** startem (komunikat na ekranie testu),
* w trakcie Hi-Pot limit czasu liczony jest od przełączenia na `hipot`; po przekroczeniu STOP i NIEWAŻNY.

Wątek STOP / klapy nigdy nie wysyła komend do przekaźnika - robi to wyłącznie sekwencja, po wyłączeniu HV.

Konfiguracja OEL przed **każdym** testem: `INP 0`, `MODE CURR` (+ `MODE?`), `VOLT:PROT`, `CURR:PROT`,
`POW:PROT`, `VOLT:ON`, `CURR` - każdy zapis sprawdzany: `ERR` po komendzie + odczyt zwrotny.
Zabezpieczenia wyliczane z profilu (`load_tester.protection_levels`): OVP = 1.2·Vmax + 1 V,
OCP = 1.25·Imax + 0.2 A, OPP = 1.3·Vmax·Imax + 2 W, Von = 0.5·Vmin. Bez `*RST` w cyklu.

## Widok ekranu testu (ENG → Ogólne)

* **oba testy na ekranie** - panele 1. OBCIĄŻENIE i 2. HI-POT obok siebie,
* **jeden po drugim** - widoczny tylko aktywny test, nad nim „Krok 1/2 … 2/2”, na końcu podsumowanie.

Na ekranie: aktualny SN, status połączeń (Chroma / OEL8512 / Interlock / Przekaźnik), pozycja przekaźnika
(LOAD / HI-POT), stan klapy, pomiary na żywo, historia 5 ostatnich wyników (obciążenie / Hi-Pot / wynik),
licznik zmiany.

## Raporty (osobny plik na test)

| Test | Plik | Katalog (ENG → Logi) |
|---|---|---|
| Hi-Pot | `SN_RRRRMMDDggmmss.txt`, format Chroma 19052 **bez zmian** | `log_dir` (IFS) |
| Obciążenie | `SN_RRRRMMDDggmmss_LOAD.txt` | `load_log_dir` (domyślnie osobny, żeby IFS go nie pobierał) |
| Test w pętli ENG | `loop_<PN>_<data>.csv` | `eng_log_dir` |

Przy błędzie zapisu - kopia w `logs_awaryjne` obok aplikacji i komunikat dla operatora.

Sztuka z FAIL obciążenia ma **tylko raport LOAD** (Hi-Pot nie był wykonywany) - nie pojawi się w IFS.
Kontrola duplikatu SN przegląda oba katalogi (Hi-Pot i obciążenie).

## Panel ENG

Wejście: **Ctrl+Alt+D ×3** albo „⚙ Panel ENG” w stopce ekranu wyboru profilu.

Hasło: to samo co w pozostałych aplikacjach stanowiskowych (w `auth.py` tylko hash SHA-256 z solą).

Zakładki:
1. **Chroma Hi-Pot** - port, baudrate, test połączenia,
2. **Test obciążenia OEL8512** - port, VID:PID, baudrate, timeout, przerwa, terminator; `*IDN?`, odczyt V/I, wejście OFF,
3. **Przekaźnik** - port, komendy `load` / `hipot`, terminator, oczekiwana odpowiedź, czasy; ręczne LOAD /
   HI-POT, **wysyłanie dowolnej linii z podglądem odpowiedzi** (do rozpoznania protokołu modułu),
4. **Interlock** - port, baudrate, włącz/wyłącz; podgląd stanu klapy,
5. **Profile** - włącz/wyłącz, dodaj, edytuj, duplikuj, usuń, **test w pętli**,
6. **Logi**, 7. **Ogólne**.

Zmiany konfiguracji i profili są logowane na konsolę (`[ENG] Zmiana konfiguracji: …`).

**Test w pętli**: pełna sekwencja N razy na jednej sztuce (np. złoty wzorzec), klapa zamknięta przez
cały czas, otwarcie przerywa; pętla zatrzymuje się po pierwszym wyniku innym niż PASS.
Tabela cykli + min/śr/max (V@MIN, V@MAX, Im Hi-Pot), zapis do CSV.
Nie trafia do IFS ani do statystyk zmiany.

## Profile (`models.py` / `models.json`)

11 PN występujących i w profilach Hi-Pot, i w tabelach obciążenia. Format PN: `PSU-000006-00`
(stare `PSU-00006-00` z Hi-Pot PSU są automatycznie migrowane). Profile bez danych obciążenia
(`PSU-000019-00`, `01403-00307`, `01403-00244`) i bez profilu Hi-Pot (`PSU-000050-00`) pominięte.

| PN | Hi-Pot | Okno V | MIN / MAX |
|---|---|---|---|
| PSU-000003-00 | AC 3000 V, 0.3-2.5 mA, 1 s | 11.4-12.6 V | 0 / 2.0 A |
| PSU-000005-00 | AC 3300 V, 0.01-10 mA, 3 s | 11.65-12.85 V | 0 / 3.7 A |
| PSU-000006-00 | AC 3000 V, 0.3-2.5 mA, 1 s | 11.4-12.6 V | 0 / 1.42 A |
| PSU-000007-00 | AC 3000 V, 0.3-2.5 mA, 1 s | 11.4-12.6 V | 0 / 1.42 A |
| PSU-000008-00 | AC 3000 V, 0.3-2.5 mA, 1 s | 11.4-12.6 V | 0 / 1.42 A |
| PSU-000013-00 | AC 3000 V, 0.15-2.5 mA, 1 s | 4.50-5.50 V | 0 / 1.12 A |
| PSU-000016-00 | AC 3000 V, 0.15-2.5 mA, 1 s | 4.50-5.50 V | 0 / 1.12 A |
| PSU-000033-00 | AC 3000 V, 0.15-2.5 mA, 1 s | 4.75-5.25 V | 0 / 1.5 A |
| PSU-000049-00 | DC 4242 V, 0-3.5 mA, 1 s | 11.4-12.6 V | 0 / 3.0 A |
| MPD-PW00476AA | AC 3750 V, 0.23-10 mA, 3 s | 11.4-12.6 V | 0.2 / 3.33 A |
| 01403-00516 | AC 3750 V, 0.23-10 mA, 3 s | 11.4-12.6 V | 0.2 / 3.33 A |

Czasy domyślne obciążenia: załączenie 2 s, ustalanie 1 s/punkt, 5 próbek, tolerancja prądu 3 % / 20 mA.

## Przekaźnik i interlock - protokół (`relay.py`, `interlock.py`)

**Przekaźnik** (ENG → Przekaźnik): PC wysyła `load` albo `hipot` + terminator (domyślnie `\n`).
Domyślnie bez potwierdzenia - po komendzie odczekanie `settle_ms`. Jeśli moduł odpowiada (np. `OK`),
wpisz fragment odpowiedzi w „Oczekiwana odpowiedź” - wtedy brak odpowiedzi w `timeout_s` = test NIEWAŻNY,
linia zaczynająca się od `ERR` = odrzucenie. Jeśli przekaźnik i interlock są na **tym samym porcie COM**,
aplikacja używa jednego połączenia (komendy idą przez port interlocka).

**Interlock**: 9600 8N1, Arduino nadaje cyklicznie `OPEN` / `CLOSED` (jak w Hi-Pot PSU). Brak danych > 5 s
= utrata interlocka (przerwanie testu, blokada startu).

## Symulacja (`--sim`)

Końcówka SN wymusza usterkę: `…HF` Hi-Pot FAIL, `…LV` za niskie napięcie, `…LI` zasilacz nie daje prądu,
`…NC` zasilacz niepodłączony. Przycisk „SIM: klapa” otwiera/zamyka klapę. Symulowany moduł przekaźników
odpowiada `OK load` / `OK hipot` i sam wraca do LOAD po `revert_s`; zasilacz daje napięcie tylko w `load`,
a prąd Hi-Pot płynie tylko w `hipot`.

## Do potwierdzenia / otwarte

* `MPD-PW00476AA`, `01403-00516`: w tabeli 3.33 A i 45 W (12 V × 3.33 A = 40 W) - w profilu 3.33 A.
* `PSU-000005-00`: „Max = -12.85 V” w tabeli potraktowane jako 12.85 V.
* Wariant widoku (oba testy / jeden po drugim) - do wyboru po testach na żywo.
* Skrypt testowy dla High Runnera - odłożony.
* Moduł przekaźników: wielkość liter komend, terminator, czy odpowiada (ENG → Przekaźnik → „Wyślij linię”),
  dokładny czas powrotu do `load` i czy liczy go od ostatniej komendy - do sprawdzenia na sprzęcie
  przed pierwszym HV. Port domyślny `COM7` (interlock `COM5`) - ustawić w ENG.
