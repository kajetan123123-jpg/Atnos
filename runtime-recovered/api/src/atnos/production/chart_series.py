from __future__ import annotations

import math

from atnos.production.spoken_numbers import numeric_mentions
from atnos.production.steps.order_script import NumericClaim, calculate_claim


def compound_checkpoints(claim: dict) -> dict:
    """Derive four dated balances from one checked annual-compounding example."""
    from atnos.production.project_profile import content_hash
    parsed = NumericClaim.model_validate(claim)
    if parsed.operation != 'compound' or parsed.unit not in {'dollars', 'PLN'}:
        raise ValueError('Derived compound series requires one monetary compound claim')
    principal, rate, years = parsed.operands
    if (principal <= 0 or rate <= 0 or years < 3 or not float(years).is_integer()
            or not math.isclose(calculate_claim(parsed), parsed.result, rel_tol=0, abs_tol=1e-8)):
        raise ValueError('Derived compound series has invalid assumptions or result')
    periods = [0, math.floor(years / 3), math.floor(2 * years / 3), int(years)]
    values = [principal * (1 + rate) ** year for year in periods]
    return {'version': 1, 'claim_id': parsed.id,
            'calculation_hash': content_hash(parsed.model_dump(mode='json')),
            'convention': 'annual_compounding_no_additional_deposits',
            'principal': principal, 'annual_rate': rate, 'horizon_years': int(years),
            'unit': parsed.unit, 'years': periods, 'values': values,
            'display_values': [math.floor(value + .5) for value in values],
            'assumption_label': f'Hypothetical · {rate * 100:g}% annually',
            'exclusions_label': 'Before fees, taxes and inflation'}


def validate_compound_checkpoints(series: dict, claims: list[dict]) -> dict:
    matches = [claim for claim in claims if claim.get('id') == series.get('claim_id')]
    if len(matches) != 1:
        raise ValueError('Derived compound series references an unknown or duplicate claim')
    expected = compound_checkpoints(matches[0])
    if series != expected:
        raise ValueError('Derived compound series differs from its verified calculation')
    return expected


def verified_chart_series(claim: dict, narration: str) -> dict:
    parsed = NumericClaim.model_validate(claim)
    calculated = calculate_claim(parsed)
    if parsed.unit not in {"dollars", "PLN"} or not math.isclose(calculated, parsed.result, rel_tol=0, abs_tol=.5):
        raise ValueError("Chart requires a verified monetary calculation")
    if parsed.operation == "monthly_contribution":
        payment, annual_rate, years = parsed.operands
        if payment <= 0:
            raise ValueError("Growth chart requires a positive monthly contribution")
        months, rate = round(years * 12), annual_rate / 12
        values = []
        for index in range(201):
            deposits = math.floor(months * index / 200)
            factor = deposits if rate == 0 else math.expm1(deposits * math.log1p(rate)) / rate
            values.append(payment * factor)
        amount = f"${payment:,.0f}" if parsed.unit == "dollars" else f"{payment:,.0f} PLN"
        return {"values": values, "horizon": years, "period_unit": "years",
                "label": f"{amount}/MO | {annual_rate*100:g}% ASSUMED | {years:g} YEARS"}
    if parsed.operation == "compound":
        principal, rate, periods = parsed.operands
        if principal <= 0 or periods <= 0:
            raise ValueError("Growth chart requires a positive principal and horizon")
        # A claim may support the hook without making that hook a growth scene.
        # Do not reveal a time series until the spoken segment names its horizon.
        if not any(item["unit"] == "years" and math.isclose(item["value"], periods)
                   for item in numeric_mentions(narration)):
            raise ValueError("Compound chart needs its matching time horizon in the narration")
        values = [principal * (1 + rate) ** (periods * index / 200) for index in range(201)]
        return {"values": values, "horizon": periods, "period_unit": "years",
                "label": f"IF IT GROWS {rate*100:g}% / {periods:g} YEARS"}
    if parsed.operation != "product" or len(parsed.operands) != 2:
        raise ValueError("Chart requires compound interest or a recurring contribution calculation")
    contribution, periods = parsed.operands
    if contribution <= 0 or periods < 1 or periods > 5200 or not float(periods).is_integer():
        raise ValueError("Savings chart requires a positive contribution and whole periods")
    mentions = numeric_mentions(narration)
    units = {item["unit"] for item in mentions if item["value"] == periods and item["unit"] in {"weeks", "years"}}
    if len(units) != 1 or not any(item["value"] == contribution and item["unit"] == parsed.unit for item in mentions):
        raise ValueError("Savings chart needs the matching contribution and time unit in its narration")
    unit = units.pop()
    # Each point is the balance after the completed deposits, with no assumed return.
    values = [contribution * math.floor(periods * index / 200) for index in range(201)]
    amount = f"${contribution:,.0f}" if parsed.unit == "dollars" else f"{contribution:,.0f} PLN"
    return {"values": values, "horizon": periods, "period_unit": unit,
            "label": f"{amount} / {unit[:-1].upper()} - {periods:g} {unit.upper()}"}
