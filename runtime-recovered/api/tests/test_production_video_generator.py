from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.project_manifest import SceneType
from atnos.production.channel_profile import DARK_CHRONICLES_PROFILE, FINANCE_PROFILE, SIGMA_SQUAD_PROFILE, WEALTH_BLUEPRINT_PROFILE
from atnos.production.qa_gate import _measure_actual_clip_visual_variety
from atnos.production.reference_profile import ReferenceShortsProfile
from atnos.production.reference_profile_builder import analyze_video_visual_profile
from atnos.production.scene_checkpoint import scene_generation_input_hash
from atnos.production.steps import thumbnail_generator, video_generator
from test_support import create_test_app


def test_wealth_image_scene_requires_one_existing_qa_passed_selected_image(tmp_path: Path) -> None:
    selected = tmp_path / "selected.png"
    selected.write_bytes(b"image")
    valid_clip = {
        "segment": 3,
        "scene_id": 2,
        "scene_type": "ImageScene",
        "backend": "comfy_image_motion",
        "selected_image_path": str(selected),
        "image_candidates": [
            {
                "path": str(selected),
                "passed": True,
                "selected": True,
                "qa": {"heuristic": {}, "semantic": {}, "text": {}},
            }
        ],
    }

    assert video_generator._wealth_image_scene_evidence_errors(WEALTH_BLUEPRINT_PROFILE, [valid_clip]) == []


def test_wealth_image_scene_rejects_selected_candidate_that_failed_quality_audits(tmp_path: Path) -> None:
    selected = tmp_path / "selected.png"
    selected.write_bytes(b"image")
    invalid_clip = {
        "segment": 3,
        "scene_id": 2,
        "scene_type": "ImageScene",
        "backend": "comfy_image_motion",
        "selected_image_path": str(selected),
        "image_candidates": [{"path": str(selected), "passed": False, "selected": True}],
    }

    errors = video_generator._wealth_image_scene_evidence_errors(WEALTH_BLUEPRINT_PROFILE, [invalid_clip])

    assert len(errors) == 1
    assert errors[0]["qa_passed_selected_candidates"] == 0


def test_wealth_image_scene_rejects_motion_graphic_without_selected_image() -> None:
    invalid_clip = {
        "segment": 3,
        "scene_id": 2,
        "scene_type": "ImageScene",
        "backend": "motion_graphic",
        "selected_image_path": None,
        "image_candidates": [],
    }

    errors = video_generator._wealth_image_scene_evidence_errors(WEALTH_BLUEPRINT_PROFILE, [invalid_clip])

    assert errors == [
        {
            "segment": 3,
            "scene_id": 2,
            "backend": "motion_graphic",
            "selected_image_path": None,
            "qa_passed_selected_candidates": 0,
        }
    ]


def test_motion_graphic_backend_does_not_require_generated_image_evidence(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "production"
    app.state.settings.shorts_video_backend = "motion_graphic"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    assert video_generator._preferred_video_backend(app.state.settings) == "motion_graphic"
    assert video_generator._requires_real_visuals(app.state.settings) is True


def test_candidate_generation_attempt_reuses_interrupted_input_revision() -> None:
    assert video_generator._candidate_generation_attempt(
        previous_status="RUNNING",
        previous_input_hash="same",
        current_input_hash="same",
        current_attempt=4,
    ) == 3
    assert video_generator._candidate_generation_attempt(
        previous_status="RUNNING",
        previous_input_hash="old",
        current_input_hash="new",
        current_attempt=4,
    ) == 4


def test_windows_ocr_forces_utf8_and_tolerates_missing_stdout(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"image")
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return type("Completed", (), {"returncode": 0, "stdout": None})()

    monkeypatch.setattr(video_generator, "_windows_ocr_runtime_available", lambda: True)
    monkeypatch.setattr(video_generator.subprocess, "run", fake_run)

    assert video_generator._run_windows_ocr(image_path) == ""
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_verified_comfy_still_is_reused_only_while_artifact_is_integral(tmp_path: Path) -> None:
    output_path = tmp_path / "candidate.png"
    image_bytes = b"verified-image" * 400
    output_path.write_bytes(image_bytes)
    prompt = "A verified semantic background."
    evidence = {
        "status": "verified",
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "seed": 42,
        "width": 512,
        "height": 896,
        "bytes": len(image_bytes),
        "artifact_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "models": ["sd_xl_base_1.0_0.9vae.safetensors"],
    }
    output_path.with_suffix(".png.comfyui.json").write_text(json.dumps(evidence), encoding="utf-8")

    assert video_generator._is_reusable_comfy_still(output_path, prompt=prompt, seed=42)
    assert video_generator._is_reusable_comfy_still(
        output_path, prompt=prompt, seed=42, preferred_checkpoint="checkpoints/sd_xl_base_1.0_0.9vae.safetensors"
    )
    assert not video_generator._is_reusable_comfy_still(
        output_path, prompt=prompt, seed=42, preferred_checkpoint="Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
    )

    output_path.write_bytes(image_bytes + b"tampered")
    assert not video_generator._is_reusable_comfy_still(output_path, prompt=prompt, seed=42)


def test_finance_generation_preflight_blocks_text_prone_surfaces() -> None:
    scenes = [
        {
            "scene_id": "unsafe-1",
            "visual_prompt": "Faceless investor checks a budget app on a phone screen beside a chart.",
        }
    ]

    with pytest.raises(RuntimeError, match="blocked before GPU startup"):
        video_generator._assert_scene_generation_safe(
            scenes,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )


def test_comfy_finance_prompt_removes_card_and_envelope_surfaces() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "visual_subject": (
                "Faceless adult hand placing a blank unbranded bank card beside three color-coded envelopes "
                "and a cash stack."
            ),
            "visual_prompt": (
                "Faceless adult hand placing a blank unbranded bank card beside three color-coded envelopes "
                "and a cash stack. Mature adult fintech editorial scene with unrelated contract instructions."
            ),
        },
        segment=1,
        total=6,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    lowered = prompt.casefold()
    assert "bank card" not in lowered
    assert "envelope" not in lowered
    assert "closed leather wallet" in lowered
    assert "paper currency" not in lowered
    assert "plain ceramic bowls" in lowered
    assert "loose unprinted metal coins" in lowered
    assert "adult hand" in lowered
    assert "hand action fully visible" in lowered
    assert "no people, no hands" not in lowered
    assert "phone screen" not in lowered
    assert "no collage" in lowered


def test_comfy_prompt_keeps_wealth_blueprint_illustrated_and_generic_finance_distinct() -> None:
    scene = {
        "visual_subject": "faceless adult hand moving coins from a pay envelope into a savings jar",
        "visual_prompt": "faceless adult hand moving coins from a pay envelope into a savings jar",
    }
    wealth_prompt = video_generator._comfy_scene_image_prompt(
        {**scene, "channel_profile_key": "wealth_blueprint"},
        segment=1,
        total=1,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()
    finance_prompt = video_generator._comfy_scene_image_prompt(
        {**scene, "channel_profile_key": "finance"},
        segment=1,
        total=1,
        channel_profile=FINANCE_PROFILE,
    ).casefold()

    assert "hand-drawn editorial" in wealth_prompt
    assert "charcoal navy" in wealth_prompt
    assert "cream paper" in wealth_prompt
    assert "muted gold" in wealth_prompt
    assert "photorealistic" not in wealth_prompt
    assert "photorealistic" in finance_prompt


def test_wealth_blueprint_comfy_retry_does_not_switch_to_photography(tmp_path: Path) -> None:
    attempted_prompts: list[str] = []

    class FailingService:
        def generate_background(self, *, prompt: str, **_kwargs) -> None:
            attempted_prompts.append(prompt)
            raise video_generator.ComfyUiImageGenerationError("simulated backend failure")

    errors = asyncio.run(
        video_generator._generate_comfy_scene_still(
            FailingService(),
            tmp_path / "candidate.png",
            prompt="A hand-drawn finance scene with a goal jar",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
            max_prompt_attempts=2,
        )
    )
    assert len(errors) == 2
    assert len(attempted_prompts) == 2
    assert "hand-drawn editorial illustration" in attempted_prompts[1]
    assert "photographic" not in attempted_prompts[1]


def test_wealth_blueprint_raise_beats_keep_money_direction_in_comfy_prompts() -> None:
    scenes = [
        {"headline": "RAISE DOES NOT EQUAL WEALTH", "visual_subject": "hand placing a paycheck into a wallet"},
        {"headline": "NEW EXPENSE TAKES MONEY", "visual_subject": "coins move from pay envelope into a recurring-bill envelope while savings jar stays untouched"},
        {"headline": "SAVE BEFORE NEW BILL", "visual_subject": "hand moving coins toward a savings goal, remaining coins in wallet"},
        {"headline": "WHAT REMAINS IS YOURS", "visual_subject": "remaining coins in a wallet beside a goal jar and shopping basket"},
    ]
    prompts = [
        video_generator._comfy_scene_image_prompt(
            {**scene, "channel_profile_key": "wealth_blueprint"},
            segment=index,
            total=len(scenes),
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        ).casefold()
        for index, scene in enumerate(scenes, start=1)
    ]
    assert "coins into an open leather wallet" in prompts[0]
    assert "payment tray" in prompts[1]
    assert "savings jar" not in prompts[1]
    assert "into one clear goal jar" in prompts[2]
    assert "shopping basket" in prompts[3]
    assert all("hand-drawn editorial" in prompt for prompt in prompts)
    assert all("photograph" not in prompt for prompt in prompts)
    expense_contract = video_generator._finance_semantic_contract_for_scene(
        {**scenes[1], "channel_profile_key": "wealth_blueprint"}
    ).casefold()
    assert "payment tray" in expense_contract
    assert "savings jar stays untouched" not in expense_contract
    expense_context = video_generator._image_candidate_semantic_context(
        "Where a Pay Raise Goes",
        {**scenes[1], "channel_profile_key": "wealth_blueprint"},
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()
    assert "payment tray" in expense_context
    assert "savings jar stays untouched" not in expense_context


def test_finance_scene_prompts_enforce_distinct_primary_objects() -> None:
    subjects = [
        "a mechanical coin sorter splitting cash and coins",
        "an open wallet beside a large analog clock",
        "a takeaway cup, parcel, and wireless headphones forming a domino chain",
        "cash and coins leaking toward a dark floor drain",
        "placing coins into a savings jar, then buying a book",
        "cash blocked by tiny purchase objects across a clean path",
        "an hourglass filled with coins above a nearly empty wallet",
    ]

    prompts = [
        video_generator._comfy_scene_image_prompt(
            {"channel_profile_key": "wealth_blueprint", "visual_subject": subject},
            segment=index,
            total=len(subjects),
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        ).casefold()
        for index, subject in enumerate(subjects, start=1)
    ]

    assert "mechanical coin sorter" in prompts[0]
    assert "analog alarm clock" in prompts[1]
    assert "one plain takeaway coffee cup" in prompts[2]
    assert "coins forming a clear trail from the cup" in prompts[2]
    assert "earbud" not in prompts[2]
    assert "floor drain" in prompts[3]
    assert "one transparent savings jar" in prompts[4]
    assert "red velvet barrier" in prompts[5]
    assert "hourglass dominating the frame" in prompts[6]
    assert sum("one transparent savings jar" in prompt for prompt in prompts) == 1
    assert all("scene uniqueness rule" in prompt for prompt in prompts)


def test_finance_prompt_prefers_concrete_reviewed_shot_over_generic_subject() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "visual_subject": "a mechanical coin sorter splitting coins into trays",
            "visual_prompt": (
                "A premium sneaker, wireless headphones, and takeaway cup on a checkout conveyor. "
                "Mature adult fintech editorial scene, generated contract follows."
            ),
        },
        segment=5,
        total=11,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "checkout conveyor" in prompt
    assert "premium sneaker" in prompt
    assert "mechanical coin sorter" not in prompt
    assert "wallet, glass jar" in prompt


def test_finance_prompt_keeps_scene_with_negative_text_guardrails() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "headline": "THE LATTE TRAP",
            "visual_prompt": (
                "An open leather wallet beside takeaway coffee cups and loose coins, "
                "no labels, no readable text, no logos."
            ),
        },
        segment=1,
        total=1,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "one plain takeaway coffee cup" in prompt
    assert "the latte trap" not in prompt


def test_finance_prompt_still_rejects_positive_text_surface() -> None:
    assert video_generator._finance_local_prompt_is_unsafe("A receipt showing readable text beside a wallet") is True


def test_finance_prompt_accepts_explicit_faceless_hand_action() -> None:
    assert video_generator._finance_local_prompt_is_unsafe(
        "faceless adult hand moving two plain coins from a brass dish onto a larger coin stack"
    ) is False


def test_finance_prompt_maps_literal_purchase_and_leak_scenes() -> None:
    source_scenes = [
        "A nearly empty leather wallet beside a trail of small everyday purchases, one coin rolling away.",
        "A delivery bag, subscription token, and upgrade box draining separate coins from a household budget.",
        "A cracked transparent pipe leaking coins into a delivery bag beside wireless headphones.",
    ]
    prompts = [
        video_generator._comfy_scene_image_prompt(
            {"channel_profile_key": "wealth_blueprint", "visual_prompt": source},
            segment=index,
            total=3,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        ).casefold()
        for index, source in enumerate(source_scenes, start=1)
    ]

    assert "three small unbranded purchases" in prompts[0]
    assert "separate trail of loose coins" in prompts[1]
    assert "cracked clear acrylic tube" in prompts[2]
    assert all("scene: one transparent savings jar" not in prompt for prompt in prompts)


def test_finance_paycheck_scene_uses_arrival_evidence_not_clock_metaphor() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "visual_prompt": "An unopened wallet beside a large analog clock when a paycheck arrives, no text.",
        },
        segment=3,
        total=11,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "wallet visibly filled" in prompt
    assert "represent pay arriving" in prompt
    assert "alarm clock" not in prompt


def test_finance_payday_overlay_uses_open_coin_pouch_contract() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "semantic_overlay_kind": "payday_arrival",
        "narration": "Your paycheck lands and the spending clock starts.",
        "visual_prompt": "A paycheck arrives.",
    }

    prompt = video_generator._comfy_scene_image_prompt(scene, segment=3, total=11).casefold()

    assert "open brown leather coin pouch" in prompt
    assert "no coins touch the outside leather" in prompt
    assert video_generator._scene_checkpoint_contract(scene)["_image_prompt_recipe"] == "finance-anchor-scene-v8"


def test_finance_boring_pause_scene_does_not_repeat_checkout_conveyor() -> None:
    prompt = video_generator._finance_scene_subject(
        "an unbranded premium sneaker and takeaway cup on a checkout conveyor",
        headline="THE BORING PAUSE",
    )

    assert "brass desk barrier" in prompt
    assert "deliberate pause" in prompt
    assert "conveyor" not in prompt.casefold()


def test_finance_unplanned_week_scene_uses_sorting_tray_anchor() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "headline": "BY FRIDAY, THE",
            "narration": "By Friday, the money did not vanish. It was assigned without a plan.",
            "visual_prompt": "Products on a checkout conveyor.",
        },
        segment=7,
        total=11,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "one stainless-steel sorting tray" in prompt
    assert "several loose silver-metal coins" in prompt
    assert "one coherent illustrated scene" in prompt.casefold()
    assert "no people" in prompt
    assert video_generator._scene_semantic_overlay_kind(
        {
            "headline": "BY FRIDAY, THE",
            "narration": "By Friday, the money did not vanish. It was assigned without a plan.",
        }
        ) == "allocation_sorter"


