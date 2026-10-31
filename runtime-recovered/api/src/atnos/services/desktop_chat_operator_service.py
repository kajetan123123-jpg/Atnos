from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import re
import time
import unicodedata

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.providers.registry import ProviderRegistry
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.local_host_control_service import (
    LocalDesktopAppView,
    LocalHostControlError,
    LocalHostControlService,
)
from atnos.services.visual_qa_service import VisualQAService


DesktopChatAction = str


@dataclass(frozen=True, slots=True)
class DesktopChatOperationResult:
    activated: bool
    action: DesktopChatAction | None = None
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class DesktopChatOperatorService:
    _OPEN_VERBS = (
        "otworz",
        "wlacz",
        "uruchom",
        "odpal",
        "pokaz",
        "przelacz na",
        "przełącz na",
        "zainstaluj",
        "zainstaluj mi",
        "launch",
        "start",
        "open",
    )
    # „pokaż” alone often means „show me info”, not „launch app”.
    _OPEN_APP_QUERY_VERBS = (
        "otworz",
        "wlacz",
        "uruchom",
        "odpal",
        "przelacz na",
        "zainstaluj",
        "launch",
        "start",
        "open",
    )
    _NON_APP_QUERY_TOKENS = (
        "umiejetnosc",
        "umiejetnosci",
        "zadanie",
        "zadania",
        "status",
        "dowod",
        "dowody",
        "produkcj",
        "kanal",
        "youtube",
        "notatk",
        "pliki",
        "plik ",
        "katalog",
        "znajdz",
        "wyszukaj",
        "napisz",
        "stworz",
        "wykonaj",
        "zrob",
        "sprawdz",
        "twoich",
        "mozesz",
        "potrafisz",
        "co umiesz",
        "panel sterowania",
        "control panel",
        "ustawienia windows",
    )
    _OBSIDIAN_NAMES = ("obsydian", "obsidian")
    _KIMI_NAMES = ("kimi",)
    _SAVE_NOTE_PATTERNS = (
        "zapisz do obsydiana",
        "zapisz do obsidian",
        "dodaj do obsydiana",
        "dodaj do obsidian",
        "utworz notatke w obsydianie",
        "utworz notatke w obsidianie",
        "stworz notatke w obsydianie",
        "stworz notatke w obsidianie",
    )
    _KIMI_PROMPT_PATTERNS = (
        "zapytaj kimi o",
        "zapytaj kimi:",
        "wyslij do kimi",
        "wyślij do kimi",
        "napisz do kimi",
        "przekaz kimi",
        "uzyj kimi do",
        "uzyj kimi:",
        "skorzystaj z kimi",
        "przepusc przez kimi",
    )

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.integration_config = IntegrationConfigService(session, settings)

    def maybe_handle(self, *, content: str) -> DesktopChatOperationResult:
        normalized = self._normalize(content)
        if not normalized:
            return DesktopChatOperationResult(activated=False)

        if self._wants_obsidian_note(normalized):
            note_body = self._extract_obsidian_note_body(content)
            if not note_body:
                return DesktopChatOperationResult(
                    activated=True,
                    action="obsidian_note",
                    assistant_content=(
                        "Moge zapisac notatke do Obsidiana, ale nie podales tresci.\n\n"
                        "Napisz to w stylu: `zapisz do obsydiana: ...`."
                    ),
                    metadata={"desktop_operator": {"action": "obsidian_note", "missing_body": True}},
                )
            return self._handle_obsidian_note(note_body=note_body, original_content=content)

        if self._wants_kimi_prompt(normalized):
            prompt = self._extract_kimi_prompt(content)
            if not prompt:
                return DesktopChatOperationResult(
                    activated=True,
                    action="kimi_prompt",
                    assistant_content=(
                        "Moge wyslac prompt do Kimi, ale nie podales tresci.\n\n"
                        "Napisz to w stylu: `zapytaj Kimi o ...`."
                    ),
                    metadata={"desktop_operator": {"action": "kimi_prompt", "missing_prompt": True}},
                )
            return self._handle_kimi_prompt(prompt=prompt)

        app_alias = self._extract_open_app_alias(normalized)
        if app_alias is not None:
            return self._handle_open_or_focus_app(app_alias=app_alias)

        app_query = self._extract_open_app_query(normalized, original=content)
        if app_query is not None:
            return self._handle_open_app_by_query(query=app_query)

        return DesktopChatOperationResult(activated=False)

    def _handle_open_or_focus_app(self, *, app_alias: str) -> DesktopChatOperationResult:
        host = LocalHostControlService(self.session, settings=self.settings)
        app = self._resolve_app(host, app_alias)
        if app is None:
            return DesktopChatOperationResult(
                activated=True,
                action="desktop_app_open",
                assistant_content=self._render_error(
                    f"Nie widze teraz aplikacji `{app_alias}` w katalogu desktop apps Atnosa."
                ),
                metadata={"desktop_operator": {"action": "desktop_app_open", "app_alias": app_alias, "status": "not_found"}},
            )
        try:
            command = (
                host.focus_desktop_app(app.app_key)
                if app.window_visible
                else host.open_desktop_app(app.app_key)
            )
        except Exception as exc:
            return DesktopChatOperationResult(
                activated=True,
                action="desktop_app_open",
                assistant_content=self._render_error(str(exc)),
                metadata={
                    "desktop_operator": {
                        "action": "desktop_app_open",
                        "app_key": app.app_key,
                        "label": app.label,
                        "error": str(exc),
                    }
                },
            )
        mode = "sfokusowalem" if app.window_visible else "otworzylem"
        return DesktopChatOperationResult(
            activated=True,
            action="desktop_app_open",
            assistant_content=(
                f"{mode.capitalize()} aplikacje `{app.label}`.\n\n"
                f"Wynik hosta: {command.stdout or 'brak stdout'}"
            ),
            metadata={
                "desktop_operator": {
                    "action": "desktop_app_open",
                    "app_key": app.app_key,
                    "label": app.label,
                    "command_exit_code": command.exit_code,
                }
            },
        )

    def _handle_open_app_by_query(self, *, query: str) -> DesktopChatOperationResult:
        host = LocalHostControlService(self.session, settings=self.settings)
        try:
            result = host.open_or_resolve_desktop_app(query=query, list_only=False)
        except LocalHostControlError as exc:
            return DesktopChatOperationResult(
                activated=True,
                action="desktop_app_open",
                assistant_content=self._render_error(str(exc)),
                metadata={
                    "desktop_operator": {
                        "action": "desktop_app_open",
                        "query": query,
                        "error": str(exc),
                    }
                },
            )
        status = str(result.get("status") or "")
        label = str(result.get("label") or result.get("app_key") or query)
        app_key = str(result.get("app_key") or "") or None
        resolved_alias = result.get("resolved_alias")
        if status == "opened":
            return DesktopChatOperationResult(
                activated=True,
                action="desktop_app_open",
                assistant_content=(
                    f"Otworzyłem aplikację `{label}`"
                    + (f" (alias: {query} → {resolved_alias})." if resolved_alias else ".")
                    + f"\n\nWynik hosta: {result.get('stdout') or 'ok'}"
                ),
                metadata={
                    "desktop_operator": {
                        "action": "desktop_app_open",
                        "query": query,
                        "app_key": app_key,
                        "label": label,
                        "resolved_alias": resolved_alias,
                        "status": status,
                    }
                },
            )
        matches = result.get("matches") if isinstance(result.get("matches"), list) else []
        match_labels = [
            str(item.get("label") or item.get("app_key") or "")
            for item in matches
            if isinstance(item, dict)
        ]
        hint = str(result.get("hint") or "Nie znaleziono lokalnej aplikacji.")
        candidates = ", ".join(item for item in match_labels if item) or "(brak)"
        next_step = (
            "Podaj dokładniejszą nazwę z menu Start, ścieżkę .exe/.lnk "
            "albo Steam AppID — wtedy spróbuję ponownie. "
            "Nie zastępuję uruchomienia programu generowaniem obrazów ani skryptami WWW."
        )
        return DesktopChatOperationResult(
            activated=True,
            action="desktop_app_open",
            assistant_content=(
                f"Status: `{status}` dla `{query}`.\n\n{hint}\n"
                f"Kandydaci: {candidates}\n\n{next_step}"
            ),
            metadata={
                "desktop_operator": {
                    "action": "desktop_app_open",
                    "query": query,
                    "status": status,
                    "resolved_alias": resolved_alias,
                    "matches": matches[:5],
                }
            },
        )

    def _handle_obsidian_note(
        self,
        *,
        note_body: str,
        original_content: str,
    ) -> DesktopChatOperationResult:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists or not config.export_enabled:
            reason = "; ".join(config.warnings) if config.warnings else "Obsidian vault nie jest gotowy."
            return DesktopChatOperationResult(
                activated=True,
                action="obsidian_note",
                assistant_content=self._render_error(reason),
                metadata={"desktop_operator": {"action": "obsidian_note", "error": reason}},
            )
        relative, absolute = self._write_obsidian_note(note_body=note_body, original_content=original_content)
        app_status = self._open_or_focus_best_effort("obsidian")
        return DesktopChatOperationResult(
            activated=True,
            action="obsidian_note",
            assistant_content=(
                "Zapisalem notatke do Obsidiana.\n\n"
                f"Sciezka vaulta: `{relative}`\n"
                f"Plik: `{absolute}`\n"
                f"Stan aplikacji: {app_status}"
            ),
            metadata={
                "desktop_operator": {
                    "action": "obsidian_note",
                    "vault_note_path": relative,
                    "absolute_note_path": str(absolute),
                    "app_status": app_status,
                }
            },
        )

    def _handle_kimi_prompt(self, *, prompt: str) -> DesktopChatOperationResult:
        host = LocalHostControlService(self.session, settings=self.settings)
        app = self._resolve_app(host, "kimi")
        if app is None:
            return DesktopChatOperationResult(
                activated=True,
                action="kimi_prompt",
                assistant_content=self._render_error(
                    "Nie widze teraz aplikacji `Kimi` w katalogu desktop apps Atnosa."
                ),
                metadata={"desktop_operator": {"action": "kimi_prompt", "missing_app": True}},
            )
        recovery_log: list[str] = []
        answer_text = ""
        answer_method = ""
        screenshot_path: str | None = None
        try:
            if app.window_visible:
                host.focus_desktop_app(app.app_key)
                recovery_log.append("focused_existing_window")
            else:
                host.open_desktop_app(app.app_key)
                recovery_log.append("opened_with_visibility_polling")
            baseline_text = self._read_accessibility_text_bounded(host=host, app_key=app.app_key, timeout_s=3.0)
            host.focus_desktop_app_input(app.app_key)
            recovery_log.append("input_focus_ready")
            self._send_prompt_text_with_fallbacks(host=host, prompt=prompt, recovery_log=recovery_log)
            host.press_keys(keys=["ENTER"])
            recovery_log.append("prompt_submitted")
            answer_text, answer_method = self._wait_for_kimi_answer(
                host=host,
                app_key=app.app_key,
                baseline_text=baseline_text,
                prompt=prompt,
                timeout_s=45.0,
            )
            if not answer_text:
                capture = host.capture_foreground_window_screenshot(label="kimi-answer")
                screenshot_path = capture.screenshot_path
                recovery_log.append("accessibility_timeout_screenshot_taken")
                answer_text = self._extract_answer_from_screenshot(
                    screenshot_path=Path(capture.screenshot_path),
                    prompt=prompt,
                )
                answer_method = "screenshot_vision"
            if not answer_text:
                raise LocalHostControlError(
                    "Kimi prompt was submitted, but no answer text was verified through accessibility or screenshot vision."
                )
        except Exception as exc:
            return DesktopChatOperationResult(
                activated=True,
                action="kimi_prompt",
                assistant_content=self._render_error(
                    f"Nie dowiozlem zweryfikowanego odczytu odpowiedzi Kimi. Dokladny powod: {exc}"
                ),
                metadata={
                    "desktop_operator": {
                        "action": "kimi_prompt",
                        "app_key": app.app_key,
                        "error": str(exc),
                        "recovery_log": recovery_log,
                        "screenshot_path": screenshot_path,
                    }
                },
            )
        preview = prompt if len(prompt) <= 220 else f"{prompt[:217]}..."
        return DesktopChatOperationResult(
            activated=True,
            action="kimi_prompt",
            assistant_content=(
                "Kimi zwrocil zweryfikowana odpowiedz przez lokalny operator desktopowy.\n\n"
                f"Prompt: `{preview}`\n"
                f"Metoda odczytu: `{answer_method}`\n\n"
                f"Odpowiedz:\n{answer_text.strip()}"
            ),
            metadata={
                "desktop_operator": {
                    "action": "kimi_prompt",
                    "app_key": app.app_key,
                    "prompt_preview": preview,
                    "prompt_length": len(prompt),
                    "answer_text": answer_text.strip(),
                    "answer_method": answer_method,
                    "screenshot_path": screenshot_path,
                    "recovery_log": recovery_log,
                }
            },
        )

    def _send_prompt_text_with_fallbacks(
        self,
        *,
        host: LocalHostControlService,
        prompt: str,
        recovery_log: list[str],
    ) -> None:
        try:
            host.type_text(text=prompt)
            recovery_log.append("prompt_typed_sendinput")
            return
        except Exception as exc:
            recovery_log.append(f"sendinput_failed:{self._compact_error(exc)}")
        try:
            host.paste_text(text=prompt)
            recovery_log.append("prompt_pasted_clipboard")
            return
        except Exception as exc:
            recovery_log.append(f"clipboard_paste_failed:{self._compact_error(exc)}")
        if self._type_text_via_pyautogui(prompt=prompt):
            recovery_log.append("prompt_typed_pyautogui")
            return
        if self._type_text_via_wscript_sendkeys(prompt=prompt):
            recovery_log.append("prompt_typed_wscript_sendkeys")
            return
        raise LocalHostControlError("All prompt input fallbacks failed.")

    @staticmethod
    def _type_text_via_pyautogui(*, prompt: str) -> bool:
        try:
            import pyautogui

            pyautogui.write(prompt, interval=0.001)
            return True
        except Exception:
            return False

    def _type_text_via_wscript_sendkeys(self, *, prompt: str) -> bool:
        try:
            # WScript SendKeys is intentionally last: it is less robust for special chars than SendInput/clipboard.
            safe = prompt.replace("'", "''")
            command = (
                "$shell=New-Object -ComObject WScript.Shell; "
                f"$shell.SendKeys('{safe}')"
            )
            result = LocalHostControlService(self.session, settings=self.settings).run_powershell(
                command,
                timeout_s=10,
            )
            return result.exit_code == 0
        except Exception:
            return False

    def _read_accessibility_text_bounded(
        self,
        *,
        host: LocalHostControlService,
        app_key: str,
        timeout_s: float,
    ) -> str:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atnos-uia-read")
        future = executor.submit(host.read_desktop_app_accessibility_text, app_key)
        try:
            return str(future.result(timeout=max(0.5, float(timeout_s))) or "")
        except FutureTimeoutError:
            future.cancel()
            return ""
        except Exception:
            return ""
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _wait_for_kimi_answer(
        self,
        *,
        host: LocalHostControlService,
        app_key: str,
        baseline_text: str,
        prompt: str,
        timeout_s: float,
    ) -> tuple[str, str]:
        deadline = time.monotonic() + max(1.0, float(timeout_s))
        last_text = ""
        stable_hits = 0
        while time.monotonic() < deadline:
            time.sleep(1.5)
            current = self._read_accessibility_text_bounded(host=host, app_key=app_key, timeout_s=3.0)
            if not current:
                continue
            candidate = self._extract_new_answer_text(
                baseline_text=baseline_text,
                current_text=current,
                prompt=prompt,
            )
            if not candidate:
                continue
            if candidate == last_text:
                stable_hits += 1
            else:
                last_text = candidate
                stable_hits = 0
            if stable_hits >= 1 or len(candidate) >= 120:
                return candidate, "uia_text_change"
        return "", ""

    def _extract_answer_from_screenshot(self, *, screenshot_path: Path, prompt: str) -> str:
        ocr_text = self._extract_answer_from_screenshot_windows_ocr(
            screenshot_path=screenshot_path,
            prompt=prompt,
        )
        if ocr_text:
            return ocr_text

        direct_vision_text = self._extract_answer_from_screenshot_direct_ollama(
            screenshot_path=screenshot_path,
            prompt=prompt,
        )
        if direct_vision_text:
            return direct_vision_text

        if self.provider_registry is None:
            return ""
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="atnos-kimi-vision")
        try:
            future = executor.submit(
                VisualQAService(
                    self.session,
                    settings=self.settings,
                    provider_registry=self.provider_registry,
                ).run_review,
                goal="Odczytaj faktyczna odpowiedz Kimi z widocznego okna. Zwroc tylko tresc odpowiedzi, bez oceny UI.",
                area="Kimi desktop answer capture",
                extra_context=f"Prompt wyslany do Kimi:\n{prompt}",
                screenshot_path=screenshot_path,
                capture_screenshot=False,
                allow_cloud_review=False,
            )
            result = future.result(timeout=30.0)
        except FutureTimeoutError:
            future.cancel()
            return ""
        except Exception:
            return ""
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        text = (result.review_text or "").strip()
        if text.startswith("VISUAL_REVIEW_FAILED:"):
            return ""
        return text

    def _extract_answer_from_screenshot_windows_ocr(self, *, screenshot_path: Path, prompt: str) -> str:
        prepared = self._prepare_screenshot_for_windows_ocr(screenshot_path)
        if prepared is None:
            return ""
        ocr_text = self._run_windows_ocr(prepared)
        if not ocr_text and prepared != screenshot_path:
            ocr_text = self._run_windows_ocr(screenshot_path)
        return self._extract_answer_from_screenshot_text(
            screenshot_text=ocr_text,
            prompt=prompt,
        )

    @staticmethod
    def _prepare_screenshot_for_windows_ocr(screenshot_path: Path) -> Path | None:
        try:
            from PIL import Image, ImageEnhance, ImageOps
        except Exception:
            return screenshot_path if screenshot_path.exists() else None

        try:
            image = Image.open(screenshot_path).convert("RGB")
        except Exception:
            return None

        width, height = image.size
        if width <= 0 or height <= 0:
            return None

        # Kimi is an Electron app with a dark chat pane. Cropping away chrome/sidebar
        # and normalizing text to black-on-white makes Windows OCR much more reliable.
        crop = image.crop(
            (
                int(width * 0.18),
                int(height * 0.06),
                int(width * 0.99),
                int(height * 0.90),
            )
        )
        crop = crop.resize((min(crop.width * 2, 2600), min(crop.height * 2, 1800)))
        gray = ImageOps.grayscale(crop)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(2.8)
        gray = ImageEnhance.Sharpness(gray).enhance(2.0)
        histogram = gray.histogram()
        if sum(histogram[:128]) > sum(histogram[128:]):
            gray = ImageOps.invert(gray)
        normalized = gray.point(lambda pixel: 255 if pixel > 145 else 0)

        output_path = screenshot_path.with_name(f"{screenshot_path.stem}-ocr.png")
        try:
            normalized.save(output_path, optimize=True)
        except Exception:
            return screenshot_path
        return output_path

    @staticmethod
    def _run_windows_ocr(image_path: Path) -> str:
        if not image_path.exists():
            return ""
        escaped_path = str(image_path.resolve()).replace("'", "''")
        script = f"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
