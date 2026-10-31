from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask, ProviderPosture
from atnos.plugins.registry import PluginRegistry
from atnos.providers.base import ProviderExecutionResult, ProviderUnavailableError, ProviderUsage
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.context_builder_service import ProviderExecutionContext
from atnos.services.model_selection_service import ModelSelectionService


@dataclass(frozen=True, slots=True)
class WorkflowLLMResult:
    content: str
    provider_key: str
    model_key: str
    routing_reason: str
    raw_result: ProviderExecutionResult


class WorkflowLLMService:
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
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.selection = ModelSelectionService(
            session,
            provider_registry=provider_registry,
            settings=settings,
        )

    def generate_text(
        self,
        *,
        prompt: str,
        task: ModelTask = ModelTask.SYNTHESIS,
        provider_posture: ProviderPosture = ProviderPosture.PREFER_LOCAL,
        force_external: bool = False,
    ) -> WorkflowLLMResult:
        owner = self.users.get_owner()
        if owner is None:
            raise RuntimeError("Owner user is not configured.")
        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise RuntimeError("Execution policy is not configured for the owner user.")

        selection = self.selection.select_for_task(
            task=task,
            execution_policy=execution_policy,
            provider_posture=provider_posture,
            internet_enabled=True,
            force_external=force_external,
        )
        adapter = self.provider_registry.get(selection.provider.key)
        if adapter is None:
            raise RuntimeError(f"No runtime adapter registered for provider '{selection.provider.key}'.")

        context = ProviderExecutionContext(
            provider_prompt=prompt.strip(),
            summary=f"workflow task={task.value}",
            visible_attachments=tuple(),
            local_attachments=tuple(),
            allow_external_attachment_content=False,
            allow_external_attachment_metadata=False,
            project_context_applied=False,
            active_document_attachment_id=None,
            provider_prompt_chars=len(prompt),
            active_document_preview_chars=0,
            active_document_preview_limit=None,
            selected_review_note_id=None,
            capability_snapshot=None,
        )
        try:
            result = adapter.execute(
                task=task,
                context=context,
                provider=selection.provider,
                model_config=selection.model_config,
            )
        except ProviderUnavailableError:
            if selection.provider.key != "ollama-local":
                raise
            result = self._direct_ollama_fallback(
                adapter=adapter,
                task=task,
                context=context,
                selection=selection,
            )
        return WorkflowLLMResult(
            content=result.content,
            provider_key=selection.provider.key,
            model_key=selection.model_config.key,
            routing_reason=selection.reason,
            raw_result=result,
        )

    def _direct_ollama_fallback(
        self,
        *,
        adapter,
        task: ModelTask,
        context: ProviderExecutionContext,
        selection,
    ) -> ProviderExecutionResult:
        import requests

        model_name = adapter._resolve_model_name(selection.model_config, task=task)
        user_message: dict[str, object] = {
            "role": "user",
            "content": context.provider_prompt,
        }
        if task == ModelTask.VISION:
            user_message["images"] = adapter._build_image_payload(context.local_attachments)
        payload: dict[str, object] = {
            "model": model_name,
            "messages": [user_message],
            "stream": False,
        }
        generation_max_tokens = adapter._resolve_generation_budget(selection.model_config, task=task)
        if generation_max_tokens is not None:
            payload["options"] = {"num_predict": generation_max_tokens}
        thinking_mode = adapter._resolve_thinking_mode(model_name=model_name, task=task)
        if thinking_mode is not None:
            payload["think"] = thinking_mode

        response = requests.post(
            f"{(selection.provider.endpoint_url or self.settings.ollama_base_url).rstrip('/')}/api/chat",
            json=payload,
            timeout=self.settings.ollama_timeout_s,
        )
        if response.status_code >= 400:
            raise ProviderUnavailableError("Ollama fallback transport failed.")
        data = response.json()
        content = adapter._extract_chat_content(data)
        if not content:
            raise ProviderUnavailableError("Ollama fallback transport returned an empty response.")
        input_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")
        total_tokens = None
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens
        return ProviderExecutionResult(
            content=content,
            provider_message_id=data.get("created_at"),
            usage=ProviderUsage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
                total_tokens=total_tokens,
            ),
            finish_reason=data.get("done_reason"),
            raw_metadata={
                "provider_key": selection.provider.key,
                "runtime_model": data.get("model", model_name),
                "fallback_transport": "requests_chat",
                "thinking_mode": thinking_mode,
            },
        )
