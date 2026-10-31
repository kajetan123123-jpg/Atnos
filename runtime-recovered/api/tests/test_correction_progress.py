"""Current correction telemetry must not certify historical downstream checkpoints."""
import copy
from atnos.production.project_profile import content_hash
from atnos.production.remotion_quality_repair import correction_progress


def state():
    old = [{'stage': key, 'status': 'done', 'attempts': 2, 'input_hash': 'old',
            'output': {'segments': [{'id': 's01'}]} if key == 'script' else {}, 'updated_at': '2026-10-29T12:00:00Z'}
        for key in ('script', 'voice', 'captions', 'remotion_render', 'remotion_mux', 'covers', 'media_qa',
                    'semantic/s01', 'semantic/cover_portrait', 'semantic/cover_landscape', 'semantic_qa', 'package')]
    body = {'job_id': 'fixture', 'scope': 'renderer_and_downstream_only'}
    digest = content_hash(body)
    registration = {'stage': 'remotion_refresh/'+digest[:12], 'status': 'done', 'input_hash': digest,
        'updated_at': '2026-10-30T12:00:00Z', 'output': {**body, 'digest': digest, 'before_checkpoints': copy.deepcopy(old)}}
    return {'id': 'fixture', 'checkpoints': [*copy.deepcopy(old), registration], 'history': []}


def test_old_done_rows_are_pending_for_new_correction():
    result = correction_progress(state())
    assert result['status'] == 'registered' and result['completed'] == 0
    assert all(row['status'] == 'pending' for row in result['stages'])


def test_new_attempt_and_cache_events_are_distinguished_from_old_history():
    current = state()
    row = next(r for r in current['checkpoints'] if r['stage'] == 'remotion_render')
    row.update(attempts=3, status='running')
    current['history'] = [
        {'stage': 'covers', 'event': 'cache_reused', 'created_at': '2026-10-29T12:30:00Z'},
        {'stage': 'media_qa', 'event': 'cache_reused', 'created_at': '2026-10-30T14:01:00+02:00'}]
    rows = {r['stage']: r for r in correction_progress(current)['stages']}
    assert rows['remotion_render']['status'] == 'running'
    assert rows['covers']['status'] == 'pending'
    assert rows['media_qa']['status'] == 'done' and rows['media_qa']['reused'] is True


def test_corrupted_registration_does_not_report_done():
    current = state(); current['checkpoints'][-1]['output']['job_id'] = 'somewhere-else'
    result = correction_progress(current)
    assert result['status'] == 'unverified' and result['stages'] == []


def test_ordinary_orders_have_no_correction_timeline():
    current = state(); current['checkpoints'].pop()
    assert correction_progress(current) is None
