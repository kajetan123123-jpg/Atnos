from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

from atnos.model_manager.registry import ModelSpec
from atnos.model_manager.verifier import verify_model_integrity

ProgressCallback = Callable[[str, float, str], None]


class ModelDownloadError(RuntimeError):
    pass


class DiskSpaceError(ModelDownloadError):
    pass


HF_HUB_PACKAGE = "huggingface-hub>=0.22,<1.0"


async def download_model(
    spec: ModelSpec,
    progress_callback: ProgressCallback | None = None,
    *,
    force: bool = False,
) -> Path:
    status = await verify_model_integrity(spec)
    if status["status"] == "ok" and not force:
        _emit(progress_callback, spec.id, 1.0, "model already available")
        return spec.local_path

    await asyncio.to_thread(_check_disk_space, spec)
    spec.local_path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_remove_stale_zero_byte_hf_locks, spec)

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            _emit(progress_callback, spec.id, 0.0, f"download start attempt {attempt}/3")
            downloaded = await _download_with_progress_monitor(spec, force, progress_callback)
            if downloaded.resolve() != spec.local_path.resolve():
                spec.local_path.parent.mkdir(parents=True, exist_ok=True)
                _replace_downloaded_file(downloaded, spec.local_path)
            _emit(progress_callback, spec.id, 0.95, "download complete; verifying")
            verified = await verify_model_integrity(spec)
            if verified["status"] != "ok":
                raise ModelDownloadError(f"Downloaded model failed verification: {verified['status']}")
            _emit(progress_callback, spec.id, 1.0, "download verified")
            return spec.local_path
        except Exception as exc:
            last_error = exc
            _emit(progress_callback, spec.id, min(0.95, attempt / 4), f"download failed: {exc}")
            if attempt < 3:
                await asyncio.sleep(10)

    raise ModelDownloadError(str(last_error) if last_error else "download failed")


async def download_multiple_models(
    specs: list[ModelSpec],
    progress_callback: ProgressCallback | None = None,
    *,
    sequential: bool = True,
) -> dict[str, Path | Exception]:
    if sequential:
        results: dict[str, Path | Exception] = {}
        for spec in specs:
            try:
                results[spec.id] = await download_model(spec, progress_callback)
            except Exception as exc:
                results[spec.id] = exc
        return results

    tasks = [download_model(spec, progress_callback) for spec in specs]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    return {spec.id: result for spec, result in zip(specs, raw)}


async def check_model_exists(spec: ModelSpec) -> bool:
    status = await verify_model_integrity(spec)
    return status["status"] == "ok"


async def get_download_progress_from_hf_cache(spec: ModelSpec) -> float:
    part_path = spec.local_path.parent / f".{_safe_cache_filename(spec.hf_filename)}.part"
    if not part_path.exists():
        incomplete_files = list((spec.local_path.parent / ".cache" / "huggingface" / "download").glob("*.incomplete"))
        if not incomplete_files:
            return 0.0
        downloaded = max((item.stat().st_size for item in incomplete_files if item.is_file()), default=0)
        expected_bytes = max(1, int(spec.size_gb * 1024**3))
        return min(downloaded / expected_bytes, 0.99)
    expected_bytes = max(1, int(spec.size_gb * 1024**3))
    return min(part_path.stat().st_size / expected_bytes, 0.99)


def _safe_cache_filename(value: str) -> str:
    return str(value or "model").replace("\\", "_").replace("/", "_")


def _replace_downloaded_file(source: Path, target: Path) -> None:
    """Atomically replace stale/corrupt target files after HF downloads.

    Interrupted model downloads often leave a wrong-size target file. On Windows,
    shutil.move to an existing destination can fail, so replace explicitly.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    try:
        source.replace(target)
    except OSError:
        shutil.move(str(source), str(target))


async def _download_with_progress_monitor(
    spec: ModelSpec,
    force: bool,
    progress_callback: ProgressCallback | None,
) -> Path:
    task = asyncio.create_task(asyncio.to_thread(_hf_download, spec, force))
    last_progress = -1.0
    while not task.done():
        await asyncio.sleep(5)
        if progress_callback is None:
            continue
        progress = await get_download_progress_from_hf_cache(spec)
        if progress <= 0:
            continue
        rounded = round(progress, 2)
        if rounded > last_progress:
            last_progress = rounded
            _emit(progress_callback, spec.id, min(progress, 0.94), "download in progress")
    return await task


def _hf_download(spec: ModelSpec, force: bool) -> Path:
    _ensure_hf_hub()
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ModelDownloadError("huggingface_hub is not installed.") from exc

    token = os.getenv("HF_TOKEN") or None
    path = hf_hub_download(
        repo_id=spec.hf_repo,
        filename=spec.hf_filename,
        local_dir=str(spec.local_path.parent),
        token=token,
        force_download=force,
        resume_download=True,
    )
    return Path(path)


def _ensure_hf_hub() -> None:
    if importlib.util.find_spec("huggingface_hub") is not None:
        return
    try:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                HF_HUB_PACKAGE,
                "--break-system-packages",
                "-q",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ModelDownloadError(f"Unable to install {HF_HUB_PACKAGE}.") from exc


def _check_disk_space(spec: ModelSpec) -> None:
    target_dir = _nearest_existing_parent(spec.local_path.parent)
    free_bytes = shutil.disk_usage(target_dir).free
    required_bytes = int(spec.size_gb * 1024**3 * 1.2)
    if free_bytes < required_bytes:
        free_gb = free_bytes / (1024**3)
        raise DiskSpaceError(
            f"Not enough free disk space for {spec.id}: need ~{spec.size_gb * 1.2:.1f}GB, have {free_gb:.1f}GB."
        )


def _remove_stale_zero_byte_hf_locks(spec: ModelSpec) -> None:
    download_dir = spec.local_path.parent / ".cache" / "huggingface" / "download"
    if not download_dir.exists():
        return
    for item in download_dir.iterdir():
        if not item.is_file() or item.stat().st_size != 0:
            continue
        if item.suffix not in {".lock", ".incomplete"}:
            continue
        try:
            item.unlink()
        except OSError:
            continue


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


def _emit(callback: ProgressCallback | None, model_id: str, progress: float, message: str) -> None:
    if callback is None:
        return
    callback(model_id, max(0.0, min(1.0, progress)), message)
