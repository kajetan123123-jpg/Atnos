from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.proactive_briefing_service import BriefingMessageResult
from atnos.services.voice_ambient_listener_service import VoiceAmbientListenerService


class VoiceAmbientListenerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_readiness_requires_local_stt_configuration(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            service = VoiceAmbientListenerService(
                app.state.database,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
            )
            readiness = service.readiness()

        self.assertFalse(readiness.available)
        self.assertIn("lokalnego STT", readiness.reason or "")

    def test_simulate_transcript_dispatches_voice_ambient_turn(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            service = VoiceAmbientListenerService(
                app.state.database,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
            )
            with patch("atnos.services.voice_ambient_listener_service.ChatOrchestrationService") as chat_cls:
                chat_cls.return_value.handle_turn.return_value = SimpleNamespace(
                    conversation=SimpleNamespace(id=101, title="Voice turn"),
                    assistant_message=SimpleNamespace(id=202, content="Juz sprawdzam."),
                )
                result = service.simulate_transcript("Sprawdz moje zadania na dzisiaj")

            request = chat_cls.return_value.handle_turn.call_args.args[0]
            self.assertEqual(result["kind"], "chat_turn")
            self.assertEqual(request.content, "Sprawdz moje zadania na dzisiaj")
            self.assertEqual(request.message_metadata_json["source"], "voice_ambient")

    def test_simulate_wake_word_only_dispatches_briefing(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            service = VoiceAmbientListenerService(
                app.state.database,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
            )
            with patch(
                "atnos.services.voice_ambient_listener_service.ProactiveBriefingService.create_briefing_message",
                return_value=BriefingMessageResult(
                    conversation_id=77,
                    conversation_title="Briefing",
                    assistant_message_id=88,
                    content="Poranny briefing",
                    metadata_json={"kind": "briefing_card"},
                ),
            ):
                result = service.simulate_wake_word_only()

        self.assertEqual(result["kind"], "briefing")
        self.assertEqual(result["metadata_json"]["kind"], "briefing_card")

    def test_readiness_allows_local_fallback_without_openwakeword(self) -> None:
        controls = SimpleNamespace(ambient_listening_enabled=True)
        effective = SimpleNamespace(
            voice_stt_engine="faster-whisper-local",
            voice_stt_model_path="F:/models/whisper",
        )

        with patch.object(
            VoiceAmbientListenerService,
            "_inspect_dependencies",
            return_value={"ready": True, "wake_detector_available": False, "reason": None},
        ):
            readiness = VoiceAmbientListenerService.readiness_for(
                controls=controls,
                effective_settings=effective,
            )

        self.assertTrue(readiness.available)
        self.assertIn("fallback", readiness.reason or "")

    def test_extract_command_from_transcript_handles_atnos_aliases(self) -> None:
        aliases = VoiceAmbientListenerService._resolve_wake_aliases("Atnos")

        wake_only = VoiceAmbientListenerService._extract_command_from_transcript(
            "Atnosie",
            wake_aliases=aliases,
            detector_triggered=False,
        )
        command = VoiceAmbientListenerService._extract_command_from_transcript(
            "Hej Atnos, sprawdz status modelu",
            wake_aliases=aliases,
            detector_triggered=False,
        )
        detector_command = VoiceAmbientListenerService._extract_command_from_transcript(
            "sprawdz status modelu",
            wake_aliases=aliases,
            detector_triggered=True,
        )

        self.assertEqual(wake_only, "")
        self.assertEqual(command, "sprawdz status modelu")
        self.assertEqual(detector_command, "sprawdz status modelu")

    def test_resolve_input_device_index_matches_index_and_label(self) -> None:
        recorder = SimpleNamespace(
            get_device_count=lambda: 3,
            get_device_info_by_index=lambda index: [
                {"name": "Glosniki", "maxInputChannels": 0},
                {"name": "USB Studio Mic", "maxInputChannels": 1},
                {"name": "Headset Mic", "maxInputChannels": 1},
            ][index],
        )

        by_index = VoiceAmbientListenerService._resolve_input_device_index(recorder, "2")
        by_label = VoiceAmbientListenerService._resolve_input_device_index(
            recorder,
            "label:studio mic",
        )

        self.assertEqual(by_index, 2)
        self.assertEqual(by_label, 1)
