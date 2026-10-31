from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from atnos.browser_operator.models import BrowserRecipe
from atnos.core.config import Settings


def _recipes_path(settings: Settings) -> Path:
    return (settings.app_root / "data" / "browser" / "site_recipes.yaml").resolve()


def _load_store(settings: Settings) -> dict[str, object]:
    path = _recipes_path(settings)
    if not path.exists():
        return {"recipes": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {"recipes": []}
    recipes = payload.get("recipes")
    if not isinstance(recipes, list):
        payload["recipes"] = []
    return payload


def _save_store(settings: Settings, payload: dict[str, object]) -> None:
    path = _recipes_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_recipes(settings: Settings) -> list[BrowserRecipe]:
    payload = _load_store(settings)
    recipes = payload.get("recipes")
    if not isinstance(recipes, list):
        return []
    return [BrowserRecipe.from_dict(item) for item in recipes if isinstance(item, dict)]


def save_recipe(settings: Settings, recipe: BrowserRecipe) -> BrowserRecipe:
    payload = _load_store(settings)
    raw_recipes = payload.get("recipes")
    recipes = [item for item in raw_recipes if isinstance(item, dict)] if isinstance(raw_recipes, list) else []
    replaced = False
    serialized = recipe.to_dict()
    for index, item in enumerate(recipes):
        if str(item.get("site") or "") == recipe.site and str(item.get("task") or "") == recipe.task:
            preserved_meta = item.get("stats") if isinstance(item.get("stats"), dict) else None
            if preserved_meta is not None:
                serialized["stats"] = preserved_meta
            recipes[index] = serialized
            replaced = True
            break
    if not replaced:
        recipes.append(serialized)
    payload["recipes"] = recipes
    _save_store(settings, payload)
    return recipe


def find_recipe(settings: Settings, site: str, task: str) -> BrowserRecipe | None:
    for recipe in load_recipes(settings):
        if recipe.site == site and recipe.task == task:
            return recipe
    return None


def update_recipe_after_success(settings: Settings, *, site: str, task: str) -> BrowserRecipe | None:
    payload = _load_store(settings)
    recipes = payload.get("recipes")
    if not isinstance(recipes, list):
        return None
    for item in recipes:
        if not isinstance(item, dict):
            continue
        if str(item.get("site") or "") != site or str(item.get("task") or "") != task:
            continue
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        stats["success_count"] = int(stats.get("success_count") or 0) + 1
        stats["last_status"] = "success"
        stats["last_updated_at"] = datetime.now(UTC).isoformat()
        item["stats"] = stats
        _save_store(settings, payload)
        return BrowserRecipe.from_dict(item)
    return None


def mark_recipe_failed(
    settings: Settings,
    *,
    site: str,
    task: str,
    reason: str,
    failed_step: str | None = None,
    screenshot_path: str | None = None,
) -> BrowserRecipe | None:
    payload = _load_store(settings)
    recipes = payload.get("recipes")
    if not isinstance(recipes, list):
        return None
    for item in recipes:
        if not isinstance(item, dict):
            continue
        if str(item.get("site") or "") != site or str(item.get("task") or "") != task:
            continue
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        stats["failure_count"] = int(stats.get("failure_count") or 0) + 1
        stats["last_status"] = "failed"
        stats["last_error"] = reason
        stats["last_failed_step"] = failed_step
        stats["last_failure_screenshot"] = screenshot_path
        stats["last_updated_at"] = datetime.now(UTC).isoformat()
        item["stats"] = stats
        _save_store(settings, payload)
        return BrowserRecipe.from_dict(item)
    return None
