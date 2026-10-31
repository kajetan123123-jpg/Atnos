# Rzeczywisty status Atnosa

Data oceny: **2026-10-31**. Zakres: bez restartów, nowych generacji, publikacji, Gmaila i płatnych wywołań. To status aplikacji rozwijanej lokalnie i bezpiecznego snapshotu źródeł, nie certyfikacja produkcyjna.

Oznaczenia dowodów: **TEST** = wykonany test izolowany; **LIVE-READ** = bieżący odczyt działającej usługi; **KOD** = analiza implementacji; **HISTORYCZNE** = wcześniejszy lokalny wynik, nie odtworzony w tej sesji; **NIEZWERYFIKOWANE** = brak aktualnego dowodu.

## Moduły

| Obszar | Status i podstawa |
| --- | --- |
| API | LIVE-READ: `/health/live` i `/health/ready` odpowiedziały HTTP 200, ready=true. Uruchomiony proces pochodzi z poprzedniego dnia; nie restartowano go. Pole code_revision nie jest pełnym hashem drzewa źródłowego. |
| Frontend | LIVE-READ: lokalny listener obecny. TEST: wyniki bieżącego sprawdzenia poniżej. Pełna obsługa desktopowa przez użytkownika nie została tu powtórzona. |
| Desktop | KOD: host C#/WebView2 i launcher. NIEZWERYFIKOWANE: zgodność bieżącego okna desktop z publikowanym snapshotem. Binariów i SDK brak w repo. |
| Ollama | LIVE-READ: endpoint katalogu modeli HTTP 200; modele lokalne dostępne w katalogu. Nie wykonywano inferencji, więc jakość rozumowania/cytowania i gotowość GPU nie są potwierdzone. |
| Czat/narzędzia | KOD: routing, strumieniowanie, pamięć i operatory. Działa częściowo; nie deklarujemy autonomicznego kodowania bez nadzoru ani poziomu GPT/Claude. Testy narzędzi używają izolacji/mocków. |
| Obsidian i wiedza kanałów | KOD: pamięć decyzji, kontekst kanału i logowanie. Prywatnego vaultu nie opublikowano. Brak nowego testu LIVE odczyt-zapis w tej sesji. |
| Scenariusze | KOD: generacja, claim gates, wersjonowane korekty i review. Zgodność semantyczna konkretnego nowego tekstu wymaga review; modelowy PASS sam nie wystarcza. |
| Ilustracje/ComfyUI | LIVE-READ: brak listenera standardowej lokalnej usługi podczas audytu. Konfiguracja innych adresów nie została uruchomiona. Zatwierdzona biblioteka kanału i modele nie są dołączone; świeży clone wymaga setup. |
| Lektor | HISTORYCZNE: pełna lokalna wersja Ryana neutral była dopuszczona przez użytkownika do dalszych prób WB. To nie globalne zatwierdzenie głosu ani dowód działania syntezy na nowej instalacji. |
| Montaż/Remotion | KOD i HISTORYCZNE: rzeczywisty film WB 39,13 s, 1080×1920, H.264/AAC istniał i przeszedł wcześniejszą techniczną kontrolę. Nie wykonano nowego renderu w tej sesji. |
| Napisy i QA | KOD: timing oparty na audio, tryb bez karaoke, kontrole renderu i mediów. HISTORYCZNE: sprawdzano wybrane klatki filmu. Pełny własny ciągły przegląd ruchu i odsłuch pozostają NIEZWERYFIKOWANE; nie deklarujemy pełnego odbioru AV. |
| Kolejka/workery | LIVE-READ: kontrola aktywności nie zgłosiła blokad; automatyka tła API wyłączona. Nie uruchamiano zlecenia ani nie odtwarzano awarii workera. KOD: lease, dispatcher, checkpointy i recovery. |
| Automatyczna publikacja | TEST: coordinator i upload guard mają izolowane testy. KOD: trwała kolejka, deduplikacja, polityka globalna i blokada local_only. NIEZWERYFIKOWANE: aktualny OAuth oraz rzeczywisty upload/schedule/readback. Nie wdrożono kompletnej, centralnej akceptacji człowieka per rewizja wszystkich ścieżek. |
| Pozostałe kanały | KOD: profile i części infrastruktury są obecne. NIEZWERYFIKOWANE: powtarzalna produkcja pełnych, odebranych bajek 4–7 i horrorów. WB pozostaje priorytetem. |

## Testy tej sesji

Wyniki są uzupełniane po rzeczywistym uruchomieniu. Podane testy nie są generacją mediów ani testem prawdziwych kont. Pełny historyczny zestaw testów nie został uruchomiony.

