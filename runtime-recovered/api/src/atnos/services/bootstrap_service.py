from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from logging import getLogger
from time import perf_counter
from typing import TYPE_CHECKING, Callable, Iterator

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from atnos.core.config import Settings

logger = getLogger(__name__)

CORE_PROVIDER_KEYS = ("ollama-local", "anthropic-cloud", "openai-cloud", "gemini-cloud")
CORE_PLUGIN_KEYS = ("local-files", "document-tools")
CORE_MODEL_KEYS = (
    "ollama-local/chat-default",
    "ollama-local/orchestration-default",
    "ollama-local/document-default",
    "ollama-local/vision-default",
    "ollama-local/code-default",
    "ollama-local/research-default",
    "anthropic-cloud/chat-default",
    "anthropic-cloud/orchestration-default",
    "anthropic-cloud/vision-default",
    "anthropic-cloud/document-default",
    "anthropic-cloud/code-default",
    "openai-cloud/chat-default",
    "openai-cloud/orchestration-default",
    "openai-cloud/vision-default",
    "openai-cloud/document-default",
    "gemini-cloud/research-default",
    "gemini-cloud/chat-default",
)


@dataclass(slots=True, frozen=True)
class BootstrapResult:
    owner_id: int
    providers_seeded: int
    models_seeded: int
    plugins_seeded: int


