"""Offline lifecycle tests; never start models, real schedulers or monitors."""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from atnos.api.main import create_app
from atnos.providers.registry import build_stub_provider_registry
from test_support import build_test_settings


@pytest.mark.parametrize('enabled', [False, True])
def test_background_startup_is_explicit_and_default_lifecycle_remains_compatible(tmp_path, enabled):
    settings = build_test_settings(tmp_path)
    assert settings.background_automation_enabled is True
    settings.app_env = 'development'
    settings.background_automation_enabled = enabled
    with ExitStack() as stack:
        recovery = stack.enter_context(patch('atnos.production.order_recovery.recover_interrupted_orders', return_value={'recovered_count': 0}))
        scheduler = stack.enter_context(patch('atnos.services.scheduler_runtime_service.SchedulerRuntimeService.start', new_callable=AsyncMock))
        stack.enter_context(patch('atnos.services.scheduler_runtime_service.SchedulerRuntimeService.stop', new_callable=AsyncMock))
        monitor = stack.enter_context(patch('atnos.production.order_autonomy.run_monitor', new_callable=AsyncMock))
        health = stack.enter_context(patch('atnos.services.provider_health_cache_service.ProviderHealthMonitorService.start'))
        stack.enter_context(patch('atnos.services.provider_health_cache_service.ProviderHealthMonitorService.stop'))
        ambient = stack.enter_context(patch('atnos.services.ambient_context_service.AmbientContextService.start'))
        voice = stack.enter_context(patch('atnos.services.voice_ambient_listener_service.VoiceAmbientListenerService.start_if_enabled'))
        capabilities = stack.enter_context(patch('atnos.api.main._delayed_refresh_optional_runtime_capabilities', new_callable=AsyncMock))
        app = create_app(settings=settings, provider_registry=build_stub_provider_registry())
        with TestClient(app) as client:
            response = client.get('/health/ready')
            assert response.status_code == 200, response.text
            assert response.json()['background_automation_enabled'] is enabled
            assert response.json()['ready'] is True
        assert recovery.call_count == int(enabled)
        assert scheduler.await_count == int(enabled)
        assert monitor.call_count == int(enabled)
        assert health.call_count == int(enabled)
        assert capabilities.call_count == int(enabled)
        if not enabled:
            ambient.assert_not_called()
            voice.assert_not_called()
