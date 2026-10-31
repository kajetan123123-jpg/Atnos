from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.content_campaign_service import ContentCampaignService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeComponent, LocalShortRuntimeSnapshot
from atnos.services.output_artifact_service import OutputArtifactService
from atnos.services.render_service import RenderArtifactQa, RenderArtifactResult

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class ContentCampaignServiceTest(unittest.TestCase):
    def test_broken_structured_output_falls_back_for_layered_jelly_topic(self) -> None:
        raw = (
            "title Warstwy Jelliego Odkrycie Soku, description Hipnotyczny start z warstwistym jelly, "
            "powolny nacisk i reveal. tags jelly, fruitpress, wetreveal, voiceover Ciemna kompozycja z jednym "
            "warstwistym jelly. thumbnail text Jelly Sok, scenes headline Ciemna Kompozycja."
        )

        bundle = ContentCampaignService._parse_script_bundle(
            raw,
            topic="Layered Jelly + Fruit Press Reveal",
        )

        self.assertEqual(bundle["title"], "Warstwy i nacisk")
        self.assertEqual(bundle["thumbnail_text"], "Warstwy i nacisk")
        self.assertLessEqual(len(str(bundle["voiceover"]).split()), 95)
        self.assertEqual(len(bundle["scenes"]), 4)

    def test_render_profile_for_quality_keeps_quality_first_except_fast_turnaround(self) -> None:
        self.assertEqual(ContentCampaignService._render_profile_for_quality("premium"), "quality")
        self.assertEqual(ContentCampaignService._render_profile_for_quality("standard"), "quality")
        self.assertEqual(ContentCampaignService._render_profile_for_quality("fast-turnaround"), "balanced")

    def test_parse_script_bundle_keeps_valid_json_when_scene_fruit_and_action_are_missing(self) -> None:
        raw = """
        {
          "title": "Czemu lokalne AI na AMD ma sens",
          "description": "Lokalna AI na AMD przyspiesza przetwarzanie danych i dziala bez chmury.",
          "tags": ["AMD AI", "lokalna inteligencja"],
          "voiceover": "Kamera zbliza sie do laptopa z zielonym swiatlem. Na ekranie dane przetwarzane przez lokalna AI. Uzytkownik wpisuje zapytanie, a w tle widac linie przetwarzania. Panel pokazuje szybsze dzialanie. Kamera pokazuje AI rozpoznajaca obiekty w czasie rzeczywistym.",
          "thumbnail_text": "AMD AI w akcji",
          "scenes": [
            {
              "headline": "Laptop z AI",
              "caption": "Kamera zbliza sie do laptopa z zielonym swiatlem.",
              "visual": "Laptop z zielonym swiatlem, ekran z animowanymi danymi",
              "fruit": "",
              "action": "Zblizenie kamery do ekranu"
            },
            {
              "headline": "Przetwarzanie danych",
              "caption": "Uzytkownik wpisuje zapytanie i widac linie przetwarzania.",
              "visual": "Uzytkownik z klawiatura, linie w tle",
              "fruit": "",
              "action": "Ruch klawiatury i animacja linii"
            },
            {
              "headline": "Wyniki testu",
              "caption": "Panel pokazuje szybsze przetwarzanie niz konkurencja.",
              "visual": "Wykresy porownujace predkosci",
              "fruit": "",
              "action": "Animacja wykresow"
            },
            {
              "headline": "Rozpoznawanie obiektow",
              "caption": "AI identyfikuje obiekty w czasie rzeczywistym.",
              "visual": "Kamera z animacja rozpoznawania obiektow",
              "fruit": "",
              "action": "Ruch kamery"
            }
          ]
        }
        """

        bundle = ContentCampaignService._parse_script_bundle(
            raw,
            topic="Czemu lokalne AI na AMD ma sens",
        )

        self.assertEqual(bundle["title"], "Czemu lokalne AI na AMD ma sens")
        self.assertEqual(bundle["thumbnail_text"], "AMD AI w akcji")
        self.assertEqual(len(bundle["scenes"]), 4)
        self.assertTrue(all(scene["fruit"] == "apple" for scene in bundle["scenes"]))
        self.assertTrue(all(scene["action"] == "impact" for scene in bundle["scenes"]))
        self.assertLessEqual(len(str(bundle["voiceover"]).split()), 95)

    def test_parse_script_bundle_repairs_labeled_non_json_response(self) -> None:
        raw = (
            "title Lokalne AI na AMD sens?, description Lokalne AI na AMD przyspiesza obliczenia i dziala w czasie rzeczywistym. "
            "tags AMD Ryzen AI, lokalne AI, obliczenia, voiceover Kamera zbliza sie do laptopa z logiem AMD. "
            "Na ekranie widac obliczenia w czasie rzeczywistym. Uzytkownik edytuje zdjecie i od razu widzi wynik. "
            "thumbnail text Szybkosc AI, scenes "
            "headline Laptop AMD, caption Kamera zbliza sie do laptopa z logiem AMD., visual Ekran pokazuje obliczenia, fruit , action Kamera porusza sie wzdluz ramki. "
            "headline Porownanie, caption Dwa komputery pokazuja roznice predkosci., visual Porownanie dwoch komputerow, fruit , action Kamera przesuwa sie miedzy obiektami. "
            "headline Edycja zdjec, caption Uzytkownik edytuje zdjecie lokalnie., visual Ekran z edycja zdjecia, fruit , action Kamera zbliza sie do ekranu. "
            "headline Final, caption Wynik pojawia sie od razu bez chmury., visual Gotowy obraz po obliczeniu, fruit , action Finalny ruch kamery."
        )

        bundle = ContentCampaignService._parse_script_bundle(
            raw,
            topic="Czemu lokalne AI na AMD ma sens",
        )

        self.assertEqual(bundle["title"], "Lokalne AI na AMD sens?")
        self.assertEqual(bundle["thumbnail_text"], "Szybkosc AI")
        self.assertEqual(len(bundle["scenes"]), 4)
        self.assertTrue(all(scene["fruit"] == "apple" for scene in bundle["scenes"]))
        self.assertTrue(all(scene["action"] == "impact" for scene in bundle["scenes"]))
        self.assertNotIn('"title"', bundle["voiceover"])
        self.assertLessEqual(len(str(bundle["voiceover"]).split()), 95)

    def test_parse_script_bundle_repairs_quoted_label_response(self) -> None:
        raw = (
            '{ "title": "Dlaczego lokalne AI na AMD ma sens", '
            '"description": "Lokalne AI na AMD przyspiesza obliczenia i dziala bez chmury.", '
            '"tags": ["AMD Ryzen AI", "lokalne AI", "energia"], '
            '"voiceover": "Kamera zbliza sie do laptopa z logiem AMD. Na ekranie widac obliczenia w czasie rzeczywistym. '
            'Uzytkownik od razu widzi wynik bez wysylki do chmury.", '
            '"thumbnail_text": "AMD AI lokalnie", '
            '"scenes": "headline Laptop AMD, caption Kamera zbliza sie do laptopa z logiem AMD., visual Ekran pokazuje obliczenia, fruit , action Kamera porusza sie wzdluz ramki. '
            'headline Porownanie, caption Dwa komputery pokazuja roznice predkosci., visual Porownanie dwoch komputerow, fruit , action Kamera przesuwa sie miedzy obiektami. '
            'headline Edycja zdjec, caption Uzytkownik edytuje zdjecie lokalnie., visual Ekran z edycja zdjecia, fruit , action Kamera zbliza sie do ekranu. '
            'headline Final, caption Wynik pojawia sie od razu bez chmury., visual Gotowy obraz po obliczeniu, fruit , action Finalny ruch kamery." }'
        )

        bundle = ContentCampaignService._parse_script_bundle(
            raw,
            topic="Czemu lokalne AI na AMD ma sens",
        )

        self.assertEqual(bundle["title"], "Dlaczego lokalne AI na AMD ma sens")
        self.assertEqual(bundle["thumbnail_text"], "AMD AI lokalnie")
        self.assertEqual(len(bundle["scenes"]), 4)
        self.assertTrue(all(scene["fruit"] == "apple" for scene in bundle["scenes"]))
        self.assertTrue(all(scene["action"] == "impact" for scene in bundle["scenes"]))
        self.assertLessEqual(len(str(bundle["voiceover"]).split()), 95)

    def test_local_render_mode_returns_manual_upload_ready(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        app = create_test_app(root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with app.state.database.session_scope() as session:
            service = ContentCampaignService(
                session,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
            )
            artifacts = OutputArtifactService(app.state.settings)
            frame_one = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".png",
                filename="frame-1.png",
                media_type="image/png",
            )
            frame_two = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".png",
                filename="frame-2.png",
                media_type="image/png",
            )
            thumbnail = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".png",
                filename="thumbnail.png",
                media_type="image/png",
            )
            narration = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".wav",
                filename="narration.wav",
                media_type="audio/wav",
            )
            subtitles = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".srt",
                filename="captions.srt",
                media_type="application/x-subrip",
            )
            video = self._managed_artifact(
                artifacts,
                conversation_id=42,
                extension=".mp4",
                filename="final.mp4",
                media_type="video/mp4",
            )
            runtime_snapshot = LocalShortRuntimeSnapshot(
                enabled=True,
                ready=True,
                reason="ready",
                components=(
                    LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "C:/ffmpeg/bin/ffmpeg.exe", "ok"),
                    LocalShortRuntimeComponent("ollama", "Ollama", True, "http://127.0.0.1:11434", "ok"),
                    LocalShortRuntimeComponent("piper", "Piper", True, "C:/bin/piper.exe", "ok"),
                    LocalShortRuntimeComponent("whisper_cpp", "whisper.cpp", True, "C:/bin/whisper-cli.exe", "ok"),
                ),
            )

            service.local_short_runtime.snapshot = Mock(return_value=runtime_snapshot)
            service._generate_research = Mock(
                return_value={
                    "content": "Hook i wizualne wskazowki.",
                    "provider_key": "ollama-local",
                    "model_key": "qwen3:14b",
                    "routing_reason": "local",
                }
            )
            service._generate_script = Mock(
                return_value={
                    "script": {
                        "title": "AMD ma sens",
                        "description": "Opis shorta",
                        "tags": ["amd", "local-ai"],
                        "voiceover": "Lokalne AI na AMD daje sensowny koszt, prywatnosc i pelna kontrole nad pipelineem.",
                        "thumbnail_text": "AMD lokalnie",
                        "hook": "Mocny start",
                        "cta": "Sprawdz sam",
                        "scenes": [
                            {"headline": "Start", "caption": "Kadr 1", "visual": "Kadr 1", "fruit": "apple", "action": "impact"},
                            {"headline": "GPU", "caption": "Kadr 2", "visual": "Kadr 2", "fruit": "orange", "action": "slice"},
                            {"headline": "Koszt", "caption": "Kadr 3", "visual": "Kadr 3", "fruit": "mango", "action": "burst"},
                            {"headline": "Final", "caption": "Kadr 4", "visual": "Kadr 4", "fruit": "kiwi", "action": "crush"},
                        ],
                    },
                    "provider_key": "ollama-local",
                    "model_key": "qwen3:14b",
                    "routing_reason": "local",
                }
            )
            service.voice.synthesize = Mock(return_value=narration)
            service.assets.generate_story_assets = Mock(
                return_value=SimpleNamespace(frames=(frame_one, frame_two), thumbnail=thumbnail)
            )
            service.subtitles.generate = Mock(return_value=subtitles)
            service._render_campaign_with_reflection = Mock(
                return_value=RenderArtifactResult(
                    descriptor=video,
                    qa=RenderArtifactQa(
                        duration_sec=31.5,
                        width=1080,
                        height=1920,
                        has_audio=True,
                        size_bytes=video.size_bytes,
                        warnings=(),
                    ),
                    watchdog_timeout_s=240,
                    render_duration_sec=2.1,
                )
            )
            service._build_metadata = Mock(
                return_value={
                    "title": "AMD ma sens",
                    "description": "Opis shorta",
                    "tags": ["amd", "local-ai"],
                    "privacy_status": "private",
                }
            )

            result = service.run_youtube_short_campaign(
                run=None,
                job=SimpleNamespace(
                    payload_json={
                        "conversation_id": 42,
                        "brief": "zrob shorta o AMD",
                        "topic": "AMD ma sens",
                        "project_slug": "amd-short",
                        "publish_mode": "local_only",
                        "approved_for_publish": False,
                        "peer_consultation": False,
                        "quality_profile": "premium",
                    },
                    goal="zrob shorta o AMD",
                    project_id=1,
                    connector_key="youtube-studio",
                    platform_key="youtube",
                ),
                controls=object(),
                publish_allowed=False,
                attach_message=False,
            )

        self.assertEqual(result["status"], "manual_upload_ready")
        self.assertEqual(result["publish_result"]["status"], "local_render_complete")
        self.assertEqual(result["publish_result"]["publish_path"], "manual_upload_ready")
        self.assertEqual(result["analytics_summary"]["status"], "skipped")
        self.assertFalse(result["approval_required"])
        self.assertIn("render_qa", result["artifacts"])
        self.assertIn("video", result["artifacts"])
        self.assertIn("quality_run", result)
        self.assertIn(result["quality_run"]["status"], {"DONE_VERIFIED", "DONE_NEEDS_REVIEW"})
        self.assertTrue(Path(str(result["quality_run"]["report_path"])).exists())
        self.assertEqual(result["workflow_state"]["current_node"], "graph.local_delivery")
        service.voice.synthesize.assert_called_once()
        service.subtitles.generate.assert_called_once()

    def test_runtime_not_ready_stops_before_generation(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        app = create_test_app(root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with app.state.database.session_scope() as session:
            service = ContentCampaignService(
                session,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
            )
            service.local_short_runtime.snapshot = Mock(
                return_value=LocalShortRuntimeSnapshot(
                    enabled=True,
                    ready=False,
                    reason="Brakuje gotowych runtime'ow: Piper, whisper.cpp.",
                    components=(),
                )
            )
            service._generate_research = Mock()

            result = service.run_youtube_short_campaign(
                run=None,
                job=SimpleNamespace(
                    payload_json={
                        "conversation_id": 42,
                        "brief": "zrob shorta o AMD",
                        "topic": "AMD ma sens",
                        "project_slug": "amd-short",
                        "publish_mode": "local_only",
                        "approved_for_publish": False,
                        "peer_consultation": False,
                        "quality_profile": "premium",
                    },
                    goal="zrob shorta o AMD",
                    project_id=1,
                    connector_key="youtube-studio",
                    platform_key="youtube",
                ),
                controls=object(),
                publish_allowed=False,
                attach_message=False,
            )

        self.assertEqual(result["status"], "runtime_not_ready")
        self.assertEqual(result["workflow_state"]["status"], "runtime_not_ready")
        self.assertEqual(result["quality_run"]["status"], "FAILED_WITH_REASON")
        service._generate_research.assert_not_called()

    @staticmethod
    def _managed_artifact(
        artifacts: OutputArtifactService,
        *,
        conversation_id: int,
        extension: str,
        filename: str,
        media_type: str,
    ):
        output_path = artifacts.create_output_path(conversation_id=conversation_id, extension=extension)
        output_path.write_bytes(b"stub-artifact-data")
        return artifacts.finalize_output(
            absolute_path=output_path,
            filename=filename,
            media_type=media_type,
        )


if __name__ == "__main__":
    unittest.main()
