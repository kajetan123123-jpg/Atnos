from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import init_intelligence_db  # noqa: E402
from atnos.production.money_machine_remediation import MoneyMachineRemediationService  # noqa: E402
from test_support import bootstrap_test_app, create_test_app  # noqa: E402


def _boot(tmp_path: Path):
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    asyncio.run(init_intelligence_db(app.state.settings))
    return app


def test_remediation_action_mapping_classifies_current_blockers(tmp_path: Path) -> None:
    app = _boot(tmp_path)
    with app.state.database.session_scope() as session:
        plan = asyncio.run(
            MoneyMachineRemediationService(app.state.settings, session=session).build_plan(
                include_live_checks=False,
            )
        )

    actions = {item["id"]: item for item in plan["actions"]}
    assert plan["status"] == "needs_remediation"
    assert "youtube_oauth" in plan["critical_blockers"]
    assert actions["reconnect_youtube_oauth"]["execution"] == "owner_action_required"
    assert actions["enable_public_upload_policy"]["execution"] == "owner_action_required"
    assert actions["use_procurement_manual_checkout"]["execution"] == "policy"
    assert plan["public_action_guard"]["public_upload_auto_execution"] is False
    assert Path(plan["artifacts"]["json_path"]).exists()
    markdown_path = Path(plan["artifacts"]["markdown_path"])
    assert markdown_path.exists()
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Reconnect YouTube OAuth" in markdown
    assert "Public upload auto execution: False" in markdown


def test_remediation_maps_public_quality_failures_to_regeneration() -> None:
    actions = {
        item["id"]: item
        for item in MoneyMachineRemediationService._actions_for_blockers(
            critical=["private_upload_public_quality_failed", "public_quality_gate_failed"],
            warnings=[],
            strategy={"status": "needs_work"},
        )
    }

    action = actions["regenerate_failed_public_quality_jobs"]
    assert action["execution"] == "configuration_required"
    assert action["endpoint"] == "POST /api/production/money-machine/run-next"
    assert action["payload"]["execute_recovery"] is True
    assert action["payload"]["approved_private_upload"] is False


def test_remediation_quality_recovery_counts_visual_blockers() -> None:
    recovery = MoneyMachineRemediationService._quality_recovery(
        {
            "money_machine": {
                "operator_summary": {
                    "cost_guard": {
                        "enabled": True,
                        "allowed": True,
                        "blocked": False,
                        "warning": False,
                        "currency": "PLN",
                        "estimated_action_cost_pln": 4.0,
                        "monthly_remaining_pln": 84.0,
                        "daily_remaining_pln": 75.0,
                    },
                    "recovery_ready": [
                        {
                            "account_id": "youtube:a@example.com",
                            "job_id": "job-a",
                            "topic_id": "topic-a",
                            "topic_title": "A",
                            "recovery_action": "regenerate_visuals_with_auto_repair",
                            "recovery_endpoint": "POST /api/production/jobs/job-a/regenerate",
                            "public_quality_blockers": [
                                "actual_visual_variety_missing_for_public",
                                "premium_visual_backend_required_for_public",
                            ],
                        },
                        {
                            "account_id": "youtube:b@example.com",
                            "job_id": "job-b",
                            "public_quality_blockers": ["premium_visual_backend_required_for_public"],
                        },
                    ]
                }
            }
        }
    )

    assert recovery["total_candidates"] == 2
    assert recovery["blocker_counts"]["premium_visual_backend_required_for_public"] == 2
    assert recovery["blocker_counts"]["actual_visual_variety_missing_for_public"] == 1
    assert recovery["safe_to_execute_next_batch"] is True
    assert recovery["cost_guard"]["monthly_remaining_pln"] == 84.0
    assert recovery["next_batch_payload"]["execute_recovery"] is True
    assert recovery["next_batch_payload"]["approved_private_upload"] is False


def test_remediation_execute_safe_runs_only_doctor(monkeypatch, tmp_path: Path) -> None:
    app = _boot(tmp_path)

    async def fake_voice_doctor(settings):
        return {
            "public_voice_ready": True,
            "next_action": "premium_voice_ready_generate_private_public_quality_short",
            "report_path": str(settings.data_dir / "reports" / "voice.json"),
        }

    monkeypatch.setattr("atnos.production.money_machine_remediation.write_voice_provider_doctor_report", fake_voice_doctor)

    with app.state.database.session_scope() as session:
        result = asyncio.run(
            MoneyMachineRemediationService(app.state.settings, session=session).execute_safe_repairs(
                include_live_checks=False,
                run_voice_doctor=True,
            )
        )

    assert result["status"] == "completed"
    assert result["executed"][0]["action_id"] == "run_voice_provider_doctor"
    assert result["executed"][0]["public_voice_ready"] is True
    assert "No public upload was enabled automatically." in result["not_executed_policy"]
    assert "No OAuth consent was started automatically." in result["not_executed_policy"]
    assert "No payment method or card operation was attempted." in result["not_executed_policy"]
    assert Path(result["artifacts"]["json_path"]).exists()
    markdown = Path(result["artifacts"]["markdown_path"]).read_text(encoding="utf-8")
    assert "No payment method or card operation was attempted." in markdown


def test_remediation_api_endpoints(tmp_path: Path) -> None:
    app = _boot(tmp_path)
    client = TestClient(app)

    plan_response = client.get("/api/production/money-machine/remediation-plan?include_live_checks=false")
    safe_response = client.post(
        "/api/production/money-machine/remediate-safe",
        json={"include_live_checks": False, "run_voice_doctor": False},
    )

    assert plan_response.status_code == 200
    assert plan_response.json()["public_action_guard"]["public_upload_auto_execution"] is False
    assert safe_response.status_code == 200
    assert safe_response.json()["executed"] == []
    assert "No public promotion was executed automatically." in safe_response.json()["not_executed_policy"]
