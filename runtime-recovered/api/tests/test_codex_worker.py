from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.ai_workers.codex_worker import CodexCommandOutcome, CodexWorker
from test_support import build_test_settings


def _configured_worker(tmp_path: Path, *, final_message: str | None = None) -> CodexWorker:
    settings = build_test_settings(tmp_path)
    cli = tmp_path / "tools" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// test cli\n", encoding="utf-8")
    settings.codex_cli_root = tmp_path / "tools"
    settings.codex_node_executable = Path(sys.executable)

    def runner(command, workdir: Path, timeout_s: int) -> CodexCommandOutcome:
        assert "--sandbox" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--skip-git-repo-check" in command
        assert "--ephemeral" in command
        assert "--json" in command
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            final_message or "Findings: no secret content was supplied. Next step: human review.",
            encoding="utf-8",
        )
        return CodexCommandOutcome(returncode=0, stdout='{"event":"completed"}', stderr="")

    return CodexWorker(settings, command_runner=runner)


def _configured_patch_worker(tmp_path: Path, *, expected_value: int) -> tuple[CodexWorker, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    tests_dir = repository / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        f"from sample import VALUE\n\ndef test_value():\n    assert VALUE == {expected_value}\n",
        encoding="utf-8",
    )
    git = CodexWorker._git_path()
    assert git is not None, "Codex patch lifecycle requires Git in the test runtime."
    for command in (
        [str(git), "init"],
        [str(git), "config", "user.email", "account-0fbe2b0e@example.invalid"],
        [str(git), "config", "user.name", "Atnos Tests"],
        [str(git), "add", "sample.py", "tests/test_sample.py"],
        [str(git), "commit", "-m", "test baseline"],
    ):
        completed = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr

    settings = build_test_settings(repository)
    settings.data_dir = tmp_path / "runtime-data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    cli = tmp_path / "tools" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// test cli\n", encoding="utf-8")
    settings.codex_cli_root = tmp_path / "tools"
    settings.codex_node_executable = Path(sys.executable)

    def runner(command, workdir: Path, timeout_s: int) -> CodexCommandOutcome:
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        (workdir / "sample.py").write_text("VALUE = 2\n", encoding="utf-8")
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            "Changed sample.py from VALUE=1 to VALUE=2; independent tests are delegated to Atnos.",
            encoding="utf-8",
        )
        return CodexCommandOutcome(returncode=0, stdout='{"event":"completed"}', stderr="")

    return CodexWorker(settings, command_runner=runner), repository


def test_codex_worker_runs_a_sanitized_read_only_bundle(tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)
    source = tmp_path / "runtime-recovered" / "api" / "src" / "atnos" / "sample.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("API_KEY = 'secret-token-value'\nprint('ok')\n", encoding="utf-8")
    secret_file = tmp_path / "runtime-recovered" / "api" / ".env"
    secret_file.write_text("PASSWORD=never-copy-me\n", encoding="utf-8")

    result = asyncio.run(
        worker.run(
            task_type="code_review",
            prompt="Review this module without changing files.",
            context={
                "source_files": [
                    "runtime-recovered/api/src/atnos/sample.py",
                    "runtime-recovered/api/.env",
                ]
            },
        )
    )

    assert result.status == "completed"
    assert result.result["execution_mode"] == "sanitized_bundle_read_only"
    assert result.result["copied_files"] == ["runtime-recovered/api/src/atnos/sample.py"]
    assert [item["phase"] for item in result.result["lifecycle"]] == [
        "prompt_prepared",
        "started",
        "running",
        "completed",
        "result_received",
    ]
    assert result.result["prompt_sha256"] != result.result["result_sha256"]
    assert result.result["prompt_equals_result"] is False
    bundle_path = Path(str(result.result["bundle_path"]))
    copied_text = (bundle_path / "source" / "runtime-recovered" / "api" / "src" / "atnos" / "sample.py").read_text(encoding="utf-8")
    assert "secret-token-value" not in copied_text
    assert not (bundle_path / "source" / "runtime-recovered" / "api" / ".env").exists()


def test_codex_worker_rejects_prompt_reused_as_result(tmp_path: Path) -> None:
    prompt = "Review this module and return independent evidence."
    worker = _configured_worker(tmp_path, final_message=prompt)

    result = asyncio.run(worker.run(task_type="code_review", prompt=prompt, context={}))

    assert result.status == "failed"
    assert result.result["prompt_equals_result"] is True
    assert result.result["prompt_sha256"] == result.result["result_sha256"]
    assert result.result["lifecycle"][-1]["phase"] == "verification_failed"
    assert result.result["lifecycle"][-1]["evidence"]["reason"] == "prompt_reused_as_result"


