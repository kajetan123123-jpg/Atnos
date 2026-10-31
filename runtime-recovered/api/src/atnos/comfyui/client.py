from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


class ComfyUIError(RuntimeError):
    """Błąd komunikacji lub wykonania workflow w ComfyUI."""


class ComfyUITimeoutError(ComfyUIError):
    """ComfyUI nie zwrócił wyniku w wymaganym czasie."""


class ComfyUIClient:
    """Synchronous ComfyUI API client for local production rendering."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188, *, base_url: str | None = None) -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            self.base_url = f"http://{host}:{int(port)}"
        self.client_id = str(uuid.uuid4())

    def is_alive(self) -> bool:
        """Return True when ComfyUI responds to /system_stats."""
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/system_stats")
            return response.status_code == 200
        except Exception as exc:
            logger.debug("ComfyUI health check failed: %s", exc)
            return False

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        """Queue a ComfyUI API prompt and return prompt_id."""
        payload = {"prompt": workflow, "client_id": self.client_id}
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(f"{self.base_url}/prompt", json=payload)
                response.raise_for_status()
                prompt_id = str(response.json().get("prompt_id") or "").strip()
                if not prompt_id:
                    raise ComfyUIError("ComfyUI /prompt did not return prompt_id.")
                return prompt_id
            except Exception as exc:
                last_error = exc
                logger.warning("ComfyUI queue prompt failed on attempt %s/3: %s", attempt, exc)
                if attempt < 3:
                    time.sleep(2 * attempt)
        raise ComfyUIError(f"Could not queue ComfyUI prompt: {last_error}") from last_error

    def wait_for_result(self, prompt_id: str, timeout_seconds: int = 300) -> dict[str, Any]:
        """Poll /history/{prompt_id} until outputs are available."""
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        while time.monotonic() < deadline:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                history = response.json()
            except Exception as exc:
                logger.warning("ComfyUI history polling failed for %s: %s", prompt_id, exc)
                time.sleep(2)
                continue

            if not isinstance(history, dict) or not history:
                time.sleep(2)
                continue
            item = history.get(prompt_id)
            if not isinstance(item, dict):
                time.sleep(2)
                continue
            status = item.get("status") if isinstance(item.get("status"), dict) else {}
            status_text = str(status.get("status_str") or status.get("status") or "").casefold()
            if status_text in {"error", "failed"}:
                raise ComfyUIError(f"ComfyUI prompt {prompt_id} failed: {status}")
            outputs = item.get("outputs")
            if isinstance(outputs, dict) and outputs:
                return item
            time.sleep(2)
        raise ComfyUITimeoutError(f"ComfyUI prompt {prompt_id} timed out after {timeout_seconds}s.")

    def get_image(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        """Download an output file from ComfyUI /view."""
        with httpx.Client(timeout=60.0) as client:
            response = client.get(
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            )
        response.raise_for_status()
        return response.content

    def download_outputs(self, prompt_id: str, output_dir: Path) -> list[Path]:
        """Download every image/video/file output from a completed prompt."""
        output_dir.mkdir(parents=True, exist_ok=True)
        result = self.wait_for_result(prompt_id)
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        paths: list[Path] = []
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for key in ("images", "videos", "gifs", "animated", "files"):
                items = node_output.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    filename = str(item.get("filename") or "").strip()
                    if not filename:
                        continue
                    data = self.get_image(
                        filename=filename,
                        subfolder=str(item.get("subfolder") or ""),
                        folder_type=str(item.get("type") or "output"),
                    )
                    target = output_dir / Path(filename).name
                    target.write_bytes(data)
                    paths.append(target)
        return paths


def ensure_comfyui_running(config: Any) -> bool:
    """Ensure ComfyUI is reachable, launching local main.py when configured."""
    base_url = _config_value(config, "comfyui_base_url") or "http://127.0.0.1:8188"
    parsed = urlparse(str(base_url))
    client = ComfyUIClient(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 8188,
        base_url=str(base_url),
    )
    if client.is_alive():
        return True

    comfyui_path = _resolve_comfyui_path(config)
    if comfyui_path is None:
        logger.warning("comfyui_path is not configured; skipping ComfyUI auto-launch.")
        return False
    main_py = _resolve_comfyui_main(comfyui_path)
    if main_py is None:
        logger.warning("ComfyUI main.py was not found under %s.", comfyui_path)
        return False

    output_dir = main_py.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [
                sys.executable,
                str(main_py),
                "--listen",
                "127.0.0.1",
                "--port",
                str(parsed.port or 8188),
                "--output-directory",
                str(output_dir),
            ],
            cwd=str(main_py.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError as exc:
        logger.error("Could not launch ComfyUI: %s", exc)
        return False

    for _ in range(15):
        time.sleep(3)
        if client.is_alive():
            return True
    logger.error("ComfyUI did not start within 45s.")
    return False


def load_local_services_config(settings: Any | None = None) -> dict[str, Any]:
    """Load optional config/local_services.json for local service paths."""
    candidates: list[Path] = []
    if settings is not None:
        data_dir = _config_value(settings, "data_dir")
        app_root = _config_value(settings, "app_root")
        if data_dir:
            candidates.append(Path(data_dir) / "config" / "local_services.json")
        if app_root:
            candidates.append(Path(app_root) / "config" / "local_services.json")
    candidates.extend([Path("config/local_services.json"), Path("local_services.json")])
    for candidate in candidates:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read local services config %s: %s", candidate, exc)
    return {}


def _resolve_comfyui_path(config: Any) -> Path | None:
    raw = (
        _config_value(config, "comfyui_path")
        or _config_value(config, "comfyui_install_dir")
        or load_local_services_config(config).get("comfyui_path")
    )
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    return path if path.exists() else None


def _resolve_comfyui_main(path: Path) -> Path | None:
    candidates = [path / "main.py", path / "ComfyUI" / "main.py"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _config_value(config: Any, key: str) -> Any:
    if isinstance(config, dict):
        return config.get(key)
    return getattr(config, key, None)
