from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import base64
import posixpath
import shlex
import socket
from typing import Any

from atnos.core.config import Settings


class UbuntuControlError(RuntimeError):
    """Base error for the Ubuntu SSH control module."""


class UbuntuControlConfigError(UbuntuControlError):
    """Raised when the local Ubuntu SSH configuration is incomplete."""


class UbuntuControlConnectionError(UbuntuControlError):
    """Raised when the remote Ubuntu host cannot be reached."""


class UbuntuControlAuthenticationError(UbuntuControlError):
    """Raised when SSH authentication fails."""


class UbuntuControlTimeoutError(UbuntuControlError):
    """Raised when a remote SSH command times out."""


class UbuntuControlApprovalRequired(UbuntuControlError):
    """Raised when sudo execution is disabled or requires human approval."""


class UbuntuControlBlockedCommand(UbuntuControlError):
    """Raised when a command targets guardrails the user explicitly forbade."""


@dataclass(frozen=True, slots=True)
class UbuntuControlConfig:
    host: str
    user: str
    key_path: Path
    workdir: str
    allow_sudo: bool
    require_approval_for_sudo: bool
    connect_timeout_s: float = 15.0

    @property
    def log_path(self) -> str:
        return posixpath.join(self.workdir.rstrip("/"), "logs", "commands.log")

    @property
    def system_report_path(self) -> str:
        return posixpath.join(self.workdir.rstrip("/"), "logs", "system_report.md")


@dataclass(frozen=True, slots=True)
class UbuntuCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _import_paramiko():
    try:
        import paramiko  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local venv
        raise UbuntuControlConfigError(
            "Paramiko is not installed in the backend environment. Install it in the active venv first."
        ) from exc
    return paramiko


