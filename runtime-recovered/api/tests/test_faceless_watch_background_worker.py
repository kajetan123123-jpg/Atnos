from __future__ import annotations

import importlib.util
from pathlib import Path


class _FakeWatchResult:
    def __init__(self, *, ready_count: int, final_videos: list[str]) -> None:
        self.ready_count = ready_count
        self.final_videos = final_videos

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "ready_for_owner_review",
            "ready_count": self.ready_count,
            "failed_count": 0,
            "final_videos": self.final_videos,
            "report_path": "F:/AtnosSystem/AppData/Roaming/Atnos/data/reports/test.md",
        }


class _FakeWatchService:
    def __init__(self, result: _FakeWatchResult | None = None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[dict[str, object]] = []

    def run_once(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


def test_background_worker_writes_completed_status(tmp_path: Path) -> None:
    module = _load_worker_module()
    status_path = tmp_path / "status.json"
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "output"
    stdout_log = tmp_path / "out.log"
    stderr_log = tmp_path / "err.log"
    config_path.write_text("{}", encoding="utf-8")
    service = _FakeWatchService(
        _FakeWatchResult(
            ready_count=1,
            final_videos=["F:/AtnosSystem/AppData/Roaming/Atnos/data/final.mp4"],
        )
    )

    code = module.run_worker(
        config_json=config_path,
        status_json=status_path,
        output_dir=output_dir,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        prompt_only=True,
        no_render_video=True,
        service=service,
    )

    payload = module.json.loads(status_path.read_text(encoding="utf-8"))
    assert code == 0
    assert payload["status"] == "completed"
    assert payload["started_at"]
    assert payload["completed_at"]
    assert payload["stdout_log"] == str(stdout_log.resolve())
    assert payload["stderr_log"] == str(stderr_log.resolve())
    assert payload["result"]["ready_count"] == 1
    assert service.calls[0]["generate_images"] is False
    assert service.calls[0]["render_video"] is False


def test_background_worker_writes_failure_status(tmp_path: Path) -> None:
    module = _load_worker_module()
    status_path = tmp_path / "status.json"
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "output"
    config_path.write_text("{}", encoding="utf-8")
    service = _FakeWatchService(exc=RuntimeError("boom"))

    code = module.run_worker(
        config_json=config_path,
        status_json=status_path,
        output_dir=output_dir,
        service=service,
    )

    payload = module.json.loads(status_path.read_text(encoding="utf-8"))
    assert code == 1
    assert payload["status"] == "failed"
    assert payload["started_at"]
    assert payload["completed_at"]
    assert payload["error"]["type"] == "RuntimeError"
    assert "boom" in payload["error"]["message"]


def _load_worker_module():
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_faceless_watch_session_background_worker.py"
    )
    spec = importlib.util.spec_from_file_location("faceless_watch_background_worker", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
