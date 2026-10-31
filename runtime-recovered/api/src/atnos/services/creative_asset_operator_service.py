from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import textwrap
import unicodedata

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from atnos.core.config import Settings
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService
from atnos.services.runtime_settings_service import RuntimeControlSnapshot


GraphicType = str
GraphicTool = str


@dataclass(frozen=True, slots=True)
class CreativeAssetRequest:
    raw_content: str
    brief: str
    graphic_type: GraphicType
    needs_text_overlay: bool
    prefers_generative_art: bool
    prefers_photorealism: bool
    prefers_external_image_api: bool
    prefers_browser_image_tool: bool
    tool_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CreativeAssetOperationResult:
    activated: bool
    assistant_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    selected_provider_key: str | None = None
    selected_model_key: str | None = None
    routing_reason: str | None = None
    artifact: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ImageApiCandidateResult:
    """One unapproved image, with evidence of the single provider request."""

    descriptor: ManagedArtifactDescriptor
    model_name: str
    settings: dict[str, object]
    usage: dict[str, object]
    request_id: str | None
    prompt: str
    revised_prompt: str | None
    reference_paths: tuple[str, ...]
    endpoint: str


class CreativeAssetOperatorService:
    IDEOGRAM_BROWSER_LANE = "ideogram-browser"
    OPENAI_IMAGE_API_LANE = "openai-image-api"
    CANVAS_COMPOSER_LANE = "canvas-composer-local"
    COMFYUI_LANE = "comfyui-local"
    BILLING_BLOCK_TTL = timedelta(minutes=30)
    BROWSER_BLOCK_TTL = timedelta(minutes=20)
    ACTION_HINTS = (
        "zrob",
        "stworz",
        "stwoz",
        "wygeneruj",
        "przygotuj",
        "zaprojektuj",
        "narysuj",
        "wyrenderuj",
        "make",
        "create",
        "generate",
        "design",
    )
    DESIRE_PATTERNS = (
        "chce grafike",
        "chce obraz",
        "potrzebuje grafiki",
        "potrzebuje obrazu",
        "poprosze grafike",
        "poprosze obraz",
    )
    GRAPHIC_HINTS = (
        "grafi",
        "grafik",
        "obraz",
        "ilustrac",
        "miniatur",
        "thumbnail",
        "plakat",
        "banner",
        "cover",
        "okladk",
        "poster",
        "wizual",
        "hero image",
        "social post",
        "instagram post",
        "youtube thumb",
        "logo",
    )
    TEXT_HEAVY_HINTS = (
        "miniatur",
        "thumbnail",
        "plakat",
        "banner",
        "cover",
        "okladk",
        "youtube",
        "z napisem",
        "z tekstem",
        "haslo",
        "headline",
        "tytul",
        "logo",
    )
    GENERATIVE_ART_HINTS = (
        "fotorealist",
        "realistic",
        "realistycz",
        "portrait",
        "portret",
        "illustration",
        "ilustracja",
        "concept art",
        "concept-art",
        "cinematic",
        "malar",
        "painting",
        "3d render",
        "render 3d",
    )
    PHOTOREALISTIC_HINTS = (
        "fotorealist",
        "realistic",
        "realistycz",
        "photo",
        "fotografia",
        "studyjny",
        "studio portrait",
        "product shot",
        "commercial photo",
    )
    EXTERNAL_IMAGE_HINTS = (
        "openai",
        "chatgpt",
        "gpt image",
        "gpt-image",
        "dall-e",
        "dalle",
    )
    BROWSER_IMAGE_HINTS = (
        "ideogram",
        "przez przegladarke",
        "przez browser",
        "browser image",
        "web ui",
        "w przegladarce",
    )
    TOOL_HINTS = {
        "elevenlabs": "ElevenLabs jest torem glosu i TTS, nie generatorem statycznych grafik.",
        "eleven labs": "ElevenLabs jest torem glosu i TTS, nie generatorem statycznych grafik.",
        "artlist": "Artlist jest lepszy do stock/media niz do generowania statycznego obrazu.",
        "ideogram": "Ideogram moze byc uzyty przez browser lane, jesli istnieje gotowa sesja logowania.",
    }
    OPENAI_IMAGE_MODEL_CANDIDATES = ("gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini")
    LEADING_PHRASE_RE = re.compile(
        r"^\s*(atnos[\s,:-]+)?"
        r"(zrob|stworz|wygeneruj|przygotuj|zaprojektuj|narysuj|wyrenderuj|make|create|generate|design)\s+",
        flags=re.IGNORECASE,
    )
    SIZE_BY_TYPE: dict[GraphicType, tuple[int, int]] = {
        "thumbnail": (1280, 720),
        "banner": (1440, 810),
        "cover": (1200, 1600),
        "poster": (1200, 1600),
        "generic_image": (1024, 1024),
    }

    def __init__(self, settings: Settings, *, account_credentials: AccountCredentialsService | None = None) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.comfyui = ComfyUiImageGenerationService(settings)
        self.account_credentials = account_credentials
        self.font_regular_path = self._find_font("Vera.ttf")
        self.font_bold_path = self._find_font("VeraBd.ttf")

    def maybe_handle(
        self,
        *,
        content: str,
        conversation_id: int | None,
        allow_local_file_write: bool,
        allow_external_fallback: bool = False,
        runtime_controls: RuntimeControlSnapshot | None = None,
        quality_profile: str = "standard",
    ) -> CreativeAssetOperationResult:
        request = self._detect_request(content)
        if request is None:
            return CreativeAssetOperationResult(activated=False)
        quality_profile = self._normalize_quality_profile(quality_profile)

        if not allow_local_file_write:
            blocker = "Local file write jest wylaczony, wiec nie moge zapisac wygenerowanej grafiki."
            return self._build_blocked_result(request=request, blocker=blocker, next_step="Wlacz local file write.")

        selected_tool, selection_reason = self._select_tool(
            request,
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        warnings: list[str] = list(request.tool_notes)
        attempted_tools: set[GraphicTool] = set()
        while True:
            attempted_tools.add(selected_tool)
            try:
                descriptor = self._execute_with_selected_tool(
                    request=request,
                    selected_tool=selected_tool,
                    conversation_id=conversation_id,
                    warnings=warnings,
                    allow_external_fallback=allow_external_fallback,
                    runtime_controls=runtime_controls,
                    quality_profile=quality_profile,
                )
                break
            except RuntimeError as exc:
                fallback = self._fallback_after_tool_failure(
                    request=request,
                    selected_tool=selected_tool,
                    failure_reason=str(exc),
                    allow_external_fallback=allow_external_fallback,
                    runtime_controls=runtime_controls,
                )
                if fallback is None:
                    return self._build_blocked_result(
                        request=request,
                        blocker=str(exc),
                        next_step="Skonfiguruj obrazowy lane lub uprosc brief do lokalnej kompozycji posterowej.",
                        selected_tool=selected_tool,
                        selection_reason=selection_reason,
                        warnings=warnings,
                    )
                fallback_tool, fallback_reason = fallback
                if fallback_tool in attempted_tools:
                    return self._build_blocked_result(
                        request=request,
                        blocker=(
                            f"Fallback grafiki zapetlil sie po torze `{fallback_tool}`. "
                            f"Ostatni blad: {exc}"
                        ),
                        next_step="Skonfiguruj obrazowy lane lub uprosc brief do lokalnej kompozycji posterowej.",
                        selected_tool=selected_tool,
                        selection_reason=selection_reason,
                        warnings=warnings,
                    )
                warnings.append(f"Pierwotny tor `{selected_tool}` nie dowiozl zadania: {exc}")
                warnings.append(fallback_reason)
                selected_tool = fallback_tool
                selection_reason = fallback_reason

        artifact = self._serialize_artifact(descriptor)
        content_lines = [
            f"Wybralem tor grafiki: `{selected_tool}`.",
            f"Powod: {selection_reason}",
        ]
        if warnings:
            content_lines.append("Uwagi do wyboru narzedzia: " + " ".join(warnings))
        content_lines.append(
            f"Wygenerowalem `{artifact['filename']}` i dolaczylem gotowy PNG jako zalacznik."
        )
        metadata = {
            "creative_asset_operator": {
                "graphic_type": request.graphic_type,
                "needs_text_overlay": request.needs_text_overlay,
                "prefers_generative_art": request.prefers_generative_art,
                "quality_profile": quality_profile,
                "selected_tool": selected_tool,
                "selection_reason": selection_reason,
                "warnings": list(warnings),
                "artifact": artifact,
            }
        }
        return CreativeAssetOperationResult(
            activated=True,
            assistant_content="\n".join(content_lines),
            metadata=metadata,
            selected_provider_key=(
                "openai-cloud"
                if selected_tool.startswith(self.OPENAI_IMAGE_API_LANE)
                else (self.IDEOGRAM_BROWSER_LANE if selected_tool == self.IDEOGRAM_BROWSER_LANE else "creative-asset-operator")
            ),
            selected_model_key=selected_tool,
            routing_reason=selection_reason,
            artifact=artifact,
        )

    def build_lane_snapshot(
        self,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> list[dict[str, object]]:
        return [
            self._build_lane_entry(
                lane_key=self.CANVAS_COMPOSER_LANE,
                display_name="Canvas composer",
                ready=True,
                status="ready",
                status_reason="Lokalny deterministic composer jest gotowy i potrafi zawsze zwrocic uzyteczny PNG.",
            ),
            self._build_lane_entry(
                lane_key=self.COMFYUI_LANE,
                display_name="ComfyUI local",
                ready=self.comfyui.can_generate(),
                status="ready" if self.comfyui.can_generate() else "blocked",
                status_reason=(
                    "Lokalny diffusion lane jest gotowy do generatywnej ilustracji."
                    if self.comfyui.can_generate()
                    else "ComfyUI nie jest obecnie gotowe lub nie ma kompletnego runtime/checkpointu."
                ),
            ),
            self._build_lane_entry(
                lane_key=self.IDEOGRAM_BROWSER_LANE,
                display_name="Ideogram browser",
                ready=self._ideogram_browser_blocker(
                    allow_external_fallback=allow_external_fallback,
                    runtime_controls=runtime_controls,
                )
                is None,
                status=(
                    "ready"
                    if self._ideogram_browser_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    is None
                    else "blocked"
                ),
                status_reason=(
                    "Browserowy lane Ideogram ma gotowa sesje i moze byc uzyty do generacji przez web UI."
                    if self._ideogram_browser_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    is None
                    else self._ideogram_browser_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    or "Browserowy lane Ideogram jest zablokowany."
                ),
            ),
            self._build_lane_entry(
                lane_key=self.OPENAI_IMAGE_API_LANE,
                display_name="OpenAI image API",
                ready=self._openai_image_api_blocker(
                    allow_external_fallback=allow_external_fallback,
                    runtime_controls=runtime_controls,
                )
                is None,
                status=(
                    "ready"
                    if self._openai_image_api_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    is None
                    else "blocked"
                ),
                status_reason=(
                    "Cloud image API jest gotowe do generacji obrazu."
                    if self._openai_image_api_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    is None
                    else self._openai_image_api_blocker(
                        allow_external_fallback=allow_external_fallback,
                        runtime_controls=runtime_controls,
                    )
                    or "Cloud image API jest zablokowane."
                ),
            ),
        ]

    def build_lane_summary(self) -> dict[str, object]:
        payload = self._load_lane_status_payload()
        cache = self._load_lane_status_cache()
        last_retest_at = payload.get("updated_at")
        last_success = payload.get("last_success") if isinstance(payload.get("last_success"), dict) else {}
        latest_blocker: tuple[datetime, str, dict[str, object]] | None = None
        for lane_key, lane_payload in cache.items():
            status = str(lane_payload.get("status") or "")
            if status not in {"blocked", "degraded"}:
                continue
            updated_at = self._parse_iso_datetime(lane_payload.get("updated_at"))
            if updated_at is None:
                continue
            candidate = (updated_at, lane_key, lane_payload)
            if latest_blocker is None or candidate[0] > latest_blocker[0] or (
                candidate[0] == latest_blocker[0] and candidate[1] < latest_blocker[1]
            ):
                latest_blocker = candidate

        summary = {
            "last_retest_at": last_retest_at if isinstance(last_retest_at, str) else None,
            "last_output_lane_key": None,
            "last_output_display_name": None,
            "last_output_filename": None,
            "last_output_storage_path": None,
            "last_output_absolute_path": None,
            "last_output_width": None,
            "last_output_height": None,
            "last_output_size_bytes": None,
            "last_output_at": None,
            "last_blocker_lane_key": None,
            "last_blocker_display_name": None,
            "last_blocker_summary": None,
            "last_blocker_reason": None,
            "last_blocker_screenshot_path": None,
            "last_blocker_at": None,
        }
        last_success_lane_key = str(last_success.get("lane_key") or "") or None
        if last_success_lane_key is not None:
            summary.update(
                {
                    "last_output_lane_key": last_success_lane_key,
                    "last_output_display_name": self._lane_display_name(last_success_lane_key),
                    "last_output_filename": str(last_success.get("filename") or "") or None,
                    "last_output_storage_path": str(last_success.get("storage_path") or "") or None,
                    "last_output_absolute_path": str(last_success.get("absolute_path") or "") or None,
                    "last_output_width": self._coerce_int(last_success.get("width")),
                    "last_output_height": self._coerce_int(last_success.get("height")),
                    "last_output_size_bytes": self._coerce_int(last_success.get("size_bytes")),
                    "last_output_at": str(last_success.get("updated_at") or "") or None,
                }
            )

        if latest_blocker is None:
            return summary

        _, lane_key, lane_payload = latest_blocker
        full_reason = str(lane_payload.get("reason") or "") or None
        summary.update(
            {
                "last_blocker_lane_key": lane_key,
                "last_blocker_display_name": self._lane_display_name(lane_key),
                "last_blocker_summary": self._summarize_lane_reason(full_reason),
                "last_blocker_reason": full_reason,
                "last_blocker_screenshot_path": self._extract_screenshot_path(full_reason),
                "last_blocker_at": str(lane_payload.get("updated_at") or "") or None,
            }
        )
        return summary

    def clear_remembered_lane_blockers(self, *, lane_keys: tuple[str, ...] | None = None) -> list[str]:
        payload = self._load_lane_status_payload()
        lanes = payload.get("lanes")
        if not isinstance(lanes, dict):
            return []

        target_keys = set(lane_keys or ())
        filtered: dict[str, object] = {}
        cleared: list[str] = []
        for key, value in lanes.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            if target_keys and key not in target_keys:
                filtered[key] = value
                continue
            status = str(value.get("status") or "")
            if status in {"blocked", "degraded"}:
                cleared.append(key)
                continue
            filtered[key] = value

        payload["lanes"] = filtered
        payload["updated_at"] = self._now().isoformat() if filtered else None
        self._write_lane_status_payload(payload)
        return cleared

    def _fallback_after_tool_failure(
        self,
        *,
        request: CreativeAssetRequest,
        selected_tool: GraphicTool,
        failure_reason: str,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> tuple[GraphicTool, str] | None:
        normalized_reason = self._normalize_text(failure_reason)
        if selected_tool == "ideogram-browser" and self._can_use_openai_image_api(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ):
            if "cloudflare" in normalized_reason or "selector" in normalized_reason or "ideogram browser lane" in normalized_reason:
                model_name = self._preferred_openai_image_model()
                return (
                    f"openai-image-api:{model_name}",
                    "Ideogram browser lane nie dowiozl obrazu, wiec przelaczam sie na gotowy OpenAI image API, zeby nadal zwrocic pelny PNG.",
                )
        if selected_tool == "ideogram-browser" and not request.prefers_generative_art:
            return (
                "canvas-composer-local",
                "Browserowy generator obrazu nie dowiozl zadania, a ten brief da sie jeszcze sensownie zamknac lokalnym composerem, wiec schodze do lokalnego PNG fallbacku.",
            )
        if selected_tool.startswith("openai-image-api:"):
            if (
                ("billing" in normalized_reason or "hard limit" in normalized_reason or "quota" in normalized_reason)
                and not request.prefers_generative_art
            ):
                return (
                    "canvas-composer-local",
                    "Cloud image API nie dowiozl obrazu przez limit billingowy lub quota, wiec schodze do lokalnego composera, zeby nadal zwrocic gotowy PNG.",
                )
        return None

    def _detect_request(self, content: str) -> CreativeAssetRequest | None:
        normalized = self._normalize_text(content)
        if not normalized:
            return None
        if not any(hint in normalized for hint in self.GRAPHIC_HINTS):
            return None
        if not (
            any(self._contains_token(normalized, hint) for hint in self.ACTION_HINTS)
            or any(pattern in normalized for pattern in self.DESIRE_PATTERNS)
        ):
            return None

        graphic_type = self._detect_graphic_type(normalized)
        needs_text_overlay = any(hint in normalized for hint in self.TEXT_HEAVY_HINTS)
        prefers_generative_art = any(hint in normalized for hint in self.GENERATIVE_ART_HINTS)
        prefers_photorealism = any(hint in normalized for hint in self.PHOTOREALISTIC_HINTS)
        prefers_external_image_api = any(hint in normalized for hint in self.EXTERNAL_IMAGE_HINTS)
        prefers_browser_image_tool = any(hint in normalized for hint in self.BROWSER_IMAGE_HINTS)
        tool_notes = tuple(
            note for hint, note in self.TOOL_HINTS.items() if hint in normalized
        )
        brief = self._extract_brief(content, graphic_type=graphic_type)
        return CreativeAssetRequest(
            raw_content=content.strip(),
            brief=brief,
            graphic_type=graphic_type,
            needs_text_overlay=needs_text_overlay,
            prefers_generative_art=prefers_generative_art,
            prefers_photorealism=prefers_photorealism,
            prefers_external_image_api=prefers_external_image_api,
            prefers_browser_image_tool=prefers_browser_image_tool,
            tool_notes=tool_notes,
        )

    def _select_tool(
        self,
        request: CreativeAssetRequest,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> tuple[GraphicTool, str]:
        if request.needs_text_overlay or request.graphic_type in {"thumbnail", "poster", "banner", "cover"}:
            return (
                "canvas-composer-local",
                "Prosba wyglada na grafike tekstowa, miniature albo plakat, wiec deterministyczny composer da lepszy i czytelniejszy wynik niz surowy model diffusion.",
            )
        if request.prefers_browser_image_tool:
            return (
                "ideogram-browser",
                "Prosba jawnie wskazuje browserowy generator obrazu, wiec priorytetowo sprawdzam lane Ideogram przez zapisana sesje webowa.",
            )
        if request.prefers_external_image_api and self._can_use_openai_image_api(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ):
            return (
                f"openai-image-api:{self._preferred_openai_image_model()}",
                "Prosba jawnie wskazuje zewnetrzny image model, a OpenAI image API jest gotowe do wykonania tego zadania.",
            )
        if (request.prefers_generative_art or request.prefers_photorealism) and self.comfyui.can_generate():
            return (
                "comfyui-local",
                "Prosba wyglada na generatywna grafike bez ciezkiego layoutu tekstowego, wiec najlepszy tor to lokalny ComfyUI.",
            )
        if request.prefers_generative_art and self._can_use_openai_image_api(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ):
            return (
                f"openai-image-api:{self._preferred_openai_image_model()}",
                "Lokalny image model nie jest gotowy, a zewnetrzny OpenAI image API moze dowiezc pelna generatywna ilustracje.",
            )
        if request.prefers_generative_art and self._can_use_ideogram_browser(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ):
            return (
                "ideogram-browser",
                "Lokalny image model nie jest gotowy, ale istnieje browserowy lane Ideogram z zapisana sesja, wiec moge dowiezc obraz przez web AI.",
            )
        if not request.prefers_generative_art:
            return (
                "canvas-composer-local",
                "Nie ma aktywnego image modelu, ale moge dowiezc uzyteczna lokalna kompozycje graficzna jako fallback.",
            )
        return (
            "blocked",
            "Prosba wymaga prawdziwego image modelu, a lokalny runtime generacji obrazu nie jest obecnie gotowy.",
        )

    def _execute_with_selected_tool(
        self,
        *,
        request: CreativeAssetRequest,
        selected_tool: GraphicTool,
        conversation_id: int | None,
        warnings: list[str],
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
        quality_profile: str,
    ) -> ManagedArtifactDescriptor:
        if selected_tool == "blocked":
            raise RuntimeError(
                "Brak gotowego lane do generatywnej grafiki. Potrzebny jest aktywny image model, np. ComfyUI."
            )
        if selected_tool == "comfyui-local":
            try:
                return self._generate_with_comfyui(
                    request=request,
                    conversation_id=conversation_id,
                    quality_profile=quality_profile,
                )
            except ComfyUiImageGenerationError as exc:
                if request.needs_text_overlay or not request.prefers_generative_art:
                    warnings.append(f"ComfyUI nie dowiozl zadania, wiec przelaczylem sie na canvas fallback: {exc}")
                    return self._generate_with_canvas(request=request, conversation_id=conversation_id)
                raise RuntimeError(f"ComfyUI nie mogl wygenerowac obrazu: {exc}") from exc
        if selected_tool == "ideogram-browser":
            return self._generate_with_ideogram_browser(
                request=request,
                conversation_id=conversation_id,
                allow_external_fallback=allow_external_fallback,
                runtime_controls=runtime_controls,
            )
        if selected_tool.startswith("openai-image-api:"):
            return self._generate_with_openai_image_api(
                request=request,
                conversation_id=conversation_id,
                allow_external_fallback=allow_external_fallback,
                runtime_controls=runtime_controls,
                quality_profile=quality_profile,
            )
        return self._generate_with_canvas(request=request, conversation_id=conversation_id)

    def _generate_with_comfyui(
        self,
        *,
        request: CreativeAssetRequest,
        conversation_id: int | None,
        quality_profile: str,
    ) -> ManagedArtifactDescriptor:
        width, height = self.SIZE_BY_TYPE.get(request.graphic_type, self.SIZE_BY_TYPE["generic_image"])
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
        prompt = self._build_comfyui_prompt(request, quality_profile=quality_profile)
        steps_override, cfg_scale_override = self._resolve_comfyui_quality_overrides(
            request,
            quality_profile=quality_profile,
        )
        self.comfyui.generate_background(
            prompt=prompt,
            output_path=output_path,
            width=width,
            height=height,
            seed=self._stable_seed(request.brief),
            negative_prompt=self._build_comfyui_negative_prompt(request),
            steps_override=steps_override,
            cfg_scale_override=cfg_scale_override,
        )
        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=self._build_filename(request.graphic_type),
            media_type="image/png",
        )
        self._record_lane_status(
            self.COMFYUI_LANE,
            status="ready",
            reason="ComfyUI zwrocil gotowy obraz lokalnie.",
            artifact=descriptor,
        )
        return descriptor

    def generate_image_api_candidate(
        self,
        *,
        request: CreativeAssetRequest,
        model_name: str = "gpt-image-1.5",
        size: str = "1024x1536",
        quality: str = "high",
        reference_paths: tuple[Path, ...] | list[Path] = (),
        conversation_id: int | None = None,
        allow_external_fallback: bool = False,
        runtime_controls: RuntimeControlSnapshot | None = None,
    ) -> ImageApiCandidateResult:
        """Execute exactly one Images request; the caller owns budget and approval.

        The explicit brief is preserved, so a production's frozen drawing style
        is not replaced with the chat lane's commercial/photorealistic defaults.
        Local references select multipart edits; otherwise use generations.
        This method never retries, falls back to another model, or approves art.
        """
        if not allow_external_fallback:
            raise RuntimeError("Execution policy blocks external image API.")
        if runtime_controls is not None and not runtime_controls.external_actions_enabled:
            raise RuntimeError("Runtime blocks external image API.")
        if not re.fullmatch(r"gpt-image-[A-Za-z0-9.-]+", model_name):
            raise ValueError("An explicit GPT Image model name is required.")
        if size not in {"1024x1024", "1024x1536", "1536x1024"}:
            raise ValueError("Unsupported candidate image size.")
        if quality not in {"low", "medium", "high"}:
            raise ValueError("Unsupported candidate image quality.")
        prompt = request.brief.strip()
        if not prompt or len(prompt) > 32000:
            raise ValueError("Image candidate brief must have 1–32000 characters.")
        if len(reference_paths) > 2:
            raise ValueError("Image candidates support at most two local references.")
        references: list[tuple[Path, str]] = []
        for source in reference_paths:
            path = Path(source).resolve()
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not path.is_file():
                raise ValueError("Image reference must be a local PNG or JPEG file.")
            if path.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("Image reference exceeds the 50 MB input limit.")
            try:
                with Image.open(path) as image:
                    if image.format not in {"PNG", "JPEG"}:
                        raise ValueError("Image reference has an unsupported decoded format.")
                    mime_type = "image/png" if image.format == "PNG" else "image/jpeg"
                    image.verify()
            except (OSError, SyntaxError):
                raise ValueError("Image reference could not be decoded.") from None
            references.append((path, mime_type))

        account = self._get_configured_account_by_provider("openai-cloud")
        if account is None:
            raise RuntimeError("Vault has no configured OpenAI image API account.")
        secret = self._resolve_secret(account.id)
        if not secret:
            raise RuntimeError("Vault could not resolve the OpenAI image API credential.")
        if self._candidate_safe_text(prompt, secret) != prompt:
            raise ValueError("Image candidate brief contains secret-like content.")

        endpoint = "https://api.openai.com/v1/images/" + ("edits" if references else "generations")
        settings: dict[str, object] = {
            "model": model_name, "size": size, "quality": quality,
            "output_format": "png", "background": "opaque", "n": 1,
        }
        if references and model_name in {"gpt-image-1.5", "gpt-image-1"}:
            settings["input_fidelity"] = "high"
        body = {**settings, "prompt": prompt}
        safe_request = httpx.Request("POST", endpoint)
        transport_error: httpx.RequestError | None = None
        try:
            with ExitStack() as files_context:
                files = [
                    ("image[]", (path.name, files_context.enter_context(path.open("rb")), mime_type))
                    for path, mime_type in references
                ]
                with httpx.Client(
                    timeout=httpx.Timeout(120.0, read=300.0), follow_redirects=False,
                ) as client:
                    if references:
                        response = client.post(
                            endpoint, headers={"Authorization": f"Bearer {secret}"},
                            data={key: str(value) for key, value in body.items()}, files=files,
                        )
                    else:
                        response = client.post(
                            endpoint, headers={"Authorization": f"Bearer {secret}"}, json=body,
                        )
        except httpx.RequestError as exc:
            # Do not retain an authenticated httpx request or arbitrary transport
            # exception text in a checkpoint's serialized exception/evidence.
            transport_error = type(exc)(
                "OpenAI image API request failed before a usable response.", request=safe_request,
            )
        if transport_error is not None:
            raise transport_error

        request_id_value = response.headers.get("x-request-id")
        request_id = (
            self._candidate_safe_text(request_id_value, secret)[:120]
            if request_id_value else None
        )
        if not 200 <= response.status_code < 300:
            error_code = None
            try:
                failure = response.json()
                provider_error = failure.get("error") if isinstance(failure, dict) else None
                candidate_code = provider_error.get("code") if isinstance(provider_error, dict) else None
                if isinstance(candidate_code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", candidate_code):
                    error_code = candidate_code
            except ValueError:
                pass
            error_code = self._candidate_safe_text(error_code, secret) if error_code else None
            reason = f"OpenAI image API returned HTTP {response.status_code}"
            if error_code:
                reason += f" ({error_code})"
            safe_response = httpx.Response(
                response.status_code,
                headers={"x-request-id": request_id} if request_id else {},
                json={"error": {"code": error_code}}, request=safe_request,
            )
            self._record_lane_status(
                self.OPENAI_IMAGE_API_LANE,
                status="blocked" if self._is_billing_or_quota_reason(reason) else "degraded",
                reason=reason,
            )
            raise httpx.HTTPStatusError(reason, request=safe_request, response=safe_response)

        try:
            payload = response.json()
            first = payload["data"][0]
            encoded = first["b64_json"]
            image_bytes = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(image_bytes)) as image:
                if image.format != "PNG" or image.size != tuple(int(part) for part in size.split("x")):
                    raise ValueError("Image output does not match the requested PNG dimensions.")
                image.load()
                if image.convert("RGBA").getchannel("A").getextrema() != (255, 255):
                    raise ValueError("Image candidate output is not opaque.")
        except (KeyError, IndexError, TypeError, ValueError, OSError, binascii.Error):
            raise ValueError("OpenAI image API did not return a valid opaque PNG at the requested size.") from None
        actual_model = payload.get("model")
        if actual_model is None:
            actual_model = model_name  # Images responses normally omit model; the POST is explicit.
        if not isinstance(actual_model, str) or not re.fullmatch(r"gpt-image-[A-Za-z0-9.-]+", actual_model):
            raise ValueError("OpenAI image API returned invalid model metadata.")
        observed_settings = dict(settings)
        observed_settings["model"] = actual_model
        for key in ("size", "quality", "background", "output_format"):
            if key in payload and payload[key] != settings[key]:
                raise ValueError("OpenAI image API returned settings different from the candidate request.")
        revised_prompt = first.get("revised_prompt")
        revised_prompt = self._candidate_safe_text(revised_prompt, secret) if isinstance(revised_prompt, str) else None
        usage = self._candidate_safe_usage(payload.get("usage"))
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
        output_path.write_bytes(image_bytes)
        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path, filename=self._build_filename(request.graphic_type), media_type="image/png",
        )
        self._record_lane_status(
            self.OPENAI_IMAGE_API_LANE, status="ready",
            reason=f"OpenAI image API returned one candidate through {actual_model}.", artifact=descriptor,
        )
        return ImageApiCandidateResult(
            descriptor=descriptor, model_name=actual_model, settings=observed_settings,
            usage=usage, request_id=request_id, prompt=prompt, revised_prompt=revised_prompt,
            reference_paths=tuple(str(path) for path, _ in references), endpoint=endpoint,
        )

    @staticmethod
    def _candidate_safe_text(value: str, secret: str) -> str:
        sanitized = value.replace(secret, "[redacted]") if secret else value
        sanitized = re.sub(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{8,}", "[redacted]", sanitized)
        return re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", sanitized)

    @staticmethod
    def _candidate_safe_usage(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, object] = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            amount = value.get(key)
            if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
                result[key] = amount
        for key in ("input_tokens_details", "output_tokens_details"):
            details = value.get(key)
            if isinstance(details, dict):
                safe_details = {
                    name: amount for name, amount in details.items()
                    if name in {"text_tokens", "image_tokens", "cached_tokens"}
                    and isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0
                }
                if safe_details:
                    result[key] = safe_details
        return result

    def _generate_with_openai_image_api(
        self,
        *,
        request: CreativeAssetRequest,
        conversation_id: int | None,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
        quality_profile: str,
    ) -> ManagedArtifactDescriptor:
        if not allow_external_fallback:
            raise RuntimeError("Execution policy ma `allow_external_fallback=false`, wiec zewnetrzny image API jest zablokowany.")
        if runtime_controls is not None and not runtime_controls.external_actions_enabled:
            raise RuntimeError("Runtime ma `external_actions_enabled=false`, wiec zewnetrzny image API jest zablokowany.")
        account = self._get_configured_account_by_provider("openai-cloud")
        if account is None:
            raise RuntimeError("Vault nie ma aktywnego konta `openai-cloud` z sekretem do image API.")
        secret = self._resolve_secret(account.id)
        if not secret:
            raise RuntimeError("Local vault nie zwrocil sekretu dla `openai-cloud`.")

        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
        size = self._openai_size_for_graphic_type(request.graphic_type)
        last_error = "OpenAI image API did not return a usable image."
        for model_name in self._openai_image_model_candidates():
            try:
                with httpx.Client(timeout=120) as client:
                    response = client.post(
                        "https://api.openai.com/v1/images/generations",
                        headers={
                            "Authorization": f"Bearer {secret}",
                            "content-type": "application/json",
                        },
                        json={
                            "model": model_name,
                            "prompt": self._build_openai_image_prompt(
                                request,
                                quality_profile=quality_profile,
                            ),
                            "size": size,
                            "quality": self._openai_quality_for_request(
                                request,
                                quality_profile=quality_profile,
                            ),
                            "output_format": "png",
                            "background": "auto",
                        },
                    )
                if response.status_code >= 400:
                    last_error = self._extract_http_error(response)
                    if response.status_code in {400, 404}:
                        continue
                    raise RuntimeError(last_error)
                image_bytes = self._parse_openai_image_response(response.json())
                if not image_bytes:
                    last_error = "OpenAI image API returned an empty image payload."
                    continue
                output_path.write_bytes(image_bytes)
                descriptor = self.artifacts.finalize_output(
                    absolute_path=output_path,
                    filename=self._build_filename(request.graphic_type),
                    media_type="image/png",
                )
                self._record_lane_status(
                    self.OPENAI_IMAGE_API_LANE,
                    status="ready",
                    reason=f"OpenAI image API zwrocilo obraz przez model {model_name}.",
                    artifact=descriptor,
                )
                return descriptor
            except Exception as exc:
                last_error = self._sanitize_reason(str(exc))
                continue

        self._record_lane_status(
            self.OPENAI_IMAGE_API_LANE,
            status=("blocked" if self._is_billing_or_quota_reason(last_error) else "degraded"),
            reason=last_error,
        )
        raise RuntimeError(last_error)

    def _generate_with_ideogram_browser(
        self,
        *,
        request: CreativeAssetRequest,
        conversation_id: int | None,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> ManagedArtifactDescriptor:
        blocker = self._ideogram_browser_blocker(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        )
        if blocker is not None:
            raise RuntimeError(blocker)

        result = asyncio.run(self._run_ideogram_browser_tool(request=request))
        if not result.get("success"):
            reason = self._sanitize_reason(str(result.get("reason") or "Nieznany blad browserowego generatora obrazu."))
            screenshot_path = str(result.get("screenshot_path") or "").strip()
            if reason == "awaiting_auth":
                self._record_lane_status(
                    self.IDEOGRAM_BROWSER_LANE,
                    status="blocked",
                    reason="Brak gotowej sesji Ideogram w browser runtime. Zaloguj sie recznie i zapisz sesje.",
                )
                raise RuntimeError(
                    "Brak gotowej sesji Ideogram w browser runtime. Zaloguj sie recznie do Ideogram i zapisz sesje webowa."
                )
            if screenshot_path:
                self._record_lane_status(
                    self.IDEOGRAM_BROWSER_LANE,
                    status="blocked",
                    reason=f"Ideogram browser lane nie dowiozl grafiki: {reason}. Screenshot: {screenshot_path}",
                )
                raise RuntimeError(f"Ideogram browser lane nie dowiozl grafiki: {reason}. Screenshot: {screenshot_path}")
            self._record_lane_status(
                self.IDEOGRAM_BROWSER_LANE,
                status="blocked",
                reason=f"Ideogram browser lane nie dowiozl grafiki: {reason}",
            )
            raise RuntimeError(f"Ideogram browser lane nie dowiozl grafiki: {reason}")

        image_path = str(result.get("image_path") or "").strip()
        if not image_path:
            raise RuntimeError("Ideogram browser lane nie zwrocil sciezki do pobranego obrazu.")
        source_path = Path(image_path).resolve()
        if not source_path.exists() or not source_path.is_file():
            raise RuntimeError("Ideogram browser lane zakonczyl sie bez realnego pliku obrazu.")

        extension = source_path.suffix.lower() or ".png"
        target_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=extension)
        shutil.copyfile(source_path, target_path)
        descriptor = self.artifacts.finalize_output(
            absolute_path=target_path,
            filename=self._build_filename(request.graphic_type),
            media_type="image/png",
        )
        self._record_lane_status(
            self.IDEOGRAM_BROWSER_LANE,
            status="ready",
            reason="Ideogram browser lane zwrocil realny plik obrazu.",
            artifact=descriptor,
        )
        return descriptor

    def _generate_with_canvas(
        self,
        *,
        request: CreativeAssetRequest,
        conversation_id: int | None,
    ) -> ManagedArtifactDescriptor:
        width, height = self.SIZE_BY_TYPE.get(request.graphic_type, self.SIZE_BY_TYPE["generic_image"])
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".png")
        image = self._render_canvas_graphic(request=request, width=width, height=height)
        image.save(output_path)
        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=self._build_filename(request.graphic_type),
            media_type="image/png",
        )
        self._record_lane_status(
            self.CANVAS_COMPOSER_LANE,
            status="ready",
            reason="Lokalny canvas composer zwrocil gotowy PNG.",
            artifact=descriptor,
        )
        return descriptor

    def _render_canvas_graphic(
        self,
        *,
        request: CreativeAssetRequest,
        width: int,
        height: int,
    ) -> Image.Image:
        image = Image.new("RGB", (width, height), "#05070D")
        draw = ImageDraw.Draw(image, "RGBA")
        self._draw_gradient_background(draw=draw, width=width, height=height)
        image = self._apply_glow_layer(image=image, width=width, height=height)
        draw = ImageDraw.Draw(image, "RGBA")

        panel_margin = max(48, width // 16)
        panel_top = max(48, height // 10)
        panel_bottom = height - max(60, height // 10)
        draw.rounded_rectangle(
            (panel_margin, panel_top, width - panel_margin, panel_bottom),
            radius=max(28, width // 28),
            fill=(9, 12, 20, 168),
            outline=(255, 255, 255, 26),
            width=2,
        )

        badge_font = self._load_font(size=max(20, width // 48), bold=True)
        title_font = self._load_font(size=max(46, width // 15), bold=True)
        subtitle_font = self._load_font(size=max(22, width // 42), bold=False)
        footer_font = self._load_font(size=max(18, width // 56), bold=False)

        badge_text = f"ATNOS / {request.graphic_type.replace('_', ' ').upper()}"
        badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
        badge_width = badge_box[2] - badge_box[0]
        badge_height = badge_box[3] - badge_box[1]
        badge_x = panel_margin + 28
        badge_y = panel_top + 28
        draw.rounded_rectangle(
            (badge_x - 16, badge_y - 10, badge_x + badge_width + 16, badge_y + badge_height + 10),
            radius=18,
            fill=(18, 28, 44, 214),
            outline=(130, 202, 255, 96),
            width=2,
        )
        draw.text((badge_x, badge_y), badge_text, font=badge_font, fill=(224, 240, 255, 235))

        headline = self._build_headline(request.brief)
        subtitle = self._build_subtitle(request.brief, headline)
        text_x = panel_margin + 32
        text_y = badge_y + badge_height + 42
        self._draw_wrapped_text(
            draw=draw,
            text=headline,
            font=title_font,
            fill=(246, 248, 252, 250),
            shadow_fill=(6, 10, 18, 172),
            x=text_x,
            y=text_y,
            wrap_width=max(18, width // 18),
            line_height=int(max(52, getattr(title_font, "size", 48) * 1.05)),
            max_lines=4,
        )

        subtitle_y = text_y + int(max(170, getattr(title_font, "size", 48) * 2.9))
        self._draw_wrapped_text(
            draw=draw,
            text=subtitle,
            font=subtitle_font,
            fill=(212, 221, 232, 230),
            shadow_fill=(6, 10, 18, 132),
            x=text_x,
            y=subtitle_y,
            wrap_width=max(24, width // 24),
            line_height=int(max(32, getattr(subtitle_font, "size", 24) * 1.35)),
            max_lines=4,
        )

        accent_y = panel_bottom - max(120, height // 8)
        draw.rounded_rectangle(
            (text_x, accent_y, width - panel_margin - 32, accent_y + 3),
            radius=2,
            fill=(139, 216, 255, 188),
        )
        footer = self._build_footer(request)
        self._draw_wrapped_text(
            draw=draw,
            text=footer,
            font=footer_font,
            fill=(150, 190, 220, 220),
            shadow_fill=(6, 10, 18, 132),
            x=text_x,
            y=accent_y + 22,
            wrap_width=max(32, width // 28),
            line_height=int(max(24, getattr(footer_font, "size", 18) * 1.35)),
            max_lines=2,
        )
        return image

    def _build_blocked_result(
        self,
        *,
        request: CreativeAssetRequest,
        blocker: str,
        next_step: str,
        selected_tool: str | None = None,
        selection_reason: str | None = None,
        warnings: list[str] | None = None,
    ) -> CreativeAssetOperationResult:
        lines = []
        if selected_tool:
            lines.append(f"Wybralbym tor grafiki: `{selected_tool}`.")
        if selection_reason:
            lines.append(f"Powod: {selection_reason}")
        if warnings:
            lines.append("Uwagi do wyboru narzedzia: " + " ".join(warnings))
        lines.append(f"Bloker: {blocker}")
        lines.append(f"Nastepny krok: {next_step}")
        metadata = {
            "creative_asset_operator": {
                "graphic_type": request.graphic_type,
                "selected_tool": selected_tool or "blocked",
                "blocker": blocker,
                "next_step": next_step,
                "warnings": list(warnings or ()),
            }
        }
        return CreativeAssetOperationResult(
            activated=True,
            assistant_content="\n".join(lines),
            metadata=metadata,
            selected_provider_key="creative-asset-operator",
            selected_model_key=selected_tool or "blocked",
            routing_reason=selection_reason or blocker,
            artifact=None,
        )

    def _build_comfyui_prompt(self, request: CreativeAssetRequest, *, quality_profile: str) -> str:
        if request.prefers_photorealism:
            style_prefix = "photorealistic commercial key visual"
        elif request.graphic_type in {"poster", "banner", "cover"}:
            style_prefix = "bold editorial key art"
        else:
            style_prefix = "high quality concept illustration"
        composition = self._quality_direction_for_request(request)
        quality_direction = self._quality_profile_direction(quality_profile)
        return (
            f"{style_prefix}, {request.brief}, {composition}, {quality_direction}, premium finish, "
            "single dominant subject, controlled composition, elegant lighting, crisp detail separation, "
            "no watermark, no logo, no accidental typography"
        )

    def _build_openai_image_prompt(self, request: CreativeAssetRequest, *, quality_profile: str) -> str:
        quality_direction = self._quality_profile_direction(quality_profile)
        if request.graphic_type in {"thumbnail", "poster", "banner", "cover"}:
            return (
                f"{request.brief}. Create a premium editorial composition with one clear focal subject, "
                "strong hierarchy, high contrast, commercial-grade lighting, deliberate negative space for overlays, "
                f"clean background separation, {quality_direction}, and no accidental text, watermark, or logo."
            )
        if request.prefers_photorealism:
            return (
                f"{request.brief}. Create a photorealistic premium still image with natural material detail, "
                "controlled cinematic lighting, clean depth separation, high micro-contrast, realistic color grading, "
                f"{quality_direction}, and no watermark, logo, or accidental text."
            )
        return (
            f"{request.brief}. Create a polished high-quality still image with a strong focal subject, "
            f"controlled lighting, premium color separation, deliberate composition, {quality_direction}, "
            "and no watermark, logo, or accidental text."
        )

    def _build_comfyui_negative_prompt(self, request: CreativeAssetRequest) -> str:
        base = [
            "lowres",
            "blurry",
            "soft focus",
            "bad anatomy",
            "duplicate objects",
            "watermark",
            "logo",
            "text",
            "typography",
            "caption",
            "gibberish letters",
            "oversaturated neon",
            "jpeg artifacts",
        ]
        if request.prefers_photorealism:
            base.extend(["cgi look", "plastic skin", "uncanny face", "fake materials"])
        return ", ".join(base)

    def _resolve_comfyui_quality_overrides(
        self,
        request: CreativeAssetRequest,
        *,
        quality_profile: str,
    ) -> tuple[int, float]:
        if request.prefers_photorealism:
            steps, cfg = 28, 6.8
        elif request.graphic_type in {"poster", "banner", "cover"}:
            steps, cfg = 24, 6.4
        elif request.graphic_type == "thumbnail":
            steps, cfg = 22, 6.0
        elif request.prefers_generative_art:
            steps, cfg = 26, 7.0
        else:
            steps, cfg = 20, 5.8
        if quality_profile == "premium":
            return (min(steps + 6, 40), round(min(cfg + 0.6, 8.0), 1))
        if quality_profile == "fast-turnaround":
            return (max(steps - 6, 14), round(max(cfg - 0.6, 5.0), 1))
        return (steps, cfg)

    def _openai_quality_for_request(self, request: CreativeAssetRequest, *, quality_profile: str) -> str:
        if quality_profile == "premium":
            return "high"
        if quality_profile == "fast-turnaround":
            return "medium"
        if request.prefers_generative_art or request.prefers_photorealism:
            return "high"
        if request.graphic_type in {"thumbnail", "poster", "banner", "cover"}:
            return "high"
        return "medium"

    def _quality_direction_for_request(self, request: CreativeAssetRequest) -> str:
        if request.graphic_type == "thumbnail":
            return "thumbnail-grade readability, oversized focal subject, high color separation, typography-safe framing"
        if request.graphic_type in {"poster", "cover"}:
            return "poster-grade hierarchy, premium silhouette, strong mood, generous breathing room"
        if request.graphic_type == "banner":
            return "wide hero framing, asymmetric balance, clear focal anchor, clean edge discipline"
        if request.prefers_photorealism:
            return "studio-grade realism, natural textures, believable materials, refined cinematic grading"
        return "premium visual storytelling, clear subject focus, refined atmosphere, balanced contrast"

    @staticmethod
    def _normalize_quality_profile(value: str | None) -> str:
        normalized = " ".join((value or "").split())
        if normalized in {"fast-turnaround", "standard", "premium"}:
            return normalized
        return "standard"

    @staticmethod
    def _quality_profile_direction(quality_profile: str) -> str:
        normalized = CreativeAssetOperatorService._normalize_quality_profile(quality_profile)
        if normalized == "premium":
            return "maximum detail fidelity, refined micro-contrast, premium finish, do not optimize for speed"
        if normalized == "fast-turnaround":
            return "keep the scene simple, reduce unnecessary complexity, favor fast clean execution"
        return "balanced production quality with clean composition and efficient execution"

    def _build_filename(self, graphic_type: GraphicType) -> str:
        suffix = graphic_type.replace("_", "-")
        return f"atnos-{suffix}.png"

    def _extract_brief(self, content: str, *, graphic_type: GraphicType) -> str:
        stripped = self.LEADING_PHRASE_RE.sub("", content or "").strip(" .:-")
        if not stripped:
            return "Nowoczesna grafika dla Atnosa"
        lowered = stripped.lower()
        for noun in ("grafike", "grafika", "obraz", "obrazek", "ilustracje", "ilustracja", "miniature", "miniatura", "plakat", "banner", "cover", "thumbnail", "logo"):
            if lowered.startswith(noun + " "):
                stripped = stripped[len(noun) :].strip(" .:-")
                break
        return stripped or f"Grafika typu {graphic_type}"

    def _detect_graphic_type(self, normalized: str) -> GraphicType:
        if any(token in normalized for token in ("thumbnail", "miniatur", "youtube")):
            return "thumbnail"
        if "banner" in normalized:
            return "banner"
        if any(token in normalized for token in ("cover", "okladk")):
            return "cover"
        if "plakat" in normalized or "poster" in normalized:
            return "poster"
        return "generic_image"

    def _build_headline(self, brief: str) -> str:
        quoted = re.findall(r'"([^"]+)"', brief)
        if quoted:
            return self._display_text(quoted[0])[:44]
        cleaned = self._display_text(brief)
        words = cleaned.split()
        headline = " ".join(words[: min(len(words), 7)]).strip()
        return headline[:44] or "ATNOS GRAPHIC"

    def _build_subtitle(self, brief: str, headline: str) -> str:
        cleaned = self._display_text(brief)
        if cleaned == headline:
            return "Lokalnie zlozona grafika dobrana przez Atnosa do tego zadania."
        return cleaned[:180]

    def _build_footer(self, request: CreativeAssetRequest) -> str:
        if request.needs_text_overlay:
            return "Tor wybrany pod czytelna grafike z mocnym layoutem i tekstem."
        if request.prefers_generative_art:
            return "Tor wybrany pod wizual z mocniejszym characterem i kompozycja."
        return "Tor wybrany pod szybka, uzyteczna grafike gotowa do dalszego workflow."

    def _apply_glow_layer(self, *, image: Image.Image, width: int, height: int) -> Image.Image:
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay, "RGBA")
        glows = (
            (int(width * 0.22), int(height * 0.18), int(width * 0.28), (0, 196, 255, 96)),
            (int(width * 0.82), int(height * 0.26), int(width * 0.26), (58, 255, 190, 82)),
            (int(width * 0.72), int(height * 0.82), int(width * 0.34), (112, 92, 255, 74)),
        )
        for center_x, center_y, radius, color in glows:
            draw.ellipse(
                (center_x - radius, center_y - radius, center_x + radius, center_y + radius),
                fill=color,
            )
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=max(26, width // 24)))
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    def _draw_gradient_background(self, *, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        top = (5, 7, 13)
        bottom = (10, 20, 36)
        for y in range(height):
            factor = y / max(1, height - 1)
            red = int(top[0] + (bottom[0] - top[0]) * factor)
            green = int(top[1] + (bottom[1] - top[1]) * factor)
            blue = int(top[2] + (bottom[2] - top[2]) * factor)
            draw.line((0, y, width, y), fill=(red, green, blue))

    def _draw_wrapped_text(
        self,
        *,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.ImageFont,
        fill: tuple[int, int, int, int],
        shadow_fill: tuple[int, int, int, int],
        x: int,
        y: int,
        wrap_width: int,
        line_height: int,
        max_lines: int,
    ) -> None:
        for line in textwrap.wrap(text, width=wrap_width)[:max_lines]:
            draw.text((x, y), line, font=font, fill=shadow_fill, stroke_width=6, stroke_fill=shadow_fill)
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height

    def _load_font(self, *, size: int, bold: bool) -> ImageFont.ImageFont:
        path = self.font_bold_path if bold else self.font_regular_path
        if path is None:
            return ImageFont.load_default()
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            return ImageFont.load_default()

    @staticmethod
    def _find_font(name: str) -> Path | None:
        reportlab_font = Path(__import__("reportlab").__file__).resolve().parent / "fonts" / name
        if reportlab_font.exists():
            return reportlab_font
        return None

    @staticmethod
    def _serialize_artifact(descriptor: ManagedArtifactDescriptor) -> dict[str, object]:
        return {
            "filename": descriptor.filename,
            "storage_path": descriptor.storage_path,
            "mime": descriptor.media_type,
            "file_extension": descriptor.file_extension,
            "size_bytes": descriptor.size_bytes,
        }

    @staticmethod
    def _stable_seed(value: str) -> int:
        seed = 17
        for char in value:
            seed = (seed * 131 + ord(char)) % 2_147_483_647
        return seed or 42

    @staticmethod
    def _display_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"\s+", " ", ascii_only).strip()
        return cleaned or "ATNOS"

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.lower())
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        return " ".join(ascii_only.split())

    def _can_use_ideogram_browser(
        self,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> bool:
        return self._ideogram_browser_blocker(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ) is None

    def _can_use_openai_image_api(
        self,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> bool:
        return self._openai_image_api_blocker(
            allow_external_fallback=allow_external_fallback,
            runtime_controls=runtime_controls,
        ) is None

    def _get_configured_account_by_provider(self, provider_key: str):
        if self.account_credentials is None:
            return None
        for item in self.account_credentials.list_accounts():
            if item.provider_key != provider_key:
                continue
            if item.status != "configured":
                continue
            if not item.has_secret:
                continue
            return item
        return None

    def _resolve_secret(self, account_id: int) -> str | None:
        if self.account_credentials is None:
            return None
        return self.account_credentials.resolve_secret_for_adapter(account_id)

    @staticmethod
    def _preferred_openai_image_model() -> str:
        configured = (os.getenv("OPENAI_IMAGE_MODEL") or "").strip()
        return configured or "gpt-image-1.5"

    @classmethod
    def _openai_image_model_candidates(cls) -> tuple[str, ...]:
        configured = (os.getenv("OPENAI_IMAGE_MODEL") or "").strip()
        ordered = [configured] if configured else []
        ordered.extend(cls.OPENAI_IMAGE_MODEL_CANDIDATES)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in ordered:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            deduped.append(normalized)
            seen.add(normalized)
        return tuple(deduped)

    @staticmethod
    def _parse_openai_image_response(payload: dict[str, object]) -> bytes | None:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return None
        first = data[0]
        if not isinstance(first, dict):
            return None
        encoded = first.get("b64_json")
        if isinstance(encoded, str) and encoded.strip():
            return base64.b64decode(encoded)
        return None

    @staticmethod
    def _extract_http_error(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"{response.request.url.host} returned status {response.status_code}."
        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str) and message:
                    return message
            message = payload.get("message")
            if isinstance(message, str) and message:
                return message
        return f"{response.request.url.host} returned status {response.status_code}."

    @staticmethod
    def _sanitize_reason(value: str) -> str:
        return " ".join(str(value or "").strip().split()) or "Nieznany blad image API."

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        try:
            numeric = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return numeric if numeric >= 0 else None

    @staticmethod
    def _openai_size_for_graphic_type(graphic_type: GraphicType) -> str:
        if graphic_type in {"poster", "cover"}:
            return "1024x1536"
        if graphic_type in {"banner", "thumbnail"}:
            return "1536x1024"
        return "1024x1024"

    @staticmethod
    def _contains_token(normalized: str, token: str) -> bool:
        pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
        return re.search(pattern, normalized) is not None

    def _ideogram_browser_blocker(
        self,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> str | None:
        if not allow_external_fallback:
            return "Execution policy ma `allow_external_fallback=false`, wiec browserowy generator obrazu jest zablokowany."
        if runtime_controls is not None and not runtime_controls.external_actions_enabled:
            return "Runtime ma `external_actions_enabled=false`, wiec browserowy generator obrazu jest zablokowany."
        if runtime_controls is not None and not runtime_controls.browser_control_enabled:
            return "Runtime ma `browser_control_enabled=false`, wiec browserowy generator obrazu jest zablokowany."
        browser_state = BrowserRuntimeService(self.settings).describe()
        if not browser_state.ready:
            return browser_state.status_reason or "Browser runtime nie jest gotowy."
        session_state_path = self._ideogram_session_state_path()
        if not session_state_path.exists():
            return "Brak zapisanej sesji Ideogram w browser runtime. Zaloguj sie recznie do Ideogram i zapisz sesje webowa."
        cached_blocker = self._recent_lane_blocker(
            self.IDEOGRAM_BROWSER_LANE,
            ttl=self.BROWSER_BLOCK_TTL,
            accepted_codes={"cloudflare_or_selector", "browser_challenge"},
        )
        if cached_blocker is not None:
            return (
                "Atnos zapamietal, ze Ideogram browser lane jest ostatnio zablokowany: "
                f"{cached_blocker}"
            )
        return None

    def _openai_image_api_blocker(
        self,
        *,
        allow_external_fallback: bool,
        runtime_controls: RuntimeControlSnapshot | None,
    ) -> str | None:
        if not allow_external_fallback:
            return "Execution policy ma `allow_external_fallback=false`, wiec cloud image API jest zablokowane."
        if runtime_controls is not None and not runtime_controls.external_actions_enabled:
            return "Runtime ma `external_actions_enabled=false`, wiec cloud image API jest zablokowane."
        account = self._get_configured_account_by_provider("openai-cloud")
        if account is None:
            return "Vault nie ma aktywnego konta `openai-cloud` z sekretem do image API."
        if not self._resolve_secret(account.id):
            return "Local vault nie zwrocil sekretu dla `openai-cloud`."
        cached_blocker = self._recent_lane_blocker(
            self.OPENAI_IMAGE_API_LANE,
            ttl=self.BILLING_BLOCK_TTL,
            accepted_codes={"billing_or_quota"},
        )
        if cached_blocker is not None:
            return (
                "Atnos zapamietal, ze OpenAI image API jest ostatnio zablokowane: "
                f"{cached_blocker}"
            )
        return None

    def _build_lane_entry(
        self,
        *,
        lane_key: str,
        display_name: str,
        ready: bool,
        status: str,
        status_reason: str,
    ) -> dict[str, object]:
        cache = self._load_lane_status_cache().get(lane_key, {})
        return {
            "lane_key": lane_key,
            "display_name": display_name,
            "ready": ready,
            "status": status,
            "status_reason": status_reason,
            "last_recorded_status": str(cache.get("status") or ""),
            "last_recorded_reason": str(cache.get("reason") or ""),
            "last_recorded_at": str(cache.get("updated_at") or "") or None,
        }

    def _record_lane_status(
        self,
        lane_key: str,
        *,
        status: str,
        reason: str,
        artifact: ManagedArtifactDescriptor | None = None,
    ) -> None:
        payload = self._load_lane_status_payload()
        lanes = payload.setdefault("lanes", {})
        sanitized_reason = self._sanitize_reason(reason)
        updated_at = self._now().isoformat()
        width, height = self._extract_image_dimensions(artifact)
        lanes[lane_key] = {
            "status": status,
            "reason": sanitized_reason,
            "code": self._classify_lane_reason(sanitized_reason),
            "updated_at": updated_at,
        }
        if artifact is not None and status == "ready":
            lanes[lane_key]["artifact"] = {
                "filename": artifact.filename,
                "storage_path": artifact.storage_path,
                "absolute_path": str(artifact.absolute_path),
                "media_type": artifact.media_type,
                "file_extension": artifact.file_extension,
                "width": width,
                "height": height,
                "size_bytes": artifact.size_bytes,
                "updated_at": updated_at,
            }
            payload["last_success"] = {
                "lane_key": lane_key,
                "filename": artifact.filename,
                "storage_path": artifact.storage_path,
                "absolute_path": str(artifact.absolute_path),
                "width": width,
                "height": height,
                "size_bytes": artifact.size_bytes,
                "updated_at": updated_at,
            }
        payload["updated_at"] = updated_at
        self._write_lane_status_payload(payload)

    @staticmethod
    def _extract_image_dimensions(artifact: ManagedArtifactDescriptor | None) -> tuple[int | None, int | None]:
        if artifact is None:
            return None, None
        if not artifact.media_type.startswith("image/"):
            return None, None
        try:
            with Image.open(artifact.absolute_path) as image:
                return int(image.width), int(image.height)
        except (OSError, ValueError):
            return None, None

    def _recent_lane_blocker(
        self,
        lane_key: str,
        *,
        ttl: timedelta,
        accepted_codes: set[str],
    ) -> str | None:
        cache = self._load_lane_status_cache().get(lane_key)
        if not isinstance(cache, dict):
            return None
        status = str(cache.get("status") or "")
        code = str(cache.get("code") or "")
        updated_at = self._parse_iso_datetime(cache.get("updated_at"))
        if status != "blocked":
            return None
        if accepted_codes and code not in accepted_codes:
            return None
        if updated_at is None or (self._now() - updated_at) > ttl:
            return None
        return str(cache.get("reason") or "") or None

    def _load_lane_status_payload(self) -> dict[str, object]:
        path = self._lane_status_path()
        if not path.exists():
            return {"lanes": {}, "updated_at": None}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"lanes": {}, "updated_at": None}
        if not isinstance(payload, dict):
            return {"lanes": {}, "updated_at": None}
        return payload

    def _load_lane_status_cache(self) -> dict[str, dict[str, object]]:
        payload = self._load_lane_status_payload()
        lanes = payload.get("lanes")
        if not isinstance(lanes, dict):
            return {}
        normalized: dict[str, dict[str, object]] = {}
        for key, value in lanes.items():
            if isinstance(key, str) and isinstance(value, dict):
                normalized[key] = value
        return normalized

    def _lane_status_path(self) -> Path:
        return (self.settings.runtime_dir / "creative-asset-lanes.json").resolve()

    def _lane_display_name(self, lane_key: str) -> str:
        return {
            self.CANVAS_COMPOSER_LANE: "Canvas composer",
            self.COMFYUI_LANE: "ComfyUI local",
            self.IDEOGRAM_BROWSER_LANE: "Ideogram browser",
            self.OPENAI_IMAGE_API_LANE: "OpenAI image API",
        }.get(lane_key, lane_key)

    def _summarize_lane_reason(self, reason: str | None) -> str | None:
        if not reason:
            return None
        summary, _ = self._split_reason_and_screenshot(reason)
        return summary or reason

    def _extract_screenshot_path(self, reason: str | None) -> str | None:
        if not reason:
            return None
        _, screenshot_path = self._split_reason_and_screenshot(reason)
        return screenshot_path

    def _split_reason_and_screenshot(self, reason: str) -> tuple[str, str | None]:
        marker = "Screenshot:"
        if marker not in reason:
            return reason.strip(), None
        summary, screenshot_path = reason.split(marker, maxsplit=1)
        return summary.strip(), (screenshot_path.strip() or None)

    def _write_lane_status_payload(self, payload: dict[str, object]) -> None:
        lanes = payload.get("lanes")
        if not isinstance(lanes, dict) or not lanes:
            try:
                self._lane_status_path().unlink()
            except FileNotFoundError:
                pass
            return
        path = self._lane_status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _classify_lane_reason(cls, reason: str) -> str:
        normalized = cls._normalize_text(reason)
        if cls._is_billing_or_quota_reason(normalized):
            return "billing_or_quota"
        if "cloudflare" in normalized or "selector" in normalized:
            return "cloudflare_or_selector"
        if "challenge" in normalized:
            return "browser_challenge"
        return "generic_failure"

    @staticmethod
    def _parse_iso_datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _is_billing_or_quota_reason(cls, value: str) -> bool:
        normalized = cls._normalize_text(value)
        return "billing" in normalized or "hard limit" in normalized or "quota" in normalized

    def _ideogram_session_state_path(self) -> Path:
        from atnos.jarvis.core.config import JarvisConfigStore

        store = JarvisConfigStore(self.settings)
        store.ensure_layout()
        return (store.paths.browser_sessions_dir / "ideogram.json").resolve()

    async def _run_ideogram_browser_tool(self, *, request: CreativeAssetRequest) -> dict[str, object]:
        from atnos.jarvis.core.config import JarvisConfigStore
        from atnos.jarvis.core.event_bus import EventBus
        from atnos.jarvis.workers.browser.session_manager import BrowserSessionManager
        from atnos.jarvis.workers.tools.image_gen_browser import ImageGenerationBrowserTool

        class _UnavailableLocalPrimary:
            async def is_available(self, **kwargs) -> bool:
                return False

        store = JarvisConfigStore(self.settings)
        await store.load(force=True)
        manager = BrowserSessionManager(self.settings, store, EventBus())
        await manager.preload_all()
        tool = ImageGenerationBrowserTool(self.settings, store, manager, _UnavailableLocalPrimary())
        try:
            result = await tool.run(
                prompt=self._build_openai_image_prompt(request),
                task_id=f"creative-image-{self._stable_seed(request.brief)}",
            )
            return {
                "success": bool(result.success),
                "reason": result.reason,
                "provider_key": result.provider_key,
                "image_path": str(result.artifacts.get("image_path") or ""),
                "screenshot_path": result.screenshot_path,
            }
        finally:
            await manager.shutdown()
