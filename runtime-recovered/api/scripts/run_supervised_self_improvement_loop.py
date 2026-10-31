from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import time

from atnos.core.config import get_settings
from atnos.db.session import DatabaseManager
from atnos.services.self_improvement_governance_service import SelfImprovementGovernanceService


APP_ROOT = Path(__file__).resolve().parents[3]


QUALITY_TOPICS = (
    {
        "name": "chat_status_transparency",
        "focus": "Chat must show real agent activity, status, artifacts and next step without debug clutter.",
        "codex_goal": "Improve user trust: make ongoing work visible, stop fake 'generating' ambiguity, preserve simple Chat UX.",
    },
    {
        "name": "content_quality",
        "focus": "Short/content outputs need sharper hooks, less generic scripting, better QA and repair before render.",
        "codex_goal": "Improve content quality gates and production reports before adding new content features.",
    },
    {
        "name": "privacy_storage",
        "focus": "All Atnos artifacts must stay in the configured external E: profile or Ubuntu sandbox, with cloud review opt-in only.",
        "codex_goal": "Find storage/privacy regressions and patch only concrete leaks or unsafe defaults.",
    },
    {
        "name": "daily_ux_speed",
        "focus": "Daily use should be faster: fewer clicks, better composer, clear project/model/persona state.",
        "codex_goal": "Reduce user friction without adding new navigation or debug surfaces.",
    },
    {
        "name": "routing_and_foundation",
        "focus": "Provider routing, strict_local privacy and agent/tool logs must remain reliable under changes.",
        "codex_goal": "Fix reliability gaps before adding more automation.",
    },
)


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: list[str]
    cwd: str
    exit_code: int
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle: int
    topic: str
    started_at: str
    finished_at: str
    note_id: int | None
    handoff_path: str | None
    report_path: str | None
    learning_brief_path: str
    checks: list[CommandResult]
    status: str


