from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.memory.obsidian_writer import write_analytics_learning_note, write_production_log
from atnos.production.models import ApprovedTopic, ProductionContext
from test_support import build_test_settings


def _context(tmp_path: Path) -> ProductionContext:
    return ProductionContext(
        job_id="prod-obsidian",
        account_id="acct-1",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="acct-1",
            title="Budzet domowy bez chaosu",
            description="Finanse osobiste PL",
            total_score=82,
        ),
        artifact_dir=tmp_path / "artifacts",
        artifacts={
            "final_status": "qa_passed",
            "hook": {"selected": {"text": "Czy pieniadze uciekaja?"}},
            "qa": {
                "timestamp": "2026-06-02T00:00:00+00:00",
                "overall": "passed",
                "blocking_failures": [],
                "warnings": [],
            },
        },
    )


def test_write_production_log_writes_inside_obsidian_vault(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    path = write_production_log(settings, _context(tmp_path))

    assert path is not None
    assert path.exists()
    assert path.resolve().is_relative_to(settings.obsidian_vault_path.resolve())
    assert path.parent == settings.obsidian_vault_path / "Atnos" / "Shorts" / "acct-1"
    text = path.read_text(encoding="utf-8")
    assert "type: production-log" in text
    assert "Czy pieniadze uciekaja?" in text
    assert "Review QA before scheduling public release." in text


def test_write_production_log_respects_obsidian_export_flags(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = False
    settings.atnos_write_obsidian_logs = True

    assert write_production_log(settings, _context(tmp_path)) is None

    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = False
    assert write_production_log(settings, _context(tmp_path)) is None


def test_write_production_log_blocks_paths_outside_vault(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "../outside"

    assert write_production_log(settings, _context(tmp_path)) is None
    assert not (tmp_path / "outside").exists()


def test_write_analytics_learning_note_includes_qa_summary(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    path = write_analytics_learning_note(
        settings,
        account_id="acct-1",
        topic_id="topic-1",
        youtube_video_id="yt-1",
        window_hours=48,
        analytics={"views": 1200, "average_view_percentage": 71},
        score_update={
            "performance_score": 84,
            "recommendation": "Repeat specific hooks and concrete finance examples.",
            "qa_summary": {
                "overall": "passed",
                "auto_uploadable": True,
                "blocking_failures": [],
                "advisory_failures": ["low_music_margin"],
                "upload_decision_status": "ready_for_private_upload",
                "private_upload_allowed": True,
            },
        },
        completed_at="2026-06-02T10:00:00+00:00",
    )

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "## QA przed uploadem" in text
    assert "- Status QA: passed" in text
    assert "- Upload decision: ready_for_private_upload" in text
    assert "- Private upload allowed: true" in text
    assert "- Blocking failures: none" in text
    assert "- Advisory failures: low_music_margin" in text
    assert "gwarancji zarobków" in text
