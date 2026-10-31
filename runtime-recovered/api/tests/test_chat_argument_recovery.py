"""Recover a rejected argument schema without replaying completed side effects."""
import asyncio
from atnos.agentic import AgentContext, OrchestratorAgent
from atnos.llm.base import ChatResponse, ToolCall
from atnos.tools import ToolRegistry
from atnos.tools.builtin.echo import EchoTool
from test_agentic_core import _ScriptedBackend

def test_invalid_arguments_are_still_an_unexecuted_required_step():
    calls = []
    class Read(EchoTool):
        name = 'read_step'
        async def execute(self, **kwargs):
            calls.append(kwargs['text'])
            return {'status': 'ok', 'text': kwargs['text']}
    registry = ToolRegistry()
    registry.register_tool(Read())
    backend = _ScriptedBackend([
        ChatResponse(content='', tool_calls=(ToolCall(name='read_step', arguments={}),)),
        ChatResponse(content='Done.'),
        ChatResponse(content='', tool_calls=(ToolCall(name='read_step', arguments={'text': 'actual result'}),)),
        ChatResponse(content='Read complete.'),
    ])
    result = asyncio.run(OrchestratorAgent().run('Read', AgentContext(system_prompt='Use tools',
        backend=backend, registry=registry, max_turns=4, required_tool_names=('read_step',))))
    assert calls == ['actual result']
    assert result.turns_used == 4
    assert result.tool_outcomes[-1].status == 'ok'

def test_argument_recovery_does_not_repeat_an_already_committed_write():
    calls = []
    class Write(EchoTool):
        name = 'write_step'
        async def execute(self, **kwargs):
            calls.append('write')
            return {'status': 'written'}
    class Read(EchoTool):
        name = 'read_step'
        async def execute(self, **kwargs):
            calls.append('read')
            return {'status': 'ok'}
    registry = ToolRegistry()
    registry.register_tool(Write())
    registry.register_tool(Read())
    backend = _ScriptedBackend([
        ChatResponse(content='', tool_calls=(ToolCall(name='write_step', arguments={'text': 'save'}),
                                           ToolCall(name='read_step', arguments={}))),
        ChatResponse(content='All done.'),
        ChatResponse(content='', tool_calls=(ToolCall(name='read_step', arguments={'text': 'read'}),)),
        ChatResponse(content='Both complete.'),
    ])
    result = asyncio.run(OrchestratorAgent().run('Write then read', AgentContext(system_prompt='Use tools',
        backend=backend, registry=registry, max_turns=4, required_tool_names=('write_step', 'read_step'))))
    assert calls == ['write', 'read']
    assert result.stopped_reason == 'completed'
    assert 'name: read_step.' in str(backend.calls[2]['messages'])

def test_uncertain_runtime_error_does_not_get_a_schema_recovery_reminder():
    calls = []
    class Uncertain(EchoTool):
        name = 'write_step'
        async def execute(self, **kwargs):
            calls.append('possibly committed')
            raise RuntimeError('An operation was dispatched; outcome unknown')
    registry = ToolRegistry()
    registry.register_tool(Uncertain())
    backend = _ScriptedBackend([
        ChatResponse(content='', tool_calls=(ToolCall(name='write_step', arguments={'text': 'save'}),)),
        ChatResponse(content='Cannot confirm the result.'),
    ])
    result = asyncio.run(OrchestratorAgent().run('Write', AgentContext(system_prompt='Use tools',
        backend=backend, registry=registry, max_turns=4, required_tool_names=('write_step',))))
    assert calls == ['possibly committed']
    assert len(backend.calls) == 2
    assert result.tool_outcomes[-1].status == 'error'

def test_later_invalid_duplicate_cannot_request_replaying_a_committed_write():
    calls = []
    class Write(EchoTool):
        name = 'write_step'
        async def execute(self, **kwargs):
            calls.append('written')
            return {'status': 'written'}
    registry = ToolRegistry()
    registry.register_tool(Write())
    backend = _ScriptedBackend([
        ChatResponse(content='', tool_calls=(ToolCall(name='write_step', arguments={'text': 'save'}),
                                           ToolCall(name='write_step', arguments={}))),
        ChatResponse(content='One write committed.'),
    ])
    result = asyncio.run(OrchestratorAgent().run('Write once', AgentContext(system_prompt='Use tools',
        backend=backend, registry=registry, max_turns=4, required_tool_names=('write_step',))))
    assert calls == ['written']
    assert len(backend.calls) == 2
    assert result.tool_outcomes[0].status == 'written'
