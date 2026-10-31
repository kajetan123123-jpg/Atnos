from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus  # noqa: E402
from atnos.db.models.execution_trace import ExecutionTrace  # noqa: E402
from atnos.db.models.project import ProjectMemory  # noqa: E402
from atnos.db.models.project_command_task import ProjectCommandTask  # noqa: E402
from atnos.llm.base import ToolCall  # noqa: E402
from atnos.repositories.project_repository import ProjectRepository  # noqa: E402
from atnos.repositories.message_repository import MessageRepository  # noqa: E402
from atnos.services.project_readiness_service import ProjectReadinessService  # noqa: E402
from atnos.services.runtime_health_service import RuntimeHealthService  # noqa: E402
from atnos.tools.base import BaseTool  # noqa: E402
from atnos.tools.builtin.file_inventory import FileInventoryTool  # noqa: E402
from atnos.tools.executor import ToolExecutor  # noqa: E402
from atnos.tools.registry import ToolRegistry  # noqa: E402
from test_support import create_test_app  # noqa: E402


def _runtime_ready(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return {
        "status": "ready",
        "components": [],
        "summary": {"ready": 1, "degraded": 0, "missing": 0, "blocked": 0},
    }


def _readiness_ready(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return {
        "generated_at": "2026-07-26T20:00:00Z",
        "projects": [
            {
                "project_id": "software:file-inventory-e2e",
                "name": "File inventory E2E",
                "status": "ready",
                "blockers": [],
                "next_action": "Run the verified local file inventory.",
                "operator_decision": {"state": "safe_local_verification"},
            }
        ],
    }


def test_file_inventory_command_creates_validated_artifact_trace_and_restart_memory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    large_file = tmp_path / "fixtures" / "large-model.bin"
    large_file.parent.mkdir(parents=True)
    with large_file.open("wb") as handle:
        handle.truncate(101 * 1024 * 1024)
    small_file = tmp_path / "fixtures" / "small.txt"
    small_file.write_text("small", encoding="utf-8")

    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create(
                "File inventory E2E",
                description="Real local tool execution and persistence proof.",
            ).id

        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={
                "content": (
                    "Znajdz pliki wieksze niz 100 MB i zapisz zweryfikowany "
                    "raport Markdown."
                )
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed", payload
        assert payload["intent"] == "file_inventory"
        assert payload["trace_status"] == "completed"
        assert payload["conversation_id"] > 0
        assert payload["user_message_id"] > 0
        assert payload["assistant_message_id"] > payload["user_message_id"]
        assert payload["task_id"] > 0
        assert payload["task_status"] == "completed"
        assert payload["verified"] is True
        assert payload["verification_status"] == "verified"
        tool_data = payload["data"]["tool"]
        assert tool_data["match_count"] == 1
        assert tool_data["matches"][0]["relative_path"] == "fixtures/large-model.bin"
        assert tool_data["validation"]["verified"] is True
        assert tool_data["validation"]["expected_count"] == 1
        assert tool_data["validation"]["observed_count"] == 1
        assert [step["status"] for step in payload["data"]["plan"]] == [
            "completed",
            "completed",
            "completed",
            "completed",
        ]

        artifact_path = Path(tool_data["artifact"]["absolute_path"])
        assert artifact_path.exists()
        assert artifact_path.stat().st_size > 0
        report = artifact_path.read_text(encoding="utf-8")
        assert "fixtures/large-model.bin" in report
        assert "fixtures/small.txt" not in report
        assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == tool_data["artifact"]["sha256"]

    with app.state.database.session_scope() as session:
        traces = list(
            session.scalars(
                select(ExecutionTrace)
                .where(ExecutionTrace.project_id == project_id)
                .order_by(ExecutionTrace.id.asc())
            )
        )
        assert any(
            item.trace_kind == ExecutionTraceKind.TOOL
            and item.status == ExecutionTraceStatus.COMPLETED
            for item in traces
        )
        assert any(
            item.trace_kind == ExecutionTraceKind.VERIFY
            and item.status == ExecutionTraceStatus.COMPLETED
            for item in traces
        )
        assert any(
            item.trace_kind == ExecutionTraceKind.MEMORY
            and item.status == ExecutionTraceStatus.COMPLETED
            for item in traces
        )
        memory = session.scalar(
            select(ProjectMemory).where(ProjectMemory.project_id == project_id)
        )
        assert memory is not None
        assert tool_data["artifact"]["sha256"] in memory.content
        messages = MessageRepository(session).list_for_conversation(
            payload["conversation_id"]
        )
        assert [item.role.value for item in messages] == ["user", "assistant"]
        assert messages[0].content.startswith("Znajdz pliki")
        assert tool_data["artifact"]["sha256"] in messages[1].content
        task = session.get(ProjectCommandTask, payload["task_id"])
        assert task is not None
        assert task.status == "completed"
        assert task.phase == "completed"
        assert task.attempt_count == 1
        assert [item["status"] for item in task.plan_json] == [
            "completed",
            "completed",
            "completed",
            "completed",
        ]
        assert task.result_json["data"]["tool"]["artifact"]["sha256"] == tool_data["artifact"]["sha256"]

    restarted_app = create_test_app(tmp_path)
    with TestClient(restarted_app) as restarted_client:
        overview = restarted_client.get(
            f"/api/project-command/{project_id}/overview"
        )
        assert overview.status_code == 200
        memories = overview.json()["memories"]
        assert any(
            tool_data["artifact"]["sha256"] in item["content"]
            for item in memories
        )

        conversation = restarted_client.get(
            f"/ui/chat/conversations/{payload['conversation_id']}"
        )
        assert conversation.status_code == 200
        persisted_messages = conversation.json()["messages"]
        assert [item["role"] for item in persisted_messages] == ["user", "assistant"]
        assert tool_data["artifact"]["sha256"] in persisted_messages[1]["content"]


def test_interrupted_file_inventory_task_is_resumed_after_application_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    large_file = tmp_path / "fixtures" / "resume-model.bin"
    large_file.parent.mkdir(parents=True)
    with large_file.open("wb") as handle:
        handle.truncate(101 * 1024 * 1024)

    app = create_test_app(tmp_path)
    with TestClient(app):
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("Resume inventory").id
            task = ProjectCommandTask(
                project_id=project_id,
                intent="file_inventory",
                status="running",
                phase="tool_execution",
                redacted_command="Znajdz pliki wieksze niz 100 MB i zapisz raport Markdown.",
                plan_json=[
                    {"step": "planning", "status": "completed"},
                    {"step": "tool_execution", "status": "running"},
                    {"step": "independent_validation", "status": "pending"},
                    {"step": "memory_writeback", "status": "pending"},
                ],
                evidence_json=[],
                attempt_count=1,
            )
            session.add(task)
            session.flush()
            task_id = task.id

    restarted_app = create_test_app(tmp_path)
    with TestClient(restarted_app) as client:
        response = client.post(f"/api/project-command/{project_id}/tasks/{task_id}/resume")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["task_status"] == "completed"
        assert payload["resumed"] is True
        assert payload["data"]["tool"]["validation"]["verified"] is True

    with restarted_app.state.database.session_scope() as session:
        persisted = session.get(ProjectCommandTask, task_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.attempt_count == 2
        assert persisted.completed_at is not None
        assert persisted.result_json["status"] == "completed"


def test_completed_file_inventory_task_cannot_be_resumed(
    tmp_path: Path,
) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("Completed inventory").id
            task = ProjectCommandTask(
                project_id=project_id,
                intent="file_inventory",
                status="completed",
                phase="completed",
                redacted_command="Znajdz duze pliki.",
                plan_json=[],
                evidence_json=[],
            )
            session.add(task)
            session.flush()
            task_id = task.id

        response = client.post(f"/api/project-command/{project_id}/tasks/{task_id}/resume")
        assert response.status_code == 400
        assert "nie wymaga wznowienia" in response.json()["detail"]


def test_file_inventory_tool_rejects_root_outside_atnos_boundaries(
    tmp_path: Path,
) -> None:
    app = create_test_app(tmp_path / "app")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("must not be scanned", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_tool(FileInventoryTool(app.state.settings))

    result = asyncio.run(
        ToolExecutor(registry).execute_tool_call(
            ToolCall(
                name="file_inventory_report",
                arguments={
                    "root": str(outside),
                    "minimum_size_bytes": 1,
                },
            )
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "PermissionError" in result.error
    outputs = app.state.settings.data_dir / "atnos-outputs"
    assert not list(outputs.rglob("*.md"))


def test_file_inventory_tool_prunes_a_vanished_directory_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    root = app.state.settings.app_root

    def _walk(scan_root, *, followlinks, onerror):  # noqa: ANN001
        assert Path(scan_root) == root
        assert followlinks is False
        dirnames = ["vanished-platform-link"]
        yield str(root), dirnames, []
        if dirnames:
            onerror(FileNotFoundError(str(root / dirnames[0])))

    monkeypatch.setattr(os, "walk", _walk)

    assert FileInventoryTool._scan(root, 1) == []


def test_file_inventory_tool_fails_when_an_entry_cannot_be_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    unreadable = app.state.settings.app_root / "unreadable.bin"
    unreadable.write_bytes(b"x")
    original_stat = Path.stat

    def _guarded_stat(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path.resolve() == unreadable.resolve():
            raise PermissionError("test access denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _guarded_stat)
    registry = ToolRegistry()
    registry.register_tool(FileInventoryTool(app.state.settings))

    result = asyncio.run(
        ToolExecutor(registry).execute_tool_call(
            ToolCall(
                name="file_inventory_report",
                arguments={
                    "root": str(app.state.settings.app_root),
                    "minimum_size_bytes": 1,
                },
            )
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "PermissionError" in result.error
    assert "test access denied" in result.error


def test_file_inventory_tool_ignores_entry_removed_during_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    transient = app.state.settings.app_root / "intelligence.db-wal"
    transient.write_bytes(b"x")
    original_stat = Path.stat

    def _transient_stat(path: Path, *args, **kwargs):  # noqa: ANN002, ANN003
        if path.resolve() == transient.resolve():
            raise FileNotFoundError("transient SQLite WAL disappeared")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _transient_stat)
    registry = ToolRegistry()
    registry.register_tool(FileInventoryTool(app.state.settings))

    result = asyncio.run(
        ToolExecutor(registry).execute_tool_call(
            ToolCall(
                name="file_inventory_report",
                arguments={
                    "root": str(app.state.settings.app_root),
                    "minimum_size_bytes": 1,
                },
            )
        )
    )

    assert result.success is True
    assert all(
        item["relative_path"] != transient.name
        for item in result.data["matches"]
    )
    assert result.data["validation"]["verified"] is True


def test_file_inventory_tool_rejects_and_removes_invalid_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    (app.state.settings.app_root / "large.bin").write_bytes(b"x" * 1024)
    monkeypatch.setattr(
        FileInventoryTool,
        "_render_report",
        staticmethod(lambda *_args, **_kwargs: "# corrupted report\n"),
    )
    registry = ToolRegistry()
    registry.register_tool(FileInventoryTool(app.state.settings))

    result = asyncio.run(
        ToolExecutor(registry).execute_tool_call(
            ToolCall(
                name="file_inventory_report",
                arguments={
                    "root": str(app.state.settings.app_root),
                    "minimum_size_bytes": 1,
                },
            )
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "validation failed" in result.error.lower()
    outputs = app.state.settings.data_dir / "atnos-outputs"
    assert not list(outputs.rglob("*.md"))


class _SlowTool(BaseTool):
    name = "slow_test_tool"
    description = "Sleeps long enough to exercise the executor timeout."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **_kwargs):
        await asyncio.sleep(0.2)
        return {"completed": True}


def test_tool_executor_enforces_timeout() -> None:
    registry = ToolRegistry()
    registry.register_tool(_SlowTool())

    result = asyncio.run(
        ToolExecutor(registry, timeout_seconds=0.01).execute_tool_call(
            ToolCall(name="slow_test_tool", arguments={})
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "TimeoutError" in result.error
    assert "0.01 seconds" in result.error
