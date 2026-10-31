from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect
from test_support import create_test_app


def test_account_create_can_seed_intelligence_config_without_collecting(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        with connect(app.state.settings) as db:
            before_yt_count = db.execute("SELECT COUNT(*) AS count FROM yt_signals").fetchone()["count"]
        response = client.post(
            "/accounts",
            json={
                "label": "YT Finanse PL",
                "provider_key": "youtube-studio",
                "account_identifier": "yt-finanse-pl",
                "secret_type": "oauth_token_later",
                "risk_level": "medium",
                "allowed_capabilities": ["read_metadata", "publish_later"],
                "intelligence_niches": ["finanse osobiste", "oszczedzanie", "finanse osobiste"],
                "intelligence_region": "pl",
                "intelligence_timeframe": "now+7-d",
                "intelligence_refresh_now": True,
            },
        )

    assert response.status_code == 201
    with connect(app.state.settings) as db:
        config = db.execute(
            """
            SELECT account_id, niches, region, timeframe, next_refresh_at, enabled
            FROM intelligence_accounts
            WHERE account_id = 'yt-finanse-pl'
            """
        ).fetchone()
        yt_count = db.execute("SELECT COUNT(*) AS count FROM yt_signals").fetchone()["count"]

    assert config["account_id"] == "yt-finanse-pl"
    assert config["region"] == "PL"
    assert config["timeframe"] == "now 7-d"
    assert config["enabled"] == 1
    assert config["next_refresh_at"]
    assert "finanse osobiste" in config["niches"]
    assert "oszczedzanie" in config["niches"]
    assert yt_count == before_yt_count


def test_account_update_can_seed_intelligence_config_without_secret_rotation(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/accounts",
            json={
                "label": "YT Finanse PL",
                "provider_key": "youtube-studio",
                "account_identifier": "yt-finanse-pl",
                "secret_type": "oauth_token_later",
                "risk_level": "medium",
                "allowed_capabilities": ["read_metadata"],
            },
        )
        account_id = created.json()["id"]
        response = client.patch(
            f"/accounts/{account_id}",
            json={
                "intelligence_niches": ["inwestowanie edukacyjne"],
                "intelligence_region": "PL",
                "intelligence_timeframe": "today 3-m",
                "intelligence_refresh_now": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["has_secret"] is False
    with connect(app.state.settings) as db:
        config = db.execute(
            """
            SELECT account_id, niches, region, timeframe, next_refresh_at, enabled
            FROM intelligence_accounts
            WHERE account_id = 'yt-finanse-pl'
            """
        ).fetchone()

    assert config["account_id"] == "yt-finanse-pl"
    assert "inwestowanie edukacyjne" in config["niches"]
    assert config["timeframe"] == "today 3-m"
    assert config["next_refresh_at"]


def test_intelligence_account_automation_mode_is_persisted_and_can_be_disabled(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        configured = client.post(
            "/api/intelligence/accounts",
            json={
                "account_id": "youtube:test@example.com",
                "niches": ["personal finance"],
                "region": "US",
                "content_profile": "wealth_blueprint",
                "automation_mode": "auto_private",
                "refresh_now": False,
            },
        )
        updated = client.patch(
            "/api/intelligence/accounts/youtube:test@example.com",
            json={"automation_mode": "manual"},
        )

    assert configured.status_code == 200
    assert configured.json()["account"]["automation_mode"] == "auto_private"
    assert updated.status_code == 200
    assert updated.json()["account"]["automation_mode"] == "manual"
    with connect(app.state.settings) as db:
        row = db.execute(
            "SELECT automation_mode FROM intelligence_accounts WHERE account_id = ?",
            ("youtube:test@example.com",),
        ).fetchone()
    assert row["automation_mode"] == "manual"
