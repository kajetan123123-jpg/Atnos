from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from sqlalchemy import select

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.ai_workers.delegation_router import (  # noqa: E402
    AiDelegationRouter,
    DelegateTaskInput,
)
from atnos.ai_workers.ollama_worker import OllamaWorker  # noqa: E402
from atnos.core.config import Settings  # noqa: E402
from atnos.db.base import Base  # noqa: E402
from atnos.db.models.ai_worker_task import AiWorkerRun, AiWorkerTask  # noqa: E402, F401
from atnos.db.models.execution_trace import ExecutionTrace  # noqa: E402
from atnos.db.models.project import Project, ProjectMemory  # noqa: E402
from atnos.db.session import DatabaseManager  # noqa: E402
from atnos.repositories.ai_worker_repository import AiWorkerRepository  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify real Ollama delegation and durable task persistence."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Directory outside the repository for the evidence bundle.",
    )
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    return parser.parse_args()


async def _run_live_task(settings: Settings) -> tuple[dict[str, object], dict[str, object]]:
    worker = OllamaWorker(settings)
    status = await worker.status()
    if not status.ready:
        raise RuntimeError(f"Ollama is not ready: {status.status}: {status.reason}")

    with DatabaseManager(settings).session_scope() as session:
        project = Project(
            name="Wealth Blueprint Live Verification",
            slug="wealth-blueprint-live-verification",
        )
        session.add(project)
        session.flush()
        router = AiDelegationRouter(session, settings)
        task = await router.delegate(
            DelegateTaskInput(
                task_type="review",
                prompt=(
                    "Review this English Shorts hook for clarity and tension. "
                    "Return exactly two concise bullet points: "
                    "'Most people misunderstand compound interest until this happens.'"
                ),
                context={
                    "project": "Wealth Blueprint",
                    "language": "English",
                    "purpose": "live local delegation verification",
                },
                preferred_worker="ollama",
                allowed_workers=("ollama",),
                privacy_mode="strict_local",
                priority="normal",
                requested_by="live-verifier",
                requires_approval_for_result=False,
                project_id=int(project.id),
            )
        )
        return status.to_dict(), task


async def _missing_model_check(settings: Settings) -> dict[str, object]:
    missing_settings = settings.model_copy(
        update={
            "ollama_fast_model": "atnos-intentionally-missing-model:latest",
            "ollama_chat_model": "atnos-intentionally-missing-model:latest",
        }
    )
    return (await OllamaWorker(missing_settings).status()).to_dict()


def _persisted_task(settings: Settings, task_id: int) -> dict[str, object]:
    database = DatabaseManager(settings)
    try:
        with database.session_scope() as session:
            task = AiWorkerRepository(session).get_task(task_id)
            if task is None:
                raise RuntimeError(f"Persisted AI worker task {task_id} was not found.")
            trace = session.get(ExecutionTrace, task.trace_id) if task.trace_id is not None else None
            memories = (
                list(
                    session.scalars(
                        select(ProjectMemory)
                        .where(ProjectMemory.project_id == trace.project_id)
                        .order_by(ProjectMemory.id.asc())
                    )
                )
                if trace is not None and trace.project_id is not None
                else []
            )
            return {
                "id": task.id,
                "status": task.status,
                "worker_key": task.worker_key,
                "verification_status": task.verification_status,
                "trace_id": task.trace_id,
                "trace": (
                    {
                        "id": trace.id,
                        "project_id": trace.project_id,
                        "status": trace.status.value,
                        "step_name": trace.step_name,
                    }
                    if trace is not None
                    else None
                ),
                "project_memory": [
                    {
                        "id": memory.id,
                        "kind": memory.memory_kind.value,
                        "content": memory.content,
                    }
                    for memory in memories
                ],
                "result_summary": task.result_summary,
                "result_json": task.result_json,
                "runs": [
                    {
                        "id": run.id,
                        "status": run.status,
                        "worker_key": run.worker_key,
                        "duration_ms": run.duration_ms,
                        "completed_at": (
                            run.completed_at.astimezone(UTC).isoformat()
                            if run.completed_at
                            else None
                        ),
                    }
                    for run in task.runs
                ],
            }
    finally:
        database.dispose()


def main() -> int:
    args = _parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.output_root.resolve() / f"ai-delegation-live-{timestamp}"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    settings = Settings(
        app_env="test",
        app_root=API_ROOT,
        data_dir=evidence_dir / "data",
        sqlite_file=evidence_dir / "data" / "ai-delegation-live.db",
        ollama_base_url=args.ollama_url,
        ollama_fast_model=args.model,
        ollama_chat_model=args.model,
    )
    settings.ensure_directories()

    database = DatabaseManager(settings)
    try:
        Base.metadata.create_all(database.engine)
    finally:
        database.dispose()

    worker_status, task = asyncio.run(_run_live_task(settings))
    persisted = _persisted_task(settings, int(task["id"]))
    missing_model = asyncio.run(_missing_model_check(settings))

    result_json = persisted.get("result_json")
    result_content = (
        str(result_json.get("content") or "")
        if isinstance(result_json, dict)
        else ""
    )
    checks = {
        "worker_ready": worker_status.get("ready") is True,
        "task_completed": persisted["status"] == "completed",
        "verification_completed": persisted["verification_status"] == "completed",
        "result_non_empty": bool(result_content.strip()),
        "result_differs_from_prompt": (
            "Most people misunderstand compound interest until this happens."
            not in result_content.strip()
        ),
        "run_completed": (
            len(persisted["runs"]) == 1
            and persisted["runs"][0]["status"] == "completed"
        ),
        "restart_persistence": persisted["id"] == task["id"],
        "trace_persisted": bool(persisted.get("trace_id") and persisted.get("trace")),
        "project_memory_written": bool(persisted.get("project_memory")),
        "missing_model_blocked": (
            missing_model.get("ready") is False
            and missing_model.get("status") == "missing_model"
        ),
    }
    report = {
        "status": "verified" if all(checks.values()) else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "ollama_url": args.ollama_url,
        "database": str(settings.sqlite_file),
        "worker_status": worker_status,
        "task": persisted,
        "negative_check": missing_model,
        "checks": checks,
        "limitations": [
            "Transport and persistence are verified; semantic correctness still requires independent review.",
            "The isolated database contains no production credentials or project secrets.",
        ],
    }
    report_path = evidence_dir / "ai-delegation-live-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report_path)
    print(report["status"])
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
