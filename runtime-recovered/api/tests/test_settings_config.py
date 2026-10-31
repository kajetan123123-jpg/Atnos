from __future__ import annotations

import os
from pathlib import Path

from atnos.core.config import Settings, load_runtime_env_files


def test_runtime_paths_can_be_overridden_from_environment(monkeypatch, tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    data_dir = tmp_path / "roaming" / "data"
    runtime_dir = tmp_path / "roaming" / "runtime"
    uploads_dir = data_dir / "uploads"
    sqlite_file = data_dir / "atnos.db"

    monkeypatch.setenv("APP_ROOT", str(app_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("SQLITE_FILE", str(sqlite_file))
    monkeypatch.setenv("RUNTIME_DIR", str(runtime_dir))

    settings = Settings()

    assert settings.app_root == app_root.resolve()
    assert settings.data_dir == data_dir.resolve()
    assert settings.uploads_dir == uploads_dir.resolve()
    assert settings.sqlite_file == sqlite_file.resolve()
    assert settings.runtime_dir == runtime_dir.resolve()
    assert settings.startup_status_file == (runtime_dir / "api-startup-status.json").resolve()
    assert settings.startup_events_file == (runtime_dir / "api-startup-events.jsonl").resolve()


def test_legacy_atnos_state_dir_selects_the_persistent_runtime_data(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "AtnosSystem" / "data"

    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("ATNOS_STATE_DIR", str(state_dir))

    settings = Settings()

    assert settings.data_dir == state_dir.resolve()
    assert settings.sqlite_file == (state_dir / "atnos.db").resolve()
    assert settings.account_secret_vault_dir == (state_dir / "account-secrets").resolve()


def test_load_runtime_env_files_never_reads_repository_env(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "Atnos"
    api_root = project_root / "runtime-recovered" / "api"
    api_root.mkdir(parents=True)
    env_path = api_root / ".env"
    env_path.write_text(
        "OAUTH_ENABLED=true\n"
        "OLLAMA_BASE_URL=http://127.0.0.1:11435\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state" / "data"
    monkeypatch.chdir(project_root)
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("ATNOS_ENV_FILE", raising=False)
    monkeypatch.setenv("ATNOS_STATE_DIR", str(state_dir))
    monkeypatch.delenv("COMFYUI_GENERATION_TIMEOUT_S", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://already-set:11434")
    monkeypatch.delenv("ATNOS_LOADED_ENV_FILES", raising=False)

    loaded = load_runtime_env_files()

    assert loaded == ()
    assert env_path.resolve() not in loaded
    assert Settings().oauth_enabled is False
    assert Settings().ollama_base_url == "http://already-set:11434"
    assert "ATNOS_LOADED_ENV_FILES" not in os.environ


def test_private_runtime_defaults_stay_under_state_dir(monkeypatch, tmp_path: Path) -> None:
    app_root = tmp_path / "repo"
    state_dir = tmp_path / "private" / "data"
    monkeypatch.setenv("ATNOS_STATE_DIR", str(state_dir))
    monkeypatch.delenv("DATA_DIR", raising=False)

    settings = Settings(app_root=app_root)

    assert settings.youtube_client_secret_file == (
        state_dir.parent / "config" / "oauth" / "youtube_client_secret.json"
    ).resolve()
    assert settings.knowledge_workspace_dir == (state_dir / "knowledge-workspace").resolve()
    assert settings.visual_qa_dir == (state_dir / "visual-qa").resolve()
    assert settings.self_improvement_handoff_dir == (
        state_dir / "self-improvement" / "codex-handoff"
    ).resolve()


def test_load_runtime_env_files_prefers_external_runtime_env(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "Atnos"
    data_dir = tmp_path / "roaming" / "data"
    external_env = data_dir.parent / "config" / ".env"
    external_env.parent.mkdir(parents=True)
    external_env.write_text("OAUTH_ENABLED=true\n", encoding="utf-8")
    project_root.mkdir()
    legacy_env = project_root / ".env"
    legacy_env.write_text("COMFYUI_GENERATION_TIMEOUT_S=180\n", encoding="utf-8")

    monkeypatch.chdir(project_root)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATNOS_ENV_FILE", str(external_env))
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)
    monkeypatch.delenv("COMFYUI_GENERATION_TIMEOUT_S", raising=False)
    monkeypatch.delenv("ATNOS_LOADED_ENV_FILES", raising=False)

    loaded = load_runtime_env_files()

    assert loaded[0] == external_env.resolve()
    assert legacy_env.resolve() not in loaded
    assert Settings().runtime_env_file == external_env.resolve()
    assert Settings().oauth_enabled is True
    assert Settings().comfyui_generation_timeout_s == 1200
