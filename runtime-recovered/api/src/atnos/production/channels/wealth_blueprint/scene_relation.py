"""Explicit source→target relations for Wealth Blueprint scenes.

A lone keyword such as checking, money, or raise must never invent a transfer story.
Unsupported meanings raise a concrete gap instead of a decorative fallback.
"""

from __future__ import annotations

import re
from typing import Any, Literal

RelationKind = Literal[
    "transfer",
    "transfer_forbidden",
    "raise_split",
    "notebook_log",
    "habit_repeat",
    "reserve_readiness",
    "unsupported",
]

_ACCOUNT = {
    "savings": ("savings", "saving account", "savings account", "savings jar", "keep pocket"),
    "checking": ("checking", "checking account", "current account", "spend pocket", "spending account"),
    "income": ("paycheck", "pay check", "income", "pay", "raise", "pay rise", "pay raise"),
    "expenses": ("expenses", "spending", "bills", "costs", "new expenses"),
    "notebook": ("notebook", "budgeting app", "budget app", "ledger", "tracker"),
}

_FROM_TO = re.compile(
    r"\bfrom\s+(?P<source>[a-z][a-z\s-]{2,40}?)\s+(?:to|into)\s+(?P<dest>[a-z][a-z\s-]{2,40}?)(?:\b|[.,;?!])",
    re.I,
)
_INTO = re.compile(
    r"\b(?:move|transfer|send|put|allocate|direct)\b.{0,40}?\b(?:into|to)\s+(?P<dest>savings|checking|expenses)\b",
    re.I,
)
_NEGATION = re.compile(
    r"\b(?:do not|don't|never|forbid|forbidden|must not|cannot|can't)\b.{0,48}?\b(?:transfer|move money|send money)\b"
    r"|\b(?:transfer|move money)\b.{0,24}?\b(?:is not allowed|forbidden|blocked)\b",
    re.I,
)


def _normalize_account(raw: str) -> str | None:
    hay = re.sub(r"\s+", " ", raw.strip().casefold())
    hay = re.sub(r"[^a-z\s-]", "", hay).strip()
    for canonical, aliases in _ACCOUNT.items():
        for alias in aliases:
            if hay == alias or hay.startswith(alias + " ") or hay.endswith(" " + alias):
                return canonical
            if alias in hay and len(alias) >= 5:
                return canonical
    return None


def _object(role: str, label: str) -> dict[str, str]:
    return {"role": role, "label": label, "id": label.casefold().replace(" ", "_")}


