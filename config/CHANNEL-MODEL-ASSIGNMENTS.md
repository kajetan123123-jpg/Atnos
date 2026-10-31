# Atnos — przypisanie modeli do projektów kanałów

Stan sprawdzony 16 września 2026. Wszystkie ścieżki są na E:. Ollama ma pobrane i widoczne: `qwen3:14b` do scenariuszy i redakcji, `qwen3-vl:8b-instruct` do kontroli obrazu, `bge-m3:latest` do wyszukiwania wiedzy oraz `qwen3:8b` jako szybszy fallback. ComfyUI ma: Flux 2 Klein Base 4B + Qwen text encoder + Flux VAE, Z-Image Turbo, SDXL Turbo i LTX Video.

## Projekty ComfyUI

- `E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Projects\wealth_blueprint` — Flux 2 Klein, paleta granat/krem/błękit/turkus/pomarańcz/oszczędne złoto, ilustracja editorial blueprint, diagramy prowadzą akcję, Alex tylko z funkcją narracyjną, ElevenLabs `iP95p4xoKVk53GoZ742B`, spokojna precyzja.
- `E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Projects\children_stories` — SDXL Turbo, ciepła spokojna ilustracja storybook 2D/2.5D, stałe postacie i lokacje, Kokoro `af_heart`, język dla wieku 4–7 lat.
- `E:\Atnos\Tools\Comfy-Desktop\ComfyUI-Projects\horror_stories` — Flux 2 Klein, chiaroscuro i kontrolowane napięcie, spójne lokacje oraz mocne zakończenie bez gore, Kokoro `am_fenrir`, niska intymna barwa i pauzy.

## Twarde reguły Atnosa

Każde zlecenie musi wybrać dokładnie jeden projekt. Atnos zapisuje nazwę projektu, hash workflow, hash użytych modeli, głos, wersję kontraktu i hashe assetów w manifeście. Brak modelu, niewłaściwy głos, mieszanie assetów między kanałami, zmiana palety lub naruszenie stylu blokuje render przed generacją. Użycie fallbacku jest logowane i nie może zmienić charakteru kanału.

„Spójność” oznacza powtarzalność procesu i jakości, nie kopiowanie cudzych filmów: ten sam język wizualny, głos, tempo, budowa zdań, safe area, zasady typografii i QA dla danego kanału. Alex jest gospodarzem Wealth Blueprint, ale nie jest obowiązkową dekoracją każdego kadru.

Konfiguracja źródłowa: `E:\Atnos\App\config\channel_comfy_projects.json`. Pliki `PROJECT.md` w każdym katalogu ComfyUI są instrukcją dla workflowów i nie zastępują walidacji Atnosa.

Od 2026-09-16 manifest jest wiążący dla runtime’u: przy utworzeniu zlecenia Atnos zamraża `project_dir`, wszystkie wymagane pliki modeli, przypisany głos oraz hashe stylu, workflowu i głosu. `begin()` sprawdza je ponownie przed każdym etapem, a `complete()` odrzuca payloady deklarujące inny kanał lub hash. Zmiana kontraktu po utworzeniu zlecenia zatrzymuje render i zapisuje kategorię `channel_contract`.

Referencje wizualne są przechowywane na E: w `E:\Atnos\App\config\channel_references\20260916`. Arkusze `image-1.png` i `image-2.png` definiują Alexa jako prowadzącego Wealth Blueprint; `image-3.png`–`image-6.png` definiują obsadę bajek (Niko, Pif, Tika, Mira). Kontrakt nie pozwala mieszać tych arkuszy między kanałami.
