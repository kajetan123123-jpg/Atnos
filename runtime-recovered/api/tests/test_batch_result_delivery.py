from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from atnos.intelligence.storage import connect
from atnos.production import batch_result_delivery as brd
from test_production_order_store import store, create
from test_publication_coordinator import ready


class Database:
    @contextmanager
    def session_scope(self):
        yield None


class Gmail:
    def __init__(self):
        self.drafts = self.sends = 0
        self.available = True
        self.timeout = self.wrong_recipient = self.missing_sent = False
        self.payload = None

    def get_status(self):
        return SimpleNamespace(live_write_enabled=self.available)

    def probe(self):
        return SimpleNamespace(probe_status="ok")

    def draft_message(self, **payload):
        self.drafts += 1
        self.payload = payload
        return {"draft_id": "draft-one"}

    def send_message(self, **_):
        self.sends += 1
        if self.timeout:
            raise TimeoutError("Unknown result of send")
        return {"message_id": "message-one"}

    def read_message(self, _):
        return {"label_ids": [] if self.missing_sent else ["SENT"],
                "headers": {"To": "wrong@example.com" if self.wrong_recipient else self.payload["to"]}}


@pytest.fixture
def delivery(store, monkeypatch):
    gmail = Gmail()
    monkeypatch.setattr(brd.OAuthConnectionRepository, "list_all", lambda _: [SimpleNamespace(connector_key="gmail", status="connected", account_id=1)])
    service = brd.BatchResultDelivery(store.settings, Database(), gmail_factory=lambda *a, **k: gmail)
    return service, gmail, store


def register(service, jobs):
    return service.register("four-finance", "account-a2dafda4@example.invalid", jobs, approved=True)


def test_batch_requires_explicit_immutable_authorization(delivery):
    service, _, store = delivery
    job = create(store)["id"]
    with pytest.raises(ValueError, match="permission"):
        service.register("four-finance", "account-a2dafda4@example.invalid", [job])
    first = register(service, [job])
    assert register(service, [job]) == first
    with pytest.raises(ValueError, match="different inputs"):
        service.register("four-finance", "another@example.com", [job], approved=True)


def test_waits_for_every_order_before_sending(delivery):
    service, gmail, store = delivery
    register(service, [ready(store), create(store)["id"]])
    assert service.process("four-finance")["status"] == "waiting_production"
    assert gmail.sends == gmail.drafts == 0


def test_intake_hold_is_not_mistaken_for_finished_production(delivery):
    service, gmail, store = delivery
    job = create(store)["id"]
    with store.lease(job) as token:
        store.fail(job, token, "intake_review", category="unsupported_format", message="Intake needs adaptation")
    register(service, [job])
    assert service.process("four-finance")["status"] == "waiting_production"
    assert gmail.sends == gmail.drafts == 0


def test_real_artifact_report_and_send_readback_are_idempotent(delivery):
    service, gmail, store = delivery
    register(service, [ready(store), ready(store)])
    result = service.process("four-finance")
    assert result["status"] == "sent"
    assert json.loads(result["evidence_json"])["recipient_verified"]
    assert all(path.is_file() for path in gmail.payload["attachments"])
    assert len({path.name for path in gmail.payload["attachments"]}) == len(gmail.payload["attachments"])
    assert "tylko na komputerze" in gmail.payload["body"]
    assert service.process("four-finance")["status"] == "sent"
    assert gmail.drafts == gmail.sends == 1


def test_gmail_reconnect_retries_without_losing_batch(delivery):
    service, gmail, store = delivery
    register(service, [ready(store)])
    gmail.available = False
    assert service.process("four-finance")["status"] == "blocked_user_action"
    assert gmail.sends == 0
    gmail.available = True
    assert service.process("four-finance")["status"] == "sent"


def test_no_connected_mailbox_preserves_local_report(delivery, monkeypatch):
    service, gmail, store = delivery
    register(service, [ready(store)])
    monkeypatch.setattr(brd.OAuthConnectionRepository, "list_all", lambda _: [])
    assert service.process("four-finance")["status"] == "blocked_user_action"
    assert (store.settings.data_dir / "production-batch-results/four-finance/email.txt").is_file()
    assert gmail.drafts == gmail.sends == 0


@pytest.mark.parametrize("failure", ["timeout", "wrong_recipient", "missing_sent"])
def test_ambiguous_send_or_bad_readback_is_not_sent_and_never_retried(delivery, failure):
    service, gmail, store = delivery
    register(service, [ready(store)])
    setattr(gmail, failure, True)
    assert service.process("four-finance")["status"] == "insufficient_evidence"
    setattr(gmail, failure, False)
    assert service.process("four-finance")["status"] == "insufficient_evidence"
    assert gmail.sends == 1


def test_restart_in_middle_of_send_does_not_repeat_it(delivery):
    service, gmail, store = delivery
    register(service, [ready(store)])
    with connect(store.settings) as db:
        db.execute("UPDATE production_batch_deliveries SET status='sending'")
        db.commit()
    assert service.process("four-finance")["status"] == "insufficient_evidence"
    assert gmail.sends == 0


