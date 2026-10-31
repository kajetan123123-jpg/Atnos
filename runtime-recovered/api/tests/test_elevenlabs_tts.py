from __future__ import annotations

from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.core.config import Settings
from content.elevenlabs_tts import ElevenLabsNarration, ElevenLabsTtsError
from content.expressive_voice_director import ExpressiveVoiceDirector
from content.script_generator import ShortScript


class _Response:
    content = b"fake-mp3"

    @staticmethod
    def raise_for_status() -> None:
        return None


class ElevenLabsTtsTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        isolated_keys = (
            "SHORTS_TTS_BACKEND",
            "SHORTS_ALLOW_CLOUD_TTS",
            "ELEVENLABS_API_KEY",
            "ELEVENLABS_VOICE_ID",
            "ELEVENLABS_MODEL_ID",
            "ELEVENLABS_LANGUAGE_CODE",
            "ELEVENLABS_OUTPUT_FORMAT",
            "FFMPEG_EXE",
        )
        previous_env = {key: os.environ.get(key) for key in isolated_keys}
        for key in isolated_keys:
            os.environ.pop(key, None)

        def restore_env() -> None:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore_env)

    def test_runtime_availability_requires_explicit_cloud_tts(self) -> None:
        settings = self._settings(allow_cloud=False)

        runtime = ElevenLabsNarration(settings).runtime_availability()

        self.assertFalse(runtime.available)
        self.assertIn("SHORTS_ALLOW_CLOUD_TTS", runtime.reason)

    def test_build_payload_uses_expressive_model_and_voice_settings(self) -> None:
        settings = self._settings()

        service = ElevenLabsNarration(settings)
        payload = service.build_payload("[excited] To jest test.")

        self.assertTrue(service.supports_expressive_tags)
        self.assertEqual(payload["model_id"], "eleven_v3")
        self.assertEqual(payload["language_code"], "en")
        self.assertEqual(payload["voice_settings"]["stability"], 0.62)
        self.assertEqual(payload["voice_settings"]["style"], 0.0)
        self.assertEqual(payload["voice_settings"]["speed"], 1.0)
        self.assertNotIn("api_key", payload)

    def test_generate_calls_api_and_writes_narration_wav(self) -> None:
        settings = self._settings()
        with TemporaryDirectory() as temp:
            output_dir = Path(temp)

            def fake_run(command, **kwargs):
                Path(command[-1]).write_bytes(b"RIFFfake-wave")
                return type("_Completed", (), {"returncode": 0, "stderr": "", "stdout": ""})()

            with patch("content.elevenlabs_tts.requests.post", return_value=_Response()) as post_mock, patch(
                "content.elevenlabs_tts.subprocess.run",
                side_effect=fake_run,
            ):
                wav = ElevenLabsNarration(settings).generate(text="[excited] Startujemy.", output_dir=output_dir)
                self.assertEqual(wav.name, "narration.wav")
                self.assertTrue(wav.exists())

        request_kwargs = post_mock.call_args.kwargs
        self.assertEqual(request_kwargs["headers"]["xi-api-key"], "test-key")
        self.assertEqual(request_kwargs["params"]["output_format"], "mp3_44100_128")
        self.assertEqual(request_kwargs["json"]["model_id"], "eleven_v3")

    def test_non_v3_model_does_not_advertise_expressive_tag_support(self) -> None:
        settings = self._settings(model_id="eleven_multilingual_v2")

        self.assertFalse(ElevenLabsNarration(settings).supports_expressive_tags)

    def test_generate_blocks_without_voice_id(self) -> None:
        settings = self._settings(voice_id="")

        with TemporaryDirectory() as temp:
            with self.assertRaises(ElevenLabsTtsError):
                ElevenLabsNarration(settings).generate(text="Test", output_dir=Path(temp))

    def test_expressive_director_keeps_plain_text_tag_free(self) -> None:
        script = ShortScript(
            title="Test",
            hook="The first two seconds decide everything.",
            body=("Show the problem clearly.", "Then give one concrete example."),
            cta="Save this before the render.",
            voiceover="The first two seconds decide everything. This is not a normal mistake. Save this before the render.",
        )

        plan = ExpressiveVoiceDirector().build(script)

        self.assertNotIn("[", plan.plain_text)
        self.assertIn("[excited]", plan.expressive_text)
        self.assertIn("[serious]", plan.expressive_text)
        self.assertIn("[slow]", plan.expressive_text)

    def _settings(
        self,
        *,
        allow_cloud: bool = True,
        voice_id: str = "voice-123",
        model_id: str = "eleven_v3",
    ) -> Settings:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        env_path = root / ".env"
        env_path.write_text(
            "\n".join(
                (
                    "SHORTS_TTS_BACKEND=elevenlabs",
                    f"SHORTS_ALLOW_CLOUD_TTS={str(allow_cloud).lower()}",
                    "ELEVENLABS_API_KEY=test-key",
                    f"ELEVENLABS_VOICE_ID={voice_id}",
                    f"ELEVENLABS_MODEL_ID={model_id}",
                    "ELEVENLABS_LANGUAGE_CODE=en",
                    "ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128",
                    "FFMPEG_EXE=ffmpeg",
                )
            ),
            encoding="utf-8",
        )
        return Settings(app_root=root, data_dir=root / "data", _env_file=env_path)


if __name__ == "__main__":
    unittest.main()
