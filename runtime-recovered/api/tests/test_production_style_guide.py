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

from atnos.production.channel_profile import DARK_CHRONICLES_PROFILE, WEALTH_BLUEPRINT_PROFILE
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import hook_generator, script_writer, video_generator, visual_planner
from test_support import build_test_settings


def test_hook_generator_injects_obsidian_channel_style_guide(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir(parents=True)
    style_path = settings.obsidian_vault_path / settings.atnos_obsidian_shorts_dir / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text(
        "# Hook Style\n\nUse: tension without fake earnings. Avoid: panic clickbait.",
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    async def fake_ollama(settings, prompt, *, timeout_s=25.0):
        captured["prompt"] = prompt
        return {
            "variants": [
                {
                    "id": "h01",
                    "variant": "question",
                    "text": "Czy tracisz pieniadze codziennie?",
                    "opening_line": "Jedna liczba pokaże ci problem.",
                    "tension_score": 82,
                    "reason": "High tension without fake earnings.",
                },
                {
                    "id": "h02",
                    "variant": "contrast",
                    "text": "Bogaci licza to inaczej",
                    "opening_line": "Nie chodzi o panike, tylko o filtr decyzji.",
                    "tension_score": 78,
                    "reason": "Contrast with safe framing.",
                },
                {
                    "id": "h03",
                    "variant": "benefit",
                    "text": "Sprawdz to w minute",
                    "opening_line": "Ten prosty test ogranicza chaos w budzecie.",
                    "tension_score": 70,
                    "reason": "Practical and specific.",
                },
            ]
        }

    monkeypatch.setattr(hook_generator, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-hook-style",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Budzet domowy bez chaosu",
            description="Finanse osobiste PL",
            total_score=80,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={},
    )

    result = asyncio.run(hook_generator.run(settings, context))

    assert "CHANNEL HOOK STYLE GUIDE FROM OBSIDIAN" in captured["prompt"]
    assert "tension without fake earnings" in captured["prompt"]
    assert result.payload["style_guide"]["used"] is True
    assert result.payload["style_guide"]["status"] == "obsidian_channel_style"
    assert result.payload["style_guide"]["source"] == str(style_path)


def test_script_writer_injects_obsidian_channel_style_guide(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir(parents=True)
    style_path = settings.obsidian_vault_path / settings.atnos_obsidian_shorts_dir / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text(
        "# Channel Style Guide\n\nUse more: fast hooks, simple money examples.\nAvoid: bank jargon.",
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    async def fake_ollama(settings, prompt, *, timeout_s=35.0):
        captured["prompt"] = prompt
        return {
            "segments": [
                {
                    "id": "s01",
                    "type": "hook",
                    "text": "Czy twoje pieniadze uciekaja codziennie?",
                    "duration_target_sec": 3,
                    "visual_note": "Dynamic receipt close-up.",
                },
                {
                    "id": "s02",
                    "type": "intro",
                    "text": "Jedna prosta liczba pokazuje problem.",
                    "duration_target_sec": 5,
                    "visual_note": "Faceless budget review.",
                },
                {
                    "id": "s03",
                    "type": "body",
                    "text": "Sprawdz roznice miedzy pensja a stalymi kosztami.",
                    "duration_target_sec": 6,
                    "visual_note": "Simple money example.",
                },
                {
                    "id": "s04",
                    "type": "body",
                    "text": "Potem odetnij jeden koszt, ktory nie daje wartosci.",
                    "duration_target_sec": 6,
                    "visual_note": "Cutting expense metaphor.",
                },
                {
                    "id": "s05",
                    "type": "body",
                    "text": "Nie potrzebujesz zargonu, tylko powtarzalnego rytualu.",
                    "duration_target_sec": 6,
                    "visual_note": "No jargon visual.",
                },
                {
                    "id": "s06",
                    "type": "cta",
                    "text": "Zapisz i sprawdz to dzis.",
                    "duration_target_sec": 4,
                    "visual_note": "Clean CTA card.",
                },
            ],
            "total_duration_target_sec": 30,
            "title_options": ["Ten blad zjada pieniadze"],
            "description_draft": "Opis",
            "tags": ["shorts", "finanse"],
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-style",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Budzet domowy bez chaosu",
            description="Finanse osobiste PL",
            total_score=80,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "Czy twoje pieniadze uciekaja codziennie?",
                    "opening_line": "Jedna prosta liczba pokazuje problem.",
                }
            }
        },
    )

    result = asyncio.run(script_writer.run(settings, context))

    assert "CHANNEL STYLE GUIDE FROM OBSIDIAN" in captured["prompt"]
    assert "fast hooks, simple money examples" in captured["prompt"]
    assert result.payload["style_guide"]["used"] is True
    assert result.payload["style_guide"]["status"] == "obsidian_channel_style"
    assert result.payload["style_guide"]["source"] == str(style_path)


def test_script_writer_records_missing_style_guide_without_blocking(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "missing-vault"

    async def fake_ollama(settings, prompt, *, timeout_s=35.0):
        raise RuntimeError("offline")

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-no-style",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Budzet domowy bez chaosu",
            description="Finanse osobiste PL",
            total_score=80,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "Czy twoje pieniadze uciekaja codziennie?",
                    "opening_line": "Jedna prosta liczba pokazuje problem.",
                }
            }
        },
    )

    result = asyncio.run(script_writer.run(settings, context))

    assert result.payload["style_guide"] == {"status": "missing", "source": None, "used": False}
    assert result.artifact_path.exists()


