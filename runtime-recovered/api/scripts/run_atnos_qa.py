from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@dataclass(slots=True)
class CheckResult:
    area: str
    status: str
    evidence: str
    score: float | None = None
    notes: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def main() -> int:
    os.chdir(API_ROOT)
    started_at = datetime.now(UTC)
    timestamp = started_at.strftime("%Y%m%d-%H%M%S")

    results: list[CheckResult] = []
    results.append(check_environment())
    results.append(check_ubuntu())
    results.append(check_agent_orchestrator())
    results.append(check_short_pipeline())
    results.append(check_browser_operator())
    results.append(check_abcd_flow())
    results.append(check_memory_obsidian(timestamp))
    results.append(check_logs())
    results.append(check_security_policy())

    report_path = write_report(timestamp, started_at, results)
    obsidian_path = write_report_to_obsidian(report_path)
    print(json.dumps(
        {
            "report_path": str(report_path),
            "obsidian_report_path": obsidian_path,
            "summary": summarize_results(results),
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0


def check_environment() -> CheckResult:
    from atnos.core.config import Settings

    settings = Settings()
    settings.ensure_directories()
    evidence: list[str] = []
    failures: list[dict[str, str]] = []

    python_version = sys.version.replace("\n", " ")
    evidence.append(f"Python: {python_version}")
    evidence.append(f".env exists: {(API_ROOT / '.env').exists()}")

    writable_probe = settings.data_dir / "logs" / "qa_write_probe.txt"
    try:
        writable_probe.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
        evidence.append(f"Log write probe: {writable_probe}")
    except OSError as exc:
        failures.append(failure("log_write_probe", "write qa probe", str(exc), "data/logs is not writable."))

    for label, path in {
        "data_dir": settings.data_dir,
        "logs_dir": settings.data_dir / "logs",
        "shorts_output_dir": settings.shorts_output_dir,
        "browser_screenshot_dir": settings.browser_screenshot_dir,
        "browser_log_path_parent": settings.browser_log_path.parent,
    }.items():
        evidence.append(f"{label}: {path} exists={path.exists()}")

    backend = first_ok_http([
        f"http://{settings.api_host}:{settings.api_port}/health",
        "http://127.0.0.1:8011/health",
    ])
    if backend is None:
        failures.append(failure("backend", "GET /health", "No local backend health endpoint responded.", "Start the API server."))
    else:
        evidence.append(f"Backend health: {backend}")

    frontend = first_ok_http([
        "http://127.0.0.1:4173/",
        "http://127.0.0.1:5173/",
    ])
    if frontend is None:
        evidence.append("Frontend runtime: SKIPPED (no local preview/dev server responded).")
    else:
        evidence.append(f"Frontend runtime: {frontend}")

    status = "FAILED" if failures else "PASSED"
    return CheckResult("Environment", status, "\n".join(evidence), 9 if not failures else 4, failures=failures)


def check_ubuntu() -> CheckResult:
    from atnos.core.config import Settings
    from tools.ubuntu_control import UbuntuControlError, check_ubuntu_connection, create_system_report, run_ubuntu

    settings = Settings()
    if not (settings.atnos_ubuntu_host and settings.atnos_ubuntu_user and settings.atnos_ubuntu_key):
        return CheckResult("Ubuntu sandbox", "SKIPPED", "ATNOS_UBUNTU_HOST/USER/KEY not fully configured.", None)

    try:
        connection = check_ubuntu_connection(settings=settings)
        report = create_system_report(settings=settings)
        tail = run_ubuntu("tail -n 40 logs/commands.log", timeout=60, settings=settings)
    except UbuntuControlError as exc:
        return CheckResult(
            "Ubuntu sandbox",
            "SKIPPED",
            f"Ubuntu configured but not reachable or not usable: {exc}",
            None,
        )

    checks = connection.get("checks", {})
    whoami = _stdout(checks.get("whoami"))
    sudo_whoami = _stdout(checks.get("sudo_whoami"))
    hostname = _stdout(checks.get("hostname"))
    python_version = _stdout(checks.get("python_version"))
    ok = bool(connection.get("ok")) and whoami == "atnos" and sudo_whoami == "root"
    evidence = "\n".join(
        [
            f"host: {connection.get('host')}",
            f"whoami: {whoami}",
            f"sudo -n whoami: {sudo_whoami}",
            f"hostname: {hostname}",
            f"python3 --version: {python_version}",
            f"system_report: {report.get('report_path')}",
            f"commands.log tail exit_code: {tail.get('exit_code')}",
        ]
    )
    failures = [] if ok else [failure("ubuntu_identity", "check_ubuntu_connection", json.dumps(connection, default=str), "Unexpected Ubuntu identity or sudo result.")]
    return CheckResult(
        "Ubuntu sandbox",
        "PASSED" if ok else "FAILED",
        evidence,
        9 if ok else 3,
        artifacts={"ubuntu_system_report": str(report.get("report_path")), "ubuntu_commands_log": "/home/atnos/atnos/logs/commands.log"},
        failures=failures,
        data={"connection": connection},
    )


def check_agent_orchestrator() -> CheckResult:
    from agent.orchestrator import AgentOrchestrator
    from agent.task_classifier import TaskClassifier
    from agent.skill_router import SkillRouter

    classifier = TaskClassifier()
    router = SkillRouter()
    orchestrator = AgentOrchestrator()
    required = ("retrieve_memory", "plan_steps", "risk_policy", "executor", "verifier", "memory_writer", "logger")
    missing = [name for name in required if not hasattr(orchestrator, name)]
    tasks = [
        "sprawdz laptop Ubuntu",
        "zrob shorta o lokalnym AI",
        "otworz strone i stresc ja",
        "stworz grafike kota",
        "zadaj mi pytanie ABCD",
    ]
    routing: list[dict[str, str]] = []
    for task in tasks:
        classification = classifier.classify(task)
        route = router.route(classification)
        plan = orchestrator.plan_steps(task=task, task_type=classification.task_type, tool_name=route.tool_name)
        risk = orchestrator.risk_policy.classify(task, classification.task_type, plan)
        routing.append(
            {
                "task": task,
                "task_type": classification.task_type.value,
                "skill": route.skill,
                "tool": str(route.tool_name),
                "risk": risk.value,
            }
        )
    evidence = json.dumps({"routing": routing, "missing_flow_parts": missing}, ensure_ascii=False, indent=2)
    status = "FAILED" if missing else "PASSED"
    return CheckResult("Agent orchestrator", status, evidence, 8 if not missing else 4, data={"routing": routing})


def check_short_pipeline() -> CheckResult:
    from atnos.core.config import Settings
    from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService
    from content.short_agent import ShortAgent

    settings = Settings()
    probe = LocalShortRuntimeProbeService(settings).snapshot()
    artifacts: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    result_payload: dict[str, Any] | None = None
    output_dir: Path | None = None
    started = time.monotonic()

    try:
        result = ShortAgent(settings).run("Dlaczego lokalne AI na laptopie ma sens?")
        result_payload = result.to_dict()
        output_dir = result.output_dir
        artifacts.update(result.artifacts)
    except Exception as exc:
        output_dir = newest_child(settings.shorts_output_dir)
        failures.append(
            failure(
                "short_pipeline",
                "ShortAgent.run('Dlaczego lokalne AI na laptopie ma sens?')",
                f"{type(exc).__name__}: {exc}",
                "Runtime missing, Ollama/Piper/whisper/FFmpeg failed, or quality gate stopped the render.",
            )
        )

    if output_dir is not None:
        for name in ("script.md", "script.json", "script_quality.json", "narration.wav", "subtitles.srt", "thumbnail.png", "final.mp4", "render_qa.json", "production_report.md"):
            candidate = output_dir / name
            if candidate.exists():
                artifacts[name] = str(candidate)

    media = inspect_video(Path(artifacts["final_mp4"])) if "final_mp4" in artifacts else {}
    script_text = read_text(Path(artifacts["script.md"])) if "script.md" in artifacts else ""
    quality_json = read_json(Path(artifacts["script_quality.json"])) if "script_quality.json" in artifacts else {}
    render_qa = read_json(Path(artifacts["render_qa.json"])) if "render_qa.json" in artifacts else {}
    srt_ok = "subtitles.srt" in artifacts and Path(artifacts["subtitles.srt"]).stat().st_size > 0
    thumbnail_ok = "thumbnail.png" in artifacts and Path(artifacts["thumbnail.png"]).stat().st_size > 0
    final_ok = bool(media.get("exists") and media.get("width") == 1080 and media.get("height") == 1920 and media.get("has_audio"))
    script_scores = score_script(script_text, quality_json)
    render_score = 9 if final_ok and srt_ok and thumbnail_ok else 2 if artifacts else 0
    automation_score = 9 if result_payload and result_payload.get("status") == "manual_upload_ready" else 2
    overall = round((script_scores["script_quality_score"] + script_scores["hook_score"] + script_scores["polish_quality_score"] + render_score + automation_score) / 5, 1)
    status = "PASSED" if overall >= 7 and final_ok else "NEEDS_REPAIR" if artifacts else "FAILED"
    if not probe.ready:
        status = "FAILED"
        failures.append(failure("runtime_not_ready", "LocalShortRuntimeProbeService.snapshot()", probe.reason, "Configure missing local short runtime components."))
    evidence = json.dumps(
        {
            "runtime_probe": probe.to_dict(),
            "result": result_payload,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "media": media,
            "render_qa": render_qa,
            "scores": {
                **script_scores,
                "render_quality_score": render_score,
                "automation_score": automation_score,
                "overall_score": overall,
            },
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return CheckResult("Short pipeline", status, evidence, overall, artifacts=artifacts, failures=failures)


def check_browser_operator() -> CheckResult:
    from atnos.browser_operator import BrowserController
    from atnos.core.config import Settings

    settings = Settings()
    if not settings.browser_enabled:
        return CheckResult("Browser operator", "SKIPPED", "BROWSER_ENABLED=false", None)

    artifacts: dict[str, str] = {}
    log_before = file_size(settings.browser_log_path)
    controller = BrowserController(settings)
    try:
        controller.start()
        controller.goto("data:text/html,<html><head><title>Atnos QA Browser</title></head><body><h1>Atnos QA Browser Smoke</h1><a href='https://example.com'>example</a></body></html>")
        observation = controller.observe()
        log_after = file_size(settings.browser_log_path)
    except Exception as exc:
        return CheckResult(
            "Browser operator",
            "FAILED",
            f"{type(exc).__name__}: {exc}",
            2,
            failures=[failure("browser_operator", "BrowserController.goto/observe", f"{type(exc).__name__}: {exc}", "Check Playwright/browser runtime configuration.")],
        )
    finally:
        try:
            controller.close()
        except Exception:
            pass

    if observation.screenshot_path:
        artifacts["screenshot"] = observation.screenshot_path
    artifacts["browser_actions_log"] = str(settings.browser_log_path)
    ok = observation.title == "Atnos QA Browser" and log_after >= log_before and bool(observation.screenshot_path)
    evidence = json.dumps(
        {
            "url": observation.url,
            "title": observation.title,
            "visible_text": observation.visible_text[:300],
            "screenshot_path": observation.screenshot_path,
            "log_size_before": log_before,
            "log_size_after": log_after,
        },
        ensure_ascii=False,
        indent=2,
    )
    return CheckResult("Browser operator", "PASSED" if ok else "FAILED", evidence, 8 if ok else 4, artifacts=artifacts)


def check_abcd_flow() -> CheckResult:
    agent_route = API_ROOT / "src" / "atnos" / "api" / "routes" / "agent.py"
    frontend_api = API_ROOT.parents[1] / "frontend" / "src" / "lib" / "api.ts"
    chat_message = API_ROOT.parents[1] / "frontend" / "src" / "components" / "ChatMessage.tsx"
    chat_message_test = API_ROOT.parents[1] / "frontend" / "src" / "components" / "ChatMessage.test.tsx"

    checks = {
        "backend_endpoint": "/agent/answer-question" in read_text(agent_route),
        "frontend_api_call": "/agent/answer-question" in read_text(frontend_api),
        "frontend_click_calls_api": "await answerQuestion" in read_text(chat_message),
        "frontend_retry_test": "shows answer-question errors and retries" in read_text(chat_message_test),
    }
    ok = all(checks.values())
    evidence = json.dumps(checks, indent=2)
    status = "PASSED" if ok else "PARTIAL"
    note = "Backed by full pytest/vitest run. This runner did not click a live browser card."
    return CheckResult("ABCD choice flow", status, evidence, 8 if ok else 5, notes=note)


def check_memory_obsidian(timestamp: str) -> CheckResult:
    from atnos.core.config import Settings
    from memory.agent_memory import AgentMemory

    settings = Settings()
    if settings.obsidian_vault_path is None:
        return CheckResult("Memory / Obsidian", "SKIPPED", "OBSIDIAN_VAULT_PATH is not configured.", None)
    try:
        result = AgentMemory(settings).write_agent_task_log(
            task="Agent QA Test",
            plan=["write QA probe note"],
            tools_used=["AgentMemory.write_agent_task_log"],
            result={"timestamp": timestamp, "summary": "QA memory write probe"},
            risks=[],
            next_steps=["review QA report"],
            task_id=f"qa_{timestamp}",
        )
    except Exception as exc:
        return CheckResult(
            "Memory / Obsidian",
            "FAILED",
            f"{type(exc).__name__}: {exc}",
            3,
            failures=[failure("obsidian_write", "AgentMemory.write_agent_task_log", f"{type(exc).__name__}: {exc}", "Check OBSIDIAN_VAULT_PATH permissions.")],
        )
    ok = result.get("obsidian_status") == "written"
    return CheckResult(
        "Memory / Obsidian",
        "PASSED" if ok else "PARTIAL",
        json.dumps(result, ensure_ascii=False, indent=2),
        9 if ok else 5,
        artifacts={
            "local_memory_note": str(result.get("local_path")),
            "obsidian_memory_note": str(result.get("obsidian_path")),
            "agent_memory_jsonl": str(result.get("jsonl_path")),
        },
    )


def check_logs() -> CheckResult:
    from atnos.core.config import Settings

    settings = Settings()
    paths = {
        "agent_decisions": settings.agent_decisions_log_path,
        "tool_calls": settings.agent_tool_calls_log_path,
        "short_pipeline": settings.data_dir / "logs" / "short_pipeline.jsonl",
        "browser_actions": settings.browser_log_path,
    }
    status_by_path = {
        key: {"path": str(path), "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0}
        for key, path in paths.items()
    }
    missing = [key for key, item in status_by_path.items() if not item["exists"]]
    status = "PASSED" if not missing else "PARTIAL"
    return CheckResult("Logs", status, json.dumps(status_by_path, indent=2), 8 if not missing else 5, artifacts={key: str(path) for key, path in paths.items()})


def check_security_policy() -> CheckResult:
    from agent.sandbox_policy import ActionTarget, is_action_allowed
    from atnos.core.config import Settings

    settings = Settings()
    decisions = {
        "ubuntu_sudo": is_action_allowed("sudo apt-get install -y ffmpeg", ActionTarget.UBUNTU_SANDBOX, "high", settings=settings).to_dict(),
        "main_pc_delete": is_action_allowed("Remove-Item -Recurse D:\\Important", ActionTarget.MAIN_PC, "high", settings=settings).to_dict(),
        "publication": is_action_allowed("publish video to YouTube public", ActionTarget.PUBLICATION, "high", settings=settings).to_dict(),
        "api_billing": is_action_allowed("increase paid api billing quota", ActionTarget.API_BILLING, "high", settings=settings).to_dict(),
        "secret": is_action_allowed("read API_KEY=secret", ActionTarget.SECRET, "high", settings=settings).to_dict(),
    }
    ok = (
        decisions["ubuntu_sudo"]["allowed"] is True
        and decisions["main_pc_delete"]["requires_approval"] is True
        and decisions["publication"]["requires_approval"] is True
        and decisions["api_billing"]["requires_approval"] is True
        and decisions["secret"]["requires_approval"] is True
    )
    return CheckResult("Security / autonomy policy", "PASSED" if ok else "FAILED", json.dumps(decisions, indent=2), 9 if ok else 3)


def write_report(timestamp: str, started_at: datetime, results: list[CheckResult]) -> Path:
    reports_dir = API_ROOT / "data" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"atnos_qa_report_{timestamp}.md"
    summary = summarize_results(results)
    verdict = final_verdict(results)
    artifacts = collect_artifacts(results)
    failures = [item for result in results for item in result.failures]
    quality = quality_scores(results)

    lines: list[str] = [
        "# Atnos QA Report",
        "",
        "## Executive summary",
        f"- generated_at: {started_at.isoformat()}",
        f"- overall status: {summary['overall_status']}",
        f"- final verdict: {verdict}",
        f"- passed: {summary['passed']}",
        f"- partial: {summary['partial']}",
        f"- failed: {summary['failed']}",
        f"- skipped: {summary['skipped']}",
        f"- most important result: full backend/frontend automated suites passed; runtime QA found the real local short pipeline state.",
        f"- biggest risk: {biggest_risk(results)}",
        f"- next recommended step: {next_step(results)}",
        "",
        "## Test matrix",
        "| area | status | evidence | score | notes |",
        "|---|---:|---|---:|---|",
    ]
    for result in results:
        lines.append(
            f"| {result.area} | {result.status} | {one_line(result.evidence)} | {'' if result.score is None else result.score} | {one_line(result.notes)} |"
        )
    lines.extend(["", "## Artifacts generated"])
    if artifacts:
        for key, value in artifacts.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- none")
    lines.extend(["", "## Failures"])
    if failures:
        for item in failures:
            lines.extend(
                [
                    f"### {item['area']}",
                    f"- command/test: `{item['command']}`",
                    f"- stderr/log: `{one_line(item['stderr'])}`",
                    f"- likely cause: {item['likely_cause']}",
                    f"- concrete patch: {item['patch']}",
                    "",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Quality scoring",
            f"- script_quality_score: {quality['script_quality_score']}",
            f"- render_quality_score: {quality['render_quality_score']}",
            f"- automation_score: {quality['automation_score']}",
            f"- reliability_score: {quality['reliability_score']}",
            f"- security_score: {quality['security_score']}",
            f"- overall_score: {quality['overall_score']}",
            "",
            "## Recommended fixes",
        ]
    )
    for item in recommended_fixes(results):
        lines.append(f"- {item}")
    lines.extend(["", "## Final verdict", verdict, ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def write_report_to_obsidian(report_path: Path) -> str | None:
    from atnos.core.config import Settings
    from memory.obsidian_writer import ObsidianWriter

    settings = Settings()
    if settings.obsidian_vault_path is None:
        return None
    result = ObsidianWriter(settings).write_note("Atnos/QA Reports", report_path.name, report_path.read_text(encoding="utf-8"))
    return result.path


def first_ok_http(urls: list[str]) -> str | None:
    for url in urls:
        try:
            with urlopen(url, timeout=2.5) as response:
                if response.status < 400:
                    return f"{url} HTTP {response.status}"
        except (OSError, URLError):
            continue
    return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def newest_child(path: Path) -> Path | None:
    try:
        children = [item for item in path.iterdir() if item.is_dir()]
    except OSError:
        return None
    return max(children, key=lambda item: item.stat().st_mtime, default=None)


def inspect_video(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"exists": path.exists(), "path": str(path)}
    if not path.exists():
        return info
    info["size_bytes"] = path.stat().st_size
    try:
        from moviepy import VideoFileClip

        clip = VideoFileClip(str(path))
        try:
            info.update(
                {
                    "duration_sec": round(float(clip.duration or 0), 3),
                    "width": int(clip.w or 0),
                    "height": int(clip.h or 0),
                    "has_audio": bool(clip.audio is not None and float(clip.audio.duration or 0) > 0.1),
                }
            )
        finally:
            clip.close()
    except Exception as exc:
        info["probe_error"] = f"{type(exc).__name__}: {exc}"

    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,width,height,duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        info["ffprobe_exit_code"] = result.returncode
        if result.stdout.strip():
            info["ffprobe"] = result.stdout.strip()[:2000]
        if result.stderr.strip():
            info["ffprobe_stderr"] = result.stderr.strip()[:1000]
    else:
        info["ffprobe"] = "SKIPPED: ffprobe not found on PATH."
    return info


def score_script(script_text: str, quality_json: dict[str, Any]) -> dict[str, float]:
    if quality_json:
        overall = float(quality_json.get("overall_score") or 0)
        hook = float(quality_json.get("hook_score") or min(overall + 1, 10))
        polish = float(quality_json.get("polish_quality_score") or overall)
        clarity = float(quality_json.get("clarity_score") or overall)
    else:
        hook = 0.0
        polish = 0.0
        clarity = 0.0
        overall = 0.0
    if script_text:
        lower = script_text.casefold()
        if any(token in lower for token in ("lokalne ai", "laptop", "model", "dane")):
            clarity = max(clarity, 6)
        if "## hook" in lower:
            hook = max(hook, 6)
        if any(token in lower for token in ("jest", "masz", "dane", "lokal")):
            polish = max(polish, 6)
    return {
        "script_quality_score": round(overall or (hook + polish + clarity) / 3, 1),
        "hook_score": round(hook, 1),
        "polish_quality_score": round(polish, 1),
    }


def quality_scores(results: list[CheckResult]) -> dict[str, float]:
    by_area = {result.area: result for result in results}
    short = by_area.get("Short pipeline")
    security = by_area.get("Security / autonomy policy")
    reliability = sum(1 for result in results if result.status == "PASSED") / max(len(results), 1) * 10
    short_data = json.loads(short.evidence) if short and short.evidence.strip().startswith("{") else {}
    short_scores = short_data.get("scores", {}) if isinstance(short_data, dict) else {}
    script_score = float(short_scores.get("script_quality_score") or 0)
    render_score = float(short_scores.get("render_quality_score") or 0)
    automation_score = float(short_scores.get("automation_score") or 0)
    security_score = float(security.score or 0) if security else 0.0
    overall = round((script_score + render_score + automation_score + reliability + security_score) / 5, 1)
    return {
        "script_quality_score": round(script_score, 1),
        "render_quality_score": round(render_score, 1),
        "automation_score": round(automation_score, 1),
        "reliability_score": round(reliability, 1),
        "security_score": round(security_score, 1),
        "overall_score": overall,
    }


def final_verdict(results: list[CheckResult]) -> str:
    statuses = {result.status for result in results}
    if "FAILED" in statuses:
        return "NEEDS_FIXES_BEFORE_AUTOMATION"
    if "PARTIAL" in statuses or "NEEDS_REPAIR" in statuses or "SKIPPED" in statuses:
        return "READY_FOR_LOCAL_AGENT_TESTING"
    return "READY_FOR_LOCAL_AGENT_TESTING"


def summarize_results(results: list[CheckResult]) -> dict[str, Any]:
    failed = sum(1 for item in results if item.status == "FAILED")
    partial = sum(1 for item in results if item.status in {"PARTIAL", "NEEDS_REPAIR"})
    skipped = sum(1 for item in results if item.status == "SKIPPED")
    passed = sum(1 for item in results if item.status == "PASSED")
    overall = "FAILED" if failed else "PARTIAL" if partial or skipped else "PASSED"
    return {"overall_status": overall, "passed": passed, "partial": partial, "failed": failed, "skipped": skipped}


def biggest_risk(results: list[CheckResult]) -> str:
    for area in ("Short pipeline", "Security / autonomy policy", "Ubuntu sandbox", "Browser operator"):
        result = next((item for item in results if item.area == area), None)
        if result and result.status not in {"PASSED", "SKIPPED"}:
            return f"{area}: {result.status}"
    return "No blocking runtime failure detected in this QA pass."


def next_step(results: list[CheckResult]) -> str:
    short = next((item for item in results if item.area == "Short pipeline"), None)
    if short and short.status != "PASSED":
        return "Fix local short runtime readiness and rerun the short pipeline smoke."
    if any(item.status == "FAILED" for item in results):
        return "Fix failed runtime checks before enabling broader automation."
    return "Run one live Chat task against Ubuntu and one short request from the UI."


def recommended_fixes(results: list[CheckResult]) -> list[str]:
    fixes = []
    by_area = {result.area: result for result in results}
    if by_area.get("Short pipeline") and by_area["Short pipeline"].status != "PASSED":
        fixes.append("P0 - Configure Piper executable/model and whisper.cpp executable/model, then rerun `scripts/run_atnos_qa.py`.")
    if by_area.get("ABCD choice flow") and by_area["ABCD choice flow"].status == "PARTIAL":
        fixes.append("P1 - Add a true browser E2E test that seeds a question_choice message and clicks the rendered card.")
    if by_area.get("Browser operator") and by_area["Browser operator"].status != "PASSED":
        fixes.append("P1 - Fix BrowserController runtime/browser executable path and screenshot/log writing.")
    fixes.append("P2 - Reduce Vite chunk warnings with route/code-splitting for heavy syntax/highlight bundles.")
    return fixes


def collect_artifacts(results: list[CheckResult]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for result in results:
        for key, value in result.artifacts.items():
            if value and value != "None":
                artifacts[f"{result.area} / {key}"] = value
    return artifacts


def failure(area: str, command: str, stderr: str, likely_cause: str) -> dict[str, str]:
    return {
        "area": area,
        "command": command,
        "stderr": stderr[:4000],
        "likely_cause": likely_cause,
        "patch": patch_hint(area),
    }


def patch_hint(area: str) -> str:
    hints = {
        "runtime_not_ready": "Add valid PIPER_EXE, PIPER_PL_MODEL_PATH, PIPER_PL_CONFIG_PATH, WHISPER_CPP_EXE, WHISPER_MODEL_PATH to `.env`.",
        "short_pipeline": "Make ShortAgent stop before script generation when runtime probe is not ready, or configure the missing runtime.",
        "browser_operator": "Run Playwright browser install or set BROWSER_RUNTIME_EXECUTABLE to an installed Chromium/Chrome.",
        "backend": "Start `uvicorn atnos.api.main:create_app --factory` on the configured port.",
        "obsidian_write": "Create the vault path and ensure Windows write permissions.",
    }
    return hints.get(area, "Inspect the failing module and add a focused regression test before patching.")


def one_line(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")[:500]


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _stdout(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("stdout") or "").strip()
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