def parse_scene_relation(text: str, *, concept: str = "", visual_context: str = "", reserve_context: bool = False, planned_bill_context: bool = False) -> dict[str, Any]:
    """Return a relation contract. Unsupported beats declare the missing capability."""
    raw = str(text or "").strip()
    hay = raw.casefold()
    concept = str(concept or "").strip()

    if _NEGATION.search(raw):
        return {
            "kind": "transfer_forbidden",
            "viewer_should_understand": "This transfer must not happen.",
            "objects": [_object("source", "source account"), _object("destination", "blocked destination")],
            "initial_state": "A transfer path is proposed",
            "action": "block",
            "direction": None,
            "condition": "transfer forbidden",
            "final_state": "Money stays put; no arrow completes",
            "compliance_criterion": "Frame shows a blocked path; money does not arrive at a destination",
            "unsupported_gap": None,
            "source": None,
            "destination": None,
        }

    if concept == "raise_absorption" or _raise_split(hay):
        return {
            "kind": "raise_split",
            "viewer_should_understand": "The raise divides into spending and saving; keep a slice before costs expand.",
            "objects": [
                _object("source", "the raise"),
                _object("branch_spend", "spend"),
                _object("branch_keep", "keep / savings"),
            ],
            "initial_state": "Raise arrives as one amount",
            "action": "split",
            "direction": "raise→spend+keep",
            "condition": None,
            "final_state": "Two labeled branches remain: spend the rest and keep a slice",
            "compliance_criterion": "Viewer sees a fork, not a single linear box chain",
            "unsupported_gap": None,
            "source": "income",
            "destination": "expenses+savings",
        }

    # Explicit source/destination transfers retain their established contract.
    reserve = None if (_FROM_TO.search(raw) or _INTO.search(raw)
                       or re.search(r'[\d$€£%]|\b(?:percent|months?|years?)\b', raw, re.I)) else _reserve_relation(hay, visual_context=visual_context, reserve_context=reserve_context, planned_bill_context=planned_bill_context)
    if reserve is not None:
        return reserve

    from_to = _FROM_TO.search(raw)
    if from_to:
        source = _normalize_account(from_to.group("source"))
        dest = _normalize_account(from_to.group("dest"))
        if source and dest and source != dest:
            return _transfer_contract(source, dest, condition=_condition(hay))
        # "Separate X from Y to prevent ..." is a purpose clause, not an
        # attempted transfer between ambiguous accounts. Preserve a genuinely
        # supported explicit reserve comparison; real transfer verbs still
        # fail closed when their accounts cannot be identified.
        if not re.search(r'\b(?:transfer|move|send|deposit|redirect|allocate)\w*\b',
                         raw[:from_to.start()], re.I):
            reserve_relation=_reserve_relation(hay,visual_context=visual_context,reserve_context=reserve_context,planned_bill_context=planned_bill_context)
            if reserve_relation and reserve_relation['stage'] in {'separate','review'}:
                return reserve_relation
        return _unsupported(
            raw,
            gap=f"Transfer phrase found, but accounts are ambiguous (source={from_to.group('source')!r}, dest={from_to.group('dest')!r}).",
        )

    into = _INTO.search(raw)
    if into and ("from " not in hay):
        dest = _normalize_account(into.group("dest"))
        source = "checking" if "checking" in hay else ("income" if any(t in hay for t in ("paycheck", "pay", "friday", "week")) else None)
        if dest and source and source != dest:
            return _transfer_contract(source, dest, condition=_condition(hay))
        if dest == "savings" and concept in {"weekly_transfer", "auto_transfer", "money_movement"}:
            # Explicit automation concept may imply paycheck→savings when source is unnamed.
            return _transfer_contract("income", "savings", condition=_condition(hay) or "scheduled move")
        if dest:
            return _unsupported(
                raw,
                gap=f"Destination is {dest}, but the source account is not stated. Refusing to invent checking→savings.",
            )

    directed=re.sub(r'^\s*native[_ ]reserve[_ ]readiness\s*:\s*','',visual_context.casefold())
    if (re.match(r'\s*(?:identify|access|separate|compare|review)\s*\(',directed)
            and not re.search(r'[\d$€£%]|\b(?:percent|months?|years?)\b', raw, re.I)):
        explicit_reserve=_reserve_relation(hay,visual_context=visual_context,reserve_context=reserve_context,planned_bill_context=planned_bill_context)
        if explicit_reserve:
            # A named notebook in the comparison is a goal object, not an
            # instruction to log a transfer. Actual transfer expressions were
            # handled above; a lone "from" is not one.
            return explicit_reserve

    if any(token in hay for token in ("notebook", "budgeting app", "budget app", "write down", "log the", "track your transfer", "track your transfers")):
        return {
            "kind": "notebook_log",
            "viewer_should_understand": "Each transfer is written down so progress stays visible.",
            "objects": [
                _object("ledger", "notebook"),
                _object("entry", "transfer line"),
                _object("source", "transfer"),
            ],
            "initial_state": "Notebook is open with an empty line",
            "action": "write",
            "direction": None,
            "condition": None,
            "final_state": "A dated transfer entry is visible in the notebook",
            "compliance_criterion": "Notebook appears in frame and an entry is completed before hold",
            "unsupported_gap": None,
            "source": None,
            "destination": "notebook",
        }

    if concept == "habit" or _habit_repeat(hay):
        return {
            "kind": "habit_repeat",
            "viewer_should_understand": "The same small move repeats and the kept amount grows.",
            "objects": [
                _object("cue", "weekly cue"),
                _object("action", "set-aside"),
                _object("result", "growing cushion"),
            ],
            "initial_state": "Week one set-aside is alone",
            "action": "repeat",
            "direction": None,
            "condition": "each week / payday",
            "final_state": "Later weeks show more kept amounts stacked",
            "compliance_criterion": "At least two completed repetitions and a visible accumulation",
            "unsupported_gap": None,
            "source": None,
            "destination": "savings",
        }

    if concept in {"weekly_transfer", "auto_transfer", "money_movement"} and "savings" in hay:
        # Concept already selected by planner; still require a readable direction.
        source = "checking" if "checking" in hay else "income"
        return _transfer_contract(source, "savings", condition=_condition(hay) or "automatic")

    if any(token in hay for token in ("transfer", "move money", "from ", " into ", "checking", "savings")) and concept in {
        "",
        "generic_finance",
        "savings_account",
    }:
        return _unsupported(
            raw,
            gap="Money-movement language without a clear source→destination relation. Add 'from X to Y' or an explicit supported concept.",
        )

    return {
        "kind": "unsupported",
        "viewer_should_understand": raw[:180] or "Scene meaning unclear",
        "objects": [],
        "initial_state": "",
        "action": "",
        "direction": None,
        "condition": None,
        "final_state": "",
        "compliance_criterion": "",
        "unsupported_gap": None,
        "source": None,
        "destination": None,
    }


