from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.stability_topics import canonical_stability_contracts
from atnos.production.steps import script_writer
from test_support import create_test_app


SUITE_PATH = TESTS_DIR.parents[0] / "config" / "wealth_blueprint_v1_stability_topics.json"


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="prod-script-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Inflacja i oszczednosci",
            description="Finanse osobiste",
            total_score=88,
        ),
        artifact_dir=tmp_path,
        artifacts={},
    )


def _hook() -> dict:
    return {
        "text": "Dlaczego twoje pieniadze znikaja?",
        "opening_line": "To nie jest kwestia szczescia, tylko decyzji.",
    }


def _wealth_context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="prod-script-wealth-test",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth-1",
            account_id="yt-wealth-blueprint",
            title="Why Your Paycheck Disappears by Friday",
            description="personal finance",
            total_score=89,
        ),
        artifact_dir=tmp_path,
        artifacts={},
    )


def _wealth_hook() -> dict:
    return {
        "text": "Saving money will not make you rich.",
        "opening_line": "The real gap starts with how your money behaves after you earn it.",
    }


def _llm_script_with_claim(claim_text: str) -> dict:
    return {
        "segments": [
            {"id": "s01", "type": "hook", "text": "Dlaczego?", "duration_target_sec": 3, "visual_note": "hook"},
            {"id": "s02", "type": "intro", "text": claim_text, "duration_target_sec": 5, "visual_note": "intro"},
            {"id": "s03", "type": "body", "text": "Sprawdz jedna decyzje.", "duration_target_sec": 6, "visual_note": "body"},
            {"id": "s04", "type": "body", "text": "Porownaj koszty.", "duration_target_sec": 6, "visual_note": "body"},
            {"id": "s05", "type": "body", "text": "Nie reaguj emocjami.", "duration_target_sec": 6, "visual_note": "body"},
            {"id": "s06", "type": "cta", "text": "Zapisz ten schemat.", "duration_target_sec": 5, "visual_note": "cta"},
        ],
        "title_options": ["Inflacja i oszczednosci"],
        "description_draft": claim_text,
        "tags": ["finanse"],
    }


