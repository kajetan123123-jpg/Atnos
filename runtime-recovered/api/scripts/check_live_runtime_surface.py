from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class RuntimeEndpointCheck:
    label: str
    method: str
    path: str
    expected_statuses: tuple[int, ...] = (200,)


REQUIRED_RUNTIME_ENDPOINTS: tuple[RuntimeEndpointCheck, ...] = (
    RuntimeEndpointCheck("health", "GET", "/health"),
    RuntimeEndpointCheck("production readiness", "GET", "/api/production/readiness?live=false"),
    RuntimeEndpointCheck("real action preflight", "GET", "/preflight/shorts-real-actions"),
    RuntimeEndpointCheck("SMTP mail status", "GET", "/connectors/smtp-mail/status"),
    RuntimeEndpointCheck("Gmail status", "GET", "/connectors/gmail/status"),
    RuntimeEndpointCheck("YouTube status", "GET", "/connectors/youtube/status"),
    RuntimeEndpointCheck("topic queue", "GET", "/api/topics/queue?account_id=runtime-smoke"),
    RuntimeEndpointCheck("models status", "GET", "/api/models/status"),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check whether the live Atnos API process exposes the current runtime surface. "
            "This catches stale uvicorn processes before real mail/upload tests."
        )
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--include-body",
        action="store_true",
        help="Include compact response bodies. Off by default to avoid leaking account identifiers.",
    )
    args = parser.parse_args()

    results = [
        _check_endpoint(
            args.base_url.rstrip("/"),
            endpoint,
            timeout_s=args.timeout,
            include_body=bool(args.include_body),
        )
        for endpoint in REQUIRED_RUNTIME_ENDPOINTS
    ]
    missing = [item for item in results if item["status"] in {"missing", "unreachable", "unexpected_status"}]
    stale = any(item["path"] == "/connectors/smtp-mail/status" and item["http_status"] == 404 for item in results)
    payload = {
        "base_url": args.base_url.rstrip("/"),
        "ready": not missing,
        "stale_runtime_suspected": stale,
        "missing_count": len(missing),
        "results": results,
        "next_action": _next_action(stale=stale, missing=missing),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ready"] else 2


def _check_endpoint(
    base_url: str,
    endpoint: RuntimeEndpointCheck,
    *,
    timeout_s: float,
    include_body: bool,
) -> dict[str, Any]:
    url = f"{base_url}{endpoint.path}"
    request = Request(url, method=endpoint.method)
    try:
        with urlopen(request, timeout=timeout_s) as response:
            http_status = int(response.status)
            body = response.read(4096).decode("utf-8", errors="replace")
    except HTTPError as exc:
        http_status = int(exc.code)
        body = exc.read(4096).decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        return {
            "label": endpoint.label,
            "method": endpoint.method,
            "path": endpoint.path,
            "status": "unreachable",
            "http_status": None,
            "message": f"{type(exc).__name__}: {exc}",
        }

    if http_status == 404:
        status = "missing"
    elif http_status in endpoint.expected_statuses:
        status = "ready"
    else:
        status = "unexpected_status"
    return {
        "label": endpoint.label,
        "method": endpoint.method,
        "path": endpoint.path,
        "status": status,
        "http_status": http_status,
        "message": _safe_message(status=status, http_status=http_status, body=body, include_body=include_body),
    }


def _safe_message(*, status: str, http_status: int, body: str, include_body: bool) -> str:
    if include_body:
        return _compact_body(body)
    if status == "ready":
        return f"HTTP {http_status}"
    if status == "missing":
        return "Endpoint returned 404."
    return f"HTTP {http_status}. Re-run with --include-body for diagnostic body."


def _compact_body(body: str) -> str:
    stripped = " ".join(str(body or "").split())
    if len(stripped) <= 260:
        return stripped
    return f"{stripped[:257]}..."


def _next_action(*, stale: bool, missing: list[dict[str, Any]]) -> str:
    if stale:
        return (
            "Live backend is probably stale. Stop the old uvicorn/python process on this port "
            "and restart with: python -m uvicorn atnos.api.main:create_app --factory --host 127.0.0.1 --port 8000"
        )
    if missing:
        labels = ", ".join(str(item["label"]) for item in missing[:5])
        return f"Fix missing live API endpoints before real mail/upload tests: {labels}."
    return "Live runtime surface is current. Continue with connector probes/readiness."


if __name__ == "__main__":
    raise SystemExit(main())
