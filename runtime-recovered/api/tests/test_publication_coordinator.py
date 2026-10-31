from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from atnos.intelligence.storage import connect
from atnos.production import publication_coordinator as pc
from atnos.production.order_store import REQUIRED_GATE_STAGES, checkpoint_fingerprints, file_hash
from test_production_order_store import store, create
from test_production_order_completion import stage

CHANNEL = "UC" + "x" * 22


def ready(store, *, delivery_mode="project_policy"):
    from atnos.production.project_profile import ProductionOrder
    job = store.create(ProductionOrder(topic="Build your first savings", direction="Practical and confident", delivery_mode=delivery_mode))["id"]
    with store.lease(job) as token:
        for name in REQUIRED_GATE_STAGES.values():
            if name != "package":
                stage(store, job, token, name)
        root = store.context(job).artifact_dir
        files = {}
        for key, ext in (("video", "mp4"), ("landscape_thumbnail", "png"), ("captions", "srt"), ("metadata", "json")):
            path = root / f"{key}.{ext}"
            path.write_text(json.dumps({"title": "Test savings title", "description": "Test description"}) if key == "metadata" else "test fixture, not a publishable media file")
            files[key] = str(path)
        rows = store.get(job)["checkpoints"]
        evidence = [root / f["path"] for row in rows for f in row["files"]] + [Path(p) for p in files.values()]
        manifest = root / "manifest.json"
        payload = {"qa": {"status": "passed"}, "files": [{"path": p.relative_to(root).as_posix(), "sha256": file_hash(p)} for p in evidence],
                   "deliverables": files, "checkpoint_fingerprints": checkpoint_fingerprints(rows), "output_file": str(manifest)}
        manifest.write_text(json.dumps(payload))
        store.begin(job, token, "package", {})
        store.complete(job, token, "package", payload, [manifest, *evidence])
        store.finish(job, token, manifest)
    return job


class FakeYouTube:
    def __init__(self):
        self.uploads = self.schedules = self.thumbnails = 0
        self.channel = CHANNEL
        self.video = {"found": True, "channel_id": CHANNEL, "privacy_status": "private", "upload_status": "processed", "processing_status": "succeeded"}
        self.ambiguous = self.ignore_schedule = self.schedule_timeout = self.oauth_expired = False

    def identity(self):
        if self.oauth_expired:
            raise RuntimeError("invalid_grant")
        return {"channel_id": self.channel}

    def upload(self, package, received):
        self.uploads += 1
        if self.ambiguous:
            raise TimeoutError("Response lost after insert")
        received("testvideo01")

    def inspect(self, video_id):
        return {"video_id": video_id, **self.video}

    def thumbnail(self, video_id, path):
        self.thumbnails += 1

    def schedule(self, video_id, publish_at):
        self.schedules += 1
        if not self.ignore_schedule:
            self.video["publish_at"] = publish_at
        if self.schedule_timeout:
            raise TimeoutError("Response lost after update")
        return {"status": "scheduled_public"}


@pytest.fixture
def system(store, monkeypatch):
    transport = FakeYouTube()
    @contextmanager
    def factory(self, project):
        yield transport
    monkeypatch.setattr(pc.PublicationCoordinator, "transport", factory)
    now = datetime.now(UTC) - timedelta(seconds=2)
    service = pc.PublicationCoordinator(store.settings, None, clock=lambda: now)
    service.configure(pc.PublicationPolicy(enabled=True, channel_id=CHANNEL))
    return service, transport, store


def test_complete_workflow_is_durable_scheduled_then_public_not_just_200(system):
    service, youtube, store = system
    job = ready(store)
    queued = service.enqueue(job)
    assert service.enqueue(job) == queued
    assert service.process(job)["status"] == "scheduled"
    restarted = pc.PublicationCoordinator(store.settings, None)
    assert restarted.process(job)["status"] == "scheduled"
    youtube.video["privacy_status"] = "public"
    assert restarted.process(job)["status"] == "published"
    assert youtube.uploads == youtube.schedules == youtube.thumbnails == 1
    assert store.get(job)["status"] == "ready"