def test_script_writer_forces_first_segment_into_three_second_hook(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)

    async def fake_ollama(settings, prompt, *, timeout_s=35.0):
        return {
            "segments": [
                {
                    "id": "s01",
                    "type": "hook",
                    "text": "Most people skip this rule and blow their money",
                    "duration_target_sec": 5,
                    "visual_note": "Counterintuitive opening visual with cognitive dissonance.",
                },
                {
                    "id": "s02",
                    "type": "intro",
                    "text": "What should you do before investing?",
                    "duration_target_sec": 4,
                    "visual_note": "Faceless dashboard.",
                },
                {"id": "s03", "type": "body", "text": "Find the weekly spending leak.", "duration_target_sec": 6},
                {"id": "s04", "type": "body", "text": "Separate hype from one decision.", "duration_target_sec": 6},
                {"id": "s05", "type": "body", "text": "Repeat the boring rule weekly.", "duration_target_sec": 6},
                {"id": "s06", "type": "cta", "text": "Save this and test it today.", "duration_target_sec": 4},
            ],
            "total_duration_target_sec": 31,
            "title_options": ["The One Money Rule"],
            "description_draft": "Draft",
            "tags": ["shorts", "finance"],
        }

    monkeypatch.setattr(script_writer, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-fast-hook",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="The One Money Rule Most Beginners Skip",
            description="English finance",
            total_score=82,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "Most people skip this rule and blow their money",
                    "opening_line": "What should you do before investing?",
                }
            }
        },
    )

    result = asyncio.run(script_writer.run(settings, context))

    assert result.payload["segments"][0]["text"] == "Most people skip this money rule"
    assert result.payload["segments"][0]["duration_target_sec"] == 3
    assert result.payload["hook"] == "Most people skip this money rule"


