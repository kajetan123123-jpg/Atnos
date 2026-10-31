"""Read-only YouTube smoke. Prints status only — never tokens or secrets."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atnos.core.config import get_settings
from atnos.db.session import DatabaseManager
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.project_content_brain.content_intelligence import (
    ContentIntelligenceStore,
    assert_youtube_read,
    cluster_comments,
    normalize_analytics,
    performance_insights,
    present_youtube_integration,
)
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService


def classify(status) -> str:
    if not status.configured and not status.connected:
        return "MISSING"
    if status.connected and any("missing required scopes" in item for item in status.warnings):
        return "INSUFFICIENT_SCOPE"
    if status.connected and any("refresh_token" in item for item in status.warnings):
        return "EXPIRED"
    if any("ponownego połączenia" in item or "reconnect" in item.casefold() for item in status.warnings):
        return "EXPIRED"
    if status.ready:
        return "CONNECTED"
    if status.connected:
        return "ERROR"
    return "MISSING"


def main() -> int:
    settings = get_settings()
    db = DatabaseManager(settings)
    report: dict = {"oauth": "MISSING", "live_smoke": "SKIPPED — OAuth missing"}
    with db.session_scope() as session:
        service = YouTubeConnectorService(session, settings)
        status = service.get_status()
        report["oauth"] = classify(status)
        report["live_smoke"] = "SKIPPED — OAuth missing" if report["oauth"] == "MISSING" else (
            "SKIPPED — OAuth expired" if report["oauth"] == "EXPIRED" else report["live_smoke"]
        )
        report["configured"] = bool(status.configured)
        report["connected"] = bool(status.connected)
        report["ready"] = bool(status.ready)
        report["channel_title"] = status.channel_title
        report["scopes"] = list(status.granted_scopes)
        report["warnings"] = [item for item in status.warnings if "token" not in item.casefold() or "missing" in item.casefold() or "refresh" in item.casefold()]
        if report["oauth"] != "CONNECTED":
            presented = present_youtube_integration(report["oauth"])
            with connect(settings) as brain_db:
                store = ContentIntelligenceStore(brain_db)
                sync = store.record_sync(
                    "wealth_blueprint",
                    status=presented["status"],
                    detail=presented["detail"],
                    success=False,
                )
                brain_db.commit()
            report["ui"] = presented
            report["last_attempt"] = sync["last_attempt"]
            report["last_success"] = sync["last_success"]
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        try:
            assert_youtube_read(report["oauth"])
            channel = service.get_owned_channel()
        except YouTubeConnectorError as exc:
            report["oauth"] = "ERROR"
            report["error"] = str(exc)[:300]
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        report["channel_id"] = channel.get("channel_id")
        report["channel_title"] = channel.get("title")
        listing = service.list_public_channel_videos(channel["channel_id"], max_results=5)
        videos = listing.get("videos") or []
        if not videos:
            report["live_smoke"] = "FAILED"
            report["error"] = "Connected channel returned no public videos."
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        video = videos[0]
        video_id = video["video_id"]
        analytics = service.fetch_analytics(video_id)
        comments_payload = {"comments": []}
        comments_error = None
        try:
            comments_payload = service.list_comment_threads(video_id, max_results=10)
        except YouTubeConnectorError as exc:
            comments_error = str(exc)[:240]
        now = utc_now_iso()
        snapshot = normalize_analytics(analytics, fetched_at=now)
        with connect(settings) as brain_db:
            store = ContentIntelligenceStore(brain_db)
            store.save_metrics("wealth_blueprint", None, snapshot)
            comments = comments_payload.get("comments") or []
            store.save_comments("wealth_blueprint", comments)
            store.save_comments("wealth_blueprint", comments)
            this_video = store.list_comments("wealth_blueprint", video_id)
            insights = performance_insights(store.list_metrics("wealth_blueprint"))
            signals = cluster_comments(this_video)
            observed = [name for name, slot in snapshot["metrics"].items() if slot["status"] == "observed"]
            unknown = [name for name, slot in snapshot["metrics"].items() if slot["status"] == "UNKNOWN"]
            analytics_ready = snapshot.get("integration") == "LIVE" and bool(observed)
            comments_ready = comments_error is None
            sync = store.record_sync(
                "wealth_blueprint",
                status="LIVE" if analytics_ready and comments_ready else ("PENDING" if snapshot.get("integration") == "PENDING" else "DEGRADED"),
                detail=str(snapshot.get("reason") or ""),
                success=analytics_ready,
            )
            brain_db.commit()
        report["live_smoke"] = "PASS" if analytics_ready else "PENDING"
        report["video_id"] = video_id
        report["title"] = video.get("title")
        report["published_at"] = video.get("published_at")
        report["analytics_status"] = analytics.get("status")
        report["observed_metrics"] = observed
        report["unknown_metrics"] = unknown
        report["comments_fetched"] = len(comments_payload.get("comments") or [])
        report["comments_error"] = comments_error
        report["deduped_comment_rows_for_video"] = len(this_video)
        report["performance_insight"] = insights[0]["status"] if insights else None
        report["audience_signals"] = len(signals)
        report["integration"] = snapshot.get("integration")
        report["window_days"] = snapshot.get("window_days")
        report["last_success"] = sync["last_success"]
        report["last_attempt"] = sync["last_attempt"]
        if not analytics_ready:
            report["error"] = "Analytics pending or empty — not declaring LIVE/PASS without observed metrics."
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
