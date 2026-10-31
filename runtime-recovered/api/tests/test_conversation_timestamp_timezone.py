from datetime import UTC, datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from test_support import create_test_app
from atnos.db.models.message import Message
from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.core.timestamps import as_utc


@pytest.mark.parametrize('stamp', [datetime(2026, 10, 29, 17, 0), datetime(2026, 7, 1, 17, 0)])
def test_reloaded_chat_history_preserves_utc_instant(tmp_path, stamp):
    app = create_test_app(tmp_path)
    try:
        with TestClient(app) as client:
            conversation_id = client.post('/conversations', json={'title':'Timezone fixture'}).json()['conversation_id']
            with app.state.database.session_scope() as session:
                session.add(Message(conversation_id=conversation_id, sequence_number=1,
                    role=MessageRole.USER, status=MessageStatus.COMPLETED, content='Isolated test',
                    created_at=stamp, updated_at=stamp))
            data = client.get(f'/ui/chat/conversations/{conversation_id}').json()
            actual = datetime.fromisoformat(data['messages'][0]['created_at'])
            assert actual.tzinfo is not None, 'SQLite UTC timestamp must keep its zone at the UI boundary'
            assert actual.timestamp() == stamp.replace(tzinfo=UTC).timestamp()
            summary = next(c for c in client.get('/conversations').json() if c['id']==conversation_id)
            assert datetime.fromisoformat(summary['updated_at']).tzinfo is not None
    finally:
        app.state.database.dispose()


def test_aware_timestamp_preserves_its_instant():
    source = datetime(2026, 7, 1, 19, 0, tzinfo=timezone(timedelta(hours=2)))
    assert as_utc(source) == datetime(2026, 7, 1, 17, 0, tzinfo=UTC)
