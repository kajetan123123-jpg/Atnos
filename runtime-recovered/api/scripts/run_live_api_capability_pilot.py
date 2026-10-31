"""Live :8000 capability workflow pilot (not TestClient)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

BASE = "http://127.0.0.1:8000"
SCRIPT = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-dc76431dac84\script.json"
)
OUT = Path(r"E:\Atnos\App\reports\live-api-capability-delegate.json")
SKILLS = Path(r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\integrations")


def http_json(method: str, path: str, payload: dict | None = None, timeout: float = 120.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {"raw": body}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return int(exc.code), parsed


def main() -> None:
    from atnos.integrations.external_repositories import set_skill_enabled

    SKILLS.mkdir(parents=True, exist_ok=True)
    for skill in ("yt-package", "yt-script", "yt-retention"):
        set_skill_enabled(skill, True, root=SKILLS)

    ready_status, ready = http_json("GET", "/health/ready")
    prompt = (
        "Review the current Wealth Blueprint short and prepare packaging and script QA "
        "using available capabilities."
    )
    status, task = http_json(
        "POST",
        "/api/ai-workers/delegate",
        {
            "task_type": "capability_workflow",
            "prompt": prompt,
            "context": {
                "project_key": "wealth_blueprint",
                "production_id": "prod-dc76431dac84",
                "script_path": str(SCRIPT),
                "run_capability_workflow": True,
            },
            "preferred_worker": "capability_runtime",
            "allowed_workers": ["capability_runtime", "ollama"],
            "privacy_mode": "strict_local",
            "priority": "normal",
            "requested_by": "live_api_capability_activation",
            "requires_approval_for_result": True,
            "acceptance_criteria": [
                "Use capability ids only; do not invent retention or social metrics.",
                "End at PRODUCTION_BRIEF_READY without publishing.",
            ],
        },
    )
    result = task.get("result_json") if isinstance(task.get("result_json"), dict) else {}
    summary = {
        "health_ready_http": ready_status,
        "health_ready": ready,
        "delegate_http": status,
        "task_id": task.get("id"),
        "task_status": task.get("status"),
        "capabilities_used": task.get("capabilities_used") or result.get("capabilities_used"),
        "workflow_status": result.get("workflow_status"),
        "production_status": result.get("production_status"),
        "skipped": result.get("skipped"),
        "qc": (result.get("quality_council") or {}).get("status"),
        "published": result.get("published"),
        "repo_names_in_plan": result.get("repo_names_in_plan"),
        "result_summary": task.get("result_summary"),
        "blocked_reason": task.get("blocked_reason"),
        "error_message": task.get("error_message"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"summary": summary, "task": task, "result": result}, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