@pytest.mark.parametrize("already_queued", [False, True])
def test_local_only_intake_never_reaches_youtube(system, already_queued):
    service, youtube, store = system
    job = ready(store)
    if already_queued:
        service.enqueue(job)
    with connect(store.settings) as db:
        row = db.execute("SELECT request_json FROM pipeline_job_orders WHERE job_id=?", (job,)).fetchone()
        request = json.loads(row[0])
        request["intake"] = {"delivery_mode": "local_only"}
        db.execute("UPDATE pipeline_job_orders SET request_json=? WHERE job_id=?", (json.dumps(request), job))
        db.commit()
    if already_queued:
        assert service.process(job)["status"] == "blocked_user_action"
    else:
        with pytest.raises(pc.PublicationBlocked, match="lokalnego odbioru"):
            service.enqueue(job)
        assert service.tick() == []
    assert youtube.uploads == youtube.schedules == youtube.thumbnails == 0


@pytest.mark.parametrize("defect", ["unfinished", "file_changed", "qa_failed"])
def test_no_enqueue_without_current_qa_and_artifacts(system, defect):
    service, youtube, store = system
    job = create(store)["id"] if defect == "unfinished" else ready(store)
    if defect == "file_changed":
        (store.context(job).artifact_dir / "video.mp4").write_text("changed")
    if defect == "qa_failed":
        with connect(store.settings) as db:
            db.execute("UPDATE pipeline_stage_checkpoints SET output_json='{}' WHERE job_id=? AND stage='semantic_qa'", (job,))
            db.commit()
    with pytest.raises(pc.PublicationBlocked):
        service.enqueue(job)
    assert youtube.uploads == 0


@pytest.mark.parametrize("defect", ["changed_after_queue", "wrong_oauth_channel", "wrong_uploaded_channel", "paused", "learning_mode"])
def test_safety_blockers_stop_external_publication(system, defect):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    if defect == "changed_after_queue":
        (store.context(job).artifact_dir / "video.mp4").write_text("changed")
    elif defect == "wrong_oauth_channel":
        youtube.channel = "UC" + "y" * 22
    elif defect == "wrong_uploaded_channel":
        youtube.video["channel_id"] = "UC" + "y" * 22
    elif defect == "paused":
        service.configure(pc.PublicationPolicy(enabled=False, channel_id=CHANNEL))
    else:
        service.settings = service.settings.model_copy(update={"learning_mode": True, "youtube_write_block": True})
    assert service.process(job)["status"] == "blocked_user_action"
    assert youtube.schedules == 0
    assert youtube.uploads == (1 if defect == "wrong_uploaded_channel" else 0)


