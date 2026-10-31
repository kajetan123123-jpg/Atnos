from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Literal

import httpx

from atnos.core.config import Settings


OllamaModelRoleStatus = Literal[
    "ready",
    "fallback_only",
    "missing_main_model",
    "missing_vision_model",
    "unreachable",
]


@dataclass(frozen=True, slots=True)
class OllamaModelRoleSnapshot:
    status: OllamaModelRoleStatus
    ready: bool
    main_text_model: str
    fast_text_model: str
    vision_model: str
    installed_models: tuple[str, ...]
    missing_models: tuple[str, ...]
    active_text_model: str | None
    active_vision_model: str | None
    reason: str
    suggestions: tuple[str, ...]
    checked_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": self.ready,
            "main_text_model": self.main_text_model,
            "fast_text_model": self.fast_text_model,
            "vision_model": self.vision_model,
            "installed_models": list(self.installed_models),
            "missing_models": list(self.missing_models),
            "active_text_model": self.active_text_model,
            "active_vision_model": self.active_vision_model,
            "reason": self.reason,
            "suggestions": list(self.suggestions),
            "checked_at": self.checked_at.isoformat(),
        }


class OllamaModelRolesService:
    DEFAULT_MAIN_TEXT_MODEL = "qwen3:14b"
    DEFAULT_FAST_TEXT_MODEL = "qwen3.5:9b"
    DEFAULT_VISION_MODEL = "qwen3-vl:8b-instruct"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def main_text_model(self) -> str:
        from atnos.llm.role_registry import ModelRole, OllamaRoleRegistry

        return OllamaRoleRegistry(self.settings).resolve(ModelRole.AGENT_DEEP).model

    @property
    def fast_text_model(self) -> str:
        from atnos.llm.role_registry import ModelRole, OllamaRoleRegistry

        return OllamaRoleRegistry(self.settings).resolve(ModelRole.AGENT_QUICK).model

    @property
    def vision_model(self) -> str:
        from atnos.llm.role_registry import ModelRole, OllamaRoleRegistry

        return OllamaRoleRegistry(self.settings).resolve(ModelRole.SCREEN_VISION).model

    def probe(self, *, installed_models: tuple[str, ...] | None = None) -> OllamaModelRoleSnapshot:
        if installed_models is None:
            try:
                installed_models = self.fetch_installed_models()
            except httpx.HTTPError as exc:
                return OllamaModelRoleSnapshot(
                    status="unreachable",
                    ready=False,
                    main_text_model=self.main_text_model,
                    fast_text_model=self.fast_text_model,
                    vision_model=self.vision_model,
                    installed_models=(),
                    missing_models=(self.main_text_model, self.vision_model),
                    active_text_model=None,
                    active_vision_model=None,
                    reason=f"Ollama endpoint is unreachable: {exc.__class__.__name__}.",
                    suggestions=("Uruchom Ollama i sprawdz OLLAMA_BASE_URL.",),
                    checked_at=self._now(),
                )

        installed = tuple(sorted({item.strip() for item in installed_models if item.strip()}))
        installed_set = set(installed)
        missing: list[str] = []
        if self.main_text_model not in installed_set:
            missing.append(self.main_text_model)
        if self.vision_model not in installed_set:
            missing.append(self.vision_model)

        if self.vision_model not in installed_set:
            return OllamaModelRoleSnapshot(
                status="missing_vision_model",
                ready=False,
                main_text_model=self.main_text_model,
                fast_text_model=self.fast_text_model,
                vision_model=self.vision_model,
                installed_models=installed,
                missing_models=tuple(missing),
                active_text_model=self._active_text_model(installed_set),
                active_vision_model=None,
                reason=f"Missing required local vision model '{self.vision_model}'.",
                suggestions=(f"ollama pull {self.vision_model}",),
                checked_at=self._now(),
            )

        if self.main_text_model in installed_set:
            return OllamaModelRoleSnapshot(
                status="ready",
                ready=True,
                main_text_model=self.main_text_model,
                fast_text_model=self.fast_text_model,
                vision_model=self.vision_model,
                installed_models=installed,
                missing_models=(),
                active_text_model=self.main_text_model,
                active_vision_model=self.vision_model,
                reason="Main text and vision Ollama roles are installed.",
                suggestions=(),
                checked_at=self._now(),
            )

        if self.fast_text_model in installed_set:
            return OllamaModelRoleSnapshot(
                status="fallback_only",
                ready=False,
                main_text_model=self.main_text_model,
                fast_text_model=self.fast_text_model,
                vision_model=self.vision_model,
                installed_models=installed,
                missing_models=tuple(missing),
                active_text_model=self.fast_text_model,
                active_vision_model=self.vision_model,
                reason=(
                    f"Main text model '{self.main_text_model}' is missing; "
                    f"Atnos can use fast fallback '{self.fast_text_model}'."
                ),
                suggestions=(f"ollama pull {self.main_text_model}",),
                checked_at=self._now(),
            )

        return OllamaModelRoleSnapshot(
            status="missing_main_model",
            ready=False,
            main_text_model=self.main_text_model,
            fast_text_model=self.fast_text_model,
            vision_model=self.vision_model,
            installed_models=installed,
            missing_models=tuple(missing),
            active_text_model=None,
            active_vision_model=self.vision_model,
            reason=f"Missing required local text model '{self.main_text_model}' and fallback '{self.fast_text_model}'.",
            suggestions=(
                f"ollama pull {self.main_text_model}",
                f"ollama pull {self.fast_text_model}",
            ),
            checked_at=self._now(),
        )

    def fetch_installed_models(self) -> tuple[str, ...]:
        base_url = self.settings.ollama_base_url.rstrip("/")
        with httpx.Client(timeout=self.settings.ollama_health_timeout_s) as client:
            response = client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
        return self.extract_model_names(payload)

    def pull_model(self, model_name: str, *, timeout_s: int = 900) -> dict[str, object]:
        normalized = model_name.strip()
        if not normalized:
            return {"model": normalized, "status": "invalid_model_name", "ok": False}
        base_url = self.settings.ollama_base_url.rstrip("/")
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.post(
                    f"{base_url}/api/pull",
                    json={"name": normalized, "stream": False},
                )
                response.raise_for_status()
                payload = response.json() if response.content else {}
        except httpx.HTTPError as exc:
            return {
                "model": normalized,
                "status": "error",
                "ok": False,
                "message": f"Ollama pull failed: {exc.__class__.__name__}",
            }
        return {
            "model": normalized,
            "status": str(payload.get("status") or "pulled") if isinstance(payload, dict) else "pulled",
            "ok": True,
            "message": str(payload.get("digest") or "") if isinstance(payload, dict) else "",
        }

    def pull_missing_models(self, snapshot: OllamaModelRoleSnapshot | None = None) -> dict[str, object]:
        snapshot = snapshot or self.probe()
        missing = tuple(dict.fromkeys(model for model in snapshot.missing_models if model.strip()))
        if snapshot.status == "unreachable":
            return {
                "requested": True,
                "status": "unreachable",
                "results": {},
                "message": "Ollama is unreachable; cannot pull missing models.",
            }
        results = {model: self.pull_model(model) for model in missing}
        ok = all(bool(result.get("ok")) for result in results.values()) if results else True
        return {
            "requested": True,
            "status": "ready" if ok else "partial",
            "results": results,
        }

    def write_status(self, snapshot: OllamaModelRoleSnapshot, path: Path | None = None) -> Path:
        target = (path or self.settings.llm_models_status_file).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def _active_text_model(self, installed_set: set[str]) -> str | None:
        if self.main_text_model in installed_set:
            return self.main_text_model
        if self.fast_text_model in installed_set:
            return self.fast_text_model
        return None

    @staticmethod
    def extract_model_names(payload: object) -> tuple[str, ...]:
        if not isinstance(payload, dict):
            return ()
        models = payload.get("models")
        if not isinstance(models, list):
            return ()
        names: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
        return tuple(sorted(set(names)))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
