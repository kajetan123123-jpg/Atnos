"""Wealth Blueprint Video Engine dependency health checks.

Every path/URL comes from Settings / env. Failures surface as BLOCKED_DEPENDENCY —
never as silent offline substitutes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import httpx

from atnos.core.config import Settings
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.remotion_renderer import probe_remotion_runtime

DependencyName = Literal["ffmpeg", "remotion", "comfyui", "tts", "whisper"]
CheckStatus = Literal["ok", "BLOCKED_DEPENDENCY", "degraded"]


def _item(
    name: DependencyName,
    *,
    status: CheckStatus,
    detail: str,
    path_or_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "detail": detail,
        "path_or_url": path_or_url,
    }
    if extra:
        payload["extra"] = extra
    return payload


def check_ffmpeg(settings: Settings | None = None) -> dict[str, Any]:
    if settings is not None and settings.ffmpeg_exe:
        os.environ.setdefault("FFMPEG_EXE", str(settings.ffmpeg_exe))
    ffmpeg = resolve_ffmpeg()
    ffprobe = resolve_ffprobe(ffmpeg)
    if not ffmpeg:
        return _item("ffmpeg", status="BLOCKED_DEPENDENCY", detail="ffmpeg executable not found (FFMPEG_EXE / ATNOS_FFMPEG / PATH)")
    if not ffprobe:
        return _item(
            "ffmpeg",
            status="BLOCKED_DEPENDENCY",
            detail="ffprobe executable not found (FFPROBE_EXE / sibling of ffmpeg)",
            path_or_url=ffmpeg,
        )
    try:
        proc = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return _item(
                "ffmpeg",
                status="BLOCKED_DEPENDENCY",
                detail=f"ffmpeg -version failed rc={proc.returncode}",
                path_or_url=ffmpeg,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _item("ffmpeg", status="BLOCKED_DEPENDENCY", detail=str(exc), path_or_url=ffmpeg)
    return _item("ffmpeg", status="ok", detail="ffmpeg+ffprobe available", path_or_url=ffmpeg, extra={"ffprobe": ffprobe})


def check_remotion(settings: Settings | None = None) -> dict[str, Any]:
    if settings is not None:
        if settings.remotion_browser_executable:
            os.environ["ATNOS_REMOTION_BROWSER"] = str(settings.remotion_browser_executable)
        if settings.atnos_node_exe:
            os.environ["ATNOS_NODE"] = str(settings.atnos_node_exe)
        if settings.ffmpeg_exe:
            os.environ.setdefault("ATNOS_FFMPEG", str(settings.ffmpeg_exe))
            os.environ.setdefault("FFMPEG_EXE", str(settings.ffmpeg_exe))
    package_root = None
    if settings is not None and settings.remotion_renderer_root:
        package_root = Path(settings.remotion_renderer_root)
    try:
        runtime = probe_remotion_runtime(package_root)
    except Exception as exc:  # noqa: BLE001
        return _item("remotion", status="BLOCKED_DEPENDENCY", detail=f"probe failed: {exc}")
    browser = os.environ.get("ATNOS_REMOTION_BROWSER") or (
        str(settings.remotion_browser_executable) if settings and settings.remotion_browser_executable else None
    )
    ready = bool(runtime.get("ready") or runtime.get("browser_compatibility_ready"))
    if not ready:
        return _item(
            "remotion",
            status="BLOCKED_DEPENDENCY",
            detail=str(runtime.get("reason") or runtime.get("status") or "remotion runtime not ready"),
            path_or_url=browser,
            extra={"runtime": runtime},
        )
    return _item(
        "remotion",
        status="ok",
        detail=str(runtime.get("status") or "ready"),
        path_or_url=browser,
        extra={"runtime": {k: runtime.get(k) for k in ("ready", "browser_compatibility_ready", "status", "reason", "action")}},
    )


def check_comfyui(settings: Settings) -> dict[str, Any]:
    base = str(settings.comfyui_base_url or "").rstrip("/")
    if not base:
        return _item("comfyui", status="BLOCKED_DEPENDENCY", detail="COMFYUI_BASE_URL not configured")
    # Cold start on Windows/ROCm often exceeds the 2s production health timeout — probe with buffer
    timeout = max(float(getattr(settings, "comfyui_health_timeout_s", 2) or 2), 8.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/system_stats")
            if response.status_code >= 400:
                response = client.get(f"{base}/object_info")
            if response.status_code >= 400:
                return _item(
                    "comfyui",
                    status="BLOCKED_DEPENDENCY",
                    detail=f"ComfyUI HTTP {response.status_code}",
                    path_or_url=base,
                )
    except (httpx.HTTPError, OSError) as exc:
        return _item("comfyui", status="BLOCKED_DEPENDENCY", detail=str(exc), path_or_url=base)
    return _item("comfyui", status="ok", detail="ComfyUI reachable", path_or_url=base)


def check_tts(settings: Settings) -> dict[str, Any]:
    """F5 TTS health — respects F5_TTS_TRANSPORT=cli|gradio. Never reports OfflineTone as healthy."""
    transport = str(getattr(settings, "f5_tts_transport", "gradio") or "gradio").strip().lower()
    if transport == "cli":
        try:
            from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService

            status = F5TTSRuntimeService(settings).status()
            payload = status.to_dict() if hasattr(status, "to_dict") else asdict(status)
            code = str(payload.get("diagnosis_code") or "")
            ready = bool(payload.get("transport_ready")) or code in {"cli_ready", "ready"}
            runtime_py = str(payload.get("runtime_python") or getattr(settings, "f5_tts_runtime_python", "") or "")
            if ready:
                return _item(
                    "tts",
                    status="ok",
                    detail=f"F5 TTS CLI ready ({code or 'cli_ready'})",
                    path_or_url=runtime_py or "cli",
                    extra={
                        "transport": "cli",
                        "diagnosis_code": code,
                        "diagnosis_message": payload.get("diagnosis_message"),
                    },
                )
            return _item(
                "tts",
                status="BLOCKED_DEPENDENCY",
                detail=str(payload.get("diagnosis_message") or code or "F5 CLI not ready"),
                path_or_url=runtime_py or "cli",
                extra={"transport": "cli", "diagnosis_code": code, "blocking_reasons": list(payload.get("blocking_reasons") or ())},
            )
        except Exception as exc:  # noqa: BLE001
            return _item("tts", status="BLOCKED_DEPENDENCY", detail=f"F5 CLI probe failed: {exc}", path_or_url="cli")

    endpoint = str(settings.f5_tts_base_url or "").strip()
    if not endpoint:
        return _item("tts", status="BLOCKED_DEPENDENCY", detail="F5_TTS_BASE_URL not configured")
    # Gradio health: prefer /config (Gradio 6); /api/predict is legacy label on base URL
    base = endpoint
    if base.endswith("/api/predict"):
        base = base[: -len("/api/predict")] or base
    probe_urls = [f"{base.rstrip('/')}/config", base.rstrip("/") or endpoint, endpoint]
    last_error = "unreachable"
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True) as client:
            for url in probe_urls:
                try:
                    response = client.get(url)
                    if response.status_code < 500:
                        return _item(
                            "tts",
                            status="ok",
                            detail=f"F5 TTS Gradio responded HTTP {response.status_code}",
                            path_or_url=endpoint,
                            extra={"transport": "gradio", "probed": url},
                        )
                    last_error = f"HTTP {response.status_code} from {url}"
                except (httpx.HTTPError, OSError) as exc:
                    last_error = str(exc)
    except (httpx.HTTPError, OSError) as exc:
        last_error = str(exc)
    return _item("tts", status="BLOCKED_DEPENDENCY", detail=last_error, path_or_url=endpoint)


def check_whisper(settings: Settings) -> dict[str, Any]:
    exe = str(settings.whisper_cpp_exe or "").strip() or None
    model = settings.whisper_model_path
    which = shutil.which("whisper-cli") or shutil.which("whisper")
    resolved_exe = exe if exe and Path(exe).is_file() else which
    if not resolved_exe:
        return _item(
            "whisper",
            status="BLOCKED_DEPENDENCY",
            detail="WHISPER_CPP_EXE not configured or missing",
        )
    if model is not None and not Path(model).is_file():
        return _item(
            "whisper",
            status="BLOCKED_DEPENDENCY",
            detail=f"WHISPER_MODEL_PATH missing: {model}",
            path_or_url=resolved_exe,
        )
    if model is None:
        return _item(
            "whisper",
            status="degraded",
            detail="whisper binary present but WHISPER_MODEL_PATH unset",
            path_or_url=resolved_exe,
        )
    return _item(
        "whisper",
        status="ok",
        detail="whisper-cpp + model configured",
        path_or_url=resolved_exe,
        extra={"model": str(model)},
    )


def run_wealth_blueprint_video_engine_health(
    settings: Settings,
    *,
    require_comfy: bool | None = None,
    require_whisper: bool | None = None,
) -> dict[str, Any]:
    """Full dependency matrix for the Wealth Blueprint Video Engine."""
    # Health matrix: Comfy is reported always, but only fail-closed when explicitly required.
    # Asset stage still enforces WEALTH_BLUEPRINT_REQUIRE_COMFY_FOR_MISSING_ASSETS separately.
    require_comfy = False if require_comfy is None else require_comfy
    require_whisper = (
        settings.wealth_blueprint_require_whisper_alignment if require_whisper is None else require_whisper
    )

    checks = [
        check_ffmpeg(settings),
        check_remotion(settings),
        check_tts(settings),
        check_whisper(settings),
        check_comfyui(settings),
    ]

    blocked = [c for c in checks if c["status"] == "BLOCKED_DEPENDENCY"]
    # Critical always: ffmpeg, remotion, tts
    critical_names = {"ffmpeg", "remotion", "tts"}
    if require_whisper:
        critical_names.add("whisper")
    if require_comfy:
        critical_names.add("comfyui")

    critical_blocked = [c for c in blocked if c["name"] in critical_names]
    overall = "BLOCKED_DEPENDENCY" if critical_blocked else ("degraded" if blocked else "ok")

    return {
        "engine": "wealth_blueprint_video_engine",
        "status": overall,
        "blocked_dependencies": [c["name"] for c in critical_blocked],
        "checks": checks,
        "require_comfy": require_comfy,
        "require_whisper": require_whisper,
        "config_sources": {
            "ffmpeg_exe": settings.ffmpeg_exe,
            "atnos_tools_dir": str(settings.atnos_tools_dir) if settings.atnos_tools_dir else None,
            "atnos_node_exe": str(settings.atnos_node_exe) if settings.atnos_node_exe else None,
            "remotion_browser_executable": str(settings.remotion_browser_executable)
            if settings.remotion_browser_executable
            else os.environ.get("ATNOS_REMOTION_BROWSER"),
            "comfyui_base_url": settings.comfyui_base_url,
            "f5_tts_base_url": settings.f5_tts_base_url,
            "whisper_cpp_exe": settings.whisper_cpp_exe,
            "whisper_model_path": str(settings.whisper_model_path) if settings.whisper_model_path else None,
            "channel_references_dir": str(settings.wealth_blueprint_channel_references_dir)
            if settings.wealth_blueprint_channel_references_dir
            else None,
        },
    }
