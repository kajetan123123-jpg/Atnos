from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_support import create_test_app  # noqa: E402


def test_visual_backend_doctor_blocks_recent_openai_billing_failure(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_video_backend = "auto"
    settings.shorts_allow_cloud_visuals = True
    error_path = settings.data_dir / "production" / "acct-1" / "job-1" / "video" / "s01.openai_errors.json"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    error_path.write_text(
        json.dumps(
            {
                "errors": [
                    {
                        "model": "gpt-image-1",
                        "status_code": 400,
                        "reason": "billing_hard_limit_reached: Billing hard limit has been reached.",
                    }
                ],
                "fallback": "local_comfyui_or_motion_graphic",
            }
        ),
        encoding="utf-8",
    )

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            raise RuntimeError("ComfyUI unavailable in test")

    monkeypatch.setattr(visual_backend_doctor, "_openai_image_motion_ready", lambda _settings: True)
    monkeypatch.setattr(visual_backend_doctor.httpx, "AsyncClient", FailingAsyncClient)

    report = asyncio.run(visual_backend_doctor.build_visual_backend_doctor_report(settings))

    assert report["public_visual_ready"] is False
    assert report["effective_video_backend"] == "auto"
    assert report["providers"]["comfyui"]["live_ready"] is False
    assert report["providers"]["openai_image_motion"]["live_ready"] is False
    assert report["providers"]["openai_image_motion"]["status"] == "blocked_recent_failure"
    assert "billing_hard_limit" in report["providers"]["openai_image_motion"]["error"]
    assert report["providers"]["openai_image_motion"]["backend_public_quality"] is True
    assert report["evidence"]["openai_image_motion_fallback_ready"] is False
    assert report["evidence"]["openai_image_motion_generation"]["status"] == "blocked_recent_failure"
    assert report["next_action"] == "run_wan_ltx_smoke_render_or_fix_openai_billing"


def test_visual_backend_doctor_accepts_recent_openai_success_as_auto_public_fallback(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_video_backend = "auto"
    settings.shorts_allow_cloud_visuals = True
    image_path = settings.data_dir / "production" / "acct-1" / "job-1" / "video" / "s01.openai.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png-success" * 512)

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            raise RuntimeError("ComfyUI unavailable in test")

    monkeypatch.setattr(visual_backend_doctor, "_openai_image_motion_ready", lambda _settings: True)
    monkeypatch.setattr(visual_backend_doctor.httpx, "AsyncClient", FailingAsyncClient)

    report = asyncio.run(visual_backend_doctor.build_visual_backend_doctor_report(settings))

    assert report["public_visual_ready"] is True
    assert report["effective_video_backend"] == "auto"
    assert report["providers"]["comfyui"]["live_ready"] is False
    assert report["providers"]["openai_image_motion"]["live_ready"] is True
    assert report["providers"]["openai_image_motion"]["status"] == "verified_success"
    assert report["evidence"]["openai_image_motion_fallback_ready"] is True
    assert report["evidence"]["openai_image_motion_generation"]["status"] == "verified_success"
    assert report["next_action"] == "visual_backend_ready_generate_private_public_quality_short"


def test_visual_backend_doctor_blocks_openai_success_when_cloud_visual_budget_is_exhausted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import asyncio
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_video_backend = "auto"
    settings.shorts_allow_cloud_visuals = True
    settings.atnos_daily_spend_limit_pln = 0.0
    settings.atnos_monthly_spend_limit_pln = 0.0
    settings.atnos_estimated_visual_cost_per_short_pln = 3.5
    image_path = settings.data_dir / "production" / "acct-1" / "job-1" / "video" / "s01.openai.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png-success" * 512)

    class FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            raise RuntimeError("ComfyUI unavailable in test")

    monkeypatch.setattr(visual_backend_doctor, "_openai_image_motion_ready", lambda _settings: True)
    monkeypatch.setattr(visual_backend_doctor.httpx, "AsyncClient", FailingAsyncClient)

    report = asyncio.run(visual_backend_doctor.build_visual_backend_doctor_report(settings))

    provider = report["providers"]["openai_image_motion"]
    assert report["public_visual_ready"] is False
    assert provider["status"] == "budget_blocked"
    assert provider["budget_allowed"] is False
    assert provider["estimated_generation_cost_pln"] == 3.5
    assert report["evidence"]["cloud_visual_budget"]["status"] == "budget_blocked"
    assert report["evidence"]["openai_image_motion_fallback_ready"] is False
    assert report["next_action"] == "approve_cloud_visual_budget_or_use_local_wan_ltx"


