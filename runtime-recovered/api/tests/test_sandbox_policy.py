from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import Settings

from agent.models import RiskLevel
from agent.sandbox_policy import ActionTarget, emergency_stop_sandbox, is_action_allowed


def _settings(tmp_path: Path, monkeypatch) -> Settings:
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ATNOS_AUTONOMY_MODE", "full_auto_sandbox")
    monkeypatch.setenv("ATNOS_SANDBOX_HOST", "198.51.100.216")
    monkeypatch.setenv("ATNOS_SANDBOX_USER", "atnos")
    monkeypatch.setenv("ATNOS_SANDBOX_ALLOW_SUDO", "true")
    monkeypatch.setenv("ATNOS_SANDBOX_LOG_EVERYTHING", "true")
    monkeypatch.setenv("ATNOS_MAIN_PC_HIGH_RISK_APPROVAL", "true")
    monkeypatch.setenv("ATNOS_PUBLICATION_APPROVAL", "true")
    monkeypatch.setenv("ATNOS_SECRET_ACCESS_APPROVAL", "true")
    return Settings()


def test_full_auto_sandbox_allows_high_risk_ubuntu_action_and_logs(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, monkeypatch)

    decision = is_action_allowed(
        "sudo apt-get install -y ffmpeg",
        ActionTarget.UBUNTU_SANDBOX,
        RiskLevel.HIGH,
        settings=settings,
    )

    assert decision.allowed is True
    assert decision.requires_approval is False
    log_line = settings.agent_decisions_log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    payload = json.loads(log_line)
    assert payload["event"] == "sandbox_policy_decision"
    assert payload["target_type"] == ActionTarget.UBUNTU_SANDBOX


def test_main_pc_high_risk_requires_approval(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, monkeypatch)

    decision = is_action_allowed(
        "Remove-Item -Recurse F:\\Apps\\Atnos",
        ActionTarget.MAIN_PC,
        RiskLevel.HIGH,
        settings=settings,
    )

    assert decision.allowed is False
    assert decision.requires_approval is True


def test_publication_and_secret_access_require_approval(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, monkeypatch)

    publish_decision = is_action_allowed(
        "publish short to YouTube public",
        ActionTarget.PUBLICATION,
        RiskLevel.MEDIUM,
        settings=settings,
    )
    secret_decision = is_action_allowed(
        "read api_key=sk-test-secret",
        ActionTarget.SECRET,
        RiskLevel.LOW,
        settings=settings,
    )

    assert publish_decision.requires_approval is True
    assert secret_decision.requires_approval is True
    assert "sk-test-secret" not in settings.agent_decisions_log_path.read_text(encoding="utf-8")


def test_emergency_stop_calls_ubuntu_stop_without_real_ssh(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path, monkeypatch)

    def fake_stop_remote_atnos_processes(*, settings: Settings | None = None) -> dict[str, object]:
        return {"command": "sudo pkill -u atnos || true", "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("agent.sandbox_policy.stop_remote_atnos_processes", fake_stop_remote_atnos_processes)

    result = emergency_stop_sandbox(settings=settings)

    assert result["ok"] is True
    assert result["status"] == "stopped"
