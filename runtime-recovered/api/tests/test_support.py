from __future__ import annotations

from pathlib import Path
import sys
import tempfile

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.api.main import create_app  # noqa: E402
from atnos.core.config import Settings  # noqa: E402
from atnos.db.migrations import upgrade_database  # noqa: E402
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository  # noqa: E402
from atnos.repositories.user_repository import UserRepository  # noqa: E402
from atnos.services.bootstrap_service import BootstrapService  # noqa: E402


def build_test_settings(root: Path, *, scheduled_tasks_enabled: bool = False) -> Settings:
    data_dir = root / "data"
    uploads_dir = data_dir / "uploads"
    sqlite_file = data_dir / "atnos-test.db"
    settings = Settings(
        app_env="test",
        app_root=root,
        data_dir=data_dir,
        runtime_dir_override=root / "runtime",
        uploads_dir=uploads_dir,
        sqlite_file=sqlite_file,
        comfyui_install_dir=root / "ComfyUI_windows_portable",
        comfyui_video_output_grace_s=0,
        kokoro_model_path=data_dir / "models" / "kokoro" / "model.onnx",
        kokoro_voices_path=data_dir / "models" / "kokoro" / "voices-v1.0.bin",
        scheduled_tasks_enabled=scheduled_tasks_enabled,
        visual_qa_dir=data_dir / "visual-qa",
        visual_qa_log_path=data_dir / "logs" / "visual_qa.jsonl",
        self_improvement_handoff_dir=data_dir / "self-improvement" / "codex-handoff",
        self_improvement_log_path=data_dir / "logs" / "self_improvement_governance.jsonl",
        shorts_output_dir=data_dir / "shorts",
        shorts_tts_backend="f5_tts_http",
        shorts_allow_cloud_tts=False,
        f5_tts_install_dir=None,
        f5_tts_runtime_python=None,
        f5_tts_launch_command="",
        f5_tts_ref_audio_path=None,
        openai_api_key="",
        elevenlabs_api_key=None,
    )
    settings.ensure_directories()
    return settings


def create_test_app(root: Path, *, scheduled_tasks_enabled: bool = False):
    settings = build_test_settings(root, scheduled_tasks_enabled=scheduled_tasks_enabled)
    return create_app(settings=settings)


def bootstrap_test_app(app) -> None:
    app.state.database.initialize()
    upgrade_database(app.state.settings)
    with app.state.database.session_scope() as session:
        BootstrapService(session, app.state.settings).ensure_local_first_defaults()


def enable_local_file_write(app) -> None:
    with app.state.database.session_scope() as session:
        owner = UserRepository(session).get_owner()
        assert owner is not None
        policies = ExecutionPolicyRepository(session)
        policy = policies.get_for_user(owner.id)
        assert policy is not None
        policies.update(policy, allow_local_file_write=True)


def build_pdf(path: Path, *lines: str) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 24
    pdf.save()


def make_temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="atnos-api-tests-")
