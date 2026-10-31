---
name: atnos-implementation-strategy
description: Zaplanuj i wykonaj zmianę Atnosa, gdy funkcja lub refaktoryzacja wymaga wyboru rozwiązania i połączenia warstw aplikacji. Nie uruchamiaj dla drobnej korekty tekstu lub CSS bez decyzji architektonicznej.
---

## PURPOSE
Przełóż cel użytkownika na najmniejszą kompletną zmianę wspólnej aplikacji z mierzalnym wynikiem.

## WHEN TO USE
Przed nową funkcją, istotną refaktoryzacją albo integracją backendu, interfejsu i procesu produkcji.

## REQUIRED WORKFLOW
1. Ustal oczekiwane zachowanie, zakres i dotychczasową autoryzację. Sprawdź istniejący diff; zachowaj niezwiązane zmiany.
2. W E:/Atnos/App czytaj odpowiednie frontend/src oraz runtime-recovered/api/src/atnos. Rozpocznij od konsumenta danych i prześledź wywołanie do serwisu lub production/order_store.py.
3. Gdy zmiana dotyczy pamięci, przeczytaj docs/atnos_project_brain.md: services/project_brain_service.py synchronizuje stan operacyjny, production/project_content_brain przechowuje wiedzę kanału. Nie twórz trzeciego źródła prawdy.
4. Przed kodem wybierz istniejący przepływ, wskaż kontrakty, konsumentów i kryterium akceptacji. Nazwij ryzyko regresji, zależności, granice kosztu oraz proporcjonalną ścieżkę cofnięcia zmiany.
5. Wykonaj autoryzowaną zmianę etapami. Przed działaniem zewnętrznym sprawdź zakres konkretnej zgody; wykorzystaj istniejącą zgodę obejmującą czynność.
6. Dobierz weryfikację z frontend/package.json lub runtime-recovered/api/tests. Rozdziel dowód implementacji od działania uruchomionego procesu.

## ATNOS INVARIANTS
Stosuj E:/AGENTS.md; zaplanuj także temp, cache i raporty na E:. Zachowaj provenance pamięci, statusy UNKNOWN i granicę produkcja–publikacja. Zmiany produkcji muszą zachować lease, checkpointy, historię prób i liczniki kosztu. Renderer i QA wynikają z zamrożonego profilu; ręczny MP4 ratunkowy nie dowodzi naprawy procesu.

## VERIFICATION
Sprawdź pełne kryterium akceptacji i zmienione kontrakty. Podaj wykonane kontrole oraz brakujący dowód LIVE; mock potwierdza wyłącznie sprawdzony kontrakt.

## DO NOT
Nie zastępuj integracji nową osobną aplikacją. Nie wdrażaj, nie restartuj ani nie uruchamiaj płatnej produkcji tylko dlatego, że zakończyła się edycja. Nie odnawiaj już udzielonej zgody bez zmiany zakresu.
