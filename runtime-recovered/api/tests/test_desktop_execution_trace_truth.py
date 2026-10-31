"""Regression from the real not_found Notatnik response, without host/model calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from atnos.db.models.enums import ExecutionTraceStatus
from atnos.services.chat_orchestration_service import ChatOrchestrationService


@pytest.mark.parametrize('outcome', [
    {'status': 'not_found'}, {'status': 'blocked'}, {'status': 'ambiguous'},
    {'error': 'host failed'}, {'command_exit_code': 1}, {'status': 'incomplete'},
    {'status': 'opened', 'error': 'host failed'}, {'status': 'opened', 'command_exit_code': 0},
])
def test_unsuccessful_desktop_result_is_not_recorded_as_completed(outcome):
    service = SimpleNamespace(
        _create_assistant_message=Mock(return_value=SimpleNamespace(id=2)),
        users=Mock(), runtime_settings=Mock(), session=Mock(), execution_traces=Mock(),
        _extract_decisions_best_effort=Mock(),
    )
    service.users.get_owner.return_value = None
    metadata = {'desktop_operator': {'action': 'desktop_app_open', 'query': 'Notatnik', **outcome}}
    conversation = SimpleNamespace(conversation=SimpleNamespace(id=1),
                                   user_message=SimpleNamespace(id=1), traces=())
    ChatOrchestrationService._build_desktop_operator_message_result(
        service, conversation_result=conversation, content='Nie otworzono aplikacji.',
        metadata_json=metadata, execution_policy=None, skill_decision=None, skill_trace=Mock())
    expected = ExecutionTraceStatus.COMPLETED if outcome == {'status': 'opened', 'command_exit_code': 0} else ExecutionTraceStatus.FAILED
    assert service.execution_traces.create.call_args.kwargs['status'] == expected
    assert service._create_assistant_message.call_args.kwargs['metadata_json'] == metadata
    service.session.commit.assert_called_once()