def test_script_writer_rewrites_unsupported_financial_claims(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return _llm_script_with_claim("Ten prosty ruch daje 20% zysku rocznie.")

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _context(tmp_path),
            "prompt",
            _hook(),
            {
                "evidence_backed": True,
                "youtube_examples": [],
                "google_trends": [],
                "score": 88,
            },
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_unsupported_claims"
    assert payload["claim_safety"]["unsupported_claims"] == ["20%"]
    assert "20%" not in payload["description_draft"]


def test_script_writer_rewrites_unsupported_polish_currency_claims(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return _llm_script_with_claim("Ten sposob moze dac 1 000 zł miesiecznie bez ryzyka.")

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _context(tmp_path),
            "prompt",
            _hook(),
            {
                "evidence_backed": True,
                "youtube_examples": [],
                "google_trends": [],
                "score": 88,
            },
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_unsupported_claims"
    assert payload["claim_safety"]["unsupported_claims"] == ["1000pln"]
    assert "1 000 zł" not in payload["description_draft"]


def test_script_writer_allows_numbers_present_in_intelligence_signals(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return _llm_script_with_claim("Podobny format mial 100000 wyswietlen w sygnalach.")

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _context(tmp_path),
            "prompt",
            _hook(),
            {
                "evidence_backed": True,
                "youtube_examples": [{"view_count": 100000, "like_count": 1200, "comment_count": 40}],
                "google_trends": [],
                "score": 88,
            },
        )
    )

    assert payload["claim_safety"]["status"] == "passed"
    assert payload["claim_safety"]["unsupported_claims"] == []
    assert "100000" in payload["description_draft"]


def test_source_backed_stability_numbers_are_allowed_but_unsourced_numbers_are_rejected() -> None:
    source_record = canonical_stability_contracts(SUITE_PATH)["wealth-v1-stability-high-interest-debt"]
    intelligence = {
        "source_record": source_record,
    }
    sourced = _llm_script_with_claim("The official example uses a rate around 18%.")
    unsourced = _llm_script_with_claim("The official example uses a rate around 19%.")

    assert script_writer._unsupported_numeric_claims(sourced, intelligence) == []
    assert script_writer._unsupported_numeric_claims(unsourced, intelligence) == ["19%"]


def test_currency_claim_cannot_reuse_unrelated_view_count() -> None:
    intelligence = {
        "youtube_examples": [{"view_count": 100000}],
        "google_trends": [],
    }
    payload = _llm_script_with_claim("This guarantees $100,000 in profit.")

    assert script_writer._unsupported_numeric_claims(payload, intelligence) == ["100000usd"]


def test_small_unitless_numeric_claim_is_not_ignored() -> None:
    payload = _llm_script_with_claim("The strategy succeeds in exactly 73 days.")

    assert script_writer._unsupported_numeric_claims(payload, {}) == ["73"]


def test_legacy_body_response_uses_the_same_claim_gate(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return {
            "body": [
                "Your paycheck reaches the bank on Friday.",
                "This guarantees $100,000 in profit from your card.",
                "Compare the receipt with your spending plan.",
                "Write down the next purchase before it happens.",
            ],
            "cta": "Review the plan.",
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _wealth_context(tmp_path),
            "prompt",
            _wealth_hook(),
            {"youtube_examples": [{"view_count": 100000}], "google_trends": []},
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_unsupported_claims"
    assert "100000usd" in payload["claim_safety"]["unsupported_claims"]


def test_script_writer_rewrites_prompt_filler_narration(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings

    async def fake_ollama(*_args, **_kwargs):
        return {
            "segments": [
                {
                    "id": "s01",
                    "type": "hook",
                    "text": "Saving money will not make you rich.",
                    "duration_target_sec": 3,
                    "visual_note": "hook",
                },
                {
                    "id": "s02",
                    "type": "intro",
                    "text": "The topic Why Your Paycheck Disappears by Friday matters because money habits compound quietly.",
                    "duration_target_sec": 5,
                    "visual_note": "intro",
                },
                {"id": "s03", "type": "body", "text": "Find one leak.", "duration_target_sec": 6, "visual_note": "body"},
                {"id": "s04", "type": "body", "text": "Track it weekly.", "duration_target_sec": 6, "visual_note": "body"},
                {"id": "s05", "type": "body", "text": "Repeat the rule.", "duration_target_sec": 6, "visual_note": "body"},
                {"id": "s06", "type": "cta", "text": "Save this.", "duration_target_sec": 5, "visual_note": "cta"},
            ]
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            _wealth_context(tmp_path),
            "prompt",
            _wealth_hook(),
            {"evidence_backed": True, "youtube_examples": [], "google_trends": [], "score": 89},
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_script_quality"
    assert "The topic" not in payload["description_draft"]
    assert "Your paycheck lands, and for one moment you feel richer than yesterday" in payload["description_draft"]
    assert "One delivery. One subscription." in payload["description_draft"]


def test_script_writer_rewrites_generic_dark_history_school_copy(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings
    context = ProductionContext(
        job_id="prod-script-dark-test",
        account_id="yt-dark-chronicles",
        topic=ApprovedTopic(
            id="topic-dark-1",
            account_id="yt-dark-chronicles",
            title="A Strange Medieval Law",
            description="dark history documentary",
            total_score=87,
        ),
        artifact_dir=tmp_path,
        artifacts={},
    )
    hook = {
        "text": "A law that made people suffer for something trivial.",
        "opening_line": "What did medieval courts punish with public humiliation and fines?",
    }

    async def fake_ollama(*_args, **_kwargs):
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": hook["text"], "duration_target_sec": 3, "visual_note": "hook"},
                {
                    "id": "s02",
                    "type": "intro",
                    "text": "One law required people to wear a specific type of clothing in public.",
                    "duration_target_sec": 4,
                    "visual_note": "intro",
                },
                {
                    "id": "s03",
                    "type": "body",
                    "text": "Failure to comply led to fines, public shaming, and even imprisonment.",
                    "duration_target_sec": 5,
                    "visual_note": "body",
                },
                {
                    "id": "s04",
                    "type": "transition",
                    "text": "But why such a strange rule?",
                    "duration_target_sec": 3,
                    "visual_note": "body",
                },
                {
                    "id": "s05",
                    "type": "body",
                    "text": "It was meant to control behavior and reinforce social order.",
                    "duration_target_sec": 4,
                    "visual_note": "body",
                },
                {
                    "id": "s06",
                    "type": "cta",
                    "text": "What other strange laws shaped the medieval world?",
                    "duration_target_sec": 3,
                    "visual_note": "cta",
                },
                {
                    "id": "s07",
                    "type": "outro",
                    "text": "History is full of surprises.",
                    "duration_target_sec": 3,
                    "visual_note": "outro",
                },
            ]
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            settings,
            context,
            "prompt",
            hook,
            {"evidence_backed": True, "youtube_examples": [], "google_trends": [], "score": 87},
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_script_quality"
    assert "History is full of surprises" not in payload["description_draft"]
    assert "The street became the courtroom" in payload["description_draft"]


def test_dark_chronicles_dancing_plague_fallback_is_complete_and_story_driven(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="prod-script-dancing-plague",
        account_id="youtube:account-d6add2f8@example.invalid",
        topic=ApprovedTopic(
            id="topic-dancing-plague",
            account_id="youtube:account-d6add2f8@example.invalid",
            title="The Dancing Plague of 1518",
            description="A documented outbreak in Strasbourg.",
            total_score=91,
        ),
        artifact_dir=tmp_path,
        artifacts={},
    )
    hook = {
        "text": "They danced until their feet bled and nobody could explain why",
        "opening_line": "A mysterious outbreak in 1518 left hundreds dancing uncontrollably.",
    }

    payload = script_writer._safe_fallback_script(context, hook)

    assert payload["hook"] == "They danced until their feet bled"
    assert payload["total_words"] >= 90
    assert "In July 1518" in payload["description_draft"]
    assert "mass psychogenic illness" in payload["description_draft"]
    body_notes = [item["visual_note"] for item in payload["segments"] if item["type"] == "body"]
    assert len(set(body_notes)) == len(body_notes)
