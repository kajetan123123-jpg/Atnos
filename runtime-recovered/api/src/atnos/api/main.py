from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from logging import getLogger
from time import monotonic
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from atnos.core.config import Settings, get_settings
from atnos.core.logging import configure_logging
from atnos.core.startup_trace import StartupTraceRecorder
from atnos.services.learning_mode_guard import classify_blocked_http_action, youtube_write_block_response

if TYPE_CHECKING:
    from atnos.plugins.registry import PluginRegistry
    from atnos.providers.registry import ProviderRegistry

logger = getLogger(__name__)


class _LazyJarvisRuntimeController:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_registry,
        plugin_registry,
    ) -> None:
        self._settings = settings
        self._provider_registry = provider_registry
        self._plugin_registry = plugin_registry
        self._controller = None

    def _get_controller(self):
        if self._controller is None:
            from atnos.jarvis.controller import JarvisRuntimeController

            self._controller = JarvisRuntimeController(
                self._settings,
                provider_registry=self._provider_registry,
                plugin_registry=self._plugin_registry,
            )
        return self._controller

    async def start(self) -> dict[str, object]:
        return await self._get_controller().start()

    async def stop(self) -> dict[str, object]:
        if self._controller is None:
            return self.status()
        return await self._controller.stop()

    def status(self) -> dict[str, object]:
        if self._controller is None:
            return {
                "running": False,
                "config": {"lazy": True, "status": "not_initialized"},
                "modules": {"runtime": {"running": False, "status": "not_initialized"}},
            }
        return self._controller.status()

    async def inject_goal(self, goal: str) -> None:
        await self._get_controller().inject_goal(goal)

    async def list_browser_sessions(self) -> list[dict[str, object]]:
        return await self._get_controller().list_browser_sessions()

    async def start_manual_login(self, service: str) -> tuple[bool, dict[str, object]]:
        return await self._get_controller().start_manual_login(service)

    async def start_manual_login_for_url(
        self,
        *,
        service: str,
        display_name: str,
        login_url: str,
        validation_url: str | None = None,
    ) -> tuple[bool, dict[str, object]]:
        return await self._get_controller().start_manual_login_for_url(
            service=service,
            display_name=display_name,
            login_url=login_url,
            validation_url=validation_url,
        )

    async def confirm_manual_login(self, service: str) -> tuple[bool, dict[str, object]]:
        return await self._get_controller().confirm_manual_login(service)


def _optional_capability_pending(reason: str) -> dict[str, dict[str, object]]:
    return {
        "image_generation": {
            "enabled": False,
            "reason": reason,
            "status": "checking",
        },
        "video_generation": {
            "enabled": False,
            "reason": reason,
            "status": "checking",
        },
    }


