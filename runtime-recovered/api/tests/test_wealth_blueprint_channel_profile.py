from __future__ import annotations

import asyncio
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.faceless_playbook import build_faceless_playbook
from atnos.intelligence.storage import init_intelligence_db
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import hook_generator, script_writer, thumbnail_generator, upload_package, video_generator, visual_planner
from atnos.production.visual_quality_contract import negative_visual_prompt, thumbnail_quality_contract, visual_quality_contract
from test_support import build_test_settings


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="job-wealth-blueprint",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth-1",
            account_id="yt-wealth-blueprint",
            title="The Rule of 72: Double Your Money Explained",
            description="English personal_finance short about compound interest for Wealth Blueprint.",
            total_score=84,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "Understand this before you invest",
                    "opening_line": "One simple rule can stop a bad money decision before it starts.",
                }
            }
        },
    )


def test_money_habit_fallback_uses_specific_tension_hook() -> None:
    context = ProductionContext(
        job_id="job-hook",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-hook",
            account_id="yt-wealth-blueprint",
            title="The Money Habit Keeping You Broke",
            description="Personal finance behavior short.",
            total_score=80,
        ),
        artifact_dir=Path("artifacts"),
        artifacts={},
    )
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, "The Money Habit Keeping You Broke")

    selected = max(variants, key=lambda item: item["tension_score"])
    assert selected["text"] == "Your paycheck starts disappearing in one hour"
    assert selected["tension_score"] == 92


def test_resolves_wealth_blueprint_profile_for_english_finance_channel(tmp_path: Path) -> None:
    profile = resolve_channel_profile(_context(tmp_path))

    assert profile.key == "wealth_blueprint"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction
    assert "gold" in profile.thumbnail_style or "navy" in profile.thumbnail_style or "hand-drawn" in profile.thumbnail_style
    assert "emerald" not in profile.thumbnail_style
    assert "specific investment advice" in profile.risk_notes
    assert profile.kokoro_speed == 1.18


def test_content_profile_can_force_wealth_blueprint_for_real_youtube_account(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="job-real-account-profile",
        account_id="youtube:account-c5034bf4@example.invalid",
        topic=ApprovedTopic(
            id="topic-real-account",
            account_id="youtube:account-c5034bf4@example.invalid",
            title="The Money Habit Keeping You Broke",
            description="A production topic for the connected YouTube account.",
            total_score=91,
            content_profile=(
                "Wealth Blueprint | English personal finance Shorts | US/global | "
                "dark minimalist fintech style | no investment advice"
            ),
        ),
        artifact_dir=tmp_path / "artifacts",
    )

    profile = resolve_channel_profile(context)

    assert profile.key == "wealth_blueprint"
    assert "English language" in profile.language_instruction


def test_wealth_blueprint_fallback_hooks_are_english(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, "Rule of 72")
    text = " ".join(f"{item['text']} {item['opening_line']}" for item in variants)

    assert "Compound interest rewards time, not hype" in text
    assert "Bogaci" not in text
    assert "pieniadze" not in text.casefold()


def test_wealth_blueprint_rejects_unsourced_money_claims_from_llm_hooks(tmp_path: Path, monkeypatch) -> None:
    async def fake_ollama_json(settings, prompt, *, timeout_s):
        return {
            "variants": [
                {
                    "id": "h01",
                    "variant": "shock",
                    "text": "This habit is costing you thousands every year",
                    "opening_line": "Fix it now before it destroys your future returns.",
                    "tension_score": 95,
                    "reason": "Unsafe invented money claim.",
                },
                {
                    "id": "h02",
                    "variant": "benefit",
                    "text": "Save hundreds with one boring rule",
                    "opening_line": "This looks small, but it can change your wallet.",
                    "tension_score": 90,
                    "reason": "Unsafe invented money claim.",
                },
                {
                    "id": "h03",
                    "variant": "question",
                    "text": "Can 20 percent solve your money stress?",
                    "opening_line": "The number sounds simple, but behavior decides the outcome.",
                    "tension_score": 82,
                    "reason": "Unsafe unsupported percentage claim.",
                },
            ]
        }

    context = _context(tmp_path)
    profile = resolve_channel_profile(context)
    monkeypatch.setattr(hook_generator, "ask_ollama_json", fake_ollama_json)

    payload = asyncio.run(
        hook_generator._llm_or_fallback(
            build_test_settings(tmp_path),
            context,
            "Generate hooks.",
            profile,
        )
    )
    text = " ".join(f"{item['text']} {item['opening_line']}" for item in payload["variants"])

    assert payload["llm_unavailable"] is False
    assert payload["llm_rejected_unsafe_claims"] is True
    assert len(payload["unsafe_claim_markers"]) == 3
    assert "thousands" not in text.casefold()
    assert "hundreds" not in text.casefold()
    assert "20 percent" not in text.casefold()


