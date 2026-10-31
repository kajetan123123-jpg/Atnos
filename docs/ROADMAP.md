# Roadmap Atnosa

Stan 2026-10-31. Dowody i ograniczenia są w [STATUS](STATUS.md); obecność modułu nie oznacza ukończenia integracji.

## Istniejące fundamenty

- FastAPI, frontend React/Studio i host Windows; health i rozdzielenie usług od modeli.
- Rejestrowane zlecenia, plany, wersje scenariusza, checkpointy, lease i odzyskiwanie.
- Routing modeli/narzędzi, pamięć rozmowy, Project Brain i integracja Obsidiana.
- Produkcja WB z Remotion, głosem lokalnym, napisami i technicznymi bramkami QA.
- Osobny coordinator publikacji z trwałą kolejką i ochroną przed duplikatami.

To fundamenty implementacyjne i częściowe dowody działania; nie lista ukończonych produktów.

## Następne trzy priorytety

1. **Odtwarzalne środowisko i uczciwa gotowość:** test czystej instalacji bez prywatnego stanu, poprawne szablony konfiguracji, dokładna tożsamość kodu API/workera, kompletne modele/fonty/workflow i czytelny preflight. Publiczny snapshot nie może udawać gotowego obrazu instalacyjnego.
2. **Powtarzalna jakość WB:** trzy różne briefy, właściwe typowane kompozycje, ilustracje zgodne z czynnością, wybrany głos, spokojne napisy; pełny odsłuch i obejrzenie każdego filmu. Uzupełnić rzeczywiste akcje Alexa i liczbowe porównania zamiast podmieniać je ogólną planszą. Nie obniżać progów, aby przepchnąć pipeline.
3. **Wiarygodny lokalny asystent:** realne scenariusze użytkownika dla cytowania, rozróżniania tytułu i opisu strony, wieloetapowych narzędzi, pamięci Obsidiana i wąskiego kodowania. Logować faktyczne wykonanie i częściowy sukces; zachować granice uprawnień, izolację zmian i kontrolę procesów.

## Kolejne etapy po zamknięciu priorytetów

- Centralna autoryzacja publikacji konkretnej rewizji/pakietu, jasne różnice globalnej polityki i zgody na film; objęcie starszych ścieżek uploadu i rzeczywisty kontrolowany test OAuth/upload/readback.
- Sprawdzenie odzyskiwania po awarii i niejednoznacznym wyniku dostawcy bez powielania kosztów/produkcji.
- Obsługa innych kanałów dopiero po udokumentowanym standardzie WB.
- Migracja Linux po inwentaryzacji Windows/WebView2/ścieżek, przeniesieniu sekretów i danych oraz próbie odtworzenia środowiska. Brak deklaracji gotowości migracyjnej.

Nowe generacje, płatne API i publikacje muszą mieć właściwy zakres autoryzacji. Repozytorium publiczne nie powinno otrzymywać danych potrzebnych wyłącznie działającej instalacji.
