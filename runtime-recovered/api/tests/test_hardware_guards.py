from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings

from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from atnos.services.hardware_guard_service import (
    HardwareFeatureUnavailableError,
    HardwareGuardService,
)
from atnos.services.wav2lip_runtime_service import Wav2LipRuntimeError, Wav2LipRuntimeService


class HardwareGuardsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd()
        self.settings = build_test_settings(self.root)

    def test_require_gpu_honors_lack_gpu_override(self) -> None:
        with patch.dict("os.environ", {HardwareGuardService.LACK_GPU_ENV: "1"}, clear=False):
            with self.assertRaises(HardwareFeatureUnavailableError) as ctx:
                HardwareGuardService.require_gpu("ComfyUI image generation")

        self.assertIn("LACK_GPU=1", str(ctx.exception))

    def test_comfyui_generation_returns_controlled_error_when_gpu_is_forced_missing(self) -> None:
        service = ComfyUiImageGenerationService(self.settings)
        with patch.dict("os.environ", {HardwareGuardService.LACK_GPU_ENV: "1"}, clear=False):
            with self.assertRaises(ComfyUiImageGenerationError) as ctx:
                service.generate_background(
                    prompt="fruit on a table",
                    output_path=self.settings.workspace_staging_dir / "test.png",
                    width=1024,
                    height=1024,
                    seed=42,
                )

        self.assertIn("unavailable on this hardware", str(ctx.exception))

    def test_wav2lip_runtime_returns_controlled_error_when_gpu_is_forced_missing(self) -> None:
        service = Wav2LipRuntimeService(self.settings)
        with patch.dict("os.environ", {HardwareGuardService.LACK_GPU_ENV: "1"}, clear=False):
            with self.assertRaises(Wav2LipRuntimeError) as ctx:
                service.ensure_ready()

        self.assertIn("unavailable on this hardware", str(ctx.exception))

    def test_select_primary_gpu_prefers_registry_qw_memory_size_over_truncated_wmi_adapter_ram(self) -> None:
        candidate = HardwareGuardService._select_primary_gpu(
            [
                {
                    "name": "AMD Radeon RX 7600 XT",
                    "device_id": "PCI\\VEN_1002&DEV_7480&SUBSYS_B4881025&REV_C0",
                    "total_bytes": 17163091968,
                }
            ],
            [
                {
                    "name": "AMD Radeon RX 7600 XT",
                    "device_id": "PCI\\VEN_1002&DEV_7480&SUBSYS_B4881025&REV_C0",
                    "video_processor": "AMD Radeon Graphics Processor (0x7480)",
                    "total_bytes": 4293918720,
                }
            ],
        )

        assert candidate is not None
        self.assertEqual(candidate["name"], "AMD Radeon RX 7600 XT")
        self.assertEqual(candidate["total_bytes"], 17163091968)

    def test_capture_exposes_live_cpu_and_used_ram_fields(self) -> None:
        service = HardwareGuardService(None, self.settings)  # type: ignore[arg-type]

        with (
            patch.object(HardwareGuardService, "_memory_snapshot", return_value=(32768, 24576, 8192)),
            patch.object(HardwareGuardService, "_cpu_snapshot", return_value=23.5),
            patch.object(HardwareGuardService, "_disk_snapshot", return_value=512),
            patch.object(
                HardwareGuardService,
                "_vram_snapshot",
                return_value=(16384, 4096, 12288, "AMD Radeon RX 7600 XT", None),
            ),
        ):
            snapshot = service.capture(persist=False)

        self.assertEqual(snapshot.used_ram_mb, 8192)
        self.assertEqual(snapshot.cpu_percent, 23.5)
        self.assertEqual(snapshot.gpu_name, "AMD Radeon RX 7600 XT")
        self.assertEqual(snapshot.free_vram_mb, 12288)

    def test_capture_keeps_low_vram_as_advisory_only(self) -> None:
        service = HardwareGuardService(None, self.settings)  # type: ignore[arg-type]

        with (
            patch.object(HardwareGuardService, "_memory_snapshot", return_value=(32768, 24576, 8192)),
            patch.object(HardwareGuardService, "_cpu_snapshot", return_value=23.5),
            patch.object(HardwareGuardService, "_disk_snapshot", return_value=512),
            patch.object(
                HardwareGuardService,
                "_vram_snapshot",
                return_value=(16384, 15872, 512, "AMD Radeon RX 7600 XT", None),
            ),
        ):
            snapshot = service.capture(vram_floor_gb=6, persist=False)

        self.assertEqual(snapshot.status, "ready")
        self.assertIn("Advisory only: free VRAM below 6 GB floor.", snapshot.notes or "")

    def test_resident_models_are_preserved_without_drain_proof(self) -> None:
        service = HardwareGuardService(None, self.settings)
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with (
            patch("atnos.services.hardware_guard_service.httpx.Client", return_value=client),
            patch("atnos.services.hardware_guard_service.subprocess.run", side_effect=AssertionError("CLI must not run")),
        ):
            result = service.release_gpu_pressure()
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["stopped_models"], [])
        self.assertEqual(result["active_models_before"], ["qwen3:14b"])
        self.assertFalse(result["verified_released"])
        self.assertIsNone(result["active_models_after"])
        self.assertEqual([(request.method, request.url.path) for request in requests], [("GET", "/api/ps")])

    def test_empty_model_listing_is_verified_without_stop(self) -> None:
        service = HardwareGuardService(None, self.settings)
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"models": []})))
        with (
            patch("atnos.services.hardware_guard_service.httpx.Client", return_value=client) as probe,
            patch("atnos.services.hardware_guard_service.subprocess.run", side_effect=AssertionError("CLI must not run")),
        ):
            result = service.release_gpu_pressure()
        self.assertEqual(result["status"], "noop")
        self.assertTrue(result["verified_released"])
        self.assertEqual(probe.call_count, 1)

    def test_failed_model_probe_does_not_report_release_or_stop(self) -> None:
        service = HardwareGuardService(None, self.settings)
        for outcome in [httpx.Response(503, text="unavailable"), httpx.ReadTimeout("busy")]:
            def respond(_):
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            client = httpx.Client(transport=httpx.MockTransport(respond))
            with (
                self.subTest(outcome=outcome),
                patch("atnos.services.hardware_guard_service.httpx.Client", return_value=client) as probe,
                patch("atnos.services.hardware_guard_service.subprocess.run", side_effect=AssertionError("CLI must not run")),
            ):
                result = service.release_gpu_pressure()
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result.get("verified_released", False))
                self.assertEqual(result["stopped_models"], [])
                self.assertEqual(probe.call_count, 1)
