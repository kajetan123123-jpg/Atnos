"""Real isolated task/run storage; controlled capabilities, never model calls."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegateTaskInput
from atnos.ai_workers.task_queue import AiWorkerTaskQueue
from atnos.db.base import Base
from atnos.db.models.ai_worker_task import AiWorkerRun
from atnos.db.session import DatabaseManager
from atnos.repositories.ai_worker_repository import AiWorkerRepository
from test_support import build_test_settings, make_temp_dir


@pytest.fixture
def storage():
    with make_temp_dir() as temp:
        settings = build_test_settings(Path(temp))
        database = DatabaseManager(settings)
        Base.metadata.create_all(database.engine)
        try:
            yield database, settings
        finally:
            database.dispose()


def create_task(repository, **kwargs):
    data = dict(task_type='capability_workflow', redacted_prompt='Sprawdź zapisane dane zadania.',
        worker_key='capability_runtime', privacy_mode='strict_local', priority='normal',
        requested_by='isolated-regression', input_summary='Controlled task', payload_json={},
        approval_required=False)
    data.update(kwargs)
    return repository.create_task(**data)


def delegation_input(task_type):
    return DelegateTaskInput(task_type=task_type, prompt='Sprawdź zapisane dane zadania.',
        context={}, preferred_worker=None, allowed_workers=(), privacy_mode='strict_local',
        priority='normal', requested_by='isolated-regression', requires_approval_for_result=False)


@pytest.mark.parametrize('entry', ['direct', 'child', 'capability_request'])
@pytest.mark.parametrize('source_status, expected', [
    ('PARTIAL', 'incomplete'), ('FAILED', 'failed'), ('BLOCKED', 'blocked'),
    ('REQUIRES_SETUP', 'blocked'), ('REQUIRES_DATA', 'blocked'),
    ('REQUIRES_APPROVAL', 'blocked'), ('UNKNOWN-FUTURE', 'failed'), (None, 'failed'),
    ('COMPLETED', 'completed'),
])
def test_real_task_and_run_do_not_promote_partial_or_unknown_result(storage, entry, source_status, expected):
    database, settings = storage
    status = ('SUCCESS' if source_status == 'COMPLETED' else source_status) if entry == 'capability_request' else source_status
    key = 'status' if entry == 'capability_request' else 'workflow_status'
    result = {key: status, 'capabilities_used': [], 'execution_id': 'controlled-evidence-729'}
    with database.session_scope() as session:
        router = AiDelegationRouter(session, settings)
        if entry == 'child':
            task = create_task(router.repository)
            with patch.object(router, '_execute_capability_workflow', return_value=result):
                answer = asyncio.run(router.run_task(task.id))
        elif entry == 'direct':
            with patch.object(router, '_execute_capability_workflow', return_value=result):
                answer = asyncio.run(router.delegate(delegation_input('capability_workflow')))
        else:
            with patch('atnos.integrations.capability_router.route_data_need', return_value=result):
                answer = asyncio.run(router.delegate(delegation_input('capability_request')))
        assert answer['status'] == expected
        task = router.repository.get_task(answer['id'])
        assert task.result_json == result
        assert len(task.runs) == 1 and task.runs[0].status == expected
        assert task.runs[0].completed_at is not None
        assert task.runs[0].result_json == result
        session.commit()


def test_task_start_cannot_be_claimed_again_from_a_stale_session(storage):
    database, _ = storage
    with database.session_scope() as session:
        task = create_task(AiWorkerRepository(session))
        task_id = task.id
    with database.session_scope() as first, database.session_scope() as second:
        repo1, repo2 = AiWorkerRepository(first), AiWorkerRepository(second)
        task1, stale = repo1.get_task(task_id), repo2.get_task(task_id)
        repo1.start_task(task1, worker_key='capability_runtime')
        first.commit()
        with pytest.raises(ValueError, match='claim|running|changed|claimed'):
            repo2.start_task(stale, worker_key='capability_runtime')
        second.rollback()
    with database.session_scope() as session:
        runs = list(session.scalars(select(AiWorkerRun).where(AiWorkerRun.task_id == task_id)))
        assert len(runs) == 1


def test_filtered_queue_preserves_declared_order_inside_a_group(storage):
    database, settings = storage
    with database.session_scope() as session:
        queue = AiWorkerTaskQueue(session)
        ordered = queue.enqueue_manual(tasks=['ORDER-729 first', 'ORDER-729 second', 'ORDER-729 third'], conversation_id=41)
        queue.enqueue_manual(tasks=['OTHER unrelated'], conversation_id=42)
        router = AiDelegationRouter(session, settings)
        tasks = router.list_tasks(query='ORDER-729', conversation_id=41)
        assert [task['id'] for task in tasks] == [task['id'] for task in ordered]
        assert all(task['payload_json']['execution_scope'] == 'hold_only' for task in tasks)


def test_background_context_can_be_filtered_by_conversation_and_receipt_token(storage):
    database, settings = storage
    with database.session_scope() as session:
        router = AiDelegationRouter(session, settings)
        task = create_task(router.repository, payload_json={'context': {
            'conversation_id': 41, 'job_token': 'JOB-SCOPE-729'}})
        create_task(router.repository, payload_json={'context': {'conversation_id': 42}})
        assert [item['id'] for item in router.list_tasks(conversation_id=41)] == [task.id]
        assert [item['id'] for item in router.list_tasks(query='JOB-SCOPE-729')] == [task.id]


def test_background_target_keeps_trusted_conversation_identity(storage):
    from atnos.services.background_subagent_service import BackgroundSubagentService
    database, settings = storage
    with patch('atnos.services.background_subagent_service.threading.Thread') as factory:
        factory.return_value.name = 'controlled-background-thread'
        requested = BackgroundSubagentService(settings).start(prompt='Sprawdź zapisane dane.',
            task_type='capability_workflow', conversation_id=41)
        target = factory.call_args.kwargs['target']
        assert requested['execution_verified'] is False
    # Execute the actual target synchronously, using a controlled capability
    # result; this is not a live worker/model claim.
    with patch.object(AiDelegationRouter, '_execute_capability_workflow', return_value={
            'workflow_status': 'COMPLETED', 'capabilities_used': []}):
        target()
    tasks = BackgroundSubagentService(settings).list_recent(conversation_id=41)
    assert tasks['count'] == 1
    assert tasks['tasks'][0]['job_token'] == requested['job_token']
    assert tasks['tasks'][0]['status'] == 'completed'
    assert BackgroundSubagentService(settings).list_recent(query=requested['job_token'])['count'] == 1
    assert BackgroundSubagentService(settings).list_recent(conversation_id=42)['count'] == 0


def test_background_tool_does_not_let_model_override_conversation_identity(storage):
    from atnos.tools.chat_agent_tools import AiDelegateBackgroundTool
    _, settings = storage
    with patch('atnos.services.background_subagent_service.BackgroundSubagentService.start', return_value={}) as start:
        asyncio.run(AiDelegateBackgroundTool(settings=settings, conversation_id=41).execute(
            prompt='Controlled task', conversation_id=999))
    assert start.call_args.kwargs['conversation_id'] == 41


@pytest.mark.parametrize('has_data', [True, False])
def test_real_healthy_provider_reply_is_not_only_a_healthcheck(storage, has_data):
    database, settings = storage
    source = Path(__file__).parent/'fixtures/worker_public_data_healthy_20261026.json'
    receipt = json.loads(source.read_text('utf-8'))
    if not has_data:
        receipt.pop('value')
    with database.session_scope() as session:
        router = AiDelegationRouter(session, settings)
        with patch('atnos.integrations.capability_router.route_data_need', return_value=receipt):
            answer = asyncio.run(router.delegate(delegation_input('capability_request')))
        assert answer['status'] == ('completed' if has_data else 'failed')
        assert answer['result_json'] == receipt


def test_capability_request_retry_uses_same_task_and_preserves_failed_run(storage):
    database, settings = storage
    receipt = json.loads((Path(__file__).parent/'fixtures/worker_public_data_healthy_20261026.json').read_text('utf-8'))
    with database.session_scope() as session:
        router = AiDelegationRouter(session, settings)
        task = create_task(router.repository, task_type='capability_request', worker_key='capability_router',
                           approval_required=True)
        first = router.repository.start_task(task, worker_key='capability_router')
        router.repository.fail_task(task, first, error_message='Old wrapper did not recognize HEALTHY', result_json=receipt)
        session.commit()
        with patch('atnos.integrations.capability_router.route_data_need', return_value=receipt), \
             patch.object(router, '_worker_status', side_effect=AssertionError('No model worker should be selected')):
            answer = asyncio.run(router.run_task(task.id))
        assert answer['id'] == task.id and answer['status'] == 'completed'
        assert answer['verification_status'] == 'awaiting_human_review'
        assert [run.status for run in task.runs] == ['failed', 'completed']
        assert task.runs[0].result_json == receipt


@pytest.mark.parametrize('human_review', [False, True])
def test_plan_cannot_hide_a_usable_partial_child_as_completed(storage, human_review):
    database, settings = storage
    with database.session_scope() as session:
        router = AiDelegationRouter(session, settings)
        root = create_task(router.repository, task_type='plan', worker_key='planner',
                           approval_required=human_review)
        router.repository.mark_planned(root, result_summary='Controlled plan', result_json={}, task_group='PARTIAL-729')
        child = create_task(router.repository, parent_task_id=root.id, task_group='PARTIAL-729', sequence_index=0)
        source_result = {'workflow_status': 'PARTIAL', 'production_status': 'PRODUCTION_BRIEF_READY',
                         'capabilities_used': [], 'skipped': ['Optional retention export missing']}
        with patch.object(router, '_execute_capability_workflow', return_value=source_result):
            answer = asyncio.run(router.run_safe_plan(root.id))
        assert answer['status'] == ('awaiting_review' if human_review else 'incomplete')
        assert answer['verification_status'] == 'incomplete'
        assert answer['result_json']['partial_child_ids'] == [child.id]
        assert child.status == 'incomplete' and child.runs[0].status == 'incomplete'
        assert 'nie jest w pełni ukończony' in answer['result_summary']
