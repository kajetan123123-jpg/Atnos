"""Server-side runner for the installed @opencoredev/social-sdk Node package."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

BRIDGE_ROOT = Path(r"E:\Atnos\Tools\social-sdk-bridge")
BRIDGE_SCRIPT = BRIDGE_ROOT / "atnos_bridge.mjs"
NODE = Path(os.environ.get("ATNOS_NODE") or r"E:\Atnos\Tools\nodejs\node.exe")
PACKAGE_JSON = BRIDGE_ROOT / "node_modules" / "@opencoredev" / "social-sdk" / "package.json"


def is_loaded() -> bool:
    return BRIDGE_SCRIPT.is_file() and PACKAGE_JSON.is_file() and NODE.is_file()


def package_version() -> str | None:
    if not PACKAGE_JSON.is_file():
        return None
    try:
        return str(json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, json.JSONDecodeError):
        return None


def run_bridge(command: str, payload: dict[str, Any] | None = None, *, timeout_s: float = 45.0) -> dict[str, Any]:
    if not is_loaded():
        return {
            "ok": False,
            "status": "REQUIRES_SETUP",
            "detail": "Social SDK bridge not installed under E:\\Atnos\\Tools\\social-sdk-bridge.",
        }
    body = json.dumps(payload or {}, ensure_ascii=True)
    env = os.environ.copy()
    # Never inherit random tokens into logs; only pass what the payload needs via stdin.
    for key in list(env):
        lowered = key.lower()
        if any(token in lowered for token in ("token", "secret", "password", "api_key")):
            if key not in {"ATNOS_NODE", "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}:
                # Keep OS essentials; drop credential-like vars from parent.
                if key.startswith(("YOUTUBE_", "GOOGLE_", "OAUTH_", "AWS_", "OPENAI_")):
                    env.pop(key, None)
    completed = subprocess.run(
        [str(NODE), str(BRIDGE_SCRIPT), command],
        input=body,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        cwd=str(BRIDGE_ROOT),
        env=env,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if not stdout:
        return {
            "ok": False,
            "status": "FAILED",
            "detail": stderr[:500] or f"bridge exit {completed.returncode}",
            "exit_code": completed.returncode,
        }
    try:
        parsed = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return {
            "ok": False,
            "status": "FAILED",
            "detail": "Bridge returned non-JSON output.",
            "raw": stdout[:300],
            "exit_code": completed.returncode,
        }
    if not isinstance(parsed, dict):
        return {"ok": False, "status": "FAILED", "detail": "Bridge payload is not an object."}
    # Defense: never bubble secrets if the SDK echoed them.
    return _redact(parsed)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in ("token", "secret", "password", "authorization", "cookie")):
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def mock_read() -> dict[str, Any]:
    return run_bridge("mock_read")


def youtube_read(*, access_token: str, channel_id: str, limit: int = 3) -> dict[str, Any]:
    return run_bridge(
        "youtube_read",
        {"access_token": access_token, "channel_id": channel_id, "limit": limit},
    )


def capabilities(adapter: str = "youtube") -> dict[str, Any]:
    return run_bridge("capabilities", {"adapter": adapter})


def doctor(adapter: str = "mock") -> dict[str, Any]:
    return run_bridge("doctor", {"adapter": adapter})
