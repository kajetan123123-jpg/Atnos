from __future__ import annotations

import pytest

from atnos.production.spoken_numbers import numeric_mentions
from atnos.production.steps.order_script import NumericClaim, calculate_claim


def monthly(payment, rate, years):
    return NumericClaim(id="dca", operation="monthly_contribution", operands=[payment, rate, years],
                        result=0, unit="PLN", spoken_result="zero",
                        assumptions="End-of-month contributions; nominal annual rate / 12; no tax, fees or inflation.")


@pytest.mark.parametrize("payment,rate,years,expected", [
    (100, .08, 10, 18294.60351817066), (100, .08, 20, 58902.04156214548),
    (100, .08, 30, 149035.9448663336), (500, .07, 20, 260463.32991275884),
    (100, 0, 1, 1200), (100, 1e-12, 1, 1200), (0, .08, 30, 0),
])
def test_reviewed_end_of_month_values(payment, rate, years, expected):
    assert calculate_claim(monthly(payment, rate, years)) == pytest.approx(expected, abs=.00001)


@pytest.mark.parametrize("payment,rate,years", [(-1, .08, 10), (100, -.01, 10),
    (100, 8, 10), (100, .08, 0), (100, .08, 101), (100, .08, .1)])
def test_invalid_monthly_contribution_assumptions(payment, rate, years):
    with pytest.raises(ValueError):
        calculate_claim(monthly(payment, rate, years))


def test_monthly_contract_requires_three_operands():
    claim = monthly(100, .08, 10)
    claim.operands = [100, .08]
    with pytest.raises(ValueError, match="nominal annual rate, years"):
        calculate_claim(claim)


def test_pln_is_not_silently_treated_as_dollars_or_unitless():
    assert numeric_mentions("one hundred zlotys")[0]["unit"] == "PLN"
    assert numeric_mentions("PLN 100")[0]["unit"] == "PLN"
    assert numeric_mentions("one hundred dollars")[0]["unit"] == "dollars"
    assert numeric_mentions("one hundred")[0]["unit"] == "none"


def test_pln_annuity_chart_uses_same_formula_and_no_dollar_label():
    from atnos.production.chart_series import verified_chart_series
    claim = monthly(100, .08, 30)
    claim.result = calculate_claim(claim)
    series = verified_chart_series(claim.model_dump(), "one hundred zlotys monthly")
    assert series["values"][0] == 0
    assert series["values"][-1] == pytest.approx(149035.9448663336)
    assert series["values"] == sorted(series["values"])
    assert "100 PLN/MO" in series["label"] and "$" not in series["label"]
    claim.result += 100
    with pytest.raises(ValueError, match="verified monetary"):
        verified_chart_series(claim.model_dump(), "one hundred zlotys monthly")


def test_explicit_spoken_rounding_does_not_change_exact_calculation():
    from atnos.production.steps.order_script import spoken_claim_value
    claim = monthly(100, .08, 30)
    claim.result = calculate_claim(claim)
    claim.spoken_rounding = 1000
    assert spoken_claim_value(claim) == 149000
    assert claim.result == pytest.approx(149035.9448663336)
    claim.unit = "percent"
    with pytest.raises(ValueError, match="monetary"):
        spoken_claim_value(claim)