def test_codex_patch_lifecycle_verifies_real_diff_without_modifying_main_worktree(tmp_path: Path) -> None:
    worker, repository = _configured_patch_worker(tmp_path, expected_value=2)

    result = asyncio.run(
        worker.run(
            task_type="safe_code_fix",
            prompt="Change the scoped value from one to two.",
            context={
                "allowed_paths": ["sample.py"],
                "verification_command": [sys.executable, "-m", "pytest", "tests/test_sample.py", "-q"],
            },
        )
    )

    assert result.status == "completed"
    assert result.result["verification"]["status"] == "passed"
    assert [item["phase"] for item in result.result["lifecycle"]] == [
        "prompt_prepared",
        "started",
        "running",
        "completed",
        "result_received",
        "verifying",
        "verified",
    ]
    assert result.result["changed_paths"] == ["sample.py"]
    assert Path(result.result["diff_path"]).is_file()
    assert result.result["proposal_state"] == "awaiting_review"
    assert result.result["rollback"]["main_worktree_modified"] is False
    assert result.result["rollback"]["isolated_worktree_removed"] is True
    assert (repository / "sample.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_codex_patch_lifecycle_rejects_failed_independent_test(tmp_path: Path) -> None:
    worker, repository = _configured_patch_worker(tmp_path, expected_value=3)

    result = asyncio.run(
        worker.run(
            task_type="safe_code_fix",
            prompt="Change the scoped value from one to two.",
            context={
                "allowed_paths": ["sample.py"],
                "verification_command": [sys.executable, "-m", "pytest", "tests/test_sample.py", "-q"],
            },
        )
    )

    assert result.status == "failed"
    assert result.result["verification"]["status"] == "failed"
    assert result.result["lifecycle"][-1]["phase"] == "verification_failed"
    assert "Independent verification command failed" in result.result["verification"]["reason"]
    assert (repository / "sample.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_codex_worker_rejects_unsupported_task_type(tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)

    result = asyncio.run(worker.run(task_type="code_apply", prompt="Change a file.", context={}))

    assert result.status == "blocked"
    assert "permits only" in (result.blocked_reason or "")


def test_codex_patch_proposal_requires_explicit_safe_allowlist(tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)

    result = asyncio.run(
        worker.run(
            task_type="safe_code_fix",
            prompt="Fix a safe scoped bug.",
            context={"verification_command": [sys.executable, "-m", "pytest", "tests/test_codex_worker.py"]},
        )
    )

    assert result.status == "blocked"
    assert "allowed_paths" in (result.blocked_reason or "")


def test_codex_patch_proposal_rejects_changed_path_outside_allowlist(tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)

    invalid = worker._invalid_changed_paths(
        [
            "runtime-recovered/api/src/atnos/allowed.py",
            "runtime-recovered/api/.env",
        ],
        ["runtime-recovered/api/src/atnos/allowed.py"],
    )

    assert invalid == ["runtime-recovered/api/.env"]


def test_codex_patch_verification_environment_excludes_runtime_settings(monkeypatch, tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)
    monkeypatch.setenv("SHORTS_VIDEO_BACKEND", "invalid-backend")
    monkeypatch.setenv("ATNOS_ENV_FILE", "C:/private/.env")

    environment = worker._isolated_test_environment()

    assert "SHORTS_VIDEO_BACKEND" not in environment
    assert "ATNOS_ENV_FILE" not in environment


def test_codex_worker_reports_missing_cli_without_running(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    settings.codex_cli_root = tmp_path / "missing-cli"
    settings.codex_node_executable = Path(sys.executable)
    worker = CodexWorker(settings)

    status = asyncio.run(worker.status())
    result = asyncio.run(worker.run(task_type="code_review", prompt="Review.", context={}))

    assert status.ready is False
    assert status.status == "not_configured"
    assert result.status == "blocked"


def test_codex_worker_reports_command_timeout_as_failed_result(tmp_path: Path) -> None:
    worker = _configured_worker(tmp_path)

    def timeout_runner(command, workdir: Path, timeout_s: int) -> CodexCommandOutcome:
        raise subprocess.TimeoutExpired(command, timeout_s)

    worker.command_runner = timeout_runner
    result = asyncio.run(
        worker.run(
            task_type="code_review",
            prompt="Review one sanitized file and return evidence.",
            context={},
        )
    )

    assert result.status == "failed"
    assert "TimeoutExpired" in result.summary
    assert result.result["lifecycle"][-1]["phase"] == "verification_failed"
    assert result.result["lifecycle"][-1]["evidence"]["reason"] == "adapter_exception:TimeoutExpired"
