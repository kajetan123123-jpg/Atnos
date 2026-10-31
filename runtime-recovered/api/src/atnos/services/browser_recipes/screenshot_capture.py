from __future__ import annotations

from pathlib import Path

from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService
from atnos.services.output_artifact_service import OutputArtifactService


def run_screenshot_capture(
    runner: BrowserRecipeRunnerService,
    payload: dict[str, object],
) -> dict[str, object]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("screenshot_capture requires url.")
    conversation_id = (
        int(payload["conversation_id"])
        if payload.get("conversation_id") not in {None, ""}
        else None
    )
    artifacts = OutputArtifactService(runner.settings)
    target_path = artifacts.create_output_path(
        conversation_id=conversation_id,
        extension=".png",
    )
    result = runner.run(
        {
            "conversation_id": conversation_id,
            "recipe_name": "screenshot_capture",
            "steps": [
                {"type": "navigate", "url": url},
                {
                    "type": "screenshot",
                    "path": target_path.as_posix(),
                    "fullPage": bool(payload.get("full_page")),
                },
            ],
            "allowed_domains": payload.get("allowed_domains") or [],
            "headless": payload.get("headless"),
            "action_timeout_ms": payload.get("action_timeout_ms"),
            "total_timeout_ms": payload.get("total_timeout_ms"),
            "retry_limit": payload.get("retry_limit"),
        }
    )
    descriptor = artifacts.finalize_output(
        absolute_path=Path(target_path),
        filename=target_path.name,
        media_type="image/png",
    )
    return {
        "ok": True,
        "url": url,
        "attachment": {
            "filename": descriptor.filename,
            "storage_path": descriptor.storage_path,
            "mime": descriptor.media_type,
            "size_bytes": descriptor.size_bytes,
        },
        "browser_result": result,
    }