def _check_production_short_video_capability(settings: Settings) -> dict[str, object]:
    """Check the actual Shorts production video path, not the legacy Diffusers path."""
    if str(getattr(settings, "shorts_video_backend", "motion_graphic") or "motion_graphic") == "motion_graphic":
        return {
            "enabled": True,
            "status": "degraded",
            "backend": "motion_graphic",
            "ready_backends": [],
            "detail": "Motion graphics can render locally, but they are a fallback and do not satisfy the public visual-quality gate.",
            "public_quality_ready": False,
            "backends": {
                "motion_graphic": {
                    "ready": True,
                    "public_quality_ready": False,
                    "missing_models": [],
                    "workflow_template": None,
                    "workflow_valid": True,
                }
            },
        }
    try:
        from atnos.model_manager.registry import build_model_registry
        from atnos.production.steps import video_generator
        from atnos.production.readiness import _validate_workflow_template
        from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

        registry = build_model_registry(settings)
        pipelines = {
            "ltx": ("ltx_video", "ltx_vae", "t5_encoder"),
            "wan": ("wan_video_1_3b",),
        }
        backend_status: dict[str, object] = {}
        ready_backends: list[str] = []
        for backend, model_ids in pipelines.items():
            missing_models: list[str] = []
            model_paths: dict[str, str] = {}
            for model_id in model_ids:
                spec = registry.get(model_id)
                if spec is None:
                    missing_models.append(model_id)
                    continue
                model_paths[model_id] = str(spec.local_path)
                if not spec.local_path.exists() or spec.local_path.stat().st_size <= 1024 * 1024:
                    missing_models.append(model_id)
            workflow_path = video_generator._workflow_path(settings, backend)
            workflow = video_generator._load_or_create_workflow(workflow_path, backend)
            class_types = sorted(
                {
                    str(node.get("class_type"))
                    for node in workflow.values()
                    if isinstance(node, dict) and node.get("class_type")
                }
            )
            workflow_key = "wan_video" if backend == "wan" else "ltx_video"
            workflow_validation = _validate_workflow_template(workflow_key, workflow, class_types)
            model_input_folders = {
                "ckpt_name": "checkpoints",
                # Current ComfyUI exposes CLIPLoader catalogs through the
                # text_encoders endpoint even when the legacy folder alias is clip.
                "clip_name": "text_encoders",
                "vae_name": "vae",
                "unet_name": "diffusion_models",
            }
            required_models: dict[str, set[str]] = {}
            for node in workflow.values():
                inputs = node.get("inputs") if isinstance(node, dict) else None
                if not isinstance(inputs, dict):
                    continue
                for input_name, folder in model_input_folders.items():
                    model_name = inputs.get(input_name)
                    if isinstance(model_name, str) and model_name and not model_name.startswith("{"):
                        required_models.setdefault(folder, set()).add(model_name)
            live_api = ComfyUiRuntimeService(settings).probe_live_api(
                required_class_types=set(class_types),
                required_models=required_models,
            )
            backend_ready = (
                not missing_models
                and bool(workflow_validation["valid"])
                and bool(live_api.get("ready"))
            )
            if backend_ready:
                ready_backends.append(backend)
            backend_status[backend] = {
                "ready": backend_ready,
                "technical_ready": backend_ready,
                "public_quality_ready": False,
                "missing_models": missing_models,
                "model_paths": model_paths,
                "workflow_template": str(workflow_path),
                "workflow_valid": bool(workflow_validation["valid"]),
                "missing_class_types": workflow_validation["missing_class_types"],
                "missing_placeholders": workflow_validation["missing_placeholders"],
                "comfyui_live": bool(live_api.get("api_online")),
                "runtime_ready": bool(live_api.get("ready")),
                "comfyui_base_url": live_api.get("base_url"),
                "missing_runtime_class_types": live_api.get("missing_class_types", []),
                "missing_runtime_models": live_api.get("missing_models", {}),
                "runtime_reason": live_api.get("reason"),
            }
        return {
            "enabled": bool(ready_backends),
            "status": "ready" if ready_backends else "unavailable",
            "backend": ready_backends[0] if ready_backends else None,
            "ready_backends": ready_backends,
            "technical_ready": bool(ready_backends),
            # Runtime availability is not evidence that a rendered Short meets
            # the public quality contract. That decision belongs to per-job QA.
            "public_quality_ready": False,
            "quality_evidence": {
                "status": "missing",
                "reason": "No verified render and QA report are attached to this runtime capability check.",
            },
            "detail": "Production Shorts ComfyUI workflow and model files are technically ready; public quality remains unverified until a real render passes QA."
            if ready_backends
            else "Production Shorts need a live ComfyUI API, installed workflow nodes, and complete LTX or Wan model files.",
            "backends": backend_status,
        }
    except Exception as exc:  # pragma: no cover - defensive capability surface
        return {
            "enabled": False,
            "status": "unavailable",
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _check_optional_runtime_capabilities(settings: Settings) -> dict[str, dict[str, object]]:
    flags: dict[str, dict[str, object]] = {}

    try:
        from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
        from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

        runtime = ComfyUiRuntimeService(settings)
        install = runtime.describe_install()
        live_api = runtime.probe_live_api()
        checkpoint_name = ComfyUiImageGenerationService(settings).resolve_checkpoint_name()
        image_generation_enabled = bool(live_api.get("ready") and checkpoint_name)
        flags["image_generation"] = {
            "enabled": image_generation_enabled,
            "reason": (
                None
                if image_generation_enabled
                else live_api.get("reason") or "No ComfyUI checkpoint was found"
            ),
            "detail": (
                "Live ComfyUI API and a local checkpoint were verified."
                if image_generation_enabled
                else "Read-only health probe; ComfyUI remains on-demand and was not started."
            ),
            "started": False,
            "lowvram": bool(settings.comfyui_lowvram),
            "checkpoint": checkpoint_name,
            "api_online": bool(live_api.get("api_online")),
            "base_url": live_api.get("base_url"),
            "install_detected": bool(install.exists),
            "install_dir": str(install.install_dir) if install.install_dir else None,
            "status": "ready" if image_generation_enabled else "unavailable",
        }
    except Exception as exc:  # pragma: no cover - hardware/runtime edge
        flags["image_generation"] = {
            "enabled": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "status": "unavailable",
        }

    try:
        from atnos.services.local_video_generation_service import LocalVideoGenerationService

        video_readiness = LocalVideoGenerationService(settings).ensure_startup_ready()
        flags["video_generation"] = {
            "enabled": video_readiness.video_generation,
            "reason": video_readiness.reason,
            "detail": video_readiness.detail,
            "backend": video_readiness.backend,
            "model_dir": str(video_readiness.model_dir) if video_readiness.model_dir else None,
            "dependencies_installed": video_readiness.dependencies_installed,
            "model_downloaded": video_readiness.model_downloaded,
            "status": "ready" if video_readiness.video_generation else "unavailable",
        }
    except Exception as exc:  # pragma: no cover - hardware/runtime edge
        flags["video_generation"] = {
            "enabled": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "status": "unavailable",
        }
    production_video = _check_production_short_video_capability(settings)
    flags["video_generation"]["production_short_video"] = production_video
    if production_video.get("status") == "ready" and flags["video_generation"].get("status") != "ready":
        flags["video_generation"] = {
            **flags["video_generation"],
            "enabled": True,
            "reason": "Production Shorts ComfyUI pipeline is ready.",
            "detail": production_video.get("detail"),
            "backend": f"production-{production_video.get('backend')}",
            "model_dir": None,
            "dependencies_installed": True,
            "model_downloaded": True,
            "status": "ready",
        }

    return flags


async def _refresh_optional_runtime_capabilities(app: FastAPI, settings: Settings) -> None:
    try:
        flags = await asyncio.to_thread(_check_optional_runtime_capabilities, settings)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive background task guard
        logger.warning("Optional runtime capability refresh failed: %s", exc)
        return
    app.state.runtime_capability_flags.update(flags)
    app.state.runtime_capability_last_refreshed_at = monotonic()
    app.state.runtime_capability_last_refreshed_wall = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Optional runtime capabilities refreshed | image=%s | video=%s",
        flags.get("image_generation", {}).get("status"),
        flags.get("video_generation", {}).get("status"),
    )


