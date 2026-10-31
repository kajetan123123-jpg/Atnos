from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import hook_generator, script_writer


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="job-sigma-squad",
        account_id="yt-sigma-squad",
        topic=ApprovedTopic(
            id="topic-sigma-1",
            account_id="yt-sigma-squad",
            title="Sigma vs Ohio simulation",
            description="English gen_alpha_brainrot short with rizz, NPC and mewing humor.",
            total_score=81,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "BRO WAIT is this Ohio energy?",
                    "opening_line": "This starts normal for half a second, then the Sigma meter breaks.",
                }
            }
        },
    )


def test_resolves_sigma_squad_profile_before_generic_brainrot(tmp_path: Path) -> None:
    profile = resolve_channel_profile(_context(tmp_path))

    assert profile.key == "sigma_squad"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction
    assert "toxic green" in profile.visual_style
    assert "Private-only" in profile.risk_notes


def test_sigma_squad_fallback_hooks_are_english_brainrot(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, "Ohio simulation")
    text = " ".join(f"{item['text']} {item['opening_line']}" for item in variants)

    assert "BRO WAIT" in text
    assert "Sigma mode" in text
    assert "Ohio logic" in text
    assert "AI zrobilo" not in text


def test_sigma_squad_fallback_script_is_english_and_safety_guarded(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    payload = script_writer._safe_fallback_script(
        context,
        context.artifacts["hook"]["selected"],
        profile,
    )
    text = " ".join(segment["text"] for segment in payload["segments"])

    assert payload["channel_profile"]["key"] == "sigma_squad"
    assert payload["language_code"] == "en"
    assert "safe meme chaos" in text
    assert "copyrighted character names" in text
    assert "Comment W or L" in text
    assert "Obserwuj" not in text
    assert "bez przemocy" not in text
