from __future__ import annotations

from collections.abc import Callable

from atnos.services.browser_recipes.generic_form_fill import run_generic_form_fill
from atnos.services.browser_recipes.link_collect import run_link_collect
from atnos.services.browser_recipes.screenshot_capture import run_screenshot_capture
from atnos.services.browser_recipes.webpage_text_extract import run_webpage_text_extract


BROWSER_RECIPE_REGISTRY: dict[str, Callable] = {
    "webpage_text_extract": run_webpage_text_extract,
    "screenshot_capture": run_screenshot_capture,
    "generic_form_fill": run_generic_form_fill,
    "link_collect": run_link_collect,
}


def resolve_browser_recipe(recipe_name: str) -> Callable:
    normalized = str(recipe_name).strip().lower()
    try:
        return BROWSER_RECIPE_REGISTRY[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown browser recipe '{recipe_name}'.") from exc
