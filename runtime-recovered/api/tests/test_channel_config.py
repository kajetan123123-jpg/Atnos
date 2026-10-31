from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tests.test_support import create_test_app  # noqa: E402


def test_get_channel_config_creates_default_file(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/channel/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["config"]["channel_name"] == "YT Finanse PL"
    assert payload["config"]["niches"]
    assert payload["warnings"] == []
    assert (tmp_path / "config" / "channel_config.json").exists()


def test_post_channel_config_saves_payload_and_warns_for_empty_niches(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/channel/config",
            json={
                "channel_name": "Historia Shorts",
                "niches": [],
                "reference_channels": "https://www.youtube.com/@alux\nhttps://www.youtube.com/@kurzgesagt",
                "publish_schedule": {"enabled": True, "time_utc": "06:00"},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["config"]["channel_name"] == "Historia Shorts"
    assert payload["config"]["niches"] == []
    assert "Brak nisz" in payload["warnings"][0]
    assert payload["config"]["reference_channels"] == [
        "https://www.youtube.com/@alux",
        "https://www.youtube.com/@kurzgesagt",
    ]

    saved = json.loads((tmp_path / "config" / "channel_config.json").read_text(encoding="utf-8"))
    assert saved["publish_schedule"]["enabled"] is True
    assert saved["publish_schedule"]["time_utc"] == "06:00"
    assert saved["publish_schedule"]["frequency_per_week"] == 3


def test_get_channel_config_returns_saved_values(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        client.post(
            "/api/channel/config",
            json={
                "channel_id": "UC_TEST",
                "channel_name": "AI Absurd PL",
                "niches": "ai absurd, brainrot dla dzieci",
                "visual_style": "kolorowy, szybki, memiczny",
                "custom_field": {"kept": True},
            },
        )
        response = client.get("/api/channel/config")

    assert response.status_code == 200
    config = response.json()["config"]
    assert config["channel_id"] == "UC_TEST"
    assert config["niches"] == ["ai absurd", "brainrot dla dzieci"]
    assert config["visual_style"] == "kolorowy, szybki, memiczny"
    assert config["custom_field"] == {"kept": True}
