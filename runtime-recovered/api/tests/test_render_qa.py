from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from content.render_qa import RenderQaService


def test_render_qa_rejects_fake_mp4_placeholder_even_when_metadata_claims_success() -> None:
    with TemporaryDirectory(prefix="atnos-render-qa-placeholder-") as temp:
        root = Path(temp)
        final_mp4 = root / "final.mp4"
        narration = root / "narration.wav"
        subtitles = root / "subtitles.srt"
        final_mp4.write_bytes(b"fake-mp4")
        narration.write_bytes(b"RIFF" + (b"0" * 128))
        subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")

        result = RenderQaService().build(
            final_mp4=final_mp4,
            render_metadata={"qa": {"duration_sec": 30, "width": 1080, "height": 1920, "has_audio": True}},
            subtitles_path=subtitles,
            audio_path=narration,
        )

        assert result["ok"] is False
        assert any("only 8 bytes" in warning for warning in result["warnings"])


def test_render_qa_warns_when_subtitles_do_not_match_narration() -> None:
    with TemporaryDirectory(prefix="atnos-render-qa-") as temp:
        root = Path(temp)
        final_mp4 = root / "final.mp4"
        audio = root / "narration.wav"
        subtitles = root / "subtitles.srt"
        final_mp4.write_bytes(b"mp4" * 20000)
        audio.write_bytes(b"wav" * 20000)
        subtitles.write_text(
            "\n".join(
                (
                    "1",
                    "00:00:00,000 --> 00:00:02,000",
                    "Lokana i trybiofliny penerowanie raportow.",
                    "",
                )
            ),
            encoding="utf-8",
        )

        qa = RenderQaService().build(
            final_mp4=final_mp4,
            render_metadata={"qa": {"warnings": []}},
            subtitles_path=subtitles,
            audio_path=audio,
            expected_text=(
                "Lokalna AI na laptopie pomaga pracować offline, generować raporty i chronić prywatność danych."
            ),
        )

        assert qa["ok"] is False
        assert any("low text overlap" in warning for warning in qa["warnings"])
        assert any("suspicious transcription tokens" in warning for warning in qa["warnings"])


def test_render_qa_accepts_subtitles_with_good_overlap() -> None:
    with TemporaryDirectory(prefix="atnos-render-qa-") as temp:
        root = Path(temp)
        final_mp4 = root / "final.mp4"
        audio = root / "narration.wav"
        subtitles = root / "subtitles.srt"
        final_mp4.write_bytes(b"mp4" * 20000)
        audio.write_bytes(b"wav" * 20000)
        narration = "Lokalna AI na laptopie pomaga pracować offline i chronić prywatność danych."
        subtitles.write_text(
            "\n".join(
                (
                    "1",
                    "00:00:00,000 --> 00:00:02,000",
                    narration,
                    "",
                )
            ),
            encoding="utf-8",
        )

        qa = RenderQaService().build(
            final_mp4=final_mp4,
            render_metadata={"qa": {"warnings": []}},
            subtitles_path=subtitles,
            audio_path=audio,
            expected_text=narration,
        )

        assert qa["ok"] is True
        assert qa["warnings"] == []


def test_render_qa_marks_vertical_1080p_short_as_youtube_ready() -> None:
    with TemporaryDirectory(prefix="atnos-render-qa-") as temp:
        root = Path(temp)
        final_mp4 = root / "final.mp4"
        audio = root / "narration.wav"
        subtitles = root / "subtitles.srt"
        final_mp4.write_bytes(b"mp4" * 20000)
        audio.write_bytes(b"wav" * 20000)
        narration = "Lokalna AI pomaga szybko przygotowac pionowy material na YouTube."
        subtitles.write_text(
            "\n".join(("1", "00:00:00,000 --> 00:00:02,000", narration, "")),
            encoding="utf-8",
        )

        qa = RenderQaService().build(
            final_mp4=final_mp4,
            render_metadata={"qa": {"warnings": [], "duration_sec": 35.0, "width": 1080, "height": 1920, "has_audio": True}},
            subtitles_path=subtitles,
            audio_path=audio,
            expected_text=narration,
        )

        assert qa["ok"] is True
        assert qa["youtube_shorts_ready"] is True
        assert qa["youtube_short_checks"]["preferred_resolution_1080x1920"] is True


def test_render_qa_warns_when_short_is_not_vertical() -> None:
    with TemporaryDirectory(prefix="atnos-render-qa-") as temp:
        root = Path(temp)
        final_mp4 = root / "final.mp4"
        audio = root / "narration.wav"
        subtitles = root / "subtitles.srt"
        final_mp4.write_bytes(b"mp4" * 20000)
        audio.write_bytes(b"wav" * 20000)
        narration = "Lokalna AI pomaga szybko przygotowac pionowy material na YouTube."
        subtitles.write_text(
            "\n".join(("1", "00:00:00,000 --> 00:00:02,000", narration, "")),
            encoding="utf-8",
        )

        qa = RenderQaService().build(
            final_mp4=final_mp4,
            render_metadata={"qa": {"warnings": [], "duration_sec": 35.0, "width": 1920, "height": 1080, "has_audio": True}},
            subtitles_path=subtitles,
            audio_path=audio,
            expected_text=narration,
        )

        assert qa["ok"] is False
        assert qa["youtube_shorts_ready"] is False
        assert any("vertical or square" in warning for warning in qa["warnings"])
