from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.local_short_runtime_doctor_service import LocalShortRuntimeDoctorService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeComponent, LocalShortRuntimeSnapshot

from test_support import build_test_settings, make_temp_dir


class FakeProbe:
    def __init__(self, snapshot: LocalShortRuntimeSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> LocalShortRuntimeSnapshot:
        return self._snapshot


def test_doctor_report_turns_missing_runtime_into_actionable_env_and_steps() -> None:
    temp_dir = make_temp_dir()
    try:
        settings = build_test_settings(Path(temp_dir.name))
        service = LocalShortRuntimeDoctorService(
            settings,
            probe=FakeProbe(
                LocalShortRuntimeSnapshot(
                    enabled=True,
                    ready=False,
                    reason="Brakuje gotowych runtime'ow: Ollama, Piper, whisper.cpp.",
                    components=(
                        LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "ffmpeg", "ready"),
                        LocalShortRuntimeComponent("ollama", "Ollama", False, "http://127.0.0.1:11434", "offline"),
                        LocalShortRuntimeComponent("piper", "Piper", False, None, "not configured"),
                        LocalShortRuntimeComponent("whisper_cpp", "whisper.cpp", False, None, "not configured"),
                    ),
                )
            ),
        )

        report = service.build_report()

        assert report.status == "blocked"
        assert report.ready is False
        assert report.missing_components == ("ollama", "piper", "whisper_cpp")
        assert "PIPER_EXE=" in report.env_snippet
        assert "WHISPER_CPP_EXE=" in report.env_snippet
        assert "OLLAMA_BASE_URL=" in report.env_snippet
        assert {action.component for action in report.recommended_actions} == {"ollama", "piper", "whisper_cpp"}
        assert "Local Short Runtime Doctor" in report.to_markdown()
    finally:
        temp_dir.cleanup()


def test_doctor_report_ready_runtime_has_no_recommended_actions_and_writes_json() -> None:
    temp_dir = make_temp_dir()
    try:
        root = Path(temp_dir.name)
        settings = build_test_settings(root)
        service = LocalShortRuntimeDoctorService(
            settings,
            probe=FakeProbe(
                LocalShortRuntimeSnapshot(
                    enabled=True,
                    ready=True,
                    reason="Local short pipeline is ready.",
                    components=(
                        LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "ffmpeg", "ready"),
                        LocalShortRuntimeComponent("ollama", "Ollama", True, "http://127.0.0.1:11434", "ready"),
                        LocalShortRuntimeComponent("piper", "Piper", True, "piper", "ready"),
                        LocalShortRuntimeComponent("whisper_cpp", "whisper.cpp", True, "whisper", "ready"),
                    ),
                )
            ),
        )

        report = service.build_report()
        output_path = service.write_report(root / "doctor.json")

        assert report.status == "ready"
        assert report.ready is True
        assert report.recommended_actions == ()
        assert output_path.exists()
        assert '"status": "ready"' in output_path.read_text(encoding="utf-8")
    finally:
        temp_dir.cleanup()


def test_doctor_report_can_include_full_production_readiness_blockers(monkeypatch) -> None:
    temp_dir = make_temp_dir()
    try:
        settings = build_test_settings(Path(temp_dir.name))

        def fake_production_readiness(self):
            return {
                "status": "needs_configuration",
                "ready": False,
                "blocking": ["intelligence_db", "youtube_oauth"],
                "next_actions": [
                    "Run /api/intelligence collectors before topic generation.",
                    "Import Google client_secret.json and start YouTube OAuth.",
                ],
            }

        monkeypatch.setattr(
            LocalShortRuntimeDoctorService,
            "_build_production_readiness",
            fake_production_readiness,
        )
        service = LocalShortRuntimeDoctorService(
            settings,
            include_production_readiness=True,
            probe=FakeProbe(
                LocalShortRuntimeSnapshot(
                    enabled=True,
                    ready=True,
                    reason="Local short pipeline is ready.",
                    components=(
                        LocalShortRuntimeComponent("ffmpeg", "FFmpeg", True, "ffmpeg", "ready"),
                        LocalShortRuntimeComponent("ollama", "Ollama", True, "http://127.0.0.1:11434", "ready"),
                    ),
                )
            ),
        )

        report = service.build_report()
        payload = report.to_dict()
        markdown = report.to_markdown()

        assert report.status == "blocked"
        assert report.ready is False
        assert report.production_blocking == ("intelligence_db", "youtube_oauth")
        assert payload["production_readiness"]["status"] == "needs_configuration"
        assert "Production blockers: intelligence_db, youtube_oauth" in report.reason
        assert "Production Readiness" in markdown
        assert "Import Google client_secret.json" in markdown
        assert "production_readiness" in {action.component for action in report.recommended_actions}
    finally:
        temp_dir.cleanup()
