from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.voice_synthesis_service import VoiceSynthesisService


class KokoroLocalRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_resolve_assets_uses_default_model_bundle_paths(self) -> None:
        model_root = self.settings.data_dir / "models" / "kokoro"
        model_root.mkdir(parents=True, exist_ok=True)
        (model_root / "model.onnx").write_bytes(b"model")
        (model_root / "voices-v1.0.bin").write_bytes(b"voices")
        service = KokoroLocalRuntimeService(self.settings)

        with patch("atnos.services.kokoro_local_runtime_service.importlib.util.find_spec") as mocked_find_spec:
            mocked_find_spec.side_effect = lambda name: object() if name == "kokoro_onnx" else None
            assets = service.resolve_assets()

        self.assertIsNotNone(assets)
        self.assertEqual(assets.model_path, (model_root / "model.onnx").resolve())
        self.assertEqual(assets.voices_path, (model_root / "voices-v1.0.bin").resolve())
        self.assertEqual(assets.preferred_voice, "af_heart")


class VoiceSynthesisServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_synthesize_prefers_kokoro_local_when_runtime_is_available(self) -> None:
        service = VoiceSynthesisService(self.settings)

        def _fake_kokoro(*, text: str, output_path: Path, voice_name: str | None, lang: str, speed: float) -> dict[str, object]:
            output_path.write_bytes(b"0" * 4096)
            return {"voice_name": voice_name or "af_heart", "sample_rate": 24000}

        with patch.object(service.kokoro, "synthesize_to_wav", side_effect=_fake_kokoro) as mocked_kokoro:
            with patch.object(service, "_try_edge_tts", return_value=False) as mocked_edge:
                result = service.synthesize(
                    text="This is a test voice reply.",
                    conversation_id=77,
                    voice_name=self.settings.kokoro_voice_name,
                )

        self.assertEqual(result.media_type, "audio/wav")
        self.assertEqual(result.filename, "atnos-narration.wav")
        self.assertTrue(result.absolute_path.exists())
        mocked_kokoro.assert_called_once()
        mocked_edge.assert_not_called()

    def test_synthesize_prefers_edge_for_polish_when_available(self) -> None:
        service = VoiceSynthesisService(self.settings)

        def _fake_edge(**kwargs) -> bool:
            output_path = kwargs["output_path"]
            output_path.write_bytes(b"0" * 4096)
            return True

        with patch.object(service, "_try_edge_tts", side_effect=_fake_edge) as mocked_edge:
            with patch.object(service, "_try_kokoro_local", return_value=False) as mocked_kokoro:
                result = service.synthesize(text="Sprawdz trase i pogode na wyjazd.", conversation_id=88)

        self.assertEqual(result.media_type, "audio/wav")
        mocked_edge.assert_called_once()
        mocked_kokoro.assert_not_called()

    def test_synthesize_uses_creator_voice_alias_for_edge_rendering(self) -> None:
        service = VoiceSynthesisService(self.settings)

        def _fake_edge(**kwargs) -> bool:
            output_path = kwargs["output_path"]
            output_path.write_bytes(b"0" * 4096)
            self.assertEqual(kwargs["preferred_voice"], "pl_maja")
            return True

        with patch.object(service, "_try_edge_tts", side_effect=_fake_edge) as mocked_edge:
            with patch.object(service, "_try_kokoro_local", return_value=False) as mocked_kokoro:
                result = service.synthesize(
                    text="To ma brzmiec bardziej ludzko i spokojnie.",
                    conversation_id=99,
                    voice_name="pl_maja",
                )

        self.assertEqual(result.media_type, "audio/wav")
        mocked_edge.assert_called_once()
        mocked_kokoro.assert_not_called()

    def test_okropek_is_default_human_polish_persona(self) -> None:
        self.assertEqual(VoiceSynthesisService.DEFAULT_POLISH_VOICE, "okropek")
        self.assertEqual(
            VoiceSynthesisService.resolve_persona_voice(wake_word="Okropek"),
            "okropek",
        )
        self.assertEqual(
            VoiceSynthesisService.resolve_persona_voice(wake_word="hej okropku"),
            "okropek",
        )
        profile = VoiceSynthesisService.VOICE_RENDER_PROFILES["okropek"]
        self.assertEqual(profile.edge_voice, "pl-PL-ZofiaNeural")
        self.assertEqual(profile.edge_rate, "-6%")

    def test_prepare_tts_text_softens_markdown_and_abbreviations(self) -> None:
        spoken = VoiceSynthesisService._prepare_tts_text(
            "OK, **zrób** to `szybko` — np. sprawdź https://example.com"
        )
        self.assertNotIn("**", spoken)
        self.assertNotIn("`", spoken)
        self.assertNotIn("https://", spoken)
        self.assertIn("okej", spoken.casefold())
        self.assertIn("na przykład", spoken.casefold())

    def test_runtime_availability_reports_edge_tts_without_kokoro(self) -> None:
        service = VoiceSynthesisService(self.settings)

        with patch.object(service.kokoro, "resolve_assets", return_value=None):
            with patch("atnos.services.voice_synthesis_service.importlib.util.find_spec", return_value=object()):
                with patch("atnos.services.voice_synthesis_service.shutil.which", side_effect=lambda _: None):
                    snapshot = service.runtime_availability()

        self.assertTrue(snapshot["available"])
        self.assertTrue(snapshot["edge_ready"])
        self.assertEqual(snapshot["preferred_voice"], VoiceSynthesisService.DEFAULT_POLISH_VOICE)


if __name__ == "__main__":
    unittest.main()
