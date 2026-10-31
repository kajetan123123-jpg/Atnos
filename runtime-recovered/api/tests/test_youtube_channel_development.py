from __future__ import annotations

import json
from pathlib import Path

from atnos.learning.youtube_channel_development import (
    ALLOWED_STATUSES,
    CHANNELS,
    CREATIVE_DIRECTIONS,
    EDITING_MANUALS,
    FORMAT_PLANS,
    YouTubeChannelDevelopmentSession,
)


def test_each_channel_has_three_directions_and_five_formats() -> None:
    for channel in CHANNELS:
        assert len(CREATIVE_DIRECTIONS[channel.key]) >= 3
        assert 1 <= sum(item["selected_for_prototype"] for item in CREATIVE_DIRECTIONS[channel.key]) <= 2
        assert len(FORMAT_PLANS[channel.key]) >= 5
        assert channel.key in EDITING_MANUALS


def test_external_reviews_are_never_synthesized_as_complete() -> None:
    source = Path(__file__).parents[1] / "src" / "atnos" / "learning" / "youtube_channel_development.py"
    text = source.read_text(encoding="utf-8")
    assert '"claude": {"status": "pending_external_review"' in text
    assert '"kimi": {"status": "pending_external_review"' in text


def test_profiles_disable_automatic_publication() -> None:
    profile_dir = Path(__file__).parents[1] / "config" / "channel_profiles"
    for channel in CHANNELS:
        profile = json.loads((profile_dir / channel.profile_name).read_text(encoding="utf-8"))
        assert profile["auto_publish_after_qa"] is False
        assert profile["publish_mode"] == "private_only"


def test_status_vocabulary_is_closed() -> None:
    assert ALLOWED_STATUSES == {
        "verified",
        "prototype_ready",
        "needs_revision",
        "insufficient_data",
        "blocked_user_action",
        "blocked_technical",
        "pending_external_review",
        "rejected",
    }


def test_quality_coordinator_blocks_publish_on_p0() -> None:
    session = object.__new__(YouTubeChannelDevelopmentSession)
    channel = CHANNELS[0]
    agents = [
        {
            "agent": "facts_and_sources",
            "problems": [
                {
                    "timestamp": "whole video",
                    "priority": "P0",
                    "description": "No source manifest.",
                    "impact": "publication readiness",
                    "fix": "Add sources.",
                }
            ],
        }
    ]
    result = session._coordinate_agents(channel, {"job_id": "test", "qa_auto_uploadable": True, "blocking_findings": ["No source manifest."]}, agents)
    assert result["decision"] == "REBUILD"
    assert result["publication_blocked"] is True
    assert result["p0_count"] == 1


def test_capability_matrix_does_not_claim_youtube_verified_when_probes_are_blocked(tmp_path: Path) -> None:
    session = object.__new__(YouTubeChannelDevelopmentSession)
    session.output_root = tmp_path
    session.intelligence_db = tmp_path / "intelligence.db"
    prototype = {
        "media_probe": {"valid": False},
        "evidence_paths": ["script.json"],
        "narration": {"path": "narration.wav"},
        "storyboard": "storyboard.jpg",
        "final_video": "final.mp4",
        "subtitles": {"path": "subtitles.srt"},
        "thumbnail": {"path": "thumbnail.jpg"},
    }
    reports = [
        {
            "channel_name": name,
            "inventory": {
                "oauth": {
                    "ready": False,
                    "status": "blocked_user_action",
                    "warnings": ["OAuth client configuration changed; reconnect required."],
                }
            },
            "prototype": prototype,
        }
        for name in ("Wealth Blueprint", "Dark Chronicles", "Sigma Squad")
    ]

    capabilities = session._capability_matrix(reports)
    youtube = next(item for item in capabilities if item["element"] == "YouTube read access")

    assert youtube["status"] == "blocked_user_action"
    assert "reconnect required" in youtube["limitation"]
