"""Literal art direction for distinct raise-pilot beats, independent of rendering style.

Object identity and action belong to the scene. A style supplies medium, linework,
palette and light; it must not invent a book, desk or machine in an object cutout.
"""
from __future__ import annotations

import re

WORLD_ID = "wb-raise-decision-world-v2"
WORLD_OBJECTS = {
    "income_envelope": "blank cream paper pay envelope with antique-gold coin discs",
    "spending_wallet": "open navy leather spending wallet",
    "recurring_cost": "blank cream recurring-payment card without marks",
    "savings_pot": "cream ceramic savings pot with a coin slot and navy base",
}


def raise_scene_art_direction(text: str, *, is_hook: bool = False, is_last: bool = False,
                             previous_function: str | None = None, style_id: str | None = None) -> dict | None:
    """Classify the sentence's causal function; unknown meanings keep their own planner.

    Tracking/compound/weekly-transfer beats are deliberately not converted into a
    raise split. No amount, quantitative proportion or investment return is invented.
    """
    hay = text.casefold()
    if re.search(r"\b(track|log|notebook|write down|compound|weekly transfer)\b", hay):
        return None
    choice = bool(re.search(r"\b(choose|decide|redirect|set aside|allocate|savings first|keep part|direct part|plan before)\b", hay))
    if is_hook and re.search(r"\b(raise|income|paycheck|pay rise)\b", hay):
        function = "income_arrival"
    elif is_last and previous_function in {"allocation_choice", "retained_goal"}:
        function = "retained_goal"
    elif choice:
        function = "allocation_choice"
    elif re.search(r"\b(new|recurring|subscription|upgrade|extra|higher)\b", hay) and re.search(r"\b(expenses?|costs?|spending|commitment|absorb|using)\b", hay):
        function = "recurring_cost"
    elif re.search(r"\b(goal|available|retained|remains?|saved)\b", hay) and re.search(r"\b(savings|money|aside|keep|retained|saved)\b", hay):
        function = "retained_goal"
    elif re.search(r"\b(raise|income|paycheck)\b", hay) and re.search(r"\b(arrive|arrives|increase|bigger|higher)\b", hay):
        function = "income_arrival"
    else:
        return None

    specs = {
        "income_arrival": {
            "subject": WORLD_OBJECTS["income_envelope"], "ids": ["income_envelope", "savings_pot"],
            "roles": {"source": "income envelope", "destination": "savings pot"},
            "initial": "Existing savings pot is unchanged; the extra income is not allocated yet",
            "action": "reveal", "final": "The pay envelope arrives while existing savings stay unchanged",
            "understand": "More income has arrived; savings have not automatically increased",
            "kind": "unsupported", "intent": "ACTION", "concept": "generic_finance",
            "composition": "One large complete open pay envelope, unmarked gold coin discs visible inside. Isolated silhouette, plain neutral backdrop, ample margin, no other props.",
            "criterion": "An income envelope appears; the savings object does not gain coins merely because income arrived",
        },
        "recurring_cost": {
            "subject": "open navy leather spending wallet holding a blank cream recurring-payment card",
            "ids": ["income_envelope", "spending_wallet", "recurring_cost", "savings_pot"],
            "roles": {"source": "income envelope", "destination": "spending wallet", "condition": "recurring payment card"},
            "initial": "Extra income is available in the same pay envelope",
            "action": "transfer", "final": "The new recurring payment takes money from income; savings stay unchanged",
            "understand": "A new recurring commitment can absorb the extra income",
            "kind": "transfer", "intent": "CAUSE_EFFECT", "concept": "money_movement",
            "composition": "One large complete open wallet, one blank cream card protruding, gold coin discs inside. Isolated silhouette, plain neutral backdrop, ample margin, no extra props.",
            "criterion": "Money moves from income toward the recurring expense, not into savings; the payment is conditional, not inevitable",
        },
        "allocation_choice": {
            "subject": "cream ceramic savings pot beside an open navy leather spending wallet",
            "ids": ["income_envelope", "spending_wallet", "recurring_cost", "savings_pot"],
            "roles": {"source": "income envelope", "branch_spend": "spending wallet", "branch_keep": "savings pot"},
            "initial": "Income could fund the new expense; an alternative destination is available",
            "action": "split", "final": "A deliberate allocation leaves money in the savings pot instead of spending all of it",
            "understand": "Deciding before a commitment can preserve an affordable part for savings",
            "kind": "raise_split", "intent": "COMPARISON", "concept": "comparison",
            "composition": "Large complete savings pot on the right and open wallet on the left, separated by a clear gap. Isolated pair, plain neutral backdrop, ample margin. No arrows or text.",
            "criterion": "Both alternatives are visible; saving follows a deliberate choice, with no invented split ratio",
        },
        "retained_goal": {
            "subject": WORLD_OBJECTS["savings_pot"], "ids": ["income_envelope", "savings_pot"],
            "roles": {"source": "retained savings", "destination": "future goal"},
            "initial": "The chosen affordable amount has already been set aside",
            "action": "hold", "final": "Retained money remains available for a future goal without any growth claim",
            "understand": "Money deliberately kept remains available; setting it aside is not an investment return",
            "kind": "unsupported", "intent": "CONSEQUENCE", "concept": "savings_account",
            "composition": "One large complete ceramic savings pot, a few unmarked gold coin discs beside its slot. Isolated silhouette, plain neutral backdrop, ample margin. No new coins appearing.",
            "criterion": "The saved object remains after the choice; no extra coins, growing graph or guarantee of a return appears",
        },
    }
    spec = dict(specs[function])
    spec.update(function=function, world_id=WORLD_ID, previous_function=previous_function,
                world_objects=dict(WORLD_OBJECTS), layer_role="background" if style_id == "D" else "icon")
    if style_id == "D":
        # This is a complete illustrated environment, not an alpha-cutout request.
        spec["subject"] = "quiet apartment dining table with " + spec["subject"]
        spec["composition"] = {
            "income_arrival": "Full apartment scene: open pay envelope in foreground, existing savings pot at right, navy wallet at left. Warm lamp light, dusk window. No people, text or markings.",
            "recurring_cost": "Same apartment table: open wallet and blank recurring-payment card in foreground, pay envelope left, savings pot right. Warm lamp light, dusk window. No people or text.",
            "allocation_choice": "Same apartment table: a cropped adult hand moves one gold disc toward the savings pot on the right; the wallet and blank payment card remain left. Warm lamp light. No face or text.",
            "retained_goal": "Same apartment table: savings pot stays right with retained gold discs, pay envelope left, navy wallet behind. Warm lamp light, dusk window. No people, writing or growth effects.",
        }[function]
    return spec
