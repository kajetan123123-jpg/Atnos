from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.db.models.enums import ModelTask, ProviderPosture
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.model_selection_service import ModelSelectionService
from atnos.services.provider_health_cache_service import ProviderHealthCacheService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class ProviderTaskRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        ProviderHealthCacheService._cache.clear()
        self.temp_dir = make_temp_dir()
        self.root = Path(self.temp_dir.__enter__())
        self.apps = []

    def tearDown(self) -> None:
        for app in self.apps:
            app.state.database.dispose()
        self.temp_dir.__exit__(None, None, None)

    def test_mvp_task_routing_prefers_expected_provider_per_task(self) -> None:
        app = create_test_app(self.root)
        self.apps.append(app)

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "OPENAI_API_KEY": "sk-test-openai",
                "GEMINI_API_KEY": "test-gemini",
            },
            clear=False,
        ):
            bootstrap_test_app(app)
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                self.assertIsNotNone(owner)
                policy = ExecutionPolicyRepository(session).get_for_user(owner.id)  # type: ignore[union-attr]
                self.assertIsNotNone(policy)

                selector = ModelSelectionService(session, provider_registry=app.state.providers)

                general = selector.select_for_task(
                    task=ModelTask.GENERAL_CHAT,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                )
                self.assertEqual(general.provider.key, "ollama-local")

                synthesis = selector.select_for_task(
                    task=ModelTask.SYNTHESIS,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                )
                self.assertEqual(synthesis.provider.key, "ollama-local")

                coding = selector.select_for_task(
                    task=ModelTask.CODING,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                )
                self.assertEqual(coding.provider.key, "ollama-local")

                research = selector.select_for_task(
                    task=ModelTask.SEARCH_RESEARCH,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                )
                self.assertEqual(research.provider.key, "ollama-local")

                forced_synthesis = selector.select_for_task(
                    task=ModelTask.SYNTHESIS,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                    force_external=True,
                )
                self.assertEqual(forced_synthesis.provider.key, "openai-cloud")

    def test_strict_local_blocks_cloud_for_cloud_preferring_tasks(self) -> None:
        app = create_test_app(self.root)
        self.apps.append(app)

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "OPENAI_API_KEY": "sk-test-openai",
                "GEMINI_API_KEY": "test-gemini",
            },
            clear=False,
        ):
            bootstrap_test_app(app)
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                self.assertIsNotNone(owner)
                policy = ExecutionPolicyRepository(session).get_for_user(owner.id)  # type: ignore[union-attr]
                self.assertIsNotNone(policy)

                selector = ModelSelectionService(session, provider_registry=app.state.providers)
                for task in (ModelTask.CODING, ModelTask.SEARCH_RESEARCH, ModelTask.SYNTHESIS):
                    selected = selector.select_for_task(
                        task=task,
                        execution_policy=policy,
                        provider_posture=ProviderPosture.STRICT_LOCAL,
                    )
                    self.assertEqual(selected.provider.key, "ollama-local")

    def test_lazy_execution_fallback_chain_does_not_probe_live_health(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "OPENAI_API_KEY": "sk-test-openai",
                "GEMINI_API_KEY": "test-gemini",
            },
            clear=False,
        ):
            app = create_test_app(self.root)
            self.apps.append(app)
            bootstrap_test_app(app)
            cloud_adapters = [
                app.state.providers.get("openai-cloud"),
                app.state.providers.get("anthropic-cloud"),
                app.state.providers.get("gemini-cloud"),
            ]
            with (
                patch.object(cloud_adapters[0], "check_health", side_effect=AssertionError("live health probe")),
                patch.object(cloud_adapters[1], "check_health", side_effect=AssertionError("live health probe")),
                patch.object(cloud_adapters[2], "check_health", side_effect=AssertionError("live health probe")),
            ):
                with app.state.database.session_scope() as session:
                    selector = ModelSelectionService(session, provider_registry=app.state.providers)
                    chain = selector.build_execution_fallback_chain(
                        task=ModelTask.GENERAL_CHAT,
                        selected_provider_key="ollama-local",
                        internet_enabled=True,
                        probe_health=False,
                    )

        self.assertTrue(chain)
        self.assertTrue(all(item.provider.key != "ollama-local" for item in chain))

    def test_strict_local_fallback_chain_never_contains_cloud_provider(self) -> None:
        app = create_test_app(self.root)
        self.apps.append(app)
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            selector = ModelSelectionService(session, provider_registry=app.state.providers)
            chain = selector.build_execution_fallback_chain(
                task=ModelTask.GENERAL_CHAT,
                selected_provider_key="ollama-local",
                internet_enabled=True,
                provider_posture=ProviderPosture.STRICT_LOCAL,
                probe_health=False,
            )

        self.assertEqual(chain, ())

    def test_default_local_selection_does_not_probe_provider_health(self) -> None:
        app = create_test_app(self.root)
        self.apps.append(app)
        bootstrap_test_app(app)
        adapter = app.state.providers.get("ollama-local")
        with patch.object(adapter, "check_health", side_effect=AssertionError("live health probe")):
            with app.state.database.session_scope() as session:
                owner = UserRepository(session).get_owner()
                self.assertIsNotNone(owner)
                policy = ExecutionPolicyRepository(session).get_for_user(owner.id)  # type: ignore[union-attr]
                self.assertIsNotNone(policy)
                selector = ModelSelectionService(session, provider_registry=app.state.providers)
                selected = selector.select_for_task(
                    task=ModelTask.GENERAL_CHAT,
                    execution_policy=policy,
                    provider_posture=ProviderPosture.IMPROVE_WHEN_NEEDED,
                )

        self.assertEqual(selected.provider.key, "ollama-local")
