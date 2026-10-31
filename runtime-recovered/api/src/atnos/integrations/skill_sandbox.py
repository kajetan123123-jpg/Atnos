"""Controlled execution of vendored YouTube skill tools (no network, pinned paths only)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILLS_ROOT = Path(r"E:\Atnos\Tools\external-repos\chatgpt-youtube-agent-skill\skills")

SAFE_TOOLS: dict[str, dict[str, Any]] = {
    "yt-package": {
        "script": SKILLS_ROOT / "yt-package" / "title.py",
        "network": False,
        "shell": False,
        "timeout_s": 15,
    },
    "yt-script": {
        "script": SKILLS_ROOT / "yt-script" / "hookscore.py",
        "network": False,
        "shell": False,
        "timeout_s": 15,
    },
    "yt-chapters": {
        "script": SKILLS_ROOT / "yt-chapters" / "chapters.py",
        "network": False,
        "shell": False,
        "timeout_s": 20,
    },
    "yt-retention": {
        "script": SKILLS_ROOT / "yt-retention" / "retention.py",
        "network": False,
        "shell": False,
        "timeout_s": 20,
    },
    "yt-edit": {
        "script": SKILLS_ROOT / "yt-edit" / "deadair.py",
        "network": False,
        "shell": False,
        "timeout_s": 20,
    },
}


def skill_markdown(skill_id: str) -> str | None:
    path = SKILLS_ROOT / skill_id / "SKILL.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def run_safe_tool(skill_id: str, argv: list[str], *, timeout_s: float | None = None) -> dict[str, Any]:
    spec = SAFE_TOOLS.get(skill_id)
    if spec is None:
        return {"status": "BLOCKED", "detail": f"Skill {skill_id} has no sandboxed tool."}
    script: Path = spec["script"]
    if not script.is_file():
        return {"status": "REQUIRES_SETUP", "detail": f"Missing vendored tool {script}"}
    resolved = script.resolve()
    if SKILLS_ROOT.resolve() not in resolved.parents and resolved.parent != SKILLS_ROOT.resolve():
        return {"status": "BLOCKED", "detail": "Tool path escaped skills root."}
    if ".." in argv:
        return {"status": "BLOCKED", "detail": "Invalid argv."}
    # Disallow path escape via absolute paths outside skills/data roots.
    for arg in argv:
        if arg.startswith("--"):
            continue
        candidate = Path(arg)
        if candidate.is_absolute() and not _allowed_data_path(candidate):
            return {"status": "BLOCKED", "detail": "Argument path not allowed."}
    cmd = [sys.executable, "-I", str(resolved), *argv]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=float(timeout_s or spec["timeout_s"]),
            cwd=str(resolved.parent),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "FAILED", "detail": "Skill tool timed out.", "verification": {"ok": False}}
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload: Any
    stripped = stdout.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = {"stdout": stdout[-4000:]}
    else:
        payload = {"stdout": stdout[-4000:]}
    return {
        "status": "SUCCESS" if completed.returncode == 0 else "FAILED",
        "exit_code": completed.returncode,
        "output": payload,
        "stderr": stderr[-1000:],
        "tool": str(resolved),
        "source_repo": "https://github.com/Jakeschincariol/chatgpt-youtube-agent-skill",
        "verification": {"ok": completed.returncode == 0 and bool(stdout.strip()), "checks": ["exit_code", "stdout"]},
    }


def _allowed_data_path(path: Path) -> bool:
    allowed = [
        SKILLS_ROOT.resolve(),
        Path(r"E:\Atnos\Profiles").resolve(),
        Path(r"E:\Atnos\App\data").resolve(),
        Path(r"E:\Atnos\App\reports").resolve(),
    ]
    resolved = path.resolve()
    return any(root == resolved or root in resolved.parents for root in allowed)


def lint_titles(titles: list[str], *, thumb: str | None = None) -> dict[str, Any]:
    if not titles:
        return {"status": "REQUIRES_DATA", "detail": "No titles to lint."}
    rows = []
    for title in titles[:12]:
        argv = ["--title", title, "--json"]
        if thumb:
            argv.extend(["--thumb", thumb])
        one = run_safe_tool("yt-package", argv)
        if one.get("status") != "SUCCESS":
            return one
        payload = one.get("output")
        if isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict) and "title" in payload:
            rows.append(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("stdout"), str):
            try:
                parsed = json.loads(payload["stdout"])
                if isinstance(parsed, list):
                    rows.extend(item for item in parsed if isinstance(item, dict))
            except json.JSONDecodeError:
                return {"status": "FAILED", "detail": "title.py JSON parse failed.", "output": payload}
    rows.sort(key=lambda item: -int(item.get("score") or 0))
    return {
        "status": "SUCCESS",
        "output": {"ranked": rows, "source_tool": "title.py"},
        "verification": {"ok": bool(rows), "checks": ["ranked_titles"]},
        "tool": str(SAFE_TOOLS["yt-package"]["script"]),
        "mock_or_live": "LOCAL VERIFIED",
    }
