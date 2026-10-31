"""Layout composition differentiation — Alex L/R, icon, comparison must not collapse."""

from __future__ import annotations

from atnos.production.channels.wealth_blueprint.design_system import require_layout
from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
from atnos.production.channels.wealth_blueprint.visual_profile import animation_default_for_layout
from atnos.production.project_manifest import SceneType


def test_alex_left_and_right_slots_differ():
    left = require_layout("character_left_text_right")
    right = require_layout("character_right_text_left")
    left_char = next(slot for slot in left.slots if slot.id == "character")
    right_char = next(slot for slot in right.slots if slot.id == "character")
    assert left_char.region_pct[0] < 20
    assert right_char.region_pct[0] > 50
    assert left_char.region_pct != right_char.region_pct


def test_icon_explainer_is_not_character_fullscreen():
    icon = require_layout("icon_explainer")
    assert icon.alex is False
    assert icon.focus == "icon"
    icon_slot = next(slot for slot in icon.slots if slot.id == "icon")
    # Upper-center icon region — not a full-bleed character plate
    assert icon_slot.region_pct[1] < 30
    assert icon_slot.region_pct[3] < 55


def test_comparison_maps_to_chart_scene_not_image():
    assert layout_to_scene_type("comparison", visual_focus="comparison", index=1, total=3, has_chart=True) == SceneType.CHART
    assert layout_to_scene_type("character_left_text_right", visual_focus="character", index=1, total=3) == SceneType.IMAGE
    assert layout_to_scene_type("icon_explainer", visual_focus="icon", index=1, total=3) == SceneType.IMAGE


def test_animation_presets_differ_by_layout():
    left = animation_default_for_layout("character_left_text_right")
    right = animation_default_for_layout("character_right_text_left")
    icon = animation_default_for_layout("icon_explainer")
    assert "CHARACTER_ENTER_LEFT" in left
    assert "CHARACTER_ENTER_RIGHT" in right
    assert "ICON_POP" in icon
    assert left != right
