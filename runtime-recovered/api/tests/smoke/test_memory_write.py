from __future__ import annotations

from pathlib import Path

from memory.agent_memory import AgentMemory


def test_memory_writer_saves_agent_task_note_without_touching_real_vault(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir(parents=True)

    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("ATNOS_OBSIDIAN_AGENT_DIR", "Atnos/Agent Logs")
    monkeypatch.setenv("ATNOS_WRITE_OBSIDIAN_LOGS", "true")

    result = AgentMemory().write_agent_task_log(
        task="Smoke memory write",
        plan=["write isolated test note"],
        tools_used=["agent_memory"],
        result={"status": "ok"},
        risks=[],
        next_steps=["keep smoke suite green"],
        task_id="smoke-memory",
    )

    local_path = Path(result["local_path"])
    obsidian_path = Path(result["obsidian_path"])
    jsonl_path = Path(result["jsonl_path"])

    assert local_path.exists()
    assert obsidian_path.exists()
    assert jsonl_path.exists()
    assert "# Agent Task Log" in local_path.read_text(encoding="utf-8")
    assert "smoke-memory" in local_path.read_text(encoding="utf-8")