def test_visual_planner_injects_obsidian_channel_style_guide(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir(parents=True)
    style_path = settings.obsidian_vault_path / settings.atnos_obsidian_shorts_dir / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text(
        "# Visual Style\n\nDark navy backgrounds, cyan accents, no bank logos, no random luxury cars.",
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        captured["prompt"] = prompt
        return {
            "visual_style": "dark navy finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "STOP STRATA",
                    "visual_prompt": "Faceless budget desk, dark navy background, cyan accent, no logos.",
                    "overlay_text": "STOP STRATA",
                    "risk_notes": "No financial advice.",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-style",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Budzet domowy bez chaosu",
            description="Finanse osobiste PL",
            total_score=80,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "body": ["Sprawdz jeden koszt, ktory ucieka z budzetu."],
                "segments": [],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))

    assert "CHANNEL VISUAL STYLE GUIDE FROM OBSIDIAN" in captured["prompt"]
    assert "Dark navy backgrounds, cyan accents" in captured["prompt"]
    assert result.payload["style_guide"]["used"] is True
    assert result.payload["style_guide"]["status"] == "obsidian_channel_style"
    assert result.payload["style_guide"]["source"] == str(style_path)


def test_visual_planner_supplements_llm_plan_to_minimum_scene_floor(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 3
    settings.shorts_visual_max_generated_scenes = 4

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "FIRST RULE",
                    "visual_prompt": "Clean finance dashboard with emerald accent.",
                    "overlay_text": "FIRST RULE",
                    "risk_notes": "No financial advice.",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-floor",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="The One Money Rule Most Beginners Skip",
            description="English finance",
            total_score=82,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Saving money will not make you rich.",
                        "visual_note": "Faceless person compares cash and index chart.",
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "The real gap starts after you earn it.",
                        "visual_note": "Minimal fintech dashboard with one highlighted decision.",
                    },
                    {
                        "id": "s03",
                        "type": "cta",
                        "text": "Save this rule before your next money decision.",
                        "visual_note": "Clean final takeaway card, no readable generated text.",
                    },
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))

    assert len(result.payload["scenes"]) >= 3
    assert result.payload["scenes"][0]["visual_relevance_score"] >= 0.34
    assert any(term in result.payload["scenes"][0]["visual_prompt"].casefold() for term in ("cash", "coins", "wallet"))
    assert not visual_planner.finance_prompt_has_text_surface(result.payload["scenes"][0]["visual_prompt"])
    assert result.payload["scenes"][1]["visual_topic_anchor_score"] >= 0.5
    assert "cash" in result.payload["scenes"][1]["visual_prompt"].casefold()
    assert not visual_planner.finance_prompt_has_text_surface(result.payload["scenes"][1]["visual_prompt"])
    assert result.payload["scenes"][1]["motion_style"]


def test_visual_planner_strengthens_generic_prompts_with_topic_specific_subjects(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 1

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "EARN MORE",
                    "visual_prompt": "Abstract finance chart background.",
                    "overlay_text": "EARN MORE",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-relevance",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="How Money Behaves After You Earn It",
            description="English finance",
            total_score=85,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "The real gap starts after you earn it.",
                        "visual_note": "Paycheck hits the desk while receipts slide away.",
                    }
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scene = result.payload["scenes"][0]

    assert scene["quality_notes"] == "visual_prompt strengthened for topic relevance"
    assert "cash" in scene["visual_prompt"].casefold()
    assert "coins" in scene["visual_prompt"].casefold()
    assert not visual_planner.finance_prompt_has_text_surface(scene["visual_prompt"])
    assert scene["visual_relevance_score"] >= 0.58
    assert scene["visual_topic_anchor_score"] >= 0.5
    assert "cash" in scene["visual_topic_anchor_matches"]


def test_visual_planner_replaces_generic_visual_note_subject_from_script_text(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 1

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "AFTER EARNING",
                    "visual_subject": "Counterintuitive opening visual with cognitive dissonance, dark fintech UI mood",
                    "visual_prompt": "Counterintuitive opening visual with cognitive dissonance, dark fintech UI mood.",
                    "overlay_text": "AFTER EARNING",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-note-relevance",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="How Money Behaves After You Earn It",
            description="English finance",
            total_score=85,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "The real gap starts after you earn it.",
                        "visual_note": "Counterintuitive opening visual with cognitive dissonance, dark fintech UI mood.",
                    }
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scene = result.payload["scenes"][0]
    prompt = scene["visual_prompt"].casefold()

    assert scene["quality_notes"] == "visual_prompt strengthened for topic relevance"
    assert "counterintuitive opening visual" not in scene["visual_subject"].casefold()
    assert "cash" in prompt
    assert "coins" in prompt
    assert not visual_planner.finance_prompt_has_text_surface(prompt)
    assert scene["visual_relevance_score"] >= 0.58


def test_visual_planner_replaces_childish_blurred_finance_prompt(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 1

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "PAYDAY GAP",
                    "visual_subject": "cute cartoon mascot with a credit card",
                    "visual_prompt": "cute cartoon mascot holding a credit card on a blurred background",
                    "motion_style": "slow camera push",
                    "overlay_text": "PAYDAY GAP",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-childish-finance-visual",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="How Money Behaves After You Earn It",
            description="English finance",
            total_score=85,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "The real gap starts after you earn it.",
                        "visual_note": "Paycheck hits the desk while receipts slide away.",
                    }
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scene = result.payload["scenes"][0]
    prompt = scene["visual_prompt"].casefold()

    assert scene["quality_notes"] == "visual_prompt strengthened for topic relevance"
    assert "mature adult fintech editorial" in prompt
    assert "cash" in prompt
    assert "coins" in prompt
    assert not visual_planner.finance_prompt_has_text_surface(prompt)
    assert "cartoon" not in prompt
    assert "mascot" not in prompt
    assert "blurred background" not in prompt


