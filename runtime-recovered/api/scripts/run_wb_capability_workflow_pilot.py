"""One-shot Wealth Blueprint capability workflow pilot via POST /api/ai-workers/delegate."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1] / "tests"
API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
for path in (str(SRC_ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi.testclient import TestClient

from atnos.integrations.external_repositories import set_skill_enabled
from test_support import bootstrap_test_app, create_test_app


SCRIPT = Path(
    r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint\prod-dc76431dac84\script.json"
)
OUT = Path(r"E:\Atnos\App\reports\wb-capability-workflow-e2e.json")


def main() -> None:
    temp = tempfile.TemporaryDirectory(prefix="atnos-cap-pilot-")
    root = Path(temp.name)
    app = create_test_app(root)
    bootstrap_test_app(app)
    integrations = app.state.settings.data_dir / "integrations"
    integrations.mkdir(parents=True, exist_ok=True)
    for skill in ("yt-package", "yt-script", "yt-retention"):
        set_skill_enabled(skill, True, root=integrations)

    prompt = (
        "Review the current Wealth Blueprint short and prepare a better production brief "
        "and packaging recommendation using available capabilities."
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/ai-workers/delegate",
            json={
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
                "requested_by": "capability_workflow_pilot",
                "requires_approval_for_result": True,
                "acceptance_criteria": [
                    "Use capability ids only; do not invent retention or social metrics.",
                    "End at PRODUCTION_BRIEF_READY without publishing.",
                ],
            },
        )
    payload = response.json()
    result = payload.get("result_json") if isinstance(payload.get("result_json"), dict) else {}
    result["_entry"] = "POST /api/ai-workers/delegate"
    result["_http_status"] = response.status_code
    result["_task"] = {
        "id": payload.get("id"),
        "status": payload.get("status"),
        "capabilities_used": payload.get("capabilities_used"),
        "result_summary": payload.get("result_summary"),
        "blocked_reason": payload.get("blocked_reason"),
        "error_message": payload.get("error_message"),
        "verification_status": payload.get("verification_status"),
    }
    if not result.get("workflow_status"):
        result["_raw_task"] = payload

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "http_status": response.status_code,
                "task_status": payload.get("status"),
                "entry": result.get("_entry"),
                "workflow_status": result.get("workflow_status"),
                "production_status": result.get("production_status"),
                "capabilities_used": result.get("capabilities_used") or payload.get("capabilities_used"),
                "skipped": [
                    {"capability": item.get("capability"), "status": item.get("status"), "message": item.get("message")}
                    for item in (result.get("skipped") or [])
                ],
                "qc": (result.get("quality_council") or {}).get("status"),
                "published": result.get("published"),
                "brain_stored": [
                    {"stored": item.get("stored"), "store_reason": item.get("store_reason"), "kind": item.get("kind")}
                    for item in (result.get("project_brain_writes") or [])
                ],
                "out": str(OUT),
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    try:
        temp.cleanup()
    except PermissionError:
        pass


if __name__ == "__main__":
    main()
