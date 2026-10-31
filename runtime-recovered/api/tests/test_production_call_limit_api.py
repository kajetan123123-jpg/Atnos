from fastapi.testclient import TestClient
import pytest

from atnos.api.routes import production as routes
from atnos.intelligence.storage import connect
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder
from test_production_order_api import seed_account
from test_support import create_test_app


@pytest.fixture
def limited_order(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)

    def unexpected_dispatch(*args, **kwargs):
        pytest.fail('Approving a limit must not launch production')

    monkeypatch.setattr(routes.ProductionOrderDispatcher, 'dispatch', unexpected_dispatch)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        store = ProductionOrderStore(app.state.settings)
        job_id = store.create(ProductionOrder(topic='Call limit contract', delivery_mode='local_only'))['id']
        with store.lease(job_id) as token:
            store.begin(job_id, token, 'script', {})
            store.fail(job_id, token, 'script', category='provider_failure', message='Retained failure')
        with connect(store.settings) as db:
            import json
            row = db.execute('SELECT profile_json FROM pipeline_job_orders WHERE job_id=?', (job_id,)).fetchone()
            profile = json.loads(row['profile_json'])
            profile['production']['max_provider_calls'] = 30
            profile['production']['max_cost_usd'] = 2.0
            db.execute(
                'UPDATE pipeline_job_orders SET profile_json=?, provider_calls=30, reserved_cost_units=1000000 WHERE job_id=?',
                (json.dumps(profile, ensure_ascii=False), job_id),
            )
            db.execute("UPDATE pipeline_jobs SET status='blocked',error='provider_call_limit' WHERE id=?", (job_id,))
            db.commit()
        yield client, store, job_id


def test_approval_preserves_consumed_work_and_is_visible_after_reload(limited_order):
    client, store, job_id = limited_order
    before = store.get(job_id)
    url = f'/api/production/orders/{job_id}'
    initial = client.get(url).json()
    assert initial['max_provider_calls'] == 30
    assert initial['can_extend_call_limit'] is True
    assert initial['can_resume'] is False
    first = client.post(f'{url}/call-limit', json={'max_calls': 45})
    assert first.status_code == 200
    for response in (first, client.get(url), client.post(f'{url}/call-limit', json={'max_calls': 45})):
        result = response.json()
        assert result['max_provider_calls'] == 45
        assert result['provider_calls'] == 30
        assert result['reserved_cost_units'] == 1000000
        assert result['budget_limit_usd'] == 2
        assert result['status'] == 'queued'
        assert result['can_resume'] is True
        assert result['worker_live'] is False
        assert result['can_extend_call_limit'] is False
    after = store.get(job_id)
    assert after['profile'] == before['profile']
    assert after['profile_hash'] == before['profile_hash']
    assert after['checkpoints'] == before['checkpoints']
    assert after['history'][:-1] == before['history']
    assert after['history'][-1]['event'] == 'call_limit_authorized'


@pytest.mark.parametrize('payload', [
    {'max_calls': True}, {'max_calls': '45'}, {'max_calls': 45.5}, {'max_calls': 100001},
    {'max_calls': 0}, {}, {'max_calls': 45, 'max_cost_usd': 100},
])
def test_invalid_approval_is_rejected_without_changing_state(limited_order, payload):
    client, store, job_id = limited_order
    before = store.get(job_id)
    response = client.post(f'/api/production/orders/{job_id}/call-limit', json=payload)
    assert response.status_code == 422
    assert store.get(job_id) == before


def test_cannot_lower_limit_or_change_a_live_order(limited_order):
    client, store, job_id = limited_order
    url = f'/api/production/orders/{job_id}/call-limit'
    assert client.post(url, json={'max_calls': 20}).status_code == 400
    with store.lease(job_id):
        assert client.post(url, json={'max_calls': 45}).status_code == 409
    assert store.get(job_id)['max_provider_calls'] == 30


@pytest.mark.parametrize('status', ['ready', 'cancelled'])
def test_terminal_order_limit_cannot_change(limited_order, status):
    client, store, job_id = limited_order
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_jobs SET status=? WHERE id=?', (status, job_id))
        db.commit()
    assert client.post(f'/api/production/orders/{job_id}/call-limit', json={'max_calls': 45}).status_code == 409
    assert store.get(job_id)['max_provider_calls'] == 30


@pytest.mark.parametrize('error', ['attempt_limit', 'cost_limit', 'script_quality_limit'])
def test_more_calls_do_not_clear_other_limits(limited_order, error):
    client, store, job_id = limited_order
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_jobs SET error=? WHERE id=?', (error, job_id))
        db.commit()
    response = client.post(f'/api/production/orders/{job_id}/call-limit', json={'max_calls': 45})
    assert response.status_code == 200
    assert response.json()['error'] == error
    assert response.json()['status'] == 'blocked'
    assert response.json()['can_resume'] is False
    assert response.json()['can_extend_call_limit'] is False


def test_missing_order_returns_404(limited_order):
    client, _, _ = limited_order
    assert client.post('/api/production/orders/prod-missing/call-limit', json={'max_calls': 45}).status_code == 404