def test_finance_autopilot_scene_uses_a_reliable_growth_anchor() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "headline": "MOVE ONE DECISION",
            "narration": "Move one decision out of autopilot before you think about investing.",
            "visual_prompt": "Coins spilling from a pouch.",
        },
        segment=9,
        total=11,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "one small healthy green plant" in prompt
    assert "one neat short stack of metal coins" in prompt
    assert "long-term growth still life" in prompt
    assert "shopping basket" not in prompt
    assert "no people" in prompt
    assert video_generator._scene_semantic_overlay_kind(
        {
            "headline": "MOVE ONE DECISION",
            "narration": "Move one decision out of autopilot before you think about investing.",
        }
        ) == "auto_invest"


def test_multi_object_candidate_variants_keep_every_required_object_visible() -> None:
    base = "Vertical overhead photo. Show all four objects: wallet, bag, cup, sneaker."

    prompts = [video_generator._image_candidate_prompt(base, index).casefold() for index in (1, 2, 3)]

    assert all("every required object remains fully visible and separate" in prompt for prompt in prompts)
    assert all("one dominant physical subject" not in prompt for prompt in prompts)


def test_wealth_illustration_candidates_never_request_a_photograph() -> None:
    base = "Vertical 9:16 hand-drawn editorial illustration of a coin leaving a wallet."

    prompts = [video_generator._image_candidate_prompt(base, index).casefold() for index in (1, 2, 3)]

    assert all("photograph" not in prompt for prompt in prompts)
    assert all("illustrated composition" in prompt for prompt in prompts)


def test_wealth_candidate_retry_seed_is_new_but_reproducible() -> None:
    first = video_generator._wealth_image_candidate_seed(
        "one wallet and one coin",
        segment=5,
        candidate_index=2,
        generation_attempt=1,
    )
    repeated = video_generator._wealth_image_candidate_seed(
        "one wallet and one coin",
        segment=5,
        candidate_index=2,
        generation_attempt=1,
    )
    retry = video_generator._wealth_image_candidate_seed(
        "one wallet and one coin",
        segment=5,
        candidate_index=2,
        generation_attempt=2,
    )

    assert first == repeated
    assert retry != first


def test_scene_narration_is_resolved_from_visual_plan_script_lines() -> None:
    scenes = [{"scene_id": 7, "script_lines": [1], "headline": "SO CREATE FRICTION"}]
    script = {
        "segments": [
            {"text": "Unused line."},
            {"text": "Create friction before the first purchase, not regret after the fifth."},
        ]
    }

    enriched = video_generator._attach_scene_narration(scenes, script)

    assert enriched[0]["narration"] == (
        "Create friction before the first purchase, not regret after the fifth."
    )
    assert video_generator._manifest_scene_type(enriched[0]) is SceneType.NUMBER


def test_image_checkpoint_contract_includes_prompt_relevant_narration() -> None:
    scene = {
        "segment_type": "body",
        "headline": "THE REAL GAP",
        "visual_prompt": "An open wallet beside a coin trail.",
        "script_lines": [1],
    }
    with_narration = {**scene, "narration": "The real gap starts after you earn money."}

    contract = video_generator._scene_checkpoint_contract(with_narration)

    assert contract["narration"] == "The real gap starts after you earn money."
    assert contract != video_generator._scene_checkpoint_contract(scene)


def test_attached_narration_freezes_semantic_overlay_before_checkpoint_hashing() -> None:
    scenes = [{"scene_id": 1, "script_lines": [0], "headline": "MOVE ONE DECISION"}]
    script = {"segments": [{"text": "Move one decision out of autopilot before investing."}]}

    enriched = video_generator._attach_scene_narration(scenes, script)
    contract = video_generator._scene_checkpoint_contract(enriched[0])

    assert enriched[0]["semantic_overlay_kind"] == "auto_invest"
    assert contract["_semantic_overlay_kind"] == "auto_invest"


def test_deterministic_checkpoint_contract_records_resolved_scene_type() -> None:
    scene = {
        "segment_type": "body",
        "headline": "ONE DELIVERY. ONE",
        "visual_prompt": "Three products.",
        "narration": "One delivery. One subscription. One tiny upgrade.",
    }

    contract = video_generator._scene_checkpoint_contract(scene)

    assert contract["_resolved_scene_type"] == SceneType.NUMBER.value
    assert "narration" not in contract


def test_finance_decision_image_checkpoint_records_prompt_recipe() -> None:
    scene = {
        "segment_type": "body",
        "headline": "MOVE ONE DECISION",
        "visual_prompt": "Coins spilling from a pouch.",
        "narration": "Move one decision out of autopilot before investing.",
        "semantic_overlay_kind": "auto_invest",
    }

    contract = video_generator._scene_checkpoint_contract(scene)

    assert contract["_image_prompt_recipe"] == "finance-anchor-scene-v7"
    assert contract["_semantic_overlay_kind"] == "auto_invest"
    assert contract["narration"] == "Move one decision out of autopilot before investing."


def test_finance_anchor_scene_recipe_invalidates_v2_checkpoint_hash() -> None:
    scene = {
        "segment_type": "body",
        "headline": "MOVE ONE DECISION",
        "visual_prompt": "Coins spilling from a pouch.",
        "narration": "Move one decision out of autopilot before investing.",
        "semantic_overlay_kind": "auto_invest",
    }
    current_contract = video_generator._scene_checkpoint_contract(scene)
    old_contract = {**current_contract, "_image_prompt_recipe": "finance-complete-scene-v2"}
    kwargs = {
        "segment": 9,
        "total": 11,
        "backend": {"mode": "comfy_image_motion", "checkpoint": "model.safetensors"},
        "workflow_name": "default.json",
        "channel_profile": "wealth_blueprint",
    }

    assert scene_generation_input_hash(old_contract, **kwargs) != scene_generation_input_hash(
        current_contract,
        **kwargs,
    )


def test_finance_income_split_uses_one_coherent_coin_anchor_without_wallet_duplication() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "headline": "THE REAL GAP",
        "narration": "The real gap starts with how your money behaves after you earn it.",
        "visual_prompt": "Delivery, subscriptions, and upgrades drain the budget.",
        "semantic_overlay_kind": "income_split",
    }

    prompt = video_generator._comfy_scene_image_prompt(scene, segment=2, total=11).casefold()

    assert "matching cool silver-metal coins" in prompt
    assert "same plain silver finish" in prompt
    assert "gold or yellow" not in prompt
    assert "one short coin stack" in prompt
    assert "wallet" not in prompt
    assert "no split composition" in prompt
    assert "gold coin" in video_generator._comfy_negative_visual_prompt("wealth_blueprint", prompt=prompt)
    assert "reject any distinctly gold or yellow coin" in video_generator._finance_semantic_image_contract(
        "income_split"
    )
    assert video_generator._scene_checkpoint_contract(scene)["_image_prompt_recipe"] == "finance-anchor-scene-v10"


def test_compound_interest_on_interest_contract_requires_one_readable_action() -> None:
    semantic = video_generator._finance_semantic_image_contract("compound_interest_on_interest").casefold()
    generation = video_generator._finance_generation_image_contract("compound_interest_on_interest").casefold()

    for contract in (semantic, generation):
        assert "adult hand" in contract
        assert "two earned plain metal coins" in contract
        assert "small brass dish" in contract
        assert "larger plain metal coin stack" in contract
        assert "gear" not in contract
        assert "transparent green blocks" not in contract


def test_compound_interest_on_interest_scene_meets_finance_relevance_floor() -> None:
    scene = {
        "visual_prompt": (
            "one small brass gear driving one larger brass gear beside a rising plain metal coin stack on a matte "
            "black machine plate as a physical compound growth feedback mechanism"
        ),
        "semantic_overlay_kind": "compound_interest_on_interest",
    }

    assert video_generator._scene_visual_relevance_score(
        scene,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ) >= 0.58


def test_compound_interest_not_guaranteed_contract_is_literal_and_financial() -> None:
    semantic = video_generator._finance_semantic_image_contract("compound_mechanism_not_guarantee").casefold()
    generation = video_generator._finance_generation_image_contract("compound_mechanism_not_guarantee").casefold()

    for contract in (semantic, generation):
        assert "balance scale" in contract
        assert "stack" in contract
        assert "coins" in contract
        assert "padlock" in contract
        assert "deep green felt" in contract
        assert "dark walnut desk" not in contract


def test_compound_interest_scenario_contract_shows_contribution_over_time() -> None:
    semantic = video_generator._finance_semantic_image_contract("compound_scenario").casefold()
    generation = video_generator._finance_generation_image_contract("compound_scenario").casefold()

    assert "multiple plain metal coins" in semantic
    assert "hourglass" in semantic
    assert "pale stone shelf" in semantic
    assert "adult hand" not in semantic
    assert "row" not in semantic

    assert "coin" in generation
    assert "row" in generation
    assert "hourglass" in generation
    assert "pale stone shelf" in generation
    assert "adult hand" not in generation


