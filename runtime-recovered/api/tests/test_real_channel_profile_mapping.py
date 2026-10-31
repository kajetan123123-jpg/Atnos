from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext


def _context(
    account_id: str,
    title: str = "Test topic",
    *,
    content_profile: str = "",
) -> ProductionContext:
    return ProductionContext(
        job_id=f"job-{account_id}",
        account_id=account_id,
        topic=ApprovedTopic(
            id=f"topic-{account_id}",
            account_id=account_id,
            title=title,
            description="Production topic for the connected Atnos channel.",
            total_score=80,
            content_profile=content_profile,
        ),
        artifact_dir=Path("F:/tmp/atnos-tests") / account_id,
    )


def test_real_finance_account_maps_to_english_wealth_blueprint_profile() -> None:
    profile = resolve_channel_profile(
        _context(
            "new-platform-account-01",
            "The Money Rule Most People Ignore",
            content_profile="wealth_blueprint",
        )
    )

    assert profile.key == "wealth_blueprint"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction


def test_real_history_account_maps_to_english_dark_chronicles_profile() -> None:
    profile = resolve_channel_profile(
        _context(
            "new-platform-account-02",
            "The Strange Law That Terrified a Kingdom",
            content_profile="dark_chronicles",
        )
    )

    assert profile.key == "dark_chronicles"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction


def test_real_brainrot_account_maps_to_english_sigma_squad_profile() -> None:
    profile = resolve_channel_profile(
        _context(
            "new-platform-account-03",
            "NPC Mode Activated in Ohio",
            content_profile="sigma_squad",
        )
    )

    assert profile.key == "sigma_squad"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction


def test_historical_account_identifier_does_not_override_explicit_profile() -> None:
    profile = resolve_channel_profile(
        _context(
            "yt-historia-pl",
            "A practical monthly budget",
            content_profile="wealth_blueprint",
        )
    )

    assert profile.key == "wealth_blueprint"
