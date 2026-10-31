---
name: atnos-verification-before-completion
description: Sprawdź granicę mock a LIVE oraz świeże dowody ukończenia Atnosa; użyj także gdy capability przeszło mock, a LIVE nie jest wykazane lub zawiodło. Wynik to macierz twierdzeń i dowodów, nie diagnoza providera. Pomijaj samą drobną edycję copy/CSS bez oceny gotowości.
---

## PURPOSE
Dopasuj każde twierdzenie o ukończeniu do rzeczywiście uzyskanego dowodu.

## WHEN TO USE
Przy rozbieżności mock/LIVE i przed oceną gotowości wykonanej zmiany lub artefaktu. Skill domenowy ustala jak testować; ta procedura rozstrzyga, co wolno stwierdzić z uzyskanego dowodu.

## REQUIRED WORKFLOW
1. Utwórz macierz: twierdzenie → wymagany poziom dowodu → rzeczywiste polecenie/artefakt → wersja/czas → wynik → luka. Mock PASS z brakującym albo nieudanym LIVE daje osobne statusy, nigdy łączny PASS.
2. W E:/Atnos/App dobierz istniejące kontrole. Backend: runtime-recovered/api/tests i jego pyproject.toml; użyj istniejącego środowiska .venv/Scripts/python.exe po sprawdzeniu dostępności. Frontend: skrypty test, lint lub build z frontend/package.json. build:desktop również wdraża pliki.
3. Ustaw TEMP, TMP, PYTHONPYCACHEPREFIX i artefakty testów na E:. Uruchom tylko kontrole potrzebne do zmiany; sprawdź kod wyjścia oraz treść wyniku.
4. Oddziel mocki, testy offline i rzeczywisty proces. Odczyt /health lub HTTP 200 dowodzi ograniczonej dostępności, nie pełnego działania integracji.
5. Dla media sprawdź production/steps/order_delivery.py oraz order_store.py: finalne pliki, manifest, sumy i wymagane QA muszą odpowiadać bieżącym checkpointom. Obejrzyj i odsłuchaj wynik, jeśli twierdzisz, że jakość artefaktu jest poprawna.
6. Z macierzy wyprowadź dozwoloną deklarację i najmniejszą brakującą kontrolę. Jeżeli wykracza poza autoryzowany zakres, nazwij lukę. Stosuj istniejącą zgodę; nie pytaj ponownie o tę samą czynność.

## ATNOS INVARIANTS
UNKNOWN, schema_only i brak metryk pozostają niewiadomymi. Zachowaj provenance Project Brain. ready oznacza gotowość pakietu; scheduled i published wymagają odpowiednich odczytów YouTube oraz zgody opisanej w docs/ATNOS_PUBLICATION_COORDINATOR.md.

## VERIFICATION
Rozróżnij static/code inspection, mock test, contract test, local integration, live E2E i visual/human review. Podaj wykonane kontrole, wynik oraz zakres. Po poprawce powtórz dotknięte kontrole. DONE/PASS/LIVE/COMPLETE wymagają dowodu odpowiedniego poziomu; niewykonaną kontrolę nazwij wprost.

## DO NOT
Nie ogłaszaj LIVE na podstawie mocka, starego raportu albo testu składni. Nie obchodź QA ratunkowym MP4, nie zmieniaj statusu ręcznie i nie restartuj aktywnej produkcji dla wygody testu.