async def _delayed_refresh_optional_runtime_capabilities(
    app: FastAPI,
    settings: Settings,
    *,
    delay_s: float = 5.0,
) -> None:
    await asyncio.sleep(delay_s)
    await _refresh_optional_runtime_capabilities(app, settings)


def create_app(
    settings: Settings | None = None,
    *,
    provider_registry: ProviderRegistry | None = None,
    plugin_registry: PluginRegistry | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    resolved_settings.ensure_directories()

    startup_trace = StartupTraceRecorder(resolved_settings, logger)
    startup_trace.initialize(phase="factory_init", checkpoint="factory_init")

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/database_session"):
        from atnos.db.session import DatabaseManager

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/database_migrations"):
        from atnos.db.migrations import upgrade_database
        from atnos.intelligence.storage import init_intelligence_db

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/providers"):
        from atnos.providers.registry import (
            build_default_provider_registry,
            build_stub_provider_registry,
        )

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/plugins"):
        from atnos.plugins.registry import build_default_plugin_registry

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/services"):
        from atnos.services.ambient_context_service import AmbientContextService
        from atnos.services.integration_config_service import IntegrationConfigService
        from atnos.services.knowledge_library_service import KnowledgeLibraryService
        from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService
        from atnos.services.semantic_knowledge_source_service import SemanticKnowledgeSourceService
        from atnos.services.knowledge_workspace_service import KnowledgeWorkspaceService
        from atnos.services.provider_health_cache_service import ProviderHealthMonitorService
        from atnos.services.remote_node_hub import RemoteNodeHub
        from atnos.services.runtime_settings_service import RuntimeSettingsService
        from atnos.services.summary_writeback_service import SummaryWritebackService
        from atnos.services.voice_ambient_listener_service import VoiceAmbientListenerService

    with startup_trace.phase_scope(phase="factory_imports", checkpoint="factory_imports/routes"):
        from atnos.api.routes.chat import router as chat_router
        from atnos.api.routes.channel import router as channel_router
        from atnos.api.routes.channel_ops import router as channel_ops_router
        from atnos.api.routes.agent import router as agent_router
        from atnos.api.routes.ai_workers import router as ai_workers_router
        from atnos.api.routes.accounts import router as accounts_router
        from atnos.api.routes.action_broker import router as action_broker_router
        from atnos.api.routes.connectors import router as connectors_router
        from atnos.api.routes.conversations import router as conversations_router
        from atnos.api.routes.creator_cockpit import router as creator_cockpit_router
        from atnos.api.routes.creator_ops import router as creator_ops_router
        from atnos.api.routes.diagnostics import router as diagnostics_router
        from atnos.api.routes.distribution import router as distribution_router
        from atnos.api.routes.feedback import router as feedback_router
        from atnos.api.routes.health import router as health_router
        from atnos.api.routes.integrations import router as integrations_router
        from atnos.api.routes.investment import router as investment_router
        from atnos.api.routes.intelligence import router as intelligence_router
        from atnos.api.routes.jarvis import router as jarvis_router
        from atnos.api.routes.knowledge import router as knowledge_router
        from atnos.api.routes.local_host import router as local_host_router
        from atnos.api.routes.memory import router as memory_router
        from atnos.api.routes.models import router as models_router
        from atnos.api.routes.n8n import router as n8n_router
        from atnos.api.routes.oauth import router as oauth_router
        from atnos.api.routes.pipeline import router as pipeline_router
        from atnos.api.routes.operational_memory import router as operational_memory_router
        from atnos.api.routes.project_command import router as project_command_router
        from atnos.api.routes.youtube_channel_identities import router as youtube_channel_identities_router
        from atnos.api.routes.preflight import router as preflight_router
        from atnos.api.routes.procurement import router as procurement_router
        from atnos.api.routes.production import router as production_router
        from atnos.api.routes.comfy_channels import router as comfy_channels_router
        from atnos.api.routes.asset_brain_approval import router as asset_brain_approval_router
        from atnos.api.routes.wb_delivery_audit import router as wb_delivery_audit_router
        from atnos.api.routes.publication_coordinator import router as publication_coordinator_router
        from atnos.api.routes.projects import router as projects_router
        from atnos.api.routes.quality_council import router as quality_council_router
        from atnos.api.routes.research import router as research_router
        from atnos.api.routes.remote_nodes import router as remote_nodes_router
        from atnos.api.routes.runtime import router as runtime_router
        from atnos.api.routes.self_improvement import router as self_improvement_router
        from atnos.api.routes.settings import router as settings_router
        from atnos.api.routes.sources import router as sources_router
        from atnos.api.routes.topics import router as topics_router
        from atnos.api.routes.uploads import router as uploads_router
        from atnos.api.routes.ui import router as ui_router
        from atnos.api.routes.voice import router as voice_router
        from atnos.api.routes.trusted_auto import router as trusted_auto_router

    startup_trace.mark_phase(phase="factory_init", checkpoint="factory_init", outcome="ok")

    database = DatabaseManager(resolved_settings)
    providers = provider_registry or (
        build_stub_provider_registry()
        if resolved_settings.app_env == "test"
        else build_default_provider_registry(
            app_root=resolved_settings.app_root,
            settings=resolved_settings,
        )
    )
    plugins = plugin_registry or build_default_plugin_registry()
    knowledge_library = KnowledgeLibraryService(resolved_settings)

    def resolve_obsidian_vault_path():
        with database.session_scope() as session:
            config = IntegrationConfigService(session, resolved_settings).get_obsidian_config()
            return config.vault_path if config.path_exists else None

    semantic_knowledge_index = SemanticKnowledgeIndexService(
        resolved_settings,
        library=knowledge_library,
        source_catalog=SemanticKnowledgeSourceService(
            resolved_settings,
            library=knowledge_library,
            obsidian_path_resolver=resolve_obsidian_vault_path,
            include_extended_sources=True,
        ),
    )
    knowledge_workspace = KnowledgeWorkspaceService(resolved_settings)
    jarvis_controller = _LazyJarvisRuntimeController(
        resolved_settings,
        provider_registry=providers,
        plugin_registry=plugins,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        startup_trace.attach_app(app)
        startup_trace.mark_phase(app, phase="bootstrapping", checkpoint="bootstrapping", outcome="start")
        scheduler_runtime = None
        ambient_context_service = None
        voice_ambient_listener_service = None
        provider_health_monitor = None
        optional_capability_task = None
        production_autonomy_task = None
        current_phase = "bootstrapping"
        logger.info(
            "Starting Atnos API | env=%s | deployment_mode=%s | database_backend=%s",
            resolved_settings.app_env,
            resolved_settings.deployment_mode,
            resolved_settings.database_backend,
        )
        try:
            current_phase = "workspace"
            with startup_trace.phase_scope(app, phase="workspace"):
                knowledge_workspace.ensure_workspace()

            current_phase = "knowledge_library"
            with startup_trace.phase_scope(app, phase="knowledge_library"):
                knowledge_library.ensure_library()

            current_phase = "database_initialize"
            with startup_trace.phase_scope(app, phase="database_initialize"):
                database.initialize()

            current_phase = "database_migrate"
            with startup_trace.phase_scope(app, phase="database_migrate"):
                upgrade_database(resolved_settings)
                await init_intelligence_db(resolved_settings)

            current_phase = "production_order_recovery"
            with startup_trace.phase_scope(app, phase="production_order_recovery"):
                from atnos.production.order_recovery import recover_interrupted_orders

                recovery = (await asyncio.to_thread(recover_interrupted_orders, resolved_settings)
                            if resolved_settings.background_automation_enabled else {"recovered_count": 0})
                if recovery["recovered_count"]:
                    logger.warning("Interrupted production orders recovered: %s", recovery["recovered_count"])

            with database.session_scope() as session:
                from atnos.services.bootstrap_service import BootstrapService
                from atnos.services.provider_runtime_secret_sync_service import (
                    ProviderRuntimeSecretSyncService,
                )
                from atnos.services.runtime_contract_service import RuntimeContractService
                from atnos.services.google_oauth_client_secret_importer import (
                    GoogleOAuthClientSecretImporter,
                )

                current_phase = "bootstrap_defaults"
                bootstrap = BootstrapService(
                    session,
                    resolved_settings,
                    progress_callback=lambda checkpoint, outcome: startup_trace.mark_phase(
                        app,
                        phase="bootstrap_defaults",
                        checkpoint=checkpoint,
                        outcome=outcome,
                    ),
                )
                with startup_trace.phase_scope(app, phase="bootstrap_defaults"):
                    bootstrap_result = bootstrap.ensure_local_first_defaults()

                current_phase = "runtime_contract"
                with startup_trace.phase_scope(app, phase="runtime_contract", checkpoint="runtime_contract/provider_secret_sync"):
                    ProviderRuntimeSecretSyncService(session, resolved_settings).sync_known_cloud_providers()

                current_phase = "runtime_contract"
                contract_service = RuntimeContractService(session, provider_registry=providers)
                with startup_trace.phase_scope(app, phase="runtime_contract"):
                    contract_service.validate_runtime_provider_contract()

                with startup_trace.phase_scope(app, phase="runtime_contract", checkpoint="runtime_contract/google_oauth_auto_import"):
                    try:
                        import_result = GoogleOAuthClientSecretImporter(
                            session,
                            resolved_settings,
                        ).auto_import_if_available()
                        app.state.google_oauth_auto_import = {
                            "imported": bool(import_result),
                            "client_secret_path": str(import_result.client_secret_path) if import_result else str(resolved_settings.youtube_client_secret_file),
                            "warnings": list(import_result.warnings) if import_result else [],
                        }
                        if import_result:
                            logger.info(
                                "Google OAuth client_secret.json auto-imported | client_secret_path=%s | warnings=%s",
                                import_result.client_secret_path,
                                len(import_result.warnings),
                            )
                    except Exception as exc:
                        app.state.google_oauth_auto_import = {
                            "imported": False,
                            "client_secret_path": str(resolved_settings.youtube_client_secret_file),
                            "warnings": [f"{type(exc).__name__}: {exc}"],
                        }
                        logger.warning("Google OAuth client_secret.json auto-import failed: %s", exc)
            logger.info(
                "Bootstrap complete | owner_id=%s | providers_seeded=%s | models_seeded=%s | plugins_seeded=%s",
                bootstrap_result.owner_id,
                bootstrap_result.providers_seeded,
                bootstrap_result.models_seeded,
                bootstrap_result.plugins_seeded,
            )
            from atnos.services.scheduler_runtime_service import SchedulerRuntimeService

            scheduler_runtime = SchedulerRuntimeService(
                database,
                resolved_settings,
                provider_registry=providers,
                plugin_registry=plugins,
                summary_writeback_factory=SummaryWritebackService,
            )
            ambient_context_service = AmbientContextService(
                database,
                settings=resolved_settings,
                provider_registry=providers,
            )
            voice_ambient_listener_service = VoiceAmbientListenerService(
                database,
                settings=resolved_settings,
                provider_registry=providers,
                plugin_registry=plugins,
                summary_writeback_factory=SummaryWritebackService,
            )
            provider_health_monitor = ProviderHealthMonitorService(
                database,
                settings=resolved_settings,
                provider_registry=providers,
            )
            app.state.scheduler_runtime = scheduler_runtime
            app.state.ambient_context_service = ambient_context_service
            app.state.voice_ambient_listener_service = voice_ambient_listener_service
            app.state.provider_health_monitor = provider_health_monitor

            current_phase = "runtime_controls"
            with database.session_scope() as session:
                with startup_trace.phase_scope(app, phase="runtime_controls"):
                    runtime_controls = RuntimeSettingsService(session, resolved_settings).snapshot()

            current_phase = "optional_runtime_capabilities"
            with startup_trace.phase_scope(app, phase="optional_runtime_capabilities"):
                if resolved_settings.app_env == "test" or not resolved_settings.background_automation_enabled:
                    app.state.runtime_capability_flags["image_generation"] = {
                        "enabled": False,
                        "reason": "automatic capability checks disabled in this runtime",
                        "status": "disabled",
                    }
                    app.state.runtime_capability_flags["video_generation"] = {
                        "enabled": False,
                        "reason": "automatic capability checks disabled in this runtime",
                        "status": "disabled",
                    }
                else:
                    app.state.runtime_capability_flags.update(
                        _optional_capability_pending("checking in background after API startup")
                    )
                    optional_capability_task = asyncio.create_task(
                        _delayed_refresh_optional_runtime_capabilities(app, resolved_settings)
                    )
                    app.state.optional_capability_task = optional_capability_task

            current_phase = "ambient_context"
            with startup_trace.phase_scope(app, phase="ambient_context"):
                if resolved_settings.background_automation_enabled and runtime_controls.ambient_context_enabled:
                    ambient_context_service.start()

            current_phase = "voice_listener"
            with startup_trace.phase_scope(app, phase="voice_listener"):
                voice_ambient_listener_service.set_muted(runtime_controls.ambient_muted)
                if resolved_settings.background_automation_enabled and runtime_controls.ambient_listening_enabled:
                    voice_ambient_listener_service.start_if_enabled()

            current_phase = "scheduler"
            with startup_trace.phase_scope(app, phase="scheduler"):
                if resolved_settings.background_automation_enabled:
                    await scheduler_runtime.start()

            current_phase = "provider_health_monitor"
            with startup_trace.phase_scope(app, phase="provider_health_monitor"):
                if resolved_settings.app_env != "test" and resolved_settings.background_automation_enabled:
                    provider_health_monitor.start()

            startup_trace.ready(app)
            if resolved_settings.app_env != 'test' and resolved_settings.background_automation_enabled:
                from atnos.production.order_autonomy import run_monitor
                production_autonomy_task = asyncio.create_task(run_monitor(resolved_settings))
            yield
        except Exception as exc:
            startup_trace.fail(app, phase=current_phase, error=str(exc))
            raise
        finally:
            if production_autonomy_task is not None:
                production_autonomy_task.cancel()
                with suppress(asyncio.CancelledError):
                    await production_autonomy_task
            startup_failed = bool(getattr(app.state, "startup_error", None))
            if not startup_failed:
                startup_trace.stopping(app)
            if voice_ambient_listener_service is not None:
                voice_ambient_listener_service.stop()
            if ambient_context_service is not None:
                ambient_context_service.stop()
            if scheduler_runtime is not None:
                await scheduler_runtime.stop()
            if provider_health_monitor is not None:
                provider_health_monitor.stop()
            if optional_capability_task is not None and not optional_capability_task.done():
                optional_capability_task.cancel()
                with suppress(asyncio.CancelledError):
                    await optional_capability_task
            jarvis_controller = getattr(app.state, "jarvis_controller", None)
            if jarvis_controller is not None:
                await jarvis_controller.stop()
            database.dispose()
            if not startup_failed:
                startup_trace.stopped(app)

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
    )
    startup_trace.attach_app(app)
    app.state.settings = resolved_settings
    app.state.database = database
    app.state.providers = providers
    app.state.plugins = plugins
    app.state.knowledge_library = knowledge_library
    app.state.semantic_knowledge_index = semantic_knowledge_index
    app.state.knowledge_workspace = knowledge_workspace
    app.state.summary_writeback_factory = SummaryWritebackService
    app.state.remote_node_hub = RemoteNodeHub()
    app.state.jarvis_controller = jarvis_controller
    app.state.runtime_capability_flags = {
        "image_generation": {
            "enabled": False,
            "reason": "not checked yet",
        },
        "video_generation": {
            "enabled": False,
            "reason": "not checked yet",
        },
    }
    app.state.runtime_capability_last_refreshed_at = 0.0
    app.state.runtime_capability_last_refreshed_wall = None
    app.state.runtime_capability_refresh_lock = asyncio.Lock()

    async def refresh_optional_runtime_capabilities_if_stale(*, max_age_s: float = 10.0) -> None:
        if resolved_settings.app_env == "test" or not resolved_settings.background_automation_enabled:
            return
        async with app.state.runtime_capability_refresh_lock:
            age_s = monotonic() - float(app.state.runtime_capability_last_refreshed_at or 0.0)
            if age_s < max(0.0, max_age_s):
                return
            await _refresh_optional_runtime_capabilities(app, resolved_settings)

    app.state.refresh_optional_runtime_capabilities = refresh_optional_runtime_capabilities_if_stale

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def enforce_learning_mode_youtube_write_block(request, call_next):
        blocked_action = classify_blocked_http_action(
            request.method,
            request.url.path,
            resolved_settings,
        )
        if blocked_action is not None:
            payload = youtube_write_block_response(resolved_settings)
            assert payload is not None
            return JSONResponse(status_code=423, content=payload)
        return await call_next(request)

    app.include_router(chat_router)
    app.include_router(channel_router)
    app.include_router(channel_ops_router)
    app.include_router(agent_router)
    app.include_router(ai_workers_router)
    app.include_router(accounts_router)
    app.include_router(action_broker_router)
    app.include_router(connectors_router)
    app.include_router(conversations_router)
    app.include_router(creator_cockpit_router)
    app.include_router(creator_ops_router)
    app.include_router(diagnostics_router)
    app.include_router(distribution_router)
    app.include_router(feedback_router)
    app.include_router(health_router)
    app.include_router(integrations_router)
    app.include_router(investment_router)
    app.include_router(intelligence_router)
    app.include_router(jarvis_router)
    app.include_router(knowledge_router)
    app.include_router(local_host_router)
    app.include_router(memory_router)
    app.include_router(models_router)
    app.include_router(n8n_router)
    app.include_router(oauth_router)
    app.include_router(pipeline_router)
    app.include_router(operational_memory_router)
    app.include_router(project_command_router)
    app.include_router(youtube_channel_identities_router)
    app.include_router(preflight_router)
    app.include_router(procurement_router)
    app.include_router(production_router)
    app.include_router(comfy_channels_router)
    app.include_router(asset_brain_approval_router)
    app.include_router(wb_delivery_audit_router)
    app.include_router(publication_coordinator_router)
    app.include_router(projects_router)
    app.include_router(quality_council_router)
    app.include_router(research_router)
    app.include_router(remote_nodes_router)
    app.include_router(runtime_router)
    app.include_router(self_improvement_router)
    app.include_router(settings_router)
    app.include_router(sources_router)
    app.include_router(topics_router)
    app.include_router(uploads_router)
    app.include_router(ui_router)
    app.include_router(voice_router)
    app.include_router(trusted_auto_router)
    return app
