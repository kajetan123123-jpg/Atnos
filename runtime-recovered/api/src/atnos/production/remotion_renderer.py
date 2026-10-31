from __future__ import annotations

import json
import hashlib
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil

from atnos.production.media_tools import resolve_ffprobe
from atnos.production.project_manifest import (
    ProjectManifest,
    SceneType,
    file_sha256,
    load_project_manifest,
)


REMOTION_RENDERER_VERSION = "wealth-blueprint-reference-v0.47"
REMOTION_WIDTH = 1080
REMOTION_HEIGHT = 1920
REMOTION_FPS = 30.0
_RENDERER_SOURCE_FILES = (
    "package.json",
    "pnpm-lock.yaml",
    "scripts/render.ts",
    "scripts/render-browser.ts",
    "src/browser-player.tsx",
    "src/index.ts",
    "src/Root.tsx",
    "src/contracts.ts",
    "src/design.tsx",
    "src/scenes.tsx",
    "src/WealthBlueprintShort.tsx",
    "src/VisualStoryScene.tsx",
    "src/ResearchCheckScene.tsx",
    "src/ResearchIntroScene.tsx",
    "src/ProfessionalResearchScene.tsx",
    "src/EditorialMotionScene.tsx",
    "src/EditorialPlanningScene.tsx",
    "src/editorial-motion.ts",
    "src/StyleComparisonScene.tsx",
    "src/ReserveReadinessScene.tsx",
    "src/ReserveObjects.tsx",
    "src/WBEditorialWorld.tsx",
    "src/WBPlanningDeskScene.tsx",
    "src/WBPlanningDeskHands.tsx",
    "src/WBPaintedMaterials.tsx",
    "src/typography.tsx",
)


class RemotionRendererError(RuntimeError):
    pass


