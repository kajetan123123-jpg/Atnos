---
name: atnos-systematic-debugging
description: Diagnozuj zgłoszony błąd naruszający kontrakt Atnosa przez odtworzenie i ustalenie przyczyny. Łącz ze skillem domenowym przy starym backendzie lub błędnym renderze. Sam brak danych albo dowodu LIVE wymaga najpierw oceny evidence; pomijaj nowe funkcje, copy i CSS bez błędu.
---

## PURPOSE
Usuń przyczynę potwierdzonego błędu, zachowując dowody i trwającą pracę.

## WHEN TO USE
Gdy interfejs, backend, model albo zlecenie zachowuje się inaczej niż wymagany kontrakt.

## REQUIRED WORKFLOW
REPRODUCE → COLLECT EVIDENCE → CLASSIFY FAILURE → FIND ROOT CAUSE → MINIMAL FIX → REGRESSION TEST → LIVE VERIFY. Skill domenowy dostarcza kontrakt; ta procedura dostarcza metodę diagnozy.

1. Odtwórz symptom najmniejszym bezpiecznym odczytem lub testem; zapisz oczekiwany wynik, czas i identyfikator. Zbieraj tylko potrzebne, zredagowane logi do E:/Atnos/App/reports.
2. Rozdziel kod na dysku od uruchomionego procesu. Odczytaj właściwy endpoint i ustal wersję, PID, executable, cwd oraz entrypoint. Sam otwarty port nie dowodzi własności ani załadowania poprawki.
3. Prześledź jeden przepływ w frontend/src i runtime-recovered/api/src/atnos. Dla produkcji czytaj production/order_store.py, checkpointy i istniejące tests/test_production_order_store.py; dla lifecycle sprawdź services/runtime_stop_safety.py i Launch-Atnos.ps1.
4. Klasyfikuj błąd jako dane/kontrakt, kod, dependency, proces lub niepewny wynik dispatch. Sformułuj jedną hipotezę oraz obserwację, która może ją obalić; oznacz użycie mocków.
5. Popraw źródło problemu i zweryfikuj ten sam scenariusz oraz najbliższy kontrakt regresyjny. Przy błędzie hipotezy wróć do dowodów, zamiast dodawać kolejną niezależną poprawkę.

## ATNOS INVARIANTS
Zachowaj aktywne zlecenia, GPU lock i lease workerów. Timeout nie upoważnia do zabicia procesu. Odczyt unknown nie oznacza zero błędów. Nie usuwaj failure history ani provenance Project Brain. Temp i Python cache muszą pozostać na E:.

## VERIFICATION
Wskaż reprodukcję przed i po poprawce, test regresji i osobny wynik LIVE albo niewykonaną kontrolę. Testy lease/accounting nie dowodzą działania modeli LIVE. Odczyt istniejącego pakietu nie zastępuje sprawdzenia poprawionej ścieżki; ponowienie wymaga ustalonego wyniku wcześniejszego dispatch i istniejącej autoryzacji.

## DO NOT
Nie patchuj losowo ani kilku subsystemów naraz. Nie restartuj stosu jako pierwszej diagnozy. Nie kradnij lease, nie zeruj liczników i nie ponawiaj wywołania po niejasnym timeout bez ustalenia wyniku. Nie podmieniaj filmu ratunkowym MP4.