def test_visual_backend_doctor_requires_comfyui_models_not_just_live_port(tmp_path: Path, monkeypatch) -> None:
    import asyncio
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_video_backend = "auto"
    settings.shorts_allow_cloud_visuals = False

    class Response:
        status_code = 200

    class ReadyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(visual_backend_doctor.httpx, "AsyncClient", ReadyAsyncClient)

    report = asyncio.run(visual_backend_doctor.build_visual_backend_doctor_report(settings))

    assert report["public_visual_ready"] is False
    assert report["providers"]["comfyui"]["live_checked"] is True
    assert report["providers"]["comfyui"]["live_ready"] is False
    assert report["providers"]["comfyui"]["status"] == "degraded"
    capability = report["evidence"]["comfyui_public_backend_capability"]
    assert capability["status"] == "missing_capability"
    assert capability["ready"] is False
    assert all(not mode["models_ready"] for mode in capability["modes"])


def test_visual_backend_doctor_detects_comfyui_motion_graphic_fallback_manifest(tmp_path: Path) -> None:
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    manifest_path = app.state.settings.data_dir / "tmp" / "wan-smoke" / "video_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "backend": {"mode": "wan"},
                "fallback_used": False,
                "clips": [{"status": "generated", "backend": "motion_graphic"}],
            }
        ),
        encoding="utf-8",
    )
    error_path = manifest_path.parent / "video" / "s01.wan_errors.json"
    error_path.parent.mkdir(parents=True, exist_ok=True)
    error_path.write_text(
        json.dumps(
            {
                "backend": "wan",
                "reason": "prompt_http_error",
                "details": {"body": "missing_node_type: EmptyLatentVideo"},
            }
        ),
        encoding="utf-8",
    )

    evidence = visual_backend_doctor._comfyui_generation_evidence(app.state.settings)

    assert evidence["status"] == "blocked_recent_fallback"
    assert evidence["backend_mode"] == "wan"
    assert evidence["clip_backends"] == ["motion_graphic"]
    assert evidence["last_error_path"] == str(error_path)
    assert "missing_node_type" in evidence["last_error_reason"]


def test_visual_backend_doctor_accepts_real_comfyui_generation_manifest(tmp_path: Path) -> None:
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    manifest_path = app.state.settings.data_dir / "tmp" / "wan-smoke" / "video_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "backend": {"mode": "wan"},
                "fallback_used": False,
                "clips": [{"status": "generated", "backend": "wan"}],
            }
        ),
        encoding="utf-8",
    )

    evidence = visual_backend_doctor._comfyui_generation_evidence(app.state.settings)

    assert evidence["status"] == "verified_success"
    assert evidence["backend_mode"] == "wan"
    assert evidence["clip_backends"] == ["wan"]


def test_visual_backend_doctor_accepts_recent_probeable_comfyui_output_without_manifest(tmp_path: Path, monkeypatch) -> None:
    from atnos.production import visual_backend_doctor

    app = create_test_app(tmp_path)
    install_dir = tmp_path / "ComfyUI_windows_portable"
    app.state.settings.comfyui_install_dir = install_dir
    output_dir = install_dir / "ComfyUI" / "output"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "atnos_ltx_01_00001_.mp4"
    output_file.write_bytes(b"probeable-ltx-video" * 500)
    monkeypatch.setattr(visual_backend_doctor.video_generator, "_is_probeable_video", lambda path: Path(path).exists())

    evidence = visual_backend_doctor._comfyui_generation_evidence(app.state.settings)

    assert evidence["status"] == "verified_success"
    assert evidence["backend_mode"] == "ltx"
    assert evidence["clip_backends"] == ["ltx"]
    assert evidence["last_output_path"] == str(output_file)


