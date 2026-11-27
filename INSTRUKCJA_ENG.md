# Combo Test PSU - instrukcja inżynieryjna (ENG)

## Wejście do panelu ENG

**Ctrl+Alt+D** trzy razy w ciągu sekundy albo „⚙ Panel ENG” w stopce ekranu wyboru profilu.
Hasło - to samo co w pozostałych aplikacjach stanowiskowych. Panel jest dostępny tylko z ekranu
wyboru profilu (nie w trakcie testu).
Zmiany pól zapisuje przycisk **💾 Zapisz ustawienia** (do `config.json` obok aplikacji).

## Zakładki

| Zakładka | Do czego |
|---|---|
| Chroma Hi-Pot | port COM i baudrate Chromy 19052, test połączenia |
| Test obciążenia OEL8512 | port, VID:PID (auto-wybór portu po podpięciu w inne USB), baudrate, timeout, przerwa, terminator; test `*IDN?`, odczyt V/I, wyłączenie wejścia; **test przełączania wejścia** (N x INP 1/0 z zasilaczem - czasy i ponowienia) |
| Przekaźnik | port, komendy `load` / `hipot`, terminator, oczekiwana odpowiedź, czasy; ręczne LOAD / HI-POT i „Wyślij linię” z podglądem odpowiedzi |
| Interlock | port i baudrate Arduino klapy, włącz / wyłącz interlock, podgląd klapy |
| Profile | baza profili: dodaj, edytuj, duplikuj, włącz / wyłącz, usuń, test w pętli, eksport CSV |
| Logi | katalogi: raporty Hi-Pot (IFS), raporty obciążenia, testy ENG, statystyki; port dashboardu; „Sprawdź dostępność” (zapis + zegar PC) |
| Ogólne | nazwa stanowiska, wariant widoku, autozapis, limit czasu Hi-Pot, **tryb stanowiska** |

## Przekaźnik - ustawienia

| Pole | Domyślnie | Uwagi |
|---|---|---|
| Komenda - test obciążenia / Hi-Pot | `load` / `hipot` | wg modułu |
| Terminator | `\n` | |
| Oczekiwana odpowiedź | pusto | **zalecane wpisać**, jeśli moduł odpowiada (np. `OK`). Bez tego martwy moduł nie jest wykrywany i sztuki dostają fałszywe FAIL |
| Timeout odpowiedzi | 2 s | używany tylko z „Oczekiwaną odpowiedzią” |
| Ustalanie po przełączeniu | 500 ms | czas na przełączenie styków |
| Powrót modułu do LOAD | 30 s | Hi-Pot musi się zmieścić w tym czasie minus ok. 3,5 s - dłuższy profil jest blokowany |
| Rozładowanie HV przed LOAD | 1 s | po Hi-Pot, zanim przekaźnik wróci na obciążenie |

Ten sam port COM co interlock = jedno wspólne połączenie (obsługiwane).

## Profile

* **Urządzenia** - lista modeli, na które wchodzi zasilacz; pokazywana operatorowi przy wyborze profilu.
* **Test obciążenia włączony** - wyłączony = profil tylko Hi-Pot.
* Limit LOW = 0 mA nie wykryje niepodłączonego zasilacza w Hi-Pot - aplikacja ostrzega przy zapisie.
* **Eksport CSV** - wszystkie parametry do Excela (do przeglądu z jakością), z kolumną
  „Hi-Pot mieści się w oknie przekaźnika”.
* **Test w pętli** - pełna sekwencja N razy na jednej sztuce (złoty wzorzec). Wyniki tylko do CSV
  w katalogu testów ENG, nie do IFS i statystyk. Zatrzymuje się po pierwszym wyniku innym niż PASS.

## Tryb stanowiska (awaria jednego z testerów)

ENG -> Ogólne -> Tryb stanowiska:
* **COMBO** - normalna praca,
* **tylko test obciążenia** - awaria Chromy (brak raportu Hi-Pot do IFS!),
* **tylko Hi-Pot** - awaria OEL8512.

Zmiana działa od razu, wymaga potwierdzenia i obowiązuje **do zamknięcia aplikacji** (po restarcie COMBO).
Na ekranach widoczny czerwony baner. W statystykach takie testy liczą się do swojego testu, ale nie do COMBO.

## Pliki obok aplikacji

| Plik | Zawartość |
|---|---|
| `config.json` | ustawienia (porty, katalogi, przekaźnik) |
| `models.json` | profile testowe |
| `combo_errors.log` | nieobsłużone błędy aplikacji |
| `stats_outbox.jsonl` | statystyki czekające na dosłanie (gdy udział był niedostępny) |
| `logs_awaryjne\` | raporty zapisane awaryjnie, gdy katalog docelowy był niedostępny |

Statystyki: `<katalog statystyk>\<stanowisko>\results_<dzień>.jsonl` - jedna linia na test.

## Symulacja

`python main.py --sim` - całe stanowisko bez sprzętu (statystyki do lokalnego `stats_sim`).
Końcówka SN wymusza usterkę:

| Końcówka | Usterka |
|---|---|
| HF | Hi-Pot FAIL |
| LV | napięcie za niskie |
| LI | zasilacz nie daje prądu |
| NC | zasilacz niepodłączony |
| RN | przekaźnik nie odpowiada i nie przełącza |
| RE | przekaźnik odpowiada ERR |
| RL | utrata portu przekaźnika w trakcie testu |
| ID | OEL ignoruje co drugą komendę INP pod obciążeniem |
| IX | OEL nie daje się włączyć pod obciążeniem |

Przycisk „SIM: klapa” otwiera / zamyka klapę.

## Kompilacja

```
pip install pyserial pyinstaller
python create_exe.py              -> dist\Combo Test PSU\  (kopiować cały folder)
```

## Typowe problemy

| Objaw | Sprawdź |
|---|---|
| Czerwony status urządzenia | port COM w ENG, kabel, „Połącz ponownie” |
| OEL8512 zmienia port po przepięciu | wpisany VID:PID w zakładce OEL8512 |
| Każda sztuka FAIL LOAD-NC | gniazdo, przewody wyjściowe, przekaźnik (ENG -> Przekaźnik -> `load`) |
| Każda sztuka Hi-Pot FAIL LOW (18) | przewody Hi-Pot, czy przekaźnik przełącza na HI-POT |
| NIEWAŻNY: „Przekaźnik nie potwierdził” | „Oczekiwana odpowiedź” zgodna z tym, co moduł odsyła („Wyślij linię”) |
| Profil zablokowany: „Hi-Pot trwa X s” | skrócić czasy Hi-Pot w profilu albo poprawić „Powrót modułu do LOAD” |
| „Aplikacja jest już uruchomiona” | druga kopia w tle - zamknąć ją (Menedżer zadań) |
| „⚠ zegar PC różni się od serwera” | synchronizacja czasu Windows (IT) |
