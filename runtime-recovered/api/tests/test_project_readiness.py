from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, upsert_intelligence_account_config, utc_now_iso  # noqa: E402
from test_support import create_test_app  # noqa: E402


def test_project_readiness_endpoint_reports_private_ready_youtube(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)

    def _fake_money_status(self, *, account_id=None, include_oauth=True):  # noqa: ANN001
        return {
            "status": "ready_for_private_review",
            "autopilot_enabled": True,
            "human_approval_before_upload": True,
            "public_upload_enabled": False,
            "global_blockers": [],
            "next_actions": ["review_and_execute_private_upload"],
            "accounts": [
                {
                    "account_id": "youtube:account-c5034bf4@example.invalid",
                    "channel_title": "Wealth Blueprint",
                    "youtube_oauth": {"ready": True, "account_id": 50},
                    "topic_counts": {"buffer_count": 7, "approved": 2},
                    "job_counts": {"ready_private": 1},
                    "signals": {"youtube_trend_videos": 14, "google_trends_keywords": 3},
                    "ready_private_upload_count": 1,
                    "public_promotion_count": 0,
                    "blockers": [],
                    "next_action": "review_and_execute_private_upload",
                }
            ],
        }

    monkeypatch.setattr(
        "atnos.services.project_readiness_service.MoneyMachineStatusService.build",
        _fake_money_status,
    )

    with TestClient(app) as client:
        upsert_intelligence_account_config(
            app.state.settings,
            account_id="youtube:account-c5034bf4@example.invalid",
            niches=["personal finance", "wealth"],
            region="US",
            youtube_oauth_account_id=50,
            channel_title="Wealth Blueprint",
            content_profile="wealth_blueprint",
        )
        response = client.get("/api/projects/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total"] >= 3
    assert payload["summary"]["youtube_private_upload_ready"] == 1
    assert payload["summary"]["cross_platform_ready"] == 0
    assert payload["money_machine"]["human_approval_before_upload"] is True
    assert payload["money_machine"]["public_upload_enabled"] is False
    assert payload["financials"]["card_data_storage"] == "forbidden"
    assert payload["financials"]["manual_checkout_only"] is True
    assert payload["financials"]["can_use_card"] is False

    by_id = {item["project_id"]: item for item in payload["projects"]}
    wealth = by_id["youtube:account-c5034bf4@example.invalid"]
    assert wealth["status"] == "ready"
    assert wealth["capabilities"]["can_research"] is True
    assert wealth["capabilities"]["can_upload_private"] is True
    assert wealth["capabilities"]["can_publish_public"] is False
    assert wealth["safe_mode"] is True
    assert wealth["operator_decision"]["state"] == "ready_private_upload_review"
    assert wealth["operator_decision"]["primary_endpoint"] == "/api/production/money-machine/run-next"
    assert wealth["operator_decision"]["requires_owner_approval"] is True
    assert wealth["operator_decision"]["safe_to_run_now"] is False
    assert wealth["operator_decision"]["card_data_storage"] == "forbidden"
    assert wealth["financials"]["card_data_storage"] == "forbidden"
    assert wealth["access_playbook"]["mode"] == "supervised_private_upload"
    assert any(item["key"] == "youtube_oauth" for item in wealth["access_playbook"]["required_connections"])
    assert "Public publication remains disabled until public upload is explicitly enabled and quality gates pass." in wealth["access_playbook"]["blocked_until_configured"]
    assert wealth["tooling_plan"]["report_ref"].endswith("atnos_tooling_research_report.md")
    youtube_tooling = {item["key"]: item for item in wealth["tooling_plan"]["must_have"]}
    assert youtube_tooling["youtube_oauth"]["status"] == "ready"
    assert "premium_voice" in youtube_tooling
    assert "visual_backend" in youtube_tooling
    distribution_by_platform = {item["platform"]: item for item in wealth["distribution_targets"]}
    assert distribution_by_platform["youtube"]["status"] == "ready"
    assert distribution_by_platform["youtube"]["ready_for_private"] is True
    assert distribution_by_platform["tiktok"]["status"] == "missing"
    assert distribution_by_platform["instagram"]["status"] == "missing"

    synthetic = by_id["creator:synthetic-model-lab"]
    assert synthetic["status"] == "blocked"
    assert synthetic["capabilities"]["can_publish_public"] is False
    assert synthetic["capabilities"]["can_trade_or_purchase"] is False
    assert synthetic["operator_decision"]["state"] == "needs_creator_policy"
    assert "autonomous_card_charge" in synthetic["operator_decision"]["forbidden_actions"]
    assert synthetic["access_playbook"]["mode"] == "draft_only_until_creator_access"
    assert "Paid offers, payment automation and subscriptions." in synthetic["access_playbook"]["blocked_until_configured"]
    synthetic_tooling = {item["key"]: item for item in synthetic["tooling_plan"]["must_have"]}
    assert synthetic_tooling["asset_ledger"]["status"] == "missing"
    assert synthetic_tooling["synthetic_disclosure"]["status"] == "missing"

    investment = by_id["investment:portfolio-alpha"]
    assert investment["status"] == "limited"
    assert investment["capabilities"]["can_trade_or_purchase"] is False
    assert investment["capabilities"]["can_research"] is True
    assert investment["operator_decision"]["state"] == "research_paper_only"
    assert investment["operator_decision"]["safe_to_run_now"] is True
    assert "real_broker_order" in investment["operator_decision"]["forbidden_actions"]
    assert investment["access_playbook"]["mode"] == "paper_trading_only"
    assert "Any buy/sell order." in investment["access_playbook"]["blocked_until_configured"]
    assert investment["evidence"]["configured_backend"] is True
    assert investment["evidence"]["real_trading_enabled"] is False
    investment_tooling = {item["key"]: item for item in investment["tooling_plan"]["must_have"]}
    assert investment_tooling["regulator_education"]["status"] == "ready"
    assert investment_tooling["watchlist"]["status"] == "ready"
    assert investment_tooling["paper_trading"]["status"] == "ready"


