from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.services.synthetic_release_pack_service import SyntheticReleasePackService


class SyntheticReleasePackServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SyntheticReleasePackService()

    def test_build_release_pack_returns_youtube_ready_metadata(self) -> None:
        preview = {
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "script_bundle": {
                "title": "Mocny testowy reel",
                "hook": "To jest szybki hook pod pierwsze sekundy.",
                "body": ["Pierwsza linia body.", "Druga linia body."],
            },
            "artifacts": {
                "portrait": {"storage_path": "outputs/portrait.png", "filename": "portrait.png", "media_type": "image/png", "file_extension": ".png", "size_bytes": 12},
                "audio": {"storage_path": "outputs/audio.wav", "filename": "audio.wav", "media_type": "audio/wav", "file_extension": ".wav", "size_bytes": 12},
                "subtitles": {"storage_path": "outputs/subs.srt", "filename": "subs.srt", "media_type": "application/x-subrip", "file_extension": ".srt", "size_bytes": 12},
                "report": {"storage_path": "outputs/report.md", "filename": "report.md", "media_type": "text/markdown", "file_extension": ".md", "size_bytes": 12},
                "cutaways": {"assets": [{}, {}, {}], "engine": "comfyui-cutaway"},
                "motion_reel": {
                    "descriptor": {
                        "storage_path": "outputs/reel.mp4",
                        "filename": "reel.mp4",
                        "media_type": "video/mp4",
                        "file_extension": ".mp4",
                        "size_bytes": 128,
                    },
                    "qa": {"duration_sec": 16.2, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
        }
        selection = {"best_variant_index": 1, "best_score": 93.7, "best_confidence": "high"}
        score_payload = {"score": 100.0, "confidence": "high", "risks": []}

        pack = self.service.build_release_pack(
            project_name="ATNOS Synthetic Creator",
            preview=preview,
            selection=selection,
            score_payload=score_payload,
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": ["OAuth missing"],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            },
        )

        payload = pack.to_dict()
        self.assertEqual(payload["platform_key"], "youtube")
        self.assertEqual(payload["publish_path"], "manual_upload_ready")
        metadata = dict(payload["metadata"])
        self.assertEqual(metadata["privacy_status"], "private")
        self.assertEqual(list(metadata["project_strategy_policy"]), [])
        self.assertEqual(payload["recommended_variant_index"], 1)
        self.assertIn("#shorts", list(metadata["hashtags"]))
        self.assertEqual(payload["publish_result_preview"]["status"], "manual_upload_required")
        self.assertTrue(str(dict(payload["approval_package"])["summary"]).startswith("Tytul:"))

    def test_build_release_pack_switches_to_official_publish_when_connector_ready(self) -> None:
        preview = {
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "cta_teaser", "label": "CTA Teaser"},
            "script_bundle": {
                "title": "Premium teaser",
                "hook": "To jest krótki hook gotowy do publikacji.",
                "body": ["Body line."],
            },
            "artifacts": {
                "portrait": {"storage_path": "outputs/portrait.png", "filename": "portrait.png", "media_type": "image/png", "file_extension": ".png", "size_bytes": 12},
                "audio": {"storage_path": "outputs/audio.wav", "filename": "audio.wav", "media_type": "audio/wav", "file_extension": ".wav", "size_bytes": 12},
                "subtitles": {"storage_path": "outputs/subs.srt", "filename": "subs.srt", "media_type": "application/x-subrip", "file_extension": ".srt", "size_bytes": 12},
                "report": {"storage_path": "outputs/report.md", "filename": "report.md", "media_type": "text/markdown", "file_extension": ".md", "size_bytes": 12},
                "cutaways": {"assets": [{}, {}], "engine": "comfyui-cutaway"},
                "motion_reel": {
                    "descriptor": {
                        "storage_path": "outputs/reel.mp4",
                        "filename": "reel.mp4",
                        "media_type": "video/mp4",
                        "file_extension": ".mp4",
                        "size_bytes": 128,
                    },
                    "qa": {"duration_sec": 14.6, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
        }

        pack = self.service.build_release_pack(
            project_name="ATNOS Synthetic Creator",
            preview=preview,
            selection={"best_variant_index": 0},
            score_payload={"score": 94.1, "confidence": "high"},
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": True,
                "connected": True,
                "ready": True,
                "warnings": [],
                "account_id": 7,
                "channel_title": "ATNOS Channel",
                "granted_scopes": ["youtube.upload"],
            },
        )

        payload = pack.to_dict()
        self.assertEqual(payload["publish_path"], "official_youtube_ready")
        self.assertEqual(payload["publish_result_preview"]["status"], "awaiting_approval")
        self.assertEqual(payload["analytics_followup"]["status"], "ready_after_publish")

    def test_build_release_pack_biases_metadata_with_historical_feedback(self) -> None:
        preview = {
            "style": {"key": "cozy_girlfriend", "label": "Cozy Girlfriend"},
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "script_bundle": {
                "title": "Spokojny wariant",
                "hook": "To jest hook gotowy do twardszego testu pod retention.",
                "body": ["Body line."],
            },
            "artifacts": {
                "portrait": {"storage_path": "outputs/portrait.png", "filename": "portrait.png", "media_type": "image/png", "file_extension": ".png", "size_bytes": 12},
                "audio": {"storage_path": "outputs/audio.wav", "filename": "audio.wav", "media_type": "audio/wav", "file_extension": ".wav", "size_bytes": 12},
                "subtitles": {"storage_path": "outputs/subs.srt", "filename": "subs.srt", "media_type": "application/x-subrip", "file_extension": ".srt", "size_bytes": 12},
                "report": {"storage_path": "outputs/report.md", "filename": "report.md", "media_type": "text/markdown", "file_extension": ".md", "size_bytes": 12},
                "cutaways": {"assets": [{}, {}], "engine": "comfyui-cutaway"},
                "motion_reel": {
                    "descriptor": {
                        "storage_path": "outputs/reel.mp4",
                        "filename": "reel.mp4",
                        "media_type": "video/mp4",
                        "file_extension": ".mp4",
                        "size_bytes": 128,
                    },
                    "qa": {"duration_sec": 16.2, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
        }

        pack = self.service.build_release_pack(
            project_name="ATNOS Synthetic Creator",
            preview=preview,
            selection={"best_variant_index": 0},
            score_payload={"score": 86.0, "confidence": "medium", "risks": []},
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": ["OAuth missing"],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            },
            analytics_learning_profile={
                "sample_count": 3,
                "baseline_average_view_percentage": 69.0,
                "baseline_views": 800.0,
                "style_feedback": {"cozy_girlfriend": -3.0},
                "style_sample_counts": {"cozy_girlfriend": 2},
                "reel_feedback": {"talking_hook": -1.5},
                "reel_sample_counts": {"talking_hook": 3},
                "strongest_style_key": "editorial_premium",
                "strongest_reel_key": "micro_story",
                "insights": [],
            },
        )

        payload = pack.to_dict()
        metadata = dict(payload["metadata"])
        self.assertTrue(str(metadata["title"]).startswith("To jest hook"))
        self.assertTrue(any("Analytics sample count" in item for item in list(metadata["historical_playbook"])))

    def test_build_release_pack_prefers_hook_first_title_when_history_says_so(self) -> None:
        preview = {
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "micro_story", "label": "Micro Story"},
            "script_bundle": {
                "title": "Spokojniejszy tytul bazowy",
                "hook": "Ten hook powinien otwierac tytul po follow-upie analytics.",
                "body": ["Body line."],
            },
            "artifacts": {
                "portrait": {"storage_path": "outputs/portrait.png", "filename": "portrait.png", "media_type": "image/png", "file_extension": ".png", "size_bytes": 12},
                "audio": {"storage_path": "outputs/audio.wav", "filename": "audio.wav", "media_type": "audio/wav", "file_extension": ".wav", "size_bytes": 12},
                "subtitles": {"storage_path": "outputs/subs.srt", "filename": "subs.srt", "media_type": "application/x-subrip", "file_extension": ".srt", "size_bytes": 12},
                "report": {"storage_path": "outputs/report.md", "filename": "report.md", "media_type": "text/markdown", "file_extension": ".md", "size_bytes": 12},
                "cutaways": {"assets": [{}, {}], "engine": "comfyui-cutaway"},
                "motion_reel": {
                    "descriptor": {
                        "storage_path": "outputs/reel.mp4",
                        "filename": "reel.mp4",
                        "media_type": "video/mp4",
                        "file_extension": ".mp4",
                        "size_bytes": 128,
                    },
                    "qa": {"duration_sec": 18.8, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
        }

        pack = self.service.build_release_pack(
            project_name="ATNOS Synthetic Creator",
            preview=preview,
            selection={"best_variant_index": 0},
            score_payload={"score": 91.0, "confidence": "high", "risks": []},
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": [],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            },
            analytics_learning_profile={
                "sample_count": 4,
                "baseline_average_view_percentage": 71.0,
                "baseline_views": 700.0,
                "style_feedback": {"editorial_premium": 0.5},
                "style_sample_counts": {"editorial_premium": 4},
                "reel_feedback": {"micro_story": 0.4},
                "reel_sample_counts": {"micro_story": 4},
                "strongest_style_key": "editorial_premium",
                "strongest_reel_key": "micro_story",
                "strongest_title_strategy": "hook_first_title",
                "insights": [],
            },
        )

        metadata = dict(pack.to_dict()["metadata"])
        self.assertTrue(str(metadata["title"]).startswith("Ten hook powinien"))

    def test_build_release_pack_uses_project_strategy_profile_for_metadata_policy(self) -> None:
        preview = {
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "script_bundle": {
                "title": "Spokojniejszy tytul bazowy",
                "hook": "Ten hook powinien prowadzic metadata projektu od pierwszej sekundy.",
                "body": ["Body line."],
            },
            "artifacts": {
                "portrait": {"storage_path": "outputs/portrait.png", "filename": "portrait.png", "media_type": "image/png", "file_extension": ".png", "size_bytes": 12},
                "audio": {"storage_path": "outputs/audio.wav", "filename": "audio.wav", "media_type": "audio/wav", "file_extension": ".wav", "size_bytes": 12},
                "subtitles": {"storage_path": "outputs/subs.srt", "filename": "subs.srt", "media_type": "application/x-subrip", "file_extension": ".srt", "size_bytes": 12},
                "report": {"storage_path": "outputs/report.md", "filename": "report.md", "media_type": "text/markdown", "file_extension": ".md", "size_bytes": 12},
                "cutaways": {"assets": [{}, {}], "engine": "comfyui-cutaway"},
                "motion_reel": {
                    "descriptor": {
                        "storage_path": "outputs/reel.mp4",
                        "filename": "reel.mp4",
                        "media_type": "video/mp4",
                        "file_extension": ".mp4",
                        "size_bytes": 128,
                    },
                    "qa": {"duration_sec": 15.8, "width": 1080, "height": 1920, "has_audio": True, "warnings": []},
                },
            },
        }

        pack = self.service.build_release_pack(
            project_name="ATNOS Synthetic Creator",
            preview=preview,
            selection={"best_variant_index": 0},
            score_payload={"score": 89.0, "confidence": "high", "risks": []},
            connector_status={
                "connector_key": "youtube-studio",
                "display_name": "YouTube Studio",
                "configured": False,
                "connected": False,
                "ready": False,
                "warnings": [],
                "account_id": None,
                "channel_title": None,
                "granted_scopes": [],
            },
            project_strategy_profile={
                "title_policy": {
                    "strategy_key": "hook_first_title",
                    "template_key": "hook_first",
                    "instruction": "Domyslnie zaczynaj tytul od hooka bez dlugiego wstepu.",
                },
                "hook_template": {
                    "strategy_key": "faster_hook",
                    "template_key": "direct_payoff",
                    "instruction": "Hook ma wchodzic szybciej i bez miekkiego rozgrzewania intro.",
                },
            },
        )

        payload = pack.to_dict()
        metadata = dict(payload["metadata"])
        self.assertTrue(str(metadata["title"]).startswith("Ten hook powinien"))
        self.assertTrue(any("Domyslnie zaczynaj tytul od hooka" in item for item in list(metadata["project_strategy_policy"])))
        self.assertTrue(any("Trzymaj projektowa polityke metadata" in item for item in list(payload["upload_checklist"])))


if __name__ == "__main__":
    unittest.main()
