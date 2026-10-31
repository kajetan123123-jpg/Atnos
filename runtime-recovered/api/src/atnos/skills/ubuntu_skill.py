from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from agent.models import AgentPlanStep, ToolCallResult


UBUNTU_CONTEXT_TOKENS = (
    "ubuntu",
    "laptop",
    "ssh",
    "sandbox",
    "atnos-latitude",
    "198.51.100.216",
)


@dataclass(frozen=True, slots=True)
class UbuntuSkillIntent:
    activated: bool
    action: str
    command: str | None = None
    reason: str = ""


def detect_ubuntu_intent(text: str) -> UbuntuSkillIntent:
    normalized = _normalize(text)
    if not any(token in normalized for token in UBUNTU_CONTEXT_TOKENS):
        return UbuntuSkillIntent(False, "none", reason="No explicit Ubuntu/laptop/SSH/sandbox context.")

    command = _extract_inline_command(text)
    if command is not None:
        return UbuntuSkillIntent(True, "run_command", command=command, reason="Inline command for Ubuntu sandbox.")

    if any(token in normalized for token in ("stan", "status", "raport", "report")):
        return UbuntuSkillIntent(True, "system_report", reason="Ubuntu health/report request.")

    if any(token in normalized for token in ("zainstaluj", "install", "pakiet", "package", "zaleznosci")):
        package = _extract_package_name(normalized)
        return UbuntuSkillIntent(True, "install_dependencies", command=package, reason="Ubuntu package/dependency request.")

    if any(token in normalized for token in ("test", "pytest", "uruchom testy")):
        return UbuntuSkillIntent(True, "run_tests", reason="Ubuntu test execution request.")

    return UbuntuSkillIntent(True, "check_connection", reason="Explicit Ubuntu context without a more specific action.")


def build_ubuntu_plan(intent: UbuntuSkillIntent, text: str) -> tuple[AgentPlanStep, ...]:
    if intent.action == "system_report":
        return (
            AgentPlanStep(
                name="check_ubuntu_connection",
                tool_name="ubuntu_check",
                expected_result="Ubuntu sandbox returns identity, sudo, hostname and runtime checks.",
            ),
            AgentPlanStep(
                name="create_ubuntu_system_report",
                tool_name="ubuntu_system_report",
                expected_result="Remote system_report.md is created in logs/.",
            ),
        )
    if intent.action == "install_dependencies":
        package = intent.command
        if package:
            return (
                AgentPlanStep(
                    name="install_ubuntu_package",
                    tool_name="ubuntu_install_package",
                    args={"package_name": package},
                    expected_result=f"Package {package} is installed on Ubuntu sandbox.",
                ),
            )
        return (
            AgentPlanStep(
                name="bootstrap_ubuntu_workspace",
                tool_name="ubuntu_bootstrap_workspace",
                expected_result="Workspace directories exist.",
            ),
            AgentPlanStep(
                name="install_ubuntu_base_dependencies",
                tool_name="ubuntu_run",
                args={
                    "command": "sudo apt-get update && sudo apt-get install -y git curl wget python3 python3-venv python3-pip ffmpeg build-essential",
                    "timeout": 900,
                },
                expected_result="Base development/media dependencies are installed.",
            ),
        )
    if intent.action == "run_tests":
        command = (
            "if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi; "
            "if [ -f pytest.ini ] || [ -d tests ]; then python -m pytest -v --tb=short; "
            "else echo 'No pytest suite found in /home/atnos/atnos'; fi"
        )
        return (
            AgentPlanStep(
                name="run_ubuntu_project_tests",
                tool_name="ubuntu_project_command",
                args={"command": command, "timeout": 900},
                expected_result="Project tests run or report that no pytest suite exists.",
            ),
        )
    if intent.action == "run_command" and intent.command:
        return (
            AgentPlanStep(
                name="run_ubuntu_command",
                tool_name="ubuntu_run",
                args={"command": intent.command, "timeout": 600},
                expected_result="Command returns exit code and stdout/stderr.",
            ),
        )
    return (
        AgentPlanStep(
            name="check_ubuntu_connection",
            tool_name="ubuntu_check",
            expected_result="Ubuntu sandbox returns identity, sudo, hostname and runtime checks.",
        ),
    )


def format_ubuntu_chat_response(tool_results: tuple[ToolCallResult, ...]) -> str:
    if not tool_results:
        return "Nie wykonalem zadnej komendy Ubuntu, bo plan nie zawieral tool calli."

    failed = [item for item in tool_results if not item.success]
    lines = ["Ubuntu sandbox: wykonano przez SSH."]
    for item in tool_results:
        result = item.result
        command = result.get("command")
        exit_code = result.get("exit_code")
        if command is not None:
            lines.append(f"- `{item.tool_name}`: exit_code={exit_code}, command=`{_compact(str(command), 120)}`")
        else:
            lines.append(f"- `{item.tool_name}`: {item.status}")
        if result.get("report_path"):
            lines.append(f"- raport: `{result['report_path']}`")
        stdout = _compact(str(result.get("stdout") or ""), 260)
        stderr = _compact(str(result.get("stderr") or item.error or ""), 260)
        if stdout:
            lines.append(f"- stdout: `{stdout}`")
        if stderr:
            lines.append(f"- stderr: `{stderr}`")
    if failed:
        lines.append("Status: czesc krokow nie przeszla. Szczegoly sa w lokalnym `data/logs/tool_calls.jsonl` i zdalnym `logs/commands.log`.")
    else:
        lines.append("Status: wykonane. Logi: lokalnie `data/logs/tool_calls.jsonl`, zdalnie `/home/atnos/atnos/logs/commands.log`.")
    return "\n".join(lines)


def _extract_inline_command(text: str) -> str | None:
    match = re.search(r"`([^`]+)`", text)
    if match:
        return match.group(1).strip()
    return None


def _extract_package_name(normalized: str) -> str | None:
    match = re.search(r"(?:zainstaluj|install|pakiet|package)\s+([a-z0-9_.+:-]+)", normalized)
    if match:
        value = match.group(1).strip(" .,!?:;")
        if value and value not in {"brakujace", "zaleznosci"}:
            return value
    return None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())


def _compact(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
