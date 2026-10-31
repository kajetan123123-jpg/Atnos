from __future__ import annotations

import asyncio
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import script_writer
from test_support import create_test_app


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="prod-script-compound-test",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="wealth-v1-stability-compound-interest",
            account_id="yt-wealth-blueprint",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="Explain that compound interest earns interest on prior interest over successive periods.",
            total_score=89,
        ),
        artifact_dir=tmp_path,
        artifacts={},
    )


def _hook() -> dict[str, str]:
    return {
        "text": "Compound interest rewards time, not hype",
        "opening_line": "The second year grows from more than the money you started with.",
    }


def test_rewrites_fluid_but_off_topic_compound_interest_script(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "Compound interest rewards time.", "duration_target_sec": 3, "visual_note": "calendar"},
                {"id": "s02", "type": "intro", "text": "The calculation begins quietly.", "duration_target_sec": 5, "visual_note": "calculator"},
                {"id": "s03", "type": "body", "text": "Your paycheck lands in your bank app.", "duration_target_sec": 6, "visual_note": "wallet"},
                {"id": "s04", "type": "body", "text": "A delivery and subscription drain it.", "duration_target_sec": 6, "visual_note": "receipt"},
                {"id": "s05", "type": "body", "text": "Pause before the next purchase.", "duration_target_sec": 6, "visual_note": "card"},
                {"id": "s06", "type": "body", "text": "Keep the rest of your paycheck alive.", "duration_target_sec": 6, "visual_note": "bank"},
                {"id": "s07", "type": "cta", "text": "Save this.", "duration_target_sec": 5, "visual_note": "cta"},
            ]
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _context(tmp_path),
            "prompt",
            _hook(),
            {"evidence_backed": True, "youtube_examples": [], "google_trends": []},
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_script_quality"
    assert "topic_mismatch_compound_interest" in payload["claim_safety"]["script_quality_markers"]
    assert "Start with $100" in payload["description_draft"]
    assert "$110.25" in payload["description_draft"]
    assert "delivery and subscription" not in payload["description_draft"].casefold()


def test_accepts_concrete_on_topic_compound_interest_script(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "How $100 becomes $110.25.", "duration_target_sec": 3, "visual_note": "calculator"},
                {"id": "s02", "type": "intro", "text": "At a hypothetical 5%, year one ends at $105.", "duration_target_sec": 5, "visual_note": "calculator"},
                {"id": "s03", "type": "body", "text": "Compound interest adds growth on prior interest.", "duration_target_sec": 6, "visual_note": "ledger"},
                {"id": "s04", "type": "body", "text": "Watch your hypothetical balance move from $100 to $105 to $110.25 as each period compounds the larger base.", "duration_target_sec": 6, "visual_note": "timeline"},
                {"id": "s05", "type": "body", "text": "More time gives that process more periods to repeat.", "duration_target_sec": 6, "visual_note": "calendar"},
                {"id": "s06", "type": "body", "text": "This explains the mechanism, not a guaranteed return.", "duration_target_sec": 6, "visual_note": "calculator"},
                {"id": "s07", "type": "body", "text": "The first period's interest becomes part of the base used for the next calculation.", "duration_target_sec": 7, "visual_note": "ledger"},
                {"id": "s08", "type": "body", "text": "That repeated step is why time can matter as much as the starting contribution.", "duration_target_sec": 7, "visual_note": "calendar"},
                {"id": "s09", "type": "body", "text": "The rate does not need to change for the base itself to keep growing.", "duration_target_sec": 7, "visual_note": "calculator"},
                {"id": "s10", "type": "cta", "text": "Calculate one scenario before you make your next money decision.", "duration_target_sec": 5, "visual_note": "cta"},
            ]
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _context(tmp_path),
            "prompt",
            _hook(),
            {"evidence_backed": True, "youtube_examples": [], "google_trends": []},
        )
    )

    assert payload["claim_safety"]["status"] == "passed"
    assert payload["script_quality"]["status"] == "passed"


def test_compound_fallback_uses_short_dense_source_backed_beats(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifacts["topic_intelligence"] = {
        "canonical_sources": [{"url": "https://example.test", "facts": ["$100", "5%", "$105", "$110.25"]}]
    }

    payload = script_writer._safe_fallback_script(context, _hook(), script_writer.resolve_channel_profile(context))
    body = [segment for segment in payload["segments"] if segment["type"] == "body"]

    assert len(body) == 8
    assert all(segment["duration_target_sec"] == 4 for segment in body)
    assert max(len(segment["text"].split()) for segment in body) <= 14
    assert "$110.25" in payload["description_draft"]
