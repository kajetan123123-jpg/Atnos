from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, datetime
import mimetypes
from pathlib import Path
import re
from typing import Any
import unicodedata
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus, MessageRole, MessageStatus
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService

ALLOWED_RESEARCH_BROWSER_STEPS = frozenset(
    {
        "navigate",
        "wait",
        "click",
        "type",
        "download",
        "extract",
        "verify_state",
        "screenshot",
    }
)


@dataclass(frozen=True, slots=True)
class LearningDownloadDescriptor:
    label: str
    source: str
    artifact: ManagedArtifactDescriptor
    metadata: dict[str, object]


class LearningIngestService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.projects = ProjectRepository(session)
        self.execution_runs = ExecutionRunRepository(session)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.integration_config = IntegrationConfigService(session, settings)
        self.browser = BrowserRecipeRunnerService(settings)
        self.artifacts = OutputArtifactService(settings)

    def run_learning_ingest(self, *, run, job) -> dict[str, Any]:
        payload = dict(job.payload_json or {})
        conversation_id = payload.get("conversation_id") if isinstance(payload.get("conversation_id"), int) else None
        project = self.projects.get(job.project_id)
        project_name = project.name if project is not None else f"project-{job.project_id}"
        goal = str(payload.get("goal") or job.goal or "Learning ingest").strip()
        topic = str(payload.get("topic") or goal or project_name).strip() or project_name

        browser_recipe = payload.get("browser_recipe") if isinstance(payload.get("browser_recipe"), dict) else None
        huggingface_downloads = (
            payload.get("huggingface_downloads")
            if isinstance(payload.get("huggingface_downloads"), list)
            else []
        )
        github_downloads = (
            payload.get("github_downloads")
            if isinstance(payload.get("github_downloads"), list)
            else []
        )

        browser_result: dict[str, Any] | None = None
        browser_artifacts: list[LearningDownloadDescriptor] = []
        if browser_recipe is not None:
            browser_result = self._run_step(
                run=run,
                step_type="graph.browser_research",
                step_name="Browser research",
                input_json={"topic": topic, "recipe_steps": len(browser_recipe.get("steps", []))},
                fn=lambda: self._run_browser_research(browser_recipe),
            )
            browser_artifacts = self._collect_browser_artifacts(
                conversation_id=conversation_id,
                browser_result=browser_result,
            )

        hf_artifacts: list[LearningDownloadDescriptor] = []
        if huggingface_downloads:
            hf_artifacts = self._run_step(
                run=run,
                step_type="graph.huggingface_download",
                step_name="Hugging Face download",
                input_json={"items": huggingface_downloads},
                fn=lambda: self._download_huggingface_resources(
                    conversation_id=conversation_id,
                    downloads=huggingface_downloads,
                ),
            )

        github_artifacts: list[LearningDownloadDescriptor] = []
        if github_downloads:
            github_artifacts = self._run_step(
                run=run,
                step_type="graph.github_download",
                step_name="GitHub download",
                input_json={"items": github_downloads},
                fn=lambda: self._download_github_resources(
                    conversation_id=conversation_id,
                    downloads=github_downloads,
                ),
            )

        note_bundle = self._run_step(
            run=run,
            step_type="graph.memory_update",
            step_name="Obsidian learning note",
            input_json={
                "topic": topic,
                "project_name": project_name,
                "browser_used": browser_result is not None,
                "huggingface_items": len(hf_artifacts),
                "github_items": len(github_artifacts),
            },
            fn=lambda: self._write_learning_note(
                conversation_id=conversation_id,
                project_name=project_name,
                goal=goal,
                topic=topic,
                browser_result=browser_result,
                browser_artifacts=browser_artifacts,
                hf_artifacts=hf_artifacts,
                github_artifacts=github_artifacts,
            ),
        )

        all_artifacts = [*browser_artifacts, *hf_artifacts, *github_artifacts, note_bundle["artifact"]]
        if conversation_id is not None:
            self._attach_learning_message(
                conversation_id=conversation_id,
                topic=topic,
                browser_result=browser_result,
                note_artifact=note_bundle["artifact"].artifact,
                note_vault_path=note_bundle["vault_note_path"],
                downloaded_artifacts=all_artifacts[:-1],
            )

        return {
            "status": "completed",
            "workflow": "learning_ingest",
            "goal": goal,
            "topic": topic,
            "browser_result": browser_result,
            "downloads": [self._serialize_download(item) for item in [*browser_artifacts, *hf_artifacts, *github_artifacts]],
            "artifact": self._serialize_artifact(note_bundle["artifact"].artifact),
            "vault_note_path": note_bundle["vault_note_path"],
            "project_name": project_name,
        }

    def _run_browser_research(self, recipe: dict[str, object]) -> dict[str, Any]:
        steps = recipe.get("steps")
        if not isinstance(steps, list) or not steps:
            raise RuntimeError("browser_recipe must include at least one step.")
        normalized_steps: list[dict[str, object]] = []
        for raw_step in steps:
            if not isinstance(raw_step, dict):
                raise RuntimeError("browser_recipe contains an invalid step.")
            step_type = str(raw_step.get("type") or "").strip().lower()
            if step_type not in ALLOWED_RESEARCH_BROWSER_STEPS:
                raise RuntimeError(f"Browser research step '{step_type}' is not allowed in learning ingest.")
            normalized_steps.append(dict(raw_step))
        safe_recipe = {**recipe, "steps": normalized_steps}
        return self.browser.run(safe_recipe)

    def _download_huggingface_resources(
        self,
        *,
        conversation_id: int | None,
        downloads: list[object],
    ) -> list[LearningDownloadDescriptor]:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise RuntimeError("huggingface_hub is not installed in this runtime.") from exc

        results: list[LearningDownloadDescriptor] = []
        cache_dir = (self.settings.workspace_staging_dir / "huggingface-cache").resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        downloads_root = (self.settings.workspace_staging_dir / "huggingface").resolve()
        downloads_root.mkdir(parents=True, exist_ok=True)

        for index, raw_item in enumerate(downloads):
            if not isinstance(raw_item, dict):
                raise RuntimeError("huggingface_downloads contains an invalid item.")
            repo_id = str(raw_item.get("repo_id") or "").strip()
            filename = str(raw_item.get("filename") or "").strip()
            if not repo_id or not filename:
                raise RuntimeError("Each Hugging Face download requires repo_id and filename.")
            repo_type = str(raw_item.get("repo_type") or "model").strip().lower()
            if repo_type not in {"model", "dataset", "space"}:
                raise RuntimeError(f"Unsupported Hugging Face repo_type '{repo_type}'.")
            revision = str(raw_item.get("revision") or "").strip() or None
            label = str(raw_item.get("label") or f"Hugging Face file {index + 1}").strip()
            target_dir = downloads_root / self._slugify(repo_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            local_path = Path(
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    repo_type=repo_type,
                    revision=revision,
                    local_dir=target_dir,
                    cache_dir=cache_dir,
                )
            ).resolve()
            media_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
            artifact = self.artifacts.finalize_output(
                absolute_path=local_path,
                filename=local_path.name,
                media_type=media_type,
            )
            results.append(
                LearningDownloadDescriptor(
                    label=label,
                    source="huggingface",
                    artifact=artifact,
                    metadata={
                        "repo_id": repo_id,
                        "repo_type": repo_type,
                        "filename": filename,
                        "revision": revision,
                        "conversation_id": conversation_id,
                    },
                )
            )
        return results

    def _download_github_resources(
        self,
        *,
        conversation_id: int | None,
        downloads: list[object],
    ) -> list[LearningDownloadDescriptor]:
        results: list[LearningDownloadDescriptor] = []
        downloads_root = (self.settings.workspace_staging_dir / "github").resolve()
        downloads_root.mkdir(parents=True, exist_ok=True)
        headers = {
            "Accept": "application/vnd.github.raw",
            "User-Agent": f"Atnos/{self.settings.app_version}",
        }

        for index, raw_item in enumerate(downloads):
            if not isinstance(raw_item, dict):
                raise RuntimeError("github_downloads contains an invalid item.")
            owner = str(raw_item.get("owner") or "").strip()
            repo = str(raw_item.get("repo") or "").strip()
            path = str(raw_item.get("path") or "").strip().lstrip("/")
            if not owner or not repo or not path:
                raise RuntimeError("Each GitHub download requires owner, repo and path.")
            ref = str(raw_item.get("ref") or "").strip() or "main"
            label = str(raw_item.get("label") or f"GitHub file {index + 1}").strip()
            target_dir = downloads_root / self._slugify(f"{owner}-{repo}-{ref}")
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = (target_dir / path).resolve()
            try:
                target_path.relative_to(target_dir)
            except ValueError as exc:
                raise RuntimeError("GitHub file path escaped the managed staging directory.") from exc
            target_path.parent.mkdir(parents=True, exist_ok=True)

            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(ref, safe='')}/{quote(path, safe='/')}"
            response = httpx.get(raw_url, headers=headers, timeout=20, follow_redirects=True)
            if response.status_code >= 400:
                api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
                response = httpx.get(api_url, headers=headers, timeout=20, follow_redirects=True)
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"GitHub download failed for {owner}/{repo}/{path} ({response.status_code})."
                    )
                payload = response.json()
                encoded = payload.get("content")
                if not isinstance(encoded, str) or not encoded.strip():
                    raise RuntimeError(f"GitHub response for {owner}/{repo}/{path} had no content.")
                import base64

                file_bytes = base64.b64decode(encoded.encode("utf-8"))
            else:
                file_bytes = response.content

            target_path.write_bytes(file_bytes)
            media_type = mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
            artifact = self.artifacts.finalize_output(
                absolute_path=target_path,
                filename=target_path.name,
                media_type=media_type,
            )
            results.append(
                LearningDownloadDescriptor(
                    label=label,
                    source="github",
                    artifact=artifact,
                    metadata={
                        "owner": owner,
                        "repo": repo,
                        "path": path,
                        "ref": ref,
                        "conversation_id": conversation_id,
                    },
                )
            )
        return results

    def _collect_browser_artifacts(
        self,
        *,
        conversation_id: int | None,
        browser_result: dict[str, Any],
    ) -> list[LearningDownloadDescriptor]:
        artifacts_payload = browser_result.get("artifacts")
        if not isinstance(artifacts_payload, dict):
            return []
        results: list[LearningDownloadDescriptor] = []
        for key, raw_path in artifacts_payload.items():
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            candidate = Path(raw_path).resolve()
            if not candidate.exists() or not candidate.is_file():
                continue
            if candidate.suffix.lower() == ".zip":
                # Trace archives stay in the trace root; we keep them out of the user-facing artifact bundle.
                continue
            media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            artifact = self.artifacts.finalize_output(
                absolute_path=candidate,
                filename=candidate.name,
                media_type=media_type,
            )
            results.append(
                LearningDownloadDescriptor(
                    label=f"Browser artifact: {key}",
                    source="browser",
                    artifact=artifact,
                    metadata={
                        "artifact_key": key,
                        "conversation_id": conversation_id,
                    },
                )
            )
        return results

    def _write_learning_note(
        self,
        *,
        conversation_id: int | None,
        project_name: str,
        goal: str,
        topic: str,
        browser_result: dict[str, Any] | None,
        browser_artifacts: list[LearningDownloadDescriptor],
        hf_artifacts: list[LearningDownloadDescriptor],
        github_artifacts: list[LearningDownloadDescriptor],
    ) -> dict[str, object]:
        markdown = self._build_learning_markdown(
            project_name=project_name,
            goal=goal,
            topic=topic,
            browser_result=browser_result,
            browser_artifacts=browser_artifacts,
            hf_artifacts=hf_artifacts,
            github_artifacts=github_artifacts,
        )
        artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-learning-ingest.md",
            content=markdown,
        )
        vault_note_path = self._write_obsidian_note(topic=topic, content=markdown)
        return {
            "artifact": LearningDownloadDescriptor(
                label="Learning ingest note",
                source="obsidian",
                artifact=artifact,
                metadata={"vault_note_path": vault_note_path},
            ),
            "vault_note_path": vault_note_path,
        }

    def _write_markdown_artifact(
        self,
        *,
        conversation_id: int | None,
        filename: str,
        content: str,
    ) -> ManagedArtifactDescriptor:
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".md")
        output_path.write_text(content, encoding="utf-8")
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename=filename,
            media_type="text/markdown",
        )

    def _write_obsidian_note(self, *, topic: str, content: str) -> str:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists:
            raise RuntimeError("Obsidian vault is not configured or is unavailable.")
        safe_topic = self._slugify(topic)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Education" / "LearningSessions" / f"{stamp}-learning-ingest-{safe_topic}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError as exc:
            raise RuntimeError("Learning note path escaped the Obsidian vault.") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _attach_learning_message(
        self,
        *,
        conversation_id: int,
        topic: str,
        browser_result: dict[str, Any] | None,
        note_artifact: ManagedArtifactDescriptor,
        note_vault_path: str,
        downloaded_artifacts: list[LearningDownloadDescriptor],
    ) -> None:
        extracted_values = self._browser_extract_values(browser_result)
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Przygotowalem pakiet nauki dla tematu `{topic}`.\n\n"
                f"- browser research: {'tak' if browser_result is not None else 'nie'}\n"
                f"- pobrane artefakty: {len(downloaded_artifacts)}\n"
                f"- vault note: {note_vault_path}\n"
                f"- ekstrakty browsera: {', '.join(extracted_values) if extracted_values else 'brak'}"
            ),
            metadata_json={
                "learning_ingest": True,
                "topic": topic,
                "vault_note_path": note_vault_path,
                "browser_result": browser_result,
            },
        )
        descriptors = [*downloaded_artifacts, LearningDownloadDescriptor(
            label="Learning ingest note",
            source="obsidian",
            artifact=note_artifact,
            metadata={"vault_note_path": note_vault_path},
        )]
        for item in descriptors:
            self.attachments.create(
                filename=item.artifact.filename,
                storage_path=item.artifact.storage_path,
                kind=self._attachment_kind_for_extension(item.artifact.file_extension),
                conversation_id=conversation_id,
                message_id=assistant_message.id,
                source=AttachmentSource.GENERATED,
                status=AttachmentStatus.AVAILABLE,
                media_type=item.artifact.media_type,
                file_extension=item.artifact.file_extension,
                size_bytes=item.artifact.size_bytes,
                extracted_text=None,
                metadata_json={
                    "generated_by": "learning_ingest",
                    "label": item.label,
                    "source": item.source,
                    **dict(item.metadata or {}),
                },
            )
        self.session.commit()

    def _build_learning_markdown(
        self,
        *,
        project_name: str,
        goal: str,
        topic: str,
        browser_result: dict[str, Any] | None,
        browser_artifacts: list[LearningDownloadDescriptor],
        hf_artifacts: list[LearningDownloadDescriptor],
        github_artifacts: list[LearningDownloadDescriptor],
    ) -> str:
        generated_at = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# Learning Ingest - {topic}",
            "",
            f"- generated_at: {generated_at}",
            f"- project: {project_name}",
            f"- goal: {goal}",
            "",
            "## Browser research",
            "",
        ]
        if browser_result is None:
            lines.append("- browser research was not used")
        else:
            lines.append(f"- final_url: {browser_result.get('finalUrl') or 'unknown'}")
            extracted_values = self._browser_extract_values(browser_result)
            if extracted_values:
                lines.append("- extracted_values:")
                lines.extend(f"  - {value}" for value in extracted_values[:4])
            else:
                lines.append("- extracted_values: brak")
            if browser_artifacts:
                lines.append("- browser_artifacts:")
                lines.extend(f"  - {item.artifact.storage_path}" for item in browser_artifacts)

        lines.extend(["", "## Hugging Face artifacts", ""])
        if hf_artifacts:
            for item in hf_artifacts:
                lines.append(
                    f"- {item.label}: `{item.metadata.get('repo_id')}` / `{item.metadata.get('filename')}` -> `{item.artifact.storage_path}`"
                )
        else:
            lines.append("- none")

        lines.extend(["", "## GitHub artifacts", ""])
        if github_artifacts:
            for item in github_artifacts:
                lines.append(
                    f"- {item.label}: `{item.metadata.get('owner')}/{item.metadata.get('repo')}` / `{item.metadata.get('path')}` @ `{item.metadata.get('ref')}` -> `{item.artifact.storage_path}`"
                )
        else:
            lines.append("- none")

        lines.extend(
            [
                "",
                "## What ATNOS should learn from this pass",
                "",
                "- Uzywaj publicznego browser research do benchmarku shortow i wzorcow hooka.",
                "- Trzymaj pobrane publiczne artefakty na F: w kontrolowanym stagingu, nie w losowych cache.",
                "- Czytaj publiczne repozytoria GitHuba selektywnie: README, docs i kluczowe pliki kodu, zamiast dumpowac cale repo.",
                "- Zapisuj tylko skondensowane notatki atomowe do Obsidiana, bez dumpowania calej strony.",
                "",
                "## Next experiments",
                "",
                "- Porownaj 3 hooki z benchmarku i zapisz, ktory payoff jest najszybszy.",
                "- Rozszerz ingest o drugi publiczny dataset albo drugi benchmark page.",
                "- Dodaj GitHub connector OAuth tylko dla prywatnych repo i metadanych, nie mieszaj go z publicznym ingestem.",
                "- Po 24-48h zapisz reflection do MEMORY, jesli ten wsad poprawi kolejnego shorta.",
                "",
            ]
        )
        return "\n".join(lines)

    def _run_step(
        self,
        *,
        run,
        step_type: str,
        step_name: str,
        input_json: dict[str, Any],
        fn,
    ):
        step = self.execution_runs.create_step(
            execution_run_id=run.id,
            step_index=len(self.execution_runs.list_steps(run.id)),
            step_type=step_type,
            step_name=step_name,
            status="running",
            selector_hint=None,
            input_json=input_json,
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=datetime.now(tz=UTC),
            finished_at=None,
        )
        self.session.commit()
        try:
            result = fn()
            step.status = "done"
            step.output_json = self._serialize_step_output(result)
            step.finished_at = datetime.now(tz=UTC)
            self.execution_runs.save_step(step)
            self.session.commit()
            return result
        except Exception as exc:
            step.status = "failed"
            step.output_json = {}
            step.error_message = str(exc)
            step.finished_at = datetime.now(tz=UTC)
            self.execution_runs.save_step(step)
            self.session.commit()
            raise

    def _serialize_step_output(self, value: Any) -> dict[str, Any]:
        if isinstance(value, ManagedArtifactDescriptor):
            return self._serialize_artifact(value)
        if isinstance(value, LearningDownloadDescriptor):
            return self._serialize_download(value)
        if isinstance(value, Path):
            return {"path": value.as_posix()}
        if isinstance(value, datetime):
            return {"value": value.isoformat()}
        if is_dataclass(value):
            return self._serialize_step_output(asdict(value))
        if isinstance(value, dict):
            return {str(key): self._serialize_jsonish(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return {"items": [self._serialize_jsonish(item) for item in value]}
        return {"value": self._serialize_jsonish(value)}

    def _serialize_jsonish(self, value: Any) -> Any:
        if isinstance(value, ManagedArtifactDescriptor):
            return self._serialize_artifact(value)
        if isinstance(value, LearningDownloadDescriptor):
            return self._serialize_download(value)
        if isinstance(value, Path):
            return value.as_posix()
        if isinstance(value, datetime):
            return value.isoformat()
        if is_dataclass(value):
            return {
                field.name: self._serialize_jsonish(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, dict):
            return {str(key): self._serialize_jsonish(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._serialize_jsonish(item) for item in value]
        return value

    @staticmethod
    def _serialize_artifact(artifact: ManagedArtifactDescriptor) -> dict[str, Any]:
        return {
            "filename": artifact.filename,
            "storage_path": artifact.storage_path,
            "media_type": artifact.media_type,
            "file_extension": artifact.file_extension,
            "size_bytes": artifact.size_bytes,
        }

    @classmethod
    def _serialize_download(cls, item: LearningDownloadDescriptor) -> dict[str, Any]:
        return {
            "label": item.label,
            "source": item.source,
            "artifact": cls._serialize_artifact(item.artifact),
            "metadata": dict(item.metadata or {}),
        }

    @staticmethod
    def _browser_extract_values(browser_result: dict[str, Any] | None) -> list[str]:
        if not browser_result:
            return []
        raw_results = browser_result.get("results")
        if not isinstance(raw_results, list):
            return []
        extracted: list[str] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if isinstance(value, str):
                cleaned = " ".join(value.split()).strip()
                if cleaned:
                    extracted.append(cleaned)
        return extracted

    @staticmethod
    def _attachment_kind_for_extension(extension: str) -> AttachmentKind:
        normalized = extension.lower()
        if normalized in {".png", ".jpg", ".jpeg", ".webp"}:
            return AttachmentKind.IMAGE
        if normalized in {".wav", ".mp3", ".m4a"}:
            return AttachmentKind.AUDIO
        if normalized in {".mp4", ".mov", ".webm"}:
            return AttachmentKind.VIDEO
        if normalized in {".md", ".txt", ".json", ".csv"}:
            return AttachmentKind.TEXT
        if normalized in {".pdf", ".docx", ".pptx", ".xlsx"}:
            return AttachmentKind.DOCUMENT
        return AttachmentKind.OTHER

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
        return cleaned or "learning-note"
