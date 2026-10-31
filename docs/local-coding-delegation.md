# Lokalne delegowanie kodowania

Atnos może przygotowywać małe poprawki przez istniejący `AiDelegationRouter` i Ollamę. To propozycje kodu, nie autonomiczne wdrażanie. Używaj dla jednoznacznych zadań dotyczących 1–3 małych istniejących plików. Diagnoza, decyzje architektoniczne, ocena bezpieczeństwa i odbiór pozostają po stronie nadzorującego wykonawcy.

## Przepływ Codex → Atnos → review

1. Ustal problem i oczekiwany wynik. Wybierz konkretne pliki; nie przekazuj całego repozytorium.
2. Wywołaj istniejący `POST http://127.0.0.1:8000/api/ai-workers/delegate`, np.:

```json
{
  "task_type": "coding",
  "prompt": "Jedno precyzyjne zadanie i kryteria odbioru",
  "context": {"source_paths": ["runtime-recovered/api/src/atnos/services/example.py"]},
  "preferred_worker": "ollama",
  "allowed_workers": ["ollama"],
  "privacy_mode": "strict_local",
  "priority": "normal",
  "requested_by": "codex_supervised",
  "requires_approval_for_result": true
}
```

Ścieżka w przykładzie jest ilustracyjna: wybierz rzeczywiście istniejący plik. W czacie odpowiada temu `ai_delegate_background` z `task_type=coding`, `source_paths` i opisem zadania. Ten tryb przypina Ollamę i strict_local niezależnie od preferencji workera; nie uruchamia fallbacku do Codexa. `ai_delegate_status` pokazuje zapisane zadania. Narzędzie musi być dostępne w aktualnym rejestrze czatu (lokalne pliki/zapis włączone).

3. Sprawdź wynik, a nie samo `completed`. `verification.status=awaiting_human_review` oznacza propozycję. Walidacja transportu ani składni nie ocenia poprawności semantycznej. Python przechodzi `ast.parse`; TypeScript/CSS wymagają osobnej kontroli.
4. Porównaj bieżącą treść plików z `source_sha256` (UTF-8 tekst po normalizacji końców linii przez Python read_text; to nie hash surowych bajtów). Jeśli źródło się zmieniło, odrzuć propozycję. Przejrzyj cały diff i zakres.
5. Dopiero po niezależnym przeglądzie zastosuj minimalną poprawkę zwykłymi narzędziami repozytorium i uruchom odpowiednie, zaufane testy. Nie uruchamiaj automatycznie poleceń ani testów otrzymanych od modelu. Zachowaj dowód review i wyników testów przy identyfikatorze zadania.

## Granice

- Obecny model kodowania: istniejąca rola `AGENT_DEEP` z rejestru Ollamy; w próbie LIVE `qwen3:14b`. Inne zadania workera zachowują dotychczasowy szybki model. Brak pobierania modeli lub płatnych wywołań.
- Maksymalnie 3 pliki z `runtime-recovered/api/src`, `runtime-recovered/api/tests`, `frontend/src`; rozszerzenia py/ts/tsx/js/jsx/css. Łącznie 12 KB źródeł, polecenie do 4000 znaków. Ścieżki absolutne, wyjście poza repo i inne typy plików odrzucone.
- Jedno wywołanie modelu na zarejestrowane zadanie; brak automatycznej pętli poprawiania. Po nieudanej propozycji zbadaj błąd zamiast ponawiać bez końca.
- Worker czyta wskazane źródła, zwraca dokładne zamiany i diff, nie zapisuje kodu ani nie wykonuje komend. Kandydat jest ponownie sprawdzany względem źródła po odpowiedzi modelu.
- Wątki czatu nadal są daemon threads: restart API może przerwać zadanie. Nie traktuj tego jako trwałego, odpornego na restart klastra workerów.
- Zmniejszenie zużycia Codexa zależy od skuteczności modelu i kosztu nadzoru. Nie zmierzono procentowej oszczędności. Pierwsze próby szybkiego modelu były niepoprawne; jedna poprawna mała zmiana nie dowodzi samodzielnego programowania.

Dowody pierwszej próby: `reports/local-coding-delegation-20261020/`.

## Kolejka i dowód wykonania — 2026-10-26

`GET /api/ai-workers/tasks` obsługuje `query`, `conversation_id` i `status`; filtry są stosowane przed limitem. Najnowsze grupy są pierwsze, kroki w grupie zachowują `sequence_index`. Kontekst rozmowy pochodzi z zaufanego wywołania czatu, nie argumentu modelu. Powiązanie zadania tła jest przechowywane jako `context.job_id`; publiczne pole `job_token` zachowuje kompatybilność, nie jest hasłem. Historycznie utraconego powiązania nie odtwarzamy przez zgadywanie.

`planned`/hold-only nie dowodzi wykonania. Atomowe przejęcie zadania chroni przed równoczesnym utworzeniem dwóch runów na podstawie starego stanu. `incomplete` oznacza częściowy wynik; nie jest `completed`. Zablokowany lub błędny wynik capability zachowuje status i odpowiedź także w runie. `awaiting_human_review` nadal wymaga odbioru.

Rzeczywiste zadanie publicznych danych #125 przeszło zwykłe ponowienie z zachowaniem pierwszej błędnej próby i wejścia. To dowód wykonawcy danych publicznych, nie autonomicznego kodowania przez LLM. Wątki daemon nadal nie zapewniają trwałego wznowienia po restarcie API. Dowody: `reports/worker-execution-boundaries-20261026/`.
