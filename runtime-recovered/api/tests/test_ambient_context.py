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

from atnos.services.ambient_context_service import AmbientContextService
from atnos.services.hardware_guard_service import HardwareSnapshotView


class AmbientContextServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_collect_once_returns_in_memory_snapshot_without_db_persist(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            service = AmbientContextService(
                app.state.database,
                settings=app.state.settings,
                provider_registry=app.state.providers,
            )
            with (
                patch.object(
                    AmbientContextService,
                    "_read_foreground_window_title",
                    return_value="Visual Studio Code",
                ),
                patch.object(
                    AmbientContextService,
                    "_read_foreground_process_name",
                    return_value="Code.exe",
                ),
                patch.object(AmbientContextService, "_network_online", return_value=True),
                patch.object(
                    AmbientContextService,
                    "_resolve_active_project",
                    return_value=SimpleNamespace(id=7, name="Atnos"),
                ),
                patch(
                    "atnos.services.ambient_context_service.psutil.virtual_memory",
                    return_value=SimpleNamespace(
                        total=16 * 1024 * 1024 * 1024,
                        available=10 * 1024 * 1024 * 1024,
                    ),
                ),
                patch(
                    "atnos.services.ambient_context_service.psutil.cpu_percent",
                    return_value=12.5,
                ),
                patch(
                    "atnos.services.ambient_context_service.HardwareGuardService.capture",
                    return_value=HardwareSnapshotView(
                        free_vram_mb=6144,
                        used_vram_mb=2048,
                        total_vram_mb=8192,
                        free_ram_mb=8192,
                        total_ram_mb=16384,
                        free_disk_gb=120,
                        disk_path="F:\\",
                        status="ready",
                        notes=None,
                    ),
                ) as capture_mock,
            ):
                snapshot = service.collect_once()

            capture_mock.assert_called_once_with(
                snapshot_type="ambient-context",
                persist=False,
            )
            self.assertEqual(snapshot.foreground_window_title, "Visual Studio Code")
            self.assertEqual(snapshot.active_app_name, "Code.exe")
            self.assertEqual(snapshot.active_project_name, "Atnos")
            self.assertEqual(snapshot.free_vram_mb, 6144)
            self.assertTrue(snapshot.network_online)
            self.assertIsNotNone(service.get_snapshot())
