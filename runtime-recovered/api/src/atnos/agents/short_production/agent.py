from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.agents.short_production.models import (
    ApprovedHook,
    PlanRequest,
    ProductionFiles,
    QualityReview,
    ReviewPackage,
    ShortAssetPlan,
)
from atnos.agents.short_production.planner import ShortProductionPlanner
from atnos.agents.short_production.qa import QualityReviewService
from atnos.agents.short_production.review_package import ReviewPackageBuilder
from atnos.core.config import Settings


class ShortProductionError(RuntimeError):
    pass


class ShortProductionAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        planner: ShortProductionPlanner | None = None,
        qa_service: QualityReviewService | None = None,
        review_builder: ReviewPackageBuilder | None = None,
    ) -> None:
        self.settings = settings
        self.planner = planner or ShortProductionPlanner(settings)
        self.qa_service = qa_service or QualityReviewService(settings)
        self.review_builder = review_builder or ReviewPackageBuilder()

    def create_plan(self, request: PlanRequest) -> ShortAssetPlan:
        topic = self._load_approved_topic(request.channel_id, request.topic_id)
        self._validate_hook(request.approved_hook, topic)

        plan_seed_dir = self.production_root(request.channel_id) / "pending"
        plan = self.planner.build_plan(
            request,
            topic_title=str(topic["title"]),
            output_dir=plan_seed_dir,
            style_guide=self._read_style_guide(request.channel_id),
        )
        plan_dir = self.plan_dir(request.channel_id, plan.plan_id)
        assets_dir = plan_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        plan = plan.model_copy(
            update={
                "narration": plan.narration.model_copy(
                    update={"output_file": str(assets_dir / Path(plan.narration.output_file).name)}
                )
            }
        )
        self._write_json(plan_dir / "plan.json", plan.model_dump(mode="json"))
        self._write_json(
            plan_dir / "topic.json",
            {
                "id": topic.get("id"),
                "title": topic.get("title"),
                "viral_score": topic.get("viral_score"),
                "rationale": topic.get("rationale"),
                "package_id": topic.get("package_id"),
            },
        )
        self._log(plan_dir, "[SHORT_PRODUCTION] planned")
        return plan

    def run_qa(self, plan_id: str, files: ProductionFiles) -> QualityReview:
        channel_id, plan_dir = self._find_plan_dir(plan_id)
        plan = self._load_plan(plan_dir)
        qa = self.qa_service.review(plan, files)
        self._write_json(plan_dir / "production_files.json", files.model_dump(mode="json"))
        self._write_json(plan_dir / "qa.json", qa.model_dump(mode="json"))
        self._log(plan_dir, f"[SHORT_PRODUCTION] qa {qa.recommendation}")
        return qa

    def build_review_package(self, plan_id: str) -> ReviewPackage:
        _channel_id, plan_dir = self._find_plan_dir(plan_id)
        plan = self._load_plan(plan_dir)
        qa_path = plan_dir / "qa.json"
        files_path = plan_dir / "production_files.json"
        if not qa_path.exists():
            raise ShortProductionError(f"QA is missing for plan: {plan_id}")
        if not files_path.exists():
            raise ShortProductionError(f"Production files are missing for plan: {plan_id}")

        qa = QualityReview.model_validate(json.loads(qa_path.read_text(encoding="utf-8")))
        files = ProductionFiles.model_validate(json.loads(files_path.read_text(encoding="utf-8")))
        package = self.review_builder.build(
            plan=plan,
            qa=qa,
            short_file_path=Path(files.short_file_path),
            thumbnail_file_path=Path(files.thumbnail_file_path),
        )
        self._write_json(plan_dir / "review_package.json", package.model_dump(mode="json"))
        self._log(plan_dir, f"[SHORT_PRODUCTION] review_package {package.review_id}")
        return package

    def approve_for_upload(self, review_id: str) -> ReviewPackage:
        plan_dir = self._find_review_dir(review_id)
        package_path = plan_dir / "review_package.json"
        package = ReviewPackage.model_validate(json.loads(package_path.read_text(encoding="utf-8")))
        if package.upload_metadata.visibility != "private":
            raise ShortProductionError("ReviewPackage visibility must remain private.")
        updated = package.model_copy(update={"status": "approved_for_upload"})
        self._write_json(package_path, updated.model_dump(mode="json"))
        self._log(plan_dir, f"[SHORT_PRODUCTION] approved_for_upload {review_id}")
        return updated

    def latest_plan(self, channel_id: str | None = None) -> ShortAssetPlan | None:
        plans: list[tuple[float, ShortAssetPlan]] = []
        roots = [self.production_root(channel_id)] if channel_id else self._all_production_roots()
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*/plan.json"):
                try:
                    plan = ShortAssetPlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
                    plans.append((path.stat().st_mtime, plan))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        if not plans:
            return None
        return sorted(plans, key=lambda item: item[0], reverse=True)[0][1]

    def latest_review_package(self, channel_id: str | None = None) -> ReviewPackage | None:
        packages: list[tuple[float, ReviewPackage]] = []
        roots = [self.production_root(channel_id)] if channel_id else self._all_production_roots()
        for root in roots:
            if not root.exists():
                continue
            for path in root.glob("*/review_package.json"):
                try:
                    package = ReviewPackage.model_validate(json.loads(path.read_text(encoding="utf-8")))
                    packages.append((path.stat().st_mtime, package))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        if not packages:
            return None
        return sorted(packages, key=lambda item: item[0], reverse=True)[0][1]

    def get_approved_topic(self, channel_id: str, topic_id: str) -> dict[str, Any]:
        return self._load_approved_topic(channel_id, topic_id)

    def production_root(self, channel_id: str) -> Path:
        safe_channel = _safe_id(channel_id)
        return (self.settings.data_dir / "channels" / safe_channel / "production").resolve()

    def plan_dir(self, channel_id: str, plan_id: str) -> Path:
        return self.production_root(channel_id) / _safe_id(plan_id)

    def _load_approved_topic(self, channel_id: str, topic_id: str) -> dict[str, Any]:
        path = self.settings.data_dir / "channels" / _safe_id(channel_id) / "topic_queue.json"
        if not path.exists():
            raise ShortProductionError(f"Topic queue not found: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ShortProductionError(f"Invalid topic queue shape: {path}")
        for item in raw:
            if isinstance(item, dict) and item.get("id") == topic_id:
                if item.get("status") != "approved":
                    raise ShortProductionError(f"Topic is not approved: {topic_id}")
                return item
        raise ShortProductionError(f"Approved topic not found: {topic_id}")

    def _validate_hook(self, hook: ApprovedHook, topic: dict[str, Any]) -> None:
        hooks = topic.get("hooks") if isinstance(topic.get("hooks"), list) else []
        if not hooks:
            return
        for item in hooks:
            if not isinstance(item, dict):
                continue
            if item.get("text") == hook.text and item.get("opening_line") == hook.opening_line:
                return
        raise ShortProductionError("Approved hook does not match the approved topic queue.")

    def _read_style_guide(self, channel_id: str) -> str | None:
        channel_ops_path = self.settings.data_dir / "channel_ops" / "channels.json"
        if not channel_ops_path.exists():
            return None
        try:
            raw = json.loads(channel_ops_path.read_text(encoding="utf-8"))
            channel = raw.get(_safe_id(channel_id)) if isinstance(raw, dict) else None
            style_path = channel.get("style_guide_path") if isinstance(channel, dict) else None
            if style_path and Path(style_path).exists():
                return Path(style_path).read_text(encoding="utf-8")[:6000]
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _find_plan_dir(self, plan_id: str) -> tuple[str, Path]:
        wanted = _safe_id(plan_id)
        channels_root = self.settings.data_dir / "channels"
        if not channels_root.exists():
            raise ShortProductionError(f"No production channels directory: {channels_root}")
        for channel_dir in channels_root.iterdir():
            if not channel_dir.is_dir():
                continue
            plan_dir = channel_dir / "production" / wanted
            if (plan_dir / "plan.json").exists():
                return channel_dir.name, plan_dir
        raise ShortProductionError(f"Plan not found: {plan_id}")

    def _find_review_dir(self, review_id: str) -> Path:
        channels_root = self.settings.data_dir / "channels"
        if not channels_root.exists():
            raise ShortProductionError(f"No production channels directory: {channels_root}")
        for path in channels_root.glob("*/production/*/review_package.json"):
            try:
                package = ReviewPackage.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if package.review_id == review_id:
                return path.parent
        raise ShortProductionError(f"Review package not found: {review_id}")

    def _load_plan(self, plan_dir: Path) -> ShortAssetPlan:
        return ShortAssetPlan.model_validate(json.loads((plan_dir / "plan.json").read_text(encoding="utf-8")))

    def _all_production_roots(self) -> list[Path]:
        channels_root = self.settings.data_dir / "channels"
        if not channels_root.exists():
            return []
        return [path / "production" for path in channels_root.iterdir() if path.is_dir()]

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _log(self, plan_dir: Path, message: str) -> None:
        plan_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        (plan_dir / "production.log").open("a", encoding="utf-8").write(f"{timestamp} {message}\n")


def _safe_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if not normalized or any(char not in allowed for char in normalized):
        raise ShortProductionError(f"Unsafe id: {value}")
    return normalized
