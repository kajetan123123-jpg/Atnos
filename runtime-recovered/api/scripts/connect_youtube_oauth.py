from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib import request as urlrequest
import webbrowser


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Google client_secret.json and start the YouTube Studio OAuth consent flow."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Running Atnos API base URL.")
    parser.add_argument(
        "--client-secret-path",
        default=None,
        help="Optional path to Google OAuth client_secret.json. Defaults to backend settings.",
    )
    parser.add_argument(
        "--redirect-base-url",
        default=DEFAULT_BASE_URL,
        help="OAuth redirect base URL registered in Google Cloud.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Print authorization URL without opening browser.")
    args = parser.parse_args()

    payload: dict[str, object] = {
        "redirect_base_url": args.redirect_base_url,
        "enable_oauth": True,
    }
    if args.client_secret_path:
        payload["client_secret_path"] = str(Path(args.client_secret_path).expanduser().resolve())

    endpoint = args.base_url.rstrip("/") + "/oauth/youtube-studio/connect"
    try:
        result = _post_json(endpoint, payload)
    except Exception as exc:
        print(f"[youtube-oauth] Cannot reach Atnos API at {endpoint}: {exc}", file=sys.stderr)
        print("[youtube-oauth] Start backend first, then run this script again.", file=sys.stderr)
        return 2

    print(json.dumps(_redact_public_payload(result), ensure_ascii=False, indent=2))
    status = str(result.get("status") or "")
    if status != "authorization_started":
        print("[youtube-oauth] OAuth was not started. Follow client_secret_status.next_steps.", file=sys.stderr)
        return 1

    authorization_url = str(result.get("authorization_url") or "").strip()
    if not authorization_url:
        print("[youtube-oauth] Authorization URL is missing.", file=sys.stderr)
        return 1

    if args.no_browser:
        print(f"[youtube-oauth] Open this URL in browser:\n{authorization_url}")
    else:
        print("[youtube-oauth] Opening Google consent screen in browser...")
        webbrowser.open(authorization_url)
    return 0


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Atnos API returned a non-object JSON response.")
    return parsed


def _redact_public_payload(payload: dict[str, object]) -> dict[str, object]:
    """Keep output useful while avoiding accidental secret values."""
    serialized = json.loads(json.dumps(payload))
    if isinstance(serialized, dict):
        _redact_recursive(serialized)
    return serialized


def _redact_recursive(value: object) -> None:
    if isinstance(value, dict):
        for key in list(value):
            lowered = str(key).lower()
            if "secret" in lowered or "token" in lowered:
                value[key] = "[redacted]" if value[key] else value[key]
            else:
                _redact_recursive(value[key])
    elif isinstance(value, list):
        for item in value:
            _redact_recursive(item)


if __name__ == "__main__":
    raise SystemExit(main())