def test_wealth_blueprint_fallback_script_is_english(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    payload = script_writer._safe_fallback_script(
        context,
        context.artifacts["hook"]["selected"],
        profile,
    )
    text = " ".join(segment["text"] for segment in payload["segments"])

    assert payload["channel_profile"]["key"] == "wealth_blueprint"
    assert payload["language_code"] == "en"
    assert payload["tags"][0] == "shorts"
    assert "Compound interest means earning interest" in text
    assert "Starting earlier gives the same process more periods to repeat" in text
    assert "One delivery. One subscription." not in text
    assert "This is education" not in text
    assert "Save this" in text
    assert "Zapisz" not in text
    assert "pieniadze" not in text.casefold()
    assert script_writer.WEALTH_BLUEPRINT_MIN_SPOKEN_WORDS <= payload["total_words"]
    assert payload["total_words"] <= script_writer.WEALTH_BLUEPRINT_MAX_SPOKEN_WORDS
    assert 45 <= payload["estimated_duration_s"] <= 60


def test_wealth_blueprint_rejects_llm_script_that_pads_duration_instead_of_words(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def fake_ollama(*_args, **_kwargs):
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "How one hundred dollars grows.", "duration_target_sec": 10},
                {"id": "s02", "type": "intro", "text": "Year one adds interest.", "duration_target_sec": 10},
                {"id": "s03", "type": "body", "text": "The next year compounds the larger base.", "duration_target_sec": 10},
                {"id": "s04", "type": "body", "text": "Prior interest now earns interest too.", "duration_target_sec": 10},
                {"id": "s05", "type": "body", "text": "Time gives that process more periods.", "duration_target_sec": 10},
                {"id": "s06", "type": "cta", "text": "Calculate your own scenario.", "duration_target_sec": 10},
            ]
        }

    context = _context(tmp_path)
    context.topic = ApprovedTopic(
        id=context.topic.id,
        account_id=context.topic.account_id,
        title="How Compound Interest Grows Over Time",
        description="English compound-interest mechanism for Wealth Blueprint.",
        total_score=context.topic.total_score,
    )
    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)

    payload = asyncio.run(
        script_writer._llm_or_fallback(
            build_test_settings(tmp_path),
            context,
            "prompt",
            context.artifacts["hook"]["selected"],
            {},
            resolve_channel_profile(context),
        )
    )

    assert payload["claim_safety"]["status"] == "fallback_rewritten_script_quality"
    assert "wealth_blueprint_narration_too_short" in payload["claim_safety"]["script_quality_markers"]
    assert script_writer.WEALTH_BLUEPRINT_MIN_SPOKEN_WORDS <= payload["total_words"]
    assert 45 <= payload["estimated_duration_s"] <= 60


def test_script_duration_estimate_uses_spoken_words_not_declared_scene_time(tmp_path: Path) -> None:
    context = _context(tmp_path)
    segments = [
        {
            "id": f"s{index:02d}",
            "type": "hook" if index == 1 else "body",
            "text": "one two three four five six seven",
            "duration_target_sec": 10,
            "visual_note": "source-backed finance scene",
        }
        for index in range(1, 7)
    ]

    payload = script_writer._finalize_script(context, segments, resolve_channel_profile(context))

    assert payload["total_words"] == 42
    assert payload["estimated_duration_s"] == 21
    assert payload["total_duration_target_sec"] == 21


