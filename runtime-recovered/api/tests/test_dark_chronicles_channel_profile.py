from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.channel_profile import resolve_channel_profile
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.steps import hook_generator, script_writer, video_generator


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="job-dark-chronicles",
        account_id="yt-dark-chronicles",
        topic=ApprovedTopic(
            id="topic-dark-1",
            account_id="yt-dark-chronicles",
            title="The Dancing Plague of 1518",
            description="English dark history short about macabre historical_curiosities and verified sources.",
            total_score=86,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "hook": {
                "selected": {
                    "text": "They danced until they died",
                    "opening_line": "In 1518, a city watched hundreds of people move until their bodies failed.",
                }
            }
        },
    )


def test_resolves_dark_chronicles_before_generic_history(tmp_path: Path) -> None:
    profile = resolve_channel_profile(_context(tmp_path))

    assert profile.key == "dark_chronicles"
    assert profile.language_code == "en"
    assert "English language" in profile.language_instruction
    assert "chiaroscuro" in profile.visual_style
    assert "pseudoscience" in profile.risk_notes


def test_dark_chronicles_fallback_hooks_are_cinematic_english(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, "Dancing Plague")
    text = " ".join(f"{item['text']} {item['opening_line']}" for item in variants)

    assert "They danced until they died" in text
    assert "One dancer became a city panic" in text
    assert "music, heat, and bodies" in text
    assert "Polska" not in text
    assert "Historia" not in text


def test_dark_chronicles_law_topic_hooks_do_not_use_dancing_plague(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="job-dark-law",
        account_id="yt-dark-chronicles",
        topic=ApprovedTopic(
            id="topic-dark-law",
            account_id="yt-dark-chronicles",
            title="The Medieval Law That Sounds Completely Fake",
            description="English dark history short about public punishment, courts and fines.",
            total_score=84,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={},
    )
    profile = resolve_channel_profile(context)

    variants = hook_generator._fallback_variants(context, profile, "Medieval Law")
    text = " ".join(f"{item['text']} {item['opening_line']}" for item in variants)
    selected = max(variants, key=lambda item: item["tension_score"])

    assert selected["text"] == "A trivial law made people suffer"
    assert "court turned shame" in text.casefold()
    assert "danced until they died" not in text.casefold()
    assert "1518" not in text


def test_dark_chronicles_fallback_script_is_source_first(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    payload = script_writer._safe_fallback_script(
        context,
        context.artifacts["hook"]["selected"],
        profile,
    )
    text = " ".join(segment["text"] for segment in payload["segments"])

    assert payload["channel_profile"]["key"] == "dark_chronicles"
    assert payload["language_code"] == "en"
    assert "In July 1518, one woman began dancing in a Strasbourg street" in text
    assert "Historical accounts disagree about deaths, but not about the panic" in text
    assert "No explanation fully closes the case five centuries later" in text
    assert "the court turned the street into a warning" not in text
    assert "Follow for darker history" in text
    assert "History is full of surprises" not in text
    assert "Obserwuj" not in text
    assert "Temat " not in text
    assert "Najpierw" not in text
    assert "exhausted woman dancing uncontrollably" in payload["segments"][0]["visual_note"]
    assert "dozens of townspeople dancing" in payload["segments"][1]["visual_note"]


def test_dark_chronicles_hook_trimmer_does_not_create_why_a_law_phrase(tmp_path: Path) -> None:
    context = _context(tmp_path)
    profile = resolve_channel_profile(context)

    fitted = script_writer._fit_fast_hook(
        "A law that made people suffer for something trivial.",
        profile,
    )

    assert fitted == "A trivial law made people suffer"
    assert not fitted.startswith("Why A ")


def test_dark_chronicles_motion_graphics_use_six_unique_history_layouts(tmp_path: Path) -> None:
    context = ProductionContext(
        job_id="job-dark-law",
        account_id="yt-dark-chronicles",
        topic=ApprovedTopic(
            id="topic-dark-law",
            account_id="yt-dark-chronicles",
            title="The Medieval Law That Sounds Completely Fake",
            description="English dark history short about public punishment, courts and fines.",
            total_score=84,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={},
    )
    profile = resolve_channel_profile(context)

    signatures = [
        video_generator._motion_composition_signature(
            "medieval courtroom sealed parchment law gavel candle clerk witness public square",
            segment=segment,
            total=6,
            channel_profile=profile,
        )
        for segment in range(1, 7)
    ]

    assert len(set(signatures)) == 6
    assert signatures[-2].endswith(":public_square")
    assert signatures[-1].endswith(":witness_shadow")


def test_dark_chronicles_motion_graphics_frame_renders_new_history_layouts(tmp_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    context = ProductionContext(
        job_id="job-dark-law",
        account_id="yt-dark-chronicles",
        topic=ApprovedTopic(
            id="topic-dark-law",
            account_id="yt-dark-chronicles",
            title="The Medieval Law That Sounds Completely Fake",
            description="English dark history short about public punishment, courts and fines.",
            total_score=84,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={},
    )
    profile = resolve_channel_profile(context)

    for segment in (5, 6):
        frame_path = tmp_path / f"history_{segment}.png"
        video_generator._write_motion_graphic_frame(
            frame_path,
            prompt_text="medieval courtroom sealed parchment law gavel candle clerk witness public square",
            segment=segment,
            total=6,
            channel_profile=profile,
            image_cls=Image,
            draw_cls=ImageDraw,
            filter_cls=ImageFilter,
        )

        assert frame_path.exists()
        assert frame_path.stat().st_size > 4096
