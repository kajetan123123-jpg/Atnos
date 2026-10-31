from __future__ import annotations

from dataclasses import dataclass
import difflib
import json
from pathlib import Path
import subprocess
import sys

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask, SelfImprovementDecision, SelfImprovementStatus
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.self_improvement_note_repository import SelfImprovementNoteRepository
from atnos.services.account_action_broker_service import AccountActionBrokerService
from atnos.services.local_file_grant_service import LocalFileGrantService
from atnos.services.project_execution_service import ProjectExecutionService
from atnos.services.self_improvement_notes_service import SelfImprovementNotesService
from atnos.services.workflow_llm_service import WorkflowLLMService

CAPABILITY_ROADMAP_PATH = "docs/product/atnos-capability-roadmap-v0.md"
LOCAL_CODEGEN_PROVIDER_KEY = "local-codegen"
LOCAL_CODEGEN_ACCOUNT_IDENTIFIER = "local-codegen"


@dataclass(frozen=True, slots=True)
class SelfCodingDraftPreview:
    note_id: int
    mode: str
    patch_plan: str
    codex_prompt: str
    code_draft: str
    test_plan: str
    safety_notes: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CodeGenerationPreview:
    mode: str
    action_draft_id: int
    title: str
    summary: str
    metadata: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProjectTestRunResult:
    runner: str
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    summary: str
    first_failures: tuple[str, ...]
    stdout: str
    stderr: str


class SelfCodingDraftNotFoundError(Exception):
    pass


class SelfCodingExecutionError(ValueError):
    pass


