# Architektura Atnosa

Aktualizacja: 2026-10-31. Ten opis wynika z analizy kodu. Dowody testów i odczytów runtime są osobno w [STATUS](STATUS.md).

## Warstwy

1. **Interfejs:** React/TypeScript/Vite (`frontend/src`). Czat, Centrum, Studio i ustawienia komunikują się z lokalnym API. `desktop-shell/AtnosShell.cs` osadza zbudowany frontend w Windows/WebView2. Kod hosta, pliki `desktop-web` i działający proces to odrębne rzeczy.
2. **API i lifecycle:** `runtime-recovered/api/src/atnos/api/main.py` składa FastAPI i usługi tła. Health live/ready raportuje proces i fazę inicjalizacji; nie sprawdza jakości filmu. Root launchery oraz `scripts/runtime_process_safety.py` kontrolują tożsamość procesów, aktywną pracę i bezpieczne zatrzymanie. Zmiana pliku Python nie aktualizuje załadowanego procesu.
3. **Czat i capability:** warstwa `services/`, routery API oraz rejestr capability dobierają lokalny model i narzędzia. Internet, aplikacje desktopowe, pliki i zadania kodowania mają różne kontrakty, uprawnienia i dowody. Paczka instrukcji skilla nie zastępuje wykonania narzędzia.
4. **Pamięć:** operacyjny Project Brain (`services/project_brain_service.py`, SQLAlchemy) przechowuje stan/readiness; `production/project_content_brain/` przechowuje wiedzę i decyzje kanału. Pamięć rozmów, wyszukiwanie wiedzy oraz integracja z Obsidianem dostarczają kontekst. Zapis notatki nie oznacza treningu wag modelu ani pełnego odczytu całego vaultu przy każdym pytaniu.
5. **Produkcja:** `production/order_store.py` i dispatcher rejestrują zlecenia, plany, rejestrowane korekty, lease i checkpointy. Etapy w `production/steps/` przechodzą przez scenariusz i dowody twierdzeń, storyboard, zasoby, głos, timing napisów, render/mux, QC/QA oraz pakiet. Nie należy ręcznie zmieniać canonical lub statusu bazy, aby ominąć bramkę.
6. **Media:** lokalne modele/API dostawców są adapterami; ComfyUI obsługuje workflow ilustracji, TTS głos, FFmpeg/ffprobe analizę/mux, a `runtime-recovered/remotion-renderer` typowane kompozycje i animację zależną od klatki. Współdzielone zasoby GPU wymagają koordynacji.
7. **Publikacja:** `production/publication_coordinator.py` ma trwałą kolejkę, lease, kontrolę pakietu, deduplikację i politykę. Wynik niepewnego uploadu wymaga uzgodnienia, a nie ślepego retry. Globalna polityka nie jest równoznaczna z zatwierdzeniem konkretnej rewizji filmu przez człowieka. Pozostają także starsze ścieżki uploadu; audyt jednej funkcji nie obejmuje wszystkich.
8. **Dane i sekrety:** SQLite/SQLAlchemy, pliki stanu, Obsidian i istniejący Vault pozostają poza publicznym źródłem. OAuth i profile przeglądarki są prywatne. Sekretów nie przenosi się przez prompt, Git ani raport.

## Przepływ zlecenia

```text
Studio/czat -> API -> rejestr zlecenia i plan
  -> preflight -> scenariusz/claims/review
  -> storyboard/asset plan -> ilustracje i ocena
  -> głos -> wyrównanie/napisy -> Remotion -> mux
  -> video QC/media QA -> pakiet do review
  -> osobny, autoryzowany proces publikacji
```

Wznowienie wymaga zgodnych wejściowych hashy, aktualnej wersji planu i właściciela lease. Odrzucony obraz nie może być substytutem zaakceptowanego assetu. Przerwane lub niejednoznaczne wywołanie zewnętrzne musi być rozliczone przed powtórzeniem. Kontrola automatyczna nie zastępuje obejrzenia i odsłuchu.

## Granice wdrożenia

Źródła aplikacji nie są pełnym obrazem uruchomionej instalacji. Modele, zatwierdzony katalog WB, fonty, próbki głosu, workflow zależne od modeli i stan OAuth trzeba dostarczyć oddzielnie. Historyczny prefiks `runtime-recovered/` jest obecnie faktycznym katalogiem backendu i renderera; dawne instrukcje `apps/api` są nieaktualne. Obecne launchery wymagają Windows i układu E:. Przenośność na Linux pozostaje do potwierdzenia.
