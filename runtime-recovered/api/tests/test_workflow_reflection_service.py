from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.workflow_reflection_service import WorkflowReflectionService


class WorkflowReflectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.hardware_guard = Mock()
        self.hardware_guard.release_gpu_pressure.return_value = {
            "status": "released",
            "message": "Stopped active Ollama models before retry: qwen3:8b.",
            "stopped_models": ["qwen3:8b"],
        }
        self.service = WorkflowReflectionService(self.settings, self.hardware_guard)

    def test_reflect_render_failure_reduces_cutaways_for_synthetic_motion(self) -> None:
        plan = self.service.reflect_render_failure(
            stage_key="synthetic_motion_reel",
            error_message="Synthetic motion watchdog stopped a stuck render after 420 seconds.",
            attempt=1,
            current_render_profile="balanced",
            cutaway_count=3,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.adjustments["render_profile"], "fast_recovery")
        self.assertEqual(plan.adjustments["max_cutaways"], 2)
        self.assertEqual(plan.gpu_release["status"], "released")
        self.hardware_guard.release_gpu_pressure.assert_called_once()

    def test_reflect_render_failure_downgrades_quality_to_balanced_first(self) -> None:
        plan = self.service.reflect_render_failure(
            stage_key="campaign_render",
            error_message="Render watchdog stopped a stuck render after 420 seconds.",
            attempt=1,
            current_render_profile="quality",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.adjustments["render_profile"], "balanced")

    def test_reflect_image_generation_failure_reduces_comfyui_load(self) -> None:
        plan = self.service.reflect_image_generation_failure(
            stage_key="synthetic_identity",
            error_message="ComfyUI generation timed out after 180 seconds.",
            attempt=1,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertLess(int(plan.adjustments["comfyui_steps"]), self.settings.comfyui_steps)
        self.assertLess(float(plan.adjustments["comfyui_cfg_scale"]), self.settings.comfyui_cfg_scale)
        self.hardware_guard.release_gpu_pressure.assert_called()


if __name__ == "__main__":
    unittest.main()
