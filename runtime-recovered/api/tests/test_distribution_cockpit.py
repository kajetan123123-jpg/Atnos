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
from test_production_distribution_planner import _seed_distribution_job  # noqa: E402
from test_support import create_test_app  # noqa: E402


def test_distribution_cockpit_reports_no_jobs(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        response = client.get("/api/distribution/cockpit?account_id=missing")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "no_jobs"
    assert payload["latest_job"] is None
    assert "no_production_jobs" in payload["blockers"]
    assert payload["capabilities"]["can_prepare_release_packages"] is False
    assert payload["capabilities"]["can_upload_tiktok_automatically"] is False
    assert payload["capabilities"]["can_upload_instagram_automatically"] is False


def test_distribution_cockpit_exposes_release_package_status_for_latest_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(settings, tmp_path, job_id="dist-cockpit-1")

    with TestClient(app) as client:
        response = client.get("/api/distribution/cockpit?account_id=acct-dist-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "youtube_private_ready"
    assert payload["latest_job"]["id"] == "dist-cockpit-1"
    assert payload["capabilities"]["can_prepare_release_packages"] is True
    assert payload["capabilities"]["can_upload_youtube_private_via_api"] is True
    assert payload["capabilities"]["can_upload_tiktok_automatically"] is False
    assert payload["capabilities"]["requires_owner_review"] is True
    platforms = {item["platform"]: item for item in payload["platforms"]}
    assert platforms["youtube_shorts"]["api_private_ready"] is True
    assert platforms["youtube_shorts"]["automatic_upload_ready"] is True
    assert platforms["tiktok"]["release_package_ready"] is True
    assert platforms["tiktok"]["automatic_upload_ready"] is False
    assert platforms["instagram_reels"]["release_package_ready"] is True
    assert platforms["instagram_reels"]["automatic_upload_ready"] is False
    assert "tiktok_trusted_account_missing" in platforms["tiktok"]["blockers"]
    assert "instagram_reels_trusted_account_missing" in platforms["instagram_reels"]["blockers"]


def test_distribution_cockpit_writes_latest_release_packages(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(settings, tmp_path, job_id="dist-cockpit-package")

    with TestClient(app) as client:
        response = client.post(
            "/api/distribution/package-latest",
            json={"account_id": "acct-dist-1"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert payload["job_id"] == "dist-cockpit-package"
    package = payload["package"]
    assert package["created_count"] == 3
    packages = {item["platform"]: item for item in package["packages"]}
    assert set(packages) == {"youtube_shorts", "tiktok", "instagram_reels"}
    assert Path(packages["tiktok"]["manifest_path"]).exists()
    assert Path(packages["instagram_reels"]["caption_path"]).exists()
    assert payload["cockpit"]["ready_package_count"] >= 3
