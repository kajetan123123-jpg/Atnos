from __future__ import annotations

from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService


def run_link_collect(
    runner: BrowserRecipeRunnerService,
    payload: dict[str, object],
) -> dict[str, object]:
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("link_collect requires url.")
    selector = str(payload.get("selector") or "a[href]").strip() or "a[href]"
    conversation_id = (
        int(payload["conversation_id"])
        if payload.get("conversation_id") not in {None, ""}
        else None
    )
    result = runner.run(
        {
            "conversation_id": conversation_id,
            "recipe_name": "link_collect",
            "steps": [
                {"type": "navigate", "url": url},
                {"type": "collect_links", "selector": selector},
            ],
            "allowed_domains": payload.get("allowed_domains") or [],
            "headless": payload.get("headless"),
            "action_timeout_ms": payload.get("action_timeout_ms"),
            "total_timeout_ms": payload.get("total_timeout_ms"),
            "retry_limit": payload.get("retry_limit"),
        }
    )
    collected: list[dict[str, str]] = []
    for item in result.get("results", []):
        if isinstance(item, dict) and item.get("type") == "collect_links":
            values = item.get("value")
            if isinstance(values, list):
                collected = [
                    {
                        "text": str(entry.get("text") or ""),
                        "url": str(entry.get("url") or ""),
                    }
                    for entry in values
                    if isinstance(entry, dict)
                ]
            break
    return {
        "ok": True,
        "url": url,
        "selector": selector,
        "links": collected,
        "browser_result": result,
    }
