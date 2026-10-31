from __future__ import annotations

"""Kontrolowany adapter Codexa z izolowanym trybem propozycji poprawek."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Sequence
from uuid import uuid4

from atnos.ai_workers.models import WorkerExecutionResult, WorkerStatusSnapshot
from atnos.ai_workers.redaction import redact_payload, redact_text
from atnos.core.config import Settings


CODEX_WORKER_KEY = "codex"
SAFE_SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".toml", ".yaml", ".yml", ".css", ".html"})
MAX_SOURCE_FILES = 12
MAX_SOURCE_FILE_BYTES = 200_000
MAX_PATCH_FILES = 8
PATCH_TASK_TYPE = "safe_code_fix"
FORBIDDEN_PATH_PARTS = frozenset({".git", "node_modules", "data", "logs", "outputs", ".venv", "dist"})
CODEX_LIFECYCLE_PHASES = (
    "prompt_prepared",
    "started",
    "running",
    "completed",
    "result_received",
    "verifying",
    "verified",
    "verification_failed",
)


@dataclass(frozen=True, slots=True)
class CodexCommandOutcome:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Path, int], CodexCommandOutcome]


class CodexWorker:
    """Uruchamia Codexa w ograniczonym pakiecie lub izolowanym worktree.

    Poprawki nigdy nie są stosowane do głównego worktree. Kod może zmieniać
    wyłącznie tymczasowy worktree utworzony z ``HEAD``. Atnos niezależnie
    sprawdza diff, zakres dozwolonych ścieżek i wskazany test przed zapisaniem
    propozycji jako zweryfikowanej.
    """

    key = CODEX_WORKER_KEY
    label = "Codex Controlled"
    allowed_task_types = (
        "code_review",
        "repository_audit",
        "engineering_plan",
        "architecture_review",
        PATCH_TASK_TYPE,
    )

    def __init__(self, settings: Settings, *, command_runner: CommandRunner | None = None) -> None:
        self.settings = settings
        self.command_runner = command_runner or self._run_command

    async def status(self) -> WorkerStatusSnapshot:
        node_path = self._node_path()
        cli_path = self._cli_path()
        if node_path is None:
            return self._snapshot(
                status="not_configured",
                ready=False,
                reason="Codex adapter cannot find a Node.js executable.",
                node_path=None,
                cli_path=cli_path,
            )
        if cli_path is None:
            return self._snapshot(
                status="not_configured",
                ready=False,
                reason="Codex CLI package is not installed in the configured Atnos tool cache.",
                node_path=node_path,
                cli_path=None,
            )
        return self._snapshot(
            status="ready",
            ready=True,
            reason="Codex CLI is available for sanitized reviews and isolated patch proposals.",
            node_path=node_path,
            cli_path=cli_path,
        )

    async def run(
        self,
        *,
        task_type: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> WorkerExecutionResult:
        status = await self.status()
        if not status.ready:
            return WorkerExecutionResult(
                worker_key=self.key,
                status="blocked",
                summary=status.reason,
                result=status.to_dict(),
                blocked_reason=status.reason,
            )
        if task_type not in self.allowed_task_types:
            reason = f"Codex worker permits only: {', '.join(self.allowed_task_types)}."
            return WorkerExecutionResult(
                worker_key=self.key,
                status="blocked",
                summary=reason,
                result={"task_type": task_type},
                blocked_reason=reason,
            )

        redacted_prompt = redact_text(prompt)
        redacted_context = redact_payload(context or {})
        safe_context = redacted_context if isinstance(redacted_context, dict) else {}
        lifecycle: list[dict[str, object]] = []
        _append_lifecycle(
            lifecycle,
            "prompt_prepared",
            prompt_sha256=_sha256_text(redacted_prompt),
            context_keys=sorted(safe_context),
        )
        if task_type == PATCH_TASK_TYPE:
            allowed_paths = self._allowed_patch_paths(safe_context)
            if not allowed_paths:
                reason = "safe_code_fix requires a non-empty, safe context.allowed_paths allowlist."
                return WorkerExecutionResult(
                    worker_key=self.key,
                    status="blocked",
                    summary=reason,
                    result={"task_type": task_type},
                    blocked_reason=reason,
                )
        _append_lifecycle(lifecycle, "started", worker_key=self.key)
        _append_lifecycle(lifecycle, "running", execution_kind=task_type)
        try:
            result = await asyncio.to_thread(
                self._run_patch_task if task_type == PATCH_TASK_TYPE else self._run_read_only_task,
                task_type=task_type,
                prompt=redacted_prompt,
                context=safe_context,
                lifecycle=lifecycle,
            )
        except Exception as exc:
            _append_lifecycle(
                lifecycle,
                "verification_failed",
                reason=f"adapter_exception:{type(exc).__name__}",
            )
            return WorkerExecutionResult(
                worker_key=self.key,
                status="failed",
                summary=f"Codex adapter failed: {type(exc).__name__}: {exc}",
                result={
                    "lifecycle": lifecycle,
                    "prompt_sha256": _sha256_text(redacted_prompt),
                    "result_sha256": None,
                    "prompt_equals_result": False,
                },
            )

        if result["returncode"] != 0:
            if _last_lifecycle_phase(result.get("lifecycle")) != "verification_failed":
                _append_lifecycle(result["lifecycle"], "verification_failed", reason="worker_non_zero_exit")
            return WorkerExecutionResult(
                worker_key=self.key,
                status="failed",
                summary="Codex CLI returned a non-zero exit code.",
                result=result,
            )
        if not result["last_message"]:
            _append_lifecycle(result["lifecycle"], "verification_failed", reason="missing_worker_result")
            return WorkerExecutionResult(
                worker_key=self.key,
                status="failed",
                summary="Codex CLI completed without a final result message.",
                result=result,
            )
        if result.get("prompt_equals_result") is True:
            _append_lifecycle(result["lifecycle"], "verification_failed", reason="prompt_reused_as_result")
            return WorkerExecutionResult(
                worker_key=self.key,
                status="failed",
                summary="Codex result was rejected because it repeated the prepared prompt instead of returning worker output.",
                result=result,
            )
        verification = result.get("verification")
        if isinstance(verification, dict) and verification.get("status") != "passed":
            return WorkerExecutionResult(
                worker_key=self.key,
                status="failed",
                summary=f"Codex proposal failed independent verification: {verification.get('reason', 'unknown reason')}",
                result=result,
            )
        return WorkerExecutionResult(
            worker_key=self.key,
            status="completed",
            summary=str(result["last_message"])[:500],
            result=result,
        )

    def _run_read_only_task(
        self,
        *,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
        lifecycle: list[dict[str, object]],
    ) -> dict[str, object]:
        node_path = self._node_path()
        cli_path = self._cli_path()
        if node_path is None or cli_path is None:
            raise RuntimeError("Codex CLI is unavailable.")

        run_id = uuid4().hex
        run_dir = (self.settings.data_dir / "ai-workers" / "codex" / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=False)
        output_path = run_dir / "final-message.txt"
        copied_files = self._write_sanitized_bundle(run_dir=run_dir, task_type=task_type, prompt=prompt, context=context)
        command = [
            str(node_path),
            str(cli_path),
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--output-last-message",
            str(output_path),
            "--cd",
            str(run_dir),
            (
                "Review only the sanitized task bundle in the current directory. "
                "Do not execute commands that modify files, access credentials, make network calls, "
                "or claim changes were applied. Return: findings, evidence from provided files, "
                "limitations, and one next step.\n\n"
                f"Task type: {task_type}\nObjective: {prompt}"
            ),
        ]
        outcome = self.command_runner(command, run_dir, self.settings.codex_adapter_timeout_s)
        _append_lifecycle(lifecycle, "completed", returncode=outcome.returncode)
        last_message = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
        redacted_message = redact_text(last_message)
        if redacted_message:
            _append_lifecycle(lifecycle, "result_received", result_sha256=_sha256_text(redacted_message))
        return {
            "run_id": run_id,
            "task_type": task_type,
            "returncode": outcome.returncode,
            "last_message": redacted_message,
            "stdout_tail": redact_text(outcome.stdout[-4_000:]),
            "stderr_tail": redact_text(outcome.stderr[-4_000:]),
            "bundle_path": str(run_dir),
            "output_path": str(output_path),
            "copied_files": copied_files,
            "completed_at": datetime.now(UTC).isoformat(),
            "execution_mode": "sanitized_bundle_read_only",
            "lifecycle": lifecycle,
            "prompt_sha256": _sha256_text(prompt),
            "result_sha256": _sha256_text(redacted_message) if redacted_message else None,
            "prompt_equals_result": bool(redacted_message) and redacted_message.strip() == prompt.strip(),
            "review": {"status": "awaiting_review", "apply_authorized": False},
        }

    def _run_patch_task(
        self,
        *,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
        lifecycle: list[dict[str, object]],
    ) -> dict[str, object]:
        """Uruchamia jedną propozycję poprawki poza głównym worktree."""
        node_path = self._node_path()
        cli_path = self._cli_path()
        git_path = self._git_path()
        repository_root = self._repository_root()
        allowed_paths = self._allowed_patch_paths(context)
        if node_path is None or cli_path is None or git_path is None:
            raise RuntimeError("Codex patch mode requires Node.js, Codex CLI, and Git.")
        if not allowed_paths:
            raise RuntimeError("Codex patch mode requires safe allowed_paths.")

        run_id = uuid4().hex
        run_dir = (self.settings.data_dir / "ai-workers" / "codex" / run_id).resolve()
        worktree = run_dir / "worktree"
        output_path = run_dir / "final-message.txt"
        diff_path = run_dir / "proposal.diff"
        run_dir.mkdir(parents=True, exist_ok=False)
        self._write_patch_contract(run_dir=run_dir, task_type=task_type, prompt=prompt, context=context, allowed_paths=allowed_paths)

        created_worktree = False
        try:
            create = self._run_tool([str(git_path), "worktree", "add", "--detach", str(worktree), "HEAD"], repository_root, 90)
            if create.returncode != 0:
                raise RuntimeError(f"Unable to create isolated worktree: {create.stderr[-500:]}")
            created_worktree = True
            command = [
                str(node_path),
                str(cli_path),
                "exec",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "--json",
                "--output-last-message",
                str(output_path),
                "--cd",
                str(worktree),
                self._patch_prompt(task_type=task_type, objective=prompt, allowed_paths=allowed_paths),
            ]
            outcome = self.command_runner(command, worktree, self.settings.codex_adapter_timeout_s)
            _append_lifecycle(lifecycle, "completed", returncode=outcome.returncode)
            diff = self._run_tool([str(git_path), "-C", str(worktree), "diff", "--no-ext-diff", "--binary"], worktree, 30)
            changed = self._run_tool([str(git_path), "-C", str(worktree), "diff", "--name-only"], worktree, 30)
            changed_paths = [line.strip().replace("\\", "/") for line in changed.stdout.splitlines() if line.strip()]
            diff_text = redact_text(diff.stdout)
            diff_path.write_text(diff_text, encoding="utf-8")
            last_message = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
            redacted_message = redact_text(last_message)
            if redacted_message:
                _append_lifecycle(
                    lifecycle,
                    "result_received",
                    result_sha256=_sha256_text(redacted_message),
                    changed_paths=changed_paths,
                    diff_sha256=_sha256_text(diff_text),
                )
            _append_lifecycle(lifecycle, "verifying", verifier="atnos_independent_patch_verifier")
            prompt_equals_result = bool(redacted_message) and redacted_message.strip() == prompt.strip()
            if prompt_equals_result:
                verification = {
                    "status": "failed",
                    "reason": "Worker result repeated the prepared prompt instead of returning independent output.",
                    "changed_paths": changed_paths,
                }
            elif outcome.returncode == 0:
                verification = self._verify_patch_proposal(
                    worktree=worktree,
                    allowed_paths=allowed_paths,
                    changed_paths=changed_paths,
                    diff_text=diff_text,
                    context=context,
                )
            else:
                verification = {
                    "status": "failed",
                    "reason": "Codex CLI returned a non-zero exit code before independent verification.",
                    "changed_paths": changed_paths,
                }
            _append_lifecycle(
                lifecycle,
                "verified" if verification.get("status") == "passed" else "verification_failed",
                reason=str(verification.get("reason") or "unknown"),
            )
            return {
                "run_id": run_id,
                "task_type": task_type,
                "returncode": outcome.returncode,
                "last_message": redacted_message,
                "stdout_tail": redact_text(outcome.stdout[-4_000:]),
                "stderr_tail": redact_text(outcome.stderr[-4_000:]),
                "changed_paths": changed_paths,
                "diff_path": str(diff_path),
                "diff_sha256": _sha256_text(diff_text),
                "verification": verification,
                "rollback": {
                    "main_worktree_modified": False,
                    "isolated_worktree_removed": True,
                    "apply_to_main_requires_human_approval": True,
                },
                "completed_at": datetime.now(UTC).isoformat(),
                "execution_mode": "isolated_worktree_patch_proposal",
                "lifecycle": lifecycle,
                "prompt_sha256": _sha256_text(prompt),
                "result_sha256": _sha256_text(redacted_message) if redacted_message else None,
                "prompt_equals_result": prompt_equals_result,
                "proposal_state": "awaiting_review",
                "review": {"status": "awaiting_review", "apply_authorized": False},
            }
        finally:
            if created_worktree and worktree.exists():
                # The diff is already stored as an evidence artefact. Removing this
                # disposable worktree guarantees a failed proposal cannot leak into
                # the active checkout.
                self._run_tool([str(git_path), "worktree", "remove", "--force", str(worktree)], repository_root, 90)

    def _write_patch_contract(
        self,
        *,
        run_dir: Path,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
        allowed_paths: list[str],
    ) -> None:
        contract = {
            "task_type": task_type,
            "objective": prompt,
            "context": context,
            "allowed_paths": allowed_paths,
            "forbidden_paths": sorted(FORBIDDEN_PATH_PARTS),
            "policy": {
                "main_worktree_write": False,
                "network_actions": False,
                "git_commit": False,
                "credential_access": False,
                "verification": "independent_diff_and_test_runner",
            },
        }
        (run_dir / "TASK.json").write_text(json_dumps(contract), encoding="utf-8")

    @staticmethod
    def _patch_prompt(*, task_type: str, objective: str, allowed_paths: list[str]) -> str:
        paths = "\n".join(f"- {path}" for path in allowed_paths)
        return (
            "You are working in an isolated disposable Git worktree. Make one minimal, safe code fix. "
            "Never access credentials, environment files, data directories, network, browser, OAuth, publishing, "
            "payments, or trading. Do not commit. Modify only this explicit allowlist:\n"
            f"{paths}\n\nTask type: {task_type}\nObjective: {objective}\n\n"
            "After editing, explain the changed files, the concrete defect, tests run, tests skipped, and limitations."
        )

    def _verify_patch_proposal(
        self,
        *,
        worktree: Path,
        allowed_paths: list[str],
        changed_paths: list[str],
        diff_text: str,
        context: dict[str, Any],
    ) -> dict[str, object]:
        invalid_paths = self._invalid_changed_paths(changed_paths, allowed_paths)
        if not changed_paths:
            return {"status": "failed", "reason": "Codex produced no source diff.", "changed_paths": []}
        if invalid_paths:
            return {
                "status": "failed",
                "reason": "Codex changed paths outside the explicit allowlist.",
                "changed_paths": changed_paths,
                "invalid_paths": invalid_paths,
            }
        if not diff_text.strip():
            return {"status": "failed", "reason": "Diff capture was empty.", "changed_paths": changed_paths}

        command = self._verification_command(context, worktree)
        if command is None:
            return {
                "status": "failed",
                "reason": "No safe verification_command was supplied for the patch proposal.",
                "changed_paths": changed_paths,
            }
        test = self._run_tool(
            command,
            worktree,
            min(self.settings.codex_adapter_timeout_s, 300),
            env=self._isolated_test_environment(),
        )
        if test.returncode != 0:
            return {
                "status": "failed",
                "reason": "Independent verification command failed.",
                "changed_paths": changed_paths,
                "command": command,
                "stdout_tail": redact_text(test.stdout[-4_000:]),
                "stderr_tail": redact_text(test.stderr[-4_000:]),
            }
        return {
            "status": "passed",
            "reason": "Allowlist, diff capture, and independent verification command passed.",
            "changed_paths": changed_paths,
            "command": command,
            "stdout_tail": redact_text(test.stdout[-4_000:]),
            "stderr_tail": redact_text(test.stderr[-4_000:]),
        }

    def _verification_command(self, context: dict[str, Any], worktree: Path) -> list[str] | None:
        requested = context.get("verification_command")
        if not isinstance(requested, list) or not requested or not all(isinstance(item, str) and item.strip() for item in requested):
            return None
        command = [item.strip() for item in requested]
        executable = Path(command[0]).name.casefold()
        is_python = executable in {"python", "python.exe", Path(sys.executable).name.casefold()}
        if not is_python or len(command) < 3 or command[1:3] != ["-m", "pytest"]:
            return None
        if any(token in {";", "&&", "|", ">", "<"} for token in command):
            return None
        command[0] = sys.executable
        return command

    @staticmethod
    def _isolated_test_environment() -> dict[str, str]:
        """Usuwa runtime settings operatora przed testem checkoutu z ``HEAD``.

        Worktree proposal is validated against the committed source. Inheriting
        the operator's current ``.env`` can otherwise make an older committed
        settings schema fail before the worker's own patch is tested.
        """
        environment = dict(os.environ)
        for field_name, field in Settings.model_fields.items():
            environment.pop(field_name.upper(), None)
            alias = field.validation_alias
            if isinstance(alias, str):
                environment.pop(alias, None)
        environment.pop("ATNOS_ENV_FILE", None)
        environment.pop("DATA_DIR", None)
        return environment

    def _allowed_patch_paths(self, context: dict[str, Any]) -> list[str]:
        raw_paths = context.get("allowed_paths")
        if not isinstance(raw_paths, list):
            return []
        allowed: list[str] = []
        root = self._repository_root()
        for raw_path in raw_paths[:MAX_PATCH_FILES]:
            if not isinstance(raw_path, str):
                continue
            normalized = raw_path.replace("\\", "/").lstrip("/")
            candidate = (root / normalized).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.suffix.lower() not in SAFE_SOURCE_SUFFIXES:
                continue
            if any(part.casefold() in FORBIDDEN_PATH_PARTS for part in Path(normalized).parts):
                continue
            if candidate.name.casefold().startswith(".env") or "secret" in candidate.name.casefold() or "token" in candidate.name.casefold():
                continue
            allowed.append(normalized)
        return list(dict.fromkeys(allowed))

    @staticmethod
    def _invalid_changed_paths(changed_paths: list[str], allowed_paths: list[str]) -> list[str]:
        allowed = set(allowed_paths)
        return [path for path in changed_paths if path not in allowed]

    def _write_sanitized_bundle(
        self,
        *,
        run_dir: Path,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
    ) -> list[str]:
        allowed_root = self._repository_root()
        requested_files = context.get("source_files") if isinstance(context.get("source_files"), list) else []
        copied: list[str] = []
        source_root = run_dir / "source"
        for raw_path in requested_files[:MAX_SOURCE_FILES]:
            if not isinstance(raw_path, str):
                continue
            source = (allowed_root / raw_path).resolve()
            try:
                relative = source.relative_to(allowed_root)
            except ValueError:
                continue
            if not source.is_file() or source.suffix.lower() not in SAFE_SOURCE_SUFFIXES:
                continue
            if any(part.casefold() in FORBIDDEN_PATH_PARTS for part in relative.parts):
                continue
            if source.name.lower().startswith(".env") or "secret" in source.name.lower() or "token" in source.name.lower():
                continue
            if source.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            target = source_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact_text(source.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
            copied.append(str(relative).replace("\\", "/"))

        bundle = {
            "task_type": task_type,
            "objective": prompt,
            "context": context,
            "allowed_source_files": copied,
            "restrictions": [
                "No source repository, secrets, data, browser profiles, or generated media are available.",
                "Read-only review only; no patch, commit, browser, OAuth, payment, publishing, or trading action is authorized.",
            ],
        }
        (run_dir / "TASK.json").write_text(json_dumps(bundle), encoding="utf-8")
        return copied

    def _repository_root(self) -> Path:
        app_root = self.settings.app_root.resolve()
        candidate = app_root.parent.parent if app_root.name.lower() == "api" else app_root
        return candidate.resolve()

    def _node_path(self) -> Path | None:
        if self.settings.codex_node_executable is not None and self.settings.codex_node_executable.is_file():
            return self.settings.codex_node_executable
        runtime_node = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
        return runtime_node if runtime_node.is_file() else None

    def _cli_path(self) -> Path | None:
        direct = self.settings.codex_cli_root / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if direct.is_file():
            return direct
        pnpm_dir = self.settings.codex_cli_root / "node_modules" / ".pnpm"
        candidates = sorted(pnpm_dir.glob("@openai+codex@*/node_modules/@openai/codex/bin/codex.js"))
        return candidates[-1] if candidates else None

    @staticmethod
    def _git_path() -> Path | None:
        configured = str(os.environ.get("ATNOS_GIT_EXECUTABLE") or "").strip()
        if configured and Path(configured).is_file():
            return Path(configured)
        runtime_git = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "git" / "cmd" / "git.exe"
        if runtime_git.is_file():
            return runtime_git
        discovered = shutil.which("git")
        return Path(discovered) if discovered else None

    def _snapshot(
        self,
        *,
        status: str,
        ready: bool,
        reason: str,
        node_path: Path | None,
        cli_path: Path | None,
    ) -> WorkerStatusSnapshot:
        return WorkerStatusSnapshot(
            worker_key=self.key,
            label=self.label,
            status=status,  # type: ignore[arg-type]
            ready=ready,
            reason=reason,
            capabilities=("code_review", "repository_audit", "engineering_plan", "isolated_patch_proposal"),
            allowed_task_types=self.allowed_task_types,
            requires_browser=False,
            safety_mode="sanitized_read_only_or_isolated_patch_proposal",
            metadata={
                "node_path": str(node_path) if node_path is not None else None,
                "cli_path": str(cli_path) if cli_path is not None else None,
                "sandbox": "read-only or isolated-workspace-write",
                "main_worktree_writes": "not_authorized",
                "network_actions": "not_authorized",
            },
        )

    @staticmethod
    def _run_command(command: Sequence[str], workdir: Path, timeout_s: int) -> CodexCommandOutcome:
        completed = subprocess.run(
            list(command),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        return CodexCommandOutcome(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def _run_tool(
        command: Sequence[str],
        workdir: Path,
        timeout_s: int,
        *,
        env: dict[str, str] | None = None,
    ) -> CodexCommandOutcome:
        completed = subprocess.run(
            list(command),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            env=env,
        )
        return CodexCommandOutcome(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def json_dumps(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append_lifecycle(
    lifecycle: list[dict[str, object]],
    phase: str,
    **evidence: object,
) -> None:
    """Dodaje sanitizowany, monotoniczny wpis lifecycle bez treści promptu."""
    if phase not in CODEX_LIFECYCLE_PHASES:
        raise ValueError(f"Unsupported Codex lifecycle phase: {phase}")
    lifecycle.append(
        {
            "phase": phase,
            "at": datetime.now(UTC).isoformat(),
            "evidence": redact_payload(evidence),
        }
    )


def _last_lifecycle_phase(value: object) -> str | None:
    if not isinstance(value, list) or not value or not isinstance(value[-1], dict):
        return None
    phase = value[-1].get("phase")
    return str(phase) if phase else None
