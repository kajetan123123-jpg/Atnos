from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import subprocess
from time import monotonic
from typing import Literal

import httpx
import imageio_ffmpeg

from atnos.core.config import Settings
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.local_video_generation_service import LocalVideoGenerationService
from atnos.services.ollama_model_roles_service import OllamaModelRolesService
from atnos.services.wav2lip_runtime_service import Wav2LipRuntimeService

LocalRuntimeProbeStatus = Literal[
    "ready",
    "configured",
    "missing",
    "unreachable",
    "blocked",
    "fallback_only",
    "missing_main_model",
    "missing_vision_model",
]
LocalRuntimeProbeKind = Literal["http", "filesystem", "python_package", "binary", "mixed"]


@dataclass(frozen=True, slots=True)
class HttpProbeResult:
    ok: bool
    status_code: int | None
    payload: dict[str, object] | None
    error: str | None
    latency_ms: int | None


@dataclass(frozen=True, slots=True)
class LocalRuntimeProbeView:
    key: str
    label: str
    status: LocalRuntimeProbeStatus
    ready: bool
    probe_kind: LocalRuntimeProbeKind
    reason: str
    checked_at: datetime
    details: dict[str, object]
    suggestions: tuple[str, ...]


class LocalRuntimeProbeService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def probe_all(self) -> list[LocalRuntimeProbeView]:
        return [
            self.probe_ollama(),
            self.probe_comfyui(),
            self.probe_kokoro_local(),
            self.probe_ffmpeg_amf(),
            self.probe_wav2lip(),
            self.probe_ltx_video(),
            self.probe_wan_video(),
        ]

    def probe_ollama(self) -> LocalRuntimeProbeView:
        base_url = self.settings.ollama_base_url.rstrip("/")
        probe = self._probe_http_json(
            f"{base_url}/api/tags",
            timeout_s=self.settings.ollama_health_timeout_s,
        )
        configured_models = self._collect_ollama_configured_models()
        if probe.ok:
            models = self._extract_ollama_models(probe.payload)
            role_snapshot = OllamaModelRolesService(self.settings).probe(
                installed_models=tuple(models),
            )
            missing_configured = [item for item in configured_models if item not in models] if models else []
            suggestions = tuple(role_snapshot.suggestions) or (
                "Skonfiguruj runtime model names w Ustawieniach, jesli chcesz precyzyjnego routingu.",
            )
            return self._view(
                key="ollama-local",
                label="Ollama Local",
                status=role_snapshot.status,
                ready=role_snapshot.ready,
                probe_kind="http",
                reason=role_snapshot.reason,
                details={
                    "base_url": base_url,
                    "latency_ms": probe.latency_ms,
                    "installed_models": models,
                    "configured_models": configured_models,
                    "missing_configured_models": missing_configured,
                    "model_roles": role_snapshot.to_dict(),
                },
                suggestions=suggestions,
            )
        return self._view(
            key="ollama-local",
            label="Ollama Local",
            status="unreachable",
            ready=False,
            probe_kind="http",
            reason=probe.error or "Ollama nie odpowiada lokalnie.",
            details={
                "base_url": base_url,
                "status_code": probe.status_code,
                "configured_models": configured_models,
            },
            suggestions=(
                "Uruchom lokalny runtime Ollama i upewnij sie, ze endpoint jest zgodny z OLLAMA_BASE_URL.",
                "Jesli runtime dziala, sprawdz zapore albo konflikt portu 11434.",
            ),
        )

    def probe_comfyui(self) -> LocalRuntimeProbeView:
        runtime = ComfyUiImageGenerationService(self.settings)
        runtime_state = ComfyUiRuntimeService(self.settings).describe_install()
        base_url = self.settings.comfyui_base_url.rstrip("/")
        probe = self._probe_http_json(
            f"{base_url}/system_stats",
            timeout_s=self.settings.comfyui_health_timeout_s,
        )
        artifact_hits = tuple(
            item
            for item in (
                str(runtime_state.install_dir) if runtime_state.install_dir is not None else None,
                str(runtime_state.run_script) if runtime_state.run_script is not None else None,
            )
            if item
        )
        checkpoint_hits = [str(path) for path in runtime.list_checkpoint_candidates()]
        checkpoint_name = runtime.resolve_local_checkpoint_name()
        api_checkpoint_names: list[str] = []
        if probe.ok and not checkpoint_name:
            checkpoint_probe = self._probe_http_json(
                f"{base_url}/object_info/CheckpointLoaderSimple",
                timeout_s=self.settings.comfyui_health_timeout_s,
            )
            api_checkpoint_names = self._extract_comfyui_checkpoint_names(checkpoint_probe.payload)
            checkpoint_name = api_checkpoint_names[0] if api_checkpoint_names else None
        if probe.ok and checkpoint_name:
            return self._view(
                key="comfyui",
                label="ComfyUI",
                status="ready",
                ready=True,
                probe_kind="http",
                reason="ComfyUI odpowiada na /system_stats.",
                details={
                    "base_url": base_url,
                    "latency_ms": probe.latency_ms,
                    "artifact_hits": list(artifact_hits),
                    "checkpoint_hits": checkpoint_hits,
                    "api_checkpoint_names": api_checkpoint_names,
                    "default_checkpoint": checkpoint_name,
                    "payload_keys": sorted((probe.payload or {}).keys()),
                },
                suggestions=("Mozesz podpinac workflow identity-still i image generation przez lokalny serwer.",),
            )
        if probe.ok:
            return self._view(
                key="comfyui",
                label="ComfyUI",
                status="configured",
                ready=False,
                probe_kind="mixed",
                reason="Serwer ComfyUI odpowiada, ale nie widze checkpointu do realnej generacji obrazow.",
                details={
                    "base_url": base_url,
                    "latency_ms": probe.latency_ms,
                    "artifact_hits": list(artifact_hits),
                    "checkpoint_hits": checkpoint_hits,
                    "api_checkpoint_names": api_checkpoint_names,
                    "default_checkpoint": checkpoint_name,
                    "payload_keys": sorted((probe.payload or {}).keys()),
                },
                suggestions=(
                    "Dodaj checkpoint .safetensors/.ckpt do ComfyUI models/checkpoints albo ustaw COMFYUI_DEFAULT_CHECKPOINT.",
                ),
            )
        status: LocalRuntimeProbeStatus = "configured" if artifact_hits or checkpoint_hits else "missing"
        reason = (
            "Znaleziono pliki ComfyUI, ale serwer nie odpowiada."
            if artifact_hits or checkpoint_hits
            else "Nie znaleziono lokalnej instancji ani plikow ComfyUI."
        )
        return self._view(
            key="comfyui",
            label="ComfyUI",
            status=status,
            ready=False,
            probe_kind="mixed",
            reason=reason,
            details={
                "base_url": base_url,
                "status_code": probe.status_code,
                "artifact_hits": list(artifact_hits),
                "checkpoint_hits": checkpoint_hits,
                "default_checkpoint": checkpoint_name,
            },
            suggestions=(
                "Jesli ComfyUI jest zainstalowane, uruchom serwer API i ustaw COMFYUI_BASE_URL, jesli port jest niestandardowy.",
                "Jesli nie jest zainstalowane, potraktuj to jako pierwszy runtime do podpiecia pod Synthetic Creator.",
            ),
        )

    @staticmethod
    def _extract_comfyui_checkpoint_names(payload: dict[str, object] | None) -> list[str]:
        loader_info = payload.get("CheckpointLoaderSimple") if isinstance(payload, dict) else None
        if not isinstance(loader_info, dict):
            return []
        input_info = loader_info.get("input")
        if not isinstance(input_info, dict):
            return []
        required = input_info.get("required")
        if not isinstance(required, dict):
            return []
        ckpt_info = required.get("ckpt_name")
        names = None
        if isinstance(ckpt_info, (list, tuple)) and ckpt_info:
            names = ckpt_info[0]
        if not isinstance(names, list):
            return []
        return [str(name).strip() for name in names if str(name).strip()]

    def _find_comfyui_runtime_hits(self) -> tuple[str, ...]:
        candidates = (
            self.settings.comfyui_install_dir,
            self.settings.app_root.parent / "ComfyUI_windows_portable",
            self.settings.app_root.parent / "ComfyUI",
            Path("E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Installs/Atnos/ComfyUI"),
            Path("E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Shared/models"),
        )
        hits: list[str] = []
        seen: set[str] = set()
        for root in candidates:
            resolved = root.resolve()
            if not resolved.exists() or not resolved.is_dir():
                continue
            if not ((resolved / "main.py").exists() or (resolved / "custom_nodes").exists()):
                continue
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(str(resolved))
        return tuple(hits)

    def probe_kokoro_local(self) -> LocalRuntimeProbeView:
        runtime = KokoroLocalRuntimeService(self.settings)
        module_name = runtime.available_python_module()
        assets = runtime.resolve_assets()
        configured_hits = [
            str(self.settings.kokoro_model_path),
            str(self.settings.kokoro_voices_path),
        ]
        if assets is not None:
            return self._view(
                key="kokoro-local",
                label="Kokoro Local",
                status="ready",
                ready=True,
                probe_kind="mixed",
                reason="Pakiet Kokoro oraz model ONNX z voices bundle sa gotowe lokalnie.",
                details={
                    "python_module": assets.python_module,
                    "model_path": str(assets.model_path),
                    "voices_path": str(assets.voices_path),
                    "configured_paths": configured_hits,
                    "preferred_voice": assets.preferred_voice,
                    "espeak_data_path": assets.espeak_data_path,
                    "espeak_library_path": assets.espeak_library_path,
                },
                suggestions=("Mozesz wlaczyc Kokoro jako domyslny lokalny voice runtime dla polskich shortow.",),
            )
        if module_name:
            return self._view(
                key="kokoro-local",
                label="Kokoro Local",
                status="configured",
                ready=False,
                probe_kind="python_package",
                reason="Pakiet Kokoro jest zainstalowany, ale brakuje model.onnx albo voices-v1.0.bin.",
                details={
                    "python_module": module_name,
                    "configured_paths": configured_hits,
                },
                suggestions=(
                    "Dodaj model ONNX i voices-v1.0.bin do kanonicznego storage Atnosa na E: przed wlaczeniem voice runtime.",
                ),
            )
        return self._view(
            key="kokoro-local",
            label="Kokoro Local",
            status="missing",
            ready=False,
            probe_kind="python_package",
            reason="Pakiet Kokoro nie jest dostepny w aktualnym venv.",
            details={
                "python_module": None,
                "configured_paths": configured_hits,
            },
            suggestions=(
                "Zainstaluj kokoro-onnx lub kokoro do runtime venv i trzymaj artefakty glosu w E:/Atnos/Models.",
            ),
        )

    def probe_ffmpeg_amf(self) -> LocalRuntimeProbeView:
        ffmpeg_path = self._resolve_ffmpeg_path()
        if ffmpeg_path is None:
            return self._view(
                key="ffmpeg-amf",
                label="FFmpeg AMF",
                status="missing",
                ready=False,
                probe_kind="binary",
                reason="Nie znaleziono lokalnego ffmpeg.",
                details={},
                suggestions=("Dodaj ffmpeg z obsluga AMF do E:/Atnos/Tools/FFmpeg.",),
            )
        command = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if command.returncode != 0:
            return self._view(
                key="ffmpeg-amf",
                label="FFmpeg AMF",
                status="blocked",
                ready=False,
                probe_kind="binary",
                reason="FFmpeg uruchomil sie z bledem i nie zwrocil listy encoderow.",
                details={
                    "ffmpeg_path": ffmpeg_path,
                    "stderr": command.stderr.strip()[:800],
                },
                suggestions=("Sprawdz binarke ffmpeg i zgodnosc lokalnego runtime z ta maszyna.",),
            )
        stdout = command.stdout.lower()
        encoders = [
            encoder
            for encoder in ("h264_amf", "hevc_amf", "av1_amf")
            if encoder in stdout
        ]
        if encoders:
            return self._view(
                key="ffmpeg-amf",
                label="FFmpeg AMF",
                status="ready",
                ready=True,
                probe_kind="binary",
                reason="FFmpeg z encoderami AMD AMF jest gotowy.",
                details={
                    "ffmpeg_path": ffmpeg_path,
                    "available_amf_encoders": encoders,
                },
                suggestions=("Mozesz kierowac finalny export shortow przez h264_amf lub hevc_amf.",),
            )
        return self._view(
            key="ffmpeg-amf",
            label="FFmpeg AMF",
            status="configured",
            ready=False,
            probe_kind="binary",
            reason="FFmpeg jest obecny, ale nie wykrylem encoderow AMD AMF.",
            details={
                "ffmpeg_path": ffmpeg_path,
                "available_amf_encoders": [],
            },
            suggestions=(
                "Podmien ffmpeg na build z obsluga AMF, jesli chcesz przyspieszonego eksportu na AMD.",
            ),
        )

    def probe_wav2lip(self) -> LocalRuntimeProbeView:
        runtime = Wav2LipRuntimeService(self.settings)
        state = runtime.describe_install()
        dependency_state = runtime.dependency_status()
        missing_dependencies = [name for name, ready in dependency_state.items() if not ready]
        artifact_hits = [
            str(item)
            for item in (state.custom_node_dir, state.runner_script)
            if item is not None
        ]
        checkpoint_hits = [str(state.checkpoint_path)] if state.checkpoint_path is not None else []
        face_detector_hits = [str(state.face_detector_path)] if state.face_detector_path is not None else []
        if state.exists and state.checkpoint_path is not None and state.face_detector_path is not None and not missing_dependencies:
            return self._view(
                key="wav2lip",
                label="Wav2Lip",
                status="ready",
                ready=True,
                probe_kind="mixed",
                reason="Wav2Lip custom node, model, detector twarzy i zaleznosci sa gotowe lokalnie.",
                details={
                    "artifact_hits": list(artifact_hits),
                    "checkpoint_hits": checkpoint_hits,
                    "face_detector_hits": face_detector_hits,
                    "dependency_state": dependency_state,
                },
                suggestions=("Przetestuj krotki talking-head pass przed wlaczeniem do batchy synthetic creator.",),
            )
        if state.exists or artifact_hits or checkpoint_hits or face_detector_hits:
            return self._view(
                key="wav2lip",
                label="Wav2Lip",
                status="configured",
                ready=False,
                probe_kind="mixed",
                reason="Widze runtime Wav2Lip, ale brakuje modelu, detektora twarzy albo zaleznosci do realnej inferencji.",
                details={
                    "artifact_hits": list(artifact_hits),
                    "checkpoint_hits": checkpoint_hits,
                    "face_detector_hits": face_detector_hits,
                    "dependency_state": dependency_state,
                    "missing_dependencies": missing_dependencies,
                },
                suggestions=("Domknij modele i zaleznosci, a potem zrob smoke still-image plus audio do MP4.",),
            )
        return self._view(
            key="wav2lip",
            label="Wav2Lip",
            status="missing",
            ready=False,
            probe_kind="filesystem",
            reason="Nie wykrylem lokalnego runtime Wav2Lip.",
            details={"artifact_hits": [], "dependency_state": dependency_state},
            suggestions=("Podlacz Wav2Lip dopiero po ustabilizowaniu glownego pipeline talking-head.",),
        )

    def probe_ltx_video(self) -> LocalRuntimeProbeView:
        return self._probe_video_runtime(
            key="ltx-video",
            label="LTX-Video",
            keywords=("ltx", "ltx-video"),
            install_hint="Dodaj szybki backend teaserowy LTX-Video do Comfy Desktop na E:.",
        )

    def probe_wan_video(self) -> LocalRuntimeProbeView:
        return self._probe_video_runtime(
            key="wan-video",
            label="Wan Video",
            keywords=("wan-video", "wan2", "wan2.1", "wan2.2"),
            install_hint="Dodaj wysokiej jakosci backend Wan jako opcjonalny quality pass, nie blocker MVP.",
        )

    def _probe_video_runtime(
        self,
        *,
        key: str,
        label: str,
        keywords: tuple[str, ...],
        install_hint: str,
    ) -> LocalRuntimeProbeView:
        video = LocalVideoGenerationService(self.settings)
        backend = "ltx-video" if key == "ltx-video" else "wan-video"
        model_dir = (
            self.settings.ltx_video_model_dir
            if backend == "ltx-video"
            else self.settings.wan_video_model_dir
        )
        dependency_state = {
            package: importlib.util.find_spec(package) is not None
            for package in video.REQUIRED_PACKAGES
        }
        missing_dependencies = [name for name, ready in dependency_state.items() if not ready]
        model_index = (model_dir / "model_index.json") if model_dir is not None else None
        if model_index is not None and model_index.exists() and not missing_dependencies:
            return self._view(
                key=key,
                label=label,
                status="ready",
                ready=True,
                probe_kind="mixed",
                reason=f"{label} model i zaleznosci diffusers sa gotowe lokalnie.",
                details={
                    "model_dir": str(model_dir),
                    "model_index": str(model_index),
                    "dependency_state": dependency_state,
                },
                suggestions=("Mozesz generowac lokalne MP4 z poziomu glownego Chatu.",),
            )
        if model_index is not None and model_index.exists():
            return self._view(
                key=key,
                label=label,
                status="configured",
                ready=False,
                probe_kind="mixed",
                reason=f"{label} model jest pobrany, ale brakuje zaleznosci inferencyjnych.",
                details={
                    "model_dir": str(model_dir),
                    "model_index": str(model_index),
                    "dependency_state": dependency_state,
                    "missing_dependencies": missing_dependencies,
                },
                suggestions=("Startup Atnosa sprobuje automatycznie doinstalowac brakujace pakiety pip.",),
            )
        artifact_hits = self._find_named_paths(keywords, max_depth=6, limit=10)
        model_hits = [path for path in artifact_hits if path.lower().endswith((".safetensors", ".ckpt", ".bin", ".pt", ".pth"))]
        if model_hits:
            return self._view(
                key=key,
                label=label,
                status="configured",
                ready=False,
                probe_kind="filesystem",
                reason="Wykrylem artefakty modelu, ale nie ma jeszcze potwierdzonego live backendu inferencyjnego.",
                details={
                    "model_dir": str(model_dir) if model_dir is not None else None,
                    "artifact_hits": list(artifact_hits),
                    "model_hits": model_hits,
                    "dependency_state": dependency_state,
                    "missing_dependencies": missing_dependencies,
                },
                suggestions=("Po podpieciu executor pathu zrob probe realnej inferencji na jednym krotkim klipie.",),
            )
        return self._view(
            key=key,
            label=label,
            status="missing",
            ready=False,
            probe_kind="filesystem",
            reason="Nie wykrylem lokalnych artefaktow runtime wideo.",
            details={
                "model_dir": str(model_dir) if model_dir is not None else None,
                "artifact_hits": list(artifact_hits),
                "dependency_state": dependency_state,
                "missing_dependencies": missing_dependencies,
            },
            suggestions=(install_hint,),
        )

    def _probe_http_json(self, url: str, *, timeout_s: float) -> HttpProbeResult:
        started_at = monotonic()
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.get(url)
        except httpx.TimeoutException:
            return HttpProbeResult(
                ok=False,
                status_code=None,
                payload=None,
                error="Probe timed out.",
                latency_ms=None,
            )
        except httpx.HTTPError as exc:
            return HttpProbeResult(
                ok=False,
                status_code=None,
                payload=None,
                error=str(exc),
                latency_ms=None,
            )
        latency_ms = max(1, int((monotonic() - started_at) * 1000))
        if response.status_code >= 400:
            return HttpProbeResult(
                ok=False,
                status_code=response.status_code,
                payload=None,
                error=f"HTTP {response.status_code}",
                latency_ms=latency_ms,
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return HttpProbeResult(
            ok=True,
            status_code=response.status_code,
            payload=payload if isinstance(payload, dict) else None,
            error=None,
            latency_ms=latency_ms,
        )

    def _collect_ollama_configured_models(self) -> list[str]:
        items: list[str] = []
        for value in (
            self.settings.ollama_chat_model,
            self.settings.ollama_main_model,
            self.settings.ollama_document_model,
            self.settings.ollama_synthesis_model,
            self.settings.ollama_orchestration_model,
            self.settings.ollama_vision_model,
            self.settings.ollama_fast_model,
        ):
            normalized = str(value or "").strip()
            if normalized and normalized not in items:
                items.append(normalized)
        return items

    @staticmethod
    def _extract_ollama_models(payload: dict[str, object] | None) -> list[str]:
        if not payload:
            return []
        models = payload.get("models")
        if not isinstance(models, list):
            return []
        extracted: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                extracted.append(name)
        return extracted

    @staticmethod
    def _first_available_module(names: tuple[str, ...]) -> str | None:
        for name in names:
            if importlib.util.find_spec(name) is not None:
                return name
        return None

    def _find_named_paths(
        self,
        keywords: tuple[str, ...],
        *,
        max_depth: int,
        limit: int,
    ) -> tuple[str, ...]:
        lowered_keywords = tuple(item.lower() for item in keywords)
        seen: set[str] = set()
        hits: list[str] = []
        for root in self._search_roots():
            if not root.exists():
                continue
            root_depth = len(root.resolve().parts)
            for current_root, dirs, files in os.walk(root):
                current_path = Path(current_root)
                depth = len(current_path.parts) - root_depth
                if depth > max_depth:
                    dirs[:] = []
                    continue
                entries = [*dirs, *files]
                for name in entries:
                    candidate_lower = name.lower()
                    if not any(keyword in candidate_lower for keyword in lowered_keywords):
                        continue
                    candidate = str((current_path / name).resolve())
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    hits.append(candidate)
                    if len(hits) >= limit:
                        return tuple(hits)
        return tuple(hits)

    def _search_roots(self) -> tuple[Path, ...]:
        roots = [
            self.settings.app_root,
            self.settings.data_dir,
            self.settings.workspace_staging_dir,
            Path("E:/Atnos/Tools"),
            Path("E:/Atnos/Models"),
        ]
        normalized: list[Path] = []
        seen: set[str] = set()
        for item in roots:
            resolved = item.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(resolved)
        return tuple(normalized)

    @staticmethod
    def _resolve_ffmpeg_path() -> str | None:
        env_path = os.environ.get("IMAGEIO_FFMPEG_EXE")
        if env_path and Path(env_path).exists():
            return str(Path(env_path).resolve())
        try:
            resolved = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            resolved = None
        if resolved and Path(resolved).exists():
            return str(Path(resolved).resolve())
        fallback_candidates = (
            Path("E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/tool-caches/ms-playwright/ffmpeg-1011/ffmpeg-win64.exe"),
            Path("E:/Atnos/Tools/FFmpeg/bin/ffmpeg.exe"),
        )
        for candidate in fallback_candidates:
            if candidate.exists():
                return str(candidate.resolve())
        return None

    def _view(
        self,
        *,
        key: str,
        label: str,
        status: LocalRuntimeProbeStatus,
        ready: bool,
        probe_kind: LocalRuntimeProbeKind,
        reason: str,
        details: dict[str, object],
        suggestions: tuple[str, ...],
    ) -> LocalRuntimeProbeView:
        return LocalRuntimeProbeView(
            key=key,
            label=label,
            status=status,
            ready=ready,
            probe_kind=probe_kind,
            reason=reason,
            checked_at=datetime.now(timezone.utc),
            details=details,
            suggestions=suggestions,
        )
