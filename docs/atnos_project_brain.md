# Project Brain Atnosa — co jest, a czego świadomie nie budujemy

Dokument opisuje **pamięć produkcyjną kanału/projektu** (`project_content_brain`), osobno od
operacyjnego Project Brain w SQLAlchemy (`services/project_brain_service.py`), który synchronizuje
readiness/runtime.

## Co istnieje (fundament)

- Snapshot mózgu per projekt: marka, audience, voice, reguły wizualne/produkcyjne, historia publikacji,
  błędy, flagi capabilities, zakresy feedbacku.
- Decyzja historii przed nowym zleceniem: `new` / `similar_angle` / `needs_rephrase` / `already_used`
  z reason + confidence, zapisana na zamówieniu (`profile.history_decision`) i w tabeli decyzji.
- Plany treści, kampanie (role), serie (`series_key`), picker z zapisanym powodem.
- Status publikacji oddzielony od produkcji; `local_only` → BLOCKED; `ready` ≠ zgoda na publikację.
- Klasyfikacja claimów podpięta pod `script_issues` (bez wymyślania źródeł, bez drugiego silent rewrite).
- Schematy na trendy, komentarze, konkurencję, A/B, wyniki po publikacji, zmęczenie formatem —
  przy braku metryk: `UNKNOWN` / `observe` / `insufficient_data` (bez CTR/retention z kapelusza).

Kod: `runtime-recovered/api/src/atnos/production/project_content_brain/`.
Wejście ścieżki zleceń: `ProductionOrderStore.create`.

## Świadomie NIE zbudowane (tylko flaga + ten opis)

| Capability | Status | Uwaga |
|---|---|---|
| `record_me_mode` | not_built | Brak telepromptera, take'ów i montażu własnego wizerunku. Nie budujemy edytora wideo w tym przebiegu. |
| `multilingual_local_versions` | not_built | Wealth Blueprint pozostaje `language=en`. Brak lokalizacji walut/jednostek/kultury poza istniejącym `example_currency`. |
| `auto_edit_user_footage` | not_built | Brak automatycznego montażu materiału użytkownika. |
| `live_youtube_comment_mining` | schema_only | Klaster komentarzy wymaga prawdziwego importu; bez niego `observe`. |
| `live_trend_metrics` / A/B winners | schema_only | Decyzje bez metryk nie ogłaszają zwycięzcy ani „użyj bo popularne”. |

Istniejące zbieranie trendów (`TrendWatchService`) i konkurencji (`intelligence/competitor.py`)
pozostaje zewnętrznym sygnałem — Project Content Brain zapisuje **decyzje i wiedzę projektową**,
nie zaciąga w tym przebiegu nowych scrape'ów API.
