from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
import sys
import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient

from atnos.intelligence.storage import connect, init_intelligence_db, json_dumps, utc_now_iso
from atnos.production.models import ApprovedTopic, ProductionContext, StepResult
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.steps import hook_generator, script_writer
from test_support import create_test_app


def _seed_approved_topic(settings, *, account_id: str = "acct-1", topic_id: str = "topic-1") -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'approved', 88, ?, ?)
            """,
            (topic_id, account_id, "Dlaczego Polacy traca pieniadze na inflacji", "Finanse osobiste PL", now, now),
        )
        db.commit()


def _seed_approved_topic_with_intelligence(settings) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, inspired_by, status, total_score,
                viral_potential, search_demand, competition_gap, trend_velocity,
                niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                created_at, updated_at
            )
            VALUES (
                'topic-intel', 'acct-1', 'inflacja oszczednosci',
                'Dlaczego inflacja zjada oszczednosci rodzin.', 'Jak inflacja niszczy twoje oszczednosci',
                'approved', 88, 22, 17, 12, 9, 10, 8, 0, 1, 1, ?, ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-intel-1', 'acct-1', 'finanse osobiste', 'PL',
                'inflacja oszczednosci: co robia Polacy', 900000, 32000, 700,
                ?, 'UC1', 'Finance Signal', 'https://img/1.jpg', ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'inflacja oszczednosci', 'now 7-d', 'PL', 83, ?, '[]', 'ready', ?
            )
            """,
            (json_dumps(["lokaty 2026", "realna inflacja"]), now),
        )
        db.commit()


def _patch_llm(monkeypatch) -> None:
    async def fake_hook(settings, prompt, *, timeout_s=30.0):
        return {
            "selected": {
                "variant": "question",
                "text": "Dlaczego inflacja zjada twoje pieniadze?",
                "opening_line": "Jedna decyzja chroni portfel bardziej niz kolejna obietnica banku.",
            },
            "variants": [],
        }

    async def fake_script(settings, prompt, *, timeout_s=30.0):
        return {
            "body": [
                "Inflacja nie zabiera pieniedzy naraz, tylko codziennie po trochu.",
                "Najpierw drozeje koszyk, potem rosnie presja na zle decyzje.",
                "Gotowka daje spokoj, ale za duzo gotowki traci sile.",
                "Najprostszy ruch to poduszka, plan i regularny przeglad kosztow.",
                "Nie chodzi o panike, tylko o kontrolowanie kilku liczb.",
                "Raz w miesiacu sprawdz, co realnie zostaje po wydatkach.",
            ],
            "cta": "Zapisz to i sprawdz swoj budzet dzis.",
        }

    monkeypatch.setattr(hook_generator, "ask_ollama_json", fake_hook)
    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_script)


def _patch_ready_production_review(monkeypatch) -> None:
    def fake_status(self, *, account_id: str, topic_id: str) -> dict:
        return {
            "account_id": account_id,
            "topic_id": topic_id,
            "has_review_plan": True,
            "ready_for_review": True,
            "ready_for_execution": False,
            "ready_for_human_review": True,
            "ready_for_production_start": True,
            "production_gate_blockers": [],
            "status": "planned",
            "reason": "Production review gate passed: all required review tasks are completed.",
            "root_task": {
                "id": 1,
                "status": "planned",
                "children": [{"id": 2, "status": "completed", "requires_review": True}],
            },
            "counts": {
                "matching_root_tasks": 1,
                "latest_child_tasks": 1,
                "latest_review_tasks": 1,
                "latest_completed_review_tasks": 1,
                "latest_queued_child_tasks": 0,
                "latest_running_child_tasks": 0,
                "latest_completed_child_tasks": 1,
                "latest_blocked_child_tasks": 0,
                "latest_failed_child_tasks": 0,
            },
            "next_action": "Review completed child task outputs; production can start.",
        }

    monkeypatch.setattr("atnos.api.routes.production.ProductionDelegationService.status", fake_status)


def test_script_writer_normalizes_ascii_currency_claims() -> None:
    assert script_writer._normalize_numeric_claim("12 000 zl") == "12000pln"
    assert script_writer._normalize_numeric_claim("4.8 EUR") == "4.8eur"
    assert script_writer._normalize_numeric_claim("5,4 proc.") == "5.4%"
    tokens = [
        script_writer._normalize_numeric_claim(token)
        for token in script_writer._numeric_claim_tokens("RPM 4.8 EUR, koszt 12 000 zl, CTR 5,4 proc.")
    ]
    assert "4.8eur" in tokens
    assert "12000pln" in tokens
    assert "5.4%" in tokens


