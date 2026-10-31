"""Regression evidence for the 2026-10-08 audit; no cloud or PC mutations."""
import asyncio
from types import SimpleNamespace

import pytest

from atnos.core.config import Settings
from atnos.tools import chat_agent_tools as tools
from atnos.integrations import capability_runtime as runtime
from atnos.production.steps.order_voice import voice_settings
from atnos.tools.executor import ToolExecutor
from atnos.tools.registry import ToolRegistry
from atnos.llm.base import ToolCall
from atnos.tools.chat_hard_stop import classify_shell_command


@pytest.mark.parametrize('command', ["[System.IO.File]::WriteAllText('C:/audit-must-not-write.txt','test')", "python -c 'print(1)'", "Get-Date; [IO.File]::Delete('C:/audit-must-not-delete.txt')"])
def test_arbitrary_script_is_not_treated_as_proven_safe(command):
    assert classify_shell_command(command)[0] is True


@pytest.mark.parametrize('command', ['Get-Date', 'Get-Process -Id 123', 'Write-Output audit-only'])
def test_simple_read_only_commands_remain_available(command):
    assert classify_shell_command(command)[0] is False


def test_invalid_arguments_can_be_corrected_without_false_loop(tmp_path):
    from atnos.agentic import AgentContext, OrchestratorAgent
    from atnos.llm.base import ChatResponse
    target=tmp_path/'read.txt'; target.write_text('AUDIT READ',encoding='utf-8')
    registry=ToolRegistry(); registry.register_tool(tools.FsReadTool())
    responses=[ChatResponse(content='',tool_calls=(ToolCall(name='fs_read',arguments={'path':str(target),'max_chars':50},id='bad'),)), ChatResponse(content='',tool_calls=(ToolCall(name='fs_read',arguments={'path':str(target),'max_chars':200},id='corrected'),)), ChatResponse(content='AUDIT READ')]
    class Backend:
        async def chat_complete(self,**kwargs): return responses.pop(0)
    result=asyncio.run(OrchestratorAgent().run('audit read',AgentContext(system_prompt='test',backend=Backend(),registry=registry,max_turns=3)))
    assert result.stopped_reason=='completed'
    assert [o.status for o in result.tool_outcomes]==['error','ok']


def test_external_tool_respects_disabled_cloud_before_dispatch(monkeypatch):
    def unexpected_service(*args, **kwargs):
        pytest.fail("Disabled cloud must be rejected before constructing the external dispatcher")
    from atnos.services import external_llm_operator_service
    monkeypatch.setattr(external_llm_operator_service, "ExternalLlmOperatorService", unexpected_service)
    tool = tools.ExternalLlmAskTool(settings=Settings(_env_file=None, cloud_llm_allowed=False), session=None, provider_registry=None)
    result = asyncio.run(tool.execute(target="openai", prompt="audit-only"))
    assert result["status"] == "blocked"
    assert result["blocker"] == "cloud_llm_disabled"


def test_self_state_does_not_claim_unprobed_external_access(monkeypatch):
    monkeypatch.setattr(tools, "ObsidianReadService", lambda *_args: SimpleNamespace(get_vault_status=lambda: SimpleNamespace(vault_name="test", path_configured=False, path_exists=False, readable=False, note_count=0, warnings=[])))
    tool = tools.SelfStateTool(settings=Settings(_env_file=None, cloud_llm_allowed=False), session=None, conversation_id=909, capability_snapshot={"browser_operator_available":False})
    monkeypatch.setattr(tool.approvals, "get", lambda _: None)
    result = asyncio.run(tool.execute())
    assert result["can_do"]["realtime_internet"] is False
    assert result["can_do"]["external_llms_claude_openai_gemini"] is False
    assert "internet=yes" not in result["summary"]


def test_external_dispatch_cannot_bypass_cloud_policy_with_browser(monkeypatch):
    from atnos.services.external_llm_operator_service import ExternalLlmOperatorService, ExternalLlmRequest
    service = ExternalLlmOperatorService.__new__(ExternalLlmOperatorService)
    service.settings = Settings(_env_file=None, cloud_llm_allowed=False)
    for method in ("_try_api_path", "_try_browser_path", "_try_desktop_path"):
        monkeypatch.setattr(service, method, lambda **kwargs: pytest.fail("External transport must not run with cloud disabled"))
    result = service._execute_request(request=ExternalLlmRequest(target="openai", prompt="audit-only", task_type="general", mode_preference="browser", explicit_target=True, fallback_policy="normal"), allow_external_fallback=True, runtime_controls=None)
    assert result.route_path == "blocked"
    assert result.blocker == "cloud_llm_disabled"