class SelfCodingDraftService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.notes = SelfImprovementNoteRepository(session)
        self.note_service = SelfImprovementNotesService(session)
        self.accounts = AccountCredentialRepository(session)
        self.action_broker = AccountActionBrokerService(session, settings) if settings is not None else None

    def build_preview(self, *, note_id: int | None = None) -> SelfCodingDraftPreview:
        note = self._resolve_note(note_id)
        warnings = self._build_warnings(note)
        safety_notes = (
            "This is a draft/preview artifact only.",
            "Legacy self_coding_draft_v0 does not execute code or modify files.",
            "Any future patch must be reviewed and explicitly approved by the user.",
        )
        return SelfCodingDraftPreview(
            note_id=note.id,
            mode="self_coding_draft_v0",
            patch_plan=self._build_patch_plan(note),
            codex_prompt=self._build_codex_prompt(note),
            code_draft=self._build_code_draft(note),
            test_plan=self._build_test_plan(note),
            safety_notes=safety_notes,
            warnings=warnings,
        )

    def generate_application_scaffold(
        self,
        *,
        app_type: str,
        language: str,
        framework: str,
        description: str,
        target_dir: str,
        project_id: int | None = None,
    ) -> CodeGenerationPreview:
        account_id = self._ensure_local_codegen_account_id()
        normalized_description = description.strip()
        if not normalized_description:
            raise SelfCodingExecutionError("description is required.")
        template_key, files = self._build_scaffold_files(
            app_type=app_type,
            language=language,
            framework=framework,
            description=normalized_description,
        )
        metadata = {
            "kind": "code_diff",
            "mode": "generate_application_scaffold",
            "template_key": template_key,
            "target_dir": target_dir,
            "title": f"Scaffold: {template_key}",
            "summary": f"Prepare scaffold with {len(files)} files in {target_dir}.",
            "files": [
                {
                    "path": path,
                    "original_content": "",
                    "proposed_content": content,
                    "diff": self._build_unified_diff("", content, path),
                }
                for path, content in files.items()
            ],
            "file_tree": sorted(files),
        }
        verification_recipe = self._verification_recipe_for_root(target_dir)
        if verification_recipe is not None:
            metadata["verification_recipe"] = verification_recipe
        draft = self._require_action_broker().create_action_draft(
            account_id=account_id,
            integration_key=LOCAL_CODEGEN_PROVIDER_KEY,
            project_id=project_id,
            action_type="code_apply_preview",
            action_title=f"Generate scaffold: {template_key}",
            action_summary=f"Create scaffold files in {target_dir} after approval.",
            preview_payload={
                **metadata,
                "project_root": target_dir,
            },
            approval_notes="Code scaffold writes remain approval-gated and atomic.",
            created_by="atnos",
        )
        metadata["action_draft_id"] = draft.id
        metadata["draft_status"] = draft.status
        return CodeGenerationPreview(
            mode="generate_application_scaffold",
            action_draft_id=draft.id,
            title=draft.action_title,
            summary=draft.action_summary,
            metadata=metadata,
        )

    def edit_existing_app(
        self,
        *,
        project_root: str,
        task_description: str,
        files_to_modify: list[str] | None = None,
        project_id: int | None = None,
    ) -> CodeGenerationPreview:
        account_id = self._ensure_local_codegen_account_id()
        normalized_task = task_description.strip()
        if not normalized_task:
            raise SelfCodingExecutionError("task_description is required.")
        grant = LocalFileGrantService(grant_root=project_root, allow_write=False)
        selected_files = files_to_modify or self._select_candidate_files(project_root)
        if not selected_files:
            raise SelfCodingExecutionError("No candidate files were available for edit preview.")
        originals: dict[str, str] = {}
        for path in selected_files:
            preview = grant.read_text(path)
            originals[preview.relative_path] = preview.content
        patch_payload = self._generate_edit_patch_payload(
            project_root=project_root,
            task_description=normalized_task,
            originals=originals,
        )
        files = patch_payload.get("files")
        if not isinstance(files, list) or not files:
            raise SelfCodingExecutionError("Codegen returned no files.")
        preview_files: list[dict[str, object]] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path") or "").strip().replace("\\", "/")
            proposed = str(entry.get("content") or "")
            if not path:
                continue
            original = originals.get(path, "")
            preview_files.append(
                {
                    "path": path,
                    "original_content": original,
                    "proposed_content": proposed,
                    "diff": self._build_unified_diff(original, proposed, path),
                }
            )
        if not preview_files:
            raise SelfCodingExecutionError("Codegen returned no valid file changes.")
        metadata = {
            "kind": "code_diff",
            "mode": "edit_existing_app",
            "project_root": project_root,
            "task_description": normalized_task,
            "title": str(patch_payload.get("summary") or "Code change preview"),
            "summary": str(patch_payload.get("summary") or "Prepare code changes for review."),
            "files": preview_files,
            "file_tree": [item["path"] for item in preview_files],
        }
        verification_recipe = self._verification_recipe_for_root(project_root)
        if verification_recipe is not None:
            metadata["verification_recipe"] = verification_recipe
        draft = self._require_action_broker().create_action_draft(
            account_id=account_id,
            integration_key=LOCAL_CODEGEN_PROVIDER_KEY,
            project_id=project_id,
            action_type="code_apply_preview",
            action_title=str(metadata["title"]),
            action_summary=str(metadata["summary"]),
            preview_payload=metadata,
            approval_notes="Code apply writes remain approval-gated and atomic.",
            created_by="atnos",
        )
        metadata["action_draft_id"] = draft.id
        metadata["draft_status"] = draft.status
        return CodeGenerationPreview(
            mode="edit_existing_app",
            action_draft_id=draft.id,
            title=draft.action_title,
            summary=draft.action_summary,
            metadata=metadata,
        )

    def execute_code_apply(self, *, draft_id: int) -> dict[str, object]:
        if self.settings is not None and not self.settings.self_improvement_allow_self_apply:
            reviewer = self.settings.self_improvement_final_reviewer
            raise SelfCodingExecutionError(
                f"Self-improvement code apply is disabled. Final changes require {reviewer} review."
            )
        broker = self._require_action_broker()
        draft = broker.get_action_draft(draft_id)
        if draft is None:
            raise SelfCodingExecutionError(f"Code apply draft {draft_id} was not found.")
        if draft.action_type != "code_apply_preview":
            raise SelfCodingExecutionError("Selected draft is not a code apply preview.")
        if draft.status != "approved":
            raise SelfCodingExecutionError("Code apply draft must be approved before execution.")
        result = ProjectExecutionService(self.session, self.settings).execute_approved_code_apply(draft_id=draft_id)
        return result.to_dict()

    @staticmethod
    def _verification_recipe_for_root(project_root: str) -> str | None:
        """Dobiera tylko znana recepte dla rozpoznanego katalogu Atnosa."""
        root = Path(project_root).expanduser().resolve()
        if (root / "src" / "atnos").is_dir() and (root / "tests").is_dir():
            return "backend_project_command_tests"
        if (root / "package.json").is_file() and (root / "src").is_dir():
            return "frontend_project_command_tests"
        return None

    def run_project_tests(self, *, project_root: str, timeout_s: int = 300) -> ProjectTestRunResult:
        root = Path(project_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise SelfCodingExecutionError("project_root must point to an existing directory.")
        runner, command = self._detect_test_runner(root)
        if command and command[0].lower() in {"sudo", "doas", "runas"}:
            raise SelfCodingExecutionError("Elevated test execution is not allowed.")
        try:
            result = subprocess.run(
                list(command),
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + "\n" + (exc.stderr or "")
            return ProjectTestRunResult(
                runner=runner,
                command=command,
                cwd=str(root),
                exit_code=124,
                summary="Test runner timed out.",
                first_failures=self._extract_failure_lines(output),
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
            )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        summary = "Tests passed." if result.returncode == 0 else "Tests failed."
        return ProjectTestRunResult(
            runner=runner,
            command=command,
            cwd=str(root),
            exit_code=int(result.returncode),
            summary=summary,
            first_failures=self._extract_failure_lines(output),
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _resolve_note(self, note_id: int | None) -> SelfImprovementNote:
        if note_id is not None:
            note = self.notes.get(note_id)
            if note is None:
                raise SelfCodingDraftNotFoundError(f"Unknown self_improvement_note_id '{note_id}'.")
            return note

        note = self.note_service.get_selected_note()
        if note is None:
            raise SelfCodingDraftNotFoundError("No selected improvement note is available for draft preview.")
        return note

    @staticmethod
    def _build_warnings(note: SelfImprovementNote) -> tuple[str, ...]:
        warnings: list[str] = []
        if note.status is not SelfImprovementStatus.SELECTED:
            warnings.append("This note is not currently selected for review.")
        if note.decision is SelfImprovementDecision.REJECTED:
            warnings.append("This note is marked rejected; treat this draft as low priority unless reopened by review.")
        if note.decision is SelfImprovementDecision.UNDECIDED:
            warnings.append("This note is undecided; confirm the review decision before implementation.")
        return tuple(warnings)

    @staticmethod
    def _note_context(note: SelfImprovementNote) -> str:
        proposed_fix = note.proposed_fix or "Brak"
        chosen_next_patch = note.chosen_next_patch or "Brak"
        return "\n".join(
            [
                f"- id: {note.id}",
                f"- source: {note.source}",
                f"- view: {note.view}",
                f"- severity: {note.severity.value}",
                f"- decision: {note.decision.value}",
                f"- status: {note.status.value}",
                f"- friction: {note.friction}",
                f"- proposed_fix: {proposed_fix}",
                f"- chosen_next_patch: {chosen_next_patch}",
            ]
        )

    def _build_patch_plan(self, note: SelfImprovementNote) -> str:
        return "\n".join(
            [
                "# Self-Coding Draft v0 - Patch Plan",
                "",
                "## Goal",
                "",
                self._goal_sentence(note),
                "",
                "## Selected Improvement Note",
                "",
                self._note_context(note),
                "",
                "## Recommended Slice",
                "",
                "- Start with the smallest patch that removes the described friction.",
                "- Prefer deterministic backend/service/UI changes over automation.",
                "- Keep the patch reviewable and reversible.",
                "- Do not add connector writes, Mission Queue integration, Codex execution, or background work.",
                "",
                "## File Discovery",
                "",
                "Najpierw zidentyfikuj minimalne pliki do zmiany. Nie skanuj repo szerzej niz trzeba.",
                "",
                "## Quality Gate",
                "",
                "- Add targeted tests for the changed behavior.",
                "- Run the narrow test file first.",
                "- Run the full relevant backend/frontend gate only after targeted tests pass.",
                "- Do a manual smoke only if runtime/UI/API behavior changed.",
            ]
        )

    def _build_codex_prompt(self, note: SelfImprovementNote) -> str:
        return "\n".join(
            [
                f"# MODE: PATCH PLAN ONLY - {self._short_title(note)}",
                "",
                "## Cel",
                "",
                self._goal_sentence(note),
                "",
                "## Kontekst",
                "",
                self._note_context(note),
                "",
                "Capability roadmap:",
                f"- {CAPABILITY_ROADMAP_PATH}",
                "",
                "## Zakres dozwolony",
                "",
                "- Przygotuj najwezsza implementacje dla tej selected Improvement Note.",
                "- Wskaz minimalne pliki do sprawdzenia i minimalne testy.",
                "- Utrzymaj local-first default oraz preview-before-action model.",
                "",
                "## Zakazane",
                "",
                "- Nie uruchamiaj Codexa z aplikacji.",
                "- Nie dodawaj Mission Queue integration.",
                "- Nie dodawaj GitHub/Linear/Notion/Google/Slack live actions.",
                "- Nie dodawaj Obsidian read/sync.",
                "- Nie dodawaj browser/computer use.",
                "- Nie zmieniaj plikow, runtime ani konfiguracji w tym kroku.",
                "",
                "## Raport",
                "",
                "Podaj rekomendowany slice, zmieniane obszary, testy, ryzyka i decyzje: ready for PATCH ONLY / no patch yet.",
            ]
        )

    def _build_code_draft(self, note: SelfImprovementNote) -> str:
        return "\n".join(
            [
                "# Code Draft Preview",
                "",
                "This is not a patch and does not change files.",
                "",
                "## Suggested Implementation Shape",
                "",
                "- Add or update the smallest service/helper that directly addresses the friction.",
                "- Keep route/UI layers thin if they are needed.",
                "- Reuse existing repository/service patterns instead of adding a new execution layer.",
                "- Preserve selected-note and project-context boundaries.",
                "",
                "## Candidate Data Flow",
                "",
                "selected improvement note -> deterministic service/helper -> preview response/UI copy -> user review",
                "",
                "## Note-Specific Input",
                "",
                self._note_context(note),
                "",
                "## Guardrail",
                "",
                "Do not implement file writes, connector calls, Codex triggers, scheduler jobs, or auto-patching in this draft.",
            ]
        )

    @staticmethod
    def _build_test_plan(note: SelfImprovementNote) -> str:
        return "\n".join(
            [
                "# Test Plan Preview",
                "",
                "## Targeted Tests",
                "",
                "- Selected-note path uses only the current selected note unless an explicit note id is provided.",
                "- Draft/preview output includes friction, severity, decision, proposed_fix and chosen_next_patch.",
                "- Missing optional fields render as Brak.",
                "- Rejected or undecided decisions produce warnings instead of actions.",
                "- No conversations, messages, execution_traces, tasks, connector calls or files are created by preview.",
                "",
                "## Full Validation",
                "",
                "- Run the targeted backend or frontend test file for the touched slice.",
                "- Run full backend or full frontend only for the side that changed.",
                "- Run manual smoke only if runtime/API/UI behavior changed.",
                "",
                "## Current Note",
                "",
                f"- note_id: {note.id}",
                f"- status: {note.status.value}",
                f"- decision: {note.decision.value}",
            ]
        )

    @staticmethod
    def _goal_sentence(note: SelfImprovementNote) -> str:
        if note.proposed_fix:
            return note.proposed_fix
        return f"Address this friction in the smallest safe slice: {note.friction}"

    @staticmethod
    def _short_title(note: SelfImprovementNote) -> str:
        normalized = " ".join(note.view.replace("_", " ").split()).strip()
        if normalized:
            return f"{normalized.title()} Improvement"
        return f"Improvement Note {note.id}"

    def _build_scaffold_files(
        self,
        *,
        app_type: str,
        language: str,
        framework: str,
        description: str,
    ) -> tuple[str, dict[str, str]]:
        normalized = (app_type.strip().lower(), language.strip().lower(), framework.strip().lower())
        template_key: str
        if normalized[0] in {"python-cli", "cli"} or normalized[1:] == ("python", "argparse"):
            template_key = "python_cli"
            files = {
                "main.py": self._render_template("python_cli_main.py.tpl", description=description),
                "setup.py": self._render_template("python_cli_setup.py.tpl", description=description),
            }
        elif normalized[0] == "fastapi" or normalized[2] == "fastapi":
            template_key = "fastapi_app"
            files = {
                "main.py": self._render_template("fastapi_main.py.tpl", description=description),
                "routes.py": self._render_template("fastapi_routes.py.tpl", description=description),
            }
        elif normalized[0] == "react-component":
            template_key = "react_component"
            files = {
                "Component.tsx": self._render_template("react_component.tsx.tpl", description=description),
            }
        elif normalized[0] in {"vite-spa", "spa"} or normalized[2] == "vite":
            template_key = "vite_spa"
            files = {
                "src/App.tsx": self._render_template("vite_app.tsx.tpl", description=description),
                "src/main.tsx": self._render_template("vite_main.tsx.tpl", description=description),
            }
        elif normalized[0] == "tauri-desktop" or normalized[2] == "tauri":
            template_key = "tauri_desktop"
            files = {
                "src/main.rs": self._render_template("tauri_main.rs.tpl", description=description),
                "src-tauri/tauri.conf.json": self._render_template("tauri_conf.json.tpl", description=description),
            }
        else:
            raise SelfCodingExecutionError("Unsupported scaffold target. Use Python CLI, FastAPI, React component, Vite SPA or Tauri desktop.")
        return template_key, files

    def _render_template(self, filename: str, **values: str) -> str:
        template_dir = Path(__file__).with_name("codegen_templates")
        template = (template_dir / filename).read_text(encoding="utf-8")
        return template.format(**values)

    def _select_candidate_files(self, project_root: str) -> list[str]:
        root = Path(project_root).expanduser().resolve()
        candidates = [
            "main.py",
            "app.py",
            "src/main.py",
            "src/App.tsx",
            "src/main.tsx",
            "package.json",
            "pyproject.toml",
        ]
        return [candidate for candidate in candidates if (root / candidate).exists()]

    def _generate_edit_patch_payload(
        self,
        *,
        project_root: str,
        task_description: str,
        originals: dict[str, str],
    ) -> dict[str, object]:
        if self.settings is None or self.provider_registry is None:
            raise SelfCodingExecutionError("Workflow LLM is not configured for edit_existing_app.")
        workflow = WorkflowLLMService(
            self.session,
            settings=self.settings,
            provider_registry=self.provider_registry,
        )
        prompt = self._build_edit_prompt(project_root=project_root, task_description=task_description, originals=originals)
        result = workflow.generate_text(prompt=prompt, task=ModelTask.SYNTHESIS)
        return self._parse_edit_payload(result.content)

    @staticmethod
    def _build_edit_prompt(
        *,
        project_root: str,
        task_description: str,
        originals: dict[str, str],
    ) -> str:
        files_block = json.dumps(
            [{"path": path, "content": content} for path, content in originals.items()],
            ensure_ascii=False,
            indent=2,
        )
        return (
            "You are preparing a supervised multi-file code edit for Atnos.\n"
            "Return ONLY valid JSON with keys summary and files.\n"
            "files must be an array of objects: {path, content}.\n"
            "Every path must already exist in the provided file list.\n"
            "Do not include markdown fences.\n\n"
            f"project_root: {project_root}\n"
            f"task_description: {task_description}\n"
            "files:\n"
            f"{files_block}\n"
        )

    @staticmethod
    def _parse_edit_payload(content: str) -> dict[str, object]:
        raw = content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if "\n" in raw:
                raw = raw.split("\n", 1)[1]
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SelfCodingExecutionError("Workflow LLM returned malformed JSON for code diff preview.") from exc
        if not isinstance(payload, dict):
            raise SelfCodingExecutionError("Workflow LLM returned an invalid code diff payload.")
        return payload

    def _ensure_local_codegen_account_id(self) -> int:
        existing = self.accounts.find_by_provider_and_identifier(
            provider_key=LOCAL_CODEGEN_PROVIDER_KEY,
            account_identifier=LOCAL_CODEGEN_ACCOUNT_IDENTIFIER,
        )
        if existing is not None:
            return existing.id
        account = self.accounts.create(
            label="Local Codegen",
            provider_key=LOCAL_CODEGEN_PROVIDER_KEY,
            account_identifier=LOCAL_CODEGEN_ACCOUNT_IDENTIFIER,
            secret_type="oauth_token_later",
            storage_backend="metadata_only",
            secret_ref=None,
            status="configured",
            risk_level="medium",
            allowed_capabilities_json=["read_metadata", "read_content", "draft_write", "connector_write"],
            linked_integration_key=LOCAL_CODEGEN_PROVIDER_KEY,
            linked_project_id=None,
            notes="Synthetic local account used only for supervised code apply drafts.",
            last_rotated_at=None,
            last_used_at=None,
        )
        return account.id

    def _require_action_broker(self) -> AccountActionBrokerService:
        if self.action_broker is None:
            raise SelfCodingExecutionError("Action broker is not configured.")
        return self.action_broker

    @staticmethod
    def _build_unified_diff(original: str, proposed: str, path: str) -> str:
        diff = difflib.unified_diff(
            original.splitlines(),
            proposed.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
        return "\n".join(diff)

    @staticmethod
    def _detect_test_runner(root: Path) -> tuple[str, tuple[str, ...]]:
        if (root / "go.mod").exists():
            return "go", ("go", "test", "./...")
        package_json = root / "package.json"
        if package_json.exists():
            try:
                package = json.loads(package_json.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                package = {}
            scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
            dev_dependencies = package.get("devDependencies") if isinstance(package.get("devDependencies"), dict) else {}
            dependencies = package.get("dependencies") if isinstance(package.get("dependencies"), dict) else {}
            combined = {**dependencies, **dev_dependencies}
            if "vitest" in combined or "vitest" in scripts:
                return "vitest", ("npx", "vitest", "run")
            if "jest" in combined or "jest" in scripts:
                return "jest", ("npx", "jest", "--runInBand")
        if (root / "pytest.ini").exists() or (root / "tests").exists() or (root / "pyproject.toml").exists():
            return "pytest", (sys.executable, "-m", "pytest")
        raise SelfCodingExecutionError("Could not detect a supported test runner for this project.")

    @staticmethod
    def _extract_failure_lines(output: str) -> tuple[str, ...]:
        lines: list[str] = []
        for line in output.splitlines():
            lowered = line.lower()
            if "failed" in lowered or "error" in lowered:
                stripped = line.strip()
                if stripped:
                    lines.append(stripped)
            if len(lines) >= 3:
                break
        return tuple(lines)
