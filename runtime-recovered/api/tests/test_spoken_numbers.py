import pytest

from atnos.production.spoken_numbers import numeric_mentions, spell_narration_integers
from atnos.production.steps.order_script import NumericClaim, script_issues
from test_production_order_script import script, execution
from test_production_order_store import store


@pytest.mark.parametrize("text,expected", [("one thousand and forty dollars", 1040),
    ("one thousand and four dollars", 1004), ("fifty-two weeks", 52), ("$1,040", 1040),
    ("seven point five percent", 7.5), ("ten thousand dollars", 10000), ("twelve hundred dollars", 1200)])
def test_number_words_have_computable_values(text, expected):
    assert numeric_mentions(text)[0]["value"] == expected


def test_units_keep_adjacent_amounts_separate():
    values = numeric_mentions("twenty dollars every week for fifty-two weeks gives you one thousand and forty dollars")
    assert [(v["value"], v["unit"]) for v in values] == [(20, "dollars"), (52, "weeks"), (1040, "dollars")]


def test_elliptical_one_and_one_time_are_not_new_financial_values(script):
    from copy import deepcopy
    from atnos.production.steps.order_script import financial_fingerprint

    before = deepcopy(script)
    after = deepcopy(script)
    before.segments[0].text = "Is this expense optional, and does it happen repeatedly?"
    after.segments[0].text = "Is this an optional one, a one-time expense, or a recurring one?"
    assert numeric_mentions(after.segments[0].text) == []
    assert financial_fingerprint(after) == financial_fingerprint(before)


def test_one_remains_numeric_for_units_and_bare_amount_assertions():
    mentions = numeric_mentions(
        "One dollar, one percent, one year, and one purchase were recorded; the account changed by one."
    )
    assert [(item["value"], item["unit"]) for item in mentions] == [
        (1, "dollars"), (1, "percent"), (1, "years"), (1, "none"), (1, "none")
    ]


def test_actual_model_mistake_is_rejected_even_when_arithmetic_is_correct(script, execution):
    script.claims = [NumericClaim(id="c5", operation="product", operands=[20, 52], result=1040, unit="dollars",
        spoken_result="one thousand and four dollars", assumptions="Fifty-two weeks of weekly savings")]
    script.segments[4].text = "Saving twenty dollars weekly for fifty-two weeks gives you one thousand and four dollars."
    script.segments[4].claim_ids = ["c5"]
    codes = {i["code"] for i in script_issues(script, execution[2].artifacts["project_profile"])}
    assert {"spoken_result_mismatch", "spoken_amount_mismatch"}.issubset(codes)


@pytest.mark.parametrize("source,expected", [
    ("Save 10 dollars for 52 weeks.", "Save ten dollars for fifty two weeks."),
    ("Save $1,040.", "Save one thousand forty dollars."),
    ("Save $1.", "Save one dollar."),
    ("Save $20 dollars.", "Save twenty dollars."),
    ("This $10 purchase costs $10.", "This ten-dollar purchase costs ten dollars."),
    ("This ten dollars purchase has an opportunity cost.", "This ten-dollar purchase has an opportunity cost."),
    ("A twenty five dollars fee.", "A twenty-five-dollar fee."),
    ("Spend ten dollars and keep five dollars.", "Spend ten dollars and keep five dollars."),
    ("Review 0 transfers.", "Review zero transfers."),
    ("A 20 percent example.", "A twenty percent example."),
    ("Use 0.05 or 20%.", "Use 0.05 or 20%."),
    ("A $10.50 fee.", "A $10.50 fee."),
    ("On 2026-09-12 or 9/12.", "On 2026-09-12 or 9/12."),
    ("Between 10-20; signed -10 and +20.", "Between 10-20; signed -10 and +20."),
    ("Version v2, code 0123 and 7th place.", "Version v2, code 0123 and 7th place."),
    ("An amount of €20 or £30.", "An amount of €20 or £30."),
    ("Numbers 20 30 are adjacent.", "Numbers 20 30 are adjacent."),
    ("Set a 90-day rule.", "Set a ninety-day rule."),
    ("Track the first 30-day window.", "Track the first thirty-day window."),
])
def test_integer_spelling_preserves_amounts_units_and_ambiguous_forms(source, expected):
    actual = spell_narration_integers(source)
    assert actual == expected
    assert [(v['value'], v['unit']) for v in numeric_mentions(actual)] == [
        (v['value'], v['unit']) for v in numeric_mentions(source)]


def test_spelling_cannot_validate_an_unsupported_financial_amount(script, execution):
    from atnos.production.steps.order_script import normalize_narration
    script.segments[0].text = "Can you save $20 every week while keeping your essential bills covered?"
    script.segments[0].on_screen = "$20 per week"
    normalized = normalize_narration(script)
    assert "twenty dollars" in normalized.segments[0].text
    assert normalized.segments[0].on_screen == "$20 per week"
    assert script.segments[0].text.startswith("Can you save $20")
    codes = {issue['code'] for issue in script_issues(normalized, execution[2].artifacts['project_profile'])}
    assert "tts_numbers" not in codes
    assert "spoken_amount_mismatch" in codes


def test_word_budget_is_measured_after_spelling_and_repair_scope_is_preserved(script, execution):
    from atnos.production.steps.order_script import normalize_narration
    script.segments[0].text = " ".join(["520 weeks."] * 9)
    script.segments[1].text = "Keep 10 weeks visible in your calendar."
    normalized = normalize_narration(script, {"s01"})
    assert len(normalized.segments[0].text.split()) == 36
    assert normalized.segments[1] == script.segments[1]
    codes = {issue['code'] for issue in script_issues(normalized, execution[2].artifacts['project_profile'])}
    assert "long_segment" in codes