def test_compound_interest_fallback_hook_is_topic_specific(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, context.topic.title)
    selected = max(variants, key=lambda item: item["tension_score"])

    assert selected["text"] == "Compound interest rewards time, not hype"
    assert "compound" in " ".join(item["text"] for item in variants).casefold()
    assert all("Saving money will not make you rich" != item["text"] for item in variants)


def test_compound_interest_rejects_llm_hooks_for_a_different_money_topic(tmp_path: Path, monkeypatch) -> None:
    async def fake_ollama(*_args, **_kwargs):
        return {
            "variants": [
                {"id": "h01", "variant": "contrast", "text": "Your paycheck disappears fast", "opening_line": "Delivery and subscriptions drain it.", "tension_score": 95},
                {"id": "h02", "variant": "question", "text": "Where did your paycheck go?", "opening_line": "The bank app hides one spending habit.", "tension_score": 91},
                {"id": "h03", "variant": "shock", "text": "This purchase keeps you broke", "opening_line": "A receipt exposes the leak.", "tension_score": 89},
            ]
        }

    context = _context(tmp_path)
    monkeypatch.setattr(hook_generator, "ask_ollama_json", fake_ollama)
    payload = asyncio.run(
        hook_generator._llm_or_fallback(
            build_test_settings(tmp_path),
            context,
            "prompt",
            resolve_channel_profile(context),
        )
    )

    assert payload["llm_rejected_topic_mismatch"] is True
    assert len(payload["topic_mismatch_markers"]) == 3
    assert payload["selected"]["text"] == "Compound interest rewards time, not hype"


def test_compound_interest_fallback_visuals_do_not_reuse_lifestyle_creep(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.topic = ApprovedTopic(
        id="wealth-v1-stability-compound-interest",
        account_id=context.topic.account_id,
        title="Why Compound Interest Rewards Time More Than Hype",
        description="Use only $100 at 5%: $105 after the first year and $110.25 after the second year.",
        total_score=context.topic.total_score,
    )
    payload = script_writer._safe_fallback_script(context, context.artifacts["hook"]["selected"])
    notes = " ".join(segment["visual_note"] for segment in payload["segments"]).casefold()
    narration = [segment["text"] for segment in payload["segments"]]

    assert "calendar" in notes
    assert "calculator" in notes
    assert "delivery bag" not in notes
    assert "subscription" not in notes
    assert any("After the first year" in line for line in narration)
    assert any("In the second year" in line for line in narration)
    assert all("year two" not in line.casefold() for line in narration)
    assert any("No magic and no guarantee." in line for line in narration)
    assert any(line.endswith("actually leave alone.") for line in narration)
    assert all(
        segment["text"].rstrip().endswith((".", "?", "!"))
        for segment in payload["segments"]
        if segment["type"] != "hook"
    )


def test_compound_interest_visual_plan_rejects_lifestyle_creep_from_llm(tmp_path: Path, monkeypatch) -> None:
    async def fake_visual_json(*_args, **_kwargs):
        return {
            "visual_style": "dark finance",
            "scenes": [
                {
                    "scene_id": index,
                    "script_lines": [index],
                    "duration_s": 4,
                    "headline": "MONEY HABIT",
                    "visual_prompt": "delivery parcel and takeaway cup draining an open wallet on a checkout belt",
                    "visual_subject": "delivery parcel, takeaway cup, shopping basket, and empty wallet",
                    "overlay_text": "MONEY HABIT",
                }
                for index in range(10)
            ],
        }

    settings = build_test_settings(tmp_path)
    context = _context(tmp_path)
    context.topic = ApprovedTopic(
        id=context.topic.id,
        account_id=context.topic.account_id,
        title="Why Compound Interest Rewards Time More Than Hype",
        description="Explain compound interest through repeated periods and prior interest.",
        total_score=context.topic.total_score,
    )
    context.artifacts["script"] = script_writer._safe_fallback_script(
        context,
        context.artifacts["hook"]["selected"],
    )
    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_visual_json)

    result = asyncio.run(visual_planner.run(settings, context))
    combined = " ".join(
        f"{scene['visual_subject']} {scene['visual_prompt']}" for scene in result.payload["scenes"]
    ).casefold()

    assert "delivery" not in combined
    assert "takeaway" not in combined
    assert "shopping basket" not in combined
    assert "checkout" not in combined
    assert "budget choice" not in combined
    assert "coin" not in combined
    assert "calculator" in combined
    assert "hourglass" in combined
    assert "timing rail" in combined
    assert "calendar" not in combined
    assert all(scene.get("topic_fidelity_enforced") is True for scene in result.payload["scenes"])
    assert [scene["script_lines"] for scene in result.payload["scenes"]] == [
        [index] for index in range(len(result.payload["scenes"]))
    ]
    assert len({scene["visual_subject"] for scene in result.payload["scenes"]}) == len(result.payload["scenes"])