def _condition(hay: str) -> str | None:
    if "rent" in hay:
        return "cover rent"
    if "friday" in hay:
        return "every Friday"
    if "every week" in hay or "each week" in hay:
        return "every week"
    if "automatic" in hay or "auto " in hay:
        return "automatic"
    return None


def _raise_split(hay: str) -> bool:
    mentions_raise = any(t in hay for t in ("raise", "pay rise", "pay raise", "higher income", "income increase"))
    mentions_split = any(
        t in hay
        for t in (
            "allocate",
            "portion",
            "part of",
            "keep part",
            "spend the rest",
            "split",
            "toward savings",
            "to savings",
            "cover current",
        )
    )
    return mentions_raise and mentions_split


def _habit_repeat(hay: str) -> bool:
    return any(
        t in hay
        for t in (
            "each week",
            "every week",
            "every friday",
            "this habit",
            "habit builds",
            "over time",
            "consistency",
            "repeats",
            "cushion",
        )
    ) and any(t in hay for t in ("amount", "habit", "friday", "week", "transfer", "set aside", "fixed"))


def _transfer_contract(source: str, dest: str, *, condition: str | None) -> dict[str, Any]:
    direction = f"{source}→{dest}"
    return {
        "kind": "transfer",
        "viewer_should_understand": f"Money moves from {source} to {dest}.",
        "objects": [_object("source", source), _object("destination", dest), _object("flow", "money")],
        "initial_state": f"Funds sit in {source}",
        "action": "move",
        "direction": direction,
        "condition": condition,
        "final_state": f"Funds arrive in {dest}",
        "compliance_criterion": f"Arrow runs {source} → {dest}; labels match spoken accounts",
        "unsupported_gap": None,
        "source": source,
        "destination": dest,
        "secondary": f"{source} to {dest}",
    }


def _unsupported(text: str, *, gap: str) -> dict[str, Any]:
    return {
        "kind": "unsupported",
        "viewer_should_understand": text[:180],
        "objects": [],
        "initial_state": "",
        "action": "",
        "direction": None,
        "condition": None,
        "final_state": "",
        "compliance_criterion": "",
        "unsupported_gap": gap,
        "source": None,
        "destination": None,
    }


def overlay_kind_for_relation(relation: dict[str, Any]) -> str | None:
    return {
        "transfer": "weekly_transfer",
        "transfer_forbidden": "weekly_transfer",
        "raise_split": "raise_absorption",
        "notebook_log": "notebook_log",
        "habit_repeat": "habit_repeat",
        "reserve_readiness": "reserve_readiness",
    }.get(str(relation.get("kind") or ""))


