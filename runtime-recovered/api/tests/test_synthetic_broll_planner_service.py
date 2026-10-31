from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings
from atnos.services.creator_ops_service import CreatorProjectView
from atnos.services.synthetic_broll_planner_service import SyntheticBrollPlannerService
from atnos.services.synthetic_creator_workflow_service import SyntheticScriptBundle


class SyntheticBrollPlannerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.service = SyntheticBrollPlannerService(self.settings)
        self.project = CreatorProjectView(
            id=91,
            project_id=191,
            name="ATNOS Synthetic Creator",
            project_type="synthetic_influencer",
            status="active",
            persona_type="synthetic_original_persona",
            disclosure_policy="platform_dependent",
            niche="premium synthetic creator",
            target_audience="adult short-form audience",
            monetization_goal="validate local preview",
            content_boundaries="non-explicit",
            safety_boundaries="no impersonation",
            platforms=("instagram", "youtube"),
            linked_account_ids=(),
            linked_integrations=(),
            operating_mode="supervised_actions",
            approval_policy="always_require_approval",
            notes=None,
            strategy_profile={},
            linked_accounts_summary=(),
            project_app_links_summary=(),
            project_app_links_defaults_summary={},
            created_at="2026-05-10T00:00:00Z",
            updated_at="2026-05-10T00:00:00Z",
        )
        self.bundle = SyntheticScriptBundle(
            title="Synthetic preview",
            hook="Pierwszy mocny hook o swietle i rytmie.",
            body=("Drugie zdanie o detalach.", "Trzecie zdanie o payoffie."),
            outro="Czwarty beat zamyka klimatem.",
            portrait_prompt="ignored",
            visual_direction="clean editorial portrait",
            engine="test",
        )

    def test_generate_cutaways_returns_three_assets(self) -> None:
        style = Mock(key="editorial_premium", label="Editorial Premium", visual_direction="premium light")
        reel = Mock(key="micro_story", label="Micro Story")
        prompts: list[str] = []

        def fake_generate_background(*, prompt: str, output_path: Path, **kwargs) -> None:
            prompts.append(prompt)
            output_path.write_bytes(b"png")

        with (
            patch.object(self.service.comfyui, "can_generate", return_value=True),
            patch.object(self.service.comfyui, "generate_background", side_effect=fake_generate_background),
        ):
            result = self.service.generate_cutaways(
                project=self.project,
                script_bundle=self.bundle,
                style=style,
                reel=reel,
                conversation_id=None,
            )

        self.assertEqual(len(result.assets), 3)
        self.assertEqual(result.engine, "comfyui-cutaway")
        self.assertEqual(len(prompts), 3)
        self.assertIn("supporting this spoken beat", prompts[0])

    def test_generate_cutaways_soft_fails_when_runtime_is_missing(self) -> None:
        style = Mock(key="editorial_premium", label="Editorial Premium", visual_direction="premium light")
        reel = Mock(key="talking_hook", label="Talking Hook")

        with patch.object(self.service.comfyui, "can_generate", return_value=False):
            result = self.service.generate_cutaways(
                project=self.project,
                script_bundle=self.bundle,
                style=style,
                reel=reel,
                conversation_id=None,
            )

        self.assertEqual(result.assets, ())
        self.assertEqual(result.engine, "comfyui-cutaway-unavailable")
        self.assertTrue(result.warnings)

    def test_generate_cutaways_premium_profile_uses_stronger_generation_overrides(self) -> None:
        style = Mock(key="editorial_premium", label="Editorial Premium", visual_direction="premium light")
        reel = Mock(key="micro_story", label="Micro Story")
        captured_calls: list[dict[str, object]] = []

        def fake_generate_background(*, output_path: Path, **kwargs) -> None:
            captured_calls.append(dict(kwargs))
            output_path.write_bytes(b"png")

        with (
            patch.object(self.service.comfyui, "can_generate", return_value=True),
            patch.object(self.service.comfyui, "generate_background", side_effect=fake_generate_background),
        ):
            result = self.service.generate_cutaways(
                project=self.project,
                script_bundle=self.bundle,
                style=style,
                reel=reel,
                conversation_id=None,
                quality_profile="premium",
            )

        self.assertEqual(result.engine, "comfyui-cutaway")
        self.assertEqual(len(captured_calls), 3)
        self.assertEqual(captured_calls[0]["steps_override"], 28)
        self.assertEqual(captured_calls[0]["cfg_scale_override"], 6.8)


def build_test_settings(root: Path) -> Settings:
    data_dir = root / "data"
    uploads_dir = data_dir / "uploads"
    sqlite_file = data_dir / "atnos-test.db"
    settings = Settings(
        app_env="test",
        app_root=root,
        data_dir=data_dir,
        uploads_dir=uploads_dir,
        sqlite_file=sqlite_file,
        comfyui_install_dir=root / "ComfyUI_windows_portable",
        kokoro_model_path=data_dir / "models" / "kokoro" / "model.onnx",
        kokoro_voices_path=data_dir / "models" / "kokoro" / "voices-v1.0.bin",
        scheduled_tasks_enabled=False,
    )
    settings.ensure_directories()
    return settings


def make_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="atnos-api-tests-")


if __name__ == "__main__":
    unittest.main()
