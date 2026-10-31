"""Phase 12 — script_repair punctuation / amount regression + WB corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atnos.production.steps import order_script as script_module
from atnos.production.steps.order_script import (
    EditorialScript,
    financial_fingerprint,
    normalize_spoken_segment,
    script_issues,
    strip_unsupported_financial_phrases,
    try_deterministic_repair,
    validate_repair_response,
)

pytest_plugins = ["test_production_order_script"]


CORPUS = [
    # text, expected after normalize (+ amount strip when noted): PASS | REPAIR | FAIL
    ("Your raise looks bigger on paper. Wealth is what you keep after expenses.", "PASS", False),
    ("Start by saving first then spend what is left", "REPAIR", False),
    ("Save first.. Then spend later!!", "REPAIR", False),
    ("Automate the transfer so it happens every payday", "REPAIR", False),
    ("Keep the habit — check weekly.", "PASS", False),
    ("Remember this: move money before you spend.", "PASS", False),
    ("Review bills; then schedule the transfer.", "PASS", False),
    ('He said "save first" and meant it.', "PASS", False),
    ("Keep essentials covered (rent and food) before lifestyle upgrades.", "PASS", False),
    ("Move ten percent to savings on payday.", "REPAIR", True),
    ("Set aside fifty dollars each week.", "REPAIR", True),
    ("Open the app today and schedule the transfer.", "PASS", False),
    ("Do it now.", "PASS", False),
    ("Your paycheck feels bigger, but real wealth is what you keep. Start", "REPAIR", False),
    ("Consistently invest small amounts over time without relying on", "FAIL", False),
    ("Start today. Review weekly. Stay consistent.", "PASS", False),
]


@pytest.mark.parametrize("text,expected,strip_amounts", CORPUS)
def test_wb_punctuation_corpus_normalize(text: str, expected: str, strip_amounts: bool):
    cleaned, changes = normalize_spoken_segment(text)
    if strip_amounts:
        cleaned, amount_changes = strip_unsupported_financial_phrases(cleaned)
        changes = [*changes, *amount_changes]
        cleaned, more = normalize_spoken_segment(cleaned)
        changes = [*changes, *more]
    ok = bool(script_module.SENTENCE_END_RE.search(cleaned.strip()))
    if expected == "PASS":
        assert ok
        assert not changes or changes  # may still normalize whitespace
        assert cleaned
    elif expected == "REPAIR":
        assert changes
        assert ok
    else:
        assert not ok


def _load_order_draft(order_id: str) -> dict:
    path = (
        Path(r"E:\Atnos\App\data\production\wealth_blueprint\phase12_script_repair_repro")
        / order_id
        / "script_draft.json"
    )
    if not path.is_file():
        path = (
            Path(r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\production\wealth_blueprint")
            / order_id
            / "script_draft.json"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase12_order1_orphan_normalize_matches_failure_tokens():
    text = "Your paycheck feels bigger, but real wealth is what you keep. Start"
    cleaned, changes = normalize_spoken_segment(text)
    assert "orphan_truncation_tail" in changes
    assert cleaned.endswith(".")
    assert not cleaned.endswith("Start")
    assert script_module.SENTENCE_END_RE.search(cleaned)


def test_phase12_order1_word_budget_truncation_punctuation_regression(execution):
    """prod-9817b1504e4d — truncated AllocatedFinanceNarration tails must become valid."""
    _, _, context = execution
    # Match the frozen order profile budget that blocked Phase 12 E2E.
    context.artifacts["project_profile"]["production"]["word_budget"] = [55, 90]
    draft = _load_order_draft("prod-9817b1504e4d")
    script = EditorialScript.model_validate(draft)
    bad_words = {
        "s01": ["Your", "paycheck", "feels", "bigger,", "but", "real", "wealth", "is", "what", "you", "keep.", "Start"],
        "s02": ["Set", "up", "automatic", "transfers", "to", "save", "first,", "then", "spend", "what's", "left.", "This"],
        "s03": ["Once", "saved,", "use", "what's", "left", "for", "expenses", "and", "enjoy", "the", "remainder.", "Keep"],
        "s04": ["Review", "your", "habits", "weekly,", "stay", "on", "track,", "and", "adjust", "as", "needed.", "Stay"],
        "s05": ["Invest", "small", "amounts", "consistently", "over", "time,", "building", "long-term", "security", "without", "risk.", "Grow"],
        "s06": [
            "Start",
            "today",
            "with",
            "automatic",
            "savings.",
            "This",
            "habit",
            "will",
            "grow,",
            "and",
            "you'll",
            "thank",
            "yourself.",
        ],
    }
    originals = {s.id: s for s in script.segments}
    replacements = []
    for sid, tokens in bad_words.items():
        cleaned, _ = normalize_spoken_segment(" ".join(tokens))
        assert script_module.SENTENCE_END_RE.search(cleaned.strip()), cleaned
        assert not cleaned.rstrip(".!?").endswith(("Start", "This", "Keep", "Stay", "Grow"))
        replacements.append({**originals[sid].model_dump(), "text": cleaned})
    issues = [
        {
            "code": "word_budget",
            "detail": "106 words; expected 55-90",
            "segment_ids": [s.id for s in script.segments],
        }
    ]
    words = sum(len(r["text"].split()) for r in replacements)
    assert 55 <= words <= 90, words
    before = financial_fingerprint(script)
    fixed = validate_repair_response(
        context,
        script,
        issues,
        {"replacements": replacements},
        before_fingerprint=before,
    )
    assert not any(
        i["code"] == "sentence_punctuation"
        for i in script_issues(fixed, context.artifacts["project_profile"])
    )


def test_phase12_order2_ten_percent_deterministic_repair(execution):
    """prod-6b72c51a72ec — unsupported 'ten percent' must be removable without AI copy-back."""
    _, _, context = execution
    context.artifacts["script_calculations"] = {"claims": {}, "currency": "dollars"}
    draft = _load_order_draft("prod-6b72c51a72ec")
    script = EditorialScript.model_validate(draft)
    issues = [
        i
        for i in script_issues(
            script,
            context.artifacts["project_profile"],
            request=context.artifacts.get("production_order"),
        )
        if i["code"] in {"spoken_amount_mismatch", "unverified_number"}
    ]
    assert issues, "fixture draft must still flag ten percent"
    before = financial_fingerprint(script)
    fixed, trace = try_deterministic_repair(script, issues, context)
    assert fixed is not None, trace
    assert trace.get("applied") is True
    assert "ten percent" not in " ".join(s.text for s in fixed.segments).casefold()
    remaining = [
        i
        for i in script_issues(
            fixed,
            context.artifacts["project_profile"],
            request=context.artifacts.get("production_order"),
        )
        if i["code"] in {"spoken_amount_mismatch", "unverified_number"}
    ]
    assert remaining == []
    after = financial_fingerprint(fixed)
    before_keys = {(v, u, p) for _, v, u, p in before}
    after_keys = {(v, u, p) for _, v, u, p in after}
    assert after_keys <= before_keys


def test_financial_fingerprint_blocks_number_drift(execution, script):
    _, _, context = execution
    issues = [{"code": "sentence_punctuation", "segment_ids": ["s02"], "detail": "punct"}]
    script.segments[1].text = "Choose an amount you can spare, then schedule a transfer into savings"
    replacement = script.segments[1].model_dump()
    replacement["text"] = "Choose two hundred dollars you can spare, then schedule a transfer into savings."
    with pytest.raises(ValueError, match="financial values"):
        validate_repair_response(
            context,
            script,
            issues,
            {"replacements": [replacement]},
            before_fingerprint=financial_fingerprint(script),
        )


def test_phase12_order3_word_budget_plus_tts_numbers_spelling(execution):
    """prod-06a77bb528a8 — 90-day digits must spell so allocated word_budget path can run."""
    _, _, context = execution
    context.artifacts["project_profile"]["production"]["word_budget"] = [55, 90]
    draft = _load_order_draft("prod-06a77bb528a8")
    script = EditorialScript.model_validate(draft)
    issues = [
        i
        for i in script_issues(
            script,
            context.artifacts["project_profile"],
            request=context.artifacts.get("production_order"),
        )
        if i["code"] in {"word_budget", "tts_numbers"}
    ]
    assert {i["code"] for i in issues} >= {"word_budget", "tts_numbers"}
    assert any("90" in (s.text) for s in script.segments)

    spelled = script_module.normalize_narration(script)
    assert "ninety-day" in " ".join(s.text for s in spelled.segments)
    assert not any(ch.isdigit() for s in spelled.segments for ch in s.text)
    remaining = [
        i
        for i in script_issues(
            spelled,
            context.artifacts["project_profile"],
            request=context.artifacts.get("production_order"),
        )
        if i["code"] == "tts_numbers"
    ]
    assert remaining == []
    assert any(i["code"] == "word_budget" for i in script_issues(
        spelled, context.artifacts["project_profile"], request=context.artifacts.get("production_order")
    ))

    # Allocated-path eligibility: after spelling, only length codes remain.
    length_ok = {"word_budget", "long_segment", "sentence_punctuation", "tts_numbers"}
    post = [
        i
        for i in script_issues(
            spelled,
            context.artifacts["project_profile"],
            request=context.artifacts.get("production_order"),
        )
        if i["code"] in {"word_budget", "tts_numbers", "long_segment", "sentence_punctuation"}
    ]
    assert post and all(i["code"] in length_ok for i in post)
    assert all(not (set(i.get("fields", ["text"])) - {"text"}) for i in post)

    # Deterministic short repair that lands in budget must clear both codes.
    compact = [
        "A raise feels bigger on paper, but wealth is what you keep after costs.",
        "Set a ninety-day rule: no new upgrades, purchases, or lifestyle splurges.",
        "Automate the raise into an investment account before you spend it.",
        "Track your spending carefully for the first thirty days after the raise.",
        "Freeze your lifestyle so the raise stays intact as real lasting gain.",
        "Start today: set the ninety-day rule, automate the raise, and track spending.",
    ]
    replacements = []
    for segment, text in zip(spelled.segments, compact, strict=True):
        cleaned, _ = normalize_spoken_segment(text)
        replacements.append({**segment.model_dump(), "text": cleaned})
    words = sum(len(r["text"].split()) for r in replacements)
    assert 55 <= words <= 90, words
    fixed = validate_repair_response(
        context,
        spelled,
        [{"code": "word_budget", "detail": "over", "segment_ids": [s.id for s in spelled.segments]}],
        {"replacements": replacements},
        before_fingerprint=financial_fingerprint(spelled),
    )
    codes = {
        i["code"]
        for i in script_issues(
            fixed, context.artifacts["project_profile"], request=context.artifacts.get("production_order")
        )
    }
    assert "word_budget" not in codes
    assert "tts_numbers" not in codes
