from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import get_settings, load_runtime_env_files
from atnos.db.session import DatabaseManager
from atnos.intelligence.google_trends import collect_google_trends
from atnos.intelligence.storage import connect, init_intelligence_db, write_collection_run
from atnos.intelligence.youtube_api import youtube_data_client_mode
from atnos.intelligence.youtube_trends import collect_youtube_trends


def _counts(settings) -> dict[str, int]:
    tables = (
        "yt_signals",
        "gtrends_signals",
        "competitor_channels",
        "competitor_videos",
        "own_performance",
        "reference_channels",
        "topic_queue",
        "collection_runs",
    )
    with connect(settings) as db:
        return {table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def _research_readiness(settings, *, account_id: str, niches: list[str], region: str) -> dict[str, Any]:
    normalized_region = region.strip().upper() or "PL"
    with connect(settings) as db:
        yt_count = int(
            db.execute(
                "SELECT COUNT(*) FROM yt_signals WHERE account_id = ? AND region = ?",
                (account_id, normalized_region),
            ).fetchone()[0]
            or 0
        )
        gt_count = int(
            db.execute(
                "SELECT COUNT(*) FROM gtrends_signals WHERE geo = ?",
                (normalized_region,),
            ).fetchone()[0]
            or 0
        )
        niche_rows: list[dict[str, Any]] = []
        run_keys: list[str] = []
        for niche in niches:
            run_key = f"youtube_trends:{account_id}:{' '.join(niche.strip().casefold().split())}:{normalized_region}"
            run_keys.append(run_key)
            row = db.execute(
                """
                SELECT COUNT(*) AS count, MAX(collected_at) AS last_collected_at
                FROM yt_signals
                WHERE account_id = ? AND region = ? AND niche = ?
                """,
                (account_id, normalized_region, niche),
            ).fetchone()
            niche_rows.append(
                {
                    "niche": niche,
                    "yt_signal_count": int(row["count"] or 0),
                    "last_collected_at": row["last_collected_at"],
                }
            )
        collector_runs: list[dict[str, Any]] = []
        if run_keys:
            placeholders = ",".join("?" for _ in run_keys)
            collector_runs = [
                dict(row)
                for row in db.execute(
                    f"""
                    SELECT run_key, collector, status, last_attempt_at, last_success_at, next_retry_at, message
                    FROM collection_runs
                    WHERE run_key IN ({placeholders})
                    ORDER BY last_attempt_at DESC
                    """,
                    tuple(run_keys),
                ).fetchall()
            ]

    blockers: list[str] = []
    if not (settings.youtube_data_api_key or (settings.oauth_google_client_id and settings.oauth_google_client_secret_ref)):
        blockers.append("youtube_data_api_key_or_oauth")
    if yt_count <= 0:
        blockers.append("yt_signals_empty")
    if gt_count <= 0:
        blockers.append("gtrends_signals_empty")
    if any(row["yt_signal_count"] <= 0 for row in niche_rows):
        blockers.append("yt_signals_missing_for_requested_niches")
    return {
        "ready_for_topic_scoring": not blockers,
        "blockers": blockers,
        "youtube_mode": youtube_data_client_mode(settings),
        "youtube_data_api_key_configured": bool(settings.youtube_data_api_key),
        "youtube_oauth_client_configured": bool(settings.oauth_google_client_id and settings.oauth_google_client_secret_ref),
        "counts": {"yt_signals": yt_count, "gtrends_signals": gt_count},
        "niches": niche_rows,
        "collector_runs": collector_runs,
        "next_actions": _next_actions(blockers, account_id=account_id, niches=niches, region=normalized_region),
    }


def _next_actions(blockers: list[str], *, account_id: str, niches: list[str], region: str) -> list[str]:
    actions: list[str] = []
    if "youtube_data_api_key_or_oauth" in blockers:
        actions.append("Set YOUTUBE_DATA_API_KEY in .env or import Google OAuth client_secret.json and connect YouTube Studio.")
    if "yt_signals_empty" in blockers or "yt_signals_missing_for_requested_niches" in blockers:
        actions.append(
            "Run: python scripts/run_intelligence_collectors.py "
            f"--account-id {account_id} --niches \"{','.join(niches)}\" --region {region} --force"
        )
    if "gtrends_signals_empty" in blockers:
        actions.append("Check pytrends availability/rate limits and rerun Google Trends collection.")
    return actions


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(marker in lowered for marker in ("secret", "token", "password", "api_key", "credential")):
                result[key] = "[REDACTED]" if item else item
            else:
                result[key] = _safe_payload(item)
        return result
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    load_runtime_env_files()
    settings = get_settings()
    await init_intelligence_db(settings)

    niches = [item.strip() for item in args.niches.split(",") if item.strip()]
    before = _counts(settings)
    readiness_before = _research_readiness(settings, account_id=args.account_id, niches=niches, region=args.region)
    results: dict[str, Any] = {
        "account_id": args.account_id,
        "region": args.region.upper(),
        "niches": niches,
        "before": before,
        "research_readiness_before": readiness_before,
        "youtube_mode": youtube_data_client_mode(settings),
        "youtube_data_api_key_configured": bool(settings.youtube_data_api_key),
        "youtube_oauth_client_configured": bool(settings.oauth_google_client_id and settings.oauth_google_client_secret_ref),
    }

    if not niches:
        results["status"] = "invalid_request"
        results["message"] = "Podaj co najmniej jedna nisze przez --niches."
        results["after"] = _counts(settings)
        return results

    google_keywords = niches
    try:
        results["google_trends"] = await collect_google_trends(
            settings=settings,
            keywords=google_keywords,
            timeframe=args.timeframe,
            geo=args.region,
        )
    except Exception as exc:
        results["google_trends"] = {"status": "error", "message": str(exc)}

    youtube_results = []
    if not settings.youtube_data_api_key and not (settings.oauth_google_client_id and settings.oauth_google_client_secret_ref):
        for niche in niches:
            run_key = f"youtube_trends:{args.account_id}:{' '.join(niche.strip().casefold().split())}:{args.region.upper()}"
            message = (
                "YouTube Trends wymaga YOUTUBE_DATA_API_KEY albo skonfigurowanego YouTube OAuth. "
                "Nie zapisano zadnych sztucznych yt_signals."
            )
            write_collection_run(
                settings,
                run_key=run_key,
                collector="youtube_trends",
                status="needs_config",
                message=message,
            )
            youtube_results.append(
                {
                    "niche": niche,
                    "status": "needs_config",
                    "message": message,
                }
            )
    else:
        database = DatabaseManager(settings)
        database.initialize()
        with database.session_scope() as session:
            for niche in niches:
                try:
                    youtube_results.append(
                        {
                            "niche": niche,
                            **await collect_youtube_trends(
                                session=session,
                                settings=settings,
                                account_id=args.account_id,
                                niche=niche,
                                region=args.region,
                                force=args.force,
                            ),
                        }
                    )
                except Exception as exc:
                    youtube_results.append({"niche": niche, "status": "error", "message": str(exc)})
    results["youtube_trends"] = youtube_results

    after = _counts(settings)
    readiness_after = _research_readiness(settings, account_id=args.account_id, niches=niches, region=args.region)
    results["after"] = after
    results["delta"] = {key: after[key] - before.get(key, 0) for key in after}
    results["research_readiness_after"] = readiness_after
    if after["yt_signals"] <= 0:
        results["status"] = "needs_youtube_signals"
        results["next_step"] = "Dodaj YOUTUBE_DATA_API_KEY w .env albo polacz YouTube OAuth, potem uruchom ten skrypt ponownie."
    elif after["gtrends_signals"] <= 0:
        results["status"] = "needs_google_trends"
        results["next_step"] = "Sprawdz pytrends/rate limit Google Trends i uruchom ponownie."
    else:
        results["status"] = "ready_for_topic_scoring"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real intelligence collectors into data/intelligence.db without seeding fake signals.",
    )
    parser.add_argument("--account-id", default="yt-finanse-pl")
    parser.add_argument("--niches", default="finanse osobiste,oszczedzanie,inwestowanie")
    parser.add_argument("--region", default="PL")
    parser.add_argument("--timeframe", default="now 7-d")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(_safe_payload(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
