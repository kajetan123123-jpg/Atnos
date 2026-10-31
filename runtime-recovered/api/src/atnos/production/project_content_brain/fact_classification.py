"""Klasyfikacja twierdzeń scenariusza — podpięta pod istniejące script_issues, bez drugiego rewritera."""
from __future__ import annotations

from typing import Any

from atnos.production.project_content_brain.types import ClaimClass


# Kody z script_issues / request_fidelity, które mapują się na klasy bez wymyślania źródeł.
_RISKY = frozenset({"unsupported_promise", "unsupported_cta_promise"})
_NEEDS_SOURCE = frozenset(
    {
        "unverified_number",
        "unsourced_tax_fact",
        "unknown_source_fact",
        "unknown_claim",
        "spoken_amount_mismatch",
        "spoken_currency_mismatch",
        "spoken_result_mismatch",
        "spoken_unit_mismatch",
        "arithmetic",
    }
)
_PARTIAL = frozenset({"orphan_claim", "practical_share", "request_cadence"})


def classify_claim_segment(
    *,
    segment: dict[str, Any],
    claim_ids: set[str],
    fact_ids: set[str],
    issue_codes: set[str],
    trusted_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Jedna klasyfikacja na segment narracji — nigdy nie inventuje źródła."""
    trusted_facts = trusted_facts or {}
    seg_issues = issue_codes
    if seg_issues & _RISKY:
        klass: ClaimClass = "risky_promise"
        reason = "Wykryto zabronioną obietnicę inwestycyjną lub CTA bez pokrycia."
        confidence = 0.95
        source = "script_issues"
    elif seg_issues & _NEEDS_SOURCE:
        klass = "needs_source"
        reason = "Liczba/fakt finansowy bez poprawnego claim/fact lub niezgodny z dowodem."
        confidence = 0.9
        source = "script_issues"
    elif segment.get("claim_ids") and all(cid in claim_ids for cid in segment.get("claim_ids") or []):
        if segment.get("fact_ids") and all(
            fid in trusted_facts for fid in segment.get("fact_ids") or []
        ):
            klass = "confirmed"
            reason = "Segment powiązany z zatwierdzonym claim i reviewed fact."
            confidence = 0.85
            source = "claims+reviewed_facts"
        else:
            klass = "confirmed"
            reason = "Segment powiązany z zatwierdzonym claim arytmetycznym (bez zewnętrznego faktu)."
            confidence = 0.8
            source = "numeric_claims"
    elif segment.get("fact_ids"):
        missing = [fid for fid in segment.get("fact_ids") or [] if fid not in trusted_facts]
        if missing:
            klass = "needs_source"
            reason = f"Brak reviewed fact dla: {', '.join(missing)}."
            confidence = 0.9
            source = "reviewed_facts"
        else:
            klass = "confirmed"
            reason = "Segment oparty wyłącznie o reviewed source facts."
            confidence = 0.82
            source = "reviewed_facts"
    elif seg_issues & _PARTIAL:
        klass = "partial"
        reason = "Częściowe problemy strukturalne; treść nie jest w pełni potwierdzona."
        confidence = 0.6
        source = "script_issues"
    else:
        # Bez liczb/claimów — opinia edukacyjna lub szacunek jakościowy; nie "confirmed".
        text = str(segment.get("text") or "")
        if any(marker in text.casefold() for marker in ("about", "around", "roughly", "approximately", "estimate")):
            klass = "estimate"
            reason = "Sformułowanie szacunkowe bez twardego claim — nie wymyślaj liczby."
            confidence = 0.55
            source = "heuristics"
        elif segment.get("type") in {"hook", "cta"}:
            klass = "opinion"
            reason = "Hook/CTA bez claim — traktowane jako opinia/wezwanie, nie fakt."
            confidence = 0.7
            source = "segment_type"
        else:
            klass = "opinion"
            reason = "Brak claim/fact — treść jakościowa, nie potwierdzony fakt finansowy."
            confidence = 0.65
            source = "absence_of_claim"

    return {
        "segment_id": segment.get("id"),
        "classification": klass,
        "reason": reason,
        "confidence": confidence,
        "source": source,
        "claim_ids": list(segment.get("claim_ids") or []),
        "fact_ids": list(segment.get("fact_ids") or []),
        "issue_codes": sorted(seg_issues),
        "invented_source": False,
    }


def classify_script_claims(
    script: Any,
    issues: list[dict],
    *,
    trusted_facts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Zbuduj klasyfikacje z wyniku istniejącego walidatora — bez drugiego silent rewrite."""
    trusted_facts = trusted_facts or {}
    if hasattr(script, "model_dump"):
        payload = script.model_dump(mode="json")
    elif isinstance(script, dict):
        payload = script
    else:
        raise TypeError("script must be EditorialScript or dict")

    claims = {c["id"] for c in payload.get("claims") or [] if isinstance(c, dict) and c.get("id")}
    fact_ids = set(trusted_facts)
    by_segment: dict[str, set[str]] = {}
    for issue in issues:
        code = str(issue.get("code") or "")
        for seg_id in issue.get("segment_ids") or []:
            by_segment.setdefault(str(seg_id), set()).add(code)

    rows = []
    for segment in payload.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        rows.append(
            classify_claim_segment(
                segment=segment,
                claim_ids=claims,
                fact_ids=fact_ids,
                issue_codes=by_segment.get(str(segment.get("id")), set()),
                trusted_facts=trusted_facts,
            )
        )
    return rows


def attach_classifications_to_qa(qa: dict, classifications: list[dict]) -> dict:
    """Doklej klasyfikacje do istniejącego wyniku QA; nie zmienia statusu bramek samodzielnie."""
    out = dict(qa)
    out["claim_classifications"] = classifications
    risky = [c for c in classifications if c.get("classification") == "risky_promise"]
    needs = [c for c in classifications if c.get("classification") == "needs_source"]
    out["claim_classification_summary"] = {
        "risky_promise_count": len(risky),
        "needs_source_count": len(needs),
        "confirmed_count": sum(1 for c in classifications if c.get("classification") == "confirmed"),
        "source_policy": "never_invent_source",
    }
    return out
