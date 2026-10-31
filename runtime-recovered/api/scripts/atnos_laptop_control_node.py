from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_DATA_DIR = Path.home() / ".atnos" / "control-node"


@dataclass(frozen=True)
class EndpointProbe:
    name: str
    method: str
    path: str
    required: bool = True


@dataclass
class ProbeResult:
    name: str
    ok: bool
    status_code: int | None
    payload: dict[str, Any] | list[Any] | None
    error: str | None
    latency_ms: int


@dataclass(frozen=True)
class Alert:
    key: str
    level: str
    message: str
    evidence: dict[str, Any]


PROBES = (
    EndpointProbe("health", "GET", "/health"),
    EndpointProbe("readiness", "GET", "/api/production/readiness?include_live_checks=false"),
    EndpointProbe("voice", "GET", "/api/production/voice-provider/status"),
    EndpointProbe("visual", "GET", "/api/production/visual-backend/status"),
    EndpointProbe("public_quality", "GET", "/api/production/public-quality/status"),
    EndpointProbe("money_machine", "GET", "/api/production/money-machine/summary?include_oauth=true&max_uploads=5&per_account_limit=2"),
    EndpointProbe("gmail", "GET", "/connectors/gmail/status", required=False),
    EndpointProbe("youtube", "GET", "/connectors/youtube/status", required=False),
    EndpointProbe("jobs", "GET", "/api/production/jobs?limit=20"),
)

FAILED_JOB_STATUSES = {
    "error",
    "failed",
    "qa_failed",
    "private_upload_failed",
    "private_upload_quota_exceeded",
    "public_publish_failed",
    "public_publish_quota_exceeded",
    "upload_connection_required",
    "public_publish_connection_required",
}

