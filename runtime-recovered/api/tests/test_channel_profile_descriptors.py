from __future__ import annotations

from pathlib import Path

from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext


def _context(content_profile: str, *, account_id: str = "youtube:test@example.com") -> ProductionContext:
    return ProductionContext(
        job_id="profile-descriptor-test",
        account_id=account_id,
        topic=ApprovedTopic(
            id="profile-descriptor-topic",
            account_id=account_id,
            title="A neutral production topic",
            description="No niche hints are present here.",
            total_score=80,
            content_profile=content_profile,
        ),
        artifact_dir=Path("artifacts"),
    )


def test_human_readable_profile_resolves_named_channel() -> None:
    profile = resolve_channel_profile(
        _context(
            "Wealth Blueprint | English personal finance Shorts | US/global | "
            "dark minimalist fintech style | no investment advice"
        )
    )

    assert profile.key == "wealth_blueprint"


def test_exact_generic_profile_is_not_promoted_to_named_channel() -> None:
    profile = resolve_channel_profile(_context("finance"))

    assert profile.key == "finance"


def test_named_wealth_blueprint_account_keeps_its_profile_with_legacy_finance_setting() -> None:
    for content_profile in ("", "finance"):
        context = _context(content_profile, account_id="yt-wealth-blueprint")

        assert resolve_channel_profile(context).key == "wealth_blueprint"