def test_source_brief_coin_ban_overrides_legacy_compound_image_contract() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "semantic_overlay_kind": "compound_scenario",
        "headline": "CALCULATE THE SCENARIO",
        "visual_subject": (
            "mature financial-planning desk with an analog calculator, a glass hourglass, three unlabeled acrylic "
            "scenario blocks, and a closed leather planning folder"
        ),
        "visual_prompt": (
            "mature financial-planning desk with an analog calculator, a glass hourglass, three unlabeled acrylic "
            "scenario blocks, and a closed leather planning folder. No readable text."
        ),
        "forbidden_visual_terms": [
            "coin",
            "coins",
            "piggy bank",
            "piggy banks",
            "fantasy wealth imagery",
        ],
    }

    prompt = video_generator._comfy_scene_image_prompt(
        scene,
        segment=8,
        total=10,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()
    negative = video_generator._comfy_negative_visual_prompt(
        "wealth_blueprint",
        prompt=prompt,
        forbidden_visual_terms=video_generator._scene_forbidden_visual_terms(scene),
    ).casefold()
    contract = video_generator._finance_semantic_contract_for_scene(scene).casefold()
    context = video_generator._image_candidate_semantic_context(
        "Why Compound Interest Rewards Time More Than Hype",
        scene,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "coin" not in prompt
    assert "analog calculator" in prompt
    assert "acrylic scenario blocks" in prompt
    assert "coin" in negative
    assert "forbidden visible props: coin" in contract
    assert "forbidden visible props: coin" in context


def test_finance_semantic_contract_always_rejects_generated_currency_and_text() -> None:
    contract = video_generator._finance_semantic_contract_for_scene(
        {
            "visual_subject": "an analog calculator beside an hourglass",
            "forbidden_visual_terms": ["coin"],
        }
    ).casefold()

    assert "banknotes" in contract
    assert "paper currency" in contract
    assert "readable text" in contract
    assert "readable digits" in contract
    assert "logos" in contract
    assert "forbidden visible props: coin" in contract


def test_finance_prompt_rejects_unsafe_planner_prompt_and_uses_subject() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "visual_prompt": "Faceless adult hands hold a wallet and blank bank card.",
            "visual_subject": "a mechanical coin sorter splitting coins into three trays",
        },
        segment=1,
        total=7,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()

    assert "mechanical coin sorter" in prompt
    assert "adult hands" not in prompt
    assert "bank card" not in prompt


def test_comfy_image_motion_cache_requires_matching_prompt_evidence(tmp_path: Path, monkeypatch) -> None:
    clip_path = tmp_path / "s01.mp4"
    image_path = clip_path.with_suffix(".s01.comfy.png")
    cutaway_path = clip_path.with_suffix(".s01.comfy.cutaway.png")
    prompt = "primary prompt"
    cutaway_prompt = "cutaway prompt"
    clip_path.write_bytes(b"probeable-video")
    image_path.write_bytes(b"p" * 5000)
    cutaway_path.write_bytes(b"c" * 5000)
    for path, value in ((image_path, prompt), (cutaway_path, cutaway_prompt)):
        path.with_suffix(f"{path.suffix}.comfyui.json").write_text(
            json.dumps(
                {
                    "status": "verified",
                    "prompt_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    "artifact": str(path),
                    "bytes": path.stat().st_size,
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(video_generator, "_is_probeable_video", lambda path: Path(path).exists())

    assert video_generator._can_reuse_comfy_image_motion_clip(
        clip_path,
        image_path=image_path,
        cutaway_path=cutaway_path,
        prompt=prompt,
        cutaway_prompt=cutaway_prompt,
    )


def test_comfy_image_motion_cache_rejects_stale_prompt(tmp_path: Path, monkeypatch) -> None:
    clip_path = tmp_path / "s01.mp4"
    image_path = clip_path.with_suffix(".s01.comfy.png")
    cutaway_path = clip_path.with_suffix(".s01.comfy.cutaway.png")
    clip_path.write_bytes(b"probeable-video")
    image_path.write_bytes(b"p" * 5000)
    cutaway_path.write_bytes(b"c" * 5000)
    for path in (image_path, cutaway_path):
        path.with_suffix(f"{path.suffix}.comfyui.json").write_text(
            json.dumps(
                {
                    "status": "verified",
                    "prompt_sha256": hashlib.sha256(b"old prompt").hexdigest(),
                    "artifact": str(path),
                    "bytes": path.stat().st_size,
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(video_generator, "_is_probeable_video", lambda path: Path(path).exists())

    assert not video_generator._can_reuse_comfy_image_motion_clip(
        clip_path,
        image_path=image_path,
        cutaway_path=cutaway_path,
        prompt="new primary",
        cutaway_prompt="new cutaway",
    )


def test_comfy_generation_evidence_rejects_invalid_or_unverified_payload(tmp_path: Path) -> None:
    image_path = tmp_path / "candidate.png"
    image_path.write_bytes(b"image")
    evidence_path = image_path.with_suffix(".png.comfyui.json")
    evidence_path.write_text("not-json", encoding="utf-8")
    assert video_generator._load_comfy_generation_evidence(image_path) == {}

    evidence_path.write_text(json.dumps({"status": "failed", "seed": 7}), encoding="utf-8")
    assert video_generator._load_comfy_generation_evidence(image_path) == {}


def test_comfy_generation_evidence_refreshes_normalized_artifact_hash(tmp_path: Path) -> None:
    image_path = tmp_path / "candidate.png"
    image_path.write_bytes(b"normalized-image")
    evidence_path = image_path.with_suffix(".png.comfyui.json")
    evidence_path.write_text(
        json.dumps({"status": "verified", "artifact_sha256": "stale", "bytes": 1}),
        encoding="utf-8",
    )

    video_generator._refresh_comfy_generation_evidence(image_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence["artifact"] == str(image_path.resolve())
    assert evidence["artifact_sha256"] == hashlib.sha256(image_path.read_bytes()).hexdigest()
    assert evidence["bytes"] == image_path.stat().st_size


def test_comfy_image_motion_keeps_checkpoint_resident_between_scenes(tmp_path: Path, monkeypatch) -> None:
    class FakeService:
        release_calls = 0

        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            self.release_calls += 1
            return True

    async def generate_still(service, output_path, *, prompt, channel_profile):
        output_path.write_bytes(b"image" * 1200)
        return []

    app = create_test_app(tmp_path)
    clip_path = tmp_path / "s01.mp4"
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_write_image_motion_clip", lambda *args, **kwargs: True)

    generated = asyncio.run(
        video_generator._try_generate_comfy_image_motion_clip(
            app.state.settings,
            {"visual_prompt": "A wallet beside a savings jar."},
            clip_path,
            1,
            2,
        )
    )

    assert generated is True
    assert FakeService.release_calls == 0


def _stub_idle_gpu_for_candidate_test(monkeypatch) -> None:
    # Candidate selection is unit-tested with a fake Comfy service; live Ollama
    # residency belongs to the hardware integration test, not these cases.
    monkeypatch.setattr(
        video_generator.HardwareGuardService,
        "release_gpu_pressure",
        lambda _self: {"status": "released", "verified_released": True},
    )


def test_wealth_image_candidates_generate_exactly_three_and_select_best(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    requested_checkpoints: list[str | None] = []
    class FakeService:
        released = False

        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            self.released = True
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        requested_checkpoints.append(preferred_checkpoint)
        output_path.write_bytes(b"image" * 1200)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        index = int(record["candidate_index"])
        return {
            **record,
            "passed": True,
            "score": {1: 0.72, 2: 0.91, 3: 0.84}[index],
            "reasons": [],
            "selected": False,
            "qa": {"text": {"status": "pass"}, "heuristic": {"status": "pass"}, "semantic": {"status": "pass"}},
        }

    app = create_test_app(tmp_path)
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)
    monkeypatch.setattr(
        video_generator,
        "compare_visual_candidates",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "ranked_candidate_indexes": [2, 3, 1],
            "rejected_candidate_indexes": [],
        },
    )

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [
                {"segment_type": "body", "headline": "SAVE MORE", "visual_prompt": "A wallet beside three bowls."},
                {"segment_type": "cta", "headline": "START NOW", "visual_prompt": "A clean final card."},
            ],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
    )

    assert list(result) == [1]
    assert result[1]["candidate_count"] == 3
    assert result[1]["status"] == "selected"
    assert result[1]["selected_candidate_id"] == "s01-candidate-02"
    assert result[1]["memory_released_before_vision"] is True
    assert sum(bool(candidate["selected"]) for candidate in result[1]["candidates"]) == 1
    assert requested_checkpoints == ["checkpoints/sd_xl_base_1.0_0.9vae.safetensors"] * 3


def test_wealth_image_candidates_use_comparative_integrity_ranking(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        Image.new("RGB", (96, 128), color=(30 + deterministic_seed % 180, 60, 80)).save(output_path)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        index = int(record["candidate_index"])
        return {
            **record,
            "passed": True,
            "score": {1: 0.75, 2: 0.96, 3: 0.82}[index],
            "reasons": [],
            "selected": False,
            "qa": {},
        }

    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)
    monkeypatch.setattr(
        video_generator,
        "audit_visual_contract_compliance",
        lambda *_args, **_kwargs: {"status": "pass", "contract_satisfied": True, "violations": []},
    )
    monkeypatch.setattr(
        video_generator,
        "compare_visual_candidates",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "ranked_candidate_indexes": [1, 3, 2],
            "rejected_candidate_indexes": [2],
            "defects_by_candidate": {"2": ["impossible coin insert"]},
        },
    )

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [{"segment_type": "body", "visual_prompt": "A wallet beside coins."}],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
    )

    assert result[1]["selected_candidate_id"] == "s01-candidate-01"
    rejected = next(candidate for candidate in result[1]["candidates"] if candidate["candidate_index"] == 2)
    assert rejected["passed"] is False
    assert "comparative_visual_integrity_rejected" in rejected["reasons"]


def test_wealth_image_candidates_apply_literal_contract_veto_before_comparative_ranking(
    tmp_path: Path, monkeypatch
) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        candidate_index = int(output_path.stem[-1])
        Image.new("RGB", (96, 128), color=(40 * candidate_index, 60, 80)).save(output_path)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        return {**record, "passed": True, "score": 0.9, "reasons": [], "selected": False, "qa": {}}

    def contract_audit(_settings, image_path, *, required_visual_contract):
        candidate_index = int(Path(image_path).stem[-1])
        assert "silver coins" in required_visual_contract
        return {
            "status": "fail" if candidate_index == 2 else "pass",
            "contract_satisfied": candidate_index != 2,
            "violations": ["distinct gold coin"] if candidate_index == 2 else [],
        }

    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)
    monkeypatch.setattr(video_generator, "audit_visual_contract_compliance", contract_audit)
    monkeypatch.setattr(
        video_generator,
        "compare_visual_candidates",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "ranked_candidate_indexes": [1, 2],
            "rejected_candidate_indexes": [],
        },
    )

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [
                {
                    "segment_type": "body",
                    "semantic_overlay_kind": "income_split",
                    "visual_prompt": "Silver coins on a desk.",
                }
            ],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
    )

    assert result[1]["selected_candidate_id"] == "s01-candidate-01"
    rejected = next(candidate for candidate in result[1]["candidates"] if candidate["candidate_index"] == 2)
    assert rejected["passed"] is False
    assert rejected["literal_contract_audit"]["violations"] == ["distinct gold coin"]
    assert "literal_visual_contract_failed" in rejected["reasons"]


def test_image_visual_difference_detects_duplicate_and_distinct_frames(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    duplicate = tmp_path / "duplicate.png"
    distinct = tmp_path / "distinct.png"
    Image.new("RGB", (96, 96), "#123C32").save(first)
    Image.new("RGB", (96, 96), "#123C32").save(duplicate)
    Image.new("RGB", (96, 96), "#D8B36A").save(distinct)

    assert video_generator._image_visual_difference(first, duplicate) == 0.0
    assert (video_generator._image_visual_difference(first, distinct) or 0.0) > 0.04
    assert video_generator._image_visual_difference(first, tmp_path / "missing.png") is None


def test_wealth_image_candidates_compare_against_previous_selected_scenes(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    previous = tmp_path / "previous.png"
    Image.new("RGB", (96, 128), "#102030").save(previous)

    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        candidate_index = int(output_path.stem[-1])
        Image.new("RGB", (96, 128), color=(150 + candidate_index * 20, 90, 40)).save(output_path)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        return {**record, "passed": True, "score": 0.9, "reasons": [], "selected": False, "qa": {}}

    app = create_test_app(tmp_path)
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)
    monkeypatch.setattr(
        video_generator,
        "compare_visual_candidates",
        lambda *_args, **_kwargs: {
            "status": "pass",
            "ranked_candidate_indexes": [1, 2, 3],
            "rejected_candidate_indexes": [],
        },
    )

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [{"segment_type": "body", "visual_prompt": "A wallet beside coins."}],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
            existing_selected_image_paths=[previous],
        )
    )

    selected = next(candidate for candidate in result[1]["candidates"] if candidate.get("selected"))
    assert selected["cross_scene_diversity"]["expected_scene_count"] == 1
    assert selected["cross_scene_diversity"]["compared_scene_count"] == 1
    assert selected["cross_scene_diversity"]["passed"] is True


def test_wealth_image_candidates_block_when_comparative_vision_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        Image.new("RGB", (96, 128), "#506070").save(output_path)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        return {**record, "passed": True, "score": 0.9, "reasons": [], "selected": False, "qa": {}}

    app = create_test_app(tmp_path)
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)
    monkeypatch.setattr(
        video_generator,
        "compare_visual_candidates",
        lambda *_args, **_kwargs: {"status": "unavailable"},
    )

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [{"segment_type": "body", "visual_prompt": "A wallet beside coins."}],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
    )

    assert result[1]["status"] == "regenerate_scene"
    assert "comparative_vision_unverified" in result[1]["reasons"]


def test_wealth_image_candidates_require_a_passing_candidate(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        output_path.write_bytes(b"image" * 1200)
        return []

    async def reject_candidate(settings, record, *, scene, topic_title, channel_profile):
        return {**record, "passed": False, "score": 0.0, "reasons": ["semantic_off_topic"], "selected": False, "qa": {}}

    app = create_test_app(tmp_path)
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", reject_candidate)

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [{"segment_type": "body", "headline": "SAVE MORE", "visual_prompt": "A wallet beside three bowls."}],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
    )

    assert result[1]["candidate_count"] == 3
    assert result[1]["status"] == "regenerate_scene"
    assert result[1]["selected_image_path"] == ""
    assert result[1]["reasons"] == ["semantic_off_topic"]


def test_wealth_image_candidates_only_generate_requested_missing_segments(tmp_path: Path, monkeypatch) -> None:
    _stub_idle_gpu_for_candidate_test(monkeypatch)
    generated_paths: list[str] = []

    class FakeService:
        def __init__(self, settings) -> None:
            self.settings = settings

        def release_memory(self) -> bool:
            return True

    async def generate_still(
        service, output_path, *, prompt, channel_profile, deterministic_seed=None, max_prompt_attempts=2, preferred_checkpoint=None
    ):
        generated_paths.append(output_path.name)
        output_path.write_bytes(b"image" * 1200)
        return []

    async def audit_candidate(settings, record, *, scene, topic_title, channel_profile):
        return {**record, "passed": True, "score": 0.8, "reasons": [], "selected": False, "qa": {}}

    app = create_test_app(tmp_path)
    monkeypatch.setattr(video_generator, "ComfyUiImageGenerationService", FakeService)
    monkeypatch.setattr(video_generator, "_generate_comfy_scene_still", generate_still)
    monkeypatch.setattr(video_generator, "_audit_image_candidate", audit_candidate)

    result = asyncio.run(
        video_generator._prepare_wealth_image_candidates(
            app.state.settings,
            [
                {"segment_type": "body", "visual_prompt": "A wallet beside coins."},
                {"segment_type": "body", "visual_prompt": "A coin sorter beside a bowl."},
            ],
            video_dir=tmp_path,
            topic_title="Stop Lifestyle Creep",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
            segments={2},
        )
    )

    assert list(result) == [2]
    assert generated_paths == [
        "s02.candidate-01.png",
        "s02.candidate-02.png",
        "s02.candidate-03.png",
    ]


def test_comfy_negative_prompt_blocks_pseudotext_compositions() -> None:
    prompt = video_generator._comfy_negative_visual_prompt("wealth_blueprint").casefold()

    assert "sticky note" in prompt
    assert "wall of cards" in prompt
    assert "typography" in prompt


def test_finance_negative_prompt_never_negates_primary_coin_sorter() -> None:
    positive = (
        "Scene: a stainless-steel mechanical coin sorter dividing coins into trays. "
        "Scene uniqueness rule: do not show glass jar, savings jar, hourglass, plant."
    )

    negative = video_generator._comfy_negative_visual_prompt(
        "wealth_blueprint",
        prompt=positive,
    ).casefold()

    assert "coin sorter" not in negative
    assert "savings jar" in negative


def test_finance_domino_prompt_uses_stable_money_story_props() -> None:
    prompt = video_generator._finance_scene_subject(
        "A takeaway cup, headphones, and parcel form a domino chain toward a wallet."
    ).casefold()

    assert "one plain takeaway coffee cup" in prompt
    assert "coins forming a clear trail from the cup" in prompt
    assert "recurring small purchase draining money" in prompt
    assert "earbud" not in prompt
    assert "headphone" not in prompt


def test_image_candidate_semantic_context_contains_literal_scene_contract() -> None:
    context = video_generator._image_candidate_semantic_context(
        "Tiny Purchases",
        {
            "headline": "THE DAILY LEAK",
            "narration": "Daily purchases drain annual savings.",
            "visual_prompt": "A takeaway cup, headphones, and parcel form a domino chain toward a wallet.",
        },
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert "Video topic: Tiny Purchases" in context
    assert "Spoken meaning (audio/caption context only" in context
    assert "Daily purchases drain annual savings." in context
    assert "Required visible evidence:" in context
    assert "one plain takeaway coffee cup" in context
    assert "coins forming a clear trail from the cup" in context


def test_image_candidate_semantic_context_does_not_require_spoken_numbers_in_pixels() -> None:
    context = video_generator._image_candidate_semantic_context(
        "Why Compound Interest Rewards Time More Than Hype",
        {
            "headline": "STARTING BASE",
            "narration": "Start with $100 at a hypothetical 5% annual rate.",
            "visual_subject": (
                "faceless adult hands holding one blank unbranded bank card directly above one closed leather wallet"
            ),
            "visual_prompt": "Documentary personal-finance photograph.",
            "forbidden_visual_terms": ["coin", "calculator", "readable text"],
        },
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    required = context.split("Required visible evidence:", maxsplit=1)[1]
    assert "$100" not in required
    assert "5%" not in required
    assert "blank unbranded bank card" in required
    assert "closed leather wallet" in required


def test_visual_metrics_detect_accidental_two_panel_collage(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    image_path = tmp_path / "collage.png"
    image = Image.new("RGB", (240, 400), (60, 45, 35))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 198, 239, 204), fill=(255, 255, 255))
    image.save(image_path)

    metrics = video_generator._image_visual_metrics(image_path)

    assert metrics["panel_separator_detected"] is True


def test_visual_metrics_do_not_mark_continuous_photo_as_collage(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "continuous.png"
    image = Image.new("RGB", (240, 400))
    for y in range(400):
        shade = 35 + (y // 8)
        for x in range(240):
            image.putpixel((x, y), (shade, 55, 48))
    image.save(image_path)

    metrics = video_generator._image_visual_metrics(image_path)

    assert metrics["panel_separator_detected"] is False


def test_visual_metrics_detect_dark_low_detail_gutter_between_stacked_subjects(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    image_path = tmp_path / "dark-stacked-panels.png"
    image = Image.new("RGB", (240, 400), (48, 62, 55))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((55, 35, 185, 155), radius=18, fill=(122, 77, 48), outline=(220, 180, 120), width=5)
    draw.rounded_rectangle((55, 245, 185, 365), radius=18, fill=(122, 77, 48), outline=(220, 180, 120), width=5)
    image.save(image_path)

    metrics = video_generator._image_visual_metrics(image_path)

    assert metrics["panel_separator_detected"] is True


def test_visual_metrics_allow_off_center_tabletop_gap_in_continuous_photo(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    image_path = tmp_path / "bowl-and-foreground-coins.png"
    image = Image.new("RGB", (240, 400), (52, 38, 28))
    draw = ImageDraw.Draw(image)
    draw.ellipse((25, 40, 215, 225), fill=(92, 82, 72), outline=(205, 190, 165), width=6)
    for x in range(45, 200, 24):
        draw.ellipse((x, 95, x + 34, 115), fill=(155, 145, 125), outline=(225, 215, 195), width=3)
    for x in range(25, 220, 36):
        draw.ellipse((x, 295, x + 42, 318), fill=(145, 135, 118), outline=(215, 205, 185), width=3)
    image.save(image_path)

    metrics = video_generator._image_visual_metrics(image_path)

    assert metrics["panel_separator_detected"] is False


def test_finance_negative_prompt_allows_controlled_faceless_adult_framing() -> None:
    negative = video_generator._comfy_negative_visual_prompt(
        "wealth_blueprint",
        prompt="a faceless adult seen from behind walking away from a shop, hands outside the frame",
    ).casefold()

    assert "person, people, human" not in negative
    assert "visible face" in negative
    assert "malformed hands" in negative
    assert "extra fingers" in negative


def test_finance_prompt_and_negative_prompt_preserve_required_hand_action() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "visual_prompt": (
            "faceless adult hand moving two earned plain coins from a small brass dish onto one larger coin stack "
            "on black stone"
        ),
    }

    prompt = video_generator._comfy_scene_image_prompt(
        scene,
        segment=4,
        total=10,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()
    negative = video_generator._comfy_negative_visual_prompt(
        "wealth_blueprint",
        prompt=prompt,
    ).casefold()

    assert "hand action fully visible" in prompt
    assert "hands outside the frame" not in prompt
    assert "no people, no hands" not in prompt
    assert "person, people, human, hand, hands, fingers" not in negative
    assert "malformed hands" in negative
    assert "extra fingers" in negative


def test_finance_prompt_without_human_action_still_excludes_people_and_hands() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "wealth_blueprint",
            "visual_prompt": "two plain metal coin towers at different heights on a charcoal stone plinth",
        },
        segment=2,
        total=10,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    ).casefold()
    negative = video_generator._comfy_negative_visual_prompt(
        "wealth_blueprint",
        prompt=prompt,
    ).casefold()

    assert "no people, no hands" in prompt
    assert "person, people, human, hand, hands, fingers" in negative


def test_dark_history_prompt_requires_authentic_people_and_bans_modern_cgi() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "dark_chronicles",
            "visual_subject": "A lone woman dancing while worried townspeople gather around her.",
        },
        segment=1,
        total=8,
        channel_profile=DARK_CHRONICLES_PROFILE,
    ).casefold()
    negative = video_generator._comfy_negative_visual_prompt(
        "dark_chronicles",
        prompt="a woman dancing in a crowded square",
    ).casefold()

    assert "authentic 1518 strasbourg" in prompt
    assert "timber-framed city street" in prompt
    assert "person, people, human" not in negative
    assert "modern building" in negative
    assert "3d render" in negative
    assert "walking calmly" in negative
    assert "freeze the peak of physical action" in prompt


def test_comfy_prompt_keeps_narrative_action_when_visual_subject_is_generic() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "dark_chronicles",
            "visual_subject": "A central historical person in visible distress.",
            "visual_prompt": (
                "Historical reconstruction. Scene line: They danced until their feet bled. "
                "slow cinematic push-in, no readable text. 9:16 vertical."
            ),
        },
        segment=1,
        total=11,
        channel_profile=DARK_CHRONICLES_PROFILE,
    ).casefold()

    assert prompt.startswith("vertical 9:16. scene: they danced until their feet bled.")
    assert "do not replace the described action with a standing portrait" in prompt
    assert "one foot lifted from the ground" in prompt


