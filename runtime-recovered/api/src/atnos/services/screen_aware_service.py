"""Screenshot → lokalne zrozumienie (Ollama vision) → wynik dla tool loop."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.services.screen_capture_service import ScreenCaptureError, ScreenCaptureService


class ScreenAwareService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.capture = ScreenCaptureService(settings)

    def capture_and_understand(
        self,
        *,
        label: str = "chat-screen",
        focus: str | None = None,
    ) -> dict[str, Any]:
        artifact = self.capture.capture_screenshot(label=label)
        summary = self._describe_screenshot(
            path=Path(artifact.path),
            focus=focus,
        )
        return {
            "status": "ok",
            "artifact": artifact.to_dict(),
            "summary": summary.get("text") or "",
            "vision_model": summary.get("model"),
            "vision_status": summary.get("status"),
            "vision_error": summary.get("error"),
            "next_action": (
                "Na podstawie summary zdecyduj o kolejnym toolu "
                "(fs_*/browser_*/shell/desktop_app_open) albo odpowiedz użytkownikowi."
            ),
        }

    def _describe_screenshot(self, *, path: Path, focus: str | None) -> dict[str, Any]:
        model = self._resolve_vision_model()
        if not model:
            return {
                "status": "no_vision_model",
                "text": (
                    f"Zrzut zapisany: {path}. Brak lokalnego modelu vision w Ollama — "
                    "opisz ekran na podstawie ścieżki albo poproś użytkownika o kontekst."
                ),
                "model": None,
                "error": "ollama_vision_model_missing",
            }
        try:
            image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError as exc:
            return {
                "status": "read_error",
                "text": f"Nie udało się odczytać zrzutu: {exc}",
                "model": model,
                "error": str(exc),
            }
        prompt = (
            "You are Atnos looking at the user's live desktop screenshot. "
            "Describe what is on screen in Polish: active app/window, key UI text, "
            "and the most useful next action. Be concrete and short (max 12 lines)."
        )
        if focus:
            prompt += f"\n\nUser focus / request:\n{focus.strip()}"
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": 400},
        }
        try:
            with httpx.Client(timeout=45.0) as client:
                response = client.post(
                    f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=payload,
                )
            if response.status_code >= 400:
                return {
                    "status": "vision_http_error",
                    "text": f"Zrzut: {path}. Vision HTTP {response.status_code}.",
                    "model": model,
                    "error": response.text[:500],
                }
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "status": "vision_request_failed",
                "text": f"Zrzut: {path}. Vision niedostępne ({exc}).",
                "model": model,
                "error": str(exc),
            }
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, dict):
            text = str(message.get("content") or "").strip()
        else:
            text = str(data.get("response") or "").strip() if isinstance(data, dict) else ""
        if not text:
            text = f"Zrzut zapisany: {path}. Model vision nie zwrócił opisu."
        return {"status": "ok", "text": text[:8000], "model": model, "error": None}

    def _resolve_vision_model(self) -> str | None:
        configured = str(getattr(self.settings, "ollama_vision_model", "") or "").strip()
        if configured:
            return configured
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            if response.status_code >= 400:
                return None
            models = response.json().get("models") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        names = [str(item.get("name") or "") for item in models if isinstance(item, dict)]
        for token in ("qwen3-vl", "qwen2.5-vl", "llava", "bakllava", "moondream", "vision", "-vl"):
            for name in names:
                if token in name.casefold():
                    return name
        return None


__all__ = ["ScreenAwareService", "ScreenCaptureError"]
