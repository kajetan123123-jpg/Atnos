"""Durable, explicitly authorized delivery of one production batch to its owner."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import getaddresses
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import psutil

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect
from atnos.production.order_store import ProductionOrderStore, _same_process
from atnos.production.project_profile import content_hash
from atnos.production.publication_coordinator import verified_package
from atnos.repositories.oauth_connection_repository import OAuthConnectionRepository
from atnos.services.gmail_connector_service import GmailConnectorService

MAX_ATTACHMENTS_BYTES = 18 * 1024 * 1024
TERMINAL = {"ready", "blocked", "failed", "cancelled"}


class BatchResultDelivery:
    def __init__(self, settings, database, *, gmail_factory=GmailConnectorService):
        self.settings, self.database = settings, database
        self.orders = ProductionOrderStore(settings)
        self.gmail_factory = gmail_factory
        with connect(settings) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS production_batch_deliveries (
                batch_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, jobs_json TEXT NOT NULL,
                status TEXT NOT NULL, authorized_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                gmail_account_id INTEGER, draft_id TEXT, message_id TEXT, report_hash TEXT,
                evidence_json TEXT NOT NULL DEFAULT '{}', error TEXT,
                lease_token TEXT, lease_pid INTEGER, lease_started REAL
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(production_batch_deliveries)")}
            if "require_all_ready" not in columns:
                db.execute("ALTER TABLE production_batch_deliveries ADD COLUMN require_all_ready INTEGER NOT NULL DEFAULT 0")
            if "quality_report_path" not in columns:
                db.execute("ALTER TABLE production_batch_deliveries ADD COLUMN quality_report_path TEXT")
            db.commit()

    def register(self, batch_id: str, recipient: str, job_ids: list[str], *, approved: bool = False,
                 require_all_ready: bool = False, quality_report: Path | str | None = None):
        if not approved:
            raise ValueError("Explicit permission to email these results is required")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", batch_id):
            raise ValueError("Invalid batch identifier")
        if not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", recipient):
            raise ValueError("Invalid recipient")
        if not 1 <= len(job_ids) <= 20 or len(set(job_ids)) != len(job_ids):
            raise ValueError("A batch needs distinct production orders")
        for job in job_ids:
            if self.orders.get(job)["request"]["project"] != "wealth_blueprint":
                raise ValueError("Unexpected project in delivery batch")
        report_path = None
        if quality_report is not None:
            report_path = Path(quality_report).resolve(strict=True)
            if not report_path.is_file():
                raise ValueError("The quality report must be a saved file")
            if report_path.drive.casefold() != self.settings.data_dir.resolve().drive.casefold():
                raise ValueError("The quality report must remain on the Atnos data drive")
        if require_all_ready and report_path is None:
            raise ValueError("Complete-video delivery needs a saved quality report")
        report_path_text = str(report_path) if report_path else None
        jobs = json.dumps(job_ids)
        now = datetime.now(UTC).isoformat()
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT recipient,jobs_json,require_all_ready,quality_report_path FROM production_batch_deliveries WHERE batch_id=?", (batch_id,)).fetchone()
            if previous and (previous["recipient"] != recipient or previous["jobs_json"] != jobs
                             or bool(previous["require_all_ready"]) != require_all_ready
                             or previous["quality_report_path"] != report_path_text):
                raise ValueError("This delivery already belongs to different inputs")
            db.execute("INSERT OR IGNORE INTO production_batch_deliveries(batch_id,recipient,jobs_json,status,authorized_at,updated_at,require_all_ready,quality_report_path) VALUES(?,?,?,'waiting_production',?,?,?,?)", (batch_id, recipient, jobs, now, now, int(require_all_ready), report_path_text))
            db.commit()
        return self.status(batch_id)

    def status(self, batch_id):
        with connect(self.settings) as db:
            row = db.execute("SELECT * FROM production_batch_deliveries WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown delivery batch")
        return {key: row[key] for key in row.keys() if not key.startswith("lease_")}

    @contextmanager
    def _lease(self, batch_id):
        token = uuid4().hex
        with connect(self.settings) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM production_batch_deliveries WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown delivery batch")
            if _same_process(row["lease_pid"], row["lease_started"]):
                raise RuntimeError("Another worker owns this delivery")
            db.execute("UPDATE production_batch_deliveries SET lease_token=?,lease_pid=?,lease_started=? WHERE batch_id=?", (token, os.getpid(), psutil.Process().create_time(), batch_id))
            db.commit()
        try:
            yield dict(row), token
        finally:
            with connect(self.settings) as db:
                db.execute("UPDATE production_batch_deliveries SET lease_token=NULL,lease_pid=NULL,lease_started=NULL WHERE batch_id=? AND lease_token=?", (batch_id, token))
                db.commit()

    def _save(self, batch_id, token, status, **fields):
        allowed = {"gmail_account_id", "draft_id", "message_id", "report_hash", "evidence_json", "error"}
        if not set(fields).issubset(allowed):
            raise ValueError("Unexpected delivery state fields")
        fields.update(status=status, updated_at=datetime.now(UTC).isoformat())
        with connect(self.settings) as db:
            db.execute("UPDATE production_batch_deliveries SET " + ",".join(f"{key}=?" for key in fields) + " WHERE batch_id=? AND lease_token=?", (*fields.values(), batch_id, token))
            db.commit()

    def report(self, row):
        jobs = [self.orders.get(job) for job in json.loads(row["jobs_json"])]
        if any(job["worker_live"] or job["status"] not in TERMINAL or job["current_step"] == "intake_review" for job in jobs):
            return None
        strict = bool(row.get("require_all_ready"))
        if strict and any(job["status"] != "ready" for job in jobs):
            return None
        folder = self.settings.data_dir / "production-batch-results" / row["batch_id"]
        folder.mkdir(parents=True, exist_ok=True)
        body = ["Atnos: wyniki zleconej partii Wealth Blueprint", "",
                "Wszystkie filmy tej dostawy sa gotowe; dolaczono ich pliki oraz raport oceny. Ocena nie oznacza akceptacji wygladu lub glosu przez uzytkownika."
                if strict else "To raport faktycznych stanow, nie potwierdzenie, ze wszystkie filmy sa gotowe.", ""]
        attachments, entries, used = [], [], 0
        packages = {}
        # A requested complete batch must never silently turn into a status-only
        # email or lose its last video because thumbnails consumed the budget.
        if strict:
            packages = {job["id"]: verified_package(self.orders, job["id"]) for job in jobs}
        quality = None
        if row.get("quality_report_path"):
            source = Path(row["quality_report_path"]).resolve(strict=True)
            if not source.is_file() or source.drive.casefold() != self.settings.data_dir.resolve().drive.casefold():
                raise ValueError("The saved quality report is no longer on the Atnos data drive")
            attachment = folder / ("quality-review" + source.suffix)
            shutil.copyfile(source, attachment)
            quality = {"file": attachment.name, "sha256": sha256(attachment.read_bytes()).hexdigest(),
                       "size": attachment.stat().st_size}
            attachments.append(attachment)
            used += quality["size"]
            body.append(f"Raport oceny: {attachment.name}")
        if strict:
            required_bytes = used + sum(package["files"]["video"].stat().st_size for package in packages.values())
            if required_bytes > MAX_ATTACHMENTS_BYTES - 65536:
                raise ValueError("All requested videos and the quality report exceed the email attachment budget; split this batch into separately authorized delivery groups before sending")
            for job in jobs:
                path = packages[job["id"]]["files"]["video"]
                attachment = folder / f"{job['id']}_video{path.suffix}"
                shutil.copyfile(path, attachment)
                attachments.append(attachment)
                used += attachment.stat().st_size
        for job in jobs:
            entry = {"job_id": job["id"], "topic": job["request"]["topic"], "status": job["status"]}
            body.extend([job['request']['topic'], f"Studio: http://127.0.0.1:3000/studio/{job['id']}"])
            if job["status"] == "ready":
                try:
                    package = packages.get(job["id"]) or verified_package(self.orders, job["id"])
                except (ValueError, KeyError, OSError) as exc:
                    entry.update(status="insufficient_evidence", error=str(redact_secrets(str(exc)))[:700])
                    body.append("Status: insufficient_evidence. Pakiet nie przeszedl ponownej weryfikacji; nie zalaczono go jako gotowy.")
                else:
                    body.append("Status: ready. Integralnosc pakietu i zapisane bramki QA potwierdzone.")
                    entry["package_hash"] = package["hash"]
                    entry["files"] = {key: str(path) for key, path in package["files"].items()}
                    keys = ("landscape_thumbnail", "metadata", "captions") if strict else ("landscape_thumbnail", "metadata", "captions", "video")
                    if strict:
                        body.append(f"Zalacznik: {job['id']}_video{package['files']['video'].suffix}")
                    for key in keys:
                        path = package["files"][key]
                        size = path.stat().st_size
                        if used + size <= MAX_ATTACHMENTS_BYTES - 65536:
                            attachment = folder / f"{job['id']}_{key}{path.suffix}"
                            shutil.copyfile(path, attachment)
                            attachments.append(attachment)
                            used += size
                            body.append(f"Zalacznik: {attachment.name}")
                        else:
                            body.append(f"Plik przekracza budzet zalacznikow maila, pozostaje w Atnosie: {path}")
            else:
                body.append("Status: " + job["status"])
                entry["error"] = str(redact_secrets(str(job.get("error") or "")))[:700]
                body.append("Kontrola/produkcja zatrzymana: " + (entry["error"] or job["current_step"]))
            entries.append(entry)
            body.append("")
        body.append("Linki 127.0.0.1 i sciezki plikow dzialaja tylko na komputerze z Atnosem. Nie sa publicznymi linkami do pobrania.")
        body.append("Raport nie jest potwierdzeniem publikacji w YouTube ani gwarancja wynikow inwestowania.")
        # The full manifest avoids confusing identically named attachments across jobs.
        manifest = folder / "results.json"
        payload = {"batch_id": row["batch_id"], "recipient": row["recipient"], "jobs": entries}
        if quality is not None:
            payload["quality_review"] = quality
        if strict:
            payload["require_all_ready"] = True
        manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        text = "\n".join(body)
        (folder / "email.txt").write_text(text, encoding="utf-8")
        if strict and used + manifest.stat().st_size > MAX_ATTACHMENTS_BYTES:
            raise ValueError("Complete batch manifest exceeds the email attachment budget")
        if used + manifest.stat().st_size <= MAX_ATTACHMENTS_BYTES:
            attachments.insert(0, manifest)
        return {"body": text, "attachments": attachments, "hash": content_hash(payload), "entries": entries}

    def process(self, batch_id):
        with self._lease(batch_id) as (row, token):
            if row["status"] in {"sent", "insufficient_evidence", "cancelled"}:
                return self.status(batch_id)
            if row["status"] == "sending":
                self._save(batch_id, token, "insufficient_evidence", error="Przerwana wysylka. Sprawdz folder Wyslane; nie ponawiam automatycznie.")
                return self.status(batch_id)
            try:
                report = self.report(row)
            except (ValueError, KeyError, OSError) as exc:
                self._save(batch_id, token, "blocked_technical", error=str(redact_secrets(str(exc)))[:1000])
                return self.status(batch_id)
            if report is None:
                self._save(batch_id, token, "waiting_production", error=None)
                return self.status(batch_id)
            if row["draft_id"] and row["report_hash"] != report["hash"]:
                self._save(batch_id, token, "blocked_technical", error="Wyniki zmienily sie po utworzeniu szkicu. Wymagana kontrola przed wysylka.")
                return self.status(batch_id)
            phase = "preflight"
            try:
                with self.database.session_scope() as session:
                    account = row["gmail_account_id"]
                    if account is None:
                        accounts = [c.account_id for c in OAuthConnectionRepository(session).list_all()
                                    if c.connector_key == "gmail" and c.status == "connected" and c.account_id is not None]
                        account = accounts[0] if accounts else None
                    if account is None:
                        self._save(batch_id, token, "blocked_user_action", error="Polacz ponownie Gmail w Atnosie. Raport pozostaje zapisany lokalnie.")
                        return self.status(batch_id)
                    gmail = self.gmail_factory(session, self.settings, account_id=account)
                    if isinstance(gmail, GmailConnectorService) and gmail.registry.get_connector("gmail").mode != "real_provider":
                        raise ValueError("Real batch delivery cannot use a simulated Gmail provider")
                    if not gmail.get_status().live_write_enabled or gmail.probe().probe_status != "ok":
                        self._save(batch_id, token, "blocked_user_action", error="Gmail nie potwierdzil dostepu do wysylki.")
                        return self.status(batch_id)
                    self._save(batch_id, token, "preparing", gmail_account_id=account, error=None)
                    draft_id = row["draft_id"]
                    if not draft_id:
                        phase = "drafting"
                        draft = gmail.draft_message(to=row["recipient"], subject=f"[Atnos] Wyniki partii {batch_id}", body=report["body"], attachments=report["attachments"])
                        draft_id = draft.get("draft_id")
                        if not draft_id:
                            raise ValueError("Gmail did not confirm a draft ID")
                        self._save(batch_id, token, "drafted", draft_id=draft_id, report_hash=report["hash"])
                    phase = "sending"
                    self._save(batch_id, token, "sending")
                    sent = gmail.send_message(draft_id=draft_id)
                    message_id = sent.get("message_id")
                    if not message_id:
                        raise ValueError("Gmail did not confirm a sent message ID")
                    self._save(batch_id, token, "sending", message_id=message_id)
                    observed = gmail.read_message(message_id)
                    recipients = {address.casefold() for _, address in getaddresses([observed.get("headers", {}).get("To", "")])}
                    if "SENT" not in observed.get("label_ids", []) or row["recipient"].casefold() not in recipients:
                        raise ValueError("Gmail readback did not confirm the recipient and SENT label")
                    self._save(batch_id, token, "sent", error=None, evidence_json=json.dumps({"message_id": message_id, "sent_label_verified": True, "recipient_verified": True, "report_hash": report["hash"]}))
            except Exception as exc:
                state = "insufficient_evidence" if phase == "sending" else "blocked_technical"
                self._save(batch_id, token, state, error=str(redact_secrets(str(exc)))[:1000])
        return self.status(batch_id)