def test_dark_history_prompt_prefers_reviewed_visual_prompt_over_unrelated_subject() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "dark_chronicles",
            "visual_subject": "empty cobblestone street with scattered shoes",
            "visual_prompt": (
                "A woman in sixteenth-century clothing dances uncontrollably in a Strasbourg street, "
                "while alarmed witnesses recoil behind her"
            ),
        },
        segment=1,
        total=11,
        channel_profile=DARK_CHRONICLES_PROFILE,
    ).casefold()

    assert "woman in sixteenth-century clothing dances uncontrollably" in prompt
    assert "scene: empty cobblestone street" not in prompt
    assert "required secondary subjects: visibly include reacting witnesses" in prompt
    assert "do not omit, crop out, hide" in prompt


def test_dark_history_prompt_requires_described_neighbors_in_frame() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "dark_chronicles",
            "visual_prompt": (
                "A lone woman dances uncontrollably in a Strasbourg street while worried neighbors "
                "watch from timber doorways"
            ),
        },
        segment=3,
        total=11,
        channel_profile=DARK_CHRONICLES_PROFILE,
    ).casefold()

    assert "required secondary subjects: visibly include worried neighbors" in prompt
    assert "reacting to the main action" in prompt
    assert "replace these people with empty architecture" in prompt
    assert "both arms thrown outward" in prompt
    assert "three frightened neighbors large enough to recognize" in prompt
    assert "open timber doorways on both sides" in prompt
    assert prompt.index("required secondary subjects") < prompt.index("style: raw photorealistic")
    assert "show several dancers" not in prompt


def test_dark_history_prompt_drops_spoken_line_before_drawable_visual_action() -> None:
    prompt = video_generator._comfy_scene_image_prompt(
        {
            "channel_profile_key": "dark_chronicles",
            "visual_subject": "an unrelated exhausted dancer at the gate",
            "visual_prompt": (
                "Scene line: Historical accounts disagree about deaths, but not about the panic. "
                "A civic clerk moves wooden tally tokens beside sealed records while worried officials argue behind him. "
                "slow cinematic push-in, no readable text. 9:16 vertical."
            ),
        },
        segment=8,
        total=11,
        channel_profile=DARK_CHRONICLES_PROFILE,
    ).casefold()

    assert "scene: a civic clerk moves wooden tally tokens" in prompt
    assert "scene: historical accounts disagree" not in prompt
    assert "unrelated exhausted dancer" not in prompt
    assert prompt.count("a civic clerk moves wooden tally tokens") == 1
    assert "closed wax-sealed leather-bound ledgers with blank covers" in prompt
    assert "one foot lifted" not in prompt
    assert prompt.index("scene: a civic clerk") < prompt.index("style: raw photorealistic")


def test_dark_history_negative_prompt_does_not_negate_intended_empty_street() -> None:
    negative = video_generator._comfy_negative_visual_prompt(
        "dark_chronicles",
        prompt="The Strasbourg city gate at dawn with an empty street beyond.",
    ).casefold()

    assert "empty street" not in negative


def test_motion_shot_frame_counts_keep_editorial_cuts_under_two_seconds() -> None:
    shots = video_generator._motion_shot_frame_counts(150)

    assert shots == [50, 50, 50]
    assert sum(shots) == 150
    assert max(shots) / 30.0 < 2.0


def test_image_motion_clip_uses_real_alternate_cutaway(tmp_path: Path) -> None:
    primary = tmp_path / "primary.png"
    alternate = tmp_path / "alternate.png"
    output = tmp_path / "cutaway-motion.mp4"
    Image.new("RGB", (512, 896), (18, 38, 72)).save(primary)
    Image.new("RGB", (512, 896), (186, 112, 24)).save(alternate)

    rendered = video_generator._write_image_motion_clip(
        output,
        image_path=primary,
        alternate_image_path=alternate,
        scene={"duration_s": 5.0},
        segment=1,
    )
    profile = analyze_video_visual_profile(output, max_video_seconds=10.0)

    assert rendered is True
    assert profile["first_cut_sec"] is not None
    assert profile["first_cut_sec"] <= 2.0
    assert profile["avg_cut_interval_sec"] is not None
    assert profile["avg_cut_interval_sec"] <= 2.0


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="prod-video-test",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Dlaczego inflacja niszczy budzet",
            description="",
            total_score=88,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "hook": {"selected": {"text": "Inflacja zjada pensje", "tension_score": 8}},
            "visuals": {
                "scenes": [
                    {
                        "scene_id": 0,
                        "segment_type": "body",
                        "duration_s": 2,
                        "headline": "SZOK",
                        "visual_prompt": "A dark premium finance chart moves upward, camera slowly zooms",
                    },
                    {
                        "scene_id": 1,
                        "segment_type": "body",
                        "duration_s": 2,
                        "headline": "PLAN",
                        "visual_prompt": "A faceless person reviews a budget dashboard, subtle parallax",
                    },
                ]
            },
        },
    )


def test_video_workflow_injects_ltx_scene_without_json_string_corruption(tmp_path: Path) -> None:
    workflow = video_generator._load_or_create_workflow(tmp_path / "ltx_video_default.json", "ltx")
    injected = video_generator._inject_scene(
        workflow,
        {
            "duration_s": 4,
            "headline": "3 SEKUNDY",
            "visual_prompt": "A chart with investor's hand, cinematic",
        },
        backend="ltx",
        segment=1,
        total=2,
    )

    prompt = injected["2"]["inputs"]["text"]
    assert "investor's hand" in prompt
    assert injected["4"]["inputs"]["length"] % 8 == 1
    assert injected["7"]["inputs"]["filename_prefix"] == "atnos_ltx_01"


def test_video_workflow_injects_wan_scene_with_wan_specific_settings(tmp_path: Path) -> None:
    workflow = video_generator._load_or_create_workflow(tmp_path / "wan_video_default.json", "wan")
    injected = video_generator._inject_scene(
        workflow,
        {
            "duration_s": 4,
            "headline": "RYZYKO",
            "visual_prompt": "A faceless investor checks a household budget, cinematic",
        },
        backend="wan",
        segment=2,
        total=3,
    )

    prompt = injected["2"]["inputs"]["text"]
    assert "Wan video" in prompt
    assert "household budget" in prompt
    assert injected["4"]["class_type"] == "EmptyLatentVideo"
    assert injected["4"]["inputs"]["length"] % 8 == 1
    assert injected["5"]["inputs"]["steps"] == 20
    assert injected["5"]["inputs"]["cfg"] == 4.0
    assert injected["7"]["inputs"]["filename_prefix"] == "atnos_wan_02"


def test_video_workflow_diagnostics_reports_valid_ltx_template(tmp_path: Path) -> None:
    workflow = video_generator._load_or_create_workflow(tmp_path / "ltx_video_default.json", "ltx")

    diagnostics = video_generator._workflow_diagnostics(workflow, "ltx")

    assert diagnostics["valid"] is True
    assert diagnostics["mode"] == "ltx"
    assert diagnostics["missing_class_types"] == []
    assert diagnostics["missing_placeholders"] == []
    assert "VHS_VideoCombine" in diagnostics["class_types"]