def main() -> None:
    args = parse_args()
    settings = get_settings()
    settings.ensure_directories()

    loop_dir = settings.self_improvement_handoff_dir.parent / "education-quality-loop"
    reports_dir = loop_dir / "reports"
    briefs_dir = loop_dir / "learning-briefs"
    loop_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    briefs_dir.mkdir(parents=True, exist_ok=True)

    log_path = loop_dir / "loop.jsonl"
    heartbeat_path = loop_dir / "heartbeat.json"
    pid_path = loop_dir / "loop.pid"
    stop_path = loop_dir / "STOP"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    deadline = datetime.now() + timedelta(hours=args.duration_hours)
    write_json(
        heartbeat_path,
        {
            "status": "running",
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(),
            "deadline": deadline.isoformat(),
            "interval_minutes": args.interval_minutes,
            "stop_file": str(stop_path),
        },
    )

    cycle = 0
    while datetime.now() < deadline:
        if stop_path.exists():
            append_log(log_path, {"event": "stopped_by_stop_file", "timestamp": datetime.now().isoformat()})
            break
        cycle += 1
        result = run_cycle(
            cycle=cycle,
            settings=settings,
            reports_dir=reports_dir,
            briefs_dir=briefs_dir,
            run_checks=not args.no_checks,
            dry_run=args.dry_run,
        )
        append_log(log_path, {"event": "cycle_complete", **cycle_to_dict(result)})
        write_json(
            heartbeat_path,
            {
                "status": result.status,
                "pid": os.getpid(),
                "cycle": cycle,
                "topic": result.topic,
                "last_finished_at": result.finished_at,
                "deadline": deadline.isoformat(),
                "last_handoff_path": result.handoff_path,
                "stop_file": str(stop_path),
            },
        )
        if args.once:
            break
        sleep_until_next_cycle(args.interval_minutes, deadline, stop_path)

    write_json(
        heartbeat_path,
        {
            "status": "finished",
            "pid": os.getpid(),
            "finished_at": datetime.now().isoformat(),
            "deadline": deadline.isoformat(),
            "stop_file": str(stop_path),
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run supervised Atnos self-improvement loop.")
    parser.add_argument("--duration-hours", type=float, default=10.0)
    parser.add_argument("--interval-minutes", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-checks", action="store_true")
    return parser.parse_args()


def run_cycle(
    *,
    cycle: int,
    settings,
    reports_dir: Path,
    briefs_dir: Path,
    run_checks: bool,
    dry_run: bool,
) -> CycleResult:
    started_at = datetime.now()
    topic = QUALITY_TOPICS[(cycle - 1) % len(QUALITY_TOPICS)]
    checks: list[CommandResult] = []

    if run_checks:
        checks.append(run_storage_assert())
        checks.append(run_targeted_pytest())

    note_id: int | None = None
    handoff_path: str | None = None
    report_path: str | None = None
    if not dry_run:
        db = DatabaseManager(settings)
        with db.session_scope() as session:
            service = SelfImprovementGovernanceService(session, settings=settings)
            note = service.seed_supervised_program()
            package = service.write_codex_handoff(note_id=note.id)
            note_id = note.id
            handoff_path = str(package.handoff_path)
            report_path = str(package.report_path)

    learning_brief_path = write_learning_brief(
        cycle=cycle,
        topic=topic,
        settings=settings,
        briefs_dir=briefs_dir,
        note_id=note_id,
        handoff_path=handoff_path,
        checks=checks,
    )

    status = "passed" if all(check.exit_code == 0 for check in checks) else "needs_codex_review"
    if dry_run:
        status = f"dry_run_{status}"
    result = CycleResult(
        cycle=cycle,
        topic=topic["name"],
        started_at=started_at.isoformat(),
        finished_at=datetime.now().isoformat(),
        note_id=note_id,
        handoff_path=handoff_path,
        report_path=report_path,
        learning_brief_path=str(learning_brief_path),
        checks=checks,
        status=status,
    )
    report = reports_dir / f"cycle_{cycle:03d}_{topic['name']}.json"
    write_json(report, cycle_to_dict(result))
    return result


def run_storage_assert() -> CommandResult:
    script = APP_ROOT / "scripts" / "assert_atnos_storage.ps1"
    return run_command(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Strict"],
        cwd=APP_ROOT,
        timeout_s=120,
    )


def run_targeted_pytest() -> CommandResult:
    api_root = APP_ROOT / "runtime-recovered" / "api"
    python = api_root / ".venv" / "Scripts" / "python.exe"
    tests = (
        "tests/test_self_improvement_governance.py",
        "tests/test_visual_qa_agent_api.py",
        "tests/test_visual_qa_services.py",
        "tests/test_agent_control_panel_api.py",
    )
    return run_command(
        [str(python), "-m", "pytest", *tests, "-q", "--tb=short"],
        cwd=api_root,
        timeout_s=240,
    )


def run_command(command: list[str], *, cwd: Path, timeout_s: int) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return CommandResult(
            command=command,
            cwd=str(cwd),
            exit_code=int(completed.returncode),
            stdout_tail=tail(completed.stdout),
            stderr_tail=tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=command,
            cwd=str(cwd),
            exit_code=124,
            stdout_tail=tail(exc.stdout or ""),
            stderr_tail=tail(exc.stderr or "timeout"),
        )


def write_learning_brief(
    *,
    cycle: int,
    topic: dict[str, str],
    settings,
    briefs_dir: Path,
    note_id: int | None,
    handoff_path: str | None,
    checks: list[CommandResult],
) -> Path:
    path = briefs_dir / f"cycle_{cycle:03d}_{topic['name']}.md"
    failed = [check for check in checks if check.exit_code != 0]
    lines = [
        "# Atnos Education + Quality Loop",
        "",
        f"- cycle: {cycle}",
        f"- generated_at: {datetime.now().isoformat()}",
        f"- topic: {topic['name']}",
        f"- note_id: {note_id if note_id is not None else 'dry-run'}",
        f"- handoff_path: {handoff_path or 'not-created'}",
        "",
        "## Learning Focus",
        "",
        topic["focus"],
        "",
        "## Codex Goal",
        "",
        topic["codex_goal"],
        "",
        "## Rules",
        "",
        "- Atnos prepares evidence and draft plans.",
        "- Codex makes final code/product decisions.",
        "- No public publishing, billing actions, or secret exposure.",
        "- Store all artifacts on F: or Ubuntu sandbox.",
        "",
        "## Check Summary",
        "",
    ]
    if not checks:
        lines.append("- checks: skipped")
    for check in checks:
        lines.append(f"- exit_code={check.exit_code} command={' '.join(check.command)}")
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            (
                "Codex should inspect failed checks before applying any patch."
                if failed
                else "Codex may inspect the latest handoff and apply only a focused quality improvement."
            ),
            "",
            "## Storage",
            "",
            f"- app_root: {settings.app_root}",
            f"- data_dir: {settings.data_dir}",
            f"- handoff_dir: {settings.self_improvement_handoff_dir}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def sleep_until_next_cycle(interval_minutes: float, deadline: datetime, stop_path: Path) -> None:
    remaining_s = max(0.0, min(interval_minutes * 60.0, (deadline - datetime.now()).total_seconds()))
    end = time.monotonic() + remaining_s
    while time.monotonic() < end:
        if stop_path.exists():
            return
        time.sleep(min(10.0, max(0.1, end - time.monotonic())))


def cycle_to_dict(result: CycleResult) -> dict[str, object]:
    payload = asdict(result)
    payload["checks"] = [asdict(check) for check in result.checks]
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def tail(text: str, max_chars: int = 3000) -> str:
    return (text or "")[-max_chars:]


if __name__ == "__main__":
    main()
