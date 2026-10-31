from __future__ import annotations

from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.memory.obsidian_style_guide import ensure_channel_style_guide, read_channel_style_guide
from atnos.memory.obsidian_writer import write_analytics_learning_note
from test_support import build_test_settings


def test_bootstrap_channel_style_guide_creates_readable_obsidian_note(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    result = ensure_channel_style_guide(
        settings,
        "YT Finanse PL",
        channel_name="YT Finanse PL",
        niche="finanse osobiste dla polskich Shorts",
    )
    readback = read_channel_style_guide(settings, "YT Finanse PL")

    assert result.created is True
    assert result.status == "created"
    assert result.path is not None
    assert result.path.resolve().is_relative_to(settings.obsidian_vault_path.resolve())
    assert result.path.name == "style_guide.md"
    assert readback.status == "obsidian_channel_style"
    assert readback.source == str(result.path)
    assert readback.content is not None
    assert "No invented earnings" in readback.content
    assert "finanse osobiste dla polskich Shorts" in readback.content
    assert "password" not in readback.content.casefold()


def test_bootstrap_channel_style_guide_uses_history_specific_template(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    result = ensure_channel_style_guide(
        settings,
        "yt-historia-pl",
        channel_name="YT Historia",
        niche="dark history facts and documentary Shorts",
    )

    assert result.path is not None
    content = result.path.read_text(encoding="utf-8")
    assert "Language: English voiceover" in content
    assert "historical object" in content
    assert "dark historical documentary" in content
    assert "one concrete financial idea" not in content
    assert "Premium dark finance editorial" not in content


def test_bootstrap_channel_style_guide_uses_brainrot_specific_template(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    result = ensure_channel_style_guide(
        settings,
        "yt-ai-brainrot-pl",
        channel_name="YT AI Brainrot",
        niche="AI brainrot absurd meme Shorts",
    )

    assert result.path is not None
    content = result.path.read_text(encoding="utf-8")
    assert "Language: English voiceover" in content
    assert "absurd AI meme situation" in content
    assert "bright neon meme animation" in content
    assert "Premium dark finance editorial" not in content
    assert "one concrete financial idea" not in content


def test_bootstrap_channel_style_guide_keeps_finance_safety_rules(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    result = ensure_channel_style_guide(
        settings,
        "yt-finanse-pl",
        channel_name="YT Finance",
        niche="personal finance Shorts",
    )

    assert result.path is not None
    content = result.path.read_text(encoding="utf-8")
    assert "counterintuitive money behavior" in content
    assert "No invented earnings" in content
    assert "Language: English voiceover" in content


def test_bootstrap_channel_style_guide_does_not_overwrite_by_default(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    first = ensure_channel_style_guide(settings, "acct-1")
    assert first.path is not None
    first.path.write_text("# Custom\n\nKeep this.", encoding="utf-8")
    second = ensure_channel_style_guide(settings, "acct-1")

    assert second.created is False
    assert second.status == "exists"
    assert first.path.read_text(encoding="utf-8") == "# Custom\n\nKeep this."


def test_read_channel_style_guide_prefers_obsidian_over_local_fallback(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    local_path = settings.data_dir / "channel_ops" / "channels" / "acct-1" / "style_guide.md"
    local_path.parent.mkdir(parents=True)
    local_path.write_text("# Local stale guide\n\nUse luxury flex and big claims.", encoding="utf-8")
    obsidian_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "acct-1" / "style_guide.md"
    obsidian_path.parent.mkdir(parents=True)
    obsidian_path.write_text("# Obsidian guide\n\nNo invented earnings. Use calm finance framing.", encoding="utf-8")

    readback = read_channel_style_guide(settings, "acct-1")

    assert readback.status == "obsidian_channel_style"
    assert readback.source == str(obsidian_path)
    assert readback.content is not None
    assert "No invented earnings" in readback.content
    assert "luxury flex" not in readback.content


def test_read_channel_style_guide_uses_sanitized_persisted_account_identifier(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    account_id = "youtube:account-c36a26ac@example.invalid"
    safe_account = "youtube-new-channel-example-test"
    local_path = settings.data_dir / "channel_ops" / "channels" / safe_account / "style_guide.md"
    local_path.parent.mkdir(parents=True)
    local_path.write_text("# Local fallback\n\nOld local style.", encoding="utf-8")
    obsidian_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / safe_account / "style_guide.md"
    obsidian_path.parent.mkdir(parents=True)
    obsidian_path.write_text("# New channel\n\nUse Obsidian source of truth.", encoding="utf-8")

    readback = read_channel_style_guide(settings, account_id)

    assert readback.status == "obsidian_channel_style"
    assert readback.source == str(obsidian_path)
    assert readback.content is not None
    assert "Use Obsidian source of truth" in readback.content
    assert "Old local style" not in readback.content


def test_read_channel_style_guide_does_not_cross_map_unrelated_accounts(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    first_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "youtube-first-example-test" / "style_guide.md"
    first_path.parent.mkdir(parents=True)
    first_path.write_text("# First channel\n\nUse archive objects.", encoding="utf-8")

    first = read_channel_style_guide(settings, "youtube:account-3f2eb91b@example.invalid")
    second = read_channel_style_guide(settings, "youtube:account-9aed32ce@example.invalid")

    assert first.status == "obsidian_channel_style"
    assert first.source == str(first_path)
    assert first.content is not None
    assert "archive objects" in first.content
    assert second.status == "missing"
    assert second.source is None
    assert second.content is None


def test_read_channel_style_guide_uses_latest_analytics_learning_note(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"

    first = write_analytics_learning_note(
        settings,
        account_id="acct-1",
        topic_id="topic-old",
        youtube_video_id="yt-old",
        window_hours=24,
        analytics={"views": 10, "average_view_percentage": 20},
        score_update={
            "performance_score": 22,
            "recommendation": "Avoid slow intros and vague finance claims.",
        },
        completed_at="2026-06-01T10:00:00+00:00",
    )
    second = write_analytics_learning_note(
        settings,
        account_id="acct-1",
        topic_id="topic-new",
        youtube_video_id="yt-new",
        window_hours=48,
        analytics={"views": 1200, "average_view_percentage": 76},
        score_update={
            "performance_score": 81,
            "recommendation": "Repeat fast hooks, specific budget examples, and no invented earnings.",
            "qa_summary": {
                "overall": "passed",
                "auto_uploadable": True,
                "blocking_failures": [],
                "advisory_failures": [],
                "upload_decision_status": "ready_for_private_upload",
                "private_upload_allowed": True,
            },
        },
        completed_at="2026-06-02T10:00:00+00:00",
    )
    assert first is not None
    assert second is not None

    readback = read_channel_style_guide(settings, "acct-1")

    assert readback.status == "obsidian_latest_learning"
    assert readback.source == str(second)
    assert readback.content is not None
    assert "Repeat fast hooks" in readback.content
    assert "Avoid slow intros" not in readback.content
    assert "Użyj tej lekcji" in readback.content
    assert "gwarancji zarobków" in readback.content
    assert "QA przed uploadem" in readback.content
    assert "ready_for_private_upload" in readback.content
    assert "Äą" not in readback.content
    assert "Ä‚" not in readback.content
    assert "Ă„" not in readback.content


def test_bootstrap_channel_style_guide_blocks_paths_outside_vault(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.atnos_obsidian_shorts_dir = "../outside"

    result = ensure_channel_style_guide(settings, "acct-1")

    assert result.created is False
    assert result.status == "path_escaped_vault"
    assert result.path is None
    assert not (tmp_path / "outside").exists()