def test_wealth_blueprint_generation_steps_record_faceless_playbook(tmp_path: Path, monkeypatch) -> None:
    async def fake_hook_json(settings, prompt, *, timeout_s):
        captured["hook_prompt"] = prompt
        return {
            "variants": [
                {
                    "id": "h01",
                    "variant": "contrast",
                    "text": "Saving money will not make you rich",
                    "opening_line": "The real gap starts with behavior, not hype.",
                    "tension_score": 90,
                    "reason": "Counterintuitive finance hook.",
                },
                {
                    "id": "h02",
                    "variant": "benefit",
                    "text": "Understand this before you invest",
                    "opening_line": "A boring rule can stop one bad money decision.",
                    "tension_score": 80,
                    "reason": "Safe educational hook.",
                },
                {
                    "id": "h03",
                    "variant": "question",
                    "text": "Why does your paycheck vanish first?",
                    "opening_line": "The answer is usually smaller than people think.",
                    "tension_score": 82,
                    "reason": "Curiosity hook.",
                },
            ]
        }

    async def fake_script_json(settings, prompt, *, timeout_s):
        captured["script_prompt"] = prompt
        return {
            "segments": [
                {"id": "s01", "type": "hook", "text": "Saving money will not make you rich", "duration_target_sec": 3, "visual_note": "cash and budget app"},
                {"id": "s02", "type": "intro", "text": "The problem starts before investing.", "duration_target_sec": 5, "visual_note": "paycheck desk"},
                {"id": "s03", "type": "body", "text": "First, separate the habit from the hype.", "duration_target_sec": 6, "visual_note": "receipts"},
                {"id": "s04", "type": "body", "text": "Then look at timing and risk.", "duration_target_sec": 6, "visual_note": "calendar chart"},
                {"id": "s05", "type": "body", "text": "A repeatable rule beats a dramatic move.", "duration_target_sec": 6, "visual_note": "simple dashboard"},
                {"id": "s06", "type": "cta", "text": "Save this for one money decision today.", "duration_target_sec": 5, "visual_note": "final desk card"},
            ],
            "title_options": ["Saving Money Is Not Enough"],
            "description_draft": "Educational finance short.",
            "tags": ["shorts", "personal finance"],
        }

    async def fake_visual_json(settings, prompt, *, timeout_s):
        captured["visual_prompt"] = prompt
        return {
            "visual_style": "dark fintech",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "SAVE SMART",
                    "visual_prompt": "faceless person with receipt, cash, phone, budget dashboard",
                    "visual_subject": "faceless person sorting receipts and cash",
                    "overlay_text": "SAVE SMART",
                    "risk_notes": "",
                },
                {
                    "scene_id": 1,
                    "script_lines": [1],
                    "duration_s": 4,
                    "headline": "CHECK RISK",
                    "visual_prompt": "calendar, index chart, phone, cash on premium desk",
                    "visual_subject": "calendar and index chart on phone",
                    "overlay_text": "CHECK RISK",
                    "risk_notes": "",
                },
                {
                    "scene_id": 2,
                    "script_lines": [2],
                    "duration_s": 4,
                    "headline": "REPEAT RULE",
                    "visual_prompt": "budget blocks, wallet, bank app, receipt stack",
                    "visual_subject": "budget blocks beside wallet",
                    "overlay_text": "REPEAT RULE",
                    "risk_notes": "",
                },
            ],
        }

    captured: dict[str, str] = {}
    settings = build_test_settings(tmp_path)
    asyncio.run(init_intelligence_db(settings))
    context = _context(tmp_path)
    context.artifact_dir.mkdir(parents=True, exist_ok=True)
    context.artifacts["faceless_playbook"] = build_faceless_playbook(settings, context)
    monkeypatch.setattr(hook_generator, "ask_ollama_json", fake_hook_json)
    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_script_json)
    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_visual_json)

    hook = asyncio.run(hook_generator.run(settings, context))
    context.artifacts["hook"] = hook.payload
    script = asyncio.run(script_writer.run(settings, context))
    context.artifacts["script"] = script.payload
    visual = asyncio.run(visual_planner.run(settings, context))

    assert "FACELESS CHANNEL PLAYBOOK" in captured["hook_prompt"]
    assert "FACELESS CHANNEL PLAYBOOK" in captured["script_prompt"]
    assert "FACELESS CHANNEL PLAYBOOK" in captured["visual_prompt"]
    assert "Mandatory visual quality contract" in captured["visual_prompt"]
    assert "Mature adult fintech editorial only" in captured["visual_prompt"]
    assert hook.payload["faceless_playbook"]["template_key"] == "science_explainer"
    assert script.payload["faceless_playbook"]["template_key"] == "science_explainer"
    assert visual.payload["faceless_playbook"]["template_key"] == "science_explainer"
    assert visual.payload["visual_quality_contract"]["profile_key"] == "wealth_blueprint"
    assert all(scene["channel_profile_key"] == "wealth_blueprint" for scene in visual.payload["scenes"])


