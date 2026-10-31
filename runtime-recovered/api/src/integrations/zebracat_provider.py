from __future__ import annotations

from dataclasses import dataclass
import logging
import os


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZebracatResult:
    status: str
    provider_job_id: str | None
    message: str
    requires_approval: bool
    payload: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "provider_job_id": self.provider_job_id,
            "message": self.message,
            "requires_approval": self.requires_approval,
            "payload": dict(self.payload),
        }


class ZebracatProvider:
    def __init__(self) -> None:
        self.api_key = os.getenv("ZEBRACAT_API_KEY", "").strip()
        self.enabled = os.getenv("ZEBRACAT_ENABLED", "false").strip().lower() == "true"
        self.default_aspect_ratio = os.getenv("ZEBRACAT_DEFAULT_ASPECT_RATIO", "9:16")
        self.default_resolution = os.getenv("ZEBRACAT_DEFAULT_RESOLUTION", "1080p")

    def generate_video_from_script(
        self,
        script: str,
        style: str,
        aspect_ratio: str | None = None,
    ) -> ZebracatResult:
        payload = {
            "script": script,
            "style": style,
            "aspect_ratio": aspect_ratio or self.default_aspect_ratio,
            "resolution": self.default_resolution,
        }
        if not self._can_call_api():
            return self._blocked_result("Zebracat is disabled or ZEBRACAT_API_KEY is empty.", payload)
        return self._scaffold_result("script_to_video_request_ready", payload)

    def generate_video_from_url(self, url: str) -> ZebracatResult:
        payload = {
            "url": url,
            "aspect_ratio": self.default_aspect_ratio,
            "resolution": self.default_resolution,
        }
        if not self._can_call_api():
            return self._blocked_result("Zebracat is disabled or ZEBRACAT_API_KEY is empty.", payload)
        return self._scaffold_result("url_to_video_request_ready", payload)

    def poll_or_wait_for_webhook(self, provider_job_id: str | None = None) -> ZebracatResult:
        payload = {"provider_job_id": provider_job_id}
        if not self._can_call_api():
            return self._blocked_result("Cannot poll Zebracat without enabled config and API key.", payload)
        return self._scaffold_result("polling_not_implemented", payload)

    def download_video(self, provider_job_id: str, output_path: str | None = None) -> ZebracatResult:
        payload = {"provider_job_id": provider_job_id, "output_path": output_path}
        if not self._can_call_api():
            return self._blocked_result("Cannot download Zebracat output without enabled config and API key.", payload)
        return self._scaffold_result("download_not_implemented", payload)

    def _can_call_api(self) -> bool:
        return self.enabled and bool(self.api_key)

    def _blocked_result(self, message: str, payload: dict[str, object]) -> ZebracatResult:
        result = ZebracatResult(
            status="blocked",
            provider_job_id=None,
            message=message,
            requires_approval=True,
            payload=payload,
        )
        logger.info("Zebracat result: %s", result.to_dict())
        return result

    def _scaffold_result(self, status: str, payload: dict[str, object]) -> ZebracatResult:
        result = ZebracatResult(
            status=status,
            provider_job_id=None,
            message="API call scaffold is ready, but live execution still needs explicit approval.",
            requires_approval=True,
            payload=payload,
        )
        logger.info("Zebracat result: %s", result.to_dict())
        return result
