from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UiBridgeRecipe:
    id: str
    name: str
    url: str
    login_required: bool
    auto_send: bool
    steps: list[str]
    safety_notes: list[str]


UI_BRIDGE_RECIPES = {
    "chatgpt_ui_bridge": UiBridgeRecipe(
        id="chatgpt_ui_bridge",
        name="ChatGPT UI Bridge",
        url="https://chatgpt.com/",
        login_required=True,
        auto_send=False,
        steps=["open browser profile", "paste prompt", "wait for manual review", "send only after approval"],
        safety_notes=["Fallback for subscription quality checks; not a core backend provider."],
    ),
    "claude_ui_bridge": UiBridgeRecipe(
        id="claude_ui_bridge",
        name="Claude UI Bridge",
        url="https://claude.ai/",
        login_required=True,
        auto_send=False,
        steps=["open browser profile", "paste prompt", "wait for manual review", "send only after approval"],
        safety_notes=["Fallback for subscription quality checks; not a core backend provider."],
    ),
    "gemini_ui_bridge": UiBridgeRecipe(
        id="gemini_ui_bridge",
        name="Gemini UI Bridge",
        url="https://gemini.google.com/",
        login_required=True,
        auto_send=False,
        steps=["open browser profile", "paste prompt", "wait for manual review", "send only after approval"],
        safety_notes=["Fallback for subscription quality checks; not a core backend provider."],
    ),
}


def get_ui_bridge_recipe(recipe_id: str) -> UiBridgeRecipe:
    return UI_BRIDGE_RECIPES[recipe_id]
