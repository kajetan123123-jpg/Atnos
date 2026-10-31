from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

from atnos.ai_workers.execution_liveness import current_executor_owner, execution_liveness


def task(status='running', children=None):
    return SimpleNamespace(status=status, children=children or [])


def test_current_process_is_only_coordinator_evidence():
    result=execution_liveness(task(), SimpleNamespace(metadata_json={'executor_owner':current_executor_owner()}))
    assert result == {'state':'coordinator_alive','execution_verified':False}


def test_missing_legacy_owner_is_unknown_not_active_or_failed():
    assert execution_liveness(task(), SimpleNamespace(metadata_json={}))['state']=='unknown'


@pytest.mark.parametrize('mode',['gone','reused','denied'])
def test_pid_identity_and_access_failures(mode):
    owner=current_executor_owner()
    with patch('atnos.ai_workers.execution_liveness.psutil.Process') as process:
        if mode=='gone': process.side_effect=psutil.NoSuchProcess(owner['pid'])
        elif mode=='denied': process.side_effect=psutil.AccessDenied(owner['pid'])
        else: process.return_value.create_time.return_value=owner['process_started_at']+100
        result=execution_liveness(task(), SimpleNamespace(metadata_json={'executor_owner':owner}))
    assert result['state']==('unknown' if mode=='denied' else 'owner_lost')
    assert result['execution_verified'] is False


def test_terminal_and_plan_status_are_not_reclassified():
    assert execution_liveness(task('completed'), None)['state']=='not_applicable'
    assert execution_liveness(task(children=[object()]), None)['state']=='not_applicable'