def test_project_readiness_endpoint_includes_dynamic_intelligence_accounts(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)

    def _fake_money_status(self, *, account_id=None, include_oauth=True):  # noqa: ANN001
        return {
            "status": "needs_topics",
            "autopilot_enabled": False,
            "human_approval_before_upload": True,
            "public_upload_enabled": False,
            "global_blockers": [],
            "next_actions": ["generate_topics"],
            "accounts": [
                {
                    "account_id": "youtube:custom@example.com",
                    "channel_title": "Custom Channel",
                    "youtube_oauth": {"ready": False, "reason": "not_connected"},
                    "topic_counts": {"buffer_count": 0},
                    "job_counts": {},
                    "signals": {},
                    "ready_private_upload_count": 0,
                    "public_promotion_count": 0,
                    "blockers": ["youtube_oauth_missing"],
                    "next_action": "connect_youtube_oauth",
                }
            ],
        }

    monkeypatch.setattr(
        "atnos.services.project_readiness_service.MoneyMachineStatusService.build",
        _fake_money_status,
    )

    with TestClient(app) as client:
        upsert_intelligence_account_config(
            app.state.settings,
            account_id="youtube:custom@example.com",
            niches=["history shorts"],
            region="GB",
            channel_title="Custom Channel",
        )
        response = client.get("/api/projects/readiness")

    assert response.status_code == 200
    by_id = {item["project_id"]: item for item in response.json()["projects"]}
    custom = by_id["youtube:custom@example.com"]
    assert custom["name"] == "Custom Channel"
    assert custom["capabilities"]["can_research"] is True
    assert custom["capabilities"]["can_upload_private"] is False
    assert custom["operator_decision"]["state"] == "can_generate_private"
    assert custom["operator_decision"]["primary_endpoint"] == "/api/production/content-loop/run-once"
    assert "youtube_oauth_missing" in custom["blockers"]


