from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from atnos.core.config import Settings
from atnos.services.secret_vault_service import SecretVaultService


def _default_env_file() -> str:
    configured = str(os.environ.get("ATNOS_ENV_FILE") or "").strip()
    if configured:
        return configured
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return str(appdata / "Atnos" / "config" / ".env")


def _load_credentials(path: Path) -> tuple[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    payload = raw.get("installed") or raw.get("web") or {}
    client_id = str(payload.get("client_id") or "").strip()
    client_secret = str(payload.get("client_secret") or "").strip()
    if not client_id:
        raise ValueError("Google OAuth JSON does not contain client_id.")
    if not client_secret:
        raise ValueError("Google OAuth JSON does not contain client_secret.")
    return client_id, client_secret


def _parse_env_file(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.exists():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def _upsert_env_lines(lines: list[str], updates: dict[str, str]) -> list[str]:
    remaining = dict(updates)
    result: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            result.append(line)
            continue
        key, _ = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in remaining:
            result.append(f"{normalized_key}={remaining.pop(normalized_key)}")
        else:
            result.append(line)
    for key, value in remaining.items():
        result.append(f"{key}={value}")
    return result


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import Google OAuth client credentials into the recovered Atnos runtime.",
    )
    parser.add_argument(
        "--credentials-json",
        required=True,
        help="Path to Google OAuth client credentials JSON downloaded from Google Cloud.",
    )
    parser.add_argument(
        "--env-file",
        default=_default_env_file(),
        help="External runtime .env file. Defaults to ATNOS_ENV_FILE or %%APPDATA%%/Atnos/config/.env.",
    )
    parser.add_argument(
        "--redirect-base-url",
        default="http://127.0.0.1:8000",
        help="OAuth redirect base URL used by the local Atnos API.",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Validate and print the changes without writing them.",
    )
    args = parser.parse_args()

    credentials_path = Path(args.credentials_json).expanduser().resolve()
    if not credentials_path.exists():
        raise SystemExit(f"Credentials JSON was not found: {credentials_path}")
    if not credentials_path.is_file():
        raise SystemExit(f"Credentials path must be a file: {credentials_path}")

    env_path = Path(args.env_file).expanduser().resolve()
    client_id, client_secret = _load_credentials(credentials_path)

    settings = Settings(_env_file=str(env_path))
    settings.ensure_directories()
    secret_vault = SecretVaultService(settings)

    existing_lines, env_values = _parse_env_file(env_path)
    existing_secret_ref = env_values.get("OAUTH_GOOGLE_CLIENT_SECRET_REF")
    storage_backend, secret_ref = secret_vault.rotate_secret(
        secret_value=client_secret,
        existing_secret_ref=existing_secret_ref,
    )
    if storage_backend != "os_keyring" or not secret_ref:
        raise SystemExit(
            "Secure secret storage is not available. Google OAuth client secret was not imported."
        )

    updates = {
        "OAUTH_ENABLED": "true",
        "OAUTH_REDIRECT_BASE_URL": args.redirect_base_url,
        "OAUTH_GOOGLE_CLIENT_ID": client_id,
        "OAUTH_GOOGLE_CLIENT_SECRET_REF": secret_ref,
    }

    next_lines = _upsert_env_lines(existing_lines, updates)
    preview = {
        "credentials_json": str(credentials_path),
        "env_file": str(env_path),
        "oauth_enabled": True,
        "redirect_base_url": args.redirect_base_url,
        "google_client_id": _mask(client_id),
        "google_client_secret_ref": secret_ref,
    }

    if args.print_only:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    print(json.dumps(preview, ensure_ascii=False, indent=2))
    print("Next steps:")
    print("1. Restart the recovered Atnos API runtime.")
    print("2. Verify GET /oauth/connectors returns Google connectors as configured.")
    print("3. Start OAuth from /oauth/gmail-readonly/start or /oauth/google-drive-readonly/start.")
    print("4. Add the redirect URIs to Google Cloud if they are not already present:")
    print("   - http://127.0.0.1:8000/oauth/gmail-readonly/callback")
    print("   - http://127.0.0.1:8000/oauth/google-drive-readonly/callback")
    print("   - http://127.0.0.1:8000/oauth/google-calendar-readonly/callback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
