# Atnos

Atnos jest lokalną aplikacją do rozmowy z LLM, pracy z narzędziami komputera oraz koordynowania produkcji krótkich filmów. Interfejs jest po polsku; kanały docelowe są anglojęzyczne: Wealth Blueprint (edukacja finansowa), spokojne historie dla dzieci 4–7 lat i fikcyjne historie grozy.

**Stan na 2026-10-31: projekt rozwijany, częściowo zweryfikowany. Nie jest gotowym, samowystarczalnym pakietem produkcyjnym.** Działające API nie dowodzi działania modeli, publikacji ani jakości filmów. Szczegóły i zakres dowodów: [STATUS](docs/STATUS.md).

## Struktura

| Katalog | Rola |
| --- | --- |
| `frontend/` | React, TypeScript, Vite: czat, Studio, ustawienia i postęp zleceń |
| `runtime-recovered/api/` | FastAPI, konfiguracja, pamięć, capability, integracje, kolejka i produkcja |
| `runtime-recovered/remotion-renderer/` | Kompozycje WB, animacje, napisy i renderowanie |
| `desktop-shell/` | Host Windows/WebView2; nie zawiera binariów ani SDK |
| `scripts/` i launchery w root | Zarządzanie procesami, sprawdzanie zależności i wdrożenie frontendu |
| `config/` | Kontrakty i konfiguracje źródłowe; nie dane uwierzytelniające |
| `.agents/skills/atnos-*` | Instrukcje pracy nad repozytorium; nie automatycznie zainstalowane narzędzia aplikacji |

[Architektura](docs/ARCHITECTURE.md) · [Status](docs/STATUS.md) · [Plan dalszych prac](docs/ROADMAP.md)

## Wymagania i granice pakietu

- Windows i katalog roboczy `E:\Atnos\App`. Obecne launchery oraz część ścieżek są związane z tym układem; migracja Linux nie jest potwierdzona.
- Python 3.11+ i Node.js 20+ z npm. Manifesty zależności są w podkatalogach API, frontendu i renderera. Konkretna zgodność świeżego środowiska wymaga testu instalacji.
- Dla filmu: FFmpeg/ffprobe, zgodna przeglądarka, zależności Remotion, fonty, wybrany lokalny TTS oraz zatwierdzone zasoby kanału. Generowanie ilustracji dodatkowo wymaga ComfyUI, właściwego workflow i modeli.
- Modele LLM, TTS, obrazy, fonty użytkownika, bazy, Obsidian, OAuth, Vault, profile przeglądarki, raporty operacyjne i gotowe filmy **nie są dołączone**. Licencje pobieranych modeli/fontów/SDK należy sprawdzić osobno.
- OpenAI, Anthropic, Gemini, ElevenLabs i inne adaptery zewnętrzne są opcjonalne. Obecność adaptera nie oznacza dostępu, salda ani zgody na płatne wywołania.

## Przygotowanie nowego środowiska

Poniższe polecenia są instrukcją przygotowania, a nie deklaracją wykonanego testu czystej instalacji. Nie używaj ich do restartowania działającej instalacji. Instalatory narzędzi i wszystkie cache kieruj na E:.

```powershell
Set-Location E:\Atnos\App
$env:TEMP = 'E:\Atnos\Data\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'E:\Atnos\Data\tool-caches\pip'
$env:npm_config_cache = 'E:\Atnos\Data\tool-caches\npm'
$env:PLAYWRIGHT_BROWSERS_PATH = 'E:\Atnos\Data\tool-caches\playwright'
$env:HF_HOME = 'E:\Atnos\Data\tool-caches\huggingface'
$env:TORCH_HOME = 'E:\Atnos\Data\tool-caches\torch'
$env:PYTHONPYCACHEPREFIX = 'E:\Atnos\Data\python-cache'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
python -m venv runtime-recovered\api\.venv
.\runtime-recovered\api\.venv\Scripts\python.exe -m pip install -e './runtime-recovered/api[dev]'
Push-Location frontend
npm ci
npm run build
Pop-Location
Push-Location runtime-recovered\remotion-renderer
npm ci
Pop-Location
```

Utwórz z szablonu prywatny plik konfiguracji poza repozytorium. Publiczny snapshot używa nieczynnych adresów przykładowych zamiast prywatnych kont i topologii sieci. Nie wpisuj rzeczywistych sekretów w szablon.

```powershell
$configRoot = 'E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\config'
New-Item -ItemType Directory -Force $configRoot | Out-Null
# Wykonaj tylko gdy plik jeszcze nie istnieje; nie zastępuj konfiguracji działającej instalacji.
if (-not (Test-Path "$configRoot\.env")) {
    Copy-Item runtime-recovered\api\.env.example "$configRoot\.env"
}
$env:ATNOS_ENV_FILE = "$configRoot\.env"
```

Przed startem ustaw rzeczywiste ścieżki do narzędzi, endpoint i nazwy dostępnych modeli. Zachowaj wyłączoną automatykę tła i wysyłkę oraz puste klucze, dopóki nie skonfigurujesz integracji. Nie kopiuj cudzych katalogów Vault, kluczy szyfrujących lub tokenów. Nie wystawiaj API ani desktop operatora do Internetu.

Dla nowej bazy wykonaj migracje z katalogu API przy ustawionym `ATNOS_ENV_FILE`:

```powershell
Push-Location runtime-recovered\api
.\.venv\Scripts\python.exe -m alembic upgrade head
Pop-Location
```

Uruchamianie istniejącej, przygotowanej instalacji: `powershell -File .\Launch-Atnos.ps1`. Flaga `-ServicesOnly` obsługuje usługi aplikacji; `-ModelsOnly` jest odrębną operacją. Launcher wymaga dodatkowych narzędzi/SDK na E:, których repozytorium nie instaluje. Alternatywny start deweloperski API: z katalogu API uruchom `.\.venv\Scripts\python.exe -m uvicorn atnos.api.main:create_app --factory --host 127.0.0.1 --port 8000`; frontend: `npm run dev` z `frontend/`. Nie uruchamiaj drugiego API na zajętym porcie.

## Testy i bezpieczeństwo

Testy muszą korzystać z izolowanych baz i plików na E:. Nie odpalaj testów integracyjnych z prawdziwymi kontami w ciemno. Polecenia i wyniki ograniczonej weryfikacji są w [STATUS](docs/STATUS.md).

`.gitignore` ogranicza przypadkowe dodawanie danych, ale nie usuwa sekretów już śledzonych przez Git. Przed każdym publicznym pushem sprawdź także commitowane pliki i ich historię. Gotowy MP4 nie oznacza zgody na publikację; `local_only` ma pozostać lokalne.

Publiczny snapshot jest oparty na historii tego repozytorium zdalnego. Prywatna historia rozwoju, raporty i konfiguracja instalacji nie zostały do niej scalone. Szczegóły anonimizacji i ograniczeń są w [STATUS](docs/STATUS.md). Nie zadeklarowano licencji dla całego projektu; publiczna widoczność sama nie nadaje licencji do ponownego użycia.