function AwaitOperation($AsyncOperation, $ResultType) {{
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {{ $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 }} |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($AsyncOperation))
    $task.Wait()
    return $task.Result
}}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{ exit 2 }}
$file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped_path}')) ([Windows.Storage.StorageFile])
$stream = AwaitOperation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) {{ Write-Output $line.Text }}
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _extract_answer_from_screenshot_direct_ollama(self, *, screenshot_path: Path, prompt: str) -> str:
        model_name = self._resolve_ollama_vision_model()
        if not model_name or not screenshot_path.exists():
            return ""
        image_path = self._prepare_screenshot_for_vision_model(screenshot_path) or screenshot_path
        try:
            image_payload = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError:
            return ""

        payload: dict[str, object] = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "You are reading a screenshot of the Kimi desktop chat. "
                        "Return only the newest assistant answer text visible in the chat. "
                        "Do not add commentary, labels, markdown, or UI analysis.\n\n"
                        f"Prompt sent to Kimi:\n{prompt}"
                    ),
                    "images": [image_payload],
                }
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 160},
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(f"{self.settings.ollama_base_url.rstrip('/')}/api/chat", json=payload)
            if response.status_code >= 400:
                return ""
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return ""

        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, dict):
            text = str(message.get("content") or "").strip()
        else:
            text = str(data.get("response") or "").strip() if isinstance(data, dict) else ""
        if not text:
            return ""
        return self._extract_answer_from_screenshot_text(
            screenshot_text=text,
            prompt=prompt,
        ) or text[:8000]

    @staticmethod
    def _prepare_screenshot_for_vision_model(screenshot_path: Path) -> Path | None:
        try:
            from PIL import Image
        except Exception:
            return None
        try:
            image = Image.open(screenshot_path).convert("RGB")
        except Exception:
            return None
        width, height = image.size
        if width <= 0 or height <= 0:
            return None
        crop = image.crop(
            (
                int(width * 0.16),
                int(height * 0.05),
                int(width * 0.99),
                int(height * 0.92),
            )
        )
        crop.thumbnail((1024, 768))
        output_path = screenshot_path.with_name(f"{screenshot_path.stem}-vision.png")
        try:
            crop.save(output_path, optimize=True)
        except Exception:
            return None
        return output_path

    def _resolve_ollama_vision_model(self) -> str | None:
        configured = (self.settings.ollama_vision_model or "").strip()
        if configured:
            return configured
        try:
            with httpx.Client(timeout=max(1.0, float(self.settings.ollama_health_timeout_s))) as client:
                response = client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            if response.status_code >= 400:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        models_payload = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models_payload, list):
            return None
        models: list[str] = []
        for item in models_payload:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                models.append(name.strip())
        for token in ("qwen3-vl", "qwen2.5-vl", "llava", "bakllava", "moondream", "vision", "-vl"):
            preferred = next((model for model in models if token in model.casefold()), None)
            if preferred:
                return preferred
        return None

    @classmethod
    def _extract_answer_from_screenshot_text(
        cls,
        *,
        screenshot_text: str,
        prompt: str,
    ) -> str:
        lines = cls._semantic_lines(screenshot_text)
        if not lines:
            return ""
        prompt_norm = cls._normalize(prompt)
        candidates_after_prompt: list[str] = []
        fallback_candidates: list[str] = []
        seen_prompt = False
        for line in lines:
            normalized = cls._normalize(line)
            if cls._is_screenshot_prompt_line(normalized=normalized, prompt_norm=prompt_norm):
                seen_prompt = True
                candidates_after_prompt = []
                continue
            if cls._is_screenshot_ui_noise(normalized):
                continue
            if seen_prompt:
                candidates_after_prompt.append(line.strip())
            fallback_candidates.append(line.strip())

        selected = candidates_after_prompt or fallback_candidates
        if not selected:
            return ""

        deduped: list[str] = []
        seen: set[str] = set()
        for line in selected:
            normalized = cls._normalize(line)
            if normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(line)
        return "\n".join(deduped[-4:]).strip()[:8000]

    @staticmethod
    def _is_screenshot_prompt_line(*, normalized: str, prompt_norm: str) -> bool:
        if not normalized or not prompt_norm:
            return False
        if normalized == prompt_norm:
            return True
        if len(normalized) >= 18 and (normalized in prompt_norm or prompt_norm in normalized):
            return True
        prompt_tokens = {token for token in prompt_norm.split() if len(token) >= 4}
        line_tokens = {token for token in normalized.split() if len(token) >= 4}
        if not prompt_tokens or not line_tokens:
            return False
        overlap = len(prompt_tokens & line_tokens) / max(1, min(len(prompt_tokens), len(line_tokens)))
        return overlap >= 0.62

    @staticmethod
    def _is_screenshot_ui_noise(normalized: str) -> bool:
        if len(normalized) < 3:
            return True
        noise_tokens = (
            "think",
            "throw me a hard one",
            "ready",
            "kimi",
            "new chat",
            "send",
            "regenerate",
            "copy",
        )
        return any(token in normalized for token in noise_tokens)

    @classmethod
    def _extract_new_answer_text(
        cls,
        *,
        baseline_text: str,
        current_text: str,
        prompt: str,
    ) -> str:
        baseline_lines = cls._semantic_lines(baseline_text)
        prompt_lines = cls._semantic_lines(prompt)
        current_lines = cls._semantic_lines(current_text)
        blocked = set(baseline_lines) | set(prompt_lines)
        new_lines = [line for line in current_lines if line not in blocked]
        if new_lines:
            return "\n".join(new_lines[-24:]).strip()[:8000]
        baseline = (baseline_text or "").strip()
        current = (current_text or "").strip()
        if baseline and current.startswith(baseline) and len(current) > len(baseline) + 20:
            return current[len(baseline):].strip()[:8000]
        return ""

    @staticmethod
    def _semantic_lines(value: str) -> list[str]:
        lines = []
        for raw_line in (value or "").splitlines():
            line = " ".join(raw_line.split()).strip()
            if len(line) < 2:
                continue
            lines.append(line)
        return lines

    def _write_obsidian_note(self, *, note_body: str, original_content: str) -> tuple[str, Path]:
        config = self.integration_config.get_obsidian_config()
        assert config.vault_path is not None
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        slug = self._slugify(note_body[:48])
        relative = Path("Atnos") / "Capture" / f"{stamp}-chat-note-{slug}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError as exc:
            raise RuntimeError("Obsidian note path escaped the configured vault.") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        markdown = "\n".join(
            [
                f"# {self._title_for_note(note_body)}",
                "",
                f"- created_at: {datetime.now(tz=UTC).isoformat()}",
                "- source: atnos-chat",
                "",
                "## Note",
                "",
                note_body.strip(),
                "",
                "## Source request",
                "",
                original_content.strip(),
                "",
            ]
        )
        target.write_text(markdown, encoding="utf-8")
        return relative.as_posix(), target

    def _open_or_focus_best_effort(self, app_alias: str) -> str:
        host = LocalHostControlService(self.session, settings=self.settings)
        app = self._resolve_app(host, app_alias)
        if app is None:
            return "app_not_found"
        try:
            if app.window_visible:
                host.focus_desktop_app(app.app_key)
                return "focused"
            host.open_desktop_app(app.app_key)
            return "opened"
        except Exception as exc:
            return f"app_error: {exc}"

    @staticmethod
    def _normalize(content: str) -> str:
        normalized = unicodedata.normalize("NFKD", (content or "").replace('ł','l').replace('Ł','L'))
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
        return " ".join(ascii_only.split())

    def _wants_obsidian_note(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in self._SAVE_NOTE_PATTERNS)

    def _wants_kimi_prompt(self, normalized: str) -> bool:
        return any(pattern in normalized for pattern in self._KIMI_PROMPT_PATTERNS)

    def _launch_command_match(self, normalized: str, verbs: tuple[str,...]):
        # The fast desktop lane accepts explicit commands, not a verb fragment
        # inside an infinitive, quoted question, restart, or a longer narrative.
        choices='|'.join(re.escape(self._normalize(v)) for v in sorted(verbs,key=len,reverse=True))
        return re.match(r'^(?:(?:hej|atnos|prosze(?: cie)?|please|czy mozesz|mozesz)[,:]?\s+)*(?:'+choices+r')(?=\s|:|$)',normalized)

    def _extract_open_app_alias(self, normalized: str) -> str | None:
        command=self._launch_command_match(normalized,self._OPEN_VERBS)
        if command:
            target=normalized[command.end():].strip(' :,')
            target=re.sub(r'^(?:(?:mi|nam|aplikacje|aplikacja|program|app)\s+)+','',target)
            if any(re.match(re.escape(name)+r'(?:\b|$)',target) for name in self._OBSIDIAN_NAMES):
                return "obsidian"
            if any(re.match(re.escape(name)+r'(?:\b|$)',target) for name in self._KIMI_NAMES):
                return "kimi"
        return None

    def _extract_open_app_query(self, normalized: str, *, original: str) -> str | None:
        # Do not treat bare „pokaż …” as app launch — too many false positives.
        if not self._launch_command_match(normalized,self._OPEN_APP_QUERY_VERBS):
            return None
        # Avoid stealing note/Kimi flows.
        if self._wants_obsidian_note(normalized) or self._wants_kimi_prompt(normalized):
            return None
        # Drop URLs / explicit web asks (strone = ASCII fold of „stronę”).
        if self._looks_like_web_request(normalized):
            return None
        # Prefer raw user tokens after the verb.
        lowered = (original or "").casefold()
        # Match both accented Polish and ASCII-folded forms (ł→l etc.).
        ascii_lowered = self._normalize(original)
        verb_spans: tuple[tuple[str, str], ...] = (
            ("otwórz", "otworz"),
            ("otworz", "otworz"),
            ("uruchom", "uruchom"),
            ("włącz", "wlacz"),
            ("wlacz", "wlacz"),
            ("odpal", "odpal"),
            ("zainstaluj mi", "zainstaluj mi"),
            ("zainstaluj", "zainstaluj"),
            ("launch", "launch"),
            ("start", "start"),
            ("open", "open"),
        )
        for surface, ascii_verb in verb_spans:
            match=re.search(r'(?<!\w)'+re.escape(surface)+r'(?!\w)',lowered)
            if match:
                tail = original[match.end():].strip(" :,-")
            else:
                match=re.search(r'(?<!\w)'+re.escape(ascii_verb)+r'(?!\w)',ascii_lowered)
                if not match:
                    continue
                # Folded string may differ in length from original; use folded tail as query.
                tail = ascii_lowered[match.end():].strip(" :,-")
            # Strip filler words.
            cleaned = re.sub(
                r"^(mi|nam|prosze|proszę|aplikacje|aplikację|aplikacje|aplikacja|program|apke|apk[eę]|app|steam)\s+",
                "",
                tail,
                flags=re.IGNORECASE,
            ).strip()
            # Also strip leading fillers repeatedly.
            for _ in range(3):
                cleaned2 = re.sub(
                    r"^(mi|nam|prosze|proszę)\s+",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                ).strip()
                if cleaned2 == cleaned:
                    break
                cleaned = cleaned2
            cleaned = cleaned.strip(" .!?")
            if cleaned and self._looks_like_app_name(cleaned):
                return cleaned
        # Fallback: last token after verb in normalized ascii form.
        for verb in self._OPEN_APP_QUERY_VERBS:
            match=re.search(r'(?<!\w)'+re.escape(verb)+r'(?!\w)',normalized)
            if not match:
                continue
            tail = normalized[match.end():].strip()
            for filler in ("mi", "nam", "prosze", "aplikacje", "aplikacja", "program", "app"):
                if tail.startswith(filler + " "):
                    tail = tail[len(filler) + 1 :].strip()
            if tail and self._looks_like_app_name(tail):
                return tail
        return None

    def _looks_like_app_name(self, candidate: str) -> bool:
        text = " ".join(str(candidate or "").strip().split())
        if not text or len(text) > 64:
            return False
        words = text.split()
        if len(words) > 6:
            return False
        normalized = self._normalize(text)
        if self.is_task_target(text):
            return False
        if any(token in normalized for token in self._NON_APP_QUERY_TOKENS):
            return False
        # Block ambiguous Windows control-panel fragments.
        if LocalHostControlService._is_ambiguous_system_panel_query(normalized):
            return False
        return True

    @staticmethod
    def is_task_target(candidate: str) -> bool:
        normalized=DesktopChatOperatorService._normalize(candidate)
        # An explicit executable/shortcut still belongs to the app resolver.
        if re.search(r'\.(?:exe|lnk)["\x27]?$',normalized):return False
        return bool(re.search(r'\b(?:worker\w*|agent(?:a|ow|y|ami|em)?|petl\w*|rownoleg\w*|kolejk\w*|proces(?:ow|y|ami|u|em)?|serwer\w*)\b',normalized))

    @staticmethod
    def _looks_like_web_request(normalized: str) -> bool:
        web_tokens = (
            "http://",
            "https://",
            "www.",
            "strone",
            "strona",
            "portal",
            "strony",
            "link ",
            " url",
            "znajdz",
            "wyszukaj",
            "nip",
            "kontaktowy",
        )
        if any(token in normalized for token in web_tokens):
            return True
        if "otworz" in normalized and ("strone" in normalized or "strona" in normalized):
            return True
        return False

    def _resolve_app(self, host: LocalHostControlService, app_alias: str) -> LocalDesktopAppView | None:
        normalized_alias = app_alias.casefold().strip()
        for app in host.list_desktop_apps():
            if app.app_key.casefold() == normalized_alias:
                return app
            if normalized_alias == "obsidian" and (
                app.app_key.casefold() == "obsidian" or app.label.casefold() == "obsidian"
            ):
                return app
            if normalized_alias == "kimi" and normalized_alias in app.label.casefold():
                return app
        return None

    def _extract_obsidian_note_body(self, content: str) -> str:
        lowered = content.lower()
        if ":" in content:
            head, tail = content.split(":", 1)
            if any(pattern in head.lower() for pattern in self._SAVE_NOTE_PATTERNS):
                return tail.strip()
        match = re.search(
            r"(?:zapisz|dodaj|utworz|stworz).{0,40}(?:obsydian|obsidian)[^a-zA-Z0-9]*(.+)$",
            lowered,
        )
        if match is not None:
            start = match.start(1)
            return content[start:].strip(" .,-")
        return ""

    def _extract_kimi_prompt(self, content: str) -> str:
        lowered = content.lower()
        normalized = self._normalize(content)
        for pattern in self._KIMI_PROMPT_PATTERNS:
            if pattern in normalized:
                start = normalized.index(pattern) + len(pattern)
                return content[start:].strip(" :,-")
        if ":" in content and "kimi" in lowered:
            return content.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _title_for_note(note_body: str) -> str:
        line = " ".join(note_body.split()).strip()
        if not line:
            return "Chat capture"
        return line[:72]

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
        return slug or "chat-note"

    @staticmethod
    def _render_error(reason: str) -> str:
        detail = " ".join(str(reason).split()).strip() or "Nieznany blad desktop operatora."
        return f"Nie dowiozlem teraz tej akcji desktopowej.\n\nDokladny powod: {detail}"