def test_visual_backend_status_returns_last_known_state_without_render(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "motion_graphic"

    with TestClient(app) as client:
        response = client.get("/api/production/visual-backend/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["checked"] is False
    assert payload["public_visual_ready"] is False
    assert payload["next_action"] == "run_visual_backend_doctor"
    assert payload["providers"]["motion_graphic"]["configured"] is True
    assert payload["providers"]["motion_graphic"]["status"] == "fallback_only"
    assert "api_key" not in str(payload).casefold()


def test_visual_backend_check_runs_probe_and_persists_report(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    report_path = settings.data_dir / "reports" / "visual_backend_doctor_test.json"
    md_path = settings.data_dir / "reports" / "visual_backend_doctor_test.md"

    async def fake_check(current_settings):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# report", encoding="utf-8")
        return {
            "generated_at": "2026-06-08T12:45:00+00:00",
            "public_visual_ready": True,
            "selected_video_backend": "auto",
            "effective_video_backend": "wan",
            "next_action": "visual_backend_ready_generate_private_public_quality_short",
            "providers": {
                "comfyui": {"configured": True, "live_checked": True, "live_ready": True, "status": "ready", "error": None},
                "openai_image_motion": {"configured": False, "live_checked": False, "live_ready": False, "status": "missing", "error": None},
                "motion_graphic": {"configured": False, "live_checked": False, "live_ready": False, "status": "fallback_only", "error": "fallback only"},
            },
            "report_path": str(report_path),
            "markdown_report_path": str(md_path),
        }

    monkeypatch.setattr("atnos.api.routes.production.write_visual_backend_doctor_report", fake_check)

    with TestClient(app) as client:
        response = client.post("/api/production/visual-backend/check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["public_visual_ready"] is True
    assert payload["effective_video_backend"] == "wan"
    assert payload["next_action"] == "visual_backend_ready_generate_private_public_quality_short"
    assert payload["report_path"] == str(report_path)
    assert payload["markdown_report_path"] == str(md_path)


def test_visual_backend_smoke_render_endpoint_runs_controlled_probe(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    artifact_dir = app.state.settings.data_dir / "tmp" / "visual-backend-smoke" / "test"
    manifest_path = artifact_dir / "video_manifest.json"

    async def fake_smoke(current_settings, *, mode: str, prompt: str):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{}", encoding="utf-8")
        return {
            "status": "completed",
            "mode": mode,
            "artifact_dir": str(artifact_dir),
            "elapsed_s": 0.1,
            "video_manifest_path": str(manifest_path),
            "video_manifest": {
                "backend": {"mode": mode},
                "fallback_used": False,
                "clips": [{"status": "generated", "backend": mode}],
            },
            "public_visual_ready": True,
            "visual_backend_report_path": str(app.state.settings.data_dir / "reports" / "visual_backend_doctor_test.json"),
            "next_action": "visual_backend_ready_generate_private_public_quality_short",
        }

    monkeypatch.setattr("atnos.api.routes.production.run_visual_backend_smoke_render", fake_smoke)

    with TestClient(app) as client:
        response = client.post(
            "/api/production/visual-backend/smoke-render",
            json={"mode": "ltx", "prompt": "A concrete topic visual with moving chart lines and realistic object motion."},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "ltx"
    assert payload["public_visual_ready"] is True
    assert payload["video_manifest_path"] == str(manifest_path)
    assert app.state.production_ws_events[-1]["type"] == "visual_backend_smoke_render"
    assert app.state.production_ws_events[-1]["public_visual_ready"] is True


def test_public_quality_status_combines_voice_visual_and_style_guide(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "voice_provider_doctor_test.json").write_text(
        json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(),
                "public_voice_ready": True,
                "selected_tts_backend": "openai_tts",
                "next_action": "premium_voice_ready_generate_private_public_quality_short",
                "providers": {
                    "elevenlabs": {"configured": False, "live_checked": False, "live_ready": False},
                    "openai_tts": {"configured": True, "live_checked": True, "live_ready": True, "model": "gpt-4o-mini-tts"},
                    "f5_tts": {"configured": False, "live_checked": False, "live_ready": False},
                },
            }
        ),
        encoding="utf-8",
    )
    (reports_dir / "visual_backend_doctor_test.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-06-08T12:01:00+00:00",
                "public_visual_ready": True,
                "selected_video_backend": "auto",
                "effective_video_backend": "wan",
                "next_action": "visual_backend_ready_generate_private_public_quality_short",
                "providers": {
                    "comfyui": {"configured": True, "live_checked": True, "live_ready": True, "status": "ready"},
                    "openai_image_motion": {"configured": True, "live_checked": False, "live_ready": True, "status": "configured"},
                    "motion_graphic": {"configured": False, "live_checked": False, "live_ready": False, "status": "fallback_only"},
                },
            }
        ),
        encoding="utf-8",
    )
    settings.obsidian_vault_path = tmp_path / "vault"
    style_path = settings.obsidian_vault_path / settings.atnos_obsidian_shorts_dir / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True, exist_ok=True)
    style_path.write_text("# Style guide\nUse concrete topic visuals and premium narration.", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/api/production/public-quality/status", params={"account_id": "acct-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["blockers"] == []
    assert payload["voice_provider"]["public_voice_ready"] is True
    assert payload["visual_backend"]["public_visual_ready"] is True
    assert payload["style_guide"]["ready"] is True
    assert payload["style_guide"]["source"] == str(style_path)


def test_style_guide_ensure_creates_default_guide_and_refreshes_public_quality(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir(parents=True, exist_ok=True)

    with TestClient(app) as client:
        response = client.post(
            "/api/production/style-guide/ensure",
            json={
                "account_id": "acct-1",
                "channel_name": "Test Channel",
                "niche": "finance Shorts",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    style_path = settings.obsidian_vault_path / settings.atnos_obsidian_shorts_dir / "acct-1" / "style_guide.md"
    assert payload["status"] == "created"
    assert payload["created"] is True
    assert payload["path"] == str(style_path)
    assert style_path.exists()
    assert "Test Channel" in style_path.read_text(encoding="utf-8")
    assert payload["public_quality"]["style_guide"]["ready"] is True
    assert payload["public_quality"]["style_guide"]["source"] == str(style_path)


def test_operating_standards_maps_latest_research_to_project_checklists(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    report_path = app.state.settings.data_dir / "learning" / "side_research" / "investment_faceless_tools_test.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Research\nUse quality gates before automation.", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/api/production/operating-standards", params={"project_id": "yt-finanse-pl"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["project_type"] == "shorts_channel"
    assert payload["source"]["path"] == str(report_path)
    assert payload["sections"][0]["key"] == "shorts_quality"
    assert any(item["key"] == "private_qa_before_public" for item in payload["sections"][0]["items"])
    assert "No low-quality fallback voice for public Shorts." in payload["forbidden"]


def test_operating_standards_marks_investment_execution_as_blocked_until_policy_exists(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/production/operating-standards", params={"project_id": "investment:main"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "baseline"
    assert payload["project_type"] == "investment"
    assert payload["metrics"]["blocked_until_configured_count"] >= 2
    assert payload["sections"][0]["key"] == "investment_safety"
    assert any(item["key"] == "risk_policy_required" for item in payload["sections"][0]["items"])
    assert any("real-money auto trading" in item for item in payload["forbidden"])


def test_faceless_playbook_endpoint_selects_template_per_project_profile(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    report_path = app.state.settings.data_dir / "learning" / "side_research" / "session" / "atnos_research_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# Research\nFaceless playbook source.", encoding="utf-8")

    with TestClient(app) as client:
        finance = client.get(
            "/api/production/faceless-playbook",
            params={"project_id": "new-project-01", "content_profile": "wealth_blueprint"},
        )
        history = client.get(
            "/api/production/faceless-playbook",
            params={"project_id": "new-project-02", "content_profile": "dark_chronicles"},
        )
        brainrot = client.get(
            "/api/production/faceless-playbook",
            params={"project_id": "new-project-03", "content_profile": "sigma_squad"},
        )

    assert finance.status_code == 200
    assert history.status_code == 200
    assert brainrot.status_code == 200
    assert finance.json()["template_key"] == "science_explainer"
    assert history.json()["template_key"] == "mystery_documentary"
    assert brainrot.json()["template_key"] == "shorts_microstory"
    assert finance.json()["source"]["path"] == str(report_path)
    assert "hook_pattern" in finance.json()
    assert "visual_direction" in history.json()
    assert "voice_direction" in brainrot.json()