@pytest.mark.parametrize("claim", [{"confirmed": True}, {"confirmed": True, "approval_id": "invented"}])
def test_model_cannot_self_approve_delete(tmp_path, monkeypatch, claim):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(tools, "classify_delete", lambda _: (True, "test_outside_root"))
    tool = tools.FsDeleteTool(settings=Settings(_env_file=None, data_dir=tmp_path), conversation_id=11)
    result = asyncio.run(tool.execute(path=str(target), **claim))
    assert result["status"] == "needs_confirmation"
    assert target.read_text(encoding="utf-8") == "keep"


def test_known_pending_id_is_not_human_authorization(tmp_path, monkeypatch):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(tools, "classify_delete", lambda _: (True, "test_outside_root"))
    tool = tools.FsDeleteTool(settings=Settings(_env_file=None, data_dir=tmp_path), conversation_id=11)
    pending = asyncio.run(tool.execute(path=str(target)))
    result = asyncio.run(tool.execute(path=str(target), confirmed=True, approval_id=pending["approval_id"]))
    assert result["status"] == "needs_confirmation"
    assert target.exists()


def test_human_approval_executes_only_recorded_action_once(tmp_path, monkeypatch):
    target = tmp_path / "approved-test.txt"
    target.write_text("test-only", encoding="utf-8")
    monkeypatch.setattr(tools, "classify_delete", lambda _: (True, "test_outside_root"))
    tool = tools.FsDeleteTool(settings=Settings(_env_file=None, data_dir=tmp_path), conversation_id=11)
    pending = asyncio.run(tool.execute(path=str(target)))
    tool.authorize_pending_approval(pending["approval_id"])
    result = asyncio.run(tool.execute(path=str(target), confirmed=True, approval_id=pending["approval_id"]))
    assert result["status"] == "deleted"
    assert not target.exists()
    target.write_text("recreated", encoding="utf-8")
    replay = asyncio.run(tool.execute(path=str(target), confirmed=True, approval_id=pending["approval_id"]))
    assert replay["status"] == "needs_confirmation"
    assert target.exists()


def test_model_cannot_self_approve_shell(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "classify_shell_command", lambda _: (True, "test_destructive"))
    monkeypatch.setattr(tools, "LocalHostControlService", lambda *_args, **_kwargs: SimpleNamespace(
        run_powershell=lambda *args, **kwargs: calls.append((args, kwargs))))
    tool = tools.ShellPowerShellTool(settings=Settings(_env_file=None, data_dir=tmp_path), session=None, conversation_id=12)
    result = asyncio.run(tool.execute(command="test-only-command", confirmed=True))
    assert result["status"] == "needs_confirmation"
    assert calls == []


@pytest.mark.parametrize("project,capability", [("children_stories", "SOCIAL_PUBLISH_PREPARE"), ("horror_stories", "ANIMATION_2D_RENDER")])
def test_canonical_project_names_keep_existing_restrictions(project, capability):
    assert runtime._project_allowed(project, "YOUTUBE_SCRIPT_QA") is True
    assert runtime._project_allowed(project, capability) is False


def test_frozen_voice_does_not_enable_disabled_cloud():
    settings = Settings(_env_file=None, shorts_allow_cloud_tts=False)
    config = dict(voice_id="test", model="eleven_v3", stability=.5, similarity=.75, style=0, speed=1, speaker_boost=True)
    assert voice_settings(settings, config).shorts_allow_cloud_tts is False


def test_tool_schema_rejects_malformed_arguments_before_effect(tmp_path):
    target = tmp_path / "must-not-exist.txt"
    registry = ToolRegistry()
    registry.register_tool(tools.FsWriteTool())
    result = asyncio.run(ToolExecutor(registry).execute_tool_call(
        ToolCall(name="fs_write", arguments={"path": str(target), "content": {"invalid": True}}, id="test")))
    assert not result.success
    assert not target.exists()


def test_tool_write_respects_E_policy_without_touching_C():
    with pytest.raises(PermissionError, match="E:"):
        asyncio.run(tools.FsWriteTool().execute(path=r"C:\Atnos-audit-forbidden\test.txt", content="test"))


@pytest.mark.parametrize("command", [
    "Remove-Item -LiteralPath 'C:\\AuditOnly\\test.txt'",
    "Remove-Item $target",
    "Remove-Item E:\\Atnos\\Temp\\safe.txt; Remove-Item -Path C:\\AuditOnly\\test.txt",
    "Stop-Process -Id 99999",
])
def test_complex_destructive_shell_cannot_evade_confirmation(command):
    assert classify_shell_command(command)[0] is True


@pytest.mark.parametrize("message", ["Nie zapisuj grafu pamięci ani innych notatek.", "Nic nie zapisuj.", "Do not save any memories."])
def test_explicit_denial_stops_automatic_memory_write(monkeypatch, message):
    from atnos.services import chat_session_memory as memory
    writes = []
    monkeypatch.setattr(memory, "persist_chat_decision_memory", lambda **kwargs: writes.append(kwargs))
    memory.persist_agentic_session_memory(settings=None, user_message=message,
        assistant_message="Zrobione", tools_used=["obsidian_write"], enabled=True)
    assert writes == []
