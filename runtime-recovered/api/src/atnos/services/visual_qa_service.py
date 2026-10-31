from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask, ProviderPosture
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.providers.base import LocalProviderRuntimeError
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.context_builder_service import LocalAttachmentMetadata, ProviderExecutionContext
from atnos.services.model_selection_service import ModelSelectionService
from atnos.services.screen_capture_service import ScreenArtifact, ScreenCaptureService


class VisualQAError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VisualQAResult:
    status: str
    goal: str
    screenshot_path: Path | None
    recording_path: Path | None
    review_provider_key: str | None
    review_model_key: str | None
    review_text: str | None
    prompt_path: Path
    report_path: Path
    codex_handoff_path: Path
    message: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        for key in ("screenshot_path", "recording_path", "prompt_path", "report_path", "codex_handoff_path"):
            value = payload[key]
            payload[key] = str(value) if value is not None else None
        return payload


class VisualQAService:
    """Creates a screen-review package and optional model review for Codex handoff."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.capture_service = ScreenCaptureService(settings)
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.selection = ModelSelectionService(
            session,
            provider_registry=provider_registry,
            settings=settings,
        )
        self.reviews_dir = settings.visual_qa_dir / "reviews"
        self.handoff_dir = settings.visual_qa_dir / "codex-handoff"
        self.log_path = settings.visual_qa_log_path

    def run_review(
        self,
        *,
        goal: str,
        area: str = "Atnos UI",
        extra_context: str = "",
        screenshot_path: Path | None = None,
        capture_screenshot: bool = True,
        include_recording_path: Path | None = None,
        allow_cloud_review: bool | None = None,
    ) -> VisualQAResult:
        normalized_goal = " ".join(goal.strip().split())
        if not normalized_goal:
            raise VisualQAError("goal cannot be empty.")

        self._ensure_dirs()
        screenshot_artifact: ScreenArtifact | None = None
        if screenshot_path is None and capture_screenshot:
            screenshot_artifact = self.capture_service.capture_screenshot(label="visual-qa")
            screenshot_path = screenshot_artifact.path
        elif screenshot_path is not None:
            screenshot_path = screenshot_path.resolve()
            if not screenshot_path.exists():
                raise VisualQAError(f"screenshot_path does not exist: {screenshot_path}")
            screenshot_path = self._copy_input_artifact_if_needed(screenshot_path, "captures")

        recording_path = include_recording_path
        if recording_path is not None:
            recording_path = recording_path.resolve()
            if not recording_path.exists():
                raise VisualQAError(f"recording_path does not exist: {recording_path}")
            recording_path = self._copy_input_artifact_if_needed(recording_path, "recordings")

        cloud_allowed = self.settings.visual_qa_allow_cloud_default if allow_cloud_review is None else allow_cloud_review
        prompt = self.build_review_prompt(
            goal=normalized_goal,
            area=area,
            extra_context=extra_context,
            screenshot_path=screenshot_path,
            recording_path=recording_path,
        )
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"visual-qa-{stamp}-{uuid4().hex[:8]}"
        prompt_path = (self.reviews_dir / f"{run_id}_prompt.md").resolve()
        prompt_path.write_text(prompt, encoding="utf-8")

        review_text: str | None = None
        provider_key: str | None = None
        model_key: str | None = None
        status = "package_ready"
        message = "Prepared Visual QA package for Codex handoff. Cloud review was not requested."

        if screenshot_path is not None:
            try:
                review_text, provider_key, model_key = self._run_provider_review(
                    prompt=prompt,
                    screenshot_path=screenshot_path,
                    allow_cloud_review=cloud_allowed,
                )
                status = "review_complete"
                message = "Visual review completed and Codex handoff package is ready."
            except Exception as exc:
                review_text = f"VISUAL_REVIEW_FAILED: {type(exc).__name__}: {exc}"
                if isinstance(exc, LocalProviderRuntimeError):
                    review_text += "\n" + json.dumps(
                        exc.failure_detail.to_dict(),
                        ensure_ascii=False,
                        indent=2,
                    )
                status = "review_failed_package_ready"
                message = "Visual review failed, but Codex handoff package was still created."

        codex_handoff = self.build_codex_handoff_prompt(
            goal=normalized_goal,
            area=area,
            screenshot_path=screenshot_path,
            recording_path=recording_path,
            review_text=review_text,
        )
        codex_handoff_path = (self.handoff_dir / f"{run_id}_codex_handoff.md").resolve()
        codex_handoff_path.write_text(codex_handoff, encoding="utf-8")

        result = VisualQAResult(
            status=status,
            goal=normalized_goal,
            screenshot_path=screenshot_path,
            recording_path=recording_path,
            review_provider_key=provider_key,
            review_model_key=model_key,
            review_text=review_text,
            prompt_path=prompt_path,
            report_path=(self.reviews_dir / f"{run_id}_report.json").resolve(),
            codex_handoff_path=codex_handoff_path,
            message=message,
        )
        result.report_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._log(result)
        return result

    def _run_provider_review(
        self,
        *,
        prompt: str,
        screenshot_path: Path,
        allow_cloud_review: bool,
    ) -> tuple[str, str, str]:
        owner = self.users.get_owner()
        if owner is None:
            raise VisualQAError("Owner user is not configured.")
        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise VisualQAError("Execution policy is not configured.")

        posture = ProviderPosture.IMPROVE_WHEN_NEEDED if allow_cloud_review else ProviderPosture.STRICT_LOCAL
        selection = self.selection.select_for_task(
            task=ModelTask.VISION,
            execution_policy=execution_policy,
            provider_posture=posture,
            internet_enabled=allow_cloud_review,
        )
        adapter = self.provider_registry.get(selection.provider.key)
        if adapter is None:
            raise VisualQAError(f"No runtime adapter registered for provider '{selection.provider.key}'.")

        context = ProviderExecutionContext(
            provider_prompt=prompt,
            summary="visual QA review",
            visible_attachments=tuple(),
            local_attachments=(
                LocalAttachmentMetadata(
                    attachment_id=-1,
                    storage_path=str(screenshot_path),
                    checksum=None,
                ),
            ),
            allow_external_attachment_content=allow_cloud_review,
            allow_external_attachment_metadata=allow_cloud_review,
            project_context_applied=False,
            active_document_attachment_id=None,
            provider_prompt_chars=len(prompt),
            active_document_preview_chars=0,
            active_document_preview_limit=None,
            selected_review_note_id=None,
            capability_snapshot=None,
        )
        provider_result = adapter.execute(
            task=ModelTask.VISION,
            context=context,
            provider=selection.provider,
            model_config=selection.model_config,
        )
        return provider_result.content, selection.provider.key, selection.model_config.key

    @staticmethod
    def build_review_prompt(
        *,
        goal: str,
        area: str,
        extra_context: str,
        screenshot_path: Path | None,
        recording_path: Path | None,
    ) -> str:
        context_block = extra_context.strip() or "Brak dodatkowego kontekstu."
        screenshot_block = str(screenshot_path) if screenshot_path is not None else "Brak screenshota."
        recording_block = str(recording_path) if recording_path is not None else "Brak nagrania."
        return f"""Jestes krytycznym reviewerem produktu Atnos.

