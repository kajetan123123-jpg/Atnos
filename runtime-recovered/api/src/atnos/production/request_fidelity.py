"""Small, deterministic constraints for an explicitly requested savings cadence."""
from __future__ import annotations

import re
import unicodedata

CADENCES = {
    'day': r'\b(?:daily|every day|each day|dzienn\w*|codzienn\w*)\b',
    'week': r'\b(?:weekly|every week|each week|tygodniow\w*|cotygodniow\w*|co tydzien)\b',
    'month': r'\b(?:monthly|every month|each month|miesieczn\w*|comiesieczn\w*|co miesiac)\b',
    'year': r'\b(?:yearly|annually|every year|each year|roczn\w*|coroczn\w*)\b',
}
ACTION = r'(?:savings?|transfers?|deposits?|contributions?|payments?)'
CADENCE_RULE = (
    'Preserve the requested cadence in examples, narration, titles and screen text. '
    'A weekly savings request must not become monthly transfers to fit a calculation. '
    'For a practical habit without an explicitly requested calculation, prefer no numerical example. '
    'Do not invent review intervals or a first/second-year timeline. Ordinal-year wording is only '
    'a pronunciation rule when an actual multi-year scenario requires it. '
)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).casefold()) if not unicodedata.combining(c))


def requested_cadence(order):
    if not order or order.get('intake'):
        return None  # Supplied, fact-checked adaptations have their own richer contract.
    topic = normalized(order.get('topic', ''))
    text = topic + ' ' + normalized(order.get('direction', '')) + ' ' + normalized(order.get('optional_notes', ''))
    found = {key for key, pattern in CADENCES.items() if re.search(pattern, text)}
    return next(iter(found)) if len(found) == 1 else None


def request_requires_calculation_example(order) -> bool:
    """True when the production request states an explicit arithmetic example that must be encoded."""
    if not order or order.get("intake"):
        return False
    # optional_notes often carries the data contract / checked values (WB quality briefs).
    text = normalized(
        f"{order.get('topic', '')} {order.get('direction', '')} {order.get('optional_notes', '')}"
    )
    has_arithmetic = bool(
        re.search(r"\b(?:times|multipl(?:y|ied|ication)|equals?|equal to|×|\*|checked values)\b", text)
    )
    has_money_or_period = bool(
        re.search(r"\b(?:dollars?|\$|percent|weeks?|months?|years?|daily|weekly|monthly)\b", text)
    )
    has_spelled_amount_timeline = bool(
        re.search(
            r"\b(?:fifty|twenty|thirty|forty|sixty|hundred|thousand|\d+)\b"
            r".{0,80}\b(?:week|month)\b.{0,80}\b(?:year|annual|annually)\b",
            text,
        )
    )
    return (has_arithmetic and has_money_or_period) or has_spelled_amount_timeline


def validate_calculation_plan_against_request(plan, order):
    """Fail closed when an explicit numeric request produces an empty CalculationPlan."""
    validate_calculation_cadence(plan, order)
    if request_requires_calculation_example(order) and not plan.examples:
        raise ValueError(
            "Request states an explicit arithmetic example, but CalculationPlan.examples is empty. "
            "Encode it as repeated_saving (or another matching kind) with IDs c1/c2; "
            "do not omit the example or leave claims empty."
        )


def validate_calculation_cadence(plan, order):
    cadence = requested_cadence(order)
    if not cadence:
        return
    for example in plan.examples:
        actual = 'month' if example.kind == 'monthly_deposits' else getattr(example, 'period', None)
        if actual and actual != cadence:
            raise ValueError(f'Requested savings cadence is {cadence}, but example {example.id} uses {actual}. '
                             'Use repeated_saving with the requested period, or omit the example. Do not rewrite the request.')