class BootstrapService:
    def __init__(
        self,
        session: "Session",
        settings: "Settings",
        *,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.progress_callback = progress_callback
        self._users = None
        self._execution_policies = None
        self._style_preferences = None
        self._providers = None
        self._model_configs = None
        self._plugins = None
        self._runtime_settings = None

    def _cached(self, attr_name: str, factory):
        value = getattr(self, attr_name)
        if value is None:
            value = factory()
            setattr(self, attr_name, value)
        return value

    @property
    def users(self):
        def factory():
            from atnos.repositories.user_repository import UserRepository

            return UserRepository(self.session)

        return self._cached("_users", factory)

    @property
    def execution_policies(self):
        def factory():
            from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository

            return ExecutionPolicyRepository(self.session)

        return self._cached("_execution_policies", factory)

    @property
    def style_preferences(self):
        def factory():
            from atnos.repositories.user_style_preferences_repository import UserStylePreferencesRepository

            return UserStylePreferencesRepository(self.session)

        return self._cached("_style_preferences", factory)

    @property
    def providers(self):
        def factory():
            from atnos.repositories.provider_repository import ProviderRepository

            return ProviderRepository(self.session)

        return self._cached("_providers", factory)

    @property
    def model_configs(self):
        def factory():
            from atnos.repositories.model_config_repository import ModelConfigRepository

            return ModelConfigRepository(self.session)

        return self._cached("_model_configs", factory)

    @property
    def plugins(self):
        def factory():
            from atnos.repositories.plugin_repository import PluginRepository

            return PluginRepository(self.session)

        return self._cached("_plugins", factory)

    @property
    def runtime_settings(self):
        def factory():
            from atnos.services.runtime_settings_service import RuntimeSettingsService

            return RuntimeSettingsService(
                self.session,
                self.settings,
                progress_callback=self.progress_callback,
            )

        return self._cached("_runtime_settings", factory)

    def ensure_local_first_defaults(self) -> BootstrapResult:
        warm_result = self._try_warm_bootstrap_result()
        if warm_result is not None:
            return warm_result

        from atnos.db.models.enums import (
            ExecutionAutomationLevel,
            ExecutionVerificationMode,
            JarvisAddressAs,
            ModelQualityTier,
            ModelTask,
            PersonaProfile,
            PluginPermissionPolicy,
            PluginSource,
            ProviderAuthMode,
            ProviderKind,
            ProviderPosture,
            ProviderTransport,
            ResponseLengthPreference,
            ResponseStructure,
            ResponseTone,
        )
        from atnos.services.ollama_model_roles_service import OllamaModelRolesService

        with self._trace("bootstrap_defaults/owner"):
            owner = self.users.get_owner()
            if owner is None:
                owner = self.users.create(
                    display_name="Owner",
                    handle="owner",
                    locale="pl-PL",
                    timezone="Europe/Warsaw",
                    is_owner=True,
                )

        with self._trace("bootstrap_defaults/execution_policy"):
            existing_policy = self.execution_policies.get_for_user(owner.id)
            if existing_policy is None:
                self.execution_policies.create(
                    user_id=owner.id,
                    automation_level=ExecutionAutomationLevel.ASSISTED,
                    verification_mode=ExecutionVerificationMode.STRICT,
                    allow_local_file_read=True,
                    allow_local_file_write=True,
                    allow_web_read=True,
                    allow_external_fallback=True,
                    allow_external_attachment_metadata=True,
                    allow_external_attachment_content=False,
                    prefer_local_llm=True,
                    default_provider_posture=ProviderPosture.STRICT_LOCAL,
                    default_persona_profile=PersonaProfile.JARVIS_MODE,
                    jarvis_address_as=JarvisAddressAs.NONE,
                    max_external_attachment_text_chars=2000,
                    max_daily_cost=100000.0,
                    max_monthly_cost=100000.0,
                )
            else:
                self.execution_policies.update(
                    existing_policy,
                    automation_level=ExecutionAutomationLevel.ASSISTED,
                    verification_mode=ExecutionVerificationMode.STRICT,
                    allow_local_file_read=True,
                    allow_local_file_write=True,
                    allow_web_read=True,
                    allow_external_fallback=True,
                    allow_external_attachment_metadata=True,
                    allow_external_attachment_content=False,
                    prefer_local_llm=True,
                    default_provider_posture=ProviderPosture.STRICT_LOCAL,
                    default_persona_profile=PersonaProfile.JARVIS_MODE,
                    max_daily_cost=100000.0,
                    max_monthly_cost=100000.0,
                )

        with self._trace("bootstrap_defaults/style_preferences"):
            if self.style_preferences.get_for_user(owner.id) is None:
                self.style_preferences.create(
                    user_id=owner.id,
                    response_length=ResponseLengthPreference.MEDIUM,
                    tone=ResponseTone.NATURAL,
                    structure=ResponseStructure.MIXED,
                    criticality_level=3,
                    supportiveness_level=3,
                    professional_default=True,
                )

        with self._trace("bootstrap_defaults/runtime_controls"):
            controls = self.runtime_settings.get_or_create_owner_controls()
            self.runtime_settings.update_owner_controls(
                existing_controls=controls,
                browser_control_enabled=True,
                browser_allow_any_url=False,
                external_actions_enabled=True,
                scheduler_enabled=True,
                oauth_enabled=True,
                autonomy_mode="supervised",
                emergency_stop_enabled=False,
                pause_all_jobs=False,
                login_automation_enabled=False,
                two_factor_automation_enabled=False,
                publish_automation_enabled=False,
                default_retry_budget=max(3, controls.default_retry_budget),
                default_vram_floor_gb=max(6, controls.default_vram_floor_gb),
            )

        providers_seeded = 0
        models_seeded = 0
        plugins_seeded = 0

        with self._trace("bootstrap_defaults/providers"):
            if self.providers.get_by_key("ollama-local") is None:
                local_provider = self.providers.create(
                    key="ollama-local",
                    label="Ollama Local",
                    vendor="ollama",
                    kind=ProviderKind.LOCAL_LLM,
                    transport=ProviderTransport.LOCAL_RUNTIME,
                    auth_mode=ProviderAuthMode.LOCAL,
                    endpoint_url="http://127.0.0.1:11434",
                    is_enabled=True,
                    is_builtin=True,
                    local_first=True,
                    supports_chat=True,
                    supports_vision=True,
                    supports_audio_in=False,
                    supports_audio_out=False,
                    supports_image_generation=False,
                )
                providers_seeded += 1
            else:
                local_provider = self.providers.get_by_key("ollama-local")
                local_provider.supports_vision = True
                self.session.add(local_provider)

            if self.providers.get_by_key("anthropic-cloud") is None:
                anthropic_provider = self.providers.create(
                    key="anthropic-cloud",
                    label="Anthropic Cloud",
                    vendor="anthropic",
                    kind=ProviderKind.REMOTE_LLM,
                    transport=ProviderTransport.ANTHROPIC,
                    auth_mode=ProviderAuthMode.API_KEY,
                    endpoint_url="https://api.anthropic.com",
                    is_enabled=True,
                    is_builtin=True,
                    local_first=False,
                    supports_chat=True,
                    supports_vision=True,
                    supports_audio_in=False,
                    supports_audio_out=False,
                    supports_image_generation=False,
                )
                providers_seeded += 1
            else:
                anthropic_provider = self.providers.get_by_key("anthropic-cloud")

            if self.providers.get_by_key("openai-cloud") is None:
                openai_provider = self.providers.create(
                    key="openai-cloud",
                    label="OpenAI Cloud",
                    vendor="openai",
                    kind=ProviderKind.REMOTE_LLM,
                    transport=ProviderTransport.OPENAI_COMPATIBLE,
                    auth_mode=ProviderAuthMode.API_KEY,
                    endpoint_url="https://api.openai.com",
                    is_enabled=True,
                    is_builtin=True,
                    local_first=False,
                    supports_chat=True,
                    supports_vision=True,
                    supports_audio_in=False,
                    supports_audio_out=False,
                    supports_image_generation=False,
                )
                providers_seeded += 1
            else:
                openai_provider = self.providers.get_by_key("openai-cloud")

            if self.providers.get_by_key("gemini-cloud") is None:
                gemini_provider = self.providers.create(
                    key="gemini-cloud",
                    label="Google Gemini",
                    vendor="google",
                    kind=ProviderKind.REMOTE_LLM,
                    transport=ProviderTransport.CUSTOM,
                    auth_mode=ProviderAuthMode.API_KEY,
                    endpoint_url="https://generativelanguage.googleapis.com",
                    is_enabled=True,
                    is_builtin=True,
                    local_first=False,
                    supports_chat=True,
                    supports_vision=True,
                    supports_audio_in=False,
                    supports_audio_out=False,
                    supports_image_generation=False,
                )
                providers_seeded += 1
            else:
                gemini_provider = self.providers.get_by_key("gemini-cloud")

            legacy_grok_provider = self.providers.get_by_key("grok-cloud")
            if legacy_grok_provider is not None:
                changed = False
                if legacy_grok_provider.is_enabled:
                    legacy_grok_provider.is_enabled = False
                    changed = True
                for model_config in self.model_configs.list_all():
                    if model_config.provider_id != legacy_grok_provider.id:
                        continue
                    if model_config.is_enabled or model_config.is_default:
                        model_config.is_enabled = False
                        model_config.is_default = False
                        self.session.add(model_config)
                        changed = True
                if changed:
                    self.session.add(legacy_grok_provider)
                    self.session.flush()
                    logger.info("Disabled legacy grok-cloud provider rows during MVP bootstrap.")

        with self._trace("bootstrap_defaults/models"):
            local_chat_model = (
                self.settings.ollama_fast_model
                or self.settings.ollama_chat_model
                or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
            )
            local_main_model = (
                self.settings.ollama_main_model
                or self.settings.ollama_chat_model
                or self.settings.ollama_orchestration_model
                or OllamaModelRolesService.DEFAULT_MAIN_TEXT_MODEL
            )
            local_vision_model = self.settings.ollama_vision_model or OllamaModelRolesService.DEFAULT_VISION_MODEL
            local_model_specs = (
                {
                    "key": "ollama-local/chat-default",
                    "label": "Ollama Chat Default",
                    "runtime_model_name": local_chat_model,
                    "task": ModelTask.GENERAL_CHAT,
                    "context_window": 40960,
                    "max_output_tokens": 8192,
                    "supports_tools": True,
                    "supports_vision": False,
                    "notes": "Fast local text model for chat and routine reasoning.",
                },
                {
                    "key": "ollama-local/orchestration-default",
                    "label": "Ollama Orchestration Default",
                    "runtime_model_name": local_main_model,
                    "task": ModelTask.ORCHESTRATION,
                    "context_window": 40960,
                    "max_output_tokens": 8192,
                    "supports_tools": True,
                    "supports_vision": False,
                    "notes": "Main local text brain for planning and agent orchestration.",
                },
                {
                    "key": "ollama-local/document-default",
                    "label": "Ollama Document Default",
                    "runtime_model_name": local_main_model,
                    "task": ModelTask.SYNTHESIS,
                    "context_window": 40960,
                    "max_output_tokens": 8192,
                    "supports_tools": True,
                    "supports_vision": False,
                    "notes": "Main local text brain for synthesis, scripts, and content quality passes.",
                },
                {
                    "key": "ollama-local/vision-default",
                    "label": "Ollama Vision Default",
                    "runtime_model_name": local_vision_model,
                    "task": ModelTask.VISION,
                    "context_window": 262144,
                    "max_output_tokens": 4096,
                    "supports_tools": False,
                    "supports_vision": True,
                    "notes": "Local vision model for screenshots, UI review, thumbnails, and image analysis.",
                },
                {
                    "key": "ollama-local/code-default",
                    "label": "Ollama Code Default",
                    "runtime_model_name": local_main_model,
                    "task": ModelTask.CODING,
                    "context_window": 40960,
                    "max_output_tokens": 8192,
                    "supports_tools": True,
                    "supports_vision": False,
                    "notes": "Local coding fallback used when privacy posture blocks cloud execution.",
                },
                {
                    "key": "ollama-local/research-default",
                    "label": "Ollama Research Default",
                    "runtime_model_name": local_main_model,
                    "task": ModelTask.SEARCH_RESEARCH,
                    "context_window": 40960,
                    "max_output_tokens": 8192,
                    "supports_tools": True,
                    "supports_vision": False,
                    "notes": "Local research fallback used when privacy posture blocks cloud execution.",
                },
            )
            for spec in local_model_specs:
                existing_model = self.model_configs.get_by_key(spec["key"])
                payload = {
                    "provider_id": local_provider.id,
                    "key": spec["key"],
                    "label": spec["label"],
                    "runtime_model_name": spec["runtime_model_name"],
                    "task": spec["task"],
                    "quality_tier": ModelQualityTier.STANDARD,
                    "context_window": spec["context_window"],
                    "max_output_tokens": spec["max_output_tokens"],
                    "is_enabled": True,
                    "is_default": True,
                    "supports_tools": spec["supports_tools"],
                    "supports_vision": spec["supports_vision"],
                    "notes": spec["notes"],
                }
                if existing_model is None:
                    self.model_configs.create(**payload)
                    models_seeded += 1
                else:
                    self._sync_model_config(existing_model, **payload)

            if self.model_configs.get_by_key("anthropic-cloud/chat-default") is None:
                self.model_configs.create(
                    provider_id=anthropic_provider.id,
                    key="anthropic-cloud/chat-default",
                    label="Claude Chat Default",
                    runtime_model_name="claude-sonnet-4-20250514",
                    task=ModelTask.GENERAL_CHAT,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=200000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=True,
                    supports_vision=True,
                    notes="Default external fallback chat model for MVP.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("anthropic-cloud/orchestration-default") is None:
                self.model_configs.create(
                    provider_id=anthropic_provider.id,
                    key="anthropic-cloud/orchestration-default",
                    label="Claude Orchestration Default",
                    runtime_model_name="claude-sonnet-4-20250514",
                    task=ModelTask.ORCHESTRATION,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=200000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=True,
                    supports_vision=False,
                    notes="Default external orchestration model for heavier planning turns.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("anthropic-cloud/vision-default") is None:
                self.model_configs.create(
                    provider_id=anthropic_provider.id,
                    key="anthropic-cloud/vision-default",
                    label="Claude Vision Default",
                    runtime_model_name="claude-sonnet-4-20250514",
                    task=ModelTask.VISION,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=200000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=True,
                    notes="Default external vision model for attachment analysis intent.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("anthropic-cloud/document-default") is None:
                self.model_configs.create(
                    provider_id=anthropic_provider.id,
                    key="anthropic-cloud/document-default",
                    label="Claude Document Default",
                    runtime_model_name="claude-sonnet-4-20250514",
                    task=ModelTask.SYNTHESIS,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=200000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=False,
                    notes="Default external synthesis model for document workflow intent.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("anthropic-cloud/code-default") is None:
                self.model_configs.create(
                    provider_id=anthropic_provider.id,
                    key="anthropic-cloud/code-default",
                    label="Claude Code Default",
                    runtime_model_name="claude-sonnet-4-20250514",
                    task=ModelTask.CODING,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=200000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=True,
                    supports_vision=False,
                    notes="Default Claude coding model route for repository and debugging turns.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("openai-cloud/chat-default") is None:
                self.model_configs.create(
                    provider_id=openai_provider.id,
                    key="openai-cloud/chat-default",
                    label="OpenAI Chat Default",
                    runtime_model_name="gpt-5.2",
                    task=ModelTask.GENERAL_CHAT,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=400000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=True,
                    supports_vision=True,
                    notes="Default OpenAI fallback chat model for MVP.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("openai-cloud/orchestration-default") is None:
                self.model_configs.create(
                    provider_id=openai_provider.id,
                    key="openai-cloud/orchestration-default",
                    label="OpenAI Orchestration Default",
                    runtime_model_name="gpt-5.2",
                    task=ModelTask.ORCHESTRATION,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=400000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=True,
                    supports_vision=True,
                    notes="Default OpenAI orchestration model route.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("openai-cloud/vision-default") is None:
                self.model_configs.create(
                    provider_id=openai_provider.id,
                    key="openai-cloud/vision-default",
                    label="OpenAI Vision Default",
                    runtime_model_name="gpt-5.2",
                    task=ModelTask.VISION,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=400000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=True,
                    notes="Default OpenAI vision model for attachment analysis intent.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("openai-cloud/document-default") is None:
                self.model_configs.create(
                    provider_id=openai_provider.id,
                    key="openai-cloud/document-default",
                    label="OpenAI Document Default",
                    runtime_model_name="gpt-5.2",
                    task=ModelTask.SYNTHESIS,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=400000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=True,
                    notes="Default OpenAI synthesis model for document workflow intent.",
                )
                models_seeded += 1

            if self.model_configs.get_by_key("gemini-cloud/research-default") is None:
                self.model_configs.create(
                    provider_id=gemini_provider.id,
                    key="gemini-cloud/research-default",
                    label="Gemini Research Default",
                    runtime_model_name=self.settings.gemini_model,
                    task=ModelTask.SEARCH_RESEARCH,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=1000000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=True,
                    notes="Default Gemini route for search and research-style turns.",
                )
                models_seeded += 1
            else:
                self._sync_model_config(
                    self.model_configs.get_by_key("gemini-cloud/research-default"),
                    runtime_model_name=self.settings.gemini_model,
                    context_window=1000000,
                    max_output_tokens=4096,
                    supports_vision=True,
                    notes="Default Gemini route for search and research-style turns.",
                )

            if self.model_configs.get_by_key("gemini-cloud/chat-default") is None:
                self.model_configs.create(
                    provider_id=gemini_provider.id,
                    key="gemini-cloud/chat-default",
                    label="Gemini Chat Default",
                    runtime_model_name=self.settings.gemini_model,
                    task=ModelTask.GENERAL_CHAT,
                    quality_tier=ModelQualityTier.DEEP,
                    context_window=1000000,
                    max_output_tokens=4096,
                    is_enabled=True,
                    is_default=True,
                    supports_tools=False,
                    supports_vision=True,
                    notes="Optional Gemini chat route for cloud-first project posture.",
                )
                models_seeded += 1
            else:
                self._sync_model_config(
                    self.model_configs.get_by_key("gemini-cloud/chat-default"),
                    runtime_model_name=self.settings.gemini_model,
                    context_window=1000000,
                    max_output_tokens=4096,
                    supports_vision=True,
                    notes="Optional Gemini chat route for cloud-first project posture.",
                )

        with self._trace("bootstrap_defaults/plugins"):
            if self.plugins.get_by_key("local-files") is None:
                self.plugins.create(
                    key="local-files",
                    name="Local Files",
                    summary="Read and write local project files with explicit policy checks.",
                    source=PluginSource.BUILTIN,
                    permission_policy=PluginPermissionPolicy.ASK,
                    requires_confirmation=True,
                    is_enabled=True,
                )
                plugins_seeded += 1

            if self.plugins.get_by_key("document-tools") is None:
                self.plugins.create(
                    key="document-tools",
                    name="Document Tools",
                    summary="Prepare structured document workflows for DOCX, PPTX, and Markdown.",
                    source=PluginSource.BUILTIN,
                    permission_policy=PluginPermissionPolicy.ASK,
                    requires_confirmation=True,
                    is_enabled=True,
                )
                plugins_seeded += 1

            for plugin in self.plugins.list_all():
                self.plugins.update(
                    plugin,
                    is_enabled=True,
                    permission_policy=PluginPermissionPolicy.ASK,
                    requires_confirmation=True,
                )

        with self._trace("bootstrap_defaults/session_flush"):
            self.session.flush()
        return BootstrapResult(
            owner_id=owner.id,
            providers_seeded=providers_seeded,
            models_seeded=models_seeded,
            plugins_seeded=plugins_seeded,
        )

    def _try_warm_bootstrap_result(self) -> BootstrapResult | None:
        """Fast path for already-seeded local databases.

        This deliberately uses small SQL checks instead of ORM repositories so a warm
        startup does not import the full model/repository graph only to discover that
        all default rows already exist.
        """
        with self._trace("bootstrap_defaults/warm_check"):
            owner_id = self.session.execute(
                text("SELECT id FROM users WHERE is_owner = 1 LIMIT 1")
            ).scalar_one_or_none()
            if owner_id is None:
                return None

            required_singleton_tables = (
                ("execution_policies", "user_id"),
                ("user_style_preferences", "user_id"),
                ("runtime_control_settings", "user_id"),
            )
            for table_name, user_column in required_singleton_tables:
                exists = self.session.execute(
                    text(f"SELECT 1 FROM {table_name} WHERE {user_column} = :owner_id LIMIT 1"),
                    {"owner_id": owner_id},
                ).scalar_one_or_none()
                if exists is None:
                    return None

            provider_count = self._count_existing_keys("providers", CORE_PROVIDER_KEYS)
            if provider_count < len(CORE_PROVIDER_KEYS):
                return None

            model_count = self._count_existing_keys("model_configs", CORE_MODEL_KEYS)
            if model_count < len(CORE_MODEL_KEYS):
                return None

            plugin_count = self._count_existing_keys("plugins", CORE_PLUGIN_KEYS)
            if plugin_count < len(CORE_PLUGIN_KEYS):
                return None

        return BootstrapResult(
            owner_id=int(owner_id),
            providers_seeded=0,
            models_seeded=0,
            plugins_seeded=0,
        )

    def _count_existing_keys(self, table_name: str, keys: tuple[str, ...]) -> int:
        placeholders = ", ".join(f":key_{index}" for index in range(len(keys)))
        params = {f"key_{index}": key for index, key in enumerate(keys)}
        statement = text(f"SELECT COUNT(DISTINCT key) FROM {table_name} WHERE key IN ({placeholders})")
        return int(self.session.execute(statement, params).scalar_one())

    def _sync_model_config(self, model_config, **values) -> None:
        for key, value in values.items():
            if key == "key":
                continue
            if getattr(model_config, key) != value:
                setattr(model_config, key, value)
        self.session.add(model_config)

    @contextmanager
    def _trace(self, checkpoint: str) -> Iterator[None]:
        self._emit_progress(checkpoint, "start")
        started = perf_counter()
        try:
            yield
        except Exception:
            elapsed_ms = max(0, int((perf_counter() - started) * 1000))
            logger.exception(
                "Bootstrap checkpoint failed | checkpoint=%s | elapsed_ms=%s",
                checkpoint,
                elapsed_ms,
            )
            self._emit_progress(checkpoint, "failed")
            raise
        else:
            elapsed_ms = max(0, int((perf_counter() - started) * 1000))
            logger.info(
                "Bootstrap checkpoint complete | checkpoint=%s | elapsed_ms=%s",
                checkpoint,
                elapsed_ms,
            )
            self._emit_progress(checkpoint, "ok")

    def _emit_progress(self, checkpoint: str, outcome: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(checkpoint, outcome)
