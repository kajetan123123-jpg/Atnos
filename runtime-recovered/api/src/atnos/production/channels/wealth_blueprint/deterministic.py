from __future__ import annotations

import math
import re
from typing import Any, Iterable

from atnos.production.channels.wealth_blueprint.layouts import LayoutId
from atnos.production.channels.wealth_blueprint.rules import (
    CHART_HINTS,
    COMPARISON_HINTS,
    ICON_HINTS,
    MAX_ON_SCREEN_WORDS,
    MAX_SCENES,
    MIN_SCENE_DURATION_SEC,
    NUMBER_PATTERN_HINTS,
)
from atnos.production.channels.wealth_blueprint.scene_plan_models import (
    AnimationCue,
    AssetNeed,
    ChartCue,
    NarrationSegment,
    PlannedScene,
    SceneMode,
    ScenePlannerInput,
    SoundCue,
    VisualIntent,
    WealthBlueprintScenePlan,
)
from atnos.production.channels.wealth_blueprint.visual_concepts import (
    concept_prefers_icon_explainer,
    icon_tags_for_concept,
    infer_visual_concept,
)


def _words(text: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9$%.,'-]+", text) if token]


def _has_digit_token(text: str) -> bool:
    return bool(re.search(r"\d|\$|%", text)) or any(hint in text.casefold() for hint in NUMBER_PATTERN_HINTS)


def _contains_any(text: str, hints: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(hint in lowered for hint in hints)


def _short_on_screen(text: str, *, prefer_number: bool = False) -> str:
    tokens = _words(text)
    if not tokens:
        return "Key idea"
    if prefer_number:
        for token in tokens:
            if re.search(r"\d|\$|%", token):
                return token[:32]
    stop = {"the", "a", "an", "and", "or", "to", "of", "in", "for", "that", "this", "is", "are", "be", "it"}
    dangling = stop | {"when", "where", "your", "with", "before", "after", "from", "into"}
    # Keep on-screen punchy: never more than ~35% of narration words, capped.
    budget = max(2, min(MAX_ON_SCREEN_WORDS, max(2, len(tokens) // 3)))
    selected: list[str] = []
    for token in tokens:
        if token.casefold() in stop and len(selected) < 1:
            continue
        selected.append(token)
        if len(selected) >= budget:
            break
    while len(selected) > 2 and selected[-1].casefold().strip(".,!?") in dangling:
        selected.pop()
    return " ".join(selected)[:120]


def _extract_number_label(text: str) -> str | None:
    match = re.search(r"\$?\d[\d,]*(?:\.\d+)?%?", text)
    if match:
        return match.group(0)
    # spelled-out percent-like
    match = re.search(r"\b(\d{1,3})\s*percent\b", text.casefold())
    if match:
        return f"{match.group(1)}%"
    return None


def _claims_for_beat(beat_id: str, input_data: ScenePlannerInput) -> list[dict[str, Any]]:
    by_id = {
        str(claim.get("id")): claim
        for claim in input_data.claims
        if isinstance(claim, dict) and claim.get("id")
    }
    claim_ids = [str(cid) for cid in (input_data.segment_claim_map.get(beat_id) or []) if cid]
    return [by_id[cid] for cid in claim_ids if cid in by_id]


def _describes_verified_compound_horizon(narration: str, claims: list[dict[str, Any]]) -> bool:
    """Route an explicitly stated rate and horizon to their verified growth model.

    Merely mentioning a deposit does not request a growth chart. Both units must
    occur in this beat, so a dollar amount, age, or percent cannot become a USD
    checkpoint by keyword matching.
    """
    if len(claims) != 1:
        return False
    try:
        from atnos.production.chart_series import compound_checkpoints
        from atnos.production.spoken_numbers import numeric_mentions
        from atnos.production.steps.order_script import NumericClaim

        parsed = NumericClaim.model_validate(claims[0])
        if parsed.operation != "compound":
            return False
        series = compound_checkpoints(parsed.model_dump(mode="json"))
        mentions = numeric_mentions(narration)
        rate_matches = any(item["unit"] == "percent" and math.isclose(
            float(item["value"]), series["annual_rate"] * 100, abs_tol=1e-8, rel_tol=0,
        ) for item in mentions)
        horizon_matches = any(item["unit"] == "years" and math.isclose(
            float(item["value"]), series["horizon_years"], abs_tol=1e-8, rel_tol=0,
        ) for item in mentions)
        return rate_matches and horizon_matches
    except (ValueError, TypeError, IndexError):
        return False


def _narration_backed_claim_values(claim: dict[str, Any], narration: str) -> list[float]:
    """Values spoken in narration that are covered by a verified claim (operands/result)."""
    from atnos.production.spoken_numbers import numeric_mentions
    from atnos.production.steps.order_script import NumericClaim, calculate_claim, spoken_claim_value

    parsed = NumericClaim.model_validate(claim)
    calculated = calculate_claim(parsed)
    money_tol = 0.5 if parsed.unit in {"dollars", "PLN"} else 0.005
    if not math.isclose(calculated, parsed.result, rel_tol=0, abs_tol=money_tol):
        raise ValueError(f"Claim {parsed.id} result does not match verified calculation")

    allowed: list[float] = [parsed.result, float(spoken_claim_value(parsed)), *[float(v) for v in parsed.operands]]
    values: list[float] = []
    seen: set[float] = set()

    def _accept(value: float) -> None:
        key = round(value, 6)
        if key not in seen:
            seen.add(key)
            values.append(value)

    # Exact spoken_result phrase in narration → include verified spoken value.
    if parsed.spoken_result and parsed.spoken_result.casefold() in narration.casefold():
        _accept(float(spoken_claim_value(parsed)))

    for mention in numeric_mentions(narration):
        value = float(mention["value"])
        for allowed_v in allowed:
            # Rates/fractions need tight equality; currency amounts allow spoken rounding.
            match_tol = money_tol if abs(allowed_v) >= 1.0 else 1e-9
            if math.isclose(value, allowed_v, rel_tol=0, abs_tol=match_tol):
                _accept(value)
                break
    return values


def _source_backed_labels(raw_labels: list[str], narration: str, count: int) -> list[str]:
    """Keep only labels that already appear in the approved narration; never invent A/B."""
    if count < 1:
        return []
    lowered = narration.casefold()
    kept: list[str] = []
    for label in raw_labels:
        text = str(label).strip()
        if text and text.casefold() in lowered:
            kept.append(text)
        if len(kept) >= count:
            break
    return kept if len(kept) == count else []


def _qualitative_chart(title: str) -> ChartCue:
    """Non-numeric diagram cue — Remotion ChartScene must not be emitted from this."""
    return ChartCue(
        chart_type="QUALITATIVE",
        title=(title or "Key idea")[:80],
        labels=[],
        values=[],
        claim_ids=[],
        unit=None,
    )


def _build_chart_cue(
    *,
    focus: str,
    narration: str,
    title: str,
    claims: list[dict[str, Any]],
    label_hints: list[str] | None = None,
) -> ChartCue:
    """Quantitative chart only from verified claims + narration-backed numbers.

    Without an approved data source, returns a qualitative cue (empty values).
    Never injects demo series such as [1.0, 2.4] or [1.0, 0.6].
    """
    label_hints = list(label_hints or [])
    if focus == "chart" and len(claims) == 1:
        claim = claims[0]
        try:
            from atnos.production.steps.order_script import NumericClaim

            parsed = NumericClaim.model_validate(claim)
            if (parsed.operation == 'compound' and parsed.operands[2] >= 3
                    and float(parsed.operands[2]).is_integer()):
                from atnos.production.chart_series import compound_checkpoints
                from atnos.production.chart_series import verified_chart_series
                verified_chart_series(parsed.model_dump(), narration)
                derived = compound_checkpoints(parsed.model_dump(mode='json'))
                return ChartCue(chart_type='COMPOUND_GROWTH', title=(title or 'A hypothetical path')[:80],
                    labels=[f'YEAR {year}' for year in derived['years']], values=derived['values'],
                    claim_ids=[parsed.id], unit=parsed.unit, verified_series=derived)
            # Growth charts must pass the shared verified-series gate when applicable.
            if parsed.operation in {"compound", "monthly_contribution", "product"}:
                from atnos.production.chart_series import verified_chart_series

                verified_chart_series(parsed.model_dump(), narration)
            values = _narration_backed_claim_values(claim, narration)
            if len(values) < 2:
                return _qualitative_chart(title)
            labels = _source_backed_labels(label_hints, narration, len(values))
            chart_type = (
                "COMPOUND_GROWTH"
                if parsed.operation in {"compound", "monthly_contribution"}
                or "compound" in narration.casefold()
                else "LINE_CHART"
            )
            return ChartCue(
                chart_type=chart_type,
                title=(title or "Key idea")[:80],
                labels=labels,
                values=values[:8],
                claim_ids=[parsed.id],
                unit=parsed.unit,
            )
        except Exception:
            return _qualitative_chart(title)

    if focus == "comparison" and len(claims) >= 2:
        try:
            from atnos.production.steps.order_script import NumericClaim

            left = NumericClaim.model_validate(claims[0])
            right = NumericClaim.model_validate(claims[1])
            values = (
                _narration_backed_claim_values(claims[0], narration)[:1]
                + _narration_backed_claim_values(claims[1], narration)[:1]
            )
            if len(values) < 2:
                text_values = _narration_backed_claim_values(claims[0], narration) + _narration_backed_claim_values(
                    claims[1], narration
                )
                if len(text_values) >= 2:
                    values = text_values[:2]
                else:
                    return _qualitative_chart(title)
            labels = _source_backed_labels(label_hints, narration, 2)
            if len(labels) != 2:
                # Pull comparison poles from narration when planner omitted explicit hints.
                auto_hints = [
                    token
                    for token in (
                        "early saver",
                        "late saver",
                        "option a",
                        "option b",
                        "path a",
                        "path b",
                        "save first",
                        "spend first",
                    )
                    if token in narration.casefold()
                ]
                labels = _source_backed_labels(auto_hints or label_hints, narration, 2)
            if len(labels) != 2:
                # Comparison without narration-backed labels → qualitative / block quantitative.
                return _qualitative_chart(title)
            return ChartCue(
                chart_type="COMPARISON",
                title=(title or "Key idea")[:80],
                labels=labels,
                values=values[:2],
                claim_ids=[left.id, right.id],
                unit=left.unit if left.unit == right.unit else None,
            )
        except Exception:
            return _qualitative_chart(title)

    # Chart/comparison focus without approved claims → qualitative diagram only.
    return _qualitative_chart(title)


def build_beats(input_data: ScenePlannerInput) -> list[NarrationSegment]:
    if input_data.segments:
        segments = [
            segment.model_copy(update={
                "source_segment_ids": segment.source_segment_ids or [segment.id],
                "source_visual_intents": segment.source_visual_intents or (
                    [segment.visual_intent] if segment.visual_intent else []
                ),
            })
            for segment in input_data.segments
        ]
    else:
        parts = re.split(r"(?<=[.!?])\s+", input_data.script.strip())
        parts = [part.strip() for part in parts if part.strip()]
        # Approximate timing by word share if no timeline provided
        total_words = max(1, sum(len(_words(part)) for part in parts))
        # Default ~2.8s per sentence floor via duration estimate 0.35s/word
        cursor = 0.0
        segments = []
        for index, part in enumerate(parts, start=1):
            weight = len(_words(part)) / total_words
            duration = max(MIN_SCENE_DURATION_SEC, weight * max(8.0, total_words * 0.35))
            segments.append(
                NarrationSegment(
                    id=f"n{index:02d}",
                    source_segment_ids=[f"n{index:02d}"],
                    text=part,
                    start=round(cursor, 3),
                    end=round(cursor + duration, 3),
                )
            )
            cursor += duration

    # Merge beats below min scene duration into the previous beat
    merged: list[NarrationSegment] = []
    for segment in segments:
        duration = segment.end - segment.start
        if merged and duration < MIN_SCENE_DURATION_SEC:
            prev = merged[-1]
            merged[-1] = NarrationSegment(
                id=prev.id,
                source_segment_ids=[*(prev.source_segment_ids or [prev.id]), *(segment.source_segment_ids or [segment.id])],
                source_visual_intents=[*prev.source_visual_intents, *segment.source_visual_intents],
                text=f"{prev.text} {segment.text}".strip(),
                visual_intent=" ".join(filter(None, [prev.visual_intent, segment.visual_intent])),
                start=prev.start,
                end=segment.end,
            )
        else:
            merged.append(segment)
    # Second pass: if first beat is still short, fold into the next
    if len(merged) >= 2 and (merged[0].end - merged[0].start) < MIN_SCENE_DURATION_SEC:
        first, second = merged[0], merged[1]
        merged = [
            NarrationSegment(
                id=first.id,
                source_segment_ids=[*(first.source_segment_ids or [first.id]), *(second.source_segment_ids or [second.id])],
                source_visual_intents=[*first.source_visual_intents, *second.source_visual_intents],
                text=f"{first.text} {second.text}".strip(),
                visual_intent=" ".join(filter(None, [first.visual_intent, second.visual_intent])),
                start=first.start,
                end=second.end,
            ),
            *merged[2:],
        ]

    # Cap scene count by merging pairs from the middle if needed
    while len(merged) > MAX_SCENES:
        mid = len(merged) // 2
        left, right = merged[mid - 1], merged[mid]
        merged = [
            *merged[: mid - 1],
            NarrationSegment(
                id=left.id,
                source_segment_ids=[*(left.source_segment_ids or [left.id]), *(right.source_segment_ids or [right.id])],
                source_visual_intents=[*left.source_visual_intents, *right.source_visual_intents],
                text=f"{left.text} {right.text}".strip(),
                visual_intent=" ".join(filter(None, [left.visual_intent, right.visual_intent])),
                start=left.start,
                end=right.end,
            ),
            *merged[mid + 1 :],
        ]
    return merged


def classify_focus(text: str) -> tuple[str, LayoutId]:
    if _contains_any(text, COMPARISON_HINTS) or infer_visual_concept(text) == "comparison":
        return "comparison", "comparison"
    if _contains_any(text, CHART_HINTS):
        return "chart", "chart_focus"
    if _has_digit_token(text):
        return "number", "big_number"
    concept = infer_visual_concept(text)
    if concept_prefers_icon_explainer(concept) or _contains_any(text, ICON_HINTS):
        return "icon", "icon_explainer"
    if text.strip().endswith("?"):
        return "statement", "fullscreen_statement"
    return "statement", "fullscreen_statement"


def _next_layout(preferred: LayoutId, previous: LayoutId | None, *, alex: bool) -> LayoutId:
    if previous != preferred:
        return preferred
    # Avoid consecutive repeats (rule 7)
    alternatives: list[LayoutId]
    if alex:
        alternatives = ["character_left_text_right", "character_right_text_left", "split_screen"]
    elif preferred == "fullscreen_statement":
        alternatives = ["quote_or_fact", "three_points", "icon_explainer"]
    elif preferred == "big_number":
        alternatives = ["chart_focus", "fullscreen_statement"]
    elif preferred == "chart_focus":
        alternatives = ["fullscreen_statement", "comparison", "quote_or_fact"]
    else:
        alternatives = ["fullscreen_statement", "quote_or_fact", "icon_explainer"]
    for layout in alternatives:
        if layout != previous:
            return layout
    return preferred


def _alex_pose_for(text: str) -> str:
    lowered = text.casefold()
    if any(token in lowered for token in ("point", "look at", "see this", "this number", "this chart", "here")):
        return "pointing"
    if any(token in lowered for token in ("wait", "but", "however", "surprise", "instead", "actually")):
        return "reacting"
    return "explaining"


def _should_use_alex(index: int, total: int, focus: str, text: str) -> bool:
    """Alex only for an explicit host beat or a brief that requires him — never decorative filler."""
    lowered = text.casefold()
    # Explicit host action always wins — even beside a mechanism diagram.
    if any(
        token in lowered
        for token in (
            "alex explains",
            "alex points",
            "alex shows",
            "alex walks",
            "host explains",
            "points to the",
            "points at the",
        )
    ):
        return index > 0
    if focus in {"chart", "number", "comparison", "icon"}:
        return False
    if index == 0:
        return False  # hook stays fullscreen without decorative host
    # Loose savings verbs alone must not summon Alex — that created filler hosts on brief A.
    functional = any(
        token in lowered
        for token in (
            "alex explains",
            "alex points",
            "alex shows",
            "means that",
            "look at",
            "notice how",
            "points to",
            "points at",
        )
    )
    if not functional:
        return False
    if "you" in lowered or "we" in lowered:
        return index % 3 == 1
    return index in {1, max(1, total // 2)}


def _visual_intent_for(
    *,
    concept: str,
    focus: str,
    use_alex: bool,
    on_screen: str,
    beat_text: str,
    chart: ChartCue | None,
    visual_context: str = "",
    reserve_context: bool = False,
    planned_bill_context: bool = False,
) -> VisualIntent:
    from atnos.production.channels.wealth_blueprint.visual_concepts import (
        concept_prefers_icon_explainer,
        semantic_overlay_for_concept,
    )
    from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
    from atnos.production.channels.wealth_blueprint.visual_decision import decide_visual

    if not use_alex and chart is not None and chart.verified_series is not None:
        series = chart.verified_series
        understand = "One deposit can follow this hypothetical annual-compounding path; it is not a forecast"
        initial = "One deposit, with no additional contributions"
        final = "Four dated checkpoints calculated from the same deposit, assumed rate and horizon"
        criterion = "Curve and all checkpoint values match the verified claim; assumptions stay readable"
        condition = str(series["assumption_label"])
        relation = {
            "kind": "compound_growth", "viewer_should_understand": understand,
            "objects": [{"role": "deposit", "label": "one deposit"},
                        {"role": "curve", "label": "hypothetical growth curve"}],
            "initial_state": initial, "action": "trace_verified_curve", "final_state": final,
            "condition": condition, "compliance_criterion": criterion,
            "unsupported_gap": None,
        }
        return VisualIntent(
            viewer_should_understand=understand, visible_objects=["one deposit", *chart.labels],
            visible_change="The verified curve reveals dated checkpoints, then holds for reading",
            scene_mode="diagram", why_this_image="Verified compound values explain the conditional growth sentence",
            dominant_intent="CHANGE_OVER_TIME", layout_family="DATA_STORY",
            primary="one deposit over time", secondary="hypothetical annual rate",
            motion="trace_verified_curve_then_hold", initial_state=initial, action="trace_verified_curve",
            condition=condition, final_state=final, compliance_criterion=criterion, relation=relation,
        )

    relation = parse_scene_relation(beat_text or on_screen, concept=concept,
                                    visual_context=visual_context, reserve_context=reserve_context, planned_bill_context=planned_bill_context)
    kind = str(relation.get("kind") or "")
    if kind == 'reserve_readiness':
        decision = {'dominant_intent':'RELATIONSHIP','layout_family':'MECHANISM',
                    'primary':'reserve readiness','secondary':relation['stage'],'motion':'reveal_then_hold'}
        return VisualIntent(
            viewer_should_understand=relation['viewer_should_understand'],
            visible_objects=[item['label'] for item in relation['objects']],
            visible_change=relation['final_state'],scene_mode='diagram',
            why_this_image='Native qualitative reserve board explains the current need, check or distinction without invented amounts',
            dominant_intent=decision['dominant_intent'],layout_family=decision['layout_family'],
            primary=decision['primary'],secondary=decision['secondary'],motion=decision['motion'],
            initial_state=relation['initial_state'],action=relation['action'],final_state=relation['final_state'],
            compliance_criterion=relation['compliance_criterion'],relation=relation)
    overlay = semantic_overlay_for_concept(concept)  # type: ignore[arg-type]
    if use_alex:
        mode: SceneMode = "character"
        objects = ["Alex", "on-screen idea"]
        change = "Alex points or reacts while the idea stays readable"
        why = "Host guides attention to one concrete element — not a decorative cutout"
    elif focus == "comparison" and chart is not None and chart.is_quantitative:
        mode = "comparison"
        objects = list(chart.labels[:4]) or ["option A", "option B"]
        change = "Side-by-side values from approved claims become readable"
        why = "Comparison uses claim-backed numbers spoken in narration"
    elif kind in {"transfer", "transfer_forbidden", "raise_split", "notebook_log", "habit_repeat"} or concept_prefers_icon_explainer(concept) or overlay:  # type: ignore[arg-type]
        mode = "diagram"
        objects = {
            "raise_absorption": ["the raise", "spend branch", "keep branch"],
            "weekly_transfer": ["source account", "coin path", "destination"],
            "auto_transfer": ["source account", "coin path", "destination"],
            "savings_account": ["checking wallet", "savings jar"],
            "emergency_fund": ["shield", "runway bars"],
            "habit": ["week 1", "week 2", "cushion"],
            "notebook_log": ["notebook", "pen", "transfer line"],
            "money_movement": ["source account", "destination"],
            "raise_split": ["the raise", "spend", "keep"],
            "transfer": [str(relation.get("source") or "source"), str(relation.get("destination") or "destination")],
            "habit_repeat": ["weekly cue", "set-aside", "growing cushion"],
            "transfer_forbidden": ["source account", "blocked path"],
        }.get(
            kind if kind in {"transfer", "transfer_forbidden", "raise_split", "notebook_log", "habit_repeat"} else concept,
            ["finance object", "labeled relation"],
        )
        if relation.get("objects"):
            objects = [str(item.get("label") or item.get("role")) for item in relation["objects"] if isinstance(item, dict)][:8]
        change = str(relation.get("final_state") or {
            "raise_absorption": "Raise forks into spend and keep",
            "weekly_transfer": "Money moves on a repeating day into a separate savings pool",
            "auto_transfer": "Money moves on a repeating day into a separate savings pool",
            "savings_account": "Paycheck splits: spend pocket vs keep pocket",
            "emergency_fund": "Buffer covers several months of runway",
            "habit": "Repeated set-asides accumulate into a cushion",
            "notebook_log": "Transfer line is written in the notebook",
            "money_movement": "Funds leave one pool and arrive in another",
            "compound": "Each period's growth rejoins the base",
        }.get(concept, "Relation between objects clarifies the spoken rule"))[:240]
        why = f"Diagram encodes concept={concept}; relation={kind or 'none'}; overlay={overlay or 'none'}"
    else:
        mode = "statement"
        objects = ["headline"]
        change = "Key phrase stays readable while narration advances"
        why = "Statement beat — no invented chart numbers"

    understand = str(relation.get("viewer_should_understand") or {
        "weekly_transfer": "Automatic weekly transfers move money into savings without willpower",
        "auto_transfer": "Automation moves savings before spending decisions happen",
        "savings_account": "Savings must live in a separate account from spending money",
        "emergency_fund": "An emergency fund is a runway of months, not a random pile",
        "habit": "A simple loop keeps the savings plan moving",
        "notebook_log": "Writing each transfer keeps the habit accountable",
        "money_movement": "Money is redirected on purpose, not leftover",
        "comparison": "Two claim-backed outcomes differ for a clear reason",
        "compound": "Growth stacks on the prior base over repeated periods",
        "generic_finance": on_screen,
    }.get(concept, on_screen))[:240]

    decision = decide_visual(
        f"{beat_text} {on_screen}",
        concept=concept,
        use_alex=use_alex,
        has_number=focus == "number",
        has_chart=chart is not None and chart.is_quantitative,
    )
    decided_relation = decision.get("relation") if isinstance(decision.get("relation"), dict) else relation
    return VisualIntent(
        viewer_should_understand=understand,
        visible_objects=objects[:8],
        visible_change=change[:240],
        scene_mode=mode,
        why_this_image=why[:240],
        dominant_intent=decision["dominant_intent"],  # type: ignore[arg-type]
        layout_family=decision["layout_family"],  # type: ignore[arg-type]
        primary=decision["primary"][:80],
        secondary=decision["secondary"][:80],
        motion=decision["motion"][:80],
        initial_state=str(decided_relation.get("initial_state") or "")[:240],
        action=str(decided_relation.get("action") or "")[:80],
        direction=str(decided_relation.get("direction") or "")[:80],
        condition=str(decided_relation.get("condition") or "")[:120],
        final_state=str(decided_relation.get("final_state") or "")[:240],
        compliance_criterion=str(decided_relation.get("compliance_criterion") or "")[:240],
        relation=decided_relation,
    )


def _request_requires_functional_alex(input_data: ScenePlannerInput) -> bool:
    """Brief explicitly asks for Alex as an on-screen guide/host — not decorative filler."""
    profile = input_data.profile if isinstance(input_data.profile, dict) else {}
    # Order topic/direction must win over LLM script titles that drop the host name.
    parts = [str(part or "").casefold().replace("’", "'") for part in (
        profile.get("topic"), profile.get("direction"), profile.get("optional_notes"),
        profile.get("brief"), input_data.script[:1200],
    )]
    hay = " ".join(parts)
    if any(p in hay for p in ("no alex", "without alex", "do not use alex", "bez alexa")):
        return False
    if not re.search(r"\balex\b", hay):
        return False
    roles = r"(?:(?:the|our|a|an)\s+)?(?:on[- ]screen\s+)?(?:host|guide|presenter)\b"
    actions = r"(?:leads?|guides?|explains?|shows?|walks?|points?|demonstrates?)\b"
    functional_requests = (
        rf"\balex\s+(?:(?:is|as|will be|should be|must be|acts as|serves as)\s+)?{roles}",
        rf"\balex\s*(?:[:,]\s*)?(?:(?:will|should|must)\s+)?{actions}",
        rf"\b(?:use|have|let|make|feature)\s+alex\s+(?:as\s+{roles}|{actions})",
        r"\b(?:host|guide|presenter)\s+(?:is|will be|should be|must be)\s+alex\b",
        r"\balex[- ]led\b",
        r"\bhost[- ]led\b.{0,60}\balex\b",
        r"\balex\b.{0,60}\bhost[- ]led\b",
    )
    for part in parts:
        # Independent requirements must not borrow 'Alex' from a prohibition
        # and 'on-screen' from captions/headlines in a different sentence.
        clauses = re.split(r"[.!?;\n]+|\b(?:but|however)\b", part)
        for clause in clauses:
            if not any(re.search(pattern, clause) for pattern in functional_requests):
                continue
            # A conditional mention does not require a host scene. A separate
            # affirmative 'Alex leads' or 'Alex is the host' requirement still
            # works even when other sentences forbid decorative appearances.
            if re.search(
                r"\b(?:do not|don't|dont|never|must not|should not|shouldn't|avoid|no|not)\b"
                r".{0,40}\balex\b", clause,
            ):
                continue
            if re.search(
                r"\balex\b.{0,60}\b(?:optional|only (?:appear(?:s)? )?(?:if|when)|"
                r"if (?:needed|necessary)|when (?:needed|necessary)|not required|not needed)\b", clause,
            ):
                continue
            return True
    return False


def _raise_illustration_for(text: str) -> dict[str, Any] | None:
    """Literal still subjects for the raise pilot; never invent a transfer animation.

    Select the pictured state from the spoken proposition, not its position in the
    film. A still may illustrate a result, but cannot claim to animate a hand,
    split, money flow, or an accumulating investment return inside the raster.
    """
    hay = text.casefold().replace("’", "'")
    if re.search(
        r"\b(?:do not|don't|never|must not|cannot|can't)\b.{0,32}"
        r"\b(?:set aside|save|keep|transfer|assign(?:ed|ing)?|allocat(?:e|ed|ing)|earmark(?:ed|ing)?)\b", hay,
    ):
        return None  # preserve the ordinary forbidden-action relation, not a false choice still
    savings_action = any(re.search(r"\b" + re.escape(phrase) + r"\b", hay) for phrase in (
        "set aside", "setting aside", "keep part", "keeping part", "keep some", "keep a",
        "save part", "saving part", "save some", "save a", "save for", "saving for",
        "separate", "reserve", "reserving", "redirect", "redirecting", "put part", "put some",
        "choose to save", "decide to save", "pay yourself", "move part", "transfer part",
        "allocate a portion", "allocating a portion", "allocate part", "allocating part",
        "earmark", "earmarking",
    ))
    savings_action = savings_action or bool(re.search(
        r"\b(?:choose|decide)\b.{0,70}\b(?:save|saving|savings|keep|money goes)\b", hay
    ))
    savings_action = savings_action or bool(re.search(
        r"\b(?:assign(?:ing)?|allocat(?:e|ing))\b.{0,90}"
        r"\b(?:savings?|specific purpose|future (?:goal|need)|reserve|buffer)\b", hay,
    ))
    goal_result = any(phrase in hay for phrase in (
        "future", "goal", "buffer", "freedom", "still available", "remains available",
        "money you keep", "money left", "kept money", "remain yours", "stays yours",
    ))
    goal_result = goal_result or bool(re.search(
        r"\b(?:money|funds|savings|portion|part)\b.{0,55}\bavailable\b.{0,25}\blater\b", hay,
    ))
    expense_state = any(phrase in hay for phrase in (
        "expense", "subscription", "payment", "bill", "cost", "lifestyle creep",
        "upgrade", "spending grows", "spending rise", "spend it", "absorbs", "absorbed",
        "disappears", "disappear", "uses up", "eats up", "swallows",
    ))
    income_state = any(phrase in hay for phrase in (
        "raise", "pay rise", "paycheck", "pay cheque", "extra income", "higher income",
        "income increase", "income grows", "earn more", "earning more", "bigger salary",
    ))
    # A result sentence may mention the preceding action as its cause. Show the
    # actually stated consequence, rather than repeating the earlier choice.
    explicit_goal_result = goal_result and bool(re.search(
        r"\b(?:available|remains|remain|stays|kept|reserved)\b", hay
    ))
    if savings_action and not explicit_goal_result:
        function = "savings_choice"
        subject = "a clear glass savings jar beside a closed navy leather spending wallet"
        objects = {"savings": "clear glass savings jar", "spending": "navy leather spending wallet"}
        state = "The savings jar and spending wallet remain physically separate."
        understand = "Part of the additional income can be reserved instead of being absorbed by spending."
        composition = "Jar on the right, wallet on the left, generous space between them. No arrow or motion is painted into the image."
    elif goal_result:
        function = "future_goal"
        subject = "a clear glass savings jar with reserved money beside a plain closed planning notebook"
        objects = {"savings": "clear glass savings jar", "goal": "plain closed planning notebook"}
        state = "Reserved money remains beside the planning notebook; no growth or guaranteed return is implied."
        understand = "Money kept available can support a future goal; merely saving it does not guarantee investment growth."
        composition = "Jar is the dominant subject, notebook beside it. Show the same jar design as the savings-choice shot."
    elif expense_state and not (hay.rstrip().endswith("?") and income_state):
        function = "recurring_expense"
        subject = "an open navy leather spending wallet with a plain unprinted purchase receipt"
        objects = {"spending": "navy leather spending wallet", "expense": "plain unprinted purchase receipt"}
        state = "The spending wallet is paired with an expense receipt; no savings jar is present."
        understand = "A new recurring expense can absorb the additional income."
        composition = "Wallet and blank receipt dominate the frame. Do not add shopping products or an unrelated luxury object."
    elif income_state:
        function = "extra_income"
        # Actual SDXL trials drew ornamental currency despite blank/negative
        # instructions. Name only the literal unprinted props in conditioning;
        # narration and the money role retain their financial meaning.
        subject = "one open cream paper envelope with several plain unprinted teal paper cards inside"
        objects = {"income": "one cream paper envelope", "money": "plain unprinted teal paper cards with blank faces"}
        state = "An opened envelope makes the additional income visible before any spending choice."
        understand = "A pay raise brings additional income; it does not by itself prove an increase in savings."
        composition = (
            "Open triangular flap, blank teal card faces visible inside. Paper folds, soft shadows; "
            "entire envelope centered with side margins. "
            "Avoid: extra envelopes, printed currency, portraits, stamps, flowers, wax seals, invitations, wallets."
        )
    else:
        return None
    return {
        "function": function,
        "concept": f"wb_raise_{function}",
        "subject": subject,
        "objects": objects,
        "state": state,
        "understand": understand,
        "composition": composition,
    }


def _source_physical_illustration_for(intents: list[str]) -> dict[str, Any] | None:
    """Map selected explicit source visual intents to honest static props.

    This reads only the script's directing handoff. It never guesses from the
    topic or narration, and unknown physical scenes remain unsupported.
    """
    if not intents or any(not str(value).strip() for value in intents):
        return None

    def one(value: str) -> dict[str, Any] | None:
        text = str(value).casefold().replace("’", "'")
        positive = re.split(r"\b(?:no|without)\b", text, maxsplit=1)[0]
        if (re.search(r"\bcar\b", positive) and re.search(r"\bgarage\b", positive)
                and re.search(r"\b(?:parked|still|stationary)\b", positive)
                and not re.search(r"\b(?:alex|person|host|footage|sign|label|text|drives?|driving|moves?|moving)\b", positive)):
            return {
                "function": "car_repair_garage", "concept": "wb_car_repair_garage",
                "subject": "one stationary mint-colored car in a quiet garage",
                "objects": {"car": "one stationary mint-colored car", "setting": "quiet garage"},
                "state": "The car remains parked; readiness to cover a repair is still a question.",
                "understand": "A car repair is a concrete unexpected need; the picture does not assert its price.",
                "composition": "One parked car in a warm cream garage, fully visible in the central frame. Layered indigo shadows, mint bodywork, amber lighting and coral accents. No people, signs, prices, writing or logos. Quiet areas for renderer headings and captions.",
            }
        # Recognize only the specific bounded synonym used in the approved
        # image direction; generic paper remains an unsupported subject.
        has_bill = bool(re.search(r"\b(?:bill|bank\s*note|banknote|paper\s+slip)\b", text))
        has_bag = bool(re.search(r"\b(?:shopping\s+)?bag\b", text))
        if has_bill and has_bag:
            return {
                "function": "bill_and_bag",
                "concept": "wb_bill_and_bag",
                "subject": "one blank-faced teal paper slip beside one small unbranded shopping bag",
                "objects": {"money": "one blank-faced teal paper slip", "bag": "one small unbranded shopping bag"},
                "state": "The single blank slip and small bag remain separate and still.",
                "understand": "The purchase has an opportunity cost, without implying a financial result.",
                "composition": (
                    "Keep both requested props fully visible with a clear gap; leave the paper face blank for a later overlay. "
                    "Place the pair on a home-office desk with quiet caption areas. Cinematic directional warm-gold practical light, "
                    "deep charcoal-navy shadows, cream-paper surfaces, restrained teal, soft painted 2.5D depth and paper texture. "
                    "Avoid: banknotes, bills, currency portraits, stacks or piles of cash, duplicate slips, extra bags, unrelated desk props, "
                    "flat gray line art, monochrome shading."
                ),
            }
        if has_bill and re.search(r"\b(?:investment\s+account|account\s+(?:screen|ui)|phone|smartphone)\b", text):
            return {
                "function": "bill_and_account_ui",
                "concept": "wb_bill_and_account_ui",
                "subject": "one blank-faced teal paper slip beside one simple smartphone with a blank cream screen",
                "objects": {"money": "one blank-faced teal paper slip", "account_ui": "one simple smartphone with a blank cream screen"},
                "state": "The single blank slip and simple phone remain separate and still; no balance or gain is shown.",
                "understand": "The same amount can be spent or considered for an investment account, without asserting a return.",
                "composition": (
                    "Exactly one blank-faced teal slip (left) and one simple phone (right), fully visible with a clear gap. "
                    "Empty cream screen; no text or amount. Desk, quiet caption areas; warm-gold light, deep charcoal-navy shadows, "
                    "cream paper, restrained teal, soft painted depth and texture. "
                    "Avoid: banknotes, currency portraits, cash stacks, duplicate slips, extra phones or bags, logos, text, letters, "
                    "numbers, currency symbols, balances, gains, charts, arrows, unrelated props, gray line art, monochrome."
                ),
            }
        has_two_goal_apps = bool(
            re.search(r"\b(?:budgeting|budget)\s+apps?\b", text)
            and re.search(r"\b(?:phone|smartphone)\b", text)
            and re.search(r"\b(?:two\s+(?:separate\s+)?goals|split\s+screen)\b", text)
        )
        if has_two_goal_apps:
            return {
                "function": "two_goal_phone",
                "concept": "wb_two_goal_phone",
                "subject": "one dark navy smartphone with two blank adjacent goal panels",
                "objects": {"phone": "one dark navy smartphone", "goals": "two blank adjacent goal panels"},
                "state": "Two separate goal panels remain visible on one phone; no amount or allocation is shown.",
                "understand": "The same money cannot be assigned to two goals at once.",
                "composition": (
                    "Show one upright smartphone as the sole focal object, with two equal blank cream panels side by side. "
                    "Keep the panels unmarked for renderer labels; no person, logos, numbers, balances, charts or arrows. "
                    "Use a hand-painted editorial illustration, layered paper depth, deep navy shadows and restrained warm gold."
                ),
            }
        has_single_pot_two_goals = bool(
            re.search(r"\b(?:single|one)\s+(?:savings\s+)?(?:pot|jar|fund)\b", text)
            and re.search(r"\b(?:two\s+paths|splitting\s+into\s+two|two\s+separate\s+goals)\b", text)
        )
        if has_single_pot_two_goals:
            return {
                "function": "one_pot_two_goals",
                "concept": "wb_one_pot_two_goals",
                "subject": "one small teal savings jar beside two separate blank cream goal cards",
                "objects": {"savings": "one small teal savings jar", "goals": "two blank cream goal cards"},
                "state": "One savings pot sits beside two possible goals; no split, transfer or selected winner is shown.",
                "understand": "A single pool of savings can be directed toward either of two competing goals.",
                "composition": (
                    "Show exactly one small teal savings jar centered behind two distinct blank cream goal cards. "
                    "Keep the cards separate and equally prominent; leave them blank for renderer labels. "
                    "No coins in motion, arrows, written labels, amounts, extra jars or unrelated props. "
                    "Use a hand-painted editorial illustration with paper texture, layered navy shadows and warm-gold edge light."
                ),
            }
        has_goal_notebook = bool(
            re.search(r"\bnotebook\b", text)
            and re.search(r"\b(?:writing|written|words)\b", text)
            and re.search(r"\b(?:crossed\s+out|focus\s+on\s+one)\b", text)
            and re.search(r"\b(?:vacation|debt\s+payoff)\b", text)
        )
        if has_goal_notebook:
            return {
                "function": "goal_notebook",
                "concept": "wb_goal_notebook",
                "subject": "one open cream budget notebook with two clear blank goal rows and one brass pencil",
                "objects": {"notebook": "one open cream budget notebook", "pencil": "one brass pencil"},
                "state": "The notebook is open to two distinct blank rows; no goal is crossed out or selected in the illustration.",
                "understand": "A viewer can compare and choose a savings goal without the image inventing which one wins.",
                "composition": (
                    "Place one open cream notebook and one brass pencil on a dark navy desk. Show two large, clean blank rows "
                    "with enough space for renderer overlays. No handwriting, letters, numbers, checkmarks, crossed-out text, "
                    "extra stationery or people. Hand-painted editorial lighting, paper grain and layered shadows."
                ),
            }
        has_priority_prompt = bool(
            re.search(r"\b(?:looking\s+directly\s+at\s+the\s+camera|text\s+card)\b", text)
            and re.search(r"\b(?:priority|what's\s+your\s+priority)\b", text)
        )
        if has_priority_prompt:
            return {
                "function": "priority_prompt_card",
                "concept": "wb_priority_prompt_card",
                "subject": "one blank cream question card with a brass pencil on a navy desk",
                "objects": {"prompt": "one blank cream question card", "pencil": "one brass pencil"},
                "state": "A blank prompt card faces the viewer; the question is added later by the renderer.",
                "understand": "The closing question invites the viewer to choose a personal priority.",
                "composition": (
                    "One large blank cream card faces the viewer on a dark navy desk, with one brass pencil beside it. "
                    "Keep the card empty for the renderer question. No person, handwriting, letters, logos or extra objects. "
                    "Hand-painted editorial illustration, tactile paper grain, layered shadows and restrained warm-gold light."
                ),
            }
        has_coffee = bool(re.search(r"\b(?:coffee\s+cup|cup\s+of\s+coffee|coffee)\b", text))
        explicit_no_x = bool(re.search(
            r"\b(?:no|without|free\s+of)\s+(?:(?:a|the)\s+)?(?:red\s+)?x\b", text
        ))
        explicit_uncrossed = bool(re.search(
            r"\buncrossed\b|\bnot\s+crossed\s+out\b", text
        ))
        reassurance = bool(re.search(
            r"\b(?:not\s+the\s+point|reassur\w*|does\s+not\s+mean|doesn't\s+mean)\b", text
        ))
        # A held cup cannot honestly represent a crossed-out state or an X/hand
        # being animated away. Explicit absent/uncrossed wording is a bounded
        # static state; generic coffee mentions still need reassurance language.
        crossed_out = bool(re.search(r"(?<!not )\bcrossed[\s-]+out\b", text))
        x_remains = bool(re.search(
            r"\b(?:red\s+)?x\s+(?:remains?|stays?|is\s+(?:still\s+)?(?:there|visible))\b", text
        )) and not explicit_no_x
        marker_motion = bool(re.search(
            r"(?:\b(?:red\s+)?x\b|\bhand\b)[^.;]{0,60}\b(?:moves?|moved|moving|removes?|removed|removing|disappears?|disappeared|vanishes?|vanished|fades?|faded|crosses|crossed|crossing|wipes?|wiped)\b"
            r"|\b(?:moves?|moved|moving|removes?|removed|removing|disappears?|disappeared|vanishes?|vanished|fades?|faded|crosses|crossed|crossing|wipes?|wiped)\b[^.;]{0,60}(?:\b(?:red\s+)?x\b|\bhand\b)",
            text,
        ))
        if has_coffee and (reassurance or explicit_no_x or explicit_uncrossed) and not (
            crossed_out or x_remains or marker_motion
        ):
            return {
                "function": "coffee_reassurance",
                "concept": "wb_coffee_reassurance",
                "subject": "one plain unbranded ceramic coffee cup",
                "objects": {"coffee": "plain unbranded ceramic coffee cup"},
                "state": "The cup remains upright, intact, and uncrossed throughout the still.",
                "understand": "The example is about considering trade-offs, not forbidding coffee.",
                "composition": "One ordinary unbranded ceramic coffee cup, upright and intact. Keep the cup uncrossed; no red X is painted into the still.",
            }
        scale_context = bool(re.search(r"\bbuy\s+now\b", text) and re.search(r"\bbuild\s+later\b", text))
        if re.search(r"\b(?:balance\s+scale|two[- ]pan\s+scale)\b", text) or (scale_context and re.search(r"\bscale\b", text)):
            return {
                "function": "balance_scale",
                "concept": "wb_balance_scale",
                "subject": "one plain brass balance scale with two empty unmarked pans",
                "objects": {"scale": "plain brass balance scale with two empty unmarked pans"},
                "state": "The two unmarked pans remain still and balanced; no labels or outcome are implied.",
                "understand": "The choice compares immediate purchase satisfaction with a possible future benefit.",
                "composition": "Show one simple level balance scale with two empty blank pans. Leave clear space for labels added by the renderer. Do not add a person or invent a host pose.",
            }
        return None

    specs = [one(value) for value in intents]
    if any(spec is None for spec in specs):
        return None
    functions = {spec["function"] for spec in specs if spec}
    # A merged beat can retain identity, but cannot collapse distinct source
    # objects into a single image request.
    return specs[0] if len(functions) == 1 else None


def _literal_illustration_intent(spec: dict[str, Any]) -> VisualIntent:
    relation = {
        "kind": "unsupported",
        "viewer_should_understand": spec["understand"],
        "objects": [{"role": role, "label": label, "id": role} for role, label in spec["objects"].items()],
        "initial_state": "The literal props are introduced as one still composition.",
        "action": "hold",
        "direction": None,
        "condition": None,
        "final_state": spec["state"],
        "compliance_criterion": f"Show {', '.join(spec['objects'].values())}; keep all text and amounts in renderer overlays.",
        "unsupported_gap": None,
        "source": None,
        "destination": None,
    }
    return VisualIntent(
        viewer_should_understand=spec["understand"],
        visible_objects=list(spec["objects"].values()),
        visible_change=spec["state"],
        scene_mode="illustration",
        why_this_image="The explicit source visual intent is a literal still; props reveal together and hold without claiming internal motion.",
        dominant_intent="ACTION",
        layout_family="EDITORIAL_HERO",
        primary=next(iter(spec["objects"].values())),
        secondary=list(spec["objects"].values())[-1],
        motion="hold",
        initial_state=relation["initial_state"],
        action="hold",
        final_state=spec["state"],
        compliance_criterion=relation["compliance_criterion"][:240],
        relation=relation,
    )


def _raise_illustration_intent(spec: dict[str, Any]) -> VisualIntent:
    """Describe the real supported reveal/hold, keeping semantic overlays off the still."""
    relation = {
        "kind": "unsupported",  # no unsupported causal motion is requested
        "viewer_should_understand": spec["understand"],
        "objects": [{"role": role, "label": label, "id": role}
                    for role, label in spec["objects"].items()],
        "initial_state": "The literal illustrative state is introduced.",
        "action": "hold",
        "direction": None,
        "condition": None,
        "final_state": spec["state"],
        "compliance_criterion": f"The illustration visibly contains {', '.join(spec['objects'].values())}; no drawn text or unspoken amounts.",
        "unsupported_gap": None,
        "source": None,
        "destination": None,
    }
    return VisualIntent(
        viewer_should_understand=spec["understand"],
        visible_objects=list(spec["objects"].values()),
        visible_change=spec["state"],
        scene_mode="illustration",
        why_this_image="A literal physical state illustrates this spoken proposition; the renderer reveals and holds it rather than claiming motion inside the raster.",
        dominant_intent="CONSEQUENCE" if spec["function"] == "future_goal" else "ACTION",
        layout_family="EDITORIAL_HERO",
        primary=next(iter(spec["objects"].values())),
        secondary=list(spec["objects"].values())[-1],
        motion="hold",
        initial_state=relation["initial_state"],
        action="hold",
        final_state=spec["state"],
        compliance_criterion=relation["compliance_criterion"][:240],
        relation=relation,
    )


def _merge_repeated_literal_holds(scenes: list[PlannedScene]) -> list[PlannedScene]:
    """Keep one object shot across adjacent narration about the identical state.

    Caption ids remain explicit. This does not merge a choice with its result,
    create new state changes, or reset a still just because a sentence ended.
    """
    from atnos.production.channels.wealth_blueprint.shot_language import is_literal_illustration_hold

    merged: list[PlannedScene] = []
    for scene in scenes:
        if not merged or not (is_literal_illustration_hold(merged[-1]) and is_literal_illustration_hold(scene)):
            merged.append(scene)
            continue
        left = merged[-1]
        left_contract, right_contract = left.meaning_contract or {}, scene.meaning_contract or {}
        assert left.visual_intent is not None and scene.visual_intent is not None
        same_state = (
            left_contract.get("illustration_function") == right_contract.get("illustration_function")
            and left.visual_intent.visible_objects == scene.visual_intent.visible_objects
            and left.visual_intent.final_state == scene.visual_intent.final_state
            and abs(left.end_sec - scene.start_sec) < 1e-3
        )
        segment_ids = list(dict.fromkeys([*left.narration_segment_ids, *scene.narration_segment_ids]))
        narration = f"{left.narration_excerpt} {scene.narration_excerpt}".strip()
        if not same_state or len(segment_ids) > 40 or len(narration) > 2000:
            merged.append(scene)
            continue
        contract = dict(left_contract)
        contract["narration"] = narration
        contract["narration_segment_ids"] = segment_ids
        if right_contract.get("source_on_screen_text"):
            contract["source_on_screen_text"] = right_contract["source_on_screen_text"]
            contract["on_screen_origin"] = right_contract.get("on_screen_origin")
        merged[-1] = left.model_copy(update={
            "end_sec": scene.end_sec,
            "narration_excerpt": narration,
            "narration_segment_ids": segment_ids,
            "source_visual_intents": [*left.source_visual_intents, *scene.source_visual_intents],
            "on_screen_text": scene.on_screen_text,
            "emphasis_words": scene.emphasis_words,
            "meaning_contract": contract,
            "rationale": f"{left.rationale}; continuous literal hold for {','.join(segment_ids)}"[:400],
        })
    return merged


def _merge_repeated_reserve_checks(scenes: list[PlannedScene]) -> list[PlannedScene]:
    """Hold one native check across adjacent phrases about the identical board.

    Do not replay the check solely because a narration segment ended. Different
    roles, access focus, headlines or timing remain separate shots. All source
    segment ids survive so audio alignment and captions retain the full speech.
    """
    merged: list[PlannedScene] = []
    for scene in scenes:
        left = merged[-1] if merged else None
        lr = left.visual_intent.relation if left and left.visual_intent else None
        rr = scene.visual_intent.relation if scene.visual_intent else None
        same_check = (
            left is not None and isinstance(lr, dict) and isinstance(rr, dict)
            and lr.get("kind") == rr.get("kind") == "reserve_readiness"
            and lr == rr and left.on_screen_text == scene.on_screen_text
            and left.layout_id == scene.layout_id
            and abs(left.end_sec - scene.start_sec) < 1e-3
            and not (left.chart or scene.chart or left.number_value or scene.number_value
                     or left.asset_needs or scene.asset_needs or left.use_alex or scene.use_alex)
        )
        if not same_check:
            merged.append(scene)
            continue
        assert left is not None
        ids = list(dict.fromkeys([*left.narration_segment_ids, *scene.narration_segment_ids]))
        narration = f"{left.narration_excerpt} {scene.narration_excerpt}".strip()
        intents = list(dict.fromkeys([*left.source_visual_intents, *scene.source_visual_intents]))
        if len(ids) > 40 or len(narration) > 2000 or len(intents) > 40:
            merged.append(scene)
            continue
        meaning = dict(left.meaning_contract or {})
        meaning.update(narration=narration, narration_segment_ids=ids,
                       source_visual_intents=intents)
        merged[-1] = left.model_copy(update={
            "end_sec": scene.end_sec, "narration_excerpt": narration,
            "narration_segment_ids": ids, "source_visual_intents": intents,
            "meaning_contract": meaning,
            "rationale": f"{left.rationale}; continuous identical native check hold"[:400],
        })
    return merged


def _raise_mechanism_story_beats(input_data: ScenePlannerInput, beats: list[NarrationSegment]) -> list[dict | None] | None:
    """Use the existing layered story only for an explicit causal raise brief.

    A broad topic keyword cannot opt an unrelated savings/reserve scene into this
    renderer. Every middle beat must be supported before selecting the sequence.
    Explicit illustrated handoffs keep their own asset/hold contracts.
    """
    profile = input_data.profile if isinstance(input_data.profile, dict) else {}
    direction = " ".join(str(profile.get(key) or "") for key in ("direction", "brief")).casefold()
    if (profile.get("disable_visual_story") or _request_requires_functional_alex(input_data)
            or input_data.design_system.get("visual_style_id") in {"A", "B", "C", "D"}
            or any(beat.visual_intent or beat.source_visual_intents for beat in beats)
            or input_data.claims or any(_has_digit_token(beat.text) for beat in beats)
            or any(re.search(r"\b(?:do not|don't|never|avoid|cannot|can't)\b.{0,60}\b(?:choose|direct|allocate|prioritiz\w*|review|plan|decide|absorb\w*|set aside)\b",
                             beat.text, re.IGNORECASE) for beat in beats)
            or len(beats) < 3):
        return None
    if not (re.search(r'\b(?:raise|pay rise|income increase)\b', direction)
            and re.search(r'\b(?:expenses|costs|commitments)\b.{0,60}\babsorb\w*\b', direction)):
        return None
    from atnos.production.channels.wealth_blueprint.visual_story import plan_raise_story

    stories: list[dict | None] = []
    previous = None
    try:
        for index, beat in enumerate(beats):
            if index == len(beats) - 1:
                stories.append(None)  # Preserve the ordinary closing action/CTA.
                continue
            story = plan_raise_story(beat.text, previous=previous, is_hook=index == 0)
            stories.append(story)
            previous = story["state"]
    except ValueError:
        # Do not manufacture a state for an unsupported sentence. The ordinary
        # planner and its unchanged validation still expose the missing meaning.
        return None
    if not ({"costs", "choice"} <= {story["state"] for story in stories if story}):
        return None
    return stories


def _raise_story_intent(story: dict) -> VisualIntent:
    """Keep the meaning contract aligned with VisualStoryScene's actual action."""
    objects = ["income", "costs", "savings", "goal"]
    relation = {
        "kind": "raise_story", "viewer_should_understand": story["understanding"],
        "objects": [{"role": role, "label": role} for role in objects],
        "initial_state": story["initial_state"], "action": story["action"],
        "final_state": story["final_state"], "compliance_criterion": story["acceptance"],
        "unsupported_gap": None,
    }
    return VisualIntent(
        viewer_should_understand=story["understanding"], visible_objects=objects,
        visible_change=story["final_state"], scene_mode="diagram",
        why_this_image="The existing layered raise sequence advances a causal state, then holds the same allocation for its review.",
        dominant_intent="SPLIT" if story["state"] == "choice" else "CONSEQUENCE",
        layout_family="MECHANISM", primary="income allocation", secondary=story["state"],
        motion=story["action"], initial_state=story["initial_state"], action=story["action"],
        final_state=story["final_state"], compliance_criterion=story["acceptance"], relation=relation,
    )


def _merge_repeated_raise_story_holds(scenes: list[PlannedScene]) -> list[PlannedScene]:
    """Preserve narration across a single hold instead of replaying one action."""
    merged: list[PlannedScene] = []
    for scene in scenes:
        left = merged[-1] if merged else None
        ls, rs = (left.visual_story or {}) if left else {}, scene.visual_story or {}
        if not (left and ls.get("sequence") == rs.get("sequence") == "raise-pilot-v1"
                and ls.get("state") == rs.get("state") == rs.get("previous_state")
                and abs(left.end_sec - scene.start_sec) < 1e-3
                and not scene.source_visual_intents):
            merged.append(scene)
            continue
        segment_ids = list(dict.fromkeys([*left.narration_segment_ids, *scene.narration_segment_ids]))
        narration = f"{left.narration_excerpt} {scene.narration_excerpt}".strip()
        if len(segment_ids) > 40 or len(narration) > 2000:
            merged.append(scene)
            continue
        meaning = dict(left.meaning_contract or {})
        meaning.update(narration=narration, narration_segment_ids=segment_ids)
        merged[-1] = left.model_copy(update={
            "end_sec": scene.end_sec, "narration_excerpt": narration,
            "narration_segment_ids": segment_ids, "meaning_contract": meaning,
            "rationale": f"{left.rationale}; continuous same-state review hold"[:400],
        })
    return merged


def is_native_logo_closure_intent(source_intents):
    """A branded closing statement is renderable even without spoken 'follow'."""
    text=' '.join(source_intents)
    return bool(re.search(r'\b(?:wealth blueprint|channel|brand)\s+logo\b',text,re.I)
                and not re.search(r'\b(?:alex|host|person|bag|bill|phone|coffee|cup|scale|hand)\b',text,re.I))


def plan_scenes_deterministic(input_data: ScenePlannerInput) -> WealthBlueprintScenePlan:
    beats = build_beats(input_data)
    scenes: list[PlannedScene] = []
    previous_layout: LayoutId | None = None
    wants_alex = _request_requires_functional_alex(input_data)
    # Zero budget when the brief does not require a host — prevents decorative Alex on mechanism pilots.
    alex_budget = max(1, int(len(beats) * 0.34)) if wants_alex else 0
    alex_used = 0
    profile = input_data.profile if isinstance(input_data.profile, dict) else {}
    order_hay = " ".join(
        str(part or "")
        for part in (
            profile.get("topic"),
            profile.get("direction"),
            profile.get("optional_notes"),
            input_data.script[:800],
        )
    ).casefold()
    order_raise_theme = any(
        token in order_hay
        for token in (
            "higher income",
            "pay raise",
            "pay rise",
            "absorb",
            "new expenses",
            "lifestyle creep",
            "raise goes",
        )
    )
    style_id = str(input_data.design_system.get("visual_style_id") or "")
    reserve_context = (profile.get('native_illustration_system') == 'wb_planning_desk_painted_v1'
        or any(token in order_hay for token in ('emergency fund','emergency reserve','reserve readiness','ready reserve')))
    # Use the narrated known-bill subject, not just a finance topic keyword.
    planned_bill_context = bool(re.search(r'\b(?:annual|yearly)\s+(?:bills?|expenses?)\b', input_data.script, re.I))
    raise_mechanism_stories = _raise_mechanism_story_beats(input_data, beats)

    for index, beat in enumerate(beats):
        # Keep the spoken words authoritative for claims and captions, while
        # allowing the director's visual handoff to affect scene selection.
        planning_text = f"{beat.text} {beat.visual_intent}".strip()
        focus, preferred = classify_focus(planning_text)
        beat_claims = _claims_for_beat(beat.id, input_data)
        verified_growth_focus = _describes_verified_compound_horizon(beat.text, beat_claims)
        if verified_growth_focus:
            focus, preferred = "chart", "chart_focus"
        layout_repeat_justified = False
        explicit_alex = any(
            token in planning_text.casefold()
            for token in (
                "alex explains",
                "alex points",
                "alex shows",
                "alex walks",
                "host explains",
                "points to the",
                "points at the",
            )
        )
        use_alex = False
        if explicit_alex and index > 0:
            use_alex = True
        elif wants_alex and alex_used < alex_budget:
            use_alex = _should_use_alex(index, len(beats), focus, planning_text)
        # Comparison/chart with claim data must not be replaced by a host cutout.
        if focus in {"comparison", "chart", "number"} and not explicit_alex:
            use_alex = False
        # When the brief requires Alex as host, guarantee at least one non-hook statement beat.
        if (
            wants_alex
            and not use_alex
            and alex_used == 0
            and index >= 1
            and focus in {"statement", "character"}
            and alex_used < max(alex_budget, 1)
            and not explicit_alex
        ):
            use_alex = True
        if use_alex:
            alex_used += 1
            concept_peek = infer_visual_concept(planning_text)
            if concept_prefers_icon_explainer(concept_peek):
                # Alex explains a mechanism: host + diagram, not stick figure alone.
                preferred = "split_screen"
                focus = "character"
            else:
                preferred = "character_left_text_right" if index % 2 else "character_right_text_left"
                focus = "character"
        layout = _next_layout(preferred, previous_layout, alex=use_alex)
        # Mechanism beats must keep icon_explainer / split_screen — do not demote to statement chrome.
        # But never reintroduce consecutive icon_explainer after _next_layout already avoided a repeat.
        concept_for_layout = infer_visual_concept(planning_text)
        if (
            not use_alex and not verified_growth_focus
            and concept_prefers_icon_explainer(concept_for_layout)
            and layout not in {"icon_explainer", "split_screen"}
        ):
            if previous_layout == "icon_explainer":
                # Mechanism continuity without violating rule 7:
                # prefer host+diagram (split_screen) when Alex is required by the brief;
                # otherwise keep a single justified consecutive icon only when streak is still 1,
                # and break longer runs with split_screen once Alex budget allows.
                trailing_icons = 0
                for prior in reversed(scenes):
                    if prior.layout_id == "icon_explainer":
                        trailing_icons += 1
                    else:
                        break
                if trailing_icons >= 1 and wants_alex and alex_used < max(alex_budget, 1) and index >= 1:
                    use_alex = True
                    alex_used += 1
                    layout = "split_screen"
                    focus = "character"
                elif trailing_icons == 0:
                    layout = "icon_explainer"
                    focus = "icon"
                elif trailing_icons == 1:
                    # Explicit continuity exception: second consecutive mechanism diagram only.
                    layout = "icon_explainer"
                    focus = "icon"
                    layout_repeat_justified = True
                else:
                    # Third+ consecutive would fail even the continuity exception — break with host diagram.
                    if wants_alex and alex_used < max(alex_budget, 1) and index >= 1:
                        use_alex = True
                        alex_used += 1
                        layout = "split_screen"
                        focus = "character"
                    else:
                        # No Alex budget: keep a mechanism diagram, never a hostless split_screen
                        # (hostless split stacks a random still under the overlay and muddies meaning).
                        layout = "icon_explainer"
                        focus = "icon"
                        layout_repeat_justified = True
            else:
                layout = "icon_explainer"
                focus = "icon"
        prior_layout = previous_layout
        previous_layout = layout

        number_value = _extract_number_label(beat.text) if focus in {"number", "chart", "comparison"} else None
        if layout == "big_number" and not number_value:
            number_value = _extract_number_label(beat.text)
        # NumberScene requires a source-backed number_value — never emit big_number without one.
        if layout == "big_number" and not number_value:
            layout = "fullscreen_statement" if prior_layout != "fullscreen_statement" else "quote_or_fact"
            focus = "statement"
            previous_layout = layout
            # Reconsider Alex after demotion — number focus had blocked host placement.
            if (
                wants_alex
                and not use_alex
                and alex_used == 0
                and index >= 1
                and alex_used < max(alex_budget, 1)
            ):
                use_alex = True
                alex_used += 1
                preferred = "character_left_text_right" if index % 2 else "character_right_text_left"
                focus = "character"
                layout = _next_layout(preferred, prior_layout, alex=True)
                previous_layout = layout
        on_screen = beat.on_screen or _short_on_screen(beat.text, prefer_number=focus == "number")
        if verified_growth_focus and not beat.on_screen:
            on_screen = "One deposit. Time to grow."
        narr_words = [word for word in beat.text.split() if word]
        screen_words = [word for word in on_screen.split() if word]
        preserve_literal_heading = bool(
            style_id in {"A", "B", "C", "D"} and order_raise_theme and not use_alex
            and focus not in {"number", "chart", "comparison"}
            and beat.on_screen and len(screen_words) <= 14
            and _raise_illustration_for(beat.text) is not None
        )
        if (not preserve_literal_heading and narr_words and len(narr_words) >= 8
                and len(screen_words) / len(narr_words) > 0.45):
            on_screen = _short_on_screen(beat.text, prefer_number=focus == "number")
        if number_value and focus == "number":
            on_screen = number_value

        simplify = focus in {"statement", "number"} and not use_alex
        chart = None
        if focus in {"chart", "comparison"}:
            chart = _build_chart_cue(
                focus=focus,
                narration=beat.text,
                title=on_screen,
                claims=beat_claims,
            )
            if not chart.is_quantitative:
                # Qualitative diagram / block quantitative ChartScene — demote layout.
                chart = _qualitative_chart(on_screen)
                if layout in {"chart_focus", "comparison"}:
                    # Avoid consecutive repeats against the prior committed scene (rule 7).
                    demoted = _next_layout("fullscreen_statement", prior_layout, alex=False)
                    if demoted in {"chart_focus", "comparison", "big_number"}:
                        demoted = "quote_or_fact" if prior_layout == "fullscreen_statement" else "fullscreen_statement"
                    layout = demoted
                    previous_layout = layout
                focus = "statement"
                simplify = not use_alex
            elif chart.verified_series is not None:
                # The extracted '$10' is a deposit, not a replacement for the
                # complete verified model. Keep the chart through layout routing.
                number_value = None
                layout = "chart_focus"
                previous_layout = layout

        asset_needs: list[AssetNeed] = []
        alex_pose = _alex_pose_for(beat.text) if use_alex else None
        if use_alex:
            asset_needs.append(
                AssetNeed(
                    type="character",
                    character="alex",
                    pose=alex_pose or "explaining",
                    tags=["finance", "host"],
                    text=f"Alex {alex_pose} toward on-screen idea: {on_screen}",
                )
            )
        if focus == "icon":
            # Prefer tags grounded in narration concept so AssetQuery hits illustrative
            # diagrams (savings account, weekly transfer) — never geometric placeholders.
            concept = infer_visual_concept(planning_text, on_screen)
            grounded = [tag for tag in ("savings", "budget", "investing", "transfer", "account") if tag in planning_text.casefold()]
            icon_need_tags = list(
                dict.fromkeys([*icon_tags_for_concept(concept), *(grounded or []), "finance", "money"])
            )
            asset_needs.append(
                AssetNeed(
                    type="icon",
                    tags=icon_need_tags,
                    text=on_screen,
                )
            )
        if chart is not None and chart.is_quantitative:
            asset_needs.append(
                AssetNeed(type="charts", tags=["investing", "growth"], required=False, text=on_screen)
            )

        animations = [
            AnimationCue(preset_id="TEXT_FADE", at_sec=beat.start + 0.05, duration_sec=0.45, target="headline"),
        ]
        if focus == "number":
            animations.append(
                AnimationCue(preset_id="NUMBER_COUNT", at_sec=beat.start + 0.15, duration_sec=0.7, target="number")
            )
        if chart is not None and chart.is_quantitative:
            animations.append(
                AnimationCue(preset_id="CHART_GROW", at_sec=beat.start + 0.2, duration_sec=0.85, target="chart")
            )
        if use_alex:
            animations.append(
                AnimationCue(preset_id="TEXT_SLIDE_LEFT", at_sec=beat.start + 0.1, duration_sec=0.5, target="alex")
            )

        sound_events: list[SoundCue] = []
        if index == 0:
            sound_events.append(SoundCue(event="soft_whoosh", at_sec=beat.start + 0.05, priority=2))
        if focus == "number":
            sound_events.append(SoundCue(event="pop", at_sec=beat.start + 0.25, priority=3))
        elif chart is not None and chart.is_quantitative:
            sound_events.append(SoundCue(event="rise", at_sec=beat.start + 0.3, priority=3))

        emphasis = [token for token in _words(on_screen) if re.search(r"[A-Za-z0-9]", token)][:4]

        # Drop qualitative chart cue so adapters cannot treat it as ChartScene data.
        chart_out = chart if (chart is not None and chart.is_quantitative) else None
        concept = infer_visual_concept(planning_text, on_screen)
        if order_raise_theme and concept in {"generic_finance", "habit"}:
            # Keep the raise-absorption diagram across the pilot instead of drifting to
            # unrelated weekly-transfer / paycheck-wealth chrome on statement beats.
            if "automatic" not in beat.text.casefold() and "weekly transfer" not in beat.text.casefold():
                concept = "raise_absorption"
        icon_tags = icon_tags_for_concept(concept) if focus in {"icon", "character", "statement"} else []
        intent = _visual_intent_for(
            concept=concept,
            focus=focus,
            use_alex=use_alex,
            on_screen=on_screen,
            beat_text=beat.text,
            visual_context=beat.visual_intent,
            reserve_context=reserve_context,
            planned_bill_context=planned_bill_context,
            chart=chart_out,
        )

        # Style comparison orders need their generated art to enter the real film.
        # Generic raise_absorption would both repeat the same diagram and hide the
        # still under Remotion's semantic overlay. Use the spoken physical state.
        source_intents = list(beat.source_visual_intents or ([beat.visual_intent] if beat.visual_intent else []))
        source_illustration = (
            _source_physical_illustration_for(source_intents)
            if style_id in {"A", "B", "C", "D"} and not use_alex and chart_out is None
            else None
        )
        legacy_illustration = (
            _raise_illustration_for(beat.text)
            if not source_intents and style_id in {"A", "B", "C", "D"} and order_raise_theme
            and not use_alex and chart_out is None and number_value is None
            else None
        )
        illustration = source_illustration or legacy_illustration
        if illustration:
            intent = (
                _literal_illustration_intent(illustration)
                if source_illustration
                else _raise_illustration_intent(illustration)
            )
            concept = illustration["concept"]
            icon_tags = ["finance", illustration["function"]]
            layout, focus, simplify = "icon_explainer", "icon", False
            number_value = None
            asset_needs = [AssetNeed(
                # The comparison pilots are complete illustrated frames. A still
                # has no moving internal parts, so no destructive alpha cutout is
                # needed. Layered object animation remains a separate capability.
                type="background",
                tags=["finance", illustration["function"]],
                text=(f"{illustration['subject']}. {illustration['composition']}")[:240],
                required=True,
            )]

        if (not illustration and
            index == 0
            and intent.dominant_intent == "TRANSFER"
            and "friday" not in beat.text.casefold()
            and "checking" not in beat.text.casefold()
        ):
            intent = intent.model_copy(
                update={
                    "dominant_intent": "ACTION",
                    "layout_family": "EDITORIAL_HERO",
                    "primary": "the claim",
                    "motion": "hold",
                }
            )
            layout = "fullscreen_statement"
            focus = "statement"
        story = None
        if raise_mechanism_stories is not None and raise_mechanism_stories[index] is not None:
            story = raise_mechanism_stories[index]
            intent = _raise_story_intent(story)
            layout, focus, use_alex, alex_pose = "icon_explainer", "icon", False, None
        elif (not illustration and not profile.get("disable_visual_story") and order_raise_theme and not wants_alex
                and ("pilot a" in order_hay or profile.get('visual_story') == 'raise-pilot-v1')):
            from atnos.production.channels.wealth_blueprint.visual_story import plan_raise_story
            previous = (scenes[-1].visual_story or {}).get("state") if scenes else None
            story = plan_raise_story(beat.text, previous=previous, is_hook=index == 0, is_last=index == len(beats)-1)
            layout, focus, use_alex, alex_pose = "icon_explainer", "icon", False, None
        elif intent.layout_family == "MECHANISM" and layout not in {"icon_explainer", "split_screen"}:
            layout = "icon_explainer"
            focus = "icon"
        elif intent.layout_family == "DATA_STORY" and number_value:
            layout = "big_number"
            focus = "number"
        elif not illustration and intent.layout_family == "EDITORIAL_HERO" and (index == 0 or index == len(beats) - 1):
            layout = "fullscreen_statement"
            focus = "statement"
        if index == len(beats) - 1 and not illustration:
            # Closing action beat: one claim, never a leftover mechanism diagram.
            intent = intent.model_copy(
                update={
                    "dominant_intent": "ACTION",
                    "layout_family": "EDITORIAL_HERO",
                    "primary": "the closing action",
                    "motion": "hold",
                    "scene_mode": "statement",
                    "why_this_image": "Closing action uses editorial statement chrome, not a repeated mechanism diagram",
                    "relation": {
                        "kind": "unsupported",
                        "viewer_should_understand": beat.text[:180],
                        "objects": [],
                        "initial_state": "",
                        "action": "hold",
                        "direction": None,
                        "condition": None,
                        "final_state": "",
                        "compliance_criterion": "Closing claim readable without inventing a transfer/split diagram",
                        "unsupported_gap": None,
                        "source": None,
                        "destination": None,
                    },
                    "initial_state": "",
                    "action": "hold",
                    "direction": "",
                    "condition": "",
                    "final_state": "",
                    "compliance_criterion": "Closing claim readable without inventing a transfer/split diagram",
                }
            )
            layout = "fullscreen_statement"
            focus = "statement"
            concept = "generic_finance"
            icon_tags = icon_tags_for_concept(concept)
            story = None
        if intent.relation and intent.relation.get('kind') == 'reserve_readiness':
            if profile.get('native_illustration_system') == 'wb_planning_desk_painted_v1':
                from atnos.production.channels.wealth_blueprint.planning_desk_contract import bind_planning_desk_intent
                intent = bind_planning_desk_intent(intent, beat.text)
            # Existing qualitative comparison layout maps to a native HookScene,
            # which renders the complete board without unrelated raster assets.
            layout, focus, use_alex, alex_pose = 'comparison', 'comparison', False, None
            asset_needs = []
            chart_out = None
        if index not in {0,len(beats)-1} and not (chart_out is not None and chart_out.is_quantitative):
            from atnos.production.channels.wealth_blueprint.research_check_contract import bind_research_check
            research = bind_research_check(intent,beat.text,on_screen,' '.join(source_intents),
                str(profile.get('direction') or '')+' '+str(profile.get('optional_notes') or ''))
            if research is not None:
                intent=research
                layout,focus,use_alex,alex_pose='comparison','comparison',False,None
                asset_needs=[]
                chart_out=None
                concept='generic_finance'
        # BUILD → HOLD → TRANSITION timing derived from beat duration (audio is time source).
        beat_len = max(0.6, float(beat.end) - float(beat.start))
        build_at = beat.start + min(0.12, beat_len * 0.08)
        hold_at = beat.start + min(beat_len * 0.42, max(0.55, beat_len * 0.35))
        hold_dur = max(0.45, min(1.8, beat_len * 0.38))
        if intent.layout_family == "MECHANISM" or intent.dominant_intent in {
            "TRANSFER",
            "SPLIT",
            "REPEAT",
            "NOTE_TRACK",
            "CHANGE_OVER_TIME",
            "LOOP",
        }:
            animations = [
                AnimationCue(preset_id="TEXT_FADE", at_sec=build_at, duration_sec=0.4, target="headline"),
                AnimationCue(preset_id="ICON_POP", at_sec=hold_at, duration_sec=hold_dur, target="primary"),
            ][:3]
        from atnos.production.channels.wealth_blueprint.scene_meaning import build_scene_meaning_contract

        meaning = build_scene_meaning_contract(
            scene_id=f"s{index + 1:02d}",
            narration=beat.text,
            relation=intent.relation if isinstance(intent.relation, dict) else None,
            concept=concept,
        )
        if story:
            meaning["visual_story_state"] = story["state"]
            meaning["motion_capability"] = "layered_raise_sequence"
            meaning["forbidden_interpretations"] = [
                "unspoken currency amounts, allocation proportions or investment returns",
                "a repeated split replacing a hold of an already selected allocation",
            ]
        if source_intents:
            meaning["source_visual_intents"] = source_intents
        if intent.relation and intent.relation.get('kind') == 'reserve_readiness':
            meaning["native_scene_role"] = "reserve_readiness"
        if chart_out is not None and chart_out.is_quantitative:
            meaning["native_scene_role"] = "chart"
        if (
            index == len(beats) - 1
            and re.search(r"\blogo\b", " ".join(source_intents), re.IGNORECASE)
            and (re.search(r"\b(?:follow|subscribe|visit|join|download)\b", beat.text, re.IGNORECASE)
                 or is_native_logo_closure_intent(source_intents))
        ):
            meaning["native_scene_role"] = "end_card"
        if illustration:
            meaning.update({
                "illustration_function": illustration["function"],
                "asset_subject": illustration["subject"],
                "asset_composition": illustration["composition"],
                "motion_capability": "reveal_then_hold_still",
                "on_screen_origin": "script" if preserve_literal_heading else "planner_summary",
                "source_on_screen_text": beat.on_screen if preserve_literal_heading else None,
                "world_id": "wb-source-intent-v1" if source_illustration else "wb-raise-physical-v1",
                "prop_ids": ({role: role for role in illustration["objects"]} if source_illustration else {
                    role: {"income": "pay-envelope", "money": "unmarked-money",
                           "spending": "spending-wallet", "expense": "blank-receipt",
                           "savings": "savings-jar", "goal": "planning-notebook"}[role]
                    for role in illustration["objects"]
                }),
                "forbidden_interpretations": [
                    "a static raster presented as an animated transfer or split",
                    "unspoken currency amounts, percentages or guaranteed returns",
                    "rendered labels, letters, amounts or arrows baked into the image",
                ],
            })
        scenes.append(
            PlannedScene(
                scene_id=f"s{index + 1:02d}",
                layout_id=layout,
                start_sec=beat.start,
                end_sec=beat.end,
                narration_segment_ids=beat.source_segment_ids or [beat.id],
                source_visual_intents=source_intents,
                narration_excerpt=beat.text,
                on_screen_text=on_screen,
                emphasis_words=emphasis,
                visual_focus=focus,  # type: ignore[arg-type]
                use_alex=use_alex,
                alex_pose=alex_pose,
                simplify=simplify,
                number_value=number_value,
                chart=chart_out,
                icon_tags=icon_tags,
                asset_needs=asset_needs,
                animations=animations[:3],
                sound_events=sound_events[:2],
                visual_intent=intent,
                visual_concept=concept,
                visual_story=story,
                meaning_contract=meaning,
                rationale=(
                    f"focus={focus}; layout={layout}; alex={use_alex}; concept={concept}; "
                    f"mode={intent.scene_mode}; chart_claims={list(chart_out.claim_ids) if chart_out else []}"
                    + ("; layout_repeat_justified=mechanism_continuity" if layout_repeat_justified else "")
                ),
            )
        )

    scenes = _merge_repeated_literal_holds(scenes)
    scenes = _merge_repeated_raise_story_holds(scenes)
    scenes = _merge_repeated_reserve_checks(scenes)
    duration = scenes[-1].end_sec if scenes else 0.0
    plan = WealthBlueprintScenePlan(
        planner_mode="deterministic",
        script=input_data.script,
        duration_sec=duration,
        scenes=scenes,
        music_bed="bed_editorial",
        notes=[
            "Deterministic Wealth Blueprint Scene Planner — no render executed",
            "Assets referenced semantically for Asset Brain / Sound Registry",
            "Quantitative charts require approved claim ids + narration-backed values",
            "Repeated layouts require a causal state change or a merged same-state hold; no decorative host fallback",
        ],
    )
    from atnos.production.channels.wealth_blueprint.shot_language import apply_shot_language

    return apply_shot_language(plan)