def test_project_readiness_surfaces_quality_review_backlog(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)

    def _fake_money_status(self, *, account_id=None, include_oauth=True):  # noqa: ANN001
        return {
            "status": "needs_private_render",
            "autopilot_enabled": True,
            "human_approval_before_upload": True,
            "public_upload_enabled": False,
            "global_blockers": [],
            "next_actions": ["produce_private_render"],
            "accounts": [
                {
                    "account_id": "youtube:account-c5034bf4@example.invalid",
                    "channel_title": "Wealth Blueprint",
                    "youtube_oauth": {"ready": True, "account_id": 50},
                    "topic_counts": {"buffer_count": 5},
                    "job_counts": {},
                    "signals": {"youtube_trend_videos": 8, "google_trends_keywords": 2},
                    "ready_private_upload_count": 0,
                    "public_promotion_count": 0,
                    "blockers": [],
                    "next_action": "produce_private_render",
                }
            ],
        }

    monkeypatch.setattr(
        "atnos.services.project_readiness_service.MoneyMachineStatusService.build",
        _fake_money_status,
    )
    _seed_quality_review_blocked_job(app.state.settings, account_id="youtube:account-c5034bf4@example.invalid")

    with TestClient(app) as client:
        upsert_intelligence_account_config(
            app.state.settings,
            account_id="youtube:account-c5034bf4@example.invalid",
            niches=["personal finance"],
            region="US",
            youtube_oauth_account_id=50,
            channel_title="Wealth Blueprint",
            content_profile="wealth_blueprint",
        )
        response = client.get("/api/projects/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["youtube_quality_review_blocked"] == 1
    wealth = {item["project_id"]: item for item in payload["projects"]}["youtube:account-c5034bf4@example.invalid"]
    requirements = {item["id"]: item for item in wealth["requirements"]}
    assert requirements["quality_review_clear"]["status"] == "missing"
    assert "quality_review_blocked:1" in wealth["blockers"]
    assert "quality_review:thumbnail_quality:local_template_thumbnail" in wealth["blockers"]
    assert wealth["operator_decision"]["state"] == "blocked_public_quality"
    assert wealth["operator_decision"]["primary_endpoint"] == "/api/production/money-machine/run-next"
    assert "quality_review" in wealth["evidence"]
    assert wealth["evidence"]["quality_review"]["blocked_count"] == 1
    assert wealth["evidence"]["quality_review"]["latest_blocked"][0]["blockers"] == [
        "thumbnail_quality:local_template_thumbnail",
        "semantic_visual:generic_blur",
    ]
    assert wealth["evidence"]["quality_review"]["remediation_endpoint"] == "POST /api/production/quality-remediation/run-once"


def test_project_readiness_uses_latest_side_research_report(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    data_dir = app.state.settings.data_dir
    older_dir = data_dir / "learning" / "tooling-research" / "20260608T003659Z"
    newer_dir = data_dir / "learning" / "side_research"
    older_dir.mkdir(parents=True, exist_ok=True)
    newer_dir.mkdir(parents=True, exist_ok=True)
    older_report = older_dir / "atnos_tooling_research_report.md"
    older_report.write_text("old tooling research", encoding="utf-8")
    os.utime(older_report, (1_780_000_000, 1_780_000_000))
    side_report = newer_dir / "investment_faceless_tools_20260608.md"
    side_report.write_text("new side research", encoding="utf-8")

    def _fake_money_status(self, *, account_id=None, include_oauth=True):  # noqa: ANN001
        return {
            "status": "needs_private_render",
            "autopilot_enabled": False,
            "human_approval_before_upload": True,
            "public_upload_enabled": False,
            "global_blockers": [],
            "next_actions": ["produce_private_render"],
            "accounts": [
                {
                    "account_id": "youtube:account-c5034bf4@example.invalid",
                    "channel_title": "Wealth Blueprint",
                    "youtube_oauth": {"ready": True, "account_id": 50},
                    "topic_counts": {"buffer_count": 3},
                    "job_counts": {},
                    "signals": {"youtube_trend_videos": 5, "google_trends_keywords": 1},
                    "ready_private_upload_count": 0,
                    "public_promotion_count": 0,
                    "blockers": [],
                    "next_action": "produce_private_render",
                }
            ],
        }

    monkeypatch.setattr(
        "atnos.services.project_readiness_service.MoneyMachineStatusService.build",
        _fake_money_status,
    )

    with TestClient(app) as client:
        upsert_intelligence_account_config(
            app.state.settings,
            account_id="youtube:account-c5034bf4@example.invalid",
            niches=["personal finance"],
            region="US",
            youtube_oauth_account_id=50,
            channel_title="Wealth Blueprint",
            content_profile="wealth_blueprint",
        )
        response = client.get("/api/projects/readiness")

    assert response.status_code == 200
    by_id = {item["project_id"]: item for item in response.json()["projects"]}
    wealth_plan = by_id["youtube:account-c5034bf4@example.invalid"]["tooling_plan"]
    synthetic_plan = by_id["creator:synthetic-model-lab"]["tooling_plan"]
    investment_plan = by_id["investment:portfolio-alpha"]["tooling_plan"]
    for plan in (wealth_plan, synthetic_plan, investment_plan):
        assert plan["report_ref"].endswith("data/learning/side_research/investment_faceless_tools_20260608.md")
        assert plan["report_kind"] == "side_research"
        assert plan["report_updated_at"]


def test_project_readiness_uses_visual_backend_doctor_status(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    reports_dir = app.state.settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "visual_backend_doctor_test.json").write_text(
        """
        {
          "generated_at": "2026-06-08T12:00:00+00:00",
          "public_visual_ready": true,
          "selected_video_backend": "auto",
          "effective_video_backend": "wan",
          "next_action": "visual_backend_ready_generate_private_public_quality_short",
          "providers": {
            "comfyui": {
              "configured": true,
              "live_checked": true,
              "live_ready": true,
              "status": "ready",
              "error": null,
              "endpoint": "http://127.0.0.1:8188",
              "install_exists": true,
              "auto_launch_supported": true,
              "backend_public_quality": true
            }
          }
        }
        """,
        encoding="utf-8",
    )

    def _fake_money_status(self, *, account_id=None, include_oauth=True):  # noqa: ANN001
        return {
            "status": "needs_private_render",
            "autopilot_enabled": False,
            "human_approval_before_upload": True,
            "public_upload_enabled": False,
            "global_blockers": [],
            "next_actions": ["produce_private_render"],
            "accounts": [
                {
                    "account_id": "youtube:account-c5034bf4@example.invalid",
                    "channel_title": "Wealth Blueprint",
                    "youtube_oauth": {"ready": True, "account_id": 50},
                    "topic_counts": {"buffer_count": 3},
                    "job_counts": {},
                    "signals": {"youtube_trend_videos": 5, "google_trends_keywords": 1},
                    "ready_private_upload_count": 0,
                    "public_promotion_count": 0,
                    "blockers": [],
                    "next_action": "produce_private_render",
                }
            ],
        }

    monkeypatch.setattr(
        "atnos.services.project_readiness_service.MoneyMachineStatusService.build",
        _fake_money_status,
    )

    with TestClient(app) as client:
        upsert_intelligence_account_config(
            app.state.settings,
            account_id="youtube:account-c5034bf4@example.invalid",
            niches=["personal finance"],
            region="US",
            youtube_oauth_account_id=50,
            channel_title="Wealth Blueprint",
            content_profile="wealth_blueprint",
        )
        response = client.get("/api/projects/readiness")

    assert response.status_code == 200
    by_id = {item["project_id"]: item for item in response.json()["projects"]}
    tooling = {item["key"]: item for item in by_id["youtube:account-c5034bf4@example.invalid"]["tooling_plan"]["must_have"]}
    assert tooling["visual_backend"]["status"] == "ready"
    assert tooling["visual_backend"]["detail"] == "Live visual backend verified via wan."


def _seed_quality_review_blocked_job(settings, *, account_id: str) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step,
                quality_review_status, quality_review_result, quality_review_checked_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 'qa_passed', 'qa', 'quality_review_blocked', ?, ?, ?, ?)
            """,
            (
                "prod-quality-backlog",
                account_id,
                "topic-quality-backlog",
                json.dumps(
                    {
                        "status": "blocked",
                        "blockers": [
                            "thumbnail_quality:local_template_thumbnail",
                            "semantic_visual:generic_blur",
                        ],
                        "next_action": "regenerate_thumbnail_and_first_frame_with_topic_specific_visuals",
                    }
                ),
                now,
                now,
                now,
            ),
        )
        db.commit()