def test_channel_profile_fallbacks_do_not_leak_finance_into_history(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings

    async def no_llm(*args, **kwargs):
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(hook_generator, "ask_ollama_json", no_llm)
    monkeypatch.setattr(script_writer, "ask_ollama_json", no_llm)
    context = ProductionContext(
        job_id="prod-history-fallback",
        account_id="yt-historia-pl",
        topic=ApprovedTopic(
            "topic-history",
            "yt-historia-pl",
            "Moment z historii Polski, o ktorym prawie nikt nie pamieta",
            "Ciekawostki historyczne PL",
            56,
        ),
        artifact_dir=tmp_path,
    )

    hook = asyncio.run(hook_generator.run(settings, context)).payload
    context.artifacts["hook"] = hook
    script = asyncio.run(script_writer.run(settings, context)).payload
    serialized = json.dumps(script, ensure_ascii=False).casefold()

    assert "historia" in serialized or "history" in serialized
    assert "finanse" not in serialized
    assert "pieniadze" not in serialized
    assert "premium finance" not in serialized
    assert script["tags"][1] == "historia"


def test_channel_profile_fallbacks_do_not_leak_finance_into_brainrot(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings

    async def no_llm(*args, **kwargs):
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(hook_generator, "ask_ollama_json", no_llm)
    monkeypatch.setattr(script_writer, "ask_ollama_json", no_llm)
    context = ProductionContext(
        job_id="prod-brainrot-fallback",
        account_id="yt-ai-brainrot-pl",
        topic=ApprovedTopic(
            "topic-brainrot",
            "yt-ai-brainrot-pl",
            "Absurdalna scena AI, ktora nie powinna miec sensu, ale ma",
            "AI brainrot PL",
            60,
        ),
        artifact_dir=tmp_path,
    )

    hook = asyncio.run(hook_generator.run(settings, context)).payload
    context.artifacts["hook"] = hook
    script = asyncio.run(script_writer.run(settings, context)).payload
    serialized = json.dumps(script, ensure_ascii=False).casefold()

    assert "brainrot" in serialized or "absurd" in serialized
    assert "finanse" not in serialized
    assert "pieniadze" not in serialized
    assert "premium finance" not in serialized
    assert "brainrot" in script["tags"]


class _FakePreflightReport:
    ready_to_start_local_job = True

    @staticmethod
    def to_dict() -> dict:
        return {
            "ready_to_start_local_job": True,
            "ready_for_private_upload": False,
            "blocking": [],
            "warnings": ["private_upload_not_ready"],
        }


async def _fake_ready_preflight(self, *, account_id: str, topic_id: str):
    return _FakePreflightReport()


def test_script_writer_injects_topic_intelligence_and_style_guide(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic_with_intelligence(settings)
    style_dir = settings.data_dir / "channel_ops" / "channels" / "acct-1"
    style_dir.mkdir(parents=True)
    (style_dir / "style_guide.md").write_text("Ton: konkretny, bez obietnic zarobku.", encoding="utf-8")
    captured: dict[str, str] = {}

    async def fake_script(settings, prompt, *, timeout_s=30.0):
        captured["prompt"] = prompt
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "Inflacja zjada oszczednosci.", "duration_target_sec": 3, "visual_note": "hook"},
                {"id": "s02", "type": "intro", "text": "Zobacz prosty filtr decyzji.", "duration_target_sec": 5, "visual_note": "intro"},
                {"id": "s03", "type": "body", "text": "Najpierw sprawdz realna sile gotowki.", "duration_target_sec": 7, "visual_note": "body"},
                {"id": "s04", "type": "body", "text": "Potem rozdziel poduszke od inwestowania.", "duration_target_sec": 7, "visual_note": "body"},
                {"id": "s05", "type": "cta", "text": "Zapisz ten schemat.", "duration_target_sec": 5, "visual_note": "cta"},
            ],
            "title_options": ["Inflacja i oszczednosci"],
            "description_draft": "Opis bez obietnic zarobku.",
            "tags": ["finanse", "inflacja"],
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_script)
    context = ProductionContext(
        job_id="prod-script-test",
        account_id="acct-1",
        topic=ApprovedTopic("topic-intel", "acct-1", "inflacja oszczednosci", "Opis", 88),
        artifact_dir=tmp_path,
        artifacts={
            "hook": {
                "selected": {
                    "text": "Inflacja zjada oszczednosci.",
                    "opening_line": "To nie jest teoria, tylko codzienny koszt.",
                }
            }
        },
    )

    result = asyncio.run(script_writer.run(settings, context))

    prompt = captured["prompt"]
    assert "TOPIC INTELLIGENCE FROM SQLITE intelligence.db" in prompt
    assert "topic_score: 88" in prompt
    assert "youtube_examples" in prompt
    assert "google_trends" in prompt
    assert "CHANNEL STYLE GUIDE FROM OBSIDIAN / MEMORY" in prompt
    assert result.payload["topic_intelligence"]["used"] is True
    assert result.payload["topic_intelligence"]["source_count"] >= 2
    assert result.payload["style_guide"]["used"] is True


