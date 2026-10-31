from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import os
import posixpath
import stat
from typing import Any


class RemoteNodeSshError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteSshCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class RemoteSshConfig:
    host: str
    port: int
    user: str
    private_key_path: Path
    workdir: str
    connect_timeout_s: float = 15.0
    command_timeout_s: float = 120.0


def _import_paramiko():
    try:
        import paramiko  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local install
        raise RemoteNodeSshError(
            "Paramiko is required for Ubuntu worker bootstrap. Install backend dependencies first."
        ) from exc
    return paramiko


class RemoteNodeSshService:
    def __init__(self, config: RemoteSshConfig) -> None:
        self.config = config

    def run(self, command: str, *, sudo: bool = False, timeout_s: float | None = None) -> RemoteSshCommandResult:
        wrapped = self._wrap_command(command, sudo=sudo)
        client = self._open_client()
        try:
            stdin, stdout, stderr = client.exec_command(
                wrapped,
                timeout=timeout_s or self.config.command_timeout_s,
            )
            _ = stdin
            channel = stdout.channel
            exit_code = channel.recv_exit_status()
            return RemoteSshCommandResult(
                command=wrapped,
                exit_code=exit_code,
                stdout=stdout.read().decode("utf-8", errors="replace"),
                stderr=stderr.read().decode("utf-8", errors="replace"),
            )
        finally:
            client.close()

    def probe(self) -> dict[str, Any]:
        hostname = self.run("hostname").stdout.strip()
        session_type = self.run("printf '%s' \"$XDG_SESSION_TYPE\" || true").stdout.strip() or "unknown"
        display = self.run("printf '%s' \"$DISPLAY\" || true").stdout.strip() or None
        wayland = self.run("printf '%s' \"$WAYLAND_DISPLAY\" || true").stdout.strip() or None
        sudo_check = self.run("sudo -n true", timeout_s=10)
        return {
            "hostname": hostname or None,
            "session_type": session_type,
            "display": display,
            "wayland_display": wayland,
            "sudo_available": sudo_check.exit_code == 0,
        }

    def ensure_directory(self, remote_path: str) -> None:
        self.run(f"mkdir -p {self._quote(remote_path)}")

    def upload_text(self, remote_path: str, content: str, *, mode: int | None = None) -> None:
        remote_dir = str(PurePosixPath(remote_path).parent)
        self.ensure_directory(remote_dir)
        client = self._open_client()
        try:
            sftp = client.open_sftp()
            try:
                with sftp.file(remote_path, "w") as handle:
                    handle.write(content)
                if mode is not None:
                    sftp.chmod(remote_path, mode)
            finally:
                sftp.close()
        finally:
            client.close()

    def upload_tree(self, local_dir: Path, remote_dir: str) -> None:
        if not local_dir.exists():
            raise RemoteNodeSshError(f"Local artifact directory '{local_dir}' does not exist.")
        client = self._open_client()
        try:
            sftp = client.open_sftp()
            try:
                self._mkdir_p_sftp(sftp, remote_dir)
                for source in sorted(local_dir.rglob("*")):
                    relative = source.relative_to(local_dir).as_posix()
                    target = posixpath.join(remote_dir, relative)
                    if source.is_dir():
                        self._mkdir_p_sftp(sftp, target)
                        continue
                    self._mkdir_p_sftp(sftp, posixpath.dirname(target))
                    sftp.put(str(source), target)
                    if os.access(source, os.X_OK):
                            sftp.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
            finally:
                sftp.close()
        finally:
            client.close()

    def _open_client(self):
        paramiko = _import_paramiko()
        key_path = self.config.private_key_path
        if not key_path.exists():
            raise RemoteNodeSshError(f"SSH key '{key_path}' does not exist.")
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(str(key_path))
        except Exception as exc:  # pragma: no cover - depends on local file
            raise RemoteNodeSshError(f"Failed to load SSH key '{key_path}': {exc}") from exc
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.user,
                pkey=pkey,
                timeout=self.config.connect_timeout_s,
                banner_timeout=self.config.connect_timeout_s,
                auth_timeout=self.config.connect_timeout_s,
            )
        except Exception as exc:  # pragma: no cover - depends on network
            raise RemoteNodeSshError(
                f"Could not connect to {self.config.user}@{self.config.host}:{self.config.port}: {exc}"
            ) from exc
        return client

    def _wrap_command(self, command: str, *, sudo: bool) -> str:
        workdir = self._quote(self.config.workdir)
        payload = command.strip()
        if sudo and not payload.startswith("sudo "):
            payload = f"sudo -n {payload}"
        return f"cd {workdir} && {payload}"

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    @classmethod
    def _mkdir_p_sftp(cls, sftp, remote_path: str) -> None:
        parts = [part for part in PurePosixPath(remote_path).parts if part not in {"/", ""}]
        current = "/"
        for part in parts:
            current = posixpath.join(current, part)
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)
