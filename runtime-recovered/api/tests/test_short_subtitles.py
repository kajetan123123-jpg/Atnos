from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import wave

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from content.subtitles import ShortSubtitleService

from test_support import build_test_settings


@dataclass(frozen=True, slots=True)
class FakeSubtitleDescriptor:
    absolute_path: Path


class FakeWhisperService:
    def __init__(self, srt_text: str | None = None, error: Exception | None = None) -> None:
        self.srt_text = srt_text
        self.error = error

    def generate(self, *, audio_path: Path, conversation_id: int | None) -> FakeSubtitleDescriptor:
        if self.error is not None:
            raise self.error
        assert self.srt_text is not None
        path = audio_path.parent / "whisper-output.srt"
        path.write_text(self.srt_text, encoding="utf-8")
        return FakeSubtitleDescriptor(absolute_path=path)


def test_short_subtitles_replace_bad_whisper_transcript_with_script_aligned_srt() -> None:
    with TemporaryDirectory(prefix="atnos-subtitles-") as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        audio = root / "narration.wav"
        _write_silent_wav(audio, duration_sec=8)
        output_dir = root / "out"
        output_dir.mkdir()
        fallback_text = "Lokalna AI na laptopie pomaga pracować offline, generować raporty i chronić prywatność danych."
        bad_srt = "1\n00:00:00,000 --> 00:00:02,000\nLokana trybiofliny penerowanie raportow.\n"

        subtitles = ShortSubtitleService(settings, service=FakeWhisperService(bad_srt)).generate(
            audio_path=audio,
            output_dir=output_dir,
            fallback_text=fallback_text,
        )

        assert subtitles == output_dir / "subtitles.srt"
        assert (output_dir / "subtitles.whisper.srt").exists()
        assert "Lokalna AI na laptopie" in subtitles.read_text(encoding="utf-8")
        quality = (output_dir / "subtitles_quality.json").read_text(encoding="utf-8")
        assert '"source": "script_aligned_fallback"' in quality
        assert "whisper_text_contains_suspicious_tokens" in quality


def test_short_subtitles_keep_good_whisper_transcript() -> None:
    with TemporaryDirectory(prefix="atnos-subtitles-") as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        audio = root / "narration.wav"
        _write_silent_wav(audio, duration_sec=8)
        output_dir = root / "out"
        output_dir.mkdir()
        fallback_text = "Lokalna AI na laptopie pomaga pracować offline i chronić prywatność danych."
        good_srt = (
            "1\n00:00:00,000 --> 00:00:02,500\nLokalna AI na laptopie\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\npomaga pracować offline i\n\n"
            "3\n00:00:05,000 --> 00:00:08,000\nchronić prywatność danych.\n"
        )

        subtitles = ShortSubtitleService(settings, service=FakeWhisperService(good_srt)).generate(
            audio_path=audio,
            output_dir=output_dir,
            fallback_text=fallback_text,
        )

        srt_text = subtitles.read_text(encoding="utf-8")
        assert "Lokalna AI na laptopie" in srt_text
        assert "chronić prywatność danych" in srt_text
        assert not (output_dir / "subtitles.whisper.srt").exists()
        quality = (output_dir / "subtitles_quality.json").read_text(encoding="utf-8")
        assert '"source": "whisper_cpp"' in quality
        assert "whisper_text_overlap_accepted" in quality


def test_short_subtitles_use_script_aligned_srt_when_whisper_fails() -> None:
    with TemporaryDirectory(prefix="atnos-subtitles-") as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        audio = root / "narration.wav"
        _write_silent_wav(audio, duration_sec=8)
        output_dir = root / "out"
        output_dir.mkdir()
        fallback_text = "Lokalna AI działa lokalnie i nie wymaga wysyłania danych do chmury."

        subtitles = ShortSubtitleService(settings, service=FakeWhisperService(error=RuntimeError("boom"))).generate(
            audio_path=audio,
            output_dir=output_dir,
            fallback_text=fallback_text,
        )

        assert fallback_text.split(" i ")[0] in subtitles.read_text(encoding="utf-8")
        quality = (output_dir / "subtitles_quality.json").read_text(encoding="utf-8")
        assert '"source": "script_aligned_runtime_error"' in quality
        assert "RuntimeError: boom" in quality


def _write_silent_wav(path: Path, *, duration_sec: int) -> None:
    frame_rate = 16_000
    frames = b"\x00\x00" * frame_rate * duration_sec
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(frame_rate)
        handle.writeframes(frames)
