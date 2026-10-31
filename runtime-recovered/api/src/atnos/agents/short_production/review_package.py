from __future__ import annotations

from pathlib import Path

from atnos.agents.short_production.models import QualityReview, ReviewPackage, ShortAssetPlan, UploadMetadata


class ReviewPackageBuilder:
    def build(
        self,
        *,
        plan: ShortAssetPlan,
        qa: QualityReview,
        short_file_path: Path,
        thumbnail_file_path: Path,
    ) -> ReviewPackage:
        title = self._title(plan)
        description = self._description(plan)
        tags = self._tags(plan)
        return ReviewPackage(
            channel_id=plan.channel_id,
            plan_id=plan.plan_id,
            short_file_path=str(short_file_path),
            thumbnail_file_path=str(thumbnail_file_path),
            qa=qa,
            upload_metadata=UploadMetadata(
                title=title,
                description=description,
                tags=tags,
                visibility="private",
            ),
            status="awaiting_approval",
        )

    def _title(self, plan: ShortAssetPlan) -> str:
        hook = plan.script.hook.strip()
        title = hook if len(hook) <= 80 else hook[:77].rstrip() + "..."
        return title or f"Short {plan.topic_id}"

    def _description(self, plan: ShortAssetPlan) -> str:
        lines = [
            plan.script.opening_line,
            "",
            "Ten short jest przygotowany jako private upload package do recznej akceptacji.",
            f"Plan: {plan.plan_id}",
            f"Topic: {plan.topic_id}",
        ]
        return "\n".join(line for line in lines if line is not None)

    def _tags(self, plan: ShortAssetPlan) -> list[str]:
        base = {
            plan.channel_id.replace("-", " "),
            "shorts",
            "ai",
            "edukacja",
            "faceless",
        }
        for word in plan.thumbnail.headline.casefold().split():
            if len(word) >= 3:
                base.add(word)
        return sorted(base)[:12]