def _stop_render_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop the owned Node process and its Chromium/FFmpeg descendants on timeout."""
    children: list[psutil.Process] = []
    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    # Capture descendants while their parent still exists, then stop the parent
    # before it can start more work. The Popen handle prevents PID-reuse mistakes.
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    for child in children:
        try:
            child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(children, timeout=3)
    for child in alive:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(alive, timeout=2)


_RENDER_PROGRESS_MARKER = re.compile(r"^ATNOS_REMOTION(?:_BROWSER)?_(?:STAGE|PROGRESS)=")


def _run_render_command(
    command: list[str], *, cwd: Path, env: dict[str, str], inactivity_timeout_s: int,
    progress_log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Remotion until it exits; stop only after a long interval with no structured progress."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    closed: set[str] = set()
    tails = {"stdout": deque(maxlen=240), "stderr": deque(maxlen=240)}
    last_progress_at = time.monotonic()
    last_progress_marker = "none"

    def pump(name: str, stream) -> None:
        try:
            try:
                for line in iter(stream.readline, ""):
                    events.put((name, line))
            except (OSError, ValueError):
                pass
        finally:
            events.put((name, None))

    readers = [
        threading.Thread(target=pump, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=pump, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    log_path = progress_log_path.resolve() if progress_log_path else None
    log = None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8", newline="")

    def consume(name: str, line: str | None) -> None:
        nonlocal last_progress_at, last_progress_marker
        if line is None:
            closed.add(name)
            return
        tails[name].append(line)
        if log:
            log.write(f"[{name}] {line}")
            if _RENDER_PROGRESS_MARKER.match(line.strip()):
                log.flush()
        if _RENDER_PROGRESS_MARKER.match(line.strip()):
            last_progress_at = time.monotonic()
            last_progress_marker = line.strip()[-240:]

    try:
        while process.poll() is None or len(closed) < 2:
            try:
                name, line = events.get(timeout=0.25)
                consume(name, line)
            except queue.Empty:
                pass
            if process.poll() is None and time.monotonic() - last_progress_at >= inactivity_timeout_s:
                _stop_render_process_tree(process)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
                for reader in readers:
                    reader.join(timeout=1)
                raise subprocess.TimeoutExpired(
                    command,
                    inactivity_timeout_s,
                    output="".join(tails["stdout"]),
                    stderr=("".join(tails["stderr"])
                            + f"\nATNOS_RENDER_INACTIVITY_TIMEOUT={inactivity_timeout_s}s"
                            + f"; last_progress={last_progress_marker}"),
                )
        return subprocess.CompletedProcess(
            command, process.returncode, "".join(tails["stdout"]), "".join(tails["stderr"])
        )
    finally:
        if log:
            log.close()


def _resolve_signed_browser_executable() -> Path | None:
    """Resolve a signed Chromium/Chrome/Edge binary for the Windows Remotion path."""
    candidates: list[Path] = []
    env = str(os.environ.get("ATNOS_REMOTION_BROWSER") or os.environ.get("REMOTION_BROWSER_EXECUTABLE") or "").strip()
    if env:
        candidates.append(Path(env))
    try:
        from atnos.core.config import get_settings

        settings = get_settings()
        if settings.remotion_browser_executable is not None:
            candidates.append(Path(settings.remotion_browser_executable))
        tools = settings.atnos_tools_dir
        if tools is not None:
            root = Path(tools) / "playwright-browsers"
            if root.is_dir():
                candidates.extend(sorted(root.glob("chromium-*/chrome-win/chrome.exe"), reverse=True))
                candidates.extend(sorted(root.glob("chromium-*/chrome-win/Chromium.exe"), reverse=True))
    except Exception:
        pass
    # Last-resort known local Tools layout (E: policy)
    candidates.append(Path(r"E:\Atnos\Tools\playwright-browsers\chromium-1187\chrome-win\chrome.exe"))
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def probe_remotion_runtime(package_root: Path | None = None) -> dict[str, Any]:
    """Select a renderer without probing blocked unsigned binaries on Windows."""
    root = (package_root or _renderer_package_root()).expanduser().resolve()
    if not (root / "node_modules").is_dir():
        return {
            "status": "missing",
            "ready": False,
            "reason": "Remotion dependencies are not installed",
            "action": f"Run pnpm install in {root}",
        }
    try:
        node, tsx_cli = _resolve_node_and_tsx(root)
    except RemotionRendererError as exc:
        return {
            "status": "missing",
            "ready": False,
            "reason": str(exc),
            "action": "Install or configure the native Node.js runtime",
        }
    native_opt_in = os.getenv("ATNOS_USE_NATIVE_REMOTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if os.name == "nt" and not native_opt_in:
        return {
            "status": "browser_compatibility",
            "ready": False,
            "browser_compatibility_ready": True,
            "reason": "Windows uses the signed-browser renderer by default",
            "action": "none",
            "node": str(node),
            "tsx": str(tsx_cli),
            "native_probe_skipped": True,
        }
    compositor = _resolve_remotion_compositor(root)
    if compositor is None:
        return {
            "status": "missing",
            "ready": False,
            "reason": "Remotion compositor executable is missing",
            "action": f"Reinstall Remotion dependencies in {root}",
            "node": str(node),
            "tsx": str(tsx_cli),
        }
    try:
        probe = subprocess.run(
            [str(compositor), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 4551:
            return {
                "status": "blocked",
                "ready": False,
                "browser_compatibility_ready": True,
                "reason": "Windows Application Control blocked the unsigned Remotion compositor",
                "action": (
                    "Use the signed-browser compatibility renderer or allow this exact binary "
                    "through an administrator-managed application-control policy"
                ),
                "winerror": 4551,
                "compositor": str(compositor),
                "node": str(node),
                "tsx": str(tsx_cli),
            }
        return {
            "status": "blocked",
            "ready": False,
            "browser_compatibility_ready": True,
            "reason": f"Remotion compositor could not start: {exc}",
            "action": "Inspect the Windows execution policy and the compositor binary",
            "compositor": str(compositor),
        }
    except (subprocess.SubprocessError, TimeoutError) as exc:
        return {
            "status": "degraded",
            "ready": False,
            "browser_compatibility_ready": True,
            "reason": f"Remotion compositor preflight failed: {exc}",
            "action": "Use the signed-browser compatibility renderer",
            "compositor": str(compositor),
        }
    if probe.returncode not in {0, 1, 2}:
        diagnostic = (probe.stderr or probe.stdout or "unknown compositor failure")[-1000:]
        return {
            "status": "degraded",
            "ready": False,
            "browser_compatibility_ready": True,
            "reason": f"Remotion compositor preflight exited {probe.returncode}: {diagnostic}",
            "action": "Use the signed-browser compatibility renderer",
            "compositor": str(compositor),
        }
    return {
        "status": "ready",
        "ready": True,
        "browser_compatibility_ready": False,
        "reason": "Remotion compositor is executable",
        "action": "none",
        "compositor": str(compositor),
        "node": str(node),
        "tsx": str(tsx_cli),
    }


def render_project_manifest(
    manifest_path: Path,
    output_path: Path,
    *,
    inactivity_timeout_s: int = 900,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    manifest = load_project_manifest(manifest_path)
    _validate_renderer_contract(manifest)
    package_root = _renderer_package_root()
    if not (package_root / "node_modules").is_dir():
        raise RemotionRendererError(
            f"Remotion dependencies are not installed: {package_root}. Run pnpm install in that directory."
        )
    runtime = probe_remotion_runtime(package_root)
    use_browser_compatibility = runtime.get("browser_compatibility_ready") is True
    if not runtime["ready"] and not use_browser_compatibility:
        raise RemotionRendererError(
            f"Remotion runtime {runtime['status']}: {runtime['reason']}. Action: {runtime['action']}"
        )
    node, tsx_cli = _resolve_node_and_tsx(package_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = output_path.with_name(
        f".{output_path.stem}.{os.getpid()}.{uuid4().hex}.rendering{output_path.suffix}"
    )
    staging_path.unlink(missing_ok=True)
    # Both paths use the official Remotion frame renderer. On Windows, the
    # signed-browser lane supplies a trusted browser and external FFmpeg; it
    # must not fall back to a manual page.screenshot() loop per frame.
    command = [
        str(node),
        str(tsx_cli),
        "scripts/render.ts",
        "--manifest",
        str(manifest_path),
        "--output",
        str(staging_path),
    ]
    env = os.environ.copy()
    env["PATH"] = f"{node.parent}{os.pathsep}{env.get('PATH', '')}"
    fonts_dir = _atnos_fonts_dir()
    if fonts_dir.is_dir():
        env["ATNOS_FONTS_DIR"] = str(fonts_dir)
    render_temp_dir = output_path.parent / ".remotion-temp"
    render_temp_dir.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(render_temp_dir)
    env["TMP"] = str(render_temp_dir)
    env["TMPDIR"] = str(render_temp_dir)
    if use_browser_compatibility:
        from atnos.production.media_tools import resolve_ffmpeg

        ffmpeg = resolve_ffmpeg()
        if not ffmpeg or not Path(ffmpeg).is_file():
            raise RemotionRendererError(
                "The signed-browser compatibility renderer requires a real FFmpeg executable "
                "(set FFMPEG_EXE / ATNOS_FFMPEG / ATNOS_TOOLS_DIR)"
            )
        env["ATNOS_FFMPEG"] = str(Path(ffmpeg).resolve())
        # Prefer configured signed browser; do not rely on ambient process env alone.
        browser = _resolve_signed_browser_executable()
        if browser is None:
            raise RemotionRendererError(
                "Signed Chrome/Edge executable missing "
                "(set ATNOS_REMOTION_BROWSER or install Playwright Chromium under ATNOS_TOOLS_DIR)"
            )
        env["ATNOS_REMOTION_BROWSER"] = str(browser)
    try:
        progress_log = output_path.with_name(
            f".{output_path.stem}.{os.getpid()}.{uuid4().hex}.render-progress.log"
        )
        process = _run_render_command(
            command,
            cwd=package_root,
            env=env,
            inactivity_timeout_s=inactivity_timeout_s,
            progress_log_path=progress_log if output_path.drive.casefold() == "e:" else None,
        )
    except subprocess.TimeoutExpired as exc:
        staging_path.unlink(missing_ok=True)
        diagnostic = str(exc.stderr or exc.output or "").strip()[-3000:]
        raise RemotionRendererError(
            f"Remotion made no structured progress for {inactivity_timeout_s}s; "
            f"the total render time is not capped. Last output: {diagnostic}"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        staging_path.unlink(missing_ok=True)
        raise RemotionRendererError(f"Remotion process could not run: {exc}") from exc
    if process.returncode != 0:
        staging_path.unlink(missing_ok=True)
        diagnostic = (process.stderr or process.stdout or "unknown Remotion failure")[-4000:]
        raise RemotionRendererError(f"Remotion render failed: {diagnostic}")
    try:
        marker = _parse_result_marker(process.stdout)
        if not staging_path.is_file() or staging_path.stat().st_size <= 4096:
            raise RemotionRendererError("Remotion returned success but the MP4 is missing or too small")
        _validate_result_marker(marker, staging_path, manifest)
        probe = probe_silent_render(staging_path)
        if not probe["ok"]:
            raise RemotionRendererError(
                f"Remotion output failed ffprobe verification: {probe['reason']}"
            )
        expected_duration = _expected_duration_in_frames(manifest) / REMOTION_FPS
        if abs(_float(probe.get("duration_s")) - expected_duration) > (1.5 / REMOTION_FPS):
            raise RemotionRendererError(
                "Remotion output duration does not match the manifest timeline"
            )
        _publish_render_atomically(staging_path, output_path)
        marker = {**marker, "staging_output": marker.get("output"), "output": str(output_path)}
        if progress_log.exists():
            marker["progress_log"] = str(progress_log)
    except (OSError, RemotionRendererError):
        staging_path.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "renderer_version": REMOTION_RENDERER_VERSION,
        "manifest_path": str(manifest_path),
        "manifest_content_hash": manifest.content_hash,
        "output_path": str(output_path),
        "output_sha256": file_sha256(output_path),
        "output_bytes": output_path.stat().st_size,
        "probe": probe,
        "renderer": marker,
    }


def _publish_render_atomically(staging_path: Path, output_path: Path) -> None:
    """Publish a verified MP4 without exposing a partial canonical artifact."""
    last_error: PermissionError | None = None
    for attempt in range(20):
        try:
            os.replace(staging_path, output_path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1 * min(attempt + 1, 5))
    raise RemotionRendererError(
        f"Verified Remotion output could not replace the busy target after retries: {output_path}: {last_error}"
    )


def probe_silent_render(path: Path, *, require_silent: bool = True) -> dict[str, Any]:
    ffprobe = resolve_ffprobe(shutil.which("ffmpeg"))
    if not ffprobe:
        return {"ok": False, "reason": "ffprobe unavailable"}
    try:
        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": str(exc)}
    if process.returncode != 0:
        return {"ok": False, "reason": (process.stderr or f"ffprobe exit {process.returncode}")[-2000:]}
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"invalid ffprobe JSON: {exc}"}
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    width = _int(video.get("width"))
    height = _int(video.get("height"))
    fps = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    codec = str(video.get("codec_name") or "")
    pixel_format = str(video.get("pix_fmt") or "")
    duration = _float((payload.get("format") or {}).get("duration"))
    checks = {
        "has_video": bool(video),
        "silent": audio is None if require_silent else True,
        "resolution": width == REMOTION_WIDTH and height == REMOTION_HEIGHT,
        "fps": abs(fps - REMOTION_FPS) <= 0.01,
        "codec": codec == "h264",
        "pixel_format": pixel_format == "yuv420p",
        "duration": 0 < duration <= 60.1,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "ok": not failed,
        "reason": "" if not failed else f"failed checks: {', '.join(failed)}",
        "checks": checks,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "codec": codec,
        "pixel_format": pixel_format,
        "duration_s": round(duration, 3),
        "has_audio": audio is not None,
    }


def _validate_renderer_contract(manifest: ProjectManifest) -> None:
    if manifest.channel != "wealth_blueprint":
        raise RemotionRendererError(
            f"Renderer V1 supports only wealth_blueprint, received {manifest.channel!r}"
        )
    if manifest.renderer_version != REMOTION_RENDERER_VERSION:
        raise RemotionRendererError(
            f"Renderer version mismatch: expected {REMOTION_RENDERER_VERSION}, "
            f"received {manifest.renderer_version!r}"
        )
    if not manifest.scenes:
        raise RemotionRendererError("Project manifest contains no scenes")
    for scene in manifest.scenes:
        if scene.visual_story:
            from atnos.production.channels.wealth_blueprint.visual_story import STATES
            roles = {a.provenance.generation_parameters.get('layer_role') for a in scene.assets}
            if roles != {'background','income','savings','goal','costs'} or len(scene.assets)!=5:
                raise RemotionRendererError('WB story requires exactly five distinct illustration layers')
            story=scene.visual_story
            if story.get('sequence')!='raise-pilot-v1' or story.get('state') not in STATES or story.get('timing_source')!='final_voice_word_alignment':
                raise RemotionRendererError('Unsupported or unaligned visual story')
            for asset in scene.assets:
                if asset.qa.status!='passed' or file_sha256(Path(asset.path))!=asset.provenance.artifact_hash:
                    raise RemotionRendererError('WB story layer failed integrity checks')
                params=asset.provenance.generation_parameters
                if not params.get('user_approved') and manifest.review_mode!='candidate_preview':
                    raise RemotionRendererError('WB story candidate cannot enter final production')
            if manifest.review_mode=='candidate_preview' and manifest.scenes[-1].timing.end_sec>30:
                raise RemotionRendererError('Candidate preview exceeds thirty seconds')
            continue
        if scene.scene_type is SceneType.NUMBER and not scene.number_value:
            raise RemotionRendererError(
                f"NumberScene {scene.scene_id} has no source-backed number_value"
            )
        if scene.scene_type is SceneType.CHART:
            has_comparison = bool(scene.comparison_left or scene.comparison_right)
            if has_comparison and (not scene.comparison_left or not scene.comparison_right):
                raise RemotionRendererError(
                    f"ChartScene {scene.scene_id} comparison needs two explicit labels"
                )
            if len(scene.chart_values) < 2:
                raise RemotionRendererError(
                    f"ChartScene {scene.scene_id} needs at least two source-backed chart_values"
                )
        if scene.scene_type is SceneType.IMAGE:
            image_assets = [
                asset
                for asset in scene.assets
                if asset.role == "selected_image"
                and asset.exists
                and Path(asset.path).is_file()
                and asset.qa.status == "passed"
            ]
            if len(image_assets) != 1:
                raise RemotionRendererError(
                    f"ImageScene {scene.scene_id} needs exactly one QA-passed selected image"
                )
            selected = image_assets[0]
            expected_hash = selected.provenance.artifact_hash
            actual_hash = file_sha256(Path(selected.path))
            if not expected_hash:
                raise RemotionRendererError(
                    f"ImageScene {scene.scene_id} selected image is missing provenance artifact_hash"
                )
            if actual_hash != expected_hash:
                raise RemotionRendererError(
                    f"ImageScene {scene.scene_id} selected image hash mismatch"
                )


def _parse_result_marker(stdout: str) -> dict[str, Any]:
    prefix = "ATNOS_REMOTION_RESULT="
    lines = [line[len(prefix) :] for line in stdout.splitlines() if line.startswith(prefix)]
    if not lines:
        raise RemotionRendererError("Remotion process did not emit a result marker")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RemotionRendererError(f"Remotion result marker is invalid JSON: {exc}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RemotionRendererError("Remotion result marker does not confirm success")
    required = {
        "renderer_version",
        "output",
        "bytes",
        "fps",
        "width",
        "height",
        "duration_in_frames",
        "manifest_content_hash",
        "output_sha256",
        "renderer_source_hashes",
        "renderer_source_fingerprint",
        "font_assets",
    }
    missing = sorted(required.difference(result))
    if missing:
        raise RemotionRendererError(
            f"Remotion result marker is missing required fields: {', '.join(missing)}"
        )
    return result


def _validate_result_marker(
    marker: dict[str, Any],
    output_path: Path,
    manifest: ProjectManifest,
) -> None:
    """Zweryfikuj, ze marker opisuje dokladnie zapisany plik i aktywny manifest."""
    actual_hash = file_sha256(output_path)
    expected_frames = _expected_duration_in_frames(manifest)
    expected_source_hashes = _renderer_source_hashes()
    expected_source_fingerprint = hashlib.sha256(
        json.dumps(
            expected_source_hashes,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    expected_fonts = _renderer_font_assets()
    checks = {
        "renderer_version": marker.get("renderer_version") == REMOTION_RENDERER_VERSION,
        "output": Path(str(marker.get("output") or "")).expanduser().resolve() == output_path.resolve(),
        "bytes": _int(marker.get("bytes")) == output_path.stat().st_size,
        "fps": abs(_float(marker.get("fps")) - REMOTION_FPS) <= 0.01,
        "width": _int(marker.get("width")) == REMOTION_WIDTH,
        "height": _int(marker.get("height")) == REMOTION_HEIGHT,
        "duration_in_frames": _int(marker.get("duration_in_frames")) == expected_frames,
        "manifest_content_hash": marker.get("manifest_content_hash") == manifest.content_hash,
        "output_sha256": bool(actual_hash) and marker.get("output_sha256") == actual_hash,
        "renderer_source_hashes": marker.get("renderer_source_hashes") == expected_source_hashes,
        "renderer_source_fingerprint": marker.get("renderer_source_fingerprint")
        == expected_source_fingerprint,
        "font_assets": marker.get("font_assets") == expected_fonts,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RemotionRendererError(
            f"Remotion result marker failed integrity checks: {', '.join(failed)}"
        )


def _expected_duration_in_frames(manifest: ProjectManifest) -> int:
    return max(1, math.ceil(max(scene.timing.end_sec for scene in manifest.scenes) * REMOTION_FPS))


def _renderer_package_root() -> Path:
    return Path(__file__).resolve().parents[4] / "remotion-renderer"


def _renderer_source_hashes() -> dict[str, str]:
    root = _renderer_package_root()
    result: dict[str, str] = {}
    for relative in _RENDERER_SOURCE_FILES:
        digest = file_sha256(root / relative)
        if digest is None:
            raise RemotionRendererError(f"Renderer source is missing: {root / relative}")
        result[relative] = digest
    return dict(sorted(result.items()))


def _renderer_font_assets() -> dict[str, dict[str, str]]:
    roots = [_atnos_fonts_dir(), Path("C:/Windows/Fonts")]
    choices = {
        "regular": ("Montserrat-Bold.ttf", "montserrat-bold.ttf", "arialbd.ttf"),
        "extra_bold": (
            "Montserrat-ExtraBold.ttf",
            "montserrat-extrabold.ttf",
            "arialbd.ttf",
        ),
    }
    result: dict[str, dict[str, str]] = {}
    for role, names in choices.items():
        selected = next(
            (root / name for root in roots for name in names if (root / name).is_file()),
            None,
        )
        if selected is None:
            raise RemotionRendererError(f"Renderer font is missing for role: {role}")
        digest = file_sha256(selected)
        if digest is None:
            raise RemotionRendererError(f"Unable to hash renderer font: {selected}")
        result[role] = {"filename": selected.name, "sha256": digest}
    return result


def _atnos_fonts_dir() -> Path:
    # Use the same directory in the child renderer and in marker verification.
    # APPDATA can still point at the interactive Windows profile, while Atnos
    # stores its state (including fonts) in its dedicated E: profile.
    explicit = os.getenv("ATNOS_FONTS_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    from atnos.core.paths import atnos_state_dir

    return atnos_state_dir() / "fonts"


def _resolve_pnpm() -> Path:
    explicit = os.getenv("ATNOS_PNPM")
    if explicit and Path(explicit).is_file():
        return Path(explicit).resolve()
    located = shutil.which("pnpm") or shutil.which("pnpm.cmd")
    if located:
        return Path(located).resolve()
    home = Path.home()
    candidates = [
        home / "AppData" / "Local" / "pnpm" / "pnpm.cmd",
        home / "AppData" / "Roaming" / "npm" / "pnpm.cmd",
    ]
    candidates.extend(
        home.glob(".cache/codex-runtimes/*/dependencies/bin/fallback/pnpm.cmd")
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RemotionRendererError("pnpm executable not found")


def _resolve_node_and_tsx(package_root: Path) -> tuple[Path, Path]:
    """Resolve native Node + TSX and avoid Windows CMD stdin inheritance hangs."""
    tsx_cli = package_root / "node_modules" / "tsx" / "dist" / "cli.mjs"
    if not tsx_cli.is_file():
        raise RemotionRendererError(f"Remotion TSX runtime is missing: {tsx_cli}")

    candidates: list[Path] = []
    explicit = os.getenv("ATNOS_NODE")
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(_canonical_node_path())
    located = shutil.which("node") or shutil.which("node.exe")
    if located:
        candidates.append(Path(located))
    try:
        pnpm = _resolve_pnpm()
    except RemotionRendererError:
        pnpm = None
    if pnpm is not None and len(pnpm.parents) >= 3:
        candidates.append(pnpm.parents[2] / "node" / "bin" / "node.exe")
    candidates.extend(Path.home().glob(".cache/codex-runtimes/*/dependencies/node/bin/node.exe"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(), tsx_cli.resolve()
    raise RemotionRendererError("Node.js executable is unavailable for the Remotion renderer")


def _canonical_node_path() -> Path:
    tools = os.getenv("ATNOS_TOOLS_DIR")
    if not tools:
        from atnos.core.config import get_settings

        configured = get_settings().atnos_tools_dir
        tools = str(configured) if configured else None
    if tools:
        return Path(tools) / "NodeJS" / "node.exe"
    if os.name == "nt":
        from atnos.core.paths import atnos_repo_root

        return atnos_repo_root().parent / "Tools" / "NodeJS" / "node.exe"
    return Path("node.exe")


def _resolve_remotion_compositor(package_root: Path) -> Path | None:
    """Znajdz natywny compositor zainstalowany przez pnpm/npm."""
    candidates = sorted(
        package_root.glob("node_modules/.pnpm/**/@remotion/compositor-*/remotion.exe")
    )
    if not candidates:
        candidates = sorted(package_root.glob("node_modules/**/remotion.exe"))
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def _frame_rate(value: Any) -> float:
    text = str(value or "0/1")
    if "/" not in text:
        return _float(text)
    numerator, denominator = text.split("/", 1)
    divisor = _float(denominator)
    return _float(numerator) / divisor if divisor else 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
