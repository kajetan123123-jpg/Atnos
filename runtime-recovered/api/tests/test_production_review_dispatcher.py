from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso  # noqa: E402
from atnos.production.owner_review_notifications import dispatch_review_packet_email  # noqa: E402
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES  # noqa: E402
from atnos.production.review_dispatcher import ProductionReviewDispatcher  # noqa: E402
from atnos.repositories.user_repository import UserRepository  # noqa: E402
from test_support import bootstrap_test_app, create_test_app  # noqa: E402


def _seed_qa_passed_job(settings, root: Path, *, job_id: str = "prod-review-1") -> None:
    # Review dispatch is tested independently from the public-promotion gate.
    # Dedicated quality-scheduler tests keep the strict public gate enabled.
    settings.shorts_review_require_public_quality = False
    now = utc_now_iso()
    artifact_dir = root / "data" / "production" / "acct-1" / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    video_path = artifact_dir / "final_short.mp4"
    thumbnail_path = artifact_dir / "thumbnail.jpg"
    subtitle_path = artifact_dir / "captions.srt"
    qa_path = artifact_dir / "qa_report.json"
    package_path = artifact_dir / "youtube_private_upload_package.json"
    video_path.write_bytes(b"fake mp4")
    thumbnail_path.write_bytes(b"fake jpg")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    qa_path.write_text(json.dumps(_valid_qa_report(), ensure_ascii=False), encoding="utf-8")
    package_path.write_text(
        json.dumps(
            {
                "review_id": f"review-{job_id}",
                "channel_account_id": "acct-1",
                "topic_id": "topic-review-1",
                "video_file_path": str(video_path),
                "thumbnail_file_path": str(thumbnail_path),
                "subtitle_file_path": str(subtitle_path),
                "qa_report_path": str(qa_path),
                "auto_uploadable": True,
                "upload_metadata": {
                    "title": "Why most people lose money",
                    "description": "A private review short.",
                    "tags": ["shorts", "finance"],
                    "visibility": "private",
                },
                "public_upload_allowed": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-review-1', 'acct-1', 'Why most people lose money', 'Opis', 'in_production', 88, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, qa_report_path,
                upload_package_path, created_at, updated_at
            )
            VALUES (?, 'acct-1', 'topic-review-1', 'qa_passed', 'upload_package', ?, ?, ?, ?, ?)
            """,
            (job_id, str(artifact_dir), str(qa_path), str(package_path), now, now),
        )
        db.commit()


def _valid_qa_report() -> dict:
    required_checks = [
        {
            "name": name,
            "status": "pass",
            "value": "test",
            "detail": "Seeded QA pass for review dispatcher tests.",
        }
        for name in REQUIRED_QA_CHECK_NAMES
    ]
    return {
        "overall": "passed",
        "auto_uploadable": True,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "blocking_failures": [],
        "advisory_failures": [],
        "upload_decision": {
            "status": "ready_for_private_upload",
            "auto_uploadable": True,
            "private_upload_allowed": True,
            "required_failures": [],
            "reason": "All required QA checks passed.",
        },
    }


def test_review_dispatcher_writes_review_notification_when_human_approval_is_required(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path)
    events: list[dict] = []

    result = asyncio.run(
        ProductionReviewDispatcher(
            settings,
            event_sink=lambda event: events.append(event),
        ).handle_job("prod-review-1")
    )

    assert result["status"] == "awaiting_human_review"
    review_path = Path(result["review_notification_path"])
    review_md_path = Path(result["review_markdown_path"])
    assert review_path.exists()
    assert review_md_path.exists()
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    assert payload["public_upload_allowed"] is False
    assert payload["private_upload_allowed"] is True
    assert payload["upload_endpoint"] == "POST /api/production/jobs/prod-review-1/upload-private"
    assert payload["video_file_path"].endswith("final_short.mp4")
    with connect(settings) as db:
        row = db.execute(
            "SELECT review_status, review_notified_at, review_notification_path FROM pipeline_jobs WHERE id = 'prod-review-1'"
        ).fetchone()
    assert row["review_status"] == "awaiting_human_review"
    assert row["review_notified_at"]
    assert Path(row["review_notification_path"]) == review_path
    assert events[0]["type"] == "production_review"
    assert events[0]["status"] == "awaiting_human_review"


def test_review_dispatcher_is_idempotent_for_existing_review_notification(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path)
    dispatcher = ProductionReviewDispatcher(settings)

    first = asyncio.run(dispatcher.handle_job("prod-review-1"))
    second = asyncio.run(dispatcher.handle_job("prod-review-1"))

    assert first["status"] == "awaiting_human_review"
    assert second["status"] == "already_notified"
    assert second["review_notification_path"] == first["review_notification_path"]


def test_review_dispatcher_sends_owner_review_email_when_gmail_is_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    sent: list[dict] = []

    class FakeGmailConnector:
        def __init__(self, session, settings, *, account_id=None):
            self.account_id = account_id

        def get_status(self):
            return SimpleNamespace(
                live_write_enabled=True,
                oauth_status="connected",
                connected=True,
                account_id=self.account_id,
                warnings=(),
            )

        def draft_message(self, *, to: str, subject: str, body: str, attachments=None):
            sent.append({"to": to, "subject": subject, "body": body, "attachments": attachments or []})
            return {"draft_id": "draft-review-1", "message_id": "message-review-1", "thread_id": "thread-review-1"}

        def send_message(self, *, draft_id: str):
            return {"message_id": "message-review-1", "thread_id": "thread-review-1", "label_ids": ["SENT"]}

    monkeypatch.setattr("atnos.production.owner_review_notifications.GmailConnectorService", FakeGmailConnector)
    try:
        bootstrap_test_app(app)
        asyncio.run(init_intelligence_db(settings))
        with app.state.database.session_scope() as session:
            owner = UserRepository(session).get_owner()
            assert owner is not None
            UserRepository(session).update(owner, email="account-b2096dbc@example.invalid")
        _seed_qa_passed_job(settings, tmp_path)

        result = asyncio.run(
            ProductionReviewDispatcher(
                settings,
                session_scope=app.state.database.session_scope,
            ).handle_job("prod-review-1")
        )

        assert result["owner_notification"]["status"] == "sent"
        assert sent
        assert sent[0]["to"] == "account-b2096dbc@example.invalid"
        assert "Review ready" in sent[0]["subject"]
        assert "Local video path:" in sent[0]["body"]
        assert "Local thumbnail path:" in sent[0]["body"]
        assert "Attached files:" in sent[0]["body"]
        assert sent[0]["attachments"]
        assert "POST /api/production/jobs/prod-review-1/upload-private" in sent[0]["body"]
        payload = json.loads(Path(result["review_notification_path"]).read_text(encoding="utf-8"))
        assert payload["owner_notification"]["status"] == "sent"
    finally:
        app.state.database.dispose()


def test_review_email_falls_back_when_mapped_gmail_has_no_mailbox(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    sent: list[dict] = []

    class NoMailboxGmail:
        def get_status(self):
            return SimpleNamespace(
                live_write_enabled=True,
                connected=True,
                account_id=53,
                warnings=(),
            )

        def probe(self):
            return SimpleNamespace(
                probe_status="error",
                message="Gmail provider probe failed.",
                provider_reachable=False,
            )

    class WorkingGmail:
        def get_status(self):
            return SimpleNamespace(
                live_write_enabled=True,
                connected=True,
                account_id=42,
                warnings=(),
            )

        def probe(self):
            return SimpleNamespace(
                probe_status="ok",
                message="Gmail probe completed.",
                provider_reachable=True,
            )

        def draft_message(self, *, to: str, subject: str, body: str, attachments=None):
            sent.append({"to": to, "subject": subject, "body": body, "attachments": attachments or []})
            return {"draft_id": "draft-fallback-1", "message_id": "message-fallback-1", "thread_id": "thread-fallback-1"}

        def send_message(self, *, draft_id: str):
            return {"message_id": "message-fallback-1", "thread_id": "thread-fallback-1", "label_ids": ["SENT"]}

    monkeypatch.setattr(
        "atnos.production.owner_review_notifications._gmail_delivery_candidates",
        lambda settings, session, *, account_id: [
            (NoMailboxGmail(), "content_account_mapping"),
            (WorkingGmail(), "connected_gmail_fallback"),
        ],
    )
    try:
        bootstrap_test_app(app)
        with app.state.database.session_scope() as session:
            owner = UserRepository(session).get_owner()
            assert owner is not None
            UserRepository(session).update(owner, email="account-b2096dbc@example.invalid")

            result = dispatch_review_packet_email(
                settings,
                session,
                {
                    "account_id": "youtube:account-c5034bf4@example.invalid",
                    "job_id": "prod-review-fallback",
                    "topic_title": "Fallback Gmail test",
                    "metadata": {"title": "Fallback Gmail test"},
                },
            )

        assert result["status"] == "sent"
        assert result["gmail_account_id"] == 42
        assert result["gmail_delivery_source"] == "connected_gmail_fallback"
        assert result["delivery_attempts"][0]["gmail_account_id"] == 53
        assert result["delivery_attempts"][0]["probe_status"] == "error"
        assert sent[0]["to"] == "account-b2096dbc@example.invalid"
    finally:
        app.state.database.dispose()


def test_review_dispatcher_can_auto_attempt_private_upload_when_human_approval_is_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_require_human_approval_before_upload = False
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path)
    calls: list[str] = []

    @contextmanager
    def fake_session_scope():
        yield object()

    def fake_upload_private_from_package(*, settings, session, job_id):
        calls.append(job_id)
        return {
            "status": "uploaded_private",
            "job_id": job_id,
            "youtube_video_id": "yt-private-1",
            "youtube_url": "https://www.youtube.com/watch?v=yt-private-1",
            "visibility": "private",
        }

    monkeypatch.setattr(
        "atnos.production.review_dispatcher.upload_private_from_package",
        fake_upload_private_from_package,
    )

    result = asyncio.run(
        ProductionReviewDispatcher(
            settings,
            session_scope=fake_session_scope,
        ).handle_job("prod-review-1")
    )

    assert result["status"] == "uploaded_private"
    assert calls == ["prod-review-1"]
    assert result["private_upload"]["visibility"] == "private"
    with connect(settings) as db:
        row = db.execute("SELECT review_status FROM pipeline_jobs WHERE id = 'prod-review-1'").fetchone()
    assert row["review_status"] == "uploaded_private"
