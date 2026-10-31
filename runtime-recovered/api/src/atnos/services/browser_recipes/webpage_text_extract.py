from __future__ import annotations

from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService


MAX_TEXT_CHARS = 50_000


def run_webpage_text_extract(
    runner: BrowserRecipeRunnerService,
    payload: dict[str, object],
) -> dict[str, object]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("webpage_text_extract requires url.")
    selector = str(payload.get("selector") or "body").strip() or "body"
    conversation_id = (
        int(payload["conversation_id"])
        if payload.get("conversation_id") not in {None, ""}
        else None
    )
    result = runner.run(
        {
            "conversation_id": conversation_id,
            "recipe_name": "webpage_text_extract",
            "steps": [
                {"type": "navigate", "url": url},
                {"type": "extract", "selector": selector},
            ],
            "allowed_domains": payload.get("allowed_domains") or [],
            "headless": payload.get("headless"),
            "action_timeout_ms": payload.get("action_timeout_ms"),
            "total_timeout_ms": payload.get("total_timeout_ms"),
            "retry_limit": payload.get("retry_limit"),
        }
    )
    extracted = ""
    for item in result.get("results", []):
        if isinstance(item, dict) and item.get("type") == "extract":
            extracted = str(item.get("value") or "")
            break
    truncated = len(extracted) > MAX_TEXT_CHARS
    text = extracted[:MAX_TEXT_CHARS]
    return {
        "ok": True,
        "url": url,
        "selector": selector,
        "text": text,
        "truncated": truncated,
        "notice": "Text was truncated to 50000 characters." if truncated else None,
        "browser_result": result,
    }
