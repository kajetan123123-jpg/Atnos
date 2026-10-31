"""A mistaken model tool call must not search Start Menu for agent tasks."""
import asyncio
from unittest.mock import patch
import pytest
from atnos.tools.chat_agent_tools import DesktopAppOpenTool

@pytest.mark.parametrize('query',['ić kilku workerów','kilku workerów','3 agentów','zadanie w pętli','restart serwera'])
def test_task_targets_do_not_touch_desktop(query):
    tool=DesktopAppOpenTool(settings=None,session=None)
    with patch('atnos.tools.chat_agent_tools.LocalHostControlService') as host:
        result=asyncio.run(tool.execute(query=query))
    assert result['status']=='not_app_request'
    host.assert_not_called()

@pytest.mark.parametrize('query',['Notatnik','Process Explorer',r'E:\Atnos\Tools\worker.exe'])
def test_real_app_targets_keep_existing_resolver(query):
    tool=DesktopAppOpenTool(settings=None,session=None)
    with patch('atnos.tools.chat_agent_tools.LocalHostControlService') as host:
        host.return_value.open_or_resolve_desktop_app.return_value={'status':'not_found'}
        result=asyncio.run(tool.execute(query=query))
    assert result['status']=='not_found'
    host.return_value.open_or_resolve_desktop_app.assert_called_once_with(query=query,list_only=False)