def script_issues(script, order):
    issues = []
    if order and not order.get('intake'):
        direction = str(order.get('direction') or '')
        request_text = normalized(direction + ' ' + str(order.get('optional_notes') or ''))
        explicit_compound_chart = bool(re.search(
            r'\b(?:include|show|plot|use)\b.{0,100}\bcompound\s+(?:growth\s+)?chart\b', request_text))
        if explicit_compound_chart:
            compound_ids = {claim.id for claim in getattr(script, 'claims', []) if claim.operation == 'compound'}
            used = [s for s in script.segments if compound_ids.intersection(s.claim_ids) and s.type != 'cta']
            if not used:
                issues.append({'code':'request_chart_omitted', 'segment_ids':[s.id for s in script.segments],
                    'detail':'The request explicitly requires a hypothetical compound chart. Include a complete narrated example linked to the compiled compound calculation; do not omit the chart or substitute invented company prices.'})
            for segment in used:
                if not re.search(r'\b(?:assum\w*|hypothetical|illustrative|for example)\b',segment.text,re.I):
                    issues.append({'code':'hypothetical_framing_missing','segment_ids':[segment.id],
                        'detail':'State in the spoken numerical example that the rate is assumed or hypothetical; a headline alone does not qualify a return as hypothetical.'})
        if 'watchlist' in request_text and re.search(r'\bstocks?\b',request_text):
            for segment in script.segments:
                for sentence in re.split(r'(?<=[.!?])\s+',segment.text):
                    if (re.search(r'\b(?:ignore|dismiss|disregard)\b.{0,55}\b(?:delays?|financing|funding|regulat\w*|loan losses)\b',sentence,re.I)
                            and not re.search(r"\b(?:never|not|don't|do not)\s+(?:ignore|dismiss|disregard)\b",sentence,re.I)):
                        issues.append({'code':'brief_fidelity','segment_ids':[segment.id],
                            'detail':'Research must evaluate the named thesis risks, not instruct the viewer to ignore them.'})
        exact_cta = re.search(r'\bExact CTA:\s*(.+)$', direction, re.I)
        if exact_cta:
            words = lambda text: re.findall(r"[a-z0-9]+", normalized(text))
            if not script.segments or words(script.segments[-1].text) != words(exact_cta.group(1)):
                issues.append({'code':'request_cta_mismatch',
                    'segment_ids':[script.segments[-1].id] if script.segments else [],
                    'detail':'Preserve the explicitly requested final CTA: ' + exact_cta.group(1)})
    cadence = requested_cadence(order)
    if not cadence:
        return issues
    fields = [('title', script.title, None), ('description', script.description, None)]
    for segment in script.segments:
        fields.extend((field, getattr(segment, field), segment.id) for field in ('text', 'on_screen'))
    for field, value, segment_id in fields:
        text = normalized(value)
        for period, pattern in CADENCES.items():
            if period == cadence:
                continue
            # Weekly habit → yearly total/goal is expected arithmetic framing, not a cadence rewrite.
            if (
                cadence == "week"
                and period == "year"
                and (
                    re.search(r"\b(?:yearly|annual(?:ly)?)\s+(?:savings?\s+)?(?:goal|total|sum|result|amount)\b", text)
                    or (
                        re.search(r"\b(?:in a year|per year|each year|every year|a year)\b", text)
                        and re.search(CADENCES["week"], text)
                    )
                )
            ):
                continue
            # Restrict matches to savings cadence, not e.g. reviewing monthly expenses.
            adjacent = rf'(?:{pattern})\s+(?:(?:automatic|regular|bank)\s+)?{ACTION}\b|\b{ACTION}\s+(?:{pattern})'
            if re.search(adjacent, text):
                issues.append({'code': 'request_cadence', 'segment_ids': [segment_id] if segment_id else [],
                    'fields': [field], 'detail': f'The request specifies {cadence} savings, but {field} changes it to {period}. Preserve the requested cadence.'})
        if (re.search(r'\b(?:review|check|reassess)\b[^.!?]{0,70}\b(?:every|each) (?:second|other|two) year', text)
                and not re.search(r'\b(?:second|other|two|2) year', normalized(str(order)))):
            issues.append({'code': 'invented_review_interval', 'segment_ids': [segment_id] if segment_id else [],
                'fields': [field], 'detail': 'Do not invent a biennial review interval. Keep the requested regular habit and review when circumstances change.'})
    return issues


def requires_action_diagram(segment):
    """Bank settings cannot be illustrated by generic calculator or cash B-roll."""
    text = normalized(segment.text)
    return bool(re.search(r'\b(?:schedule|automate|set up)\b[^.!?]{0,70}\b(?:transfer|payment|deposit)', text)
                or (re.search(r'\b(?:transfer|payment|deposit)', text)
                    and re.search(r'\b(?:automatically|automatic|recurring|standing order)\b', text)))
