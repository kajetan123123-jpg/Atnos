from __future__ import annotations

from integrations.browser_saas_recipes import BROWSER_SAAS_RECIPES, get_browser_saas_recipe
from integrations.provider_router import choose_provider


def test_short_saas_provider_routing_requires_approval() -> None:
    cases = [
        ("uzyj Klap do wyciecia shortow z dlugiego wideo", "klap_browser_recipe"),
        ("sprawdz AutoShorts / AutomaticShorts dla faceless serii", "autoshorts_browser_recipe"),
        ("porownaj AutoAIShorts dynamic captions i AI images", "autoaishorts_browser_recipe"),
        ("uzyj ElevenLabs do premium voiceover", "elevenlabs_browser_recipe"),
    ]

    for task, expected_provider in cases:
        decision = choose_provider(task)

        assert decision.provider_id == expected_provider
        assert decision.requires_approval is True
        assert "local_short_pipeline" in decision.fallback_provider_ids
        assert any("publish" in note.lower() for note in decision.safety_notes)


def test_short_saas_browser_recipes_are_browser_only_and_not_silent_publishing() -> None:
    expected_ids = {
        "klap_browser_recipe",
        "autoshorts_browser_recipe",
        "autoaishorts_browser_recipe",
        "elevenlabs_browser_recipe",
    }

    assert expected_ids.issubset(BROWSER_SAAS_RECIPES)

    for recipe_id in expected_ids:
        recipe = get_browser_saas_recipe(recipe_id)
        blocked = " ".join(recipe.not_for).lower()

        assert recipe.login_required is True
        assert recipe.browser_only is True
        assert any("approval" in step.lower() or "human" in step.lower() for step in recipe.steps)
        assert any(term in blocked for term in ("publish", "upload", "billing", "export"))
