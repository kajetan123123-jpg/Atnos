"""Bind Wealth Blueprint narration meaning → visual concept + icon tags + overlay kind."""

from __future__ import annotations

from typing import Literal

VisualConcept = Literal[
    "raise_absorption",
    "savings_account",
    "weekly_transfer",
    "auto_transfer",
    "emergency_fund",
    "habit",
    "notebook_log",
    "money_movement",
    "comparison",
    "compound",
    "generic_finance",
]

_CONCEPT_HINTS: list[tuple[VisualConcept, tuple[str, ...]]] = [
    (
        "raise_absorption",
        (
            "pay raise",
            "pay rise",
            "a raise",
            "the raise",
            "your raise",
            "get a raise",
            "higher income",
            "income increase",
            "absorb the raise",
            "absorb the increase",
            "new recurring expenses",
            "new expenses",
            "expenses often rise",
            "lifestyle creep",
            "spending rises",
            "extra money to save",
            "no extra money",
            "part of the increase",
            "where will your next raise",
            "next raise",
            "keep the raise",
            "every raise",
            "keep part",
            "part of every",
            "direct part of",
            "toward savings first",
            "income enters a budget",
            "income enters",
            "spending plan",
            "savings don't grow",
            "savings do not grow",
        ),
    ),
    (
        "weekly_transfer",
        (
            "weekly transfer",
            "weekly transfers",
            "automatic weekly",
            "auto transfer",
            "automatic transfer",
            "schedule your",
            "scheduled transfer",
            "turn on automatic",
            "transfer arrow",
            "lands in savings",
        ),
    ),
    (
        "auto_transfer",
        ("automatic", "autopilot", "automate", "without willpower", "set it up automatically"),
    ),
    (
        "savings_account",
        (
            "savings account",
            "separate savings",
            "high-yield",
            "high yield",
            "open your account",
            "choose a separate",
            "separate account",
            "two pockets",
            "checking account",
        ),
    ),
    ("emergency_fund", ("emergency fund", "runway", "cash buffer", "rainy day")),
    (
        "notebook_log",
        (
            "notebook",
            "budgeting app",
            "budget app",
            "track your transfer",
            "track your transfers",
            "write down",
            "log the transfer",
            "keeps you accountable",
        ),
    ),
    # Habit must not steal raise-absorption or weekly-transfer beats.
    (
        "habit",
        (
            "habit loop",
            "make the habit",
            "savings habit",
            "budget habit",
            "build a habit",
            "this habit",
            "habit builds",
            "fixed amount each week",
            "financial cushion",
        ),
    ),
    ("money_movement", ("transfer", "move money", "send money", "pay yourself")),
    ("comparison", ("versus", " vs ", "compared", "instead of", "rather than", "early saver", "late saver")),
    # Keep compound narrow — "growth" / "over time" alone falsely matched raise briefs.
    ("compound", ("compound interest", "interest on interest", "compounding", "reinvest the interest")),
]

_CONCEPT_TAGS: dict[VisualConcept, list[str]] = {
    "raise_absorption": ["finance", "income", "expenses", "savings", "raise", "budget", "money"],
    "savings_account": ["savings", "account", "choose", "banking", "finance", "money"],
    "weekly_transfer": ["transfer", "weekly", "automatic", "savings", "banking", "finance", "money"],
    "auto_transfer": ["transfer", "automatic", "savings", "banking", "finance", "money"],
    "emergency_fund": ["emergency", "fund", "savings", "finance", "money"],
    "habit": ["habit", "budget", "savings", "finance", "money"],
    "notebook_log": ["notebook", "budget", "transfer", "finance", "money"],
    "money_movement": ["transfer", "banking", "money", "finance"],
    "comparison": ["finance", "investing"],
    "compound": ["investing", "growth", "finance"],
    "generic_finance": ["finance", "savings", "money"],
}

_CONCEPT_OVERLAY: dict[VisualConcept, str | None] = {
    "raise_absorption": "raise_absorption",
    "savings_account": "savings_account",
    "weekly_transfer": "weekly_transfer",
    "auto_transfer": "weekly_transfer",
    "emergency_fund": "purchase_pause",
    "habit": "habit_repeat",
    "notebook_log": "notebook_log",
    "money_movement": "weekly_transfer",
    "comparison": None,
    "compound": "compound_layers",
    "generic_finance": None,
}


def infer_visual_concept(*values: object) -> VisualConcept:
    context = " ".join(str(v or "") for v in values).casefold()
    for concept, hints in _CONCEPT_HINTS:
        if any(hint in context for hint in hints):
            return concept
    return "generic_finance"


def icon_tags_for_concept(concept: VisualConcept) -> list[str]:
    return list(_CONCEPT_TAGS.get(concept, _CONCEPT_TAGS["generic_finance"]))


def semantic_overlay_for_concept(concept: VisualConcept) -> str | None:
    return _CONCEPT_OVERLAY.get(concept)


def concept_prefers_icon_explainer(concept: VisualConcept) -> bool:
    """Concrete object/action beats must show a meaning diagram, not decorative geometry."""
    return concept in {
        "raise_absorption",
        "savings_account",
        "weekly_transfer",
        "auto_transfer",
        "emergency_fund",
        "habit",
        "notebook_log",
        "money_movement",
    }
