# Combo Test PSU 1.3.0 - checklista testów na stanowisku

Każdy punkt: wykonaj, porównaj z wynikiem oczekiwanym, zaznacz [x] i wpisz uwagi.
Testy z wysokim napięciem wykonuje tylko osoba uprawniona, przy zamkniętej klapie.
Wszystko poniżej zostało sprawdzone w symulacji (`python main.py --sim`) - tu potwierdzamy na sprzęcie.

Data: ____________  Stanowisko: ____________  Wykonał: ____________

---

## 0. Przygotowanie

- [ ] Kompilacja na PC stanowiska: `python create_exe.py` -> folder `dist/Combo Test PSU/` skopiowany w całości
- [ ] Pierwsze uruchomienie: brak błędów, `combo_errors.log` pusty
- [ ] Druga kopia aplikacji: komunikat „Aplikacja jest już uruchomiona” i brak drugiego okna
- [ ] Porty COM z Menedżera urządzeń wpisane w ENG: Chroma ____  OEL8512 ____  Interlock ____  Przekaźnik ____
- [ ] ENG -> Logi: raporty Hi-Pot = katalog IFS, raporty obciążenia, statystyki (wspólny katalog dla stanowisk)
- [ ] ENG -> Logi -> „Sprawdź dostępność”: wszystkie katalogi ✓, zegar PC vs serwer ✓ (różnica: ____ s)

## 1. Połączenia (bez zasilacza w gnieździe)

| # | Krok | Oczekiwane | OK | Uwagi |
|---|---|---|---|---|
| 1.1 | ENG -> Chroma Hi-Pot -> Testuj połączenie | ✓ i identyfikator Chromy | [ ] | |
| 1.2 | ENG -> Test obciążenia OEL8512 -> Testuj / Odczyt V/I | ✓, V ok. 0 V, brak zabezpieczeń | [ ] | |
| 1.3 | ENG -> Interlock -> Połącz, otwórz / zamknij klapę | stan zmienia się w < 1 s | [ ] | |
| 1.4 | Ekran testu: 4 statusy połączeń zielone | Chroma, OEL8512, Interlock, Przekaźnik | [ ] | |

## 2. Protokół przekaźnika (ENG -> Przekaźnik, BEZ zasilacza w gnieździe)

| # | Krok | Wynik / odpowiedź modułu |
|---|---|---|
| 2.1 | Połącz, „Wyślij linię”: `load` | odpowiedź: ______________ |
| 2.2 | „Wyślij linię”: `hipot` | odpowiedź: ______________  słychać przełączenie: TAK / NIE |
| 2.3 | `LOAD` / `HIPOT` (wielkie litery) | działa: TAK / NIE |
| 2.4 | Nieznana komenda, np. `test` | odpowiedź: ______________ |
| 2.5 | Terminator: działa z `\n`? z `\r\n`? | ______________ |
| 2.6 | Czas powrotu do LOAD po `hipot` (stoper) | ______ s |
| 2.7 | `hipot`, po 20 s ponownie `hipot` - czy liczy od nowa? | TAK / NIE |

- [ ] Jeśli moduł odpowiada (np. `OK`) - wpisać fragment w „Oczekiwana odpowiedź” i zapisać.
      Bez potwierdzenia martwy moduł daje fałszywe FAIL sztuk (patrz 6.6).
- [ ] „Powrót modułu do LOAD” = zmierzony czas z 2.6
- [ ] Izolacja torów sprawdzona miernikiem przez osobę odpowiedzialną za moduł (bez HV): w LOAD wyjście
      Hi-Pot odłączone od zasilacza, w HIPOT wyjście zasilacza odłączone od OEL8512

## 3. Test obciążenia (złoty wzorzec, dla każdego używanego PN)

| PN | V @ MIN | V @ MAX | I @ MAX | Miernik ref. V | Wynik | Uwagi |
|---|---|---|---|---|---|---|
| | | | | | | |
| | | | | | | |
| | | | | | | |

- [ ] Napięcie ustala się przed końcem „Załączenie” (podgląd na żywo) - w razie potrzeby wydłużyć w profilu
- [ ] Prąd pobierany zgodny z nastawą (tolerancja 3 % / 20 mA)
- [ ] Raport `SN_..._LOAD.txt` w katalogu raportów obciążenia

## 4. Zabezpieczenia OEL8512 (kopia profilu, potem usunąć)

- [ ] ENG -> Profile -> Duplikuj, w kopii V max = 8 V (dla zasilacza 12 V) -> OVP zadziała
      Oczekiwane: FAIL, kod LOAD-PROT, wejście OEL wyłączone, Hi-Pot pominięty