Cel produktu:
- Atnos ma byc prostym, osobistym agentem AI, nie arena modeli.
- Glowne powierzchnie: Chat i Ustawienia.
- Priorytety: czytelnosc, szybkosc codziennej pracy, jakosc contentu, brak debugowego chaosu.
- UX ma pokazywac co agent realnie robi, bez udawania postepu.

Zadanie review:
Ocen widoczny ekran/artefakt i wskaz, co warto poprawic lub naprawic.

Obszar: {area}
Cel aktualnej iteracji: {goal}
Dodatkowy kontekst: {context_block}
Screenshot: {screenshot_block}
Nagranie ekranu: {recording_block}

Zwroc krotko:
1. Najwiekszy problem jakosciowy.
2. 3-5 konkretnych zmian do zrobienia.
3. Co moze poczekac.
4. Ryzyka prywatnosci albo automatyzacji.
5. Finalna rekomendacje: APPLY / REVIEW_FIRST / SKIP.
"""

    @staticmethod
    def build_codex_handoff_prompt(
        *,
        goal: str,
        area: str,
        screenshot_path: Path | None,
        recording_path: Path | None,
        review_text: str | None,
    ) -> str:
        return f"""Jestes Codexem pracujacym nad Atnosem.

Cel Atnosa:
Prosty osobisty agent AI. Chat + Ustawienia jako glowne powierzchnie. Bez areny, bez debugowego balaganu, bez losowej automatyzacji.

Zadanie:
Przeanalizuj feedback z Visual QA i zastosuj tylko te zmiany, ktore pasuja do kierunku produktu. Jesli feedback jest zly albo sprzeczny z celem, odrzuc go i zaproponuj lepsza poprawke.

Obszar: {area}
Cel iteracji: {goal}
Screenshot: {screenshot_path or 'brak'}
Nagranie: {recording_path or 'brak'}

Feedback z AI/review:
{review_text or 'Brak automatycznego review. Ocen samodzielnie na podstawie artefaktow.'}

Wymagania:
- Nie zmieniaj niepowiazanych rzeczy.
- Nie dodawaj nowej nawigacji.
- Nie publikuj nic na zewnatrz.
- Nie wysylaj sekretow.
- Zapisuj lokalne artefakty w kanonicznym katalogu danych Atnosa na E:.
- Po zmianach uruchom sensowna walidacje i podaj wynik.
"""

    def _ensure_dirs(self) -> None:
        self.reviews_dir.mkdir(parents=True, exist_ok=True)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _copy_input_artifact_if_needed(self, path: Path, subdir: str) -> Path:
        if self._is_atnos_owned_path(path):
            return path

        target_dir = self.settings.visual_qa_dir / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix or ".bin"
        target = (
            target_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_imported_{uuid4().hex[:8]}{suffix}"
        ).resolve()
        shutil.copy2(path, target)
        return target

    def _is_atnos_owned_path(self, path: Path) -> bool:
        resolved = path.resolve()
        allowed_roots = (
            self.settings.app_root,
            self.settings.data_dir,
            self.settings.visual_qa_dir,
        )
        for root in allowed_roots:
            if root is None:
                continue
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def _log(self, result: VisualQAResult) -> None:
        payload = result.to_dict()
        payload["timestamp"] = datetime.now().isoformat()
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