def test_ambiguous_insert_is_never_automatically_retried(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    youtube.ambiguous = True
    assert service.process(job)["status"] == "insufficient_evidence"
    youtube.ambiguous = False
    assert service.process(job)["status"] == "insufficient_evidence"
    assert youtube.uploads == 1


def test_interrupted_upload_is_not_repeated_after_process_restart(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    with connect(store.settings) as db:
        db.execute("UPDATE production_publication_queue SET status='uploading' WHERE job_id=?", (job,))
        db.commit()
    assert service.process(job)["status"] == "insufficient_evidence"
    assert youtube.uploads == 0


def test_schedule_timeout_reconciles_without_second_insert_or_update(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    youtube.schedule_timeout = True
    assert service.process(job)["status"] == "retry_wait"
    youtube.schedule_timeout = False
    assert service.process(job)["status"] == "scheduled"
    assert youtube.uploads == youtube.schedules == 1


def test_schedule_success_response_without_readback_is_not_verified(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    youtube.ignore_schedule = True
    result = service.process(job)
    assert result["status"] == "blocked_user_action"
    assert "nie potwierdzil" in result["error"]


def test_two_orders_get_different_slots_and_old_orders_are_excluded(system):
    service, _, store = system
    one, two = ready(store), ready(store)
    assert service.enqueue(one)["publish_at"] != service.enqueue(two)["publish_at"]
    old = ready(store)
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_jobs SET created_at='2000-01-01' WHERE id=?", (old,))
        db.commit()
    with pytest.raises(pc.PublicationBlocked, match="Stare"):
        service.enqueue(old)


@pytest.mark.parametrize("offset,minutes,allowed", [(2, -10, False), (-5, 10, True)])
def test_consent_cutoff_compares_instants_not_timezone_text(system, offset, minutes, allowed):
    service, youtube, store = system
    job = ready(store)
    created = (service.clock() + timedelta(minutes=minutes)).astimezone(timezone(timedelta(hours=offset)))
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_jobs SET created_at=? WHERE id=?", (created.isoformat(), job))
        db.commit()
    if allowed:
        assert [item["job_id"] for item in service.tick()] == [job]
        assert youtube.uploads == 1
    else:
        with pytest.raises(pc.PublicationBlocked, match="Stare"):
            service.enqueue(job)
        assert service.tick() == []
        assert youtube.uploads == 0


def test_lease_prevents_two_publishers(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    with service._lease(job):
        with pytest.raises(pc.PublicationBlocked, match="Inny"):
            service.process(job)
    assert youtube.uploads == 0


def test_expired_oauth_is_bounded_and_creates_no_upload(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    youtube.oauth_expired = True
    for _ in range(5):
        result = service.process(job)
    assert result["status"] == "blocked_technical"
    assert youtube.uploads == 0


def test_policy_api_requires_explicit_approval_and_preserves_intent(system):
    from atnos.api.routes.publication_coordinator import router
    service, _, store = system
    app = FastAPI()
    import time
    app.state.settings, app.state.database = store.settings.model_copy(update={
        "local_host_capability_token": "test-publication-session", "local_host_capability_expires_at": int(time.time()) + 600}), None
    app.include_router(router)
    with TestClient(app) as client:
        payload = {"policy": {"enabled": True, "channel_id": CHANNEL}}
        assert client.put("/api/production/publication/policy", json=payload).status_code == 401
        client.headers["X-Atnos-Local-Capability"] = "test-publication-session"
        assert client.put("/api/production/publication/policy", json=payload).status_code == 409
        payload["approve_scheduled_publication"] = True
        assert client.put("/api/production/publication/policy", json=payload).status_code == 200
        assert client.get("/api/production/publication").json()["policy"]["enabled"]
        assert client.post("/api/production/publication/verify").json()["status"] == "verified"
        payload["policy"]["times_utc"] = ["25:80"]
        assert client.put("/api/production/publication/policy", json=payload).status_code == 422


def test_tick_discovers_ready_orders_and_leaves_unready_orders_alone(system):
    service, youtube, store = system
    job, unfinished = ready(store), create(store)["id"]
    results = service.tick()
    assert [r["job_id"] for r in results] == [job]
    assert results[0]["status"] == "scheduled"
    assert not service.status(unfinished)["jobs"]
    assert youtube.uploads == 1


def test_local_only_order_cannot_be_queued_or_auto_published(system):
    service, youtube, store = system
    job = ready(store, delivery_mode="local_only")
    assert store.get(job)["request"]["delivery_mode"] == "local_only"
    with pytest.raises(pc.PublicationBlocked, match="lokalnego"):
        service.enqueue(job)
    assert service.tick() == []
    assert youtube.uploads == youtube.schedules == youtube.thumbnails == 0


def test_local_only_guard_blocks_stale_queue_before_any_transport(system):
    service, youtube, store = system
    job = ready(store, delivery_mode="local_only")
    with pytest.raises(pc.PublicationBlocked, match="lokalnego"):
        service._authorize_write({"job_id": job})
    assert youtube.uploads == youtube.schedules == youtube.thumbnails == 0


def test_removed_remote_schedule_is_not_silently_recreated(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    assert service.process(job)["status"] == "scheduled"
    youtube.video.pop("publish_at")
    assert service.process(job)["status"] == "blocked_user_action"
    assert youtube.schedules == 1


def test_schedule_uses_utc_and_never_publishes_immediately_for_missed_slot():
    now = datetime(2026, 9, 6, 20, 1, tzinfo=UTC)
    assert pc.next_slot(pc.PublicationPolicy(), now, set()) == '2026-09-07T14:00:00+00:00'
    assert pc.next_slot(pc.PublicationPolicy(times_utc=['14:00']), now, {'2026-09-07T14:00:00+00:00'}) == '2026-09-08T14:00:00+00:00'


def test_initial_disabled_configuration_does_not_authorize_earlier_orders(store):
    start = datetime.now(UTC)
    service = pc.PublicationCoordinator(store.settings, None, clock=lambda: start)
    service.configure(pc.PublicationPolicy(enabled=False))
    job = ready(store)
    service.clock = lambda: start + timedelta(days=1)
    service.configure(pc.PublicationPolicy(enabled=True, channel_id=CHANNEL))
    with pytest.raises(pc.PublicationBlocked, match="Stare"):
        service.enqueue(job)


def test_pause_during_private_upload_prevents_public_schedule(system):
    service, youtube, store = system
    job = ready(store)
    service.enqueue(job)
    original = youtube.upload
    def upload(package, received):
        original(package, received)
        service.configure(pc.PublicationPolicy(enabled=False, channel_id=CHANNEL))
    youtube.upload = upload
    assert service.process(job)["status"] == "blocked_user_action"
    assert youtube.uploads == 1
    assert youtube.schedules == youtube.thumbnails == 0


@pytest.mark.parametrize('fail', [False, True])
def test_real_transport_uses_one_insert_and_persists_id_before_followup(tmp_path, monkeypatch, fail):
    from io import BytesIO
    from types import SimpleNamespace
    import googleapiclient.http
    import youtube.upload_guard
    stream, requests, chunks, received = BytesIO(b'video fixture'), [], [], []
    monkeypatch.setattr(googleapiclient.http, 'MediaFileUpload', lambda *a, **kw: SimpleNamespace(stream=lambda: stream))
    monkeypatch.setattr(youtube.upload_guard, 'assert_upload_video_file', lambda path, **kw: path)
    def chunk(**kwargs):
        chunks.append(kwargs)
        if fail:
            raise TimeoutError('Unknown insert result')
        return None, {'id': 'testvideo01'}
    def insert(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(next_chunk=chunk)
    api = SimpleNamespace(videos=lambda: SimpleNamespace(insert=insert))
    transport = object.__new__(pc.YouTubePublicationTransport)
    transport.connector = SimpleNamespace(_youtube_client=lambda: api, VIDEO_UPLOAD_CHUNK_SIZE_BYTES=1024,
        settings=SimpleNamespace(youtube_category_id='27'))
    package = {'files': {'video': tmp_path / 'test.mp4'}, 'metadata': {'title': 'Test', 'description': 'Verified test'}}
    if fail:
        with pytest.raises(TimeoutError):
            transport.upload(package, received.append)
        assert received == []
    else:
        transport.upload(package, received.append)
        assert received == ['testvideo01']
    assert len(requests) == len(chunks) == 1
    assert chunks[0]['num_retries'] == 0
    assert requests[0]['body']['status']['privacyStatus'] == 'private'
    assert stream.closed


def test_connector_readback_exposes_channel_and_actual_schedule():
    from types import SimpleNamespace
    from atnos.services.youtube_connector_service import YouTubeConnectorService
    response = {'items': [{'snippet': {'channelId': CHANNEL, 'title': 'Test'},
        'status': {'privacyStatus': 'private', 'publishAt': '2026-09-07T14:00:00Z', 'uploadStatus': 'processed'},
        'processingDetails': {'processingStatus': 'succeeded'}}]}
    connector = object.__new__(YouTubeConnectorService)
    connector._youtube_client = lambda: SimpleNamespace(videos=lambda: SimpleNamespace(list=lambda **kw: SimpleNamespace(execute=lambda: response)))
    result = connector.fetch_video_status('testvideo01')
    assert result['channel_id'] == CHANNEL
    assert result['publish_at'] == '2026-09-07T14:00:00Z'
    assert result['processing_status'] == 'succeeded'