- [ ] Kopia profilu usunięta / wyłączona po teście

## 5. Hi-Pot

- [ ] Złoty wzorzec: PASS, napięcie i prąd na żywo zgodne z Chromą
- [ ] Raport w katalogu IFS, odebrany przez IFS (format identyczny jak z Hi-Pot PSU - sprawdzone bajt w bajt)
- [ ] Sztuka znana jako zła (jeśli dostępna): FAIL z kodem Chromy
- [ ] Hi-Pot kończy się przed powrotem przekaźnika (brak komunikatu o przekroczeniu czasu)

## 6. Bezpieczeństwo sekwencji

| # | Krok | Oczekiwane | OK |
|---|---|---|---|
| 6.1 | Otwarcie klapy w trakcie testu obciążenia | PRZERWANY natychmiast, wejście OEL OFF, Hi-Pot nie startuje | [ ] |
| 6.2 | Otwarcie klapy w trakcie Hi-Pot | HV OFF natychmiast (wyświetlacz Chromy), po ok. 1 s przekaźnik na LOAD | [ ] |
| 6.3 | STOP w trakcie obciążenia i w trakcie Hi-Pot | jak 6.1 / 6.2 | [ ] |
| 6.4 | Brak zasilacza w gnieździe / odpięte wyjście | FAIL LOAD-NC, Hi-Pot pominięty | [ ] |
| 6.5 | FAIL obciążenia (zła sztuka albo kopia profilu z wąskim oknem V) | Hi-Pot NIE jest wykonywany | [ ] |
| 6.6 | 3 kolejne takie same FAIL | ostrzeżenie „Możliwa usterka stanowiska” | [ ] |
| 6.7 | Start bez nowego SN po teście | blokada, komunikat „kliknij Następny SN” | [ ] |
| 6.8 | Ten sam SN drugi raz w zmianie | ostrzeżenie o duplikacie, po potwierdzeniu RETEST | [ ] |

## 7. Utrata komunikacji (w trakcie testu, bez HV dla 7.2-7.4)

| # | Krok | Oczekiwane | OK |
|---|---|---|---|
| 7.1 | Odpięcie RS232 Chromy w trakcie Hi-Pot | NIEWAŻNY, wynik nie zapisany (sprawdzić HV OFF na Chromie) | [ ] |
| 7.2 | Odpięcie USB OEL8512 w trakcie obciążenia | NIEWAŻNY | [ ] |
| 7.3 | Odpięcie USB przekaźnika | NIEWAŻNY, start zablokowany do „Połącz ponownie” | [ ] |
| 7.4 | Odpięcie interlocka | test przerwany, start zablokowany | [ ] |
| 7.5 | Ponowne podpięcie + „Połącz ponownie” | wszystkie statusy zielone | [ ] |

## 8. Statystyki

- [ ] Licznik na ekranie po kilku testach zgodny z historią (testy / PASS / FAIL obc. / FAIL Hi-Pot / retest)
- [ ] Okno „📊 Statystyki”: dzień i zmiany zgodne
- [ ] Odłączenie sieci: „⚠ statystyki offline (N)”, po podłączeniu w ciągu 30 s znika, wpisy na udziale
- [ ] Przełom zmiany (06 / 14 / 22): licznik przechodzi na nową zmianę bez restartu

## 9. Tryby awaryjne (ENG -> Ogólne -> Tryb stanowiska)

- [ ] „Tylko obciążenie”: czerwony baner, Chroma nie jest wymagana, tylko raport LOAD, brak raportu IFS
- [ ] „Tylko Hi-Pot”: czerwony baner, OEL8512 nie jest wymagany, tylko raport Hi-Pot
- [ ] Po restarcie aplikacji tryb wraca na COMBO

## 10. Stabilność i decyzje

- [ ] Test w pętli złotego wzorca, 20 cykli (ENG -> Profile -> Test w pętli): rozrzut V@MAX ____ V, Im ____ mA, CSV zapisany
- [ ] Wariant widoku wybrany: oba testy na ekranie / jeden po drugim (ENG -> Ogólne)
- [ ] Parametry profili zatwierdzone z jakością (ENG -> Profile -> Eksport CSV)

## Ustalenia do wpisania w ENG

| Parametr | Wartość |
|---|---|
| Oczekiwana odpowiedź przekaźnika | |
| Terminator przekaźnika | |
| Powrót modułu do LOAD [s] | |
| Ustalanie po przełączeniu [ms] | |
| Rozładowanie HV przed LOAD [s] | |
| Wariant widoku | |
