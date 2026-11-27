# Combo Test PSU - instrukcja operatora

Stanowisko sprawdza zasilacz w dwóch krokach: **1. test obciążenia**, **2. Hi-Pot (wysokie napięcie)**.
Hi-Pot wykonuje się tylko wtedy, gdy test obciążenia przeszedł.

> **Bezpieczeństwo:** w trakcie testu w gnieździe jest 230 V i wysokie napięcie Hi-Pot.
> Nie otwieraj klapy w trakcie testu. Otwarcie klapy przerywa test - sztukę trzeba przetestować od nowa.

## 1. Start zmiany

1. Uruchom „Combo Test PSU”.
2. Z listy wybierz profil (PN zasilacza).
3. W oknie potwierdzenia sprawdź PN i listę urządzeń, na które wchodzi zasilacz. Zgodne - **TAK**.
4. Zeskanuj numer seryjny (SN) i naciśnij **POTWIERDŹ**.
5. Na ekranie testu sprawdź, czy 4 statusy w prawym górnym rogu są zielone:
   Chroma 19052, OEL8512, Interlock, Przekaźnik. Czerwony status - zgłoś inżynierowi.

## 2. Test sztuki

1. Włóż zasilacz do gniazda i podłącz wtyki.
2. **Zamknij klapę** - test startuje sam (albo przycisk **START TEST**).
3. Poczekaj na wynik. Postęp widać na panelach „1. OBCIĄŻENIE” i „2. HI-POT”.
4. Po wyniku otwórz klapę, wyjmij zasilacz i odłóż go zgodnie z wynikiem.
5. Zeskanuj kolejny SN w oknie „Następny SN” i zamknij klapę.

## 3. Wyniki

| Wynik | Co oznacza | Co zrobić |
|---|---|---|
| **PASS** (zielony) | oba testy zaliczone | sztuka dobra |
| **FAIL** (czerwony) | sztuka nie przeszła testu (przyczyna pod wynikiem) | odłożyć jako NOK |
| **NIEWAŻNY** (pomarańczowy) | problem stanowiska (komunikacja, przekaźnik), nie sztuki | powtórzyć test; jeśli się powtarza - zgłosić |
| **PRZERWANY** | STOP albo otwarta klapa w trakcie testu | powtórzyć test |
| Hi-Pot **POMINIĘTY** | test obciążenia nie przeszedł - Hi-Pot nie był robiony | sztuka NOK |

Najczęstsze przyczyny FAIL:
* **Brak napięcia z zasilacza (LOAD-NC)** - sprawdź, czy zasilacz jest podłączony i wtyki dobrze siedzą.
  Jeśli tak - sztuka NOK.
* **Napięcie za niskie / za wysokie** - zasilacz poza tolerancją, sztuka NOK.
* **Hi-Pot FAIL** - sztuka NOK, nie testować ponownie bez decyzji jakości.

## 4. Komunikaty

| Komunikat | Co zrobić |
|---|---|
| „Zamknij klapę, aby rozpocząć test” | zamknąć klapę |
| „Wynik tego SN jest zapisany - kliknij Następny SN” | zeskanować nowy SN |
| „DUPLIKAT: SN był już testowany” | sprawdzić SN; test ponowny tylko celowo (liczy się jako RETEST) |
| „Możliwa usterka stanowiska” (3 takie same FAIL z rzędu) | sprawdzić podłączenie gniazda i przewodów, zgłosić inżynierowi |
| „⚠ statystyki offline” | testować dalej - wyniki dośle się same; jeśli długo nie znika, zgłosić |
| „⚠ zegar PC różni się od serwera” | zgłosić do IT |
| Czerwony baner **TRYB AWARYJNY** | stanowisko robi tylko jeden z testów - pracować wg decyzji przełożonego |

## 5. Licznik zmiany

Na dole ekranu: liczba testów, PASS, FAIL (z podziałem: obciążenie / Hi-Pot) i RETEST bieżącej zmiany
(I 06-14, II 14-22, III 22-06). Szczegóły - przycisk **📊 Statystyki**.
