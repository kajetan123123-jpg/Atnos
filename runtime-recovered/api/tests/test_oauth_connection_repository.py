from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository

from .test_support import bootstrap_test_app, create_test_app


def _create_connection(repository: OAuthConnectionRepository, *, status: str):
    return repository.create(
        connector_key="youtube-studio",
        display_name="YouTube Studio",
        status=status,
        scopes_json=["openid"],
        state_hash=None,
        code_verifier_ref=None,
        token_ref="aes_256_gcm:" + ("1" * 32) if status == "connected" else None,
        account_id=None,
        connected_at=datetime.now(timezone.utc) if status == "connected" else None,
        disconnected_at=None,
        last_error="Reconnect required." if status == "error" else None,
        metadata_json={},
        audit_log_json=[],
    )


def test_connected_connection_is_not_hidden_by_new_pending_flow(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    try:
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            repository = OAuthConnectionRepository(session)
            connected = _create_connection(repository, status="connected")
            _create_connection(repository, status="pending")

            selected = repository.get_latest_for_connector("youtube-studio")

            assert selected is not None
            assert selected.id == connected.id
            assert selected.status == "connected"
    finally:
        app.state.database.dispose()


def test_expired_pending_flow_does_not_hide_reconnect_error(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    try:
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            repository = OAuthConnectionRepository(session)
            reconnect_error = _create_connection(repository, status="error")
            pending = _create_connection(repository, status="pending")
            pending.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20)
            repository.save(pending)

            selected = repository.get_latest_for_connector("youtube-studio")

            assert selected is not None
            assert selected.id == reconnect_error.id
            assert selected.status == "error"
    finally:
        app.state.database.dispose()
