from __future__ import annotations

from pathlib import Path
import tomllib

from atnos.core.config import Settings


def test_short_pipeline_runtime_dependencies_are_declared() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = {item.split(">=", maxsplit=1)[0].split("[", maxsplit=1)[0].casefold() for item in payload["project"]["dependencies"]}

    assert "apscheduler" in dependencies
    assert "cryptography" in dependencies
    assert "google-api-python-client" in dependencies
    assert "google-auth" in dependencies
    assert "google-auth-oauthlib" in dependencies
    assert "huggingface_hub" in dependencies
    assert "imageio-ffmpeg" in dependencies
    assert "pytrends" in dependencies
    assert "python-dotenv" in dependencies
    assert "pyttsx3" in dependencies


def test_short_pipeline_env_example_exposes_operational_infra_keys() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")

    assert "SLACK_NOTIFICATIONS_ENABLED=true" in text
    assert "SLACK_WEBHOOK_URL=" in text
    assert "SHORTS_TTS_BACKEND=f5_tts_http" in text
    assert "F5_TTS_BASE_URL=http://localhost:7860/api/predict" in text
    assert "COQUI_TTS_MODEL=tts_models/pl/mai_female/vits" in text
    secret_file_line = next(
        line for line in text.splitlines() if line.startswith("YOUTUBE_CLIENT_SECRET_FILE=")
    )
    secret_file = Path(secret_file_line.partition("=")[2])
    assert secret_file.is_absolute()
    assert secret_file.name == "youtube_client_secret.json"
    assert "runtime-recovered" not in secret_file.parts
    assert "OAUTH_ENABLED=false" in text
    assert "OBSIDIAN_VAULT_PATH=" in text


def test_settings_accepts_f5_tts_backend_from_env_example(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHORTS_TTS_BACKEND", "f5_tts_http")

    settings = Settings()

    assert settings.shorts_tts_backend == "f5_tts_http"
    assert settings.f5_tts_base_url == "http://localhost:7860/api/predict"
