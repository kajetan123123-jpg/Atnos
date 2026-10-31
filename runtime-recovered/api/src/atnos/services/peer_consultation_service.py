from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

import httpx
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import AttachmentKind, AttachmentSource, AttachmentStatus, MessageRole, MessageStatus, ModelTask, ProviderPosture
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService
from atnos.services.workflow_llm_service import WorkflowLLMService


@dataclass(frozen=True, slots=True)
class AdvisorReply:
    advisor_key: str
    advisor_label: str
    status: str
    provider_key: str
    model_key: str
    content: str
    reason: str | None = None
    external: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "advisor_key": self.advisor_key,
            "advisor_label": self.advisor_label,
            "status": self.status,
            "provider_key": self.provider_key,
            "model_key": self.model_key,
            "content": self.content,
            "reason": self.reason,
            "external": self.external,
        }


class PeerConsultationService:
    OPENAI_MODEL_CANDIDATES = ("gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1")
    ANTHROPIC_MODEL_CANDIDATES = ("claude-sonnet-4-20250514", "claude-3-7-sonnet-latest")
    MAX_CONTEXT_CHARS_PER_NOTE = 2800
    MAX_BULLETS_PER_SECTION = 4
    MAX_LOCAL_ADVISORS = 2

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
        self.projects = ProjectRepository(session)
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.account_credentials = AccountCredentialsService(session, settings)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.artifacts = OutputArtifactService(settings)
        self.integration_config = IntegrationConfigService(session, settings)
        self.workflow_llm = WorkflowLLMService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )

    def run_project_consultation(
        self,
        *,
        project_id: int,
        conversation_id: int | None,
        goal: str,
        topic_hint: str | None = None,
    ) -> dict[str, Any]:
        project = self.projects.get(project_id)
        project_name = project.name if project is not None else f"project-{project_id}"
        context_bundle = self._build_context_bundle(project_name=project_name, goal=goal, topic_hint=topic_hint)
        prompt = self._build_advisor_prompt(context_bundle=context_bundle)

        replies: list[AdvisorReply] = []
        replies.extend(self._ask_local_advisors(prompt))

        external_allowed = self._external_advisors_allowed()
        if external_allowed:
            replies.append(self._ask_anthropic_advisor(prompt))
            replies.append(self._ask_openai_advisor(prompt))
        else:
            replies.append(
                AdvisorReply(
                    advisor_key="anthropic-cloud",
                    advisor_label="Anthropic advisor",
                    status="skipped",
                    provider_key="anthropic-cloud",
                    model_key="n/a",
                    content="",
                    reason="External fallback is disabled by execution policy.",
                    external=True,
                )
            )
            replies.append(
                AdvisorReply(
                    advisor_key="openai-cloud",
                    advisor_label="OpenAI advisor",
                    status="skipped",
                    provider_key="openai-cloud",
                    model_key="n/a",
                    content="",
                    reason="External fallback is disabled by execution policy.",
                    external=True,
                )
            )

        merged = self._merge_recommendations(replies)
        markdown = self._build_markdown_report(
            project_name=project_name,
            goal=goal,
            topic_hint=topic_hint,
            context_bundle=context_bundle,
            replies=replies,
            merged=merged,
        )
        artifact = self._write_markdown_artifact(
            conversation_id=conversation_id,
            filename="atnos-peer-consultation.md",
            content=markdown,
        )
        vault_note_path = self._write_obsidian_note(project_name=project_name, content=markdown)
        if conversation_id is not None:
            self._attach_consultation_message(
                conversation_id=conversation_id,
                project_name=project_name,
                replies=replies,
                artifact=artifact,
                vault_note_path=vault_note_path,
            )
        return {
            "status": "completed",
            "project_name": project_name,
            "goal": goal,
            "topic_hint": topic_hint,
            "advisors": [item.to_dict() for item in replies],
            "merged_recommendations": merged,
            "artifact": self._serialize_artifact(artifact),
            "vault_note_path": vault_note_path,
            "external_advisors_used": [
                item.advisor_key for item in replies if item.external and item.status == "completed"
            ],
        }

    def _ask_local_advisors(self, prompt: str) -> list[AdvisorReply]:
        model_names = self._discover_local_models()
        if not model_names:
            return [self._ask_local_advisor(prompt)]

        replies: list[AdvisorReply] = []
        for model_name in model_names[: self.MAX_LOCAL_ADVISORS]:
            replies.append(self._ask_local_model_advisor(model_name=model_name, prompt=prompt))
        return replies

    def _ask_local_advisor(self, prompt: str) -> AdvisorReply:
        try:
            result = self.workflow_llm.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            )
            return AdvisorReply(
                advisor_key="local-advisor",
                advisor_label="Local advisor",
                status="completed",
                provider_key=result.provider_key,
                model_key=result.model_key,
                content=result.content,
                reason=result.routing_reason,
                external=False,
            )
        except Exception as exc:
            return AdvisorReply(
                advisor_key="local-advisor",
                advisor_label="Local advisor",
                status="failed",
                provider_key="ollama-local",
                model_key="unknown",
                content="",
                reason=str(exc),
                external=False,
            )

    def _ask_local_model_advisor(self, *, model_name: str, prompt: str) -> AdvisorReply:
        advisor_label = "Visual advisor" if "vl" in model_name.lower() else "Local advisor"
        advisor_key = f"ollama-{self._slugify(model_name)}"
        model_prompt = prompt
        if "vl" in model_name.lower():
            model_prompt = (
                f"{prompt}\n\n"
                "Dodatkowy nacisk dla tego doradcy:\n"
                "- oceniaj przede wszystkim sile obrazu,\n"
                "- rytm kadrow,\n"
                "- czy payoff wizualny jest natychmiast czytelny,\n"
                "- dawaj uwagi jak visual-first reviewer.\n"
            )

        try:
            payload: dict[str, Any] = {
                "model": model_name,
                "messages": [{"role": "user", "content": model_prompt}],
                "stream": False,
                "think": False,
                "options": {"num_predict": 900},
            }
            with httpx.Client(timeout=self.settings.ollama_timeout_s) as client:
                response = client.post(f"{self.settings.ollama_base_url.rstrip('/')}/api/chat", json=payload)
            if response.status_code >= 400:
                raise RuntimeError(self._extract_http_error(response))
            raw_payload = response.json()
            message = raw_payload.get("message") if isinstance(raw_payload, dict) else None
            if isinstance(message, dict):
                content = str(message.get("content") or "").strip()
            else:
                content = str(raw_payload.get("response") or "").strip() if isinstance(raw_payload, dict) else ""
            if not content:
                raise RuntimeError("Ollama returned an empty response.")
            return AdvisorReply(
                advisor_key=advisor_key,
                advisor_label=f"{advisor_label} ({model_name})",
                status="completed",
                provider_key="ollama-local",
                model_key=model_name,
                content=content,
                reason="Direct local advisor call.",
                external=False,
            )
        except Exception as exc:
            return AdvisorReply(
                advisor_key=advisor_key,
                advisor_label=f"{advisor_label} ({model_name})",
                status="failed",
                provider_key="ollama-local",
                model_key=model_name,
                content="",
                reason=self._sanitize_reason(str(exc)),
                external=False,
            )

    def _ask_anthropic_advisor(self, prompt: str) -> AdvisorReply:
        return self._ask_cloud_advisor(
            advisor_key="anthropic-cloud",
            advisor_label="Anthropic advisor",
            account_provider_key="anthropic-cloud",
            endpoint="https://api.anthropic.com/v1/messages",
            model_candidates=self.ANTHROPIC_MODEL_CANDIDATES,
            builder=self._build_anthropic_request,
            parser=self._parse_anthropic_response,
            headers_builder=lambda secret: {
                "x-api-key": secret,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            prompt=prompt,
        )

    def _ask_openai_advisor(self, prompt: str) -> AdvisorReply:
        return self._ask_cloud_advisor(
            advisor_key="openai-cloud",
            advisor_label="OpenAI advisor",
            account_provider_key="openai-cloud",
            endpoint="https://api.openai.com/v1/chat/completions",
            model_candidates=self.OPENAI_MODEL_CANDIDATES,
            builder=self._build_openai_request,
            parser=self._parse_openai_response,
            headers_builder=lambda secret: {
                "Authorization": f"Bearer {secret}",
                "content-type": "application/json",
            },
            prompt=prompt,
        )

    def _ask_cloud_advisor(
        self,
        *,
        advisor_key: str,
        advisor_label: str,
        account_provider_key: str,
        endpoint: str,
        model_candidates: tuple[str, ...],
        builder,
        parser,
        headers_builder,
        prompt: str,
    ) -> AdvisorReply:
        account = self._get_configured_account_by_provider(account_provider_key)
        if account is None:
            return AdvisorReply(
                advisor_key=advisor_key,
                advisor_label=advisor_label,
                status="unavailable",
                provider_key=account_provider_key,
                model_key="n/a",
                content="",
                reason="No configured account secret is available in the local vault.",
                external=True,
            )
        secret = self.account_credentials.resolve_secret_for_adapter(account.id)
        if not secret:
            return AdvisorReply(
                advisor_key=advisor_key,
                advisor_label=advisor_label,
                status="unavailable",
                provider_key=account_provider_key,
                model_key="n/a",
                content="",
                reason="Secret could not be resolved from the local vault.",
                external=True,
            )
        secret_validation_error = self._validate_secret_for_provider(provider_key=account_provider_key, secret=secret)
        if secret_validation_error:
            return AdvisorReply(
                advisor_key=advisor_key,
                advisor_label=advisor_label,
                status="unavailable",
                provider_key=account_provider_key,
                model_key="n/a",
                content="",
                reason=secret_validation_error,
                external=True,
            )

        last_error = "No supported model candidate succeeded."
        for model_name in model_candidates:
            try:
                with httpx.Client(timeout=75) as client:
                    response = client.post(
                        endpoint,
                        headers=headers_builder(secret),
                        json=builder(model_name, prompt),
                    )
                if response.status_code >= 400:
                    last_error = self._extract_http_error(response)
                    if response.status_code in {400, 404}:
                        continue
                    raise RuntimeError(last_error)
                content = parser(response.json())
                if not content:
                    last_error = "Advisor returned empty content."
                    continue
                return AdvisorReply(
                    advisor_key=advisor_key,
                    advisor_label=advisor_label,
                    status="completed",
                    provider_key=account_provider_key,
                    model_key=model_name,
                    content=content,
                    reason="Vault-backed direct advisor call.",
                    external=True,
                )
            except Exception as exc:
                last_error = self._sanitize_reason(str(exc))
                continue

        return AdvisorReply(
            advisor_key=advisor_key,
            advisor_label=advisor_label,
            status="failed",
            provider_key=account_provider_key,
            model_key=model_candidates[0],
            content="",
            reason=self._sanitize_reason(last_error),
            external=True,
        )

    def _discover_local_models(self) -> list[str]:
        candidate_order: list[str] = []
        for configured in (
            self.settings.ollama_document_model,
            self.settings.ollama_chat_model,
            self.settings.ollama_vision_model,
            "qwen3:8b",
            "qwen3-vl:8b",
        ):
            if isinstance(configured, str) and configured.strip():
                candidate_order.append(configured.strip())

        try:
            with httpx.Client(timeout=self.settings.ollama_health_timeout_s) as client:
                response = client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            if response.status_code < 400:
                payload = response.json()
                models = payload.get("models")
                if isinstance(models, list):
                    for item in models:
                        if not isinstance(item, dict):
                            continue
                        name = item.get("name")
                        if isinstance(name, str) and name.strip():
                            candidate_order.append(name.strip())
        except Exception:
            pass

        unique: list[str] = []
        seen: set[str] = set()
        for model_name in candidate_order:
            lowered = model_name.lower()
            if lowered in seen:
                continue
            unique.append(model_name)
            seen.add(lowered)
        return unique

    def _build_context_bundle(
        self,
        *,
        project_name: str,
        goal: str,
        topic_hint: str | None,
    ) -> dict[str, str]:
        note_map = {
            "memory": self._read_vault_note("Atnos/MEMORY.md"),
            "benchmark": self._read_vault_note("Atnos/Social/Ideas/2026-05-09-Shorts-Benchmark-Feed.md"),
            "hooks": self._read_vault_note("Atnos/Social/Templates/Shorts-Hook-Library.md"),
            "quality_rules": self._read_vault_note("Atnos/Education/Patterns/ATNOS-Shorts-Quality-Rules.md"),
        }
        context: dict[str, str] = {
            "project_name": project_name,
            "goal": goal.strip(),
            "topic_hint": (topic_hint or "").strip(),
        }
        for key, value in note_map.items():
            if value:
                context[key] = value[: self.MAX_CONTEXT_CHARS_PER_NOTE]
        return context

    def _build_advisor_prompt(self, *, context_bundle: dict[str, str]) -> str:
        return (
            "Jestes zewnetrznym doradca dla systemu ATNOS, ktory ma tworzyc profesjonalne shorty.\n"
            "Masz wskazac tylko to, co realnie poprawi jakosc, retention i workflow.\n"
            "Zwracaj TYLKO poprawny JSON.\n"
            'Schemat: {"missing_knowledge":[""], "workflow_changes":[""], "quality_checks":[""], "next_experiments":[""], "pitfalls":[""], "what_atnos_should_learn_next":[""]}\n\n'
            f"Projekt: {context_bundle.get('project_name')}\n"
            f"Cel teraz: {context_bundle.get('goal')}\n"
            f"Hint tematu: {context_bundle.get('topic_hint') or 'brak'}\n\n"
            "Pamiec Atnosa:\n"
            f"{context_bundle.get('memory', 'brak')}\n\n"
            "Benchmark shortow:\n"
            f"{context_bundle.get('benchmark', 'brak')}\n\n"
            "Biblioteka hookow:\n"
            f"{context_bundle.get('hooks', 'brak')}\n\n"
            "Reguly jakosci:\n"
            f"{context_bundle.get('quality_rules', 'brak')}\n\n"
            "Zasady odpowiedzi:\n"
            "- bez lania wody,\n"
            "- bez korpomowy,\n"
            "- dawaj rzeczy wykonawcze,\n"
            "- skup sie na shortach premium, retention i sile obrazu,\n"
            "- jesli widzisz brakujaca wiedze lub brakujacy etap workflow, nazwij go wprost.\n"
        )

    def _merge_recommendations(self, replies: list[AdvisorReply]) -> dict[str, Any]:
        sections: dict[str, list[str]] = {
            "missing_knowledge": [],
            "workflow_changes": [],
            "quality_checks": [],
            "next_experiments": [],
            "pitfalls": [],
            "what_atnos_should_learn_next": [],
        }
        seen: dict[str, set[str]] = {key: set() for key in sections}

        for reply in replies:
            if reply.status != "completed" or not reply.content.strip():
                continue
            parsed = self._parse_advisor_payload(reply.content)
            for section_name, items in parsed.items():
                if section_name not in sections:
                    continue
                for item in items:
                    normalized = item.strip()
                    if not normalized:
                        continue
                    lowered = normalized.lower()
                    if lowered in seen[section_name]:
                        continue
                    sections[section_name].append(normalized)
                    seen[section_name].add(lowered)

        for key in sections:
            sections[key] = sections[key][: self.MAX_BULLETS_PER_SECTION]

        action_priority = [
            *sections["workflow_changes"][:2],
            *sections["quality_checks"][:2],
            *sections["next_experiments"][:2],
        ]
        sections["action_priority"] = action_priority[:5]
        return sections

    def _parse_advisor_payload(self, raw: str) -> dict[str, list[str]]:
        cleaned = raw.strip()
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                payload = json.loads(json_match.group(0))
                if isinstance(payload, dict):
                    return {
                        str(key): [str(item).strip() for item in value if str(item).strip()]
                        if isinstance(value, list)
                        else [str(value).strip()] if isinstance(value, str) and value.strip() else []
                        for key, value in payload.items()
                    }
            except json.JSONDecodeError:
                pass

        bullets = [line.strip("-*• \n\r\t") for line in cleaned.splitlines() if line.strip()]
        fallback = bullets[: self.MAX_BULLETS_PER_SECTION]
        return {
            "workflow_changes": fallback,
            "quality_checks": fallback,
            "next_experiments": fallback[:2],
            "pitfalls": [],
            "missing_knowledge": [],
            "what_atnos_should_learn_next": [],
        }

    def _build_markdown_report(
        self,
        *,
        project_name: str,
        goal: str,
        topic_hint: str | None,
        context_bundle: dict[str, str],
        replies: list[AdvisorReply],
        merged: dict[str, Any],
    ) -> str:
        generated_at = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"# Peer Consultation - {project_name}",
            "",
            f"- generated_at: {generated_at}",
            f"- goal: {goal}",
            f"- topic_hint: {topic_hint or 'brak'}",
            "",
            "## Advisors reached",
            "",
        ]
        for reply in replies:
            reason = f" | {reply.reason}" if reply.reason else ""
            lines.append(
                f"- {reply.advisor_label}: {reply.status} | provider={reply.provider_key} | model={reply.model_key}{reason}"
            )

        lines.extend(
            [
                "",
                "## Merged action priority",
                "",
            ]
        )
        for item in merged.get("action_priority", []):
            lines.append(f"- {item}")

        for section_name, heading in (
            ("missing_knowledge", "Missing knowledge"),
            ("workflow_changes", "Workflow changes"),
            ("quality_checks", "Quality checks"),
            ("next_experiments", "Next experiments"),
            ("pitfalls", "Pitfalls"),
            ("what_atnos_should_learn_next", "What ATNOS should learn next"),
        ):
            lines.extend(["", f"## {heading}", ""])
            section_items = merged.get(section_name, [])
            if section_items:
                for item in section_items:
                    lines.append(f"- {item}")
            else:
                lines.append("- none")

        lines.extend(["", "## Raw advisor outputs", ""])
        for reply in replies:
            lines.extend(
                [
                    f"### {reply.advisor_label}",
                    "",
                    f"- status: {reply.status}",
                    f"- provider: {reply.provider_key}",
                    f"- model: {reply.model_key}",
                    "",
                    "```text",
                    reply.content.strip() or (reply.reason or "no output"),
                    "```",
                    "",
                ]
            )

        lines.extend(
            [
                "## Context bundle used",
                "",
                f"- memory_chars: {len(context_bundle.get('memory', ''))}",
                f"- benchmark_chars: {len(context_bundle.get('benchmark', ''))}",
                f"- hooks_chars: {len(context_bundle.get('hooks', ''))}",
                f"- quality_rules_chars: {len(context_bundle.get('quality_rules', ''))}",
            ]
        )
        return "\n".join(lines).strip() + "\n"

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

    def _write_obsidian_note(self, *, project_name: str, content: str) -> str | None:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists:
            return None
        safe_project = self._slugify(project_name)
        stamp = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d-%H-%M-%S")
        relative = Path("Atnos") / "Education" / "LearningSessions" / f"{stamp}-peer-consultation-{safe_project}.md"
        target = (config.vault_path.resolve() / relative).resolve()
        try:
            target.relative_to(config.vault_path.resolve())
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _attach_consultation_message(
        self,
        *,
        conversation_id: int,
        project_name: str,
        replies: list[AdvisorReply],
        artifact: ManagedArtifactDescriptor,
        vault_note_path: str | None,
    ) -> None:
        completed = [item.advisor_label for item in replies if item.status == "completed"]
        assistant_message = self.messages.create(
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=(
                f"Przygotowalem konsultacje z innymi AI dla projektu `{project_name}`.\n\n"
                f"- aktywni doradcy: {', '.join(completed) if completed else 'brak'}\n"
                f"- notatka robocza: {artifact.filename}\n"
                f"- vault_note: {vault_note_path or 'brak'}"
            ),
            metadata_json={
                "peer_consultation": True,
                "advisors": [item.to_dict() for item in replies],
                "vault_note_path": vault_note_path,
            },
        )
        self.attachments.create(
            filename=artifact.filename,
            storage_path=artifact.storage_path,
            kind=AttachmentKind.TEXT,
            conversation_id=conversation_id,
            message_id=assistant_message.id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=artifact.media_type,
            file_extension=artifact.file_extension,
            size_bytes=artifact.size_bytes,
            extracted_text=None,
            metadata_json={"generated_by": "peer_consultation", "label": "Peer consultation note"},
        )
        self.session.commit()

    def _external_advisors_allowed(self) -> bool:
        owner = self.users.get_owner()
        if owner is None:
            return False
        policy = self.execution_policies.get_for_user(owner.id)
        return bool(policy and policy.allow_external_fallback)

    def _get_configured_account_by_provider(self, provider_key: str):
        for item in self.account_credentials.list_accounts():
            if item.provider_key != provider_key:
                continue
            if item.status != "configured":
                continue
            if not item.has_secret:
                continue
            return item
        return None

    def _read_vault_note(self, relative_path: str) -> str:
        config = self.integration_config.get_obsidian_config()
        if config.vault_path is None or not config.path_exists:
            return ""
        candidate = (config.vault_path.resolve() / Path(relative_path)).resolve()
        try:
            candidate.relative_to(config.vault_path.resolve())
        except ValueError:
            return ""
        if not candidate.exists() or not candidate.is_file():
            return ""
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _serialize_artifact(artifact: ManagedArtifactDescriptor) -> dict[str, Any]:
        return {
            "filename": artifact.filename,
            "storage_path": artifact.storage_path,
            "media_type": artifact.media_type,
            "file_extension": artifact.file_extension,
            "size_bytes": artifact.size_bytes,
        }

    @staticmethod
    def _build_openai_request(model_name: str, prompt: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 900,
        }

    @staticmethod
    def _parse_openai_response(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
        return ""

    @staticmethod
    def _build_anthropic_request(model_name: str, prompt: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "max_tokens": 900,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }

    @staticmethod
    def _parse_anthropic_response(payload: dict[str, Any]) -> str:
        content_blocks = payload.get("content")
        if not isinstance(content_blocks, list):
            return ""
        fragments: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                fragments.append(block["text"])
        return "".join(fragments).strip()

    @staticmethod
    def _extract_http_error(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return PeerConsultationService._sanitize_reason(message.strip())
        return PeerConsultationService._sanitize_reason(f"HTTP {response.status_code}")

    @staticmethod
    def _validate_secret_for_provider(*, provider_key: str, secret: str) -> str | None:
        trimmed = secret.strip()
        if provider_key == "openai-cloud" and trimmed.startswith("sk-ant-"):
            return "Configured OpenAI vault secret looks like an Anthropic key. Fix the OpenAI provider card."
        if provider_key == "anthropic-cloud" and (trimmed.startswith("sk-proj-") or trimmed.startswith("sk-")) and not trimmed.startswith("sk-ant-"):
            return "Configured Anthropic vault secret does not look like an Anthropic key. Fix the Anthropic provider card."
        return None

    @staticmethod
    def _sanitize_reason(value: str) -> str:
        redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[redacted-api-key]", value)
        redacted = re.sub(r"AIza[0-9A-Za-z\\-_]{20,}", "[redacted-api-key]", redacted)
        return redacted

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower()).strip("-")
        return cleaned or "project"
