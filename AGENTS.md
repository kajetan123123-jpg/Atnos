# Atnos — stałe zasady repozytorium

Obowiązują nadrzędne ustalenia z `E:/AGENTS.md`. Dokumentacja historyczna ani stare ścieżki nie zmieniają tych ustaleń.

- Przed implementacją sprawdź istniejący subsystem, kontrakty i konsumentów danych. Rozwijaj istniejący przepływ; równoległy system wymaga uzasadnienia po audycie.
- Zachowuj niezwiązane zmiany użytkownika i Cursora. Nie resetuj ani nie nadpisuj wspólnego drzewa pracy.
- Mock PASS nie oznacza LIVE PASS. Deklaracje ukończenia oprzyj na aktualnym dowodzie odpowiedniego zakresu; niewykonaną kontrolę nazwij wprost.
- Nie wymyślaj danych. UNKNOWN nie oznacza zera; hipoteza nie jest zaobserwowanym faktem. Zachowuj źródła, czas i provenance istniejących warstw Project Brain oraz Claim Evidence Gate.
- Sekrety obsługuj przez istniejący Vault. Nie zapisuj ich w kodzie, instrukcjach, logach, raportach ani przykładach.
- Chroń aktywne zlecenia i procesy innych aplikacji. Zmiany zachowują lease, checkpointy, historię prób, liczniki kosztu oraz tożsamość procesu.
- Atnos produkuje przez zwykły pipeline. Ręczne poprawienie finalnego MP4 nie dowodzi naprawy procesu; nie obchodź bramek jakości ani integralności.
- Gotowy plik nie jest publikacją ani akceptacją wyglądu. Publikacja wymaga istniejącej zgody i bramek aplikacji; stosuj już udzieloną zgodę w jej zakresie. `local_only` pozostaje lokalne.

Procedury zadaniowe są w `.agents/skills`; dobieraj tylko pasujące. Instrukcja skillu nie instaluje narzędzia, nie dodaje capability Atnosa i nie nadaje uprawnień.

## Lokalne wsparcie kodowania

Dla małych, jednoznacznych poprawek rozważ istniejący lokalny worker Atnosa zgodnie z `docs/local-coding-delegation.md`, aby ograniczać zużycie Codexa. Deleguj wąski zakres, nie całe zadanie architektoniczne. Tryb coding ma być strict_local, bez fallbacku do Codexa; wynik wymaga niezależnego przeglądu i zaufanych testów przed zastosowaniem. Nie ponawiaj nieudanych propozycji w nieograniczonej pętli i nie przedstawiaj propozycji jako ukończonej naprawy.