def _reserve_relation(hay: str, *, visual_context: str = '', reserve_context: bool = False, planned_bill_context: bool = False) -> dict[str, Any] | None:
    """Qualitative reserve actions, never an invented transfer, yield or amount."""
    # The director's positive instruction binds aliases such as "these funds"
    # and "these roles". Negative clauses must not select the forbidden action.
    directed = re.split(r'\b(?:without|no|not)\b', visual_context.casefold(), maxsplit=1)[0]
    # The writer's explicit capability namespace is not part of the action.
    # Strip only this bounded known prefix, then validate the actual stage and
    # roles below. A marker alone never qualifies an unsupported relation.
    directed = re.sub(r'^\s*native[_ ]reserve[_ ]readiness\s*:\s*', '', directed)
    context_reserved = reserve_context or 'ready reserve' in directed
    directed_stage = None
    if context_reserved:
        if re.match(r'\s*access\s*\(', directed) and 'reserve' in directed and 'conditions' in directed:
            directed_stage='access'
        elif re.match(r'\s*identify\s*\(', directed) and 'reserve' in directed and any(
                value in directed for value in ('repair invoice', 'actual need', 'essential need', 'emergency reserve')):
            directed_stage='identify'
        elif re.match(r'\s*review\s*\(', directed) and 'review both roles' in directed:
            directed_stage='review'
        elif ('review' in hay and 'circumstances' in hay
                and 'magnifying glass' in directed and 'envelope' in directed and 'notebook' in directed):
            directed_stage='review'
        elif re.match(r'\s*review\b', directed) and 'separation' in directed:
            directed_stage='review'
        elif re.match(r'\s*(?:compare|separate)\b', directed) and 'future goal' in directed and 'reserve' in directed:
            directed_stage='separate'
        elif re.match(r'\s*(?:identify|name)\b', directed) and 'car repair' in directed:
            directed_stage='identify'
        elif re.match(r'\s*access\s*\(', directed) and 'reserve' in directed and 'access' in directed:
            directed_stage='access'
        elif 'ready reserve' in directed and any(word in directed for word in ('access','question','conditions')):
            directed_stage='access'
    car_repair = ('car repair' in hay or bool(re.search(r'\bcar\b.{0,64}\b(?:breaks down|breakdown|repair)\b', hay))
                  or context_reserved and 'car repair' in directed)
    expense = car_repair or any(t in hay for t in ('essential expense', 'unexpected expense', 'unexpected medical',
                                    'home repair', 'unplanned expense'))
    reserve = any(t in hay for t in ('reserve', 'emergency fund', 'emergency savings'))
    if directed_stage:
        stage=directed_stage
        action={'identify':'identify_need','access':'check_access','separate':'compare_roles','review':'review_separation'}[stage]
    elif ('review' in hay and 'separation' in hay and any(t in hay for t in ('funds', 'reserve', 'goal'))):
        stage, action = 'review', 'review_separation'
    elif reserve and any(t in hay for t in ('access', 'locate', 'held')):
        stage, action = 'access', 'check_access'
    elif reserve and any(t in hay for t in ('readiness', 'future goal', 'longer-term', 'long-term', 'rather than')):
        stage, action = 'separate', 'compare_roles'
    elif expense:
        stage = 'identify' if any(t in hay for t in ('identify', 'name one', 'name it')) else 'need'
        action = 'identify_need'
    else:
        return None
    label = ('CAR REPAIR' if car_repair else 'MEDICAL BILL' if 'medical' in hay
             else 'HOME REPAIR' if 'home repair' in hay else 'ANNUAL BILL' if planned_bill_context else 'UNEXPECTED NEED')
    objects = [_object('expense', label), _object('reserve', 'READY RESERVE')]
    if stage in {'separate', 'review'}:
        objects.append(_object('goal', 'PLANNED BILL FUND' if planned_bill_context else 'FUTURE GOAL'))
    # The brief can identify the subject as a planned bill, but only the spoken
    # action can distinguish a due-date hook from the practical listing beat.
    bill_focus = None
    if planned_bill_context and stage == 'identify' and label == 'ANNUAL BILL':
        named_bill = re.search(r'\b(?:bills?|costs?|expenses?|obligations?|payments?)\b', hay)
        if named_bill and re.search(r'\b(?:list|write(?:\s+down)?|record|log|note(?:\s+down)?)\b', hay):
            bill_focus = 'list'
        elif named_bill and re.search(r'\bcalendar\b|\bdue\s+date\b', hay):
            bill_focus = 'calendar'
    outcomes = {
        'need': 'An unexpected necessary expense is identified; reserve availability is a question',
        'identify': 'A concrete essential expense is named without an invented price',
        'access': 'Reserve location, access time and applicable conditions are questions to verify',
        'separate': 'Readiness and a future goal are shown as distinct roles, not a quantified allocation',
        'review': 'Changed circumstances prompt a review of both distinct roles',
    }
    if planned_bill_context:
        if label == 'ANNUAL BILL':
            outcomes['identify']='A known annual bill is identified as a planned obligation without an invented price or due date'
        outcomes['separate']='Money assigned to known bills and money reserved for unexpected needs have distinct roles'
        outcomes['review']='Review the known bill commitment alongside the emergency reserve without double-counting the same money'
    initial_state = 'Reserve and need have distinct roles'
    compliance_criterion = 'Show the named qualitative roles and the current check; no amounts, runway months, returns or completed transfer'
    if bill_focus == 'calendar':
        action = 'show_known_due_date'
        initial_state = 'A calendar contains a known annual bill deadline'
        outcomes['identify'] = 'The known annual bill due date is highlighted on the calendar as a planned obligation, not an emergency'
        compliance_criterion = 'Show a calendar cue for a known bill due date without inventing a specific date, amount or emergency'
    elif bill_focus == 'list':
        action = 'record_known_bills'
        initial_state = 'A planning list is ready for known annual bills'
        outcomes['identify'] = 'Known annual bills are recorded on a planning list before they arrive, with no emergency transfer implied'
        compliance_criterion = 'Show annual bill entries being listed without inventing bill names, dates, amounts or a transfer'
    access_focus=None
    if stage=='access':
        access_focus=('terms' if re.search(r'\b(?:penalt\w*|fees?|waiting periods?|notice periods?)\b',hay)
                      else 'overview')
        if access_focus=='terms':
            outcomes['access']='A close-up asks whether waiting time or charges apply under this account\'s own terms; no restriction is asserted'
    return {'kind':'reserve_readiness','stage':stage,'viewer_should_understand':outcomes[stage],
            'objects':objects,'initial_state':initial_state,
            'action':action,'direction':None,'condition':None,'final_state':outcomes[stage],
            'compliance_criterion':compliance_criterion,
            'unsupported_gap':None,'source':None,'destination':None,
            **({'access_focus':access_focus} if access_focus else {}),
            **({'bill_focus':bill_focus} if bill_focus else {})}
