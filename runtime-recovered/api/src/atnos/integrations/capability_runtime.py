"""Executable capability runtime: TASK → CAPABILITY → skill/tool → verify → provenance.

Repo names are sources, not business logic. Capabilities only exist when an execution path exists.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from atnos.integrations import external_repositories as repos

Executor = Callable[[dict[str, Any], Path | None], dict[str, Any]]

PROJECT_SKILL_ACCESS: dict[str, frozenset[str]] = {
    "wealth_blueprint": frozenset(
        {
            "RESOURCE_DISCOVERY",
            "RESOURCE_REVIEW",
            "YOUTUBE_PACKAGING",
            "YOUTUBE_SEO_COPY",
            "YOUTUBE_SCRIPT_QA",
            "YOUTUBE_RETENTION_ANALYSIS",
            "SOCIAL_READ",
            "SOCIAL_COMMENTS_READ",
            "SOCIAL_PUBLISH_PREPARE",
            "WEB_PAGE_EXTRACT",
        }
    ),
    "wonder_crew": frozenset(
        {
            "RESOURCE_DISCOVERY",
            "RESOURCE_REVIEW",
            "YOUTUBE_PACKAGING",
            "YOUTUBE_SCRIPT_QA",
            "ANIMATION_2D_RENDER",
            "WEB_PAGE_EXTRACT",
        }
    ),
    "dark_chronicles": frozenset(
        {
            "RESOURCE_DISCOVERY",
            "RESOURCE_REVIEW",
            "YOUTUBE_PACKAGING",
            "YOUTUBE_SCRIPT_QA",
            "YOUTUBE_SEO_COPY",
            "WEB_PAGE_EXTRACT",
        }
    ),
}

# Capability → runtime binding. Only entries with a real executor are listed.
CAPABILITY_BINDINGS: dict[str, dict[str, Any]] = {
    "RESOURCE_DISCOVERY": {
        "provider": "awesome",
        "skill_id": None,
        "kind": "DISCOVERY_CATALOG",
        "permissions": ("network:read_public",),
        "conflict": "USE_EXTERNAL",
        "verification": "live",
    },
    "RESOURCE_REVIEW": {
        "provider": "awesome",
        "skill_id": None,
        "kind": "DISCOVERY_CATALOG",
        "permissions": ("network:read_public",),
        "conflict": "USE_EXTERNAL",
        "verification": "local",
    },
    "YOUTUBE_PACKAGING": {
        "provider": "youtube_skills_chatgpt",
        "skill_id": "yt-package",
        "kind": "NATIVE_INSTRUCTION",
        "permissions": ("skill:execute",),
        "conflict": "MERGE_LOGIC",
        "verification": "local",
    },
    "YOUTUBE_SEO_COPY": {
        "provider": "youtube_skills_chatgpt",
        "skill_id": "yt-seo",
        "kind": "NATIVE_INSTRUCTION",
        "permissions": ("skill:execute",),
        "conflict": "MERGE_LOGIC",
        "verification": "local",
    },
    "YOUTUBE_SCRIPT_QA": {
        "provider": "youtube_skills_chatgpt",
        "skill_id": "yt-script",
        "kind": "NATIVE_INSTRUCTION",
        "permissions": ("skill:execute",),
        "conflict": "IMPROVES_EXISTING",
        "verification": "local",
    },
    "YOUTUBE_RETENTION_ANALYSIS": {
        "provider": "youtube_skills_chatgpt",
        "skill_id": "yt-retention",
        "kind": "NATIVE_INSTRUCTION",
        "permissions": ("skill:execute", "data:retention_export"),
        "conflict": "USE_EXTERNAL",
        "verification": "local",
    },
    "SOCIAL_READ": {
        "provider": "social_sdk",
        "skill_id": None,
        "kind": "INTEGRATION_ADAPTER",
        "permissions": ("vault:resolve", "network:provider"),
        "conflict": "USE_NATIVE",
        "verification": "live",
    },
    "SOCIAL_COMMENTS_READ": {
        "provider": "social_sdk",
        "skill_id": None,
        "kind": "INTEGRATION_ADAPTER",
        "permissions": ("vault:resolve", "network:provider"),
        "conflict": "USE_NATIVE",
        "verification": "live",
    },
    "SOCIAL_PUBLISH_PREPARE": {
        "provider": "social_sdk",
        "skill_id": None,
        "kind": "INTEGRATION_ADAPTER",
        "permissions": ("skill:execute",),
        "conflict": "USE_NATIVE",
        "verification": "local",
    },
    "ANIMATION_2D_RENDER": {
        "provider": "opentoonz",
        "skill_id": None,
        "kind": "LOCAL_APPLICATION",
        "permissions": ("filesystem:tool", "process:spawn"),
        "conflict": "USE_EXTERNAL",
        "verification": "local",
    },
    "WEB_PAGE_EXTRACT": {
        "provider": "scrapling",
        "skill_id": None,
        "kind": "INTEGRATION_LIBRARY",
        "permissions": ("network:read_public",),
        "conflict": "USE_EXTERNAL",
        "verification": "live",
    },
    "YOUTUBE_TRANSCRIPT": {
        "provider": "youtube_transcript_api",
        "skill_id": None,
        "kind": "INTEGRATION_LIBRARY",
        "permissions": ("network:read_public",),
        "conflict": "USE_EXTERNAL",
        "verification": "live",
    },
    "YOUTUBE_METADATA": {
        "provider": "yt_dlp",
        "skill_id": None,
        "kind": "INTEGRATION_LIBRARY",
        "permissions": ("network:read_public",),
        "conflict": "USE_EXTERNAL",
        "verification": "live",
    },
    "CAPTION_ALIGN": {
        "provider": "whisperx",
        "skill_id": None,
        "kind": "INTEGRATION_LIBRARY",
        "permissions": ("filesystem:tool",),
        "conflict": "USE_EXTERNAL",
        "verification": "local",
    },
    "ICON_SVG_LIBRARY": {
        "provider": "lucide",
        "skill_id": None,
        "kind": "ASSET_LIBRARY",
        "permissions": ("filesystem:tool",),
        "conflict": "USE_EXTERNAL",
        "verification": "local",
    },
    "FINANCE_CHART_SKILL": {
        "provider": "remotion_trading",
        "skill_id": None,
        "kind": "SKILL_PACK",
        "permissions": ("filesystem:tool",),
        "conflict": "IMPROVES_EXISTING",
        "verification": "local",
    },
}


def list_executable_capabilities() -> list[dict[str, Any]]:
    rows = []
    for capability, binding in CAPABILITY_BINDINGS.items():
        skill_id = binding.get("skill_id")
        enabled = True
        if skill_id:
            skill = next((item for item in repos.list_skills() if item["id"] == skill_id), None)
            enabled = bool(skill and skill.get("enabled"))
        rows.append(
            {
                "capability": capability,
                "provider": binding["provider"],
                "skill_id": skill_id,
                "kind": binding["kind"],
                "conflict": binding["conflict"],
                "permissions": list(binding["permissions"]),
                "skill_enabled": enabled if skill_id else True,
                "executable": (not skill_id) or enabled,
            }
        )
    return rows


def classify_task(text: str) -> tuple[str, dict[str, Any]]:
    """Map natural language to a capability without naming repos."""
    from atnos.integrations.capability_router import classify_intent

    capability, params, _fresh = classify_intent(text)
    if capability:
        return capability, params
    lowered = text.casefold()
    if re.search(r"\b(retention|retencj)\b", lowered):
        return "YOUTUBE_RETENTION_ANALYSIS", {}
    if re.search(r"\b(packaging|thumbnail|tytuł|title|hook)\b", lowered):
        return "YOUTUBE_PACKAGING", {}
    if re.search(r"\b(seo|description|tags)\b", lowered):
        return "YOUTUBE_SEO_COPY", {}
    if re.search(r"\b(script qa|qa skrypt|script review)\b", lowered):
        return "YOUTUBE_SCRIPT_QA", {}
    if re.search(r"\b(comment|komentarz)\b", lowered):
        return "SOCIAL_COMMENTS_READ", {}
    if re.search(r"\b(publish|publikacj)\b", lowered) and re.search(r"\b(prepare|przygotuj|plan)\b", lowered):
        return "SOCIAL_PUBLISH_PREPARE", {}
    if re.search(r"\b(social|channel|konto|account identity|profile)\b", lowered):
        return "SOCIAL_READ", {}
    if re.search(r"\b(opentoonz|2d anim|render scen)\b", lowered):
        return "ANIMATION_2D_RENDER", {}
    if re.search(r"\b(scrape|scraping|extract (from )?(html|page|url)|parse html|web page extract)\b", lowered):
        return "WEB_PAGE_EXTRACT", {}
    if re.search(r"\b(review|sprawdź licenc|license check)\b", lowered) and re.search(
        r"\b(repo|github|library|narzęd)\b", lowered
    ):
        return "RESOURCE_REVIEW", {"query": text}
    return "", {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(
    *,
    capability: str,
    provider: str,
    status: str,
    output: Any = None,
    artifacts: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    skill_id: str | None = None,
    input_reference: dict[str, Any] | None = None,
    started_at: str | None = None,
    verification: dict[str, Any] | None = None,
    mock_or_live: str = "LOCAL VERIFIED",
) -> dict[str, Any]:
    finished = _now()
    return {
        "execution_id": uuid.uuid4().hex[:16],
        "capability": capability,
        "provider": provider,
        "skill_id": skill_id,
        "input_reference": _redact(input_reference or {}),
        "output": _redact(output),
        "artifacts": artifacts or [],
        "status": status,
        "started_at": started_at or finished,
        "finished_at": finished,
        "evidence": evidence or [],
        "errors": errors or [],
        "verification": verification or {},
        "provenance": {
            "source_type": "capability_runtime",
            "provider": provider,
            "skill_id": skill_id,
            "capability": capability,
            "verification_level": mock_or_live,
            "finished_at": finished,
        },
        "mock_or_live": mock_or_live,
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        blocked = {
            "password",
            "access_token",
            "refresh_token",
            "client_secret",
            "api_key",
            "authorization",
            "cookie",
            "token",
            "secret",
        }
        out = {}
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in blocked or "token" in str(key).casefold():
                out[key] = "[REDACTED]"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _project_allowed(project_id: str | None, capability: str) -> bool:
    if not project_id:
        return True
    # Canonical production ids must use the same restrictions as legacy profiles.
    project_id = {"children_stories": "wonder_crew", "horror_stories": "dark_chronicles"}.get(project_id, project_id)
    allowed = PROJECT_SKILL_ACCESS.get(project_id)
    if allowed is None:
        return True
    return capability in allowed


def execute_capability(
    capability: str | None = None,
    *,
    intent: str | None = None,
    parameters: dict[str, Any] | None = None,
    project_id: str | None = None,
    root: Path | None = None,
    settings: Any = None,
    session: Any = None,
) -> dict[str, Any]:
    started = _now()
    params = dict(parameters or {})
    resolved = capability
    if not resolved and intent:
        resolved, inferred = classify_task(intent)
        for key, value in inferred.items():
            params.setdefault(key, value)
    if not resolved:
        return _result(
            capability="",
            provider="",
            status="BLOCKED",
            errors=["Nie rozpoznano capability."],
            started_at=started,
            mock_or_live="LOCAL VERIFIED",
        )
    binding = CAPABILITY_BINDINGS.get(resolved)
    if binding is None:
        return _result(
            capability=resolved,
            provider="",
            status="BLOCKED",
            errors=["Capability nie ma ścieżki wykonania."],
            started_at=started,
        )
    if not _project_allowed(project_id, resolved):
        return _result(
            capability=resolved,
            provider=binding["provider"],
            status="BLOCKED",
            errors=[f"Projekt {project_id} nie ma dostępu do {resolved}."],
            skill_id=binding.get("skill_id"),
            started_at=started,
        )
    skill_id = binding.get("skill_id")
    if skill_id:
        skill = next((item for item in repos.list_skills(root) if item["id"] == skill_id), None)
        if not skill or not skill.get("enabled"):
            return _result(
                capability=resolved,
                provider=binding["provider"],
                status="BLOCKED",
                errors=[f"Skill {skill_id} jest wyłączony. Włącz go w Settings → Skills."],
                skill_id=skill_id,
                started_at=started,
            )

    from atnos.integrations.capability_executors import run_executor

    raw = run_executor(
        resolved,
        params,
        root=root,
        settings=settings,
        session=session,
        project_id=project_id,
    )
    status = str(raw.get("status") or "FAILED")
    verification = raw.get("verification") if isinstance(raw.get("verification"), dict) else {}
    if status == "SUCCESS" and not verification.get("ok"):
        status = "FAILED"
        raw.setdefault("errors", []).append("Verifier odrzucił wynik mimo raw SUCCESS.")
    result = _result(
        capability=resolved,
        provider=binding["provider"],
        status=status,
        output=raw.get("output"),
        artifacts=raw.get("artifacts"),
        evidence=raw.get("evidence"),
        errors=raw.get("errors"),
        skill_id=skill_id,
        input_reference={"parameters": params, "project_id": project_id, "intent": intent},
        started_at=started,
        verification=verification,
        mock_or_live=str(raw.get("mock_or_live") or "LOCAL VERIFIED"),
    )
    # Preserve extractor provenance (content_hash, final_url, redirect chain) when present.
    nested = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else None
    if nested:
        merged = dict(result["provenance"])
        merged.update(nested)
        result["provenance"] = _redact(merged)
    if isinstance(raw.get("diagnostics"), dict):
        result["diagnostics"] = _redact(raw["diagnostics"])
    return result


def record_last_execution(root: Path | None, payload: dict[str, Any]) -> None:
    state = repos._load(root)
    history = state.get("executions") if isinstance(state.get("executions"), list) else []
    history.append(
        {
            "execution_id": payload.get("execution_id"),
            "capability": payload.get("capability"),
            "status": payload.get("status"),
            "finished_at": payload.get("finished_at"),
            "mock_or_live": payload.get("mock_or_live"),
            "skill_id": payload.get("skill_id"),
        }
    )
    state["executions"] = history[-40:]
    state["last_execution"] = state["executions"][-1]
    repos._save(root, state)


def last_execution(root: Path | None = None) -> dict[str, Any] | None:
    state = repos._load(root)
    last = state.get("last_execution")
    return last if isinstance(last, dict) else None