class UbuntuControl:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.config = self._build_config(self.settings)

    @staticmethod
    def _build_config(settings: Settings) -> UbuntuControlConfig:
        host = (settings.atnos_ubuntu_host or "").strip()
        if not host:
            raise UbuntuControlConfigError("ATNOS_UBUNTU_HOST is missing in .env.")

        user = settings.atnos_ubuntu_user.strip() or "atnos"
        key_path = settings.atnos_ubuntu_key
        if key_path is None:
            raise UbuntuControlConfigError("ATNOS_UBUNTU_KEY is missing in .env.")
        if not key_path.exists():
            raise UbuntuControlConfigError(f"SSH private key does not exist: {key_path}")
        if not key_path.is_file():
            raise UbuntuControlConfigError(f"ATNOS_UBUNTU_KEY is not a file: {key_path}")

        workdir = settings.atnos_ubuntu_workdir.strip() or "/home/atnos/atnos"
        return UbuntuControlConfig(
            host=host,
            user=user,
            key_path=key_path,
            workdir=workdir,
            allow_sudo=settings.atnos_ubuntu_allow_sudo,
            require_approval_for_sudo=settings.atnos_ubuntu_require_approval_for_sudo,
        )

    def run(self, command: str, *, timeout: int = 600) -> UbuntuCommandResult:
        normalized = command.strip()
        if not normalized:
            raise UbuntuControlConfigError("Command cannot be empty.")
        self._enforce_hard_safety_blocks(normalized)
        self._enforce_sudo_policy(normalized)

        started_at = datetime.now(UTC)
        client = self._open_client()
        try:
            self._ensure_remote_log_dir(client)
            remote_command = self._wrap_command(normalized)
            try:
                stdin, stdout, stderr = client.exec_command(remote_command, timeout=timeout)
                _ = stdin
                channel = stdout.channel
                exit_code = channel.recv_exit_status()
                result = UbuntuCommandResult(
                    command=normalized,
                    exit_code=exit_code,
                    stdout=stdout.read().decode("utf-8", errors="replace"),
                    stderr=stderr.read().decode("utf-8", errors="replace"),
                )
            except TimeoutError as exc:
                raise UbuntuControlTimeoutError(
                    f"Remote command timed out after {timeout}s: {normalized}"
                ) from exc
            ended_at = datetime.now(UTC)
            self._append_remote_log(client, self._format_log_entry(started_at, ended_at, result))
            return result
        finally:
            client.close()

    def check_connection(self) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {}
        commands = {
            "whoami": "whoami",
            "sudo_whoami": "sudo -n whoami",
            "hostname": "hostname",
            "pwd": "pwd",
            "python_version": "python3 --version",
            "disk": "df -h | head",
            "memory": "free -h",
        }
        overall_ok = True
        for key, command in commands.items():
            result = self.run(command, timeout=60)
            checks[key] = result.to_dict()
            overall_ok = overall_ok and result.exit_code == 0
        return {
            "ok": overall_ok,
            "host": self.config.host,
            "user": self.config.user,
            "workdir": self.config.workdir,
            "checks": checks,
        }

    def create_system_report(self) -> dict[str, Any]:
        report_path = self.config.system_report_path
        command = """
python3 - <<'PY'
from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
import subprocess

REPORT_PATH = Path("logs/system_report.md")
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

def run(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )
    return (result.stdout or result.stderr).strip()

os_name = "unknown"
os_release = Path("/etc/os-release")
if os_release.exists():
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip().strip('"')
            break

lines = [
    "# Atnos Ubuntu System Report",
    "",
    f"- generated_at: {datetime.now(UTC).isoformat()}",
    f"- user: {run('whoami')}",
    f"- sudo_status: {run('sudo -n whoami 2>/dev/null || echo unavailable')}",
    f"- hostname: {run('hostname')}",
    f"- ip: {run(\"hostname -I | awk '{print $1}'\")}",
    f"- os: {os_name}",
    f"- kernel: {run('uname -r')}",
    "",
    "## CPU",
    run("lscpu | sed -n '1,12p'"),
    "",
    "## RAM",
    run("free -h"),
    "",
    "## Disk",
    run("df -h"),
    "",
    "## Python",
    run("python3 --version"),
    "",
    "## Git",
    run("git --version || true"),
    "",
    "## FFmpeg",
    run("ffmpeg -version | head -n 1 || true"),
    "",
]

REPORT_PATH.write_text("\\n".join(lines), encoding="utf-8")
PY
""".strip()
        result = self.run(command, timeout=120)
        payload = result.to_dict()
        payload["report_path"] = report_path
        return payload

    def bootstrap_workspace(self) -> dict[str, Any]:
        directories = (
            "src",
            "tools",
            "data",
            "logs",
            "models",
            "assets",
            "renders",
            "obsidian",
            "backups",
        )
        command = "mkdir -p " + " ".join(shlex.quote(item) for item in directories)
        result = self.run(command, timeout=120)
        payload = result.to_dict()
        payload["directories"] = [posixpath.join(self.config.workdir, item) for item in directories]
        return payload

    def install_package(self, package_name: str) -> dict[str, Any]:
        package = package_name.strip()
        if not package:
            raise UbuntuControlConfigError("Package name cannot be empty.")
        if not all(char.isalnum() or char in {".", "+", "-", "_", ":"} for char in package):
            raise UbuntuControlConfigError(f"Unsafe package name: {package_name}")
        result = self.run(f"sudo apt-get install -y {shlex.quote(package)}", timeout=600)
        payload = result.to_dict()
        payload["package_name"] = package
        return payload

    def run_project_command(self, command: str, *, timeout: int = 600) -> dict[str, Any]:
        result = self.run(command, timeout=timeout)
        payload = result.to_dict()
        payload["workdir"] = self.config.workdir
        return payload

    def stop_remote_atnos_processes(self) -> dict[str, Any]:
        result = self.run("sudo pkill -u atnos || true", timeout=60)
        payload = result.to_dict()
        payload["stopped_user"] = "atnos"
        return payload

    def _open_client(self):
        paramiko = _import_paramiko()
        try:
            private_key = paramiko.Ed25519Key.from_private_key_file(str(self.config.key_path))
        except FileNotFoundError as exc:
            raise UbuntuControlConfigError(f"SSH private key does not exist: {self.config.key_path}") from exc
        except paramiko.PasswordRequiredException as exc:
            raise UbuntuControlAuthenticationError(
                "The SSH private key is encrypted. Load it into an agent or use an unencrypted automation key."
            ) from exc
        except paramiko.SSHException as exc:
            raise UbuntuControlAuthenticationError(
                f"Could not load the Ed25519 SSH key '{self.config.key_path}': {exc}"
            ) from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.config.host,
                port=22,
                username=self.config.user,
                pkey=private_key,
                timeout=self.config.connect_timeout_s,
                banner_timeout=self.config.connect_timeout_s,
                auth_timeout=self.config.connect_timeout_s,
            )
        except paramiko.AuthenticationException as exc:
            raise UbuntuControlAuthenticationError(
                f"SSH authentication failed for {self.config.user}@{self.config.host}."
            ) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise UbuntuControlTimeoutError(
                f"Timed out while connecting to {self.config.user}@{self.config.host}."
            ) from exc
        except OSError as exc:
            raise UbuntuControlConnectionError(
                f"Could not connect to {self.config.user}@{self.config.host}: {exc}"
            ) from exc
        except paramiko.SSHException as exc:
            raise UbuntuControlConnectionError(
                f"SSH negotiation failed for {self.config.user}@{self.config.host}: {exc}"
            ) from exc
        return client

    def _ensure_remote_log_dir(self, client) -> None:
        mkdir_command = f"mkdir -p {shlex.quote(posixpath.dirname(self.config.log_path))}"
        self._exec_internal(client, f"bash -lc {shlex.quote(mkdir_command)}", timeout=30)

    def _append_remote_log(self, client, entry: str) -> None:
        encoded = base64.b64encode(entry.encode("utf-8")).decode("ascii")
        log_command = f"""
python3 - <<'PY'
from pathlib import Path
import base64

log_path = Path({self.config.log_path!r})
log_path.parent.mkdir(parents=True, exist_ok=True)
payload = base64.b64decode({encoded!r}).decode("utf-8")
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(payload)
PY
""".strip()
        self._exec_internal(client, self._wrap_command(log_command), timeout=30)

    def _enforce_sudo_policy(self, command: str) -> None:
        if "sudo" not in command:
            return
        if not self.config.allow_sudo:
            raise UbuntuControlApprovalRequired(
                "This runtime forbids sudo on the Ubuntu sandbox. Set ATNOS_UBUNTU_ALLOW_SUDO=true to enable it."
            )
        if self.config.require_approval_for_sudo:
            raise UbuntuControlApprovalRequired(
                "Sudo requires explicit approval. Set ATNOS_UBUNTU_REQUIRE_APPROVAL_FOR_SUDO=false to allow it."
            )

    @staticmethod
    def _enforce_hard_safety_blocks(command: str) -> None:
        normalized = " ".join(command.casefold().split())
        blocked_patterns = (
            "deluser atnos",
            "userdel atnos",
            "passwd -l atnos",
            "systemctl stop ssh",
            "systemctl stop sshd",
            "systemctl disable ssh",
            "systemctl disable sshd",
            "service ssh stop",
            "service sshd stop",
        )
        if any(pattern in normalized for pattern in blocked_patterns):
            raise UbuntuControlBlockedCommand(
                "Blocked command: this workflow must not remove the 'atnos' user or disable SSH automatically."
            )

    def _wrap_command(self, command: str) -> str:
        payload = f"cd {shlex.quote(self.config.workdir)} && {command}"
        return f"bash -lc {shlex.quote(payload)}"

    @staticmethod
    def _format_log_entry(started_at: datetime, ended_at: datetime, result: UbuntuCommandResult) -> str:
        compact_command = result.command.replace("\r", "\\r").replace("\n", "\\n")
        lines = [
            "===== ATNOS UBUNTU COMMAND =====",
            f"started_at={started_at.isoformat()}",
            f"ended_at={ended_at.isoformat()}",
            f"command={compact_command}",
            f"exit_code={result.exit_code}",
            "--- STDOUT ---",
            result.stdout.rstrip(),
            "--- STDERR ---",
            result.stderr.rstrip(),
            "===== END COMMAND =====",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _exec_internal(client, remote_command: str, *, timeout: int) -> None:
        stdin, stdout, stderr = client.exec_command(remote_command, timeout=timeout)
        _ = stdin
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise UbuntuControlError(
                "Remote helper command failed: "
                f"{stderr.read().decode('utf-8', errors='replace') or stdout.read().decode('utf-8', errors='replace')}"
            )


def run_ubuntu(command: str, timeout: int = 600, *, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).run(command, timeout=timeout).to_dict()


def run_ubuntu_command(command: str, timeout: int = 600, *, settings: Settings | None = None) -> dict[str, Any]:
    return run_ubuntu(command, timeout=timeout, settings=settings)


def check_ubuntu_connection(*, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).check_connection()


def create_system_report(*, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).create_system_report()


def bootstrap_workspace(*, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).bootstrap_workspace()


def install_package(package_name: str, *, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).install_package(package_name)


def run_project_command(command: str, timeout: int = 600, *, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).run_project_command(command, timeout=timeout)


def stop_remote_atnos_processes(*, settings: Settings | None = None) -> dict[str, Any]:
    return UbuntuControl(settings=settings).stop_remote_atnos_processes()