def test_visual_planner_diversifies_repeated_finance_subjects(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 3

    repeated_subject = "clean index fund chart reflected on a phone screen beside still cash and a simple calendar"

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "ASK FIRST",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "ASK FIRST",
                },
                {
                    "scene_id": 1,
                    "script_lines": [1],
                    "duration_s": 4,
                    "headline": "BEFORE BUYING",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "BEFORE BUYING",
                },
                {
                    "scene_id": 2,
                    "script_lines": [2],
                    "duration_s": 4,
                    "headline": "LONG TERM",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "LONG TERM",
                },
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-diversity",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="The One Money Rule Most Beginners Skip",
            description="English finance",
            total_score=85,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "What is the one rule that stops money from disappearing?",
                        "visual_note": "Money disappearing from a wallet.",
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "Before investing, ask yourself this one question every time.",
                        "visual_note": "Investor decision desk.",
                    },
                    {
                        "id": "s03",
                        "type": "body",
                        "text": "Is this purchase helping long-term, or just a temporary fix?",
                        "visual_note": "Purchase versus growth decision.",
                    },
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    subjects = [scene["visual_subject"] for scene in result.payload["scenes"]]
    subject_keys = [visual_planner._visual_subject_key(visual_planner.resolve_channel_profile(context), subject) for subject in subjects]

    assert len(set(subject_keys)) >= 2
    assert any(scene.get("quality_notes") == "visual_prompt diversified after repeated visual subject key" for scene in result.payload["scenes"])
    assert any(
        any(term in subject.casefold() for term in ("shopping basket", "savings bowl", "hourglass", "floor drain"))
        for subject in subjects
    )
    assert all(not visual_planner.finance_prompt_has_text_surface(subject) for subject in subjects)


def test_visual_planner_reanchors_diversified_finance_scene_to_topic_terms(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 3

    repeated_subject = "clean index fund chart reflected on a phone screen beside still cash and a simple calendar"

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark finance editorial",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "EARN GAP",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "EARN GAP",
                },
                {
                    "scene_id": 1,
                    "script_lines": [1],
                    "duration_s": 4,
                    "headline": "PAYCHECK LEAK",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "PAYCHECK LEAK",
                },
                {
                    "scene_id": 2,
                    "script_lines": [2],
                    "duration_s": 4,
                    "headline": "CASH MOVES",
                    "visual_subject": repeated_subject,
                    "visual_prompt": repeated_subject,
                    "overlay_text": "CASH MOVES",
                },
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-visual-anchor",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-wealth",
            account_id="yt-wealth-blueprint",
            title="The One Money Question Before Every Purchase",
            description="English finance",
            total_score=85,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Saving money will not make you rich.",
                        "visual_note": "Opening contrast.",
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "The real gap starts with how your money behaves after you earn it.",
                        "visual_note": "Clean dashboard.",
                    },
                    {
                        "id": "s03",
                        "type": "body",
                        "text": "Your paycheck disappears because small choices happen before you notice them.",
                        "visual_note": "Show paycheck leak.",
                    },
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    anchor_scores = [scene["visual_topic_anchor_score"] for scene in result.payload["scenes"]]

    assert min(anchor_scores) >= 0.5
    assert all(not visual_planner.finance_prompt_has_text_surface(scene["visual_prompt"]) for scene in result.payload["scenes"])
    assert any("cash" in scene["visual_subject"].casefold() or "coins" in scene["visual_subject"].casefold() for scene in result.payload["scenes"])


