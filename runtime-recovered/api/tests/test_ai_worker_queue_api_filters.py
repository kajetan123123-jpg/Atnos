"""Normal HTTP list endpoint must not silently ignore task-scope filters."""
from pathlib import Path
from fastapi.testclient import TestClient
from atnos.ai_workers.task_queue import AiWorkerTaskQueue
from test_support import create_test_app, make_temp_dir


def test_task_list_applies_query_conversation_and_order_before_limit():
    with make_temp_dir() as temp:
        app = create_test_app(Path(temp))
        try:
            with TestClient(app) as client:
                with app.state.database.session_scope() as session:
                    queue = AiWorkerTaskQueue(session)
                    target = queue.enqueue_manual(tasks=['API-SCOPE-729 first', 'API-SCOPE-729 second',
                        'API-SCOPE-729 third'], conversation_id=41)
                    queue.enqueue_manual(tasks=['OTHER unrelated'], conversation_id=42)
                response = client.get('/api/ai-workers/tasks', params={
                    'query': 'API-SCOPE-729', 'conversation_id': 41, 'limit': 2})
                assert response.status_code == 200
                assert [row['id'] for row in response.json()['tasks']] == [row['id'] for row in target[:2]]
                assert client.get('/api/ai-workers/tasks', params={
                    'query': 'API-SCOPE-729', 'conversation_id': 42}).json()['tasks'] == []
        finally:
            app.state.database.dispose()
