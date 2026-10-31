"""Preserve actual tool outcomes on provider failure, never cancellation or a fake success."""
import asyncio

import pytest

from atnos.agentic import AgentContext, OrchestratorAgent
from atnos.llm.base import ChatResponse, ToolCall, LLMRunCancelled
from atnos.providers.base import ProviderUnavailableError, ProviderTimeoutError
from atnos.tools import ToolRegistry, discover_builtin_tools


@pytest.mark.parametrize('error', [ProviderUnavailableError('controlled500'), ProviderTimeoutError('controlled timeout')])
def test_provider_failure_before_tools_still_raises(error):
    class Backend:
        async def chat_complete(self, **kwargs):
            raise error
    with pytest.raises(type(error)):
        asyncio.run(OrchestratorAgent().run('test', AgentContext(system_prompt='test', backend=Backend())))


@pytest.mark.parametrize('cancelled', [False, True])
def test_provider_failure_preserves_echo_but_cancel_is_not_swallowed(cancelled):
    registry = ToolRegistry()
    discover_builtin_tools(registry)
    class Backend:
        count = 0
        async def chat_complete(self, **kwargs):
            self.count += 1
            if self.count == 1:
                return ChatResponse(content='', tool_calls=(ToolCall(name='echo', arguments={'text': 'actual receipt'}),))
            if cancelled:
                raise LLMRunCancelled('cancelled')
            raise ProviderUnavailableError('controlled500')
    backend = Backend()
    run = OrchestratorAgent().run('test', AgentContext(system_prompt='test', backend=backend, registry=registry))
    if cancelled:
        with pytest.raises(LLMRunCancelled):
            asyncio.run(run)
    else:
        result = asyncio.run(run)
        assert result.stopped_reason == 'provider_error'
        assert len(result.tool_outcomes) == 1
        assert result.tool_outcomes[0].data == {'text': 'actual receipt'}
        assert result.tool_outcomes[0].status == 'ok'
    assert backend.count == 2