def test_visual_repair_rebuilds_finance_storyboard_with_distinct_concepts(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 7
    settings.shorts_visual_max_generated_scenes = 7
    segments = [
        {
            "id": f"s{index:02d}",
            "type": "body",
            "text": text,
            "visual_note": "",
        }
        for index, text in enumerate(
            (
                "Your paycheck arrives before your spending begins.",
                "A tempting purchase reaches the checkout.",
                "Small convenience purchases drain the wallet.",
                "Pause before buying and create spending friction.",
                "Money leaks through repeated delivery habits.",
                "Redirect one purchase into savings.",
                "Allocate the money deliberately for long-term growth.",
            ),
            start=1,
        )
    ]
    context = ProductionContext(
        job_id="job-finance-repair",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-finance-repair",
            account_id="yt-wealth-blueprint",
            title="The One Money Question Before Every Purchase",
            description="English personal finance",
            total_score=88,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {"segments": segments},
            "visual_repair_request": {
                "reason": "qa_visual_quality_failed",
                "blocking_failures": ["Visual variety", "Visual topic relevance"],
            },
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scenes = result.payload["scenes"]
    motifs = {
        video_generator._motion_subject_motif(scene["visual_prompt"], channel_profile=WEALTH_BLUEPRINT_PROFILE)
        for scene in scenes
    }

    assert len(scenes) == 7
    assert len({scene["visual_subject"] for scene in scenes}) == 7
    assert len(motifs) >= 6
    assert all(scene["visual_generation_revision"] == 1 for scene in scenes)
    assert all(scene["visual_topic_anchor_score"] >= 0.5 for scene in scenes)
    assert all(not visual_planner.finance_prompt_has_text_surface(scene["visual_prompt"]) for scene in scenes)


def test_visual_repair_preserves_finance_segment_semantics(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 2
    settings.shorts_visual_max_generated_scenes = 2
    context = ProductionContext(
        job_id="job-finance-semantic-repair",
        account_id="yt-wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-finance-semantic-repair",
            account_id="yt-wealth-blueprint",
            title="The One Money Rule Most Beginners Skip",
            description="English personal finance",
            total_score=88,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Saving money will not make you rich.",
                        "visual_note": "A closed wallet beside one savings bowl.",
                    },
                    {
                        "id": "s02",
                        "type": "body",
                        "text": "The real gap starts with how your money behaves after you earn it.",
                        "visual_note": "An open wallet splitting fresh coins between spending and savings.",
                    },
                ]
            },
            "visual_repair_request": {
                "reason": "qa_visual_quality_failed",
                "blocking_failures": ["Visual topic relevance"],
            },
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    income_scene = result.payload["scenes"][1]
    income_prompt = income_scene["visual_prompt"].casefold()

    assert income_scene["script_lines"] == [1]
    assert any(token in income_prompt for token in ("wallet", "cash", "coins", "income", "payday"))
    assert "premium sneaker" not in income_prompt
    assert "wireless-earbuds" not in income_prompt


def test_finance_scene_seeds_follow_spoken_beats_instead_of_scene_index() -> None:
    cases = (
        (
            "One delivery. One subscription. One tiny upgrade.",
            ("delivery bag", "subscription token", "upgrade box", "checkout conveyor"),
            ("mechanical coin sorter", "floor drain"),
        ),
        (
            "Each feels too small to matter, but together they become a pattern.",
            ("domino chain", "purchase objects", "wallet"),
            ("balance scale", "payday envelope"),
        ),
        (
            "By Friday, the money did not vanish. It was assigned without a plan.",
            ("mixed loose coins", "coin sorter", "allocation trays"),
            ("premium sneaker", "floor drain"),
        ),
        (
            "Move one decision out of autopilot before you think about investing.",
            ("coin redirected", "investment bowl", "shopping basket"),
            ("floor drain", "checkout conveyor"),
        ),
        (
            "The boring pause is what keeps the rest of your paycheck alive.",
            ("payday envelope", "velvet barrier", "analog clock"),
            ("wireless-earbuds", "floor drain"),
        ),
    )

    for index, (scene_text, expected, forbidden) in enumerate(cases):
        subject = visual_planner._safe_finance_visual_seed("Personal finance habits", scene_text, index).casefold()
        assert all(token in subject for token in expected)
        assert all(token not in subject for token in forbidden)
        assert not visual_planner.finance_prompt_has_text_surface(subject)


def test_visual_planner_rebuilds_abstract_dark_history_baptism_scene(tmp_path: Path, monkeypatch) -> None:
    settings = build_test_settings(tmp_path)
    settings.shorts_visual_min_real_clips = 1

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark gothic documentary",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "FAKE LAW",
                    "visual_subject": "pure black background with a flickering candle and a shadowy medieval silhouette",
                    "visual_prompt": "pure black background with a flickering candle and a shadowy medieval silhouette",
                    "overlay_text": "FAKE LAW",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-dark-history-concrete",
        account_id="youtube:account-d6add2f8@example.invalid",
        topic=ApprovedTopic(
            id="topic-history",
            account_id="youtube:account-d6add2f8@example.invalid",
            title="The Medieval Law That Sounds Completely Fake",
            description="Dark Chronicles dark history",
            total_score=86,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "intro",
                        "text": "In parts of Europe, children born on Sundays were denied baptism.",
                        "visual_note": "Pure black background with one candle.",
                    }
                ],
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scene = result.payload["scenes"][0]
    prompt = scene["visual_prompt"].casefold()
    quality = video_generator._quality_plan(
        settings,
        [scene],
        [{"status": "generated", "composition_signature": "dark_chronicles:history:church:baptism"}],
        channel_profile=DARK_CHRONICLES_PROFILE,
    )

    assert scene["quality_notes"] in {
        "visual_prompt strengthened for topic relevance",
        "visual_prompt rebuilt for concrete topic-specific objects",
    }
    assert "baptismal" in prompt
    assert "priest" in prompt
    assert "parchment" in prompt
    assert scene["visual_relevance_score"] >= 0.78
    assert scene["visual_topic_anchor_score"] >= 0.7
    assert quality["passes_scene_concreteness_floor"] is True
    assert quality["passes_visual_relevance_floor"] is True


