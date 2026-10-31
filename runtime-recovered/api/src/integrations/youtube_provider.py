from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class YouTubePublishPreview:
    title: str
    description: str
    video_path: str
    privacy_status: str = "private"
    requires_approval: bool = True


class YouTubeProvider:
    """Official publishing target scaffold. Execution stays approval-gated."""

    def create_upload_preview(
        self,
        *,
        video_path: str,
        title: str,
        description: str,
        privacy_status: str = "private",
    ) -> YouTubePublishPreview:
        return YouTubePublishPreview(
            video_path=video_path,
            title=title,
            description=description,
            privacy_status=privacy_status,
            requires_approval=True,
        )

    def fetch_analytics_summary(self, *, video_id: str) -> dict[str, object]:
        return {
            "video_id": video_id,
            "status": "not_implemented",
            "requires_api_key": True,
            "notes": "Wire this to the existing YouTube connector after OAuth and quota checks.",
        }