RUNNING_JOB_STATUSES = {"running", "queued", "in_production"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def normalize_api_base(value: str) -> str:
    return value.strip().rstrip("/") or DEFAULT_API_BASE


def request_json(
    api_base: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout_s: float = 15.0,
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    url = normalize_api_base(api_base) + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method.upper())
    with urlopen(request, timeout=timeout_s) as response:
        raw = response.read()
        if not raw:
            return int(response.status), None
        return int(response.status), json.loads(raw.decode("utf-8"))


def probe_endpoint(api_base: str, probe: EndpointProbe, *, timeout_s: float) -> ProbeResult:
    started = time.perf_counter()
    try:
        status_code, payload = request_json(api_base, probe.method, probe.path, timeout_s=timeout_s)
        ok = 200 <= status_code < 300
        error = None if ok else f"HTTP {status_code}"
    except HTTPError as exc:
        status_code = int(exc.code)
        payload = _read_error_payload(exc)
        ok = False
        error = f"HTTP {status_code}"
    except (OSError, TimeoutError, URLError) as exc:
        status_code = None
        payload = None
        ok = False
        error = str(exc)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ProbeResult(
        name=probe.name,
        ok=ok,
        status_code=status_code,
        payload=payload,
        error=error,
        latency_ms=latency_ms,
    )


def _read_error_payload(exc: HTTPError) -> dict[str, Any] | None:
    try:
        raw = exc.read()
        return json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        return None


def run_snapshot(
    api_base: str,
    *,
    timeout_s: float,
    stale_job_minutes: int = 45,
    failed_job_window_hours: int = 24,
) -> dict[str, Any]:
    results = [probe_endpoint(api_base, probe, timeout_s=timeout_s) for probe in PROBES]
    payload = {
        "checked_at": utc_now_iso(),
        "api_base": normalize_api_base(api_base),
        "probes": {
            result.name: {
                "ok": result.ok,
                "status_code": result.status_code,
                "latency_ms": result.latency_ms,
                "error": result.error,
                "payload": result.payload,
            }
            for result in results
        },
    }
    payload["alerts"] = [
        alert.__dict__
        for alert in analyze_snapshot(
            payload,
            stale_job_minutes=stale_job_minutes,
            failed_job_window_hours=failed_job_window_hours,
        )
    ]
    payload["overall_status"] = overall_status(payload["alerts"])
    return payload


def analyze_snapshot(
    snapshot: dict[str, Any],
    *,
    stale_job_minutes: int = 45,
    failed_job_window_hours: int = 24,
) -> list[Alert]:
    probes = snapshot.get("probes") if isinstance(snapshot.get("probes"), dict) else {}
    alerts: list[Alert] = []

    health = _probe(probes, "health")
    if not health.get("ok"):
        alerts.append(
            Alert(
                key="api_unreachable",
                level="critical",
                message="Główny PC nie odpowiada na /health. Laptop nie widzi Atnosa.",
                evidence={"error": health.get("error"), "status_code": health.get("status_code")},
            )
        )
        return alerts

    for name in ("readiness", "voice", "visual", "public_quality", "money_machine", "jobs"):
        item = _probe(probes, name)
        if not item.get("ok"):
            alerts.append(
                Alert(
                    key=f"{name}_endpoint_failed",
                    level="warning",
                    message=f"Endpoint kontrolny {name} nie odpowiedział poprawnie.",
                    evidence={"error": item.get("error"), "status_code": item.get("status_code")},
                )
            )

    voice_payload = _payload(probes, "voice")
    if isinstance(voice_payload, dict) and voice_payload.get("public_voice_ready") is False:
        alerts.append(
            Alert(
                key="public_voice_not_ready",
                level="error",
                message="Public-quality voice nie jest gotowy. Publiczne uploady powinny zostać zablokowane.",
                evidence=_pick(voice_payload, "next_action", "provider", "public_voice_ready"),
            )
        )

    visual_payload = _payload(probes, "visual")
    if isinstance(visual_payload, dict) and visual_payload.get("public_visual_ready") is False:
        alerts.append(
            Alert(
                key="public_visual_not_ready",
                level="error",
                message="Public-quality visual backend nie jest gotowy.",
                evidence=_pick(visual_payload, "selected_video_backend", "effective_video_backend", "next_action"),
            )
        )

    public_quality_payload = _payload(probes, "public_quality")
    blockers = _collect_blockers(public_quality_payload)
    if blockers:
        alerts.append(
            Alert(
                key="public_quality_blocked",
                level="warning",
                message="Public quality gate ma blokery.",
                evidence={"blockers": blockers[:10]},
            )
        )

    gmail_payload = _payload(probes, "gmail")
    if isinstance(gmail_payload, dict) and gmail_payload.get("live_write_enabled") is False:
        alerts.append(
            Alert(
                key="gmail_write_not_ready",
                level="warning",
                message="Gmail nie ma aktywnego live_write_enabled, więc alerty mailowe mogą nie wyjść.",
                evidence=_pick(gmail_payload, "oauth_status", "connected_at", "warnings"),
            )
        )

    youtube_payload = _payload(probes, "youtube")
    if isinstance(youtube_payload, dict) and youtube_payload.get("ready") is False:
        alerts.append(
            Alert(
                key="youtube_not_ready",
                level="warning",
                message="YouTube connector nie jest gotowy do prywatnych uploadów/analityki.",
                evidence=_pick(youtube_payload, "connected", "ready", "warnings", "channel_title"),
            )
        )

    jobs_payload = _payload(probes, "jobs")
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else []
    if isinstance(jobs, list):
        alerts.extend(
            _job_alerts(
                jobs,
                stale_job_minutes=stale_job_minutes,
                failed_job_window_hours=failed_job_window_hours,
            )
        )

    return _dedupe_alerts(alerts)


def _probe(probes: dict[str, Any], name: str) -> dict[str, Any]:
    item = probes.get(name)
    return item if isinstance(item, dict) else {}


def _payload(probes: dict[str, Any], name: str) -> Any:
    return _probe(probes, name).get("payload")


def _pick(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: payload.get(key) for key in keys if key in payload}


def _collect_blockers(payload: Any) -> list[str]:
    blockers: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = key.lower()
            if "blocker" in lowered and isinstance(value, list):
                blockers.extend(str(item) for item in value if item)
            elif isinstance(value, (dict, list)):
                blockers.extend(_collect_blockers(value))
    elif isinstance(payload, list):
        for item in payload:
            blockers.extend(_collect_blockers(item))
    return list(dict.fromkeys(blockers))


def _job_alerts(jobs: list[Any], *, stale_job_minutes: int, failed_job_window_hours: int) -> list[Alert]:
    alerts: list[Alert] = []
    now = utc_now()
    stale_seconds = stale_job_minutes * 60
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id") or "")
        status = str(job.get("status") or "").lower()
        current_step = str(job.get("current_step") or "")
        updated_at = _parse_dt(str(job.get("updated_at") or ""))
        if status in FAILED_JOB_STATUSES:
            if failed_job_window_hours > 0 and updated_at is not None:
                age_seconds = (now - updated_at).total_seconds()
                if age_seconds > failed_job_window_hours * 3600:
                    continue
            alerts.append(
                Alert(
                    key=f"job_failed:{job_id}",
                    level="error",
                    message=f"Job produkcyjny {job_id} jest w stanie {status}.",
                    evidence=_pick(job, "id", "account_id", "topic_title", "current_step", "error"),
                )
            )
            continue
        if status in RUNNING_JOB_STATUSES and updated_at is not None:
            age_seconds = (now - updated_at).total_seconds()
            if age_seconds > stale_seconds:
                alerts.append(
                    Alert(
                        key=f"job_stale:{job_id}",
                        level="critical",
                        message=f"Job produkcyjny {job_id} nie aktualizował się od {int(age_seconds // 60)} min.",
                        evidence={
                            "id": job_id,
                            "status": status,
                            "current_step": current_step,
                            "updated_at": job.get("updated_at"),
                        },
                    )
                )
    return alerts


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _dedupe_alerts(alerts: list[Alert]) -> list[Alert]:
    seen: set[str] = set()
    result: list[Alert] = []
    for alert in alerts:
        if alert.key in seen:
            continue
        seen.add(alert.key)
        result.append(alert)
    return result


def overall_status(alerts: list[dict[str, Any]] | list[Alert]) -> str:
    levels = {alert["level"] if isinstance(alert, dict) else alert.level for alert in alerts}
    if "critical" in levels:
        return "critical"
    if "error" in levels:
        return "error"
    if "warning" in levels:
        return "warning"
    return "ok"


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def read_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sent_alerts": {}}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def alert_due(alert: dict[str, Any], state: dict[str, Any], *, cooldown_s: int) -> bool:
    sent = state.setdefault("sent_alerts", {})
    last = _parse_dt(str(sent.get(alert["key"]) or ""))
    if last is None:
        return True
    return (utc_now() - last).total_seconds() >= cooldown_s


def mark_alert_sent(alert: dict[str, Any], state: dict[str, Any]) -> None:
    state.setdefault("sent_alerts", {})[alert["key"]] = utc_now_iso()


def format_alert_email(snapshot: dict[str, Any], alerts: list[dict[str, Any]]) -> tuple[str, str]:
    subject = f"Atnos Control Node: {snapshot.get('overall_status', 'unknown').upper()}"
    lines = [
        "Atnos wykrył problem w trybie laptop control node.",
        "",
        f"Czas UTC: {snapshot.get('checked_at')}",
        f"API: {snapshot.get('api_base')}",
        f"Status: {snapshot.get('overall_status')}",
        "",
        "Alerty:",
    ]
    for alert in alerts:
        lines.append(f"- [{alert.get('level')}] {alert.get('message')}")
        evidence = alert.get("evidence")
        if evidence:
            lines.append(f"  evidence: {json.dumps(evidence, ensure_ascii=False)}")
    lines.extend(
        [
            "",
            "To jest alert operacyjny. Laptop nie renderuje filmów; tylko monitoruje główny PC.",
        ]
    )
    return subject, "\n".join(lines)


def dispatch_gmail_alert(
    api_base: str,
    *,
    owner_email: str,
    subject: str,
    body: str,
    send: bool,
    timeout_s: float,
) -> dict[str, Any]:
    draft_status, draft = request_json(
        api_base,
        "POST",
        "/connectors/gmail/drafts",
        body={"to": owner_email, "subject": subject, "body": body},
        timeout_s=timeout_s,
    )
    if not isinstance(draft, dict) or not draft.get("draft_id"):
        return {"status": "draft_failed", "http_status": draft_status, "response": draft}
    result: dict[str, Any] = {"status": "drafted", "draft": draft}
    if not send:
        return result

    _, gmail_status = request_json(api_base, "GET", "/connectors/gmail/status", timeout_s=timeout_s)
    account_id = gmail_status.get("account_id") if isinstance(gmail_status, dict) else None
    if not account_id:
        result["send_status"] = "blocked"
        result["reason"] = "Gmail account_id is not available."
        return result

    _, preview = request_json(
        api_base,
        "POST",
        "/connectors/gmail/send-preview",
        body={
            "account_id": int(account_id),
            "project_id": None,
            "draft_id": str(draft["draft_id"]),
            "to": owner_email,
            "subject": subject,
            "body": body,
        },
        timeout_s=timeout_s,
    )
    draft_action_id = preview.get("id") if isinstance(preview, dict) else None
    result["send_preview"] = preview
    if not draft_action_id:
        result["send_status"] = "preview_failed"
        return result

    for path in (
        f"/action-drafts/{draft_action_id}/submit-for-approval",
        f"/action-drafts/{draft_action_id}/approve",
    ):
        request_json(api_base, "POST", path, body={}, timeout_s=timeout_s)
    _, executed = request_json(
        api_base,
        "POST",
        f"/action-drafts/{draft_action_id}/execute",
        body={"confirm": True},
        timeout_s=timeout_s,
    )
    result["send_status"] = "sent"
    result["execution"] = executed
    return result


def run_once(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    snapshot = run_snapshot(
        args.api_base,
        timeout_s=args.timeout,
        stale_job_minutes=args.stale_job_minutes,
        failed_job_window_hours=args.failed_job_window_hours,
    )
    alerts = snapshot.get("alerts") if isinstance(snapshot.get("alerts"), list) else []

    due_alerts = [alert for alert in alerts if alert_due(alert, state, cooldown_s=args.alert_cooldown)]
    if due_alerts and args.owner_email:
        subject, body = format_alert_email(snapshot, due_alerts)
        try:
            snapshot["gmail_alert"] = dispatch_gmail_alert(
                args.api_base,
                owner_email=args.owner_email,
                subject=subject,
                body=body,
                send=args.send_gmail_alerts,
                timeout_s=args.timeout,
            )
            for alert in due_alerts:
                mark_alert_sent(alert, state)
        except Exception as exc:
            snapshot["gmail_alert"] = {"status": "failed", "error": str(exc)}
    elif due_alerts:
        snapshot["gmail_alert"] = {"status": "skipped", "reason": "--owner-email is not configured."}

    write_jsonl(args.events_log, snapshot)
    return snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lekki kontroler laptopowy dla Atnosa.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Base URL głównego PC, np. http://198.51.100.115:8000")
    parser.add_argument("--interval", type=int, default=300, help="Polling interval w sekundach.")
    parser.add_argument("--timeout", type=float, default=15.0, help="Timeout pojedynczego HTTP requestu.")
    parser.add_argument("--once", action="store_true", help="Wykonaj jeden cykl i zakończ.")
    parser.add_argument("--owner-email", default="", help="Adres właściciela do draftów/alertów Gmail.")
    parser.add_argument("--send-gmail-alerts", action="store_true", help="Po utworzeniu draftu wykonaj rzeczywistą wysyłkę przez Gmail API.")
    parser.add_argument("--alert-cooldown", type=int, default=1800, help="Minimalny odstęp między tym samym alertem.")
    parser.add_argument("--stale-job-minutes", type=int, default=45, help="Po ilu minutach bez update'u job jest uznany za zacięty.")
    parser.add_argument("--failed-job-window-hours", type=int, default=24, help="Raportuj tylko świeże failed/qa_failed z tego okna. 0 = wszystkie.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Katalog logów Control Node.")
    args = parser.parse_args()
    args.api_base = normalize_api_base(args.api_base)
    args.events_log = args.data_dir / "control_node_events.jsonl"
    args.state_path = args.data_dir / "control_node_state.json"
    return args


def main() -> int:
    args = parse_args()
    state = read_state(args.state_path)
    while True:
        snapshot = run_once(args, state)
        write_state(args.state_path, state)
        print(
            json.dumps(
                {
                    "checked_at": snapshot.get("checked_at"),
                    "overall_status": snapshot.get("overall_status"),
                    "alerts": len(snapshot.get("alerts") or []),
                    "gmail_alert": snapshot.get("gmail_alert", {}).get("status") if isinstance(snapshot.get("gmail_alert"), dict) else None,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if args.once:
            return 0 if snapshot.get("overall_status") in {"ok", "warning"} else 2
        time.sleep(max(10, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