| Kontrola | Wynik |
| --- | --- |
| Backend: 11 wskazanych plików testowych, izolowane bazy i mocki | **124 PASS, 5 subtestów PASS**, 93,25 s |
| Frontend: PublicationPanel, api.productionOrders, productionMessages | **40 PASS** |
| Frontend: Settings, końcowa próba po anonimizacji | **16 PASS** |
| Renderer: cały obecny zestaw 19 plików | **175 PASS** |
| Renderer: `tsc --noEmit` | PASS |
| Frontend: design tokens, kodowanie PL, `tsc -b`, `vite build` | PASS; ostrzeżenie chunków >500 kB |
| Parsowanie publicznego `.env.example` przez Settings | PASS; automatyka i upload publiczny wyłączone, kontrola kosztu włączona |

Polecenia z odpowiednich katalogów (Python z istniejącego środowiska, zależności Node z istniejącej instalacji na E:, kod z kopii publicznej):

```text
python -m pytest tests/test_publication_coordinator.py tests/test_youtube_upload_guard.py tests/test_production_preflight.py tests/test_remotion_renderer_checkpoint_identity.py tests/test_chat_source_grounding.py tests/test_chat_capability_truth.py tests/test_chat_session_memory.py tests/test_agent_memory.py tests/test_command_surface_api.py tests/test_trusted_auto_workflow.py tests/test_atnos_agent_identity.py -q --tb=short
node node_modules/vitest/vitest.mjs run src/components/PublicationPanel.test.tsx src/lib/api.productionOrders.test.ts src/lib/productionMessages.test.ts src/routes/Settings.test.tsx --maxWorkers=1 --minWorkers=1
node node_modules/vitest/vitest.mjs run src/routes/Settings.test.tsx --maxWorkers=1 --minWorkers=1
node node_modules/vitest/vitest.mjs run --maxWorkers=1 --minWorkers=1
node node_modules/typescript/bin/tsc --noEmit
node scripts/verify-design-tokens.mjs
node scripts/verify-polish-text-encoding.mjs
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

Pierwsze próby kopii wykryły brak modeli ORM, dwóch modułów usług, jednej migracji oraz rejestru instrukcji skilli. Zasoby źródłowe przywrócono do paczki, bez danych runtime. Poprawiono także anonimizowany locator testu UI i kodowanie jego pliku. Końcowy zestaw backendu obejmuje wcześniejsze błędy; wcześniejszych nieudanych prób nie uznano za PASS. Wyniki frontendu obejmują 40 testów z trzech plików oraz końcowe 16 Settings, bez sumowania powtórzeń. Testy nie dowodzą czystej instalacji zależności ani działania dostawców LIVE.


## Co blokuje deklarację gotowości AV

1. Świeża instalacja wymaga modeli, workflow, fontów, zależności przeglądarki oraz zaakceptowanej biblioteki ilustracji. Nie są częścią publicznego pakietu.
2. Działający jeden przykład WB nie dowodzi powtarzalności. Trzy briefy: mechanizm, przykład liczbowy i decyzja Alexa nie zostały wszystkie ukończone i odebrane. Nie każda kompozycja obsługuje każdą relację/postać.
3. Pełny odsłuch i ciągła ocena ruchu pozostają odrębne od QC/ffprobe/vision. Nie wykonano ich ponownie.
4. API i workery muszą potwierdzać spójność załadowanego kodu, canonical, planu i checkpointów przed kolejną produkcją. Sam ready=true tego nie dowodzi.
5. Publikacja wymaga rzeczywistych uprawnień i bramek. Obecny audyt niczego nie wysłał ani nie opublikował.

## Zakres publicznego snapshotu i kontrola bezpieczeństwa

- Oryginalna lokalna historia i historia GitHub nie mają wspólnego przodka. Snapshot opiera się na istniejącym zdalnym main; nie zawiera prywatnej historii rozwoju i nie zastępuje lokalnego repozytorium.
- Wyłączono bazy, modele, nagrania, gotowe filmy, cache, logi, raporty operacyjne, profile, OAuth, Vault oraz prywatne katalogi grafik/głosu. Instrukcje i dane lokalnej instalacji pozostają na komputerze.
- Publiczna kopia zastępuje identyfikatory kont i adresy wdrożenia nieczynnymi przykładami. Zmienione zostały także odpowiadające im oczekiwania testów. Operacyjna konfiguracja nie została zmieniona. Nie jest to kopia bitowa środowiska produkcyjnego.
- Skaner sekretów oraz dodatkowy przegląd danych są częścią przygotowania publikacji. Sztuczne sekrety w testach redakcji nie są kluczami usług; nie zastosowano globalnego wyłączenia skanowania testów.
- Brak znalezionego sekretu nie jest pełnym audytem podatności lub zależności. Publiczne API dostępu do PC nie jest rekomendowanym modelem wdrożenia; aplikacja ma pozostać lokalna.