def test_script_writer_uses_inspired_by_when_topic_title_differs_from_signal(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, inspired_by, status, total_score,
                viral_potential, search_demand, competition_gap, trend_velocity,
                niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                created_at, updated_at
            )
            VALUES (
                'topic-inspired', 'acct-1', 'lokaty 2026',
                'Co banki robia z twoja gotowka.', 'inflacja oszczednosci',
                'approved', 76, 18, 16, 8, 6, 10, 7, 0, 0, 1, ?, ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-inspired-1', 'acct-1', 'finanse osobiste', 'PL',
                'Jak inflacja niszczy twoje oszczednosci', 900000, 30000, 700,
                ?, 'UC1', 'Finance Signal', 'https://img/1.jpg', ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES (
                'inflacja oszczednosci', 'now 7-d', 'PL', 83, ?, '[]', 'ready', ?
            )
            """,
            (json_dumps([{"query": "lokaty 2026", "value": 450}]), now),
        )
        db.commit()

    captured: dict[str, str] = {}

    async def fake_script(settings, prompt, *, timeout_s=30.0):
        captured["prompt"] = prompt
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "Lokaty nie zawsze chronia gotowke.", "duration_target_sec": 3, "visual_note": "hook"},
                {"id": "s02", "type": "intro", "text": "Najpierw sprawdz realna inflacje.", "duration_target_sec": 5, "visual_note": "intro"},
                {"id": "s03", "type": "body", "text": "Bank pokazuje procent, ale ceny pokazuja sile pieniadza.", "duration_target_sec": 7, "visual_note": "body"},
                {"id": "s04", "type": "body", "text": "Porownaj oprocentowanie z tym, co realnie kupujesz.", "duration_target_sec": 7, "visual_note": "body"},
                {"id": "s05", "type": "cta", "text": "Zapisz ten filtr.", "duration_target_sec": 5, "visual_note": "cta"},
            ],
            "title_options": ["Lokaty 2026"],
            "description_draft": "Opis edukacyjny.",
            "tags": ["finanse", "lokaty"],
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_script)
    context = ProductionContext(
        job_id="prod-script-inspired",
        account_id="acct-1",
        topic=ApprovedTopic("topic-inspired", "acct-1", "lokaty 2026", "Opis", 76),
        artifact_dir=tmp_path,
        artifacts={
            "hook": {
                "selected": {
                    "text": "Lokaty nie zawsze chronia gotowke.",
                    "opening_line": "Procent z reklamy to nie cala historia.",
                }
            }
        },
    )

    result = asyncio.run(script_writer.run(settings, context))

    assert result.payload["topic_intelligence"]["used"] is True
    assert result.payload["topic_intelligence"]["source_count"] >= 2
    assert "Jak inflacja niszczy twoje oszczednosci" in captured["prompt"]
    assert "lokaty 2026" in captured["prompt"]


def test_production_orchestrator_blocks_private_upload_for_placeholder_video(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    _patch_llm(monkeypatch)
    events: list[dict] = []

    async def run() -> dict:
        orchestrator = ProductionOrchestrator(settings, event_sink=events.append)
        return await orchestrator.start(account_id="acct-1", topic_id="topic-1")

    result = asyncio.run(run())
    assert result["status"] == "qa_failed"
    assert result["upload_package_path"].endswith("youtube_private_upload_package.json")
    assert Path(result["upload_package_path"]).exists()
    assert Path(result["qa_report_path"]).exists()
    upload_package = json.loads(Path(result["upload_package_path"]).read_text(encoding="utf-8"))
    standards_path = Path(upload_package["operating_standards_path"])
    playbook_path = Path(upload_package["faceless_playbook_path"])
    assert standards_path.exists()
    assert playbook_path.exists()
    assert upload_package["operating_standards"]["used"] is True
    assert upload_package["operating_standards"]["section_keys"] == ["shorts_quality"]
    assert upload_package["faceless_playbook"]["used"] is True
    assert upload_package["faceless_playbook"]["template_key"] in {
        "science_explainer",
        "mystery_documentary",
        "shorts_microstory",
    }
    assert Path(upload_package["video_file_path"]).exists()
    assert Path(upload_package["thumbnail_file_path"]).exists()
    assert upload_package["thumbnail_file_path"].endswith("thumbnail_v1.png")
    assert Path(upload_package["subtitle_file_path"]).exists()
    assert upload_package["upload_metadata"]["visibility"] == "private"
    assert upload_package["auto_uploadable"] is False
    assert upload_package["upload_decision"]["status"] == "blocked"
    assert upload_package["upload_decision"]["private_upload_allowed"] is False
    assert "Final MP4 present" in upload_package["upload_decision"]["required_failures"]
    assert any(item["name"] == "Final MP4 present" for item in upload_package["blocking_failure_details"])
    assert any(event["type"] == "pipeline_step" and event["step"] == "done" for event in events)
    done_event = next(event for event in events if event["type"] == "pipeline_step" and event["step"] == "done")
    assert done_event["status"] == "qa_failed"
    assert "private upload is blocked" in done_event["message"]
    assert any(event["type"] == "pipeline_step" and event["step"] == "video" for event in events)

    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-1'").fetchone()
        assert topic["status"] == "approved"
        steps = db.execute(
            "SELECT COUNT(*) AS count FROM pipeline_job_steps WHERE job_id = ?",
            (result["id"],),
        ).fetchone()
        assert steps["count"] == 9
        job = db.execute(
            "SELECT hooks, selected_hook_id, script, audio_timestamps, asset_plan FROM production_jobs WHERE id = ?",
            (result["id"],),
        ).fetchone()
        hooks = json.loads(job["hooks"])
        assert len(hooks["variants"]) == 3
        assert job["selected_hook_id"] == "h02"
        assert json.loads(job["script"])["segments"][0]["type"] == "hook"
        assert json.loads(job["script"])["faceless_playbook"]["used"] is True
        assert json.loads(job["audio_timestamps"])["timestamps"]
        assert json.loads(job["asset_plan"])["scenes"]
        assert json.loads(job["asset_plan"])["faceless_playbook"]["used"] is True
        video_manifest = Path(result["artifact_dir"]) / "video_manifest.json"
        assert video_manifest.exists()
        manifest = json.loads(video_manifest.read_text(encoding="utf-8"))
        assert manifest["video_raw_path"]
        assert manifest["fallback_used"] is True
        qa = json.loads(Path(result["qa_report_path"]).read_text(encoding="utf-8"))
        assert "Final MP4 present" in qa["blocking_failures"]
        assert qa["operating_standards"]["used"] is True
        assert qa["operating_standards"]["required_count"] >= 4


def test_production_start_endpoint_queues_background_job(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    _patch_llm(monkeypatch)
    _patch_ready_production_review(monkeypatch)
    slack_events: list[dict] = []

    from atnos.services.slack_webhook_notifier import SlackWebhookNotifier

    def fake_notify(self, event: dict):
        slack_events.append(event)
        return {"status": "sent"}

    monkeypatch.setattr(SlackWebhookNotifier, "notify_pipeline_event", fake_notify)
    monkeypatch.setattr(
        "atnos.api.routes.production.ProductionPreflightService.check",
        _fake_ready_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/start",
            json={"account_id": "acct-1", "topic_id": "topic-1"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["preflight"]["ready_to_start_local_job"] is True

        deadline = time.time() + 5
        status = "queued"
        while time.time() < deadline:
            with connect(settings) as db:
                row = db.execute("SELECT status FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
                status = row["status"]
                if status == "qa_failed":
                    break
            time.sleep(0.05)
        assert status == "qa_failed"

        response = client.get(f"/api/production/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == job_id
        assert payload["topic_title"] == "Dlaczego Polacy traca pieniadze na inflacji"
        assert payload["ready_for_review"] is True
        assert payload["ready_for_private_upload"] is False
        assert payload["review_status"] == "qa_review_required"
        assert "qa_not_passed" in payload["private_upload_blockers"]
        assert "qa_not_auto_uploadable" in payload["private_upload_blockers"]
        assert "qa_blocking_failures" in payload["private_upload_blockers"]
        assert any(step["step"] == "qa" and step["status"] == "done" for step in payload["steps"])

        response = client.get("/api/production/jobs", params={"account_id": "acct-1"})
        assert response.status_code == 200
        assert response.json()["jobs"][0]["id"] == job_id

    final_slack_events = [
        event for event in slack_events if event.get("step") == "done" and event.get("status") == "qa_failed"
    ]
    assert len(final_slack_events) == 1
    ws_events = getattr(app.state, "production_ws_events", [])
    assert any(event.get("step") == "done" and event.get("status") == "qa_failed" for event in ws_events)


def test_production_start_endpoint_blocks_when_preflight_fails(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)

    class _BlockedPreflight:
        ready_to_start_local_job = False

        @staticmethod
        def to_dict() -> dict:
            return {
                "ready_to_start_local_job": False,
                "blocking": ["local_production_not_ready"],
                "warnings": ["private_upload_not_ready"],
            }

    async def fake_blocked_preflight(self, *, account_id: str, topic_id: str):
        return _BlockedPreflight()

    monkeypatch.setattr(
        "atnos.api.routes.production.ProductionPreflightService.check",
        fake_blocked_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/start",
            json={"account_id": "acct-1", "topic_id": "topic-1"},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == "Production preflight failed; job was not queued."
    assert detail["preflight"]["blocking"] == ["local_production_not_ready"]
    with connect(settings) as db:
        count = db.execute("SELECT COUNT(*) AS count FROM pipeline_jobs").fetchone()["count"]
    assert count == 0


def test_production_start_endpoint_blocks_when_review_gate_fails(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    monkeypatch.setattr(
        "atnos.api.routes.production.ProductionPreflightService.check",
        _fake_ready_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/start",
            json={"account_id": "acct-1", "topic_id": "topic-1"},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == "Production review gate failed; job was not queued."
    assert detail["production_review"]["ready_for_production_start"] is False
    assert "production_review_plan_missing" in detail["production_review"]["production_gate_blockers"]
    with connect(settings) as db:
        count = db.execute("SELECT COUNT(*) AS count FROM pipeline_jobs").fetchone()["count"]
    assert count == 0


def test_production_regenerate_endpoint_queues_fresh_job_without_upload(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES ('prod-old-visual', 'acct-1', 'topic-1', 'qa_passed', 'upload_package', ?, ?, ?)
            """,
            (str(tmp_path / "old"), utc_now_iso(), utc_now_iso()),
        )
        db.execute("UPDATE topic_queue SET status = 'done' WHERE id = 'topic-1'")
        db.commit()

    async def fake_run_job(self, context):
        return {"id": context.job_id, "status": "queued_for_test"}

    monkeypatch.setattr(ProductionOrchestrator, "run_job", fake_run_job)

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-old-visual/regenerate",
            json={"reason": "scene_concreteness_risk_for_public"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["source_job_id"] == "prod-old-visual"
    assert payload["job_id"].startswith("prod-")
    assert payload["job_id"] != "prod-old-visual"


    assert payload["message"].endswith("No upload or public publication was executed.")
    with connect(settings) as db:
        new_job = db.execute("SELECT account_id, topic_id, status FROM pipeline_jobs WHERE id = ?", (payload["job_id"],)).fetchone()
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-1'").fetchone()
    assert dict(new_job) == {"account_id": "acct-1", "topic_id": "topic-1", "status": "queued"}
    assert topic["status"] == "in_production"
    ws_events = getattr(app.state, "production_ws_events", [])
    assert any(event.get("type") == "pipeline_regeneration" and event.get("source_job_id") == "prod-old-visual" for event in ws_events)


def test_production_review_package_exposes_only_owner_deliverables(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    artifact_dir = tmp_path / "review-job"
    (artifact_dir / "assets").mkdir(parents=True)
    (artifact_dir / "thumbnail").mkdir()
    (artifact_dir / "assets" / "final_short.mp4").write_bytes(b"video-review")
    (artifact_dir / "thumbnail" / "thumbnail_v1.png").write_bytes(b"thumbnail-review")
    (artifact_dir / "youtube_private_upload_package.json").write_text(
        json.dumps(
            {
                "upload_metadata": {
                    "title": "Time beats hype",
                    "description": "A complete publishing description.",
                    "tags": ["shorts", "compound interest"],
                }
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "qa_report.json").write_text(
        json.dumps({"blocking_failures": ["Production voice approval"]}),
        encoding="utf-8",
    )
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            ) VALUES ('prod-review', 'acct-1', 'topic-1', 'qa_failed', 'upload_package', ?, ?, ?)
            """,
            (str(artifact_dir), utc_now_iso(), utc_now_iso()),
        )
        db.commit()

    with TestClient(app) as client:
        response = client.get("/api/production/jobs/prod-review/review-package")
        assert response.status_code == 200
        payload = response.json()
        assert payload["title"] == "Time beats hype"
        assert payload["description"] == "A complete publishing description."
        assert payload["tags"] == ["shorts", "compound interest"]
        assert payload["video_ready"] is True
        assert payload["thumbnail_ready"] is True
        assert payload["blocking_failures"] == ["Production voice approval"]
        assert "artifact_dir" not in payload

        video = client.get(payload["video_url"])
        thumbnail = client.get(payload["thumbnail_url"])
        assert video.status_code == 200
        assert video.content == b"video-review"
        assert thumbnail.status_code == 200
        assert thumbnail.content == b"thumbnail-review"

        missing = client.get("/api/production/jobs/missing/review-package")
        assert missing.status_code == 404


def test_production_regenerate_endpoint_blocks_duplicate_active_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES ('prod-old-visual', 'acct-1', 'topic-1', 'qa_passed', 'upload_package', ?, ?, ?)
            """,
            (str(tmp_path / "old"), now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES ('prod-active', 'acct-1', 'topic-1', 'running', 'video', ?, ?, ?)
            """,
            (str(tmp_path / "active"), now, now),
        )
        db.commit()

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-old-visual/regenerate")

    assert response.status_code == 409
    assert response.json()["detail"]["active_job_id"] == "prod-active"


def test_production_orchestrator_restores_topic_to_approved_on_step_error(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)

    async def failing_hook(_settings, _context):
        raise RuntimeError("hook generator down")

    monkeypatch.setattr("atnos.production.orchestrator.hook_generator.run", failing_hook)

    result = asyncio.run(ProductionOrchestrator(settings, max_retries=1).start(account_id="acct-1", topic_id="topic-1"))

    assert result["status"] == "error"
    assert result["current_step"] == "hook"
    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-1'").fetchone()

    assert topic["status"] == "approved"


def test_production_orchestrator_sanitizes_real_account_id_for_artifact_dir(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings, account_id="youtube:account-c5034bf4@example.invalid", topic_id="topic-real-account")

    context = asyncio.run(
        ProductionOrchestrator(settings, max_retries=1).prepare(
            account_id="youtube:account-c5034bf4@example.invalid",
            topic_id="topic-real-account",
        )
    )

    assert context.account_id == "youtube:account-c5034bf4@example.invalid"
    assert context.artifact_dir.exists()
    assert ":" not in context.artifact_dir.name
    assert ":" not in context.artifact_dir.parent.name
    assert context.artifact_dir.parent.name == "youtube-atnos-int.pl"
    with connect(settings) as db:
        row = db.execute("SELECT account_id, artifact_dir FROM pipeline_jobs WHERE id = ?", (context.job_id,)).fetchone()
    assert row["account_id"] == "youtube:account-c5034bf4@example.invalid"
    assert row["artifact_dir"] == str(context.artifact_dir)


def test_production_orchestrator_emits_retrying_event_for_transient_step_failure(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    events: list[dict] = []
    attempts = {"hook": 0}

    async def flaky_hook(_settings, context):
        attempts["hook"] += 1
        if attempts["hook"] == 1:
            raise RuntimeError("temporary ollama outage")
        artifact_path = context.artifact_dir / "hook_retry.json"
        artifact_path.write_text("{}", encoding="utf-8")
        return StepResult(
            artifact_name="hook",
            artifact_path=artifact_path,
            message="hook generated after retry",
            payload={
                "selected_hook_id": "h01",
                "selected": {"id": "h01", "text": "Retry hook"},
                "variants": [{"id": "h01", "text": "Retry hook"}],
            },
        )

    monkeypatch.setattr("atnos.production.orchestrator.hook_generator.run", flaky_hook)

    context = asyncio.run(ProductionOrchestrator(settings, event_sink=events.append, max_retries=2).prepare(account_id="acct-1", topic_id="topic-1"))
    result = asyncio.run(
        ProductionOrchestrator(settings, event_sink=events.append, max_retries=2)._run_step_with_retries(
            context,
            "hook",
            hook_generator.run,
            0.0,
        )
    )

    assert result.payload["selected_hook_id"] == "h01"
    assert attempts["hook"] == 2
    assert any(event["step"] == "hook" and event["status"] == "retrying" for event in events)
    assert any(event["step"] == "hook" and event["status"] == "done" for event in events)
    with connect(settings) as db:
        step = db.execute(
            "SELECT status, attempt, message FROM pipeline_job_steps WHERE job_id = ? AND step = 'hook'",
            (context.job_id,),
        ).fetchone()
    assert step["status"] == "done"
    assert step["attempt"] == 2
    assert step["message"] == "hook generated after retry"


def test_production_orchestrator_does_not_retry_f5_timeout(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    events: list[dict] = []
    attempts = 0

    async def timed_out_voice(_settings, _context):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("F5-TTS Gradio client timed out after 600s.")

    context = asyncio.run(ProductionOrchestrator(settings, max_retries=3).prepare(account_id="acct-1", topic_id="topic-1"))
    with pytest.raises(RuntimeError, match="voice failed after 1 attempts"):
        asyncio.run(
            ProductionOrchestrator(settings, event_sink=events.append, max_retries=3)._run_step_with_retries(
                context,
                "voice",
                timed_out_voice,
                0.22,
            )
        )

    assert attempts == 1
    assert not any(event["step"] == "voice" and event["status"] == "retrying" for event in events)


def test_production_orchestrator_auto_repairs_repairable_visual_qa_failure(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    events: list[dict] = []

    async def fake_visuals(_settings, context):
        payload = {
            "visual_style": "test",
            "scenes": [
                {
                    "scene_id": 0,
                    "visual_prompt": "faceless person sorting receipts, cash, credit card, and a glowing budget app on a dark premium desk",
                    "visual_subject": "faceless person sorting receipts, cash, credit card, and phone on desk",
                    "motion_style": "slow camera push",
                    "headline": "MONEY LEAK",
                }
            ],
            "repair": context.artifacts.get("visual_repair_request"),
        }
        path = context.artifact_dir / "visual_plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return StepResult("visuals", path, payload, "visual plan repaired")

    async def fake_video(_settings, context):
        payload = {
            "status": "ready",
            "fallback_used": False,
            "clips": [{"status": "generated", "backend": "openai_image_motion"}],
            "quality_plan": {
                "generated_clip_count": 3,
                "placeholder_clip_count": 0,
                "cached_fallback_clip_count": 0,
                "non_generated_clip_count": 0,
                "min_real_clips": 3,
                "passes_visual_variety_floor": True,
                "passes_visual_relevance_floor": True,
                "passes_scene_concreteness_floor": True,
                "scene_concreteness_risk_count": 0,
                "passes_generated_image_text_floor": True,
                "generated_image_text_risk_count": 0,
                "passes_generated_image_visual_floor": True,
                "generated_image_visual_risk_count": 0,
            },
        }
        path = context.artifact_dir / "video_manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return StepResult("video", path, payload, "video repaired")

    async def fake_assembly(_settings, context):
        payload = {"output_file": str(context.artifact_dir / "final.mp4"), "subtitle_file": str(context.artifact_dir / "subtitles.srt")}
        path = context.artifact_dir / "assembly.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return StepResult("assembly", path, payload, "assembly repaired")

    async def fake_thumbnail(_settings, context):
        payload = {"thumbnail_path": str(context.artifact_dir / "thumbnail.png"), "status": "generated"}
        path = context.artifact_dir / "thumbnail.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return StepResult("thumbnail", path, payload, "thumbnail repaired")

    async def fake_qa(_settings, context):
        payload = {"overall": "passed", "auto_uploadable": True, "blocking_failures": []}
        path = context.artifact_dir / "qa_report.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return StepResult("qa", path, payload, "qa repaired")

    monkeypatch.setattr("atnos.production.orchestrator.asset_planner.run", fake_visuals)
    monkeypatch.setattr("atnos.production.orchestrator.video_generator.run", fake_video)
    monkeypatch.setattr("atnos.production.orchestrator.assembler.run", fake_assembly)
    monkeypatch.setattr("atnos.production.orchestrator.thumbnail_generator.run", fake_thumbnail)
    monkeypatch.setattr("atnos.production.orchestrator.quality_gate.run", fake_qa)
    guarded_steps: list[str] = []
    original_runtime_guard = ProductionOrchestrator._run_step_with_runtime_guard

    async def record_runtime_guard(self, context, step_name, runner, progress_base):
        guarded_steps.append(step_name)
        return await original_runtime_guard(self, context, step_name, runner, progress_base)

    monkeypatch.setattr(ProductionOrchestrator, "_run_step_with_runtime_guard", record_runtime_guard)

    context = asyncio.run(ProductionOrchestrator(settings, event_sink=events.append, max_retries=1).prepare(account_id="acct-1", topic_id="topic-1"))
    context.artifacts["script"] = {"segments": [{"text": "Inflacja zjada budzet.", "visual_note": "receipts and cash"}]}
    context.artifacts["qa"] = {
        "overall": "failed",
        "auto_uploadable": False,
        "blocking_failures": ["Scene concreteness"],
    }
    assert ProductionOrchestrator(settings)._qa_needs_visual_repair(
        {
            "auto_uploadable": False,
            "blocking_failures": ["Semantic storyboard quality"],
        },
        context,
    ) is True

    assert ProductionOrchestrator(settings)._qa_needs_visual_repair(
        {
            "auto_uploadable": False,
            "blocking_failures": ["Semantic storyboard quality"],
            "checks": [
                {
                    "name": "Semantic storyboard quality",
                    "status": "fail",
                    "value": {"status": "unavailable", "blockers": ["semantic_visual_audit_unavailable"]},
                }
            ],
        },
        context,
    ) is False

    assert ProductionOrchestrator(settings)._qa_needs_visual_repair(
        {
            "auto_uploadable": False,
            "blocking_failures": ["Visual regression baseline"],
            "checks": [
                {
                    "name": "Visual regression baseline",
                    "status": "fail",
                    "value": {"status": "not_configured"},
                }
            ],
        },
        context,
    ) is False

    result = asyncio.run(
        ProductionOrchestrator(settings, event_sink=events.append, max_retries=1)._repair_visual_quality(
            context,
            context.artifacts["qa"],
        )
    )

    assert result.payload["auto_uploadable"] is True
    assert context.artifacts["visual_auto_repair_done"] is True
    assert context.artifacts["visuals"]["repair"]["reason"] == "qa_visual_quality_failed"
    assert guarded_steps == ["visuals", "video", "assembly", "thumbnail", "qa"]
    assert any(event["step"] == "visual_repair" and event["status"] == "done" for event in events)
    with connect(settings) as db:
        row = db.execute("SELECT asset_plan, video_manifest, qa_report FROM production_jobs WHERE id = ?", (context.job_id,)).fetchone()
    assert json.loads(row["asset_plan"])["repair"]["reason"] == "qa_visual_quality_failed"
    assert json.loads(row["video_manifest"])["quality_plan"]["passes_scene_concreteness_floor"] is True
    assert json.loads(row["qa_report"])["auto_uploadable"] is True


def test_video_step_timeout_covers_multi_candidate_quality_runtime() -> None:
    import atnos.production.orchestrator as orchestrator_module

    assert orchestrator_module.PRODUCTION_STEP_TIMEOUTS_S["video"] >= 1800.0


def test_production_orchestrator_times_out_hanging_step(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    events: list[dict] = []
    import atnos.production.orchestrator as orchestrator_module

    monkeypatch.setitem(orchestrator_module.PRODUCTION_STEP_TIMEOUTS_S, "hook", 0.05)

    async def hanging_hook(_settings, _context):
        await asyncio.sleep(1.0)
        raise AssertionError("hanging step should be cancelled by orchestrator timeout")

    context = asyncio.run(
        ProductionOrchestrator(settings, event_sink=events.append, max_retries=1).prepare(
            account_id="acct-1",
            topic_id="topic-1",
        )
    )
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(
            ProductionOrchestrator(settings, event_sink=events.append, max_retries=1)._run_step_with_retries(
                context,
                "hook",
                hanging_hook,
                0.0,
            )
        )

    assert "hook exceeded" in str(exc.value)
    assert "timeout" in str(exc.value)
    with connect(settings) as db:
        step = db.execute(
            "SELECT status, attempt, message FROM pipeline_job_steps WHERE job_id = ? AND step = 'hook'",
            (context.job_id,),
        ).fetchone()
    assert step["status"] == "error"
    assert step["attempt"] == 1
    assert "timeout" in step["message"]
    assert any(event["step"] == "hook" and event["status"] == "running" for event in events)


def test_production_orchestrator_heartbeats_during_long_running_step(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    events: list[dict] = []
    import atnos.production.orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "PRODUCTION_STEP_HEARTBEAT_INTERVAL_S", 0.01)
    orchestrator = ProductionOrchestrator(settings, event_sink=events.append, max_retries=1)
    context = asyncio.run(orchestrator.prepare(account_id="acct-1", topic_id="topic-1"))
    touch_calls: list[str] = []
    original_touch = orchestrator._touch_running_step

    def recording_touch(job_id: str, step_name: str) -> None:
        touch_calls.append(step_name)
        original_touch(job_id, step_name)

    monkeypatch.setattr(orchestrator, "_touch_running_step", recording_touch)

    async def slow_voice(_settings, context):
        await asyncio.sleep(0.05)
        with connect(settings) as db:
            job = db.execute(
                "SELECT status, current_step, updated_at FROM pipeline_jobs WHERE id = ?",
                (context.job_id,),
            ).fetchone()
        assert job["status"] == "running"
        assert job["current_step"] == "voice"
        return StepResult(
            artifact_name="voice",
            artifact_path=context.artifact_dir / "audio_timestamps.json",
            message="voice generated",
            payload={"status": "generated"},
        )

    result = asyncio.run(orchestrator._run_step_with_retries(context, "voice", slow_voice, 0.22))

    assert result.message == "voice generated"
    assert touch_calls.count("voice") >= 2


def test_production_orchestrator_scales_video_timeout_for_ltx_multi_scene_render(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.comfyui_generation_timeout_s = 180
    context = ProductionContext(
        job_id="prod-timeout-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Why most people stay broke after a raise",
            description="",
            total_score=88,
        ),
        artifact_dir=tmp_path / "job",
        artifacts={
            "visuals": {
                "scenes": [
                    {"scene_id": 0, "visual_prompt": "scene one"},
                    {"scene_id": 1, "visual_prompt": "scene two"},
                    {"scene_id": 2, "visual_prompt": "scene three"},
                ]
            }
        },
    )

    orchestrator = ProductionOrchestrator(settings, max_retries=1)

    assert orchestrator._step_timeout_s(context, "video") == 3900.0
    assert orchestrator._step_timeout_s(context, "hook") == 90.0


def test_production_orchestrator_assembly_timeout_covers_renderer_budget(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    orchestrator = ProductionOrchestrator(app.state.settings)
    context = ProductionContext(
        job_id="prod-assembly-timeout-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="A topic",
            description="",
            total_score=80,
        ),
        artifact_dir=tmp_path / "job",
        artifacts={},
    )

    assert orchestrator._step_timeout_s(context, "assembly") > 900.0
    assert orchestrator._step_error_is_retryable(
        "assembly",
        "assembly exceeded 1020.0s timeout",
    ) is False


def test_production_orchestrator_scales_voice_timeout_for_multi_segment_f5_render(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    context = ProductionContext(
        job_id="prod-voice-timeout-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Why most people stay broke after a raise",
            description="",
            total_score=88,
        ),
        artifact_dir=tmp_path / "job",
        artifacts={
            "script": {
                "segments": [{"id": f"s{index:02d}", "text": "line"} for index in range(1, 10)]
            }
        },
    )

    orchestrator = ProductionOrchestrator(settings, max_retries=1)

    assert orchestrator._step_timeout_s(context, "voice") == 2010.0


def test_production_orchestrator_switches_gpu_runtime_between_voice_and_video(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_tts_backend = "f5_tts_http"
    context = ProductionContext(
        job_id="prod-runtime-switch",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="A topic",
            description="",
            total_score=80,
        ),
        artifact_dir=tmp_path / "job",
        artifacts={},
    )
    context.artifact_dir.mkdir(parents=True)
    orchestrator = ProductionOrchestrator(settings, max_retries=1)

    monkeypatch.setattr(
        "atnos.production.orchestrator.ComfyUiRuntimeService.stop_server",
        lambda self, **kwargs: {"status": "stopped", "stopped": True},
    )
    stopped_f5 = type(
        "StoppedF5",
        (),
        {"to_dict": lambda self: {"endpoint_ready": False, "stopped": True}},
    )()
    monkeypatch.setattr(
        "atnos.production.orchestrator.F5TTSRuntimeService.stop",
        lambda self, **kwargs: stopped_f5,
    )
    monkeypatch.setattr(
        "atnos.production.orchestrator.HardwareGuardService.release_gpu_pressure",
        lambda self: {
            "status": "released",
            "message": "released",
            "stopped_models": ["qwen3:14b"],
            "active_models_before": ["qwen3:14b"],
            "active_models_after": [],
            "verified_released": True,
        },
    )

    asyncio.run(orchestrator._prepare_gpu_runtime_for_step(context, "voice"))
    asyncio.run(orchestrator._release_gpu_runtime_after_step(context, "voice"))
    asyncio.run(orchestrator._prepare_gpu_runtime_for_step(context, "video"))
    asyncio.run(orchestrator._release_gpu_runtime_after_step(context, "thumbnail"))

    transitions = context.artifacts["runtime_transitions"]
    assert [item["action"] for item in transitions] == [
        "ollama_release_before_gpu_step",
        "comfyui_stop_before_f5",
        "f5_stop_after_voice",
        "ollama_release_before_gpu_step",
        "f5_stop_before_comfyui",
        "comfyui_stop_before_semantic_qa",
    ]
    assert (context.artifact_dir / "runtime_transitions.json").exists()


def test_select_hook_endpoint_updates_stored_hook(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_approved_topic(settings)
    _patch_llm(monkeypatch)
    _patch_ready_production_review(monkeypatch)
    monkeypatch.setattr(
        "atnos.api.routes.production.ProductionPreflightService.check",
        _fake_ready_preflight,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/start",
            json={"account_id": "acct-1", "topic_id": "topic-1"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        response = client.patch(f"/api/production/jobs/{job_id}/hook", json={"hook_id": "h01"})
        assert response.status_code == 200
        assert response.json()["selected_hook_id"] == "h01"

    with connect(settings) as db:
        row = db.execute("SELECT selected_hook_id, hooks FROM production_jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["selected_hook_id"] == "h01"
        assert json.loads(row["hooks"])["selection_mode"] == "user_selected"
