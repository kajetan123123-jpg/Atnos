"""Durable scheduled publication for verified production orders, not legacy jobs."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps
from atnos.production.order_store import ProductionOrderStore, REQUIRED_GATE_STAGES, _event, _same_process, checkpoint_fingerprints, file_hash
from atnos.production.project_profile import load_project_profile
from atnos.production.oauth_resolver import resolve_youtube_oauth_account_id
from atnos.services.learning_mode_guard import learning_mode_write_block_active
from atnos.services.youtube_connector_service import YouTubeConnectorService


class PublicationBlocked(ValueError):
    pass


class PublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    channel_id: str | None = Field(default=None, pattern=r"^UC[A-Za-z0-9_-]{22}$")
    times_utc: list[str] = Field(default_factory=lambda: ["14:00", "20:00"], min_length=1, max_length=2)

    @field_validator("times_utc")
    @classmethod
    def valid_times(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Godziny publikacji nie moga sie powtarzac.")
        for value in values:
            if len(value) != 5 or datetime.strptime(value, "%H:%M").strftime("%H:%M") != value:
                raise ValueError("Godziny wymagaja formatu HH:MM (UTC).")
        return sorted(values)


def next_slot(policy: PublicationPolicy, now: datetime, occupied: set[str]) -> str:
    earliest = now.astimezone(UTC) + timedelta(minutes=15)
    for day in range(366):
        for clock in policy.times_utc:
            hour, minute = map(int, clock.split(":"))
            slot = (earliest + timedelta(days=day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if slot > earliest and slot.isoformat() not in occupied:
                return slot.isoformat()
    raise PublicationBlocked("Brak wolnego terminu publikacji.")


def verified_package(store: ProductionOrderStore, job_id: str, *, pending_renderer_registration: tuple[str,str] | None = None) -> dict:
    state = store.get(job_id)
    pending_stage=None
    if pending_renderer_registration is not None:
        pending_stage,expected_input=pending_renderer_registration
        row=next((r for r in state['checkpoints'] if r['stage']==pending_stage),{})
        error=json.loads(row.get('error_json') or '{}')
        if (state['status']!='blocked' or state['current_step']!=pending_stage
                or state['request'].get('delivery_mode')!='local_only'
                or pending_stage!='remotion_refresh/'+expected_input[:12]
                or not re.fullmatch('[0-9a-f]{64}',expected_input)
                or row.get('status')!='failed' or row.get('input_hash')!=expected_input
                or error.get('category')!='dependency_failure'):
            raise PublicationBlocked('Pending renderer registration does not match its failed archive operation')
    if (state["status"] != "ready" and pending_stage is None) or state["worker_live"]:
        raise PublicationBlocked("Rolka nie zakonczyla produkcji i kontroli jakosci.")
    rows = {row["stage"]: row for row in state["checkpoints"]}
    root = store.context(job_id).artifact_dir
    for stage in REQUIRED_GATE_STAGES.values():
        row = rows.get(stage, {})
        verdict = row.get("output", {})
        if (row.get("status") != "done" or verdict.get("qa", verdict).get("status") != "passed"
                or not store._valid_files(root, row.get("files_json", "[]"))):
            raise PublicationBlocked("Niepelne lub zmienione dowody kontroli jakosci: " + stage)
    package = rows["package"]["output"]
    package_rows=[r for r in state['checkpoints'] if r['stage']!=pending_stage]
    if package.get("checkpoint_fingerprints") != checkpoint_fingerprints(package_rows):
        raise PublicationBlocked("Pakiet nie odpowiada aktualnym etapom produkcji.")
    entries = {entry["path"]: entry["sha256"] for entry in package["files"]}
    files = {}
    for key in ("video", "landscape_thumbnail", "captions", "metadata"):
        path = Path(package["deliverables"][key]).resolve()
        if not path.is_relative_to(root) or entries.get(path.relative_to(root).as_posix()) != file_hash(path):
            raise PublicationBlocked("Plik pakietu zostal zmieniony: " + key)
        files[key] = path
    metadata = json.loads(files["metadata"].read_text(encoding="utf-8"))
    if not 1 <= len(metadata.get("title", "")) <= 100 or not 1 <= len(metadata.get("description", "")) <= 5000:
        raise PublicationBlocked("Tytul lub opis nie spelnia wymagan YouTube.")
    return {"state": state, "files": files, "metadata": metadata,
            "hash": file_hash(Path(package["output_file"]))}


class YouTubePublicationTransport:
    """Uses the existing OAuth connector; never restarts an ambiguous video insert."""

    def __init__(self, session, settings, account_id):
        if not account_id:
            raise PublicationBlocked("Brak przypisanego konta OAuth. Polacz YouTube w Ustawieniach.")
        self.connector = YouTubeConnectorService(session, settings, account_id=account_id)

    def identity(self):
        status = self.connector.get_status()
        if not status.ready:
            raise PublicationBlocked("; ".join(status.warnings) or "Polacz ponownie YouTube w Ustawieniach.")
        return self.connector.get_owned_channel()

    def inspect(self, video_id):
        return self.connector.fetch_video_status(video_id)

    def upload(self, package, received):
        from googleapiclient.http import MediaFileUpload
        from youtube.upload_guard import assert_upload_video_file
        video = assert_upload_video_file(package["files"]["video"], settings=self.connector.settings, require_run_artifacts=False)
        metadata = package["metadata"]
        media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True,
                                chunksize=self.connector.VIDEO_UPLOAD_CHUNK_SIZE_BYTES)
        try:
            request = self.connector._youtube_client().videos().insert(part="snippet,status", body={
                "snippet": {"title": metadata["title"], "description": metadata["description"],
                    "tags": metadata.get("tags", [])[:15], "categoryId": self.connector.settings.youtube_category_id,
                    "defaultLanguage": metadata.get("language", "en"), "defaultAudioLanguage": metadata.get("language", "en")},
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk(num_retries=0)
            identity = str(response.get("id") or "")
            if not identity:
                raise PublicationBlocked("YouTube nie zwrocil identyfikatora filmu. Nie ponawiaj uploadu.")
            received(identity)
        finally:
            media.stream().close()

    def thumbnail(self, video_id, path):
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(path), mimetype="image/png")
        try:
            self.connector._youtube_client().thumbnails().set(videoId=video_id, media_body=media).execute()
        finally:
            media.stream().close()

    def schedule(self, video_id, publish_at):
        return self.connector.promote_private_video(video_id=video_id, approved=True, publish_at=publish_at)


class PublicationCoordinator:
    def __init__(self, settings, database, *, transport_factory=YouTubePublicationTransport, clock=None):
        self.settings, self.database = settings, database
        self.store = ProductionOrderStore(settings)
        self.transport_factory = transport_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def policy(self, project="wealth_blueprint"):
        load_project_profile(self.settings, project)
        with connect(self.settings) as db:
            row = db.execute("SELECT * FROM production_publication_policies WHERE project=?", (project,)).fetchone()
        payload = json.loads(row["policy_json"]) if row else PublicationPolicy().model_dump()
        return {**payload, "mode": "scheduled_public", "authorized_after": row["authorized_after"] if row else None}

    def configure(self, policy: PublicationPolicy, project="wealth_blueprint"):
        load_project_profile(self.settings, project)
        now = self.clock().astimezone(UTC).isoformat()
        with connect(self.settings) as db:
            db.execute("INSERT INTO production_publication_policies VALUES(?,?,?,?) ON CONFLICT(project) DO UPDATE SET policy_json=excluded.policy_json,authorized_after=CASE WHEN production_publication_policies.authorized_after='' THEN excluded.authorized_after ELSE production_publication_policies.authorized_after END,updated_at=excluded.updated_at",
                       (project, policy.model_dump_json(), now if policy.enabled else "", now))
            db.commit()
        return self.policy(project)

    @contextmanager
    def transport(self, project):
        account = load_project_profile(self.settings, project)["account_id"]
        with self.database.session_scope() as session:
            oauth_id = resolve_youtube_oauth_account_id(self.settings, account, session=session)
            # Public permission is scoped to this explicit policy, never to legacy jobs.
            scoped = self.settings.model_copy(update={"allow_public_upload": True})
            yield self.transport_factory(session, scoped, oauth_id)

    def verify_connection(self, project="wealth_blueprint"):
        policy = self.policy(project)
        try:
            with self.transport(project) as transport:
                observed = transport.identity()
            if not policy["channel_id"]:
                return {"status": "partially_verified", "channel_id": observed["channel_id"], "title": observed.get("title"),
                        "message": "Odczytano kanal z przypisanego OAuth. Potwierdz go przez zapisanie ustawien publikacji."}
            if observed["channel_id"] != policy["channel_id"]:
                raise PublicationBlocked("Polaczone konto wskazuje inny kanal. Publikacja zostala zablokowana.")
            return {"status": "verified", "channel_id": observed["channel_id"], "title": observed.get("title"),
                    "message": "Dostep do kanalu potwierdzony. Publiczna publikacja wymaga osobnego potwierdzenia YouTube."}
        except Exception as exc:
            return {"status": "blocked_user_action", "message": str(redact_secrets(str(exc)))[:1000]}

    def status(self, job_id=None):
        with connect(self.settings) as db:
            rows = db.execute("SELECT * FROM production_publication_queue WHERE (? IS NULL OR job_id=?) ORDER BY created_at DESC LIMIT 30", (job_id, job_id)).fetchall()
        jobs = []
        for row in rows:
            item = {key: row[key] for key in ("job_id", "status", "publish_at", "channel_id", "video_id", "error", "attempts")}
            item["evidence"] = json.loads(row["evidence_json"])
            item["youtube_url"] = f"https://www.youtube.com/watch?v={row['video_id']}" if row["video_id"] else None
            jobs.append(item)
        return {"policy": self.policy(), "jobs": jobs, "write_blocked": learning_mode_write_block_active(self.settings)}

    def enqueue(self, job_id):
        self._require_publication_scope(job_id)
        package = verified_package(self.store, job_id)
        project = package["state"]["request"]["project"]
        saved = self.policy(project)
        policy = PublicationPolicy.model_validate({k: saved[k] for k in PublicationPolicy.model_fields})
        if not policy.enabled or not policy.channel_id:
            raise PublicationBlocked("Publikacja jest wstrzymana lub brak docelowego kanalu.")
        # ISO text ordering is unsafe when imported timestamps use different offsets.
        created = datetime.fromisoformat(package["state"]["created_at"])
        cutoff = datetime.fromisoformat(saved["authorized_after"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        if created < cutoff:
            raise PublicationBlocked("Stare produkcje nie sa automatycznie objete nowa zgoda na publikacje.")
        now = self.clock()
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT job_id FROM production_publication_queue WHERE job_id=?", (job_id,)).fetchone()
            if not previous:
                occupied = {row[0] for row in db.execute("SELECT publish_at FROM production_publication_queue WHERE channel_id=?", (policy.channel_id,))}
                slot = next_slot(policy, now, occupied)
                db.execute("INSERT INTO production_publication_queue(job_id,project,channel_id,publish_at,status,package_hash,next_check_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, project, policy.channel_id, slot, "queued", package["hash"], now.isoformat(), now.isoformat(), now.isoformat()))
                _event(db, job_id, "publication", "publication_queued", {"channel_id": policy.channel_id, "publish_at": slot})
            db.commit()
        return self.status(job_id)["jobs"][0]

    def _save(self, job_id, token, status, *, evidence=None, error=None, video_id=None):
        now = self.clock()
        with connect(self.settings) as db:
            next_check = now + timedelta(minutes=5)
            if status == "scheduled":
                row = db.execute("SELECT publish_at FROM production_publication_queue WHERE job_id=?", (job_id,)).fetchone()
                next_check = max(next_check, datetime.fromisoformat(row[0]) + timedelta(minutes=1))
            db.execute("UPDATE production_publication_queue SET status=?,evidence_json=COALESCE(?,evidence_json),error=?,video_id=COALESCE(?,video_id),next_check_at=?,updated_at=? WHERE job_id=? AND lease_token=?",
                       (status, json_dumps(evidence) if evidence is not None else None, error, video_id,
                        next_check.isoformat(), now.isoformat(), job_id, token))
            _event(db, job_id, "publication", status, {"error": error, "video_id": video_id})
            db.commit()

    @contextmanager
    def _lease(self, job_id):
        token = uuid4().hex
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM production_publication_queue WHERE job_id=?", (job_id,)).fetchone()
            if not row or _same_process(row["lease_pid"], row["lease_started"]):
                raise PublicationBlocked("Inny wykonawca obsluguje publikacje.")
            db.execute("UPDATE production_publication_queue SET lease_token=?,lease_pid=?,lease_started=? WHERE job_id=?",
                       (token, os.getpid(), psutil.Process().create_time(), job_id))
            db.commit()
        try:
            yield dict(row), token
        finally:
            with connect(self.settings) as db:
                db.execute("UPDATE production_publication_queue SET lease_token=NULL,lease_pid=NULL,lease_started=NULL WHERE job_id=? AND lease_token=?", (job_id, token))
                db.commit()

    def process(self, job_id):
        with self._lease(job_id) as (row, token):
            with connect(self.settings) as db:
                db.execute("UPDATE production_publication_queue SET attempts=attempts+1 WHERE job_id=? AND lease_token=?", (job_id, token))
                db.commit()
            try:
                self._process(row, token)
            except Exception as exc:
                with connect(self.settings) as db:
                    current = db.execute("SELECT video_id,status,attempts FROM production_publication_queue WHERE job_id=?", (job_id,)).fetchone()
                ambiguous = current["status"] == "uploading" and not current["video_id"]
                status = "insufficient_evidence" if ambiguous else "blocked_user_action" if isinstance(exc, PublicationBlocked) else "retry_wait"
                if current["attempts"] >= 5 and status == "retry_wait":
                    status = "blocked_technical"
                self._save(job_id, token, status, error=str(redact_secrets(str(exc)))[:1000])
        return self.status(job_id)["jobs"][0]

    def _process(self, row, token):
        job_id = row["job_id"]
        if row["status"] in {"published", "cancelled", "insufficient_evidence", "blocked_technical"}:
            return
        self._require_publication_scope(job_id)
        policy = self.policy(row["project"])
        if not policy["enabled"] or learning_mode_write_block_active(self.settings):
            raise PublicationBlocked("Publikacja wstrzymana przez ustawienia.")
        if policy["channel_id"] != row["channel_id"]:
            raise PublicationBlocked("Docelowy kanal zmienil sie od utworzenia kolejki.")
        if row["status"] == "uploading" and not row["video_id"]:
            self._save(job_id, token, "insufficient_evidence", error="Przerwany upload ma nieznany wynik. Sprawdz YouTube Studio; nie wysylam duplikatu.")
            return
        package = verified_package(self.store, job_id)
        if package["hash"] != row["package_hash"]:
            raise PublicationBlocked("Pakiet zmienil sie od zaplanowania. Nie publikuje zmienionego pliku.")
        with self.transport(row["project"]) as transport:
            if transport.identity()["channel_id"] != row["channel_id"]:
                raise PublicationBlocked("OAuth wskazuje inny kanal niz zatwierdzony cel.")
            video_id = row["video_id"]
            if not video_id:
                self._authorize_write(row)
                self._save(job_id, token, "uploading")
                def received(identity):
                    nonlocal video_id
                    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
                        raise PublicationBlocked("YouTube zwrocil nieprawidlowy identyfikator filmu.")
                    video_id = identity
                    self._save(job_id, token, "uploaded_private", video_id=identity)
                transport.upload(package, received)
                if not video_id:
                    raise RuntimeError("Brak potwierdzenia identyfikatora uploadu.")
            observed = transport.inspect(video_id)
            if not observed.get("found") or observed.get("channel_id") != row["channel_id"]:
                raise PublicationBlocked("YouTube nie potwierdzil filmu na docelowym kanale.")
            if observed.get("upload_status") in {"rejected", "failed", "deleted"} or observed.get("processing_status") in {"failed", "terminated"}:
                raise PublicationBlocked("YouTube odrzucil lub nie przetworzyl filmu.")
            if observed.get("privacy_status") == "public":
                if row["status"] not in {"scheduled", "scheduling", "retry_wait"}:
                    raise PublicationBlocked("Film zostal upubliczniony poza zaplanowanym przebiegiem.")
                self._save(job_id, token, "published", evidence=observed)
                return
            if observed.get("publish_at"):
                if observed.get("privacy_status") != "private":
                    raise PublicationBlocked("YouTube nie potwierdzil prywatnego filmu oczekujacego na publikacje.")
                if datetime.fromisoformat(observed["publish_at"].replace("Z", "+00:00")) != datetime.fromisoformat(row["publish_at"]):
                    raise PublicationBlocked("YouTube ma inna date publikacji. Nie nadpisuje jej automatycznie.")
                self._save(job_id, token, "scheduled", evidence=observed)
                return
            if row["status"] == "scheduled":
                raise PublicationBlocked("Harmonogram w YouTube zostal usuniety. Nie przywracam go bez decyzji uzytkownika.")
            if observed.get("processing_status") != "succeeded":
                self._save(job_id, token, "processing", evidence=observed)
                return
            if datetime.fromisoformat(row["publish_at"]) <= self.clock() + timedelta(minutes=15):
                saved = PublicationPolicy.model_validate({k: policy[k] for k in PublicationPolicy.model_fields})
                with connect(self.settings) as db:
                    db.execute("BEGIN IMMEDIATE")
                    occupied = {r[0] for r in db.execute("SELECT publish_at FROM production_publication_queue WHERE channel_id=?", (row["channel_id"],))}
                    row["publish_at"] = next_slot(saved, self.clock(), occupied)
                    db.execute("UPDATE production_publication_queue SET publish_at=? WHERE job_id=? AND lease_token=?", (row["publish_at"], job_id, token))
                    db.commit()
            evidence = json.loads(row["evidence_json"])
            if not evidence.get("thumbnail_uploaded"):
                self._authorize_write(row)
                transport.thumbnail(video_id, package["files"]["landscape_thumbnail"])
                evidence["thumbnail_uploaded"] = True
                self._save(job_id, token, "uploaded_private", evidence=evidence)
            self._authorize_write(row)
            self._save(job_id, token, "scheduling", evidence=evidence)
            transport.schedule(video_id, row["publish_at"])
            confirmed = transport.inspect(video_id)
            matches = (confirmed.get("found") and confirmed.get("channel_id") == row["channel_id"]
                       and confirmed.get("privacy_status") == "private" and confirmed.get("publish_at")
                       and datetime.fromisoformat(confirmed["publish_at"].replace("Z", "+00:00")) == datetime.fromisoformat(row["publish_at"]))
            if not matches:
                raise PublicationBlocked("YouTube nie potwierdzil harmonogramu. Sprawdz ograniczenia/audyt API w YouTube Studio.")
            self._save(job_id, token, "scheduled", evidence={**evidence, **confirmed})

    def _require_publication_scope(self, job_id):
        request = self.store.get(job_id)["request"]
        if request.get("delivery_mode") == "local_only" or (request.get("intake") or {}).get("delivery_mode") == "local_only":
            raise PublicationBlocked("Ta rolka jest przeznaczona tylko do lokalnego odbioru, bez publikacji.")

    def _authorize_write(self, row):
        self._require_publication_scope(row["job_id"])
        policy = self.policy(row["project"])
        if not policy["enabled"] or policy["channel_id"] != row["channel_id"] or learning_mode_write_block_active(self.settings):
            raise PublicationBlocked("Zgoda lub docelowy kanal zmienily sie w trakcie operacji. Dalsze dzialania wstrzymane.")

    def tick(self):
        policy = self.policy()
        if not policy["enabled"] or not policy["channel_id"] or learning_mode_write_block_active(self.settings):
            return []
        with connect(self.settings) as db:
            ready = [r[0] for r in db.execute("SELECT j.id FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id LEFT JOIN production_publication_queue q ON q.job_id=j.id WHERE j.status='ready' AND julianday(j.created_at)>=julianday(?) AND q.job_id IS NULL AND json_extract(o.request_json,'$.project')='wealth_blueprint' AND COALESCE(json_extract(o.request_json,'$.delivery_mode'),'')!='local_only' AND COALESCE(json_extract(o.request_json,'$.intake.delivery_mode'),'')!='local_only' ORDER BY julianday(j.created_at) LIMIT 5", (policy["authorized_after"],))]
        for job in ready:
            try:
                self.enqueue(job)
            except (PublicationBlocked, ValueError):
                continue
        with connect(self.settings) as db:
            due = [r[0] for r in db.execute("SELECT job_id FROM production_publication_queue WHERE status IN ('queued','uploading','uploaded_private','processing','scheduling','scheduled','retry_wait') AND next_check_at<=? ORDER BY next_check_at LIMIT 2", (self.clock().isoformat(),))]
        results = []
        for job in due:
            try:
                results.append(self.process(job))
            except PublicationBlocked:
                # Another process may already own this entry; do not starve other entries.
                continue
        return results
