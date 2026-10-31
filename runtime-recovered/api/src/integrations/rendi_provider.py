from __future__ import annotations

from dataclasses import dataclass
import logging
import os


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RendiResult:
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


class RendiProvider:
    """Approval-gated scaffold for external Rendi FFmpeg processing."""

    def __init__(self) -> None:
        self.api_key = os.getenv("RENDI_API_KEY", "").strip()
        self.enabled = os.getenv("RENDI_ENABLED", "false").strip().lower() == "true"
        self.mcp_url = os.getenv("RENDI_MCP_URL", "https://www.rendi.dev/docs/mcp").strip()

    def build_ffmpeg_job_preview(
        self,
        *,
        input_url: str,
        ffmpeg_args: list[str],
        output_format: str = "mp4",
    ) -> RendiResult:
        payload: dict[str, object] = {
            "input_url": input_url,
            "ffmpeg_args": list(ffmpeg_args),
            "output_format": output_format,
            "mcp_url": self.mcp_url,
        }
        if not self._can_call_api():
            return self._blocked_result("Rendi is disabled or RENDI_API_KEY is empty.", payload)
        return self._scaffold_result("ffmpeg_job_preview_ready", payload)

    def poll_job(self, provider_job_id: str) -> RendiResult:
        payload = {"provider_job_id": provider_job_id}
        if not self._can_call_api():
            return self._blocked_result("Cannot poll Rendi without enabled config and API key.", payload)
        return self._scaffold_result("polling_not_implemented", payload)

    def download_output(self, provider_job_id: str, output_path: str | None = None) -> RendiResult:
        payload = {"provider_job_id": provider_job_id, "output_path": output_path}
        if not self._can_call_api():
            return self._blocked_result("Cannot download Rendi output without enabled config and API key.", payload)
        return self._scaffold_result("download_not_implemented", payload)

    def _can_call_api(self) -> bool:
        return self.enabled and bool(self.api_key)

    def _blocked_result(self, message: str, payload: dict[str, object]) -> RendiResult:
        result = RendiResult(
            status="blocked",
            provider_job_id=None,
            message=message,
            requires_approval=True,
            payload=payload,
        )
        logger.info("Rendi result: %s", result.to_dict())
        return result

    def _scaffold_result(self, status: str, payload: dict[str, object]) -> RendiResult:
        result = RendiResult(
            status=status,
            provider_job_id=None,
            message="Rendi API call scaffold is ready, but live execution still needs explicit approval.",
            requires_approval=True,
            payload=payload,
        )
        logger.info("Rendi result: %s", result.to_dict())
        return result
