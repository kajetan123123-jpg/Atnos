"""Visual quality gate regressions: meaning contracts, picker intent, storyboard unsupported."""

from __future__ import annotations

from atnos.production.channels.wealth_blueprint.scene_meaning import (
    build_scene_meaning_contract,
    required_interaction_for_contract,
)
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
from atnos.production.channels.wealth_blueprint.scene_production_assets import (
    _asset_intent_score,
    _needs_for_scene,
)
from atnos.production.channels.wealth_blueprint.visual_decision import (
    composition_flags,
    decide_visual,
    storyboard_blocks_render,
)
from atnos.production.asset_brain.models import AssetRecord


def test_transfer_meaning_contract_keeps_direction():
    relation = parse_scene_relation("move money from savings back to checking")
    contract = build_scene_meaning_contract(
        scene_id="s02",
        narration="move money from savings back to checking",
        relation=relation,
    )
    assert contract["direction"] == "savings→checking"
    assert contract["object_roles"]["source"] == "savings"
    assert contract["object_roles"]["destination"] == "checking"
    assert any("reversed" in item.casefold() or "→" in item for item in contract["forbidden_interpretations"])
    assert required_interaction_for_contract(contract) == "source_to_destination_motion"


def test_decide_visual_assigns_split_repeat_note_track():
    raise_habit = decide_visual("When you receive a raise, allocate a portion to savings", concept="raise_absorption")
    assert raise_habit["dominant_intent"] == "SPLIT"
    assert raise_habit["layout_family"] == "MECHANISM"
    notebook = decide_visual("Track your transfers with a budgeting app or notebook.", concept="notebook_log")
    assert notebook["dominant_intent"] == "NOTE_TRACK"
    habit = decide_visual("Start with a fixed amount each week. Over time, this habit builds a cushion.", concept="habit")
    assert habit["dominant_intent"] == "REPEAT"


def test_storyboard_blocks_navy_card_mechanism_fallback():
    blocked = storyboard_blocks_render(
        {
            "scenes": [
                {
                    "scene_id": "s01",
                    "layout_id": "fullscreen_statement",
                    "visual_intent": {"dominant_intent": "SPLIT", "layout_family": "MECHANISM", "relation": {}},
                },
                {
                    "scene_id": "s02",
                    "layout_id": "icon_explainer",
                    "visual_intent": {"dominant_intent": "TRANSFER", "layout_family": "MECHANISM", "relation": {}},
                },
                {
                    "scene_id": "s03",
                    "layout_id": "icon_explainer",
                    "visual_intent": {"dominant_intent": "NOTE_TRACK", "layout_family": "MECHANISM", "relation": {}},
                },
            ]
        }
    )
    assert blocked is not None
    assert "VISUAL_INTENT_UNSUPPORTED" in blocked


def test_template_overuse_flag_for_consecutive_cards():
    flags = composition_flags(
        {
            "scenes": [
                {"scene_id": "s01", "layout_id": "fullscreen_statement", "visual_focus": "statement", "visual_intent": {}},
                {"scene_id": "s02", "layout_id": "quote_or_fact", "visual_focus": "statement", "visual_intent": {}},
                {"scene_id": "s03", "layout_id": "fullscreen_statement", "visual_focus": "statement", "visual_intent": {}},
            ]
        }
    )
    assert any(flag["code"] == "VISUAL_TEMPLATE_OVERUSE" for flag in flags)


def test_picker_rejects_raise_still_for_notebook_need():
    need = {
        "visual_concept": "notebook_log",
        "visual_intent": "NOTE_TRACK",
        "required_interaction": "notebook_entry_write",
        "object_roles": {"ledger": "notebook"},
        "tags": ["notebook", "budget", "finance"],
    }
    raise_asset = AssetRecord(
        id="illust_raise_absorption_flow_v01",
        type="icon",
        file_path="illustrative/illust_raise_absorption_flow_v01.png",
        channel="wealth_blueprint",
        tags=["finance", "raise", "income", "savings", "money"],
        approved=True,
        quality_status="APPROVED",
        production_role="icon",
        transparent_background=True,
    )
    notebook_asset = AssetRecord(
        id="illust_notebook_log_v01",
        type="icon",
        file_path="illustrative/illust_notebook_log_v01.png",
        channel="wealth_blueprint",
        tags=["finance", "notebook", "ledger", "budget", "money"],
        approved=True,
        quality_status="APPROVED",
        production_role="icon",
        transparent_background=True,
    )
    assert _asset_intent_score(raise_asset, need) < 0
    assert _asset_intent_score(notebook_asset, need) > 0.2


def test_needs_for_scene_carry_intent_and_interaction():
    needs = _needs_for_scene(
        {
            "scene_id": "s05",
            "visual_focus": "icon",
            "visual_concept": "notebook_log",
            "visual_intent": {"dominant_intent": "NOTE_TRACK", "layout_family": "MECHANISM"},
            "meaning_contract": {
                "relation_kind": "notebook_log",
                "object_roles": {"ledger": "notebook"},
            },
            "on_screen_text": "Track transfers",
            "narration_excerpt": "Track your transfers with a notebook.",
        }
    )
    assert needs[0]["visual_concept"] == "notebook_log"
    assert needs[0]["visual_intent"] == "NOTE_TRACK"
    assert needs[0]["required_interaction"] == "notebook_entry_write"
