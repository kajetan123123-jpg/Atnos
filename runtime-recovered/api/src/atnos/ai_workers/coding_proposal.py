"""Bounded local coding proposals. Never executes or applies model output."""
from __future__ import annotations

import difflib
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


def proposal_schema(paths: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": {"edits": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
        "type": "object", "properties": {"path": {"type": "string", "enum": paths}, "old": {"type": "string", "minLength": 1}, "new": {"type": "string"}},
        "required": ["path", "old", "new"], "additionalProperties": False,
    }}}, "required": ["edits"], "additionalProperties": False}


def source_snapshot(root: Path, paths: Any) -> dict[str, str]:
    if not isinstance(paths, list) or not 1 <= len(paths) <= 3:
        raise ValueError("coding requires source_paths: 1 to 3 repository source files")
    root = root.resolve()
    sources: dict[str, str] = {}
    for raw in paths:
        if not isinstance(raw, str):
            raise ValueError("source path must be a string")
        name = raw.replace("\\", "/")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or ":" in name:
            raise ValueError("source path must be repository-relative")
        if not name.startswith(("runtime-recovered/api/src/", "runtime-recovered/api/tests/", "frontend/src/")):
            raise ValueError("coding source must belong to application source or tests")
        target = (root / name).resolve()
        if not target.is_relative_to(root) or target.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx", ".css"}:
            raise ValueError("unsupported coding source path")
        if target.stat().st_size > 32000:
            raise ValueError("source file exceeds 32000 bytes; choose a smaller task")
        sources[name] = target.read_text(encoding="utf-8")
    if sum(len(value.encode("utf-8")) for value in sources.values()) > 12000:
        raise ValueError("coding source context exceeds 12000 bytes; split the task")
    return sources


def validate_proposal(content: str, sources: dict[str, str]) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("edits"), list) or not 1 <= len(data["edits"]) <= 8:
        raise ValueError("proposal requires 1 to 8 exact replacements")
    candidates = dict(sources)
    for edit in data["edits"]:
        if not isinstance(edit, dict):
            raise ValueError("edit must be an object")
        name, old, new = edit.get("path"), edit.get("old"), edit.get("new")
        if not isinstance(name, str) or name not in sources:
            raise ValueError("edit outside explicit source_paths")
        if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
            raise ValueError("edit requires different old/new strings and a nonempty old string")
        if candidates[name].count(old) != 1:
            raise ValueError("old string must match exactly once")
        candidates[name] = candidates[name].replace(old, new, 1)
    changed = {name: value for name, value in candidates.items() if value != sources[name]}
    if not changed:
        raise ValueError("proposal has no net changes")
    for name, value in changed.items():
        if name.endswith(".py"):
            try:
                ast.parse(value, filename=name)
            except SyntaxError as exc:
                raise ValueError(f"candidate Python syntax invalid: {exc.msg} at line {exc.lineno}") from exc
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {
        "execution_mode": "local_coding_proposal",
        "edits": data["edits"],
        "source_sha256": {name: digest(value) for name, value in sources.items()},
        "candidate_sha256": {name: digest(value) for name, value in changed.items()},
        "diff": "".join("".join(difflib.unified_diff(sources[name].splitlines(True), value.splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}")) for name, value in changed.items()),
        "main_worktree_modified": False,
        "verification": {"status": "awaiting_human_review", "contract": "passed", "python_syntax": "passed" if any(name.endswith(".py") for name in changed) else "not_checked", "tests_run": False},
    }
