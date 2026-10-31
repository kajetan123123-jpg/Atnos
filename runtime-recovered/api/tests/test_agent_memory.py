from __future__ import annotations

from pathlib import Path

from agent.memory_writer import AgentMemoryWriter
from agent.models import AgentRunResult, AgentRunStatus, AgentTaskType, RiskLevel
from atnos.core.config import Settings
from memory.agent_memory import AgentMemory, redact_sensitive


def _configure_memory_env(monkeypatch, tmp_path: Path, *, obsidian_enabled: bool = True) -> Path:
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    monkeypatch.setenv("ATNOS_OBSIDIAN_AGENT_DIR", "Atnos/Agent Logs")
    monkeypatch.setenv("ATNOS_OBSIDIAN_DECISION_DIR", "Atnos/Decision Log")
    monkeypatch.setenv("ATNOS_OBSIDIAN_SHORTS_DIR", "Atnos/Shorts")
    monkeypatch.setenv("ATNOS_WRITE_OBSIDIAN_LOGS", "true" if obsidian_enabled else "false")
    return vault


def test_write_agent_task_log_writes_local_jsonl_and_obsidian(monkeypatch, tmp_path: Path) -> None:
    vault = _configure_memory_env(monkeypatch, tmp_path)

    result = AgentMemory().write_agent_task_log(
        task="Sprawdz Ubuntu key=C:\\Users\\localuser\\.ssh\\atnos_lab api_key=sk-test12345678901234567890",
        plan=["check_ubuntu_connection"],
        tools_used=[{"name": "ubuntu_control", "private_key_path": "C:\\Users\\localuser\\.ssh\\atnos_lab"}],
        result={"status": "completed", "token": "secret-token"},
        risks=["sudo sandbox"],
        next_steps=["review report"],
    )

    local_path = Path(result["local_path"])
    jsonl_path = Path(result["jsonl_path"])
    obsidian_path = Path(result["obsidian_path"])

    assert local_path.exists()
    assert jsonl_path.exists()
    assert obsidian_path.exists()
    assert obsidian_path.is_relative_to(vault)

    markdown = local_path.read_text(encoding="utf-8")
    assert "# Agent Task Log" in markdown
    assert result["task_id"] in markdown
    assert "sk-test12345678901234567890" not in markdown
    assert "C:\\Users\\localuser\\.ssh\\atnos_lab" not in markdown
    assert "api_key=[REDACTED]" in markdown
    assert "[REDACTED_PRIVATE_KEY_PATH]" in markdown
    assert "[REDACTED]" in markdown

    jsonl = jsonl_path.read_text(encoding="utf-8")
    assert result["task_id"] in jsonl
    assert "sk-test12345678901234567890" not in jsonl


def test_write_decision_and_search_recent_logs(monkeypatch, tmp_path: Path) -> None:
    _configure_memory_env(monkeypatch, tmp_path)
    memory = AgentMemory()

    written = memory.write_decision(
        "Keep Ubuntu sandbox as full-access tool",
        "It is a disposable machine and every command is logged.",
        ["disable sudo", "manual only"],
    )
    results = memory.search_recent_agent_logs("Ubuntu")

    assert Path(written["local_path"]).exists()
    assert written["task_id"] in Path(written["local_path"]).read_text(encoding="utf-8")
    assert results
    assert any(item["task_id"] == written["task_id"] for item in results)


def test_obsidian_memory_bridge_writes_human_dashboard_and_agent_index(monkeypatch, tmp_path: Path) -> None:
    vault = _configure_memory_env(monkeypatch, tmp_path)
    memory = AgentMemory()

    written = memory.write_decision(
        "Obsidian is both human dashboard and Atnos operational memory",
        "The user wants to inspect what happens and Atnos needs compact context retrieval.",
        ["Obsidian only as archive", "JSONL only without human dashboard"],
    )
    results = memory.search_operational_memory("operational memory")

    index_path = Path(written["memory_index_path"])
    snapshot_path = index_path.with_name("obsidian_memory_snapshot.json")
    dashboard_path = vault / "Atnos" / "Memory" / "_Atnos Memory Index.md"

    assert index_path.exists()
    assert snapshot_path.exists()
    assert dashboard_path.exists()
    assert written["memory_id"] in index_path.read_text(encoding="utf-8")
    assert "Atnos Memory Index" in dashboard_path.read_text(encoding="utf-8")
    assert results
    assert results[0]["domain"] == "decision"


def test_obsidian_can_be_disabled_while_local_log_still_writes(monkeypatch, tmp_path: Path) -> None:
    _configure_memory_env(monkeypatch, tmp_path, obsidian_enabled=False)

    result = AgentMemory().write_short_production_note({"topic": "lokalne AI", "final_mp4": "final.mp4"})

    assert Path(result["local_path"]).exists()
    assert result["obsidian_path"] is None
    assert result["obsidian_status"] == "obsidian_logging_disabled"


def test_agent_memory_writer_integrates_with_agent_result(monkeypatch, tmp_path: Path) -> None:
    _configure_memory_env(monkeypatch, tmp_path)
    settings = Settings()
    writer = AgentMemoryWriter(settings)
    result = AgentRunResult(
        task="Zapamietaj wynik wiekszego zadania.",
        task_type=AgentTaskType.MEMORY,
        skill="memory",
        risk_level=RiskLevel.LOW,
        status=AgentRunStatus.COMPLETED,
        response="Zapisalem notatke.",
        approval_required=False,
    )

    assert writer.write(result) is True
    assert list((settings.data_dir / "memory" / "agent_logs").glob("*.md"))


def test_redact_sensitive_masks_private_key_paths_and_secret_fields() -> None:
    payload = {
        "api_key": "sk-test12345678901234567890",
        "path": "C:\\Users\\localuser\\.ssh\\atnos_lab",
        "nested": {"token": "abc"},
    }

    redacted = redact_sensitive(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["path"] == "[REDACTED_PRIVATE_KEY_PATH]"
    assert redacted["nested"]["token"] == "[REDACTED]"