def test_tampered_ready_video_is_not_emailed_as_approved(delivery):
    service, gmail, store = delivery
    job = ready(store)
    register(service, [job])
    (store.context(job).artifact_dir / "video.mp4").write_text("corrupted after QA")
    assert service.process("four-finance")["status"] == "sent"
    assert "Status: insufficient_evidence" in gmail.payload["body"]
    assert "Status: ready" not in gmail.payload["body"]
    assert [p.name for p in gmail.payload["attachments"]] == ["results.json"]


def test_reports_blocked_orders_honestly_and_never_attaches_unfinished_media(delivery):
    service, gmail, store = delivery
    job = create(store)["id"]
    with store.lease(job) as token:
        store.fail(job, token, "preflight", category="dependency_unavailable", message="test missing dependency")
    register(service, [job])
    assert service.process("four-finance")["status"] == "sent"
    assert "Status: blocked" in gmail.payload["body"]
    assert "Status: ready" not in gmail.payload["body"]


def test_live_delivery_lease_cannot_be_stolen(delivery):
    service, gmail, store = delivery
    register(service, [ready(store)])
    with service._lease("four-finance"):
        with pytest.raises(RuntimeError, match="Another worker"):
            service.process("four-finance")
    assert gmail.sends == 0


def test_attachment_budget_is_enforced(delivery, monkeypatch):
    service, gmail, store = delivery
    register(service, [ready(store)])
    monkeypatch.setattr(brd, "MAX_ATTACHMENTS_BYTES", 65536)
    assert service.process("four-finance")["status"] == "sent"
    assert sum(p.stat().st_size for p in gmail.payload["attachments"]) <= 65536
    assert "budzet zalacznikow" in gmail.payload["body"]


def register_complete(service, store, jobs):
    review = store.settings.data_dir / "quality-review.md"
    review.write_text("Ocena wygladu wykonana. Odsluch nie zostal wykonany. Brak akceptacji glosu przez uzytkownika.", encoding="utf-8")
    return service.register("four-complete", "account-a2dafda4@example.invalid", jobs,
                            approved=True, require_all_ready=True, quality_report=review)


def test_complete_delivery_requires_an_immutable_saved_review(delivery):
    service, gmail, store = delivery
    jobs = [ready(store)]
    with pytest.raises(ValueError, match="saved quality report"):
        service.register("four-complete", "account-a2dafda4@example.invalid", jobs, approved=True, require_all_ready=True)
    first = register_complete(service, store, jobs)
    with pytest.raises(ValueError, match="different inputs"):
        service.register("four-complete", "account-a2dafda4@example.invalid", jobs, approved=True)
    assert first["require_all_ready"] == 1
    assert gmail.sends == 0


def test_complete_delivery_waits_on_blocked_order_and_sends_no_failure_report(delivery):
    service, gmail, store = delivery
    job = create(store)["id"]
    with store.lease(job) as token:
        store.fail(job, token, "preflight", category="dependency_unavailable", message="not ready")
    register_complete(service, store, [ready(store), job])
    assert service.process("four-complete")["status"] == "waiting_production"
    assert gmail.drafts == gmail.sends == 0


def test_complete_delivery_contains_every_video_and_review_and_is_idempotent(delivery):
    service, gmail, store = delivery
    jobs = [ready(store) for _ in range(4)]
    register_complete(service, store, jobs)
    assert service.process("four-complete")["status"] == "sent"
    names = {path.name for path in gmail.payload["attachments"]}
    assert {f"{job}_video.mp4" for job in jobs}.issubset(names)
    assert "quality-review.md" in names
    manifest = next(path for path in gmail.payload["attachments"] if path.name == "results.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["quality_review"]["sha256"]
    assert payload["require_all_ready"] is True
    assert len(payload["jobs"]) == 4
    assert service.process("four-complete")["status"] == "sent"
    assert gmail.sends == gmail.drafts == 1


def test_complete_delivery_never_sends_tampered_ready_video(delivery):
    service, gmail, store = delivery
    job = ready(store)
    register_complete(service, store, [job])
    (store.context(job).artifact_dir / "video.mp4").write_text("tampered after QA")
    assert service.process("four-complete")["status"] == "blocked_technical"
    assert gmail.drafts == gmail.sends == 0


def test_complete_delivery_never_drops_videos_to_meet_email_limit(delivery, monkeypatch):
    service, gmail, store = delivery
    register_complete(service, store, [ready(store) for _ in range(4)])
    monkeypatch.setattr(brd, "MAX_ATTACHMENTS_BYTES", 65536)
    result = service.process("four-complete")
    assert result["status"] == "blocked_technical"
    assert "split this batch" in result["error"]
    assert gmail.drafts == gmail.sends == 0


def test_complete_delivery_detects_changed_quality_review_after_draft(delivery):
    service, gmail, store = delivery
    register_complete(service, store, [ready(store)])
    report = service.report(service.status("four-complete"))
    with connect(store.settings) as db:
        db.execute("UPDATE production_batch_deliveries SET status='drafted', draft_id='draft-one', report_hash=? WHERE batch_id='four-complete'", (report["hash"],))
        db.commit()
    (store.settings.data_dir / "quality-review.md").write_text("Changed review after draft", encoding="utf-8")
    assert service.process("four-complete")["status"] == "blocked_technical"
    assert gmail.drafts == gmail.sends == 0