def test_video_generator_builds_valid_multi_scene_raw_video(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["status"] == "placeholder_ready"
    assert result.payload["fallback_used"] is True
    assert result.payload["concat"]["clip_count"] == 2
    raw_video = Path(result.payload["video_raw_path"])
    assert raw_video.exists()
    assert raw_video.stat().st_size > 4096
    assert len(result.payload["clips"]) == 2


def test_video_generator_uses_reference_profile_for_clip_duration_and_grading(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    profile = ReferenceShortsProfile(
        available=True,
        source="test",
        sample_count=3,
        median_avg_cut_interval_sec=2.4,
        median_brightness=0.24,
        median_saturation=0.52,
    )
    scenes = video_generator._select_generation_scenes(
        app.state.settings,
        [{"duration_s": 4, "visual_prompt": "finance chart"}, {"duration_s": 4, "visual_prompt": "budget"}],
        reference_profile=profile,
    )

    assert [scene["generation_duration_s"] for scene in scenes] == [2.4, 2.4]
    assert "contrast=1.20" in video_generator._motion_eq_filter(profile)
    assert "saturation=0.71" in video_generator._motion_eq_filter(profile)
    assert video_generator._vignette_shade_alpha(profile) == 145


def test_motion_graphic_fallback_writes_probeable_scene_clip(tmp_path: Path) -> None:
    clip_path = tmp_path / "motion.mp4"

    ok = video_generator._write_motion_graphic_clip(
        clip_path,
        scene={
            "generation_duration_s": 1.0,
            "headline": "MONEY TEST",
            "visual_prompt": "A dark minimalist fintech dashboard with emerald glow and clean chart motion",
            "motion_style": "slow camera push, smooth motion, high contrast",
        },
        segment=1,
        total=3,
    )

    assert ok is True
    assert clip_path.exists()
    assert clip_path.stat().st_size > 4096
    assert video_generator._is_probeable_video(clip_path) is True


def test_motion_graphic_uses_channel_profile_for_sigma_visual_style(tmp_path: Path) -> None:
    clip_path = tmp_path / "sigma-motion.mp4"
    palette = video_generator._motion_palette(
        "finance reference profile should not mute sigma",
        reference_profile=ReferenceShortsProfile(available=True, median_brightness=0.44, median_saturation=0.18),
        channel_profile=SIGMA_SQUAD_PROFILE,
    )

    ok = video_generator._write_motion_graphic_clip(
        clip_path,
        scene={
            "generation_duration_s": 1.0,
            "headline": "OHIO TEST",
            "visual_prompt": "Sigma vs Ohio energy, neon chaos, deep-fried 3D meme render",
            "motion_style": "very fast punchy motion, zoom bursts, meme compilation pacing",
        },
        segment=1,
        total=3,
        reference_profile=ReferenceShortsProfile(available=True, median_brightness=0.44, median_saturation=0.18),
        channel_profile=SIGMA_SQUAD_PROFILE,
    )

    assert palette["profile_key"] == "sigma_squad"
    assert palette["accent_rgb"] == (204, 255, 0)
    assert "saturation=1.24" in video_generator._motion_eq_filter(channel_profile=SIGMA_SQUAD_PROFILE)
    assert video_generator._vignette_shade_alpha(channel_profile=SIGMA_SQUAD_PROFILE) == 34
    assert ok is True
    assert clip_path.exists()
    assert clip_path.stat().st_size > 4096


def test_motion_graphic_uses_readable_wealth_blueprint_visual_style() -> None:
    palette = video_generator._motion_palette(
        "finance reference profile should stay readable",
        reference_profile=ReferenceShortsProfile(available=True, median_brightness=0.44, median_saturation=0.18),
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert palette["profile_key"] == "wealth_blueprint"
    assert palette["bg_top"] != "#030706"
    assert palette["bg_bottom"] != "#09251E"
    assert palette["accent_rgb"] == (159, 216, 194)
    assert "saturation=0.58" in video_generator._motion_eq_filter(channel_profile=WEALTH_BLUEPRINT_PROFILE)
    assert "brightness=0.090" in video_generator._motion_eq_filter(channel_profile=WEALTH_BLUEPRINT_PROFILE)
    assert video_generator._vignette_shade_alpha(channel_profile=WEALTH_BLUEPRINT_PROFILE) == 34


def test_video_quality_plan_scores_topic_relevant_visuals(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": "Faceless person sorting receipts, cash, credit card, and a glowing budget app on a dark desk.",
            "visual_subject": "receipts cash credit card budget app",
        },
        {
            "visual_prompt": "Clean index fund chart reflected on a phone screen beside still cash and a simple calendar.",
            "visual_subject": "index chart phone cash calendar",
        },
    ]
    clips = [{"status": "generated"}, {"status": "generated"}]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_visual_relevance_floor"] is True
    assert quality["avg_visual_relevance_score"] >= 0.78


def test_motion_graphic_subject_motifs_follow_channel_topic() -> None:
    assert (
        video_generator._motion_subject_motif(
            "paycheck landing on a desk while receipts slide away",
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
        == "income"
    )
    assert (
        video_generator._motion_subject_motif(
            "torchlit medieval courtroom with a sealed parchment law",
            channel_profile=DARK_CHRONICLES_PROFILE,
        )
        == "court"
    )
    assert (
        video_generator._motion_subject_motif(
            "glitchy 3D NPC avatar staring at a phone",
            channel_profile=SIGMA_SQUAD_PROFILE,
        )
        == "npc"
    )


def test_finance_motifs_distinguish_real_editorial_actions() -> None:
    prompts = [
        "a mechanical coin sorter splitting cash between a shopping basket and savings bowl",
        "an analog clock beside loose cash and an empty wallet",
        "takeaway cup and headphones forming a domino chain toward an empty wallet",
        "cash leaking toward a floor drain beside a delivery bag",
        "a hand placing coins into a jar before buying a book",
        "cash blocked by tiny purchase objects across a path toward a plant",
    ]

    motifs = {
        video_generator._motion_subject_motif(prompt, channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for prompt in prompts
    }

    assert motifs == {
        "allocation_sorter",
        "time_pressure",
        "micro_spending",
        "money_leak",
        "saving_action",
        "spending_friction",
    }


def test_finance_motifs_preserve_distinct_spoken_beats() -> None:
    prompts = [
        "a payday envelope feeding coins through an allocation gate",
        "a payday envelope landing beside coins still untouched",
        "a delivery bag and takeaway cup beginning a domino chain",
        "a subscription token on a checkout conveyor",
        "everyday purchase objects forming a domino chain",
        "mixed loose coins before empty allocation trays",
        "five plain everyday purchase objects behind a brass barrier",
        "one coin redirected by a brass switch toward investing",
        "a payday envelope protected by a red velvet barrier",
        "one final coin entering a savings bowl",
        "a physical fork between a shopping basket and a savings bowl",
    ]

    motifs = [
        video_generator._motion_subject_motif(prompt, channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for prompt in prompts
    ]

    assert len(set(motifs)) == len(prompts)


def test_finance_motifs_preserve_distinct_compound_interest_beats() -> None:
    prompts = [
        "two layered brass coin towers showing the original base and prior growth",
        "a brass circular mechanism returning earned coins into the next period",
        "hands combining one large base stack with a smaller earned stack",
        "a mechanical arm returning earned coins before adding a new layer on top",
        "progressively taller physical coin towers across repeated periods",
        "two clear glass cylinders showing the first earned layer",
        "two parallel rows of identical plain coins showing an earlier start",
        "six identical plain coins while a speculative token stays behind a barrier",
        "a starting coin stack beside repeated contribution coins and an hourglass",
        "one final plain coin beside a plant indicating long-term growth",
    ]

    motifs = [
        video_generator._motion_subject_motif(prompt, channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for prompt in prompts
    ]

    assert len(set(motifs)) == len(prompts)
    assert all(motif.startswith("compound_") for motif in motifs)


def test_current_compound_interest_contracts_keep_ten_distinct_motifs() -> None:
    prompts = [
        "adult hand rolling one brass coin along a circular brass track toward a taller stack",
        "one complete transparent green value block with five small brass growth tokens",
        "one large transparent starting block and five tokens in a precision metal tray",
        "adult hand transferring one complete glass value block between two calculation trays",
        "a brass feedback wheel returning a layer into a larger glass base",
        "three distinct transparent value columns with brass growth layers",
        "adult hand placing one brass coin onto a vertical stack of transparent green blocks",
        "a balance scale with coins and one closed brass padlock",
        "one contribution coin at the end of a row of matching coins beside an hourglass",
        "one completed transparent scenario block locked into a brass planning frame",
    ]

    motifs = [
        video_generator._motion_subject_motif(prompt, channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for prompt in prompts
    ]

    assert len(set(motifs)) == 10
    assert all(motif.startswith("compound_") for motif in motifs)


def test_reference_v1_compound_storyboard_keeps_eight_distinct_motifs() -> None:
    prompts = [
        "a hand placing a clear starting-value block on a brass timeline rail beside an hourglass",
        "an hourglass aligned with three clear growth stages along a timing rail",
        "hands placing one thin transparent growth layer onto an acrylic base block in a steel tray",
        "an acrylic base block with one transparent growth layer attached on a stone planning surface",
        "three transparent value layers crossing positions on a brass timing rail",
        "a hand transferring one small transparent growth layer onto an acrylic base block",
        "a hand comparing a scenario block with a padlock beside a calculator",
        "a hand closing a leather planning folder beside a calculator and hourglass",
    ]

    motifs = [
        video_generator._motion_subject_motif(prompt, channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for prompt in prompts
    ]

    assert len(set(motifs)) == len(prompts)
    assert all(motif.startswith("compound_") for motif in motifs)


def test_reference_v1_compound_storyboard_has_concrete_relevant_scenes() -> None:
    prompts = [
        "a hand placing a clear starting-value base block on a brass timing rail beside an hourglass",
        "an hourglass aligned with three clear growth stages along a timing rail",
        "hands placing one transparent growth layer onto an acrylic base block in a steel planning tray",
        "an acrylic base block with one transparent growth layer attached on a stone planning surface",
        "three transparent value layers crossing positions on a brass timing rail",
        "a hand transferring one transparent growth layer onto an acrylic base block",
        "a hand comparing a scenario block with a padlock beside a calculator",
        "a hand closing a planning folder beside a calculator and hourglass on a desk",
    ]

    for prompt in prompts:
        scene = {"visual_prompt": prompt, "visual_subject": prompt, "motion_style": "slow camera push"}
        assert video_generator._scene_visual_relevance_score(
            scene,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        ) >= 0.58
        assert video_generator._scene_concreteness_audit(
            scene,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )["status"] == "pass"


def test_wealth_thumbnail_polish_restrains_saturation(tmp_path: Path) -> None:
    image_path = tmp_path / "thumbnail.png"
    image = Image.new("RGB", (1280, 720), (0, 255, 70))
    for x in range(0, 1280, 32):
        for y in range(0, 720, 32):
            color = (255, 30, 30) if (x // 32 + y // 32) % 2 else (0, 255, 70)
            for px in range(x, min(x + 16, 1280)):
                for py in range(y, min(y + 16, 720)):
                    image.putpixel((px, py), color)
    image.save(image_path)
    context = ProductionContext(
        job_id="thumb-wealth",
        account_id="youtube:account-c5034bf4@example.invalid",
        topic=ApprovedTopic(
            id="compound-thumb",
            account_id="youtube:account-c5034bf4@example.invalid",
            title="Why Compound Interest Rewards Time More Than Hype",
            description="",
            total_score=90,
            content_profile="wealth_blueprint",
        ),
        artifact_dir=tmp_path,
        artifacts={"hook": {"selected": {"text": "TIME BEATS HYPE"}}},
    )

    thumbnail_generator._polish_thumbnail(
        image_path,
        context,
        {"punch_text": "TIME BEATS HYPE"},
        "#9FD8C2",
    )
    audit = thumbnail_generator.audit_thumbnail_quality(image_path, profile_key="wealth_blueprint")

    assert audit["metrics"]["saturation"] <= 145
    assert "wealth_thumbnail_too_childish_saturated" not in audit["blockers"]


def test_compound_interest_quality_plan_requires_real_beat_variety(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_visual_min_real_clips = 3
    scenes = [
        {"visual_prompt": "two layered brass coin towers showing the original base and prior growth"},
        {"visual_prompt": "a brass circular mechanism returning earned coins into the next period"},
        {"visual_prompt": "hands combining one large base stack with a smaller earned stack"},
        {"visual_prompt": "progressively taller physical coin towers across repeated periods"},
        {"visual_prompt": "two parallel rows of identical plain coins showing an earlier start"},
        {"visual_prompt": "a starting coin stack beside repeated contribution coins and an hourglass"},
    ]
    clips = [
        {"status": "generated", "composition_signature": f"finance:compound:layout-{index}"}
        for index in range(len(scenes))
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["unique_concept_count"] == len(scenes)
    assert quality["passes_concept_diversity_floor"] is True
    assert quality["passes_visual_variety_floor"] is True


def test_compound_interest_comfy_subjects_do_not_collapse_to_one_hourglass() -> None:
    prompts = [
        "two layered brass coin towers showing the original base and prior growth",
        "a brass circular mechanism returning earned coins into the next period",
        "combining one large base stack with a smaller earned stack",
        "a mechanical arm returning earned coins before adding a new layer on top",
        "progressively taller physical coin towers across repeated periods",
        "two clear glass cylinders holding the first earned layer",
        "two parallel rows of identical plain coins showing an earlier start",
        "six identical plain coins while a speculative token remains behind a barrier",
        "a starting coin stack beside repeated contribution coins and an hourglass",
    ]

    subjects = [video_generator._finance_scene_subject(prompt) for prompt in prompts]

    assert len(set(subjects)) == len(prompts)
    assert sum("large clear hourglass containing" in subject for subject in subjects) == 0
    assert any("circular coin track" in subject for subject in subjects)
    assert any("glass cylinders" in subject for subject in subjects)
    assert any("red velvet rope barrier" in subject for subject in subjects)


def test_empty_wallet_is_not_misclassified_as_empty_wall(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": "cash and coins form a domino chain toward an empty wallet on a desk",
            "visual_subject": "cash coins and empty wallet",
            "motion_style": "coins moving with a slow camera push",
        }
    ]
    clips = [{"status": "generated", "composition_signature": "wealth:micro-spending:domino"}]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_scene_concreteness_floor"] is True
    assert quality["scene_concreteness_risk_count"] == 0


def test_motion_graphic_composition_signature_rotates_by_segment() -> None:
    prompt = "Clean index fund ETF chart reflected on a phone screen beside a simple calendar."

    signatures = {
        video_generator._motion_composition_signature(
            prompt,
            segment=segment,
            total=3,
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
        for segment in (1, 2, 3)
    }

    assert len(signatures) == 3
    assert all(signature.startswith("wealth_blueprint:finance:investing:") for signature in signatures)


def test_video_quality_plan_blocks_repeated_compositions(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_visual_min_real_clips = 3
    repeated_scenes = [
        {"visual_prompt": "Phone budget dashboard with receipts and cash on a desk."},
        {"visual_prompt": "Phone budget dashboard with receipts and cash on a desk."},
        {"visual_prompt": "Phone budget dashboard with receipts and cash on a desk."},
    ]
    varied_scenes = [
        {"visual_prompt": "Open wallet beside a paycheck and salary envelope."},
        {"visual_prompt": "Delivery parcel and headphones show micro spending."},
        {"visual_prompt": "Cracked pipe leaking coins into a dark drain."},
    ]
    repeated_clips = [
        {"status": "generated", "composition_signature": "wealth:finance:budget:receipt_phone_card"},
        {"status": "generated", "composition_signature": "wealth:finance:budget:receipt_phone_card"},
        {"status": "generated", "composition_signature": "wealth:finance:budget:receipt_phone_card"},
    ]
    varied_clips = [
        {"status": "generated", "composition_signature": "wealth:finance:budget:receipt_phone_card"},
        {"status": "generated", "composition_signature": "wealth:finance:budget:split_chart_phone"},
        {"status": "generated", "composition_signature": "wealth:finance:budget:cash_leak_closeup"},
    ]

    repeated = video_generator._quality_plan(
        app.state.settings,
        repeated_scenes,
        repeated_clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )
    varied = video_generator._quality_plan(
        app.state.settings,
        varied_scenes,
        varied_clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert repeated["passes_composition_diversity_floor"] is False
    assert repeated["passes_visual_variety_floor"] is False
    assert repeated["unique_composition_count"] == 1
    assert varied["passes_composition_diversity_floor"] is True
    assert varied["passes_concept_diversity_floor"] is True
    assert varied["passes_visual_variety_floor"] is True
    assert varied["unique_composition_count"] == 3


def test_video_quality_plan_blocks_rearranged_props_with_one_concept(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_visual_min_real_clips = 3
    scenes = [
        {"visual_prompt": "Savings jar and coins on a desk."},
        {"visual_prompt": "Coins beside a different savings jar and plant."},
        {"visual_prompt": "Three savings bowls with coins and a plant."},
    ]
    clips = [
        {"status": "generated", "composition_signature": f"finance:savings:layout-{index}"}
        for index in range(3)
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_composition_diversity_floor"] is True
    assert quality["unique_concept_count"] == 1
    assert quality["passes_concept_diversity_floor"] is False
    assert quality["passes_visual_variety_floor"] is False


def test_dark_history_motifs_ignore_repeated_topic_anchor() -> None:
    prompts = [
        "A lone woman dancing in Strasbourg. Topic: The Dancing Plague of 1518.",
        "Musicians play drums on a wooden platform. Topic: The Dancing Plague of 1518.",
        "Bruised feet beside torn leather shoes. Topic: The Dancing Plague of 1518.",
        "A worried family watches from an open timber-frame window. Topic: The Dancing Plague of 1518.",
        "A civic clerk moves tally tokens beside sealed records. Topic: The Dancing Plague of 1518.",
        "The church bell tower rises over a packed crowd. Topic: The Dancing Plague of 1518.",
        "An exhausted dancer is supported by two townspeople at the city gate at dawn. Topic: The Dancing Plague of 1518.",
    ]

    motifs = {
        video_generator._motion_subject_motif(prompt, channel_profile=DARK_CHRONICLES_PROFILE)
        for prompt in prompts
    }

    assert motifs == {
        "first_dancer",
        "musicians",
        "footwear_aftermath",
        "window_witnesses",
        "civic_records",
        "bell_tower_crowd",
        "exhausted_aftermath",
    }


def test_dark_history_generated_image_is_graded_below_brightness_floor(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "bright-history.png"
    Image.new("RGB", (128, 128), (220, 210, 190)).save(image_path)

    result = video_generator._normalize_generated_image_for_channel(
        image_path,
        channel_profile=DARK_CHRONICLES_PROFILE,
    )

    assert result["status"] == "graded"
    assert result["after"]["brightness"] < 0.58


def test_dark_wealth_generated_image_is_lifted_to_readable_floor(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "dark-wealth.png"
    Image.new("RGB", (128, 128), (18, 22, 28)).save(image_path)

    result = video_generator._normalize_generated_image_for_channel(
        image_path,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert result["status"] == "graded"
    assert result["after"]["brightness"] >= 0.18


def test_readable_wealth_generated_image_is_not_regraded(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "readable-wealth.png"
    Image.new("RGB", (128, 128), (72, 92, 110)).save(image_path)

    result = video_generator._normalize_generated_image_for_channel(
        image_path,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert result["status"] == "unchanged"
    assert result["after"] == result["before"]


def test_targeted_visual_floor_repair_rebuilds_only_dark_clip(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    app = create_test_app(tmp_path)
    context = _context(tmp_path)
    context.topic = ApprovedTopic(
        id=context.topic.id,
        account_id=context.topic.account_id,
        title=context.topic.title,
        description=context.topic.description,
        total_score=context.topic.total_score,
        content_profile="wealth_blueprint",
    )
    video_dir = context.artifact_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    primary = video_dir / "s01.s01.comfy.png"
    cutaway = video_dir / "s01.s01.comfy.cutaway.png"
    clip_path = video_dir / "s01.mp4"
    Image.new("RGB", (128, 128), (18, 22, 28)).save(primary)
    Image.new("RGB", (128, 128), (20, 24, 30)).save(cutaway)
    clip_path.write_bytes(b"existing")
    context.artifacts["visuals"] = {
        "scenes": [{"visual_prompt": "cash and coins moving into savings", "duration_s": 2.0}]
    }
    context.artifacts["voice"] = {"output_file": str(tmp_path / "narration.wav")}
    context.artifacts["video"] = {
        "clips": [
            {
                "segment": 1,
                "file": str(clip_path),
                "status": "generated",
                "backend": "comfy_image_motion",
                "target_backend": "comfy_image_motion",
                "duration_s": 2.0,
                "generated_image_paths": [str(primary), str(cutaway)],
                "image_visual_audit": {
                    "status": "fail",
                    "failures": ["finance_visual_too_dark"],
                },
                "cutaway_image_visual_audit": {
                    "status": "fail",
                    "failures": ["finance_visual_too_dark"],
                },
            }
        ],
        "video_raw_path": str(video_dir / "video_raw.mp4"),
        "workflow_diagnostics": {"valid": True},
        "backend": {"mode": "comfy_image_motion"},
        "fallback_used": False,
    }
    monkeypatch.setattr(
        video_generator,
        "_write_image_motion_clip",
        lambda path, **_kwargs: path.write_bytes(b"repaired-video") > 0,
    )
    monkeypatch.setattr(video_generator, "_probe_media_duration", lambda _path: 2.0)
    monkeypatch.setattr(video_generator, "_expand_clip_timeline", lambda paths, **_kwargs: paths)
    monkeypatch.setattr(
        video_generator,
        "_audit_generated_image_visual_relevance",
        lambda path, **_kwargs: {
            "status": "pass",
            "image_path": str(path),
            "failures": [],
            "metrics": {"brightness": 0.21},
        },
    )
    monkeypatch.setattr(
        video_generator,
        "_concat_clips",
        lambda _paths, output: (output.write_bytes(b"raw-video"), {"status": "ready"})[1],
    )

    result = video_generator.repair_generated_image_visual_floor(app.state.settings, context)

    assert result is not None
    assert result.payload["targeted_visual_repair"]["segments"] == [1]
    assert result.payload["clips"][0]["visual_floor_repair"]["status"] == "repaired"
    assert result.payload["clips"][0]["image_visual_audit"]["status"] == "pass"


def test_generated_image_text_risk_tokens_detect_readable_words() -> None:
    tokens = video_generator._readable_text_risk_tokens("SIGMA 2026 unreadable @@ A")

    assert "SIGMA" in tokens
    assert "2026" in tokens
    assert "A" not in tokens


def test_video_quality_plan_blocks_generated_image_text_risk(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [{"visual_prompt": "Surreal neon meme character reacting to a glowing object."}]
    clips = [
        {
            "status": "generated",
            "composition_signature": "sigma_squad:meme:reaction:neon_closeup",
            "image_text_audit": {
                "status": "risk",
                "text": "SIGMA",
                "risk_tokens": ["SIGMA"],
            },
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=SIGMA_SQUAD_PROFILE,
    )

    assert quality["passes_generated_image_text_floor"] is False
    assert quality["generated_image_text_risk_count"] == 1


def test_generated_image_visual_audit_blocks_off_profile_finance_image(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "near_black.png"
    Image.new("RGB", (320, 480), (4, 5, 5)).save(image_path)

    audit = video_generator._audit_generated_image_visual_relevance(
        image_path,
        scene={"visual_prompt": "A random empty black wall with no finance objects."},
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert audit["status"] == "fail"
    assert "finance_visual_too_dark" in audit["failures"]


def test_generated_image_visual_audit_blocks_blurry_flat_finance_image(tmp_path: Path) -> None:
    from PIL import Image

    image_path = tmp_path / "flat_finance_blur.png"
    Image.new("RGB", (320, 480), (80, 96, 86)).save(image_path)

    audit = video_generator._audit_generated_image_visual_relevance(
        image_path,
        scene={"visual_prompt": "faceless adult hands sorting a receipt, wallet, cash, and phone budget app"},
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert audit["status"] == "fail"
    assert "finance_visual_blurry_or_unstructured" in audit["failures"]


def test_video_quality_plan_blocks_generated_image_visual_risk(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [{"visual_prompt": "A random empty wall with no finance objects."}]
    clips = [
        {
            "status": "generated",
            "composition_signature": "wealth_blueprint:generic:wall:flat",
            "image_visual_audit": {
                "status": "fail",
                "failures": ["prompt_not_topic_specific", "finance_visual_too_dark"],
            },
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_generated_image_visual_floor"] is False
    assert quality["generated_image_visual_risk_count"] == 1


def test_video_quality_plan_blocks_abstract_chart_only_finance_scene(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": "A dark abstract line graph on a generic background showing income behavior.",
            "visual_subject": "line graph",
            "motion_style": "slow camera push",
        }
    ]
    clips = [
        {
            "status": "generated",
            "composition_signature": "wealth_blueprint:finance:generic:line_graph",
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_scene_concreteness_floor"] is False
    assert quality["scene_concreteness_risk_count"] == 1
    audit = quality["scene_concreteness_audits"][0]
    assert "abstract_or_background_only" in audit["failures"]
    assert "not_enough_physical_objects" in audit["failures"]


def test_video_quality_plan_blocks_childish_blurred_finance_scene(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": (
                "A cute cartoon mascot holding a phone and receipt on a blurred background, playful sticker style."
            ),
            "visual_subject": "cartoon mascot phone receipt",
            "motion_style": "holding objects with slow camera push",
        }
    ]
    clips = [
        {
            "status": "generated",
            "composition_signature": "wealth_blueprint:finance:budget:receipt_phone_card",
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_scene_concreteness_floor"] is False
    audit = quality["scene_concreteness_audits"][0]
    assert "abstract_or_background_only" in audit["failures"]
    assert "finance_childish_or_toy_style" in audit["failures"]


def test_video_quality_plan_blocks_scene_without_topic_anchor(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": "Faceless person near a phone, receipt, cash and card on a premium desk.",
            "visual_subject": "finance desk with person and phone",
            "motion_style": "slow camera push",
            "visual_topic_anchor_terms": ["paycheck", "income", "salary"],
            "visual_topic_anchor_score": 0.0,
        }
    ]
    clips = [
        {
            "status": "generated",
            "composition_signature": "wealth_blueprint:finance:desk:receipt_phone_card",
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_topic_anchor_floor"] is False
    assert quality["topic_anchor_risk_count"] == 1
    assert quality["passes_visual_relevance_floor"] is False
    assert quality["topic_anchor_audits"][0]["failures"] == ["topic_anchor_missing", "topic_anchor_score_too_low"]


def test_video_quality_plan_accepts_concrete_envelope_purchase_finance_scene(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    scenes = [
        {
            "visual_prompt": (
                "overhead shot of three envelopes, cash moving from a spending envelope to a long-term savings "
                "envelope while a faceless hand pauses before a purchase"
            ),
            "visual_subject": "cash moving between spending envelope and savings envelope before purchase",
            "motion_style": "slow camera push with parallax",
            "visual_topic_anchor_terms": ["paycheck", "spending", "cash", "receipt"],
            "visual_topic_anchor_score": 1.0,
        }
    ]
    clips = [
        {
            "status": "generated",
            "composition_signature": "wealth_blueprint:finance:savings:cash_leak_closeup",
        }
    ]

    quality = video_generator._quality_plan(
        app.state.settings,
        scenes,
        clips,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert quality["passes_scene_concreteness_floor"] is True
    assert quality["scene_concreteness_risk_count"] == 0
    matched = set(quality["scene_concreteness_audits"][0]["matched_physical_objects"])
    assert {"cash", "envelope", "purchase"}.issubset(matched)


def test_motion_graphic_finance_clips_pass_actual_visual_variety_gate(tmp_path: Path) -> None:
    video_dir = tmp_path / "video"
    video_dir.mkdir()
    scenes = [
        {
            "visual_prompt": "paycheck landing on a desk while receipts slide away and hands compare income and spending",
            "motion_style": "slow push with parallax",
            "headline": "EARN MORE",
            "overlay_text": "EARN MORE",
            "duration_s": 1,
        },
        {
            "visual_prompt": "split-screen desk scene with a crumpled shopping receipt and growing plant beside savings calendar",
            "motion_style": "slow push with parallax",
            "headline": "SPEND SMART",
            "overlay_text": "SPEND SMART",
            "duration_s": 1,
        },
        {
            "visual_prompt": "phone budget app glowing beside a crossed-out impulse cart and bank card turned face down",
            "motion_style": "slow push with parallax",
            "headline": "WAIT FIRST",
            "overlay_text": "WAIT FIRST",
            "duration_s": 1,
        },
    ]
    clips: list[dict[str, str]] = []

    for index, scene in enumerate(scenes, start=1):
        path = video_dir / f"s{index:02d}.mp4"
        assert video_generator._write_motion_graphic_clip(
            path,
            scene=scene,
            segment=index,
            total=len(scenes),
            channel_profile=WEALTH_BLUEPRINT_PROFILE,
        )
        clips.append({"status": "generated", "file": str(path)})

    report = _measure_actual_clip_visual_variety({"clips": clips})

    assert report["status"] == "pass"
    assert report["mean_hash_distance"] >= report["thresholds"]["min_mean_hash_distance"]
    assert report["mean_top_crop_pixel_diff"] >= report["thresholds"]["min_mean_top_crop_pixel_diff"]


def test_video_generator_prefers_wan_before_ltx_for_local_auto(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    async def fake_comfyui(settings):
        return True

    async def fake_models(settings, mode):
        return (mode == "wan", f"{mode}-status")

    async def fake_generate(settings, workflow, scene, clip_path, segment, total, backend):
        video_generator._write_placeholder_clip(clip_path, duration_s=1)
        return True

    monkeypatch.setattr(video_generator, "_ensure_comfyui", fake_comfyui)
    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)
    monkeypatch.setattr(video_generator, "_try_generate_clip", fake_generate)

    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["backend"]["mode"] == "wan"
    assert result.payload["workflow_template"].endswith("wan_video_default.json")
    assert [clip["backend"] for clip in result.payload["clips"]] == ["remotion_native", "wan"]
    assert result.payload["fallback_used"] is False
    assert result.payload["backend"]["model_attempts"] == [
        {"mode": "wan", "pipeline": "shorts_wan", "ready": True, "message": "wan-status"},
    ]


def test_video_generator_resumes_five_scene_job_after_scene_04_crash(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "wan"
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)
    context.artifacts["visuals"] = {
        "scenes": [
            {
                "scene_id": "hook",
                "segment_type": "hook",
                "duration_s": 1,
                "headline": "YOUR MONEY IS LEAKING",
                "visual_prompt": "A split budget opens with immediate tension.",
            },
            {
                "scene_id": "image",
                "segment_type": "body",
                "duration_s": 1,
                "headline": "SMALL PURCHASES",
                "visual_prompt": "A wallet beside several unbranded everyday purchases.",
            },
            {
                "scene_id": "number",
                "segment_type": "body",
                "duration_s": 1,
                "headline": "3 RULES",
                "visual_prompt": "Three clear budget rules appear one after another.",
            },
            {
                "scene_id": "chart",
                "segment_type": "body",
                "duration_s": 1,
                "headline": "THE GAP GROWS",
                "visual_prompt": "A clean savings chart rises while spending stays flat.",
            },
            {
                "scene_id": "cta",
                "segment_type": "cta",
                "duration_s": 1,
                "headline": "TRACK IT TODAY",
                "visual_prompt": "A concise call to action closes the lesson.",
            },
        ]
    }
    render_calls: list[tuple[str, int]] = []
    crash_scene_04 = True

    async def fake_comfyui(settings):
        return True

    async def fake_models(settings, mode):
        return True, f"{mode}-ready"

    async def fake_generate(settings, workflow, scene, clip_path, segment, total, backend):
        render_calls.append((backend, segment))
        video_generator._write_placeholder_clip(clip_path, duration_s=0.35)
        return True

    def fake_remotion(clip_path, *, scene, segment, total, **kwargs):
        nonlocal crash_scene_04
        render_calls.append(("remotion", segment))
        if segment == 4 and crash_scene_04:
            raise RuntimeError("controlled scene_04 crash")
        video_generator._write_placeholder_clip(clip_path, duration_s=0.35)
        return True

    monkeypatch.setattr(video_generator, "_ensure_comfyui", fake_comfyui)
    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)
    monkeypatch.setattr(video_generator, "_try_generate_clip", fake_generate)
    monkeypatch.setattr(video_generator, "_write_motion_graphic_clip", fake_remotion)

    with pytest.raises(RuntimeError, match="controlled scene_04 crash"):
        asyncio.run(video_generator.run(app.state.settings, context))

    checkpoint_path = context.artifact_dir / "video" / "scene_checkpoint.json"
    first_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    first_scenes = {scene["scene_id"]: scene for scene in first_checkpoint["scenes"]}
    assert [first_scenes[f"scene_{index:02d}"]["status"] for index in range(1, 6)] == [
        "DONE",
        "DONE",
        "DONE",
        "RUNNING",
        "WAITING",
    ]
    first_hashes = {
        index: first_scenes[f"scene_{index:02d}"]["artifact_hash"]
        for index in range(1, 4)
    }
    first_mtimes = {
        index: (context.artifact_dir / "video" / f"s{index:02d}.mp4").stat().st_mtime_ns
        for index in range(1, 4)
    }

    crash_scene_04 = False
    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["status"] == "ready"
    assert render_calls == [
        ("remotion", 1),
        ("wan", 2),
        ("remotion", 3),
        ("remotion", 4),
        ("remotion", 4),
        ("remotion", 5),
    ]
    assert [clip["checkpoint_status"] for clip in result.payload["clips"]] == [
        "CACHED",
        "CACHED",
        "CACHED",
        "DONE",
        "DONE",
    ]
    resumed_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    resumed_scenes = {scene["scene_id"]: scene for scene in resumed_checkpoint["scenes"]}
    assert [resumed_scenes[f"scene_{index:02d}"]["status"] for index in range(1, 6)] == [
        "CACHED",
        "CACHED",
        "CACHED",
        "DONE",
        "DONE",
    ]
    assert [resumed_scenes[f"scene_{index:02d}"]["attempt"] for index in range(1, 6)] == [1, 1, 1, 2, 1]
    for index in range(1, 4):
        assert resumed_scenes[f"scene_{index:02d}"]["artifact_hash"] == first_hashes[index]
        assert (context.artifact_dir / "video" / f"s{index:02d}.mp4").stat().st_mtime_ns == first_mtimes[index]


def test_video_generator_auto_uses_openai_image_motion_only_when_comfyui_unavailable(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    app.state.settings.shorts_allow_cloud_visuals = True
    app.state.settings.openai_api_key = "sk-test-openai"
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    async def fake_comfyui(settings):
        return False

    async def fake_openai_motion(settings, scene, clip_path, segment, total, **kwargs):
        video_generator._write_placeholder_clip(clip_path, duration_s=1)
        return True

    monkeypatch.setattr(video_generator, "_ensure_comfyui", fake_comfyui)
    monkeypatch.setattr(video_generator, "_try_generate_openai_image_motion_clip", fake_openai_motion)

    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["backend"]["mode"] == "openai_image_motion"
    assert result.payload["workflow_template"].endswith("openai_image_motion.json")
    assert [clip["backend"] for clip in result.payload["clips"]] == [
        "remotion_native",
        "openai_image_motion",
    ]
    assert result.payload["fallback_used"] is False
    assert result.payload["backend"]["model_attempts"] == [
        {
            "mode": "openai_image_motion",
            "pipeline": "cloud_image_motion",
            "ready": True,
            "message": "ComfyUI unavailable; OPENAI_API_KEY configured and SHORTS_ALLOW_CLOUD_VISUALS enabled",
        }
    ]


def test_video_generator_selects_comfy_image_motion_with_live_image_checkpoint(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "comfy_image_motion"
    monkeypatch.setattr(
        video_generator.ComfyUiImageGenerationService,
        "resolve_checkpoint_name",
        lambda _self: "sd_xl_turbo_1.0_fp16.safetensors",
    )

    backend = asyncio.run(video_generator._select_backend(app.state.settings, comfy_ready=True))

    assert backend["mode"] == "comfy_image_motion"
    assert backend["models_ready"] is True
    assert backend["model_message"] == "sd_xl_turbo_1.0_fp16.safetensors"


def test_video_generator_auto_blocks_openai_image_motion_after_billing_failure(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    app.state.settings.shorts_allow_cloud_visuals = True
    app.state.settings.openai_api_key = "sk-test-openai"
    error_dir = app.state.settings.data_dir / "production" / "acct" / "job" / "video"
    error_dir.mkdir(parents=True)
    (error_dir / "s01.openai_errors.json").write_text(
        json.dumps(
            {
                "errors": [
                    {
                        "model": "gpt-image-1",
                        "reason": "billing_hard_limit_reached: Billing hard limit has been reached.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    backend = asyncio.run(video_generator._select_backend(app.state.settings, comfy_ready=False))

    assert backend["mode"] == "placeholder"
    assert backend["models_ready"] is False
    assert backend["model_attempts"] == [
        {
            "mode": "openai_image_motion",
            "pipeline": "cloud_image_motion",
            "ready": False,
            "message": "ComfyUI unavailable; OpenAI image motion not ready: billing_hard_limit_reached: Billing hard limit has been reached.",
        }
    ]


def test_video_generator_auto_prefers_local_wan_even_when_openai_image_motion_is_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    app.state.settings.shorts_allow_cloud_visuals = True
    app.state.settings.openai_api_key = "sk-test-openai"

    async def fake_models(settings, mode):
        return (mode == "wan", f"{mode}-ready")

    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)

    backend = asyncio.run(video_generator._select_backend(app.state.settings, comfy_ready=True))

    assert backend["mode"] == "wan"
    assert backend["reason"] == "ready"
    assert backend["model_attempts"] == [
        {"mode": "wan", "pipeline": "shorts_wan", "ready": True, "message": "wan-ready"},
    ]


def test_video_generator_auto_skips_wan_when_runtime_nodes_are_missing(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"

    async def fake_models(settings, mode):
        return True, f"{mode}-models-ready"

    async def fake_nodes(settings, mode):
        if mode == "wan":
            return False, "missing ComfyUI node(s): EmptyLatentVideo"
        return True, "workflow nodes available"

    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)
    monkeypatch.setattr(video_generator, "_comfyui_backend_nodes_ready", fake_nodes)

    backend = asyncio.run(video_generator._select_backend(app.state.settings, comfy_ready=True))

    assert backend["mode"] == "ltx"
    assert backend["reason"] == "ready"
    assert backend["model_attempts"] == [
        {
            "mode": "wan",
            "pipeline": "shorts_wan",
            "ready": False,
            "message": "wan-models-ready; runtime_not_ready: missing ComfyUI node(s): EmptyLatentVideo",
        },
        {"mode": "ltx", "pipeline": "shorts_ltx", "ready": True, "message": "ltx-models-ready"},
    ]


def test_comfyui_runtime_rejects_model_name_missing_from_live_object_info() -> None:
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"},
        }
    }
    object_info = {
        "CheckpointLoaderSimple": {
            "input": {
                "required": {
                    "ckpt_name": [["sd_xl_turbo_1.0_fp16.safetensors"], {}],
                }
            }
        }
    }

    mismatches = video_generator._workflow_runtime_choice_mismatches(workflow, object_info)

    assert mismatches == ["CheckpointLoaderSimple.ckpt_name='ltx-video-2b-v0.9.5.safetensors'"]


def test_comfyui_runtime_accepts_model_name_visible_in_live_object_info() -> None:
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"},
        }
    }
    object_info = {
        "CheckpointLoaderSimple": {
            "input": {
                "required": {
                    "ckpt_name": [["ltx-video-2b-v0.9.5.safetensors"], {}],
                }
            }
        }
    }

    mismatches = video_generator._workflow_runtime_choice_mismatches(workflow, object_info)

    assert mismatches == []


def test_openai_image_candidates_skip_nonexistent_gpt_image_2(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_IMAGE_MODEL", raising=False)

    assert video_generator._openai_image_model_candidates() == (
        "gpt-image-1.5",
        "gpt-image-1",
        "gpt-image-1-mini",
    )


def test_video_generator_downgrades_claimed_generation_when_clip_is_not_probeable(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    async def fake_comfyui(settings):
        return True

    async def fake_models(settings, mode):
        return True, f"{mode}-ready"

    async def fake_generate(settings, workflow, scene, clip_path, segment, total, backend):
        clip_path.write_bytes(b"not-a-real-video" * 500)
        return True

    monkeypatch.setattr(video_generator, "_ensure_comfyui", fake_comfyui)
    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)
    monkeypatch.setattr(video_generator, "_try_generate_clip", fake_generate)

    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["status"] == "placeholder_ready"
    assert result.payload["fallback_used"] is True
    assert [clip["status"] for clip in result.payload["clips"]] == ["generated", "placeholder"]
    assert [clip["backend"] for clip in result.payload["clips"]] == ["remotion_native", "placeholder"]
    assert Path(result.payload["video_raw_path"]).exists()


def test_video_generator_skips_comfyui_when_workflow_template_is_invalid(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    async def fake_comfyui(settings):
        return True

    async def fake_models(settings, mode):
        return True, f"{mode}-ready"

    def fake_load_or_create(path, backend="ltx"):
        return {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "broken.safetensors"}}}

    async def forbidden_generate(*_args, **_kwargs):
        raise AssertionError("ComfyUI generation must not run with an invalid workflow template.")

    monkeypatch.setattr(video_generator, "_ensure_comfyui", fake_comfyui)
    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)
    monkeypatch.setattr(video_generator, "_load_or_create_workflow", fake_load_or_create)
    monkeypatch.setattr(video_generator, "_try_generate_clip", forbidden_generate)

    result = asyncio.run(video_generator.run(app.state.settings, context))

    assert result.payload["status"] == "placeholder_ready"
    assert result.payload["fallback_used"] is True
    assert result.payload["workflow_diagnostics"]["valid"] is False
    assert "CLIPTextEncode" in result.payload["workflow_diagnostics"]["missing_class_types"]
    assert "{positive_prompt}" in result.payload["workflow_diagnostics"]["missing_placeholders"]
    assert [clip["status"] for clip in result.payload["clips"]] == ["generated", "placeholder"]
    assert [clip["backend"] for clip in result.payload["clips"]] == ["remotion_native", "placeholder"]


def test_video_manifest_marks_fallback_when_premium_backend_outputs_motion_graphic() -> None:
    fallback_used = video_generator._video_fallback_used(
        [
            {
                "status": "generated",
                "backend": "motion_graphic",
                "target_backend": "wan",
            }
        ],
        {"mode": "wan"},
        {"valid": True},
    )

    assert fallback_used is True


def test_video_manifest_accepts_remotion_native_for_non_image_wealth_scenes() -> None:
    clips = [
        {
            "status": "generated",
            "backend": "remotion_native",
            "target_backend": "comfy_image_motion",
            "scene_type": scene_type,
        }
        for scene_type in ("HookScene", "NumberScene", "ChartScene", "CTAScene")
    ]

    assert video_generator._video_fallback_used(
        clips,
        {"mode": "comfy_image_motion"},
        {"valid": True},
    ) is False


def test_video_manifest_rejects_remotion_native_for_image_scene() -> None:
    fallback_used = video_generator._video_fallback_used(
        [
            {
                "status": "generated",
                "backend": "remotion_native",
                "target_backend": "comfy_image_motion",
                "scene_type": "ImageScene",
            }
        ],
        {"mode": "comfy_image_motion"},
        {"valid": True},
    )

    assert fallback_used is True


def test_real_visual_requirement_blocks_placeholder_backend(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    app.state.settings.shorts_video_backend = "comfy_image_motion"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError, match="Real visual backend is required"):
        video_generator._assert_real_visual_backend_available(
            app.state.settings,
            {"mode": "placeholder", "reason": "ComfyUI unavailable"},
        )


def test_real_visual_requirement_blocks_motion_fallback_from_premium_backend(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    app.state.settings.shorts_video_backend = "wan"
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError, match="fallback or placeholder"):
        video_generator._assert_real_visual_generation_succeeded(
            app.state.settings,
            {"mode": "wan"},
            [
                {
                    "segment": 1,
                    "status": "generated",
                    "backend": "motion_graphic",
                    "target_backend": "wan",
                }
            ],
            {"valid": True},
            fallback_used=True,
        )


def test_strict_real_visual_mode_fails_before_motion_fallback(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    app.state.settings.shorts_video_backend = "comfy_image_motion"
    app.state.settings.shorts_require_real_visuals = True
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(RuntimeError, match="refusing motion-graphic or placeholder fallback"):
        video_generator._assert_scene_fallback_allowed(
            app.state.settings,
            {"mode": "comfy_image_motion"},
            generated=False,
            segment=2,
            total=7,
        )


def test_explicit_motion_graphic_mode_allows_motion_fallback(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    app.state.settings.shorts_video_backend = "motion_graphic"
    app.state.settings.shorts_require_real_visuals = True
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    video_generator._assert_scene_fallback_allowed(
        app.state.settings,
        {"mode": "motion_graphic"},
        generated=False,
        segment=1,
        total=1,
    )


def test_video_generator_records_model_attempts_when_no_video_models_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.shorts_video_backend = "auto"

    async def fake_models(settings, mode):
        return False, f"{mode}-missing"

    monkeypatch.setattr(video_generator, "_ensure_video_models", fake_models)

    backend = asyncio.run(video_generator._select_backend(app.state.settings, comfy_ready=True))

    assert backend["mode"] == "placeholder"
    assert backend["models_ready"] is False
    assert backend["model_attempts"] == [
        {"mode": "wan", "pipeline": "shorts_wan", "ready": False, "message": "wan-missing"},
        {"mode": "ltx", "pipeline": "shorts_ltx", "ready": False, "message": "ltx-missing"},
    ]


def test_video_generator_rejects_corrupt_comfyui_clip_bytes(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    clip_path = tmp_path / "bad.mp4"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"prompt_id": "prompt-1"}

    class FakeClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    async def fake_poll(client, base_url, prompt_id, **_kwargs):
        return {"filename": "bad.mp4", "type": "output", "subfolder": ""}

    async def fake_download(client, base_url, output):
        return b"not-a-real-mp4" * 500

    monkeypatch.setattr(video_generator.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(video_generator, "_poll_history", fake_poll)
    monkeypatch.setattr(video_generator, "_download_output", fake_download)

    ok = asyncio.run(
        video_generator._try_generate_clip(
            app.state.settings,
            {"1": {"class_type": "noop", "inputs": {}}},
            {"duration_s": 2, "visual_prompt": "bad"},
            clip_path,
            1,
            1,
            "ltx",
        )
    )

    assert ok is False
    assert not clip_path.exists()
    error_path = clip_path.with_suffix(".ltx_errors.json")
    assert error_path.exists()
    error_payload = json.loads(error_path.read_text(encoding="utf-8"))
    assert error_payload["reason"] == "invalid_or_unprobeable_video"
    assert error_payload["fallback"] == "motion_graphic"


def test_video_generator_recovers_late_comfyui_output_after_http_timeout(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.comfyui_video_output_grace_s = 5
    clip_path = tmp_path / "late.mp4"
    copy_attempts: list[int] = []

    class TimeoutClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, *args, **kwargs):
            raise video_generator.httpx.ReadTimeout("late comfyui output")

    def fake_copy(settings, path, *, backend: str, segment: int, min_mtime: float) -> bool:
        copy_attempts.append(segment)
        if len(copy_attempts) < 2:
            return False
        path.write_bytes(b"late-real-comfyui-output")
        return True

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(video_generator.httpx, "AsyncClient", TimeoutClient)
    monkeypatch.setattr(video_generator, "_copy_recent_comfyui_output", fake_copy)
    monkeypatch.setattr(video_generator.asyncio, "sleep", fake_sleep)

    ok = asyncio.run(
        video_generator._try_generate_clip(
            app.state.settings,
            {"1": {"class_type": "noop", "inputs": {}}},
            {"duration_s": 2, "visual_prompt": "late"},
            clip_path,
            1,
            1,
            "ltx",
        )
    )

    assert ok is True
    assert copy_attempts == [1, 1]
    assert clip_path.exists()
    assert not clip_path.with_suffix(".ltx_errors.json").exists()


def test_video_generator_replaces_motion_fallback_with_late_comfyui_output(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    clip_path = tmp_path / "s02.mp4"
    clip_path.write_bytes(b"motion-fallback")
    error_path = clip_path.with_suffix(".ltx_errors.json")
    error_path.write_text(json.dumps({"reason": "ReadTimeout"}), encoding="utf-8")
    clips = [
        {
            "segment": 2,
            "status": "generated",
            "backend": "motion_graphic",
            "target_backend": "ltx",
            "fallback_from_backend": "ltx",
            "file": str(clip_path),
        }
    ]

    def fake_copy(settings, path, *, backend: str, segment: int, min_mtime: float) -> bool:
        assert backend == "ltx"
        assert segment == 2
        assert min_mtime > 0
        path.write_bytes(b"late-ltx")
        return True

    monkeypatch.setattr(video_generator, "_copy_recent_comfyui_output", fake_copy)

    video_generator._replace_fallback_clips_with_recent_comfyui_outputs(app.state.settings, clips)

    assert clips[0]["backend"] == "ltx"
    assert clips[0]["status"] == "generated"
    assert clips[0]["fallback_from_backend"] is None
    assert clips[0]["late_comfyui_output_recovered"] is True
    assert clip_path.read_bytes() == b"late-ltx"


def test_video_generator_recovers_recent_comfyui_output_when_history_is_empty(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    install_dir = tmp_path / "ComfyUI_windows_portable"
    app.state.settings.comfyui_install_dir = install_dir
    output_dir = install_dir / "ComfyUI" / "output"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "atnos_ltx_01_00001_.mp4"
    output_file.write_bytes(b"real-comfyui-video" * 500)
    clip_path = tmp_path / "clip.mp4"

    monkeypatch.setattr(video_generator, "_is_probeable_video", lambda path: Path(path).exists())

    recovered = video_generator._copy_recent_comfyui_output(
        app.state.settings,
        clip_path,
        backend="ltx",
        segment=1,
        min_mtime=0,
    )

    assert recovered is True
    assert clip_path.read_bytes() == output_file.read_bytes()


def test_video_generator_retries_premium_backend_before_fallback(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.comfyui_video_retry_attempts = 2
    clip_path = tmp_path / "retry-success.mp4"
    attempts: list[int] = []

    async def fake_try(settings, workflow, scene, path, segment, total, backend):
        attempts.append(segment)
        if len(attempts) == 1:
            return False
        path.write_bytes(b"valid-video-after-retry")
        return True

    async def fake_recover(settings, *, backend: str, attempt: int) -> None:
        return None

    monkeypatch.setattr(video_generator, "_try_generate_clip", fake_try)
    monkeypatch.setattr(video_generator, "_recover_comfyui_between_attempts", fake_recover)
    monkeypatch.setattr(video_generator, "_is_probeable_video", lambda path: Path(path).exists())

    ok = asyncio.run(
        video_generator._try_generate_clip_with_retries(
            app.state.settings,
            {"1": {"class_type": "noop", "inputs": {}}},
            {"duration_s": 2, "visual_prompt": "retry"},
            clip_path,
            1,
            1,
            "ltx",
        )
    )

    assert ok is True
    assert attempts == [1, 1]
    assert clip_path.exists()


def test_video_generator_stops_after_configured_premium_backend_retries(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.comfyui_video_retry_attempts = 3
    clip_path = tmp_path / "retry-fail.mp4"
    attempts: list[int] = []
    recoveries: list[int] = []

    async def fake_try(settings, workflow, scene, path, segment, total, backend):
        attempts.append(segment)
        path.write_bytes(b"invalid-video")
        return False

    async def fake_recover(settings, *, backend: str, attempt: int) -> None:
        recoveries.append(attempt)

    monkeypatch.setattr(video_generator, "_try_generate_clip", fake_try)
    monkeypatch.setattr(video_generator, "_recover_comfyui_between_attempts", fake_recover)
    monkeypatch.setattr(video_generator, "_is_probeable_video", lambda path: False)

    ok = asyncio.run(
        video_generator._try_generate_clip_with_retries(
            app.state.settings,
            {"1": {"class_type": "noop", "inputs": {}}},
            {"duration_s": 2, "visual_prompt": "retry"},
            clip_path,
            1,
            1,
            "wan",
        )
    )

    assert ok is False
    assert attempts == [1, 1, 1]
    assert recoveries == [1, 2]
    assert not clip_path.exists()


def test_video_generator_restarts_comfyui_between_retries_when_health_fails(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[dict[str, object]] = []

    async def fake_health(settings):
        return False

    class FakeRuntime:
        def __init__(self, settings) -> None:
            self.settings = settings

        def restart_server(self, *, wait_timeout_s: int, lowvram: bool) -> bool:
            calls.append({"wait_timeout_s": wait_timeout_s, "lowvram": lowvram})
            return True

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(video_generator, "_comfyui_health", fake_health)
    monkeypatch.setattr(video_generator, "ComfyUiRuntimeService", FakeRuntime)
    monkeypatch.setattr(video_generator.asyncio, "sleep", fake_sleep)

    asyncio.run(video_generator._recover_comfyui_between_attempts(app.state.settings, backend="ltx", attempt=1))

    assert calls == [{"wait_timeout_s": 120, "lowvram": True}]


def test_video_generator_uses_public_quality_minimum_comfyui_timeout(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.comfyui_generation_timeout_s = 180

    assert video_generator._comfyui_generation_timeout_s(app.state.settings) == 1200.0


def test_finance_semantic_overlay_uses_single_scene_anchor_contract() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "headline": "BY FRIDAY",
        "narration": "By Friday, every dollar is assigned without a plan.",
        "visual_prompt": "Show a wallet and three purchases.",
    }

    assert video_generator._scene_semantic_overlay_kind(scene) == "allocation_sorter"
    prompt = video_generator._comfy_scene_image_prompt(scene, segment=7, total=10)
    assert "one stainless-steel sorting tray" in prompt
    assert "stainless-steel sorting tray" in prompt
    assert "several loose silver-metal coins" in prompt
    assert "one coherent illustrated scene" in prompt.casefold()
    assert "no people" in prompt
    assert video_generator._scene_checkpoint_contract(scene)["_image_prompt_recipe"] == "finance-anchor-scene-v10"


def test_finance_semantic_context_requires_generated_scene_evidence_before_overlay() -> None:
    scene = {
        "channel_profile_key": "wealth_blueprint",
        "headline": "BY FRIDAY",
        "narration": "By Friday, every dollar is assigned without a plan.",
        "visual_prompt": "Show a wallet and three purchases.",
    }

    context = video_generator._image_candidate_semantic_context(
        "The One Money Rule Most Beginners Skip",
        scene,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert "Required visible evidence:" in context
    assert "one stainless-steel sorting tray" in context
    assert "stainless-steel sorting tray" in context
    assert "several loose metal coins" in context
    assert "supplemental" in context
    assert "does not replace any required visible evidence" in context
    assert "only the photographic background" not in context
    assert context.index("supplemental") < context.index("Required visible evidence:")


def test_video_model_check_uses_model_manager_auto_download_pipelines(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[str] = []

    class FakeManager:
        async def ensure_pipeline_models(self, pipeline: str):
            calls.append(pipeline)
            return {"model": True}

    monkeypatch.setattr(video_generator.ModelManager, "get_instance", lambda settings: FakeManager())

    ltx_ok, ltx_message = asyncio.run(video_generator._ensure_video_models(app.state.settings, "ltx"))
    wan_ok, wan_message = asyncio.run(video_generator._ensure_video_models(app.state.settings, "wan"))

    assert ltx_ok is True
    assert wan_ok is True
    assert calls == ["shorts_ltx", "shorts_wan"]
    assert "true" in ltx_message.lower()
    assert "true" in wan_message.lower()


def test_video_generator_uses_shared_comfyui_runtime_service(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[dict[str, object]] = []

    class FakeRuntime:
        def __init__(self, settings) -> None:
            self.settings = settings

        def ensure_server_running(self, *, wait_timeout_s: int, lowvram: bool) -> bool:
            calls.append({"wait_timeout_s": wait_timeout_s, "lowvram": lowvram})
            return True

    monkeypatch.setattr(video_generator, "ComfyUiRuntimeService", FakeRuntime)

    assert asyncio.run(video_generator._ensure_comfyui(app.state.settings)) is True
    assert calls == [{"wait_timeout_s": 120, "lowvram": True}]


def test_thumbnail_generator_writes_comfyui_template_and_fallback_png(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    result = asyncio.run(thumbnail_generator.run(app.state.settings, context))

    thumbnail_path = Path(result.payload["thumbnail_path"])
    workflow_path = Path(result.payload["workflow_template"])
    assert result.payload["status"] == "generated"
    assert thumbnail_path.exists()
    assert thumbnail_path.stat().st_size > 50 * 1024
    assert result.payload["local_generated"] is True
    assert result.payload["fallback_used"] is False
    assert result.payload["thumbnail_generation_backend"] == "local_pillow_thumbnail"
    assert result.payload["review_ready"] is False
    assert result.payload["thumbnail_quality"]["status"] == "pass"
    assert result.payload["thumbnail_quality"]["metrics"]["width"] == 1280
    assert result.payload["thumbnail_quality"]["metrics"]["height"] == 720
    assert workflow_path.exists()
    assert json.loads(workflow_path.read_text(encoding="utf-8"))["7"]["class_type"] == "SaveImage"


def test_video_timeline_expands_with_rotating_cycles(tmp_path: Path, monkeypatch) -> None:
    clips = [tmp_path / f"s{index:02d}.mp4" for index in range(1, 5)]
    for clip in clips:
        clip.write_bytes(b"clip")
    monkeypatch.setattr(video_generator, "_probe_media_duration", lambda _path: 1.0)

    timeline = video_generator._expand_clip_timeline(clips, target_duration_s=12.0)

    assert len(timeline) >= 13
    assert timeline[:4] == clips
    assert timeline[4:8] != clips
    assert all(left != right for left, right in zip(timeline, timeline[1:]))


def test_video_timeline_keeps_voice_aligned_single_pass_when_long_enough(tmp_path: Path, monkeypatch) -> None:
    clips = [tmp_path / f"s{index:02d}.mp4" for index in range(1, 12)]
    for clip in clips:
        clip.write_bytes(b"clip")
    monkeypatch.setattr(video_generator, "_probe_media_duration", lambda _path: 5.0)

    timeline = video_generator._expand_clip_timeline(clips, target_duration_s=52.0)

    assert timeline == clips


def test_scene_durations_align_to_voice_segments() -> None:
    scenes = [{"scene_id": index, "generation_duration_s": 1.35} for index in range(3)]
    voice = {
        "timestamps": [
            {"segment_id": "s01", "duration_sec": 2.6},
            {"segment_id": "s02", "duration_sec": 4.3},
            {"segment_id": "s03", "duration_sec": 5.2},
        ]
    }

    aligned = video_generator._align_scene_durations_to_voice(scenes, voice)

    assert [scene["generation_duration_s"] for scene in aligned] == [2.6, 4.3, 5.2]
    assert [scene["voice_segment_id"] for scene in aligned] == ["s01", "s02", "s03"]


def test_thumbnail_generator_wealth_blueprint_prompt_uses_adult_fintech_contract(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="prod-thumbnail-wealth",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth-thumb",
            account_id="yt-wealth-blueprint",
            title="How Money Behaves After You Earn It",
            description="English personal finance",
            total_score=88,
        ),
        artifact_dir=tmp_path / "job",
        artifacts={"hook": {"selected": {"text": "What Happens After Payday", "tension_score": 8}}},
    )

    inputs = thumbnail_generator._prompt_inputs(context)
    prompt = inputs["positive_prompt"].casefold()

    assert "hand-drawn mature editorial illustration" in prompt
    assert "navy and paper cream" in prompt
    assert "muted warm gold" in prompt
    assert "emerald neon" not in prompt
    assert "photorealistic corporate stock" in inputs["negative_prompt"].casefold()


def test_thumbnail_punch_text_is_complete_and_topic_specific() -> None:
    assert (
        thumbnail_generator._thumbnail_punch_text(
            "The Dancing Plague of 1518",
            "dark_chronicles",
            fallback="Dancing until exhaustion, then collapse",
        )
        == "THE DANCING PLAGUE"
    )
    assert (
        thumbnail_generator._thumbnail_punch_text(
            "The Money Habit Keeping You Broke",
            "wealth_blueprint",
        )
        == "THE HABIT KEEPING YOU BROKE"
    )
    assert (
        thumbnail_generator._thumbnail_punch_text(
            "The One Money Rule Most Beginners Skip",
            "wealth_blueprint",
        )
        == "ONE MONEY RULE"
    )
    assert (
        thumbnail_generator._thumbnail_punch_text(
            "Why Your Savings Account Is Lying to You",
            "wealth_blueprint",
        )
        == "YOUR SAVINGS LIE"
    )
    assert (
        thumbnail_generator._thumbnail_punch_text(
            "Why Compound Interest Rewards Time More Than Hype",
            "wealth_blueprint",
        )
        == "TIME BEATS HYPE"
    )


def test_thumbnail_reframes_verified_scene_from_current_job(tmp_path: Path) -> None:
    source = tmp_path / "scene.png"
    output = tmp_path / "thumbnail.png"
    source_image = Image.effect_noise((512, 896), 42.0).convert("RGB")
    source_image.paste((184, 128, 74), (0, 448, 512, 896))
    source_image.save(source)
    context = _context(tmp_path / "job")
    context.artifacts["video"] = {
        "clips": [
            {
                "segment": 1,
                "status": "generated",
                "file": str(tmp_path / "s01.mp4"),
                "generated_image_paths": [str(source)],
            }
        ]
    }
    prompt_inputs: dict[str, object] = {}

    generated = thumbnail_generator._try_prepare_thumbnail_from_scene(context, output, prompt_inputs)

    assert generated is True
    assert prompt_inputs["scene_source_path"] == str(source)
    with Image.open(output) as image:
        assert image.size == (1280, 720)


def test_thumbnail_generator_uses_model_manager_for_sdxl_checkpoint(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    class FakeManager:
        async def ensure_model(self, model_id: str, *, auto_download: bool = True):
            assert model_id == "sdxl_base_vae"
            assert auto_download is True
            return True, "F:/models/custom_sdxl.safetensors"

    async def fake_generate(settings, workflow, prompt_inputs, output_path):
        output_path.write_bytes(b"x" * 5000)
        return True

    monkeypatch.setattr(thumbnail_generator.ModelManager, "get_instance", lambda settings: FakeManager())
    monkeypatch.setattr(thumbnail_generator, "_try_generate_thumbnail", fake_generate)

    result = asyncio.run(thumbnail_generator.run(app.state.settings, context))

    assert result.payload["status"] == "generated"
    assert result.payload["thumbnail_model"]["ready"] is True
    assert result.payload["thumbnail_model"]["checkpoint"] == "custom_sdxl.safetensors"
    assert result.payload["prompt_inputs"]["checkpoint"] == "custom_sdxl.safetensors"


def test_thumbnail_generator_uses_shared_comfyui_runtime_service(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    calls: list[dict[str, object]] = []

    class FakeRuntime:
        def __init__(self, settings) -> None:
            self.settings = settings

        def ensure_server_running(self, *, wait_timeout_s: int, lowvram: bool) -> bool:
            calls.append({"wait_timeout_s": wait_timeout_s, "lowvram": lowvram})
            return False

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("ComfyUI API should not be called when runtime startup fails.")

    monkeypatch.setattr(thumbnail_generator, "ComfyUiRuntimeService", FakeRuntime)
    monkeypatch.setattr(thumbnail_generator.httpx, "AsyncClient", ForbiddenClient)

    ok = asyncio.run(
        thumbnail_generator._try_generate_thumbnail(
            app.state.settings,
            {"1": {"class_type": "noop", "inputs": {}}},
            {"positive_prompt": "test", "checkpoint": "sd_xl_base_1.0_0.9vae.safetensors"},
            tmp_path / "thumbnail.png",
        )
    )

    assert ok is False
    assert calls == [{"wait_timeout_s": 120, "lowvram": True}]


def test_thumbnail_generator_uses_local_thumbnail_when_sdxl_model_unavailable(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    context = _context(tmp_path / "job")
    context.artifact_dir.mkdir(parents=True)

    class FakeManager:
        async def ensure_model(self, model_id: str, *, auto_download: bool = True):
            return False, "download failed"

    async def forbidden_generate(*_args, **_kwargs):
        raise AssertionError("ComfyUI thumbnail generation should not run without SDXL model.")

    monkeypatch.setattr(thumbnail_generator.ModelManager, "get_instance", lambda settings: FakeManager())
    monkeypatch.setattr(thumbnail_generator, "_try_generate_thumbnail", forbidden_generate)

    result = asyncio.run(thumbnail_generator.run(app.state.settings, context))

    assert result.payload["status"] == "generated"
    assert result.payload["thumbnail_model"]["ready"] is False
    assert result.payload["thumbnail_model"]["message"] == "download failed"
    assert result.payload["local_generated"] is True
    assert result.payload["fallback_used"] is False
    assert result.payload["thumbnail_generation_backend"] == "local_pillow_thumbnail"
    assert result.payload["review_ready"] is False
