from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, rows_to_dicts, utc_now_iso
from atnos.production.distribution_planner import (
    DistributionPlanError,
    build_distribution_plan,
    build_distribution_release_package,
)
from atnos.repositories.account_credential_repository import AccountCredentialRepository


class CrossPlatformDistributionCockpitService:
    """Operator cockpit for supervised YouTube/TikTok/Instagram distribution."""

    PLATFORM_LABELS = {
        "youtube_shorts": "YouTube Shorts",
        "tiktok": "TikTok",
        "instagram_reels": "Instagram Reels",
    }

    def __init__(self, settings: Settings, *, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session

    def status(self, *, account_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        """Return latest distribution state for a channel/project."""
        jobs = self._latest_jobs(account_id=account_id, limit=limit)
        latest_job = jobs[0] if jobs else None
        latest_plan = self._safe_plan(str(latest_job["id"])) if latest_job else None
        platform_accounts = self._platform_accounts()
        platform_status = self._platform_status(latest_plan=latest_plan, platform_accounts=platform_accounts)
        ready_packages = [
            platform
            for platform in platform_status
            if platform.get("release_package_ready") or platform.get("api_private_ready")
        ]
        blockers = list(
            dict.fromkeys(
                blocker
                for platform in platform_status
                for blocker in (platform.get("blockers") or [])
            )
        )
        if not jobs:
            blockers.append("no_production_jobs")
        return {
            "generated_at": utc_now_iso(),
            "account_id": account_id,
            "status": self._overall_status(jobs=jobs, platform_status=platform_status),
            "latest_job": latest_job,
            "latest_plan": latest_plan,
            "jobs": jobs,
            "platform_accounts": platform_accounts,
            "platforms": platform_status,
            "ready_package_count": len(ready_packages),
            "blockers": blockers,
            "capabilities": {
                "can_prepare_release_packages": bool(latest_plan and latest_plan.get("export_ready")),
                "can_upload_youtube_private_via_api": bool(
                    latest_plan
                    and any(
                        item.get("platform") == "youtube_shorts" and item.get("private_upload_ready")
                        for item in latest_plan.get("platforms") or []
                    )
                ),
                "can_upload_tiktok_automatically": False,
                "can_upload_instagram_automatically": False,
                "requires_owner_review": True,
            },
            "next_actions": self._next_actions(jobs=jobs, latest_plan=latest_plan, platform_status=platform_status),
            "safety": {
                "public_autopublish": "blocked",
                "owner_approval_required": True,
                "tiktok_instagram_mode": "manual_or_supervised_browser_later",
                "youtube_mode": "api_private_upload_after_review",
            },
        }

    def create_latest_release_package(
        self,
        *,
        account_id: str | None = None,
        platforms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Write release packages for the latest package-ready production job."""
        jobs = self._latest_jobs(account_id=account_id, limit=10)
        if not jobs:
            return {
                "status": "blocked",
                "reason": "No production jobs found for distribution.",
                "account_id": account_id,
                "packages": [],
                "blocked": [],
                "cockpit": self.status(account_id=account_id),
            }
        for job in jobs:
            plan = self._safe_plan(str(job["id"]))
            if plan and bool(plan.get("export_ready")):
                package = build_distribution_release_package(
                    self.settings,
                    job_id=str(job["id"]),
                    platforms=platforms,
                )
                return {
                    "status": package.get("status"),
                    "job_id": job["id"],
                    "account_id": job.get("account_id"),
                    "package": package,
                    "cockpit": self.status(account_id=account_id),
                }
        return {
            "status": "blocked",
            "reason": "No export-ready production job found.",
            "account_id": account_id,
            "packages": [],
            "blocked": [],
            "cockpit": self.status(account_id=account_id),
        }

    def _latest_jobs(self, *, account_id: str | None, limit: int) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 5), 25))
        with connect(self.settings) as db:
            if account_id:
                rows = db.execute(
                    """
                    SELECT id, account_id, topic_id, status, current_step, artifact_dir,
                           qa_report_path, upload_package_path, youtube_video_id, youtube_url,
                           created_at, updated_at
                    FROM pipeline_jobs
                    WHERE account_id = ?
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (account_id, safe_limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT id, account_id, topic_id, status, current_step, artifact_dir,
                           qa_report_path, upload_package_path, youtube_video_id, youtube_url,
                           created_at, updated_at
                    FROM pipeline_jobs
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
        return rows_to_dicts(rows)

    def _safe_plan(self, job_id: str) -> dict[str, Any] | None:
        try:
            return build_distribution_plan(self.settings, job_id=job_id)
        except DistributionPlanError:
            return None

    def _platform_accounts(self) -> dict[str, dict[str, Any]]:
        result = {
            "youtube_shorts": {"connected": False, "status": "unknown", "accounts": []},
            "tiktok": {"connected": False, "status": "missing", "accounts": []},
            "instagram_reels": {"connected": False, "status": "missing", "accounts": []},
        }
        if self.session is None:
            return result

        accounts = AccountCredentialRepository(self.session).list_all()
        for account in accounts:
            platform = self._platform_for_account(str(account.provider_key or ""), str(account.linked_integration_key or ""))
            if platform is None:
                continue
            item = {
                "id": account.id,
                "label": account.label,
                "provider_key": account.provider_key,
                "account_identifier": account.account_identifier,
                "status": account.status,
                "risk_level": account.risk_level,
                "allowed_capabilities": list(account.allowed_capabilities_json or []),
                "has_secret": bool(account.secret_ref),
            }
            result[platform]["accounts"].append(item)
            if account.status == "configured" and bool(account.secret_ref):
                result[platform]["connected"] = True
                result[platform]["status"] = "ready"
            elif result[platform]["status"] != "ready":
                result[platform]["status"] = "limited"
        return result

    @staticmethod
    def _platform_for_account(provider_key: str, linked_integration_key: str) -> str | None:
        raw = f"{provider_key} {linked_integration_key}".lower().replace("-", "_")
        if "tiktok" in raw:
            return "tiktok"
        if "instagram" in raw or "meta" in raw:
            return "instagram_reels"
        if "youtube" in raw:
            return "youtube_shorts"
        return None

    def _platform_status(
        self,
        *,
        latest_plan: dict[str, Any] | None,
        platform_accounts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        plan_by_platform = {
            str(platform.get("platform")): platform
            for platform in ((latest_plan or {}).get("platforms") or [])
            if isinstance(platform, dict)
        }
        platforms: list[dict[str, Any]] = []
        for platform_key, label in self.PLATFORM_LABELS.items():
            plan = plan_by_platform.get(platform_key, {})
            account = platform_accounts.get(platform_key, {"connected": False, "status": "missing", "accounts": []})
            requires_manual = bool(plan.get("requires_manual_upload"))
            release_package_ready = bool(plan.get("export_ready") and (requires_manual or platform_key == "youtube_shorts"))
            blockers = list(plan.get("blockers") or [])
            if platform_key in {"tiktok", "instagram_reels"} and not account.get("connected"):
                blockers.append(f"{platform_key}_trusted_account_missing")
            platforms.append(
                {
                    "platform": platform_key,
                    "label": label,
                    "status": plan.get("status") or ("no_job" if not latest_plan else "blocked"),
                    "connector_status": plan.get("connector_status") or account.get("status") or "missing",
                    "account_connected": bool(account.get("connected")),
                    "account_status": account.get("status"),
                    "account_count": len(account.get("accounts") or []),
                    "export_ready": bool(plan.get("export_ready")),
                    "release_package_ready": release_package_ready,
                    "api_private_ready": bool(plan.get("private_upload_ready")) if platform_key == "youtube_shorts" else False,
                    "automatic_upload_ready": False if platform_key != "youtube_shorts" else bool(plan.get("private_upload_ready")),
                    "requires_manual_upload": requires_manual,
                    "requires_owner_approval": True,
                    "blockers": list(dict.fromkeys(blockers)),
                    "next_actions": list(plan.get("next_actions") or []),
                    "artifact_paths": plan.get("artifact_paths") or {},
                    "metadata": plan.get("metadata") or {},
                }
            )
        return platforms

    @staticmethod
    def _overall_status(*, jobs: list[dict[str, Any]], platform_status: list[dict[str, Any]]) -> str:
        if not jobs:
            return "no_jobs"
        if any(platform.get("api_private_ready") for platform in platform_status):
            return "youtube_private_ready"
        if any(platform.get("release_package_ready") for platform in platform_status):
            return "release_package_ready"
        if any(platform.get("export_ready") for platform in platform_status):
            return "export_ready"
        return "blocked"

    @staticmethod
    def _next_actions(
        *,
        jobs: list[dict[str, Any]],
        latest_plan: dict[str, Any] | None,
        platform_status: list[dict[str, Any]],
    ) -> list[str]:
        if not jobs:
            return ["Generate a QA-passed production job first."]
        if not latest_plan:
            return ["Fix latest job artifacts before distribution planning."]
        actions: list[str] = []
        if latest_plan.get("export_ready"):
            actions.append("Prepare release packages for YouTube, TikTok and Instagram after owner review.")
        else:
            actions.append("Fix QA/artifact blockers before preparing release packages.")
        missing = [
            platform["label"]
            for platform in platform_status
            if platform["platform"] in {"tiktok", "instagram_reels"} and not platform.get("account_connected")
        ]
        if missing:
            actions.append("Connect trusted accounts for supervised workflows: " + ", ".join(missing))
        actions.append("Keep public autopublish disabled until platform-specific approvals are implemented.")
        return actions