def test_wealth_blueprint_visual_quality_contract_feeds_video_and_thumbnail_prompts(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)
    contract = visual_quality_contract(profile.key)

    scene = {
        "visual_prompt": "faceless adult hands sorting cash, coins, a wallet, blank unbranded card, and color-coded envelopes",
        "motion_style": profile.video_motion,
        "headline": "MONEY LEAK",
        "channel_profile_key": profile.key,
        "visual_quality_contract": contract,
    }
    video_prompt = video_generator._video_prompt(scene, backend="wan")
    injected = video_generator._inject_scene(
        video_generator._load_or_create_workflow(tmp_path / "wan_video_default.json", "wan"),
        scene,
        backend="wan",
    )
    thumb_inputs = thumbnail_generator._prompt_inputs(context)

    assert "Mature adult fintech editorial only" in video_prompt
    assert "Forbidden: childish" in video_prompt
    assert "abstract line charts" in video_prompt
    assert "childish" in injected["3"]["inputs"]["text"]
    assert "abstract chart only" in injected["3"]["inputs"]["text"]
    assert "Mature adult fintech editorial only" in thumb_inputs["positive_prompt"]
    assert "Thumbnail must work at phone size" in thumb_inputs["positive_prompt"]
    assert "childish" in thumb_inputs["negative_prompt"]
    assert "PRIVATE QA RENDER" not in thumb_inputs["positive_prompt"]
    assert "Mature adult fintech editorial only" in thumbnail_quality_contract(profile.key)
    assert "abstract chart only" in negative_visual_prompt(profile.key)


def test_wealth_blueprint_upload_package_marks_youtube_metadata_as_english(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.artifact_dir.mkdir(parents=True, exist_ok=True)
    video = tmp_path / "final.mp4"
    thumbnail = tmp_path / "thumbnail.png"
    subtitles = tmp_path / "subtitles.srt"
    video.write_bytes(b"fake video")
    thumbnail.write_bytes(b"fake thumbnail")
    subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nSave this\n", encoding="utf-8")
    context.artifacts.update(
        {
            "script": script_writer._safe_fallback_script(context, context.artifacts["hook"]["selected"]),
            "qa": {"overall": "passed", "auto_uploadable": True},
            "assembly": {"output_file": str(video), "subtitle_file": str(subtitles)},
            "thumbnail": {"thumbnail_path": str(thumbnail)},
        }
    )

    result = asyncio.run(upload_package.run(build_test_settings(tmp_path), context))

    metadata = result.payload["upload_metadata"]
    assert metadata["language"] == "en"
    assert metadata["audio_language"] == "en"
    assert "personal finance" in metadata["tags"]