def test_visual_planner_prefers_scene_aligned_history_prompt_over_contradictory_subject(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = build_test_settings(tmp_path)

    async def fake_ollama(settings, prompt, *, timeout_s=30.0):
        return {
            "visual_style": "dark historical documentary",
            "scenes": [
                {
                    "scene_id": 0,
                    "script_lines": [0],
                    "duration_s": 4,
                    "headline": "SHE COULD NOT STOP",
                    "visual_subject": "empty cobblestone street with scattered shoes and a church silhouette",
                    "visual_prompt": (
                        "A faceless woman in sixteenth-century clothing dances in a narrow Strasbourg street, "
                        "clutching a wooden staff while a candle flickers in a nearby window"
                    ),
                    "overlay_text": "SHE COULD NOT STOP",
                }
            ],
        }

    monkeypatch.setattr(visual_planner, "ask_ollama_json", fake_ollama)
    context = ProductionContext(
        job_id="job-dancing-plague-alignment",
        account_id="youtube:account-d6add2f8@example.invalid",
        topic=ApprovedTopic(
            id="topic-dancing-plague",
            account_id="youtube:account-d6add2f8@example.invalid",
            title="The Dancing Plague of 1518",
            description="Dark Chronicles history",
            total_score=88,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "script": {
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "In July 1518, one woman began dancing in a Strasbourg street.",
                        "visual_note": "A lone woman dancing while worried neighbors watch.",
                    }
                ]
            }
        },
    )

    result = asyncio.run(visual_planner.run(settings, context))
    scene = result.payload["scenes"][0]

    assert "woman" in scene["visual_subject"].casefold()
    assert "dances" in scene["visual_subject"].casefold()
    assert "empty cobblestone street" not in scene["visual_subject"].casefold()
    assert scene["visual_topic_anchor_score"] >= 0.7
    assert scene["visual_relevance_score"] >= 0.58


def test_dark_history_diversification_uses_scene_meaning_not_segment_index() -> None:
    records = visual_planner._dark_history_scene_visual_seed(
        "The Dancing Plague of 1518",
        "Historical accounts disagree about deaths, but not about the panic.",
    ).casefold()
    theory = visual_planner._dark_history_scene_visual_seed(
        "The Dancing Plague of 1518",
        "Modern theories point to extreme stress and mass psychogenic illness.",
    ).casefold()
    unresolved = visual_planner._dark_history_scene_visual_seed(
        "The Dancing Plague of 1518",
        "No explanation fully closes the case five centuries later.",
    ).casefold()

    assert "civic clerk" in records
    assert "blank covers" in records
    assert "distressed dancer" in theory
    assert "city gate at dawn" in unresolved
