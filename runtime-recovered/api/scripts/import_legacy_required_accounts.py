from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path(
    os.environ.get(
        "SQLITE_FILE",
        r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data\atnos.db",
    )
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class RequiredAccount:
    label: str
    provider_key: str
    account_identifier: str
    secret_type: str
    linked_integration_key: str
    login_identifier: str | None
    login_url: str
    validation_url: str
    domains: list[str]
    purpose: str
    capabilities: list[str]
    notes: str
    status: str = "metadata_only"
    storage_backend: str = "metadata_only"
    risk_level: str = "high"


REQUIRED_ACCOUNTS: tuple[RequiredAccount, ...] = (
    RequiredAccount(
        label="Ideogram",
        provider_key="ideogram",
        account_identifier="ideogram-browser-session",
        secret_type="session_cookie_later",
        linked_integration_key="ideogram_browser_session",
        login_identifier=None,
        login_url="https://ideogram.ai/",
        validation_url="https://ideogram.ai/",
        domains=["ideogram.ai"],
        purpose="Generowanie i referencje wizualne dla shortów oraz synthetic model content.",
        capabilities=["trusted_login", "browser_workflow", "asset_generation", "read_metadata"],
        notes="Wyciągnięte z legacy profilu browsera Jarvis/Ideogram. Sekret nie jest kopiowany; użyj zapisanej sesji albo dodaj hasło w Vault.",
    ),
    RequiredAccount(
        label="Hugging Face",
        provider_key="huggingface",
        account_identifier="hf-model-download",
        secret_type="api_token_later",
        linked_integration_key="huggingface",
        login_identifier=None,
        login_url="https://huggingface.co/login",
        validation_url="https://huggingface.co/settings/tokens",
        domains=["huggingface.co"],
        purpose="Pobieranie modeli LTX/WAN/TTS/Whisper i assetów ML potrzebnych do lokalnego pipeline.",
        capabilities=["read_metadata", "model_download", "trusted_login", "browser_workflow"],
        notes="Metadata-only account for model downloads. HF token can be added later through Vault/env.",
    ),
    RequiredAccount(
        label="Replicate",
        provider_key="replicate",
        account_identifier="replicate-fallback",
        secret_type="api_token_later",
        linked_integration_key="replicate",
        login_identifier=None,
        login_url="https://replicate.com/signin",
        validation_url="https://replicate.com/account/api-tokens",
        domains=["replicate.com"],
        purpose="Cloud fallback dla generowania obrazów/wideo, gdy lokalny ComfyUI/model nie dowozi.",
        capabilities=["read_metadata", "asset_generation", "trusted_login", "browser_workflow"],
        notes="Metadata-only fallback account. API token is intentionally not guessed.",
    ),
    RequiredAccount(
        label="Gemini AI Studio",
        provider_key="gemini",
        account_identifier="gemini-ai-studio",
        secret_type="api_token_later",
        linked_integration_key="gemini",
        login_identifier=None,
        login_url="https://aistudio.google.com/",
        validation_url="https://aistudio.google.com/app/apikey",
        domains=["aistudio.google.com", "accounts.google.com"],
        purpose="Opcjonalny cloud LLM fallback dla planowania, researchu i skryptów.",
        capabilities=["read_metadata", "draft_write", "trusted_login", "browser_workflow"],
        notes="Metadata-only Gemini account. API key/OAuth must be connected separately.",
    ),
    RequiredAccount(
        label="TradingView",
        provider_key="tradingview",
        account_identifier="tradingview-investment-monitor",
        secret_type="password_later",
        linked_integration_key="tradingview",
        login_identifier=None,
        login_url="https://www.tradingview.com/accounts/signin/",
        validation_url="https://www.tradingview.com/chart/",
        domains=["tradingview.com", "www.tradingview.com"],
        purpose="Monitoring rynku, wykresów i alertów dla projektu inwestycyjnego.",
        capabilities=["trusted_login", "browser_workflow", "read_metadata", "market_monitor"],
        notes="Wyciągnięte z legacy projektu ATNOS Crypto Market Lab. Brak sekretu w poprzedniej bazie.",
    ),
    RequiredAccount(
        label="XTB",
        provider_key="xtb",
        account_identifier="xtb-investment-account",
        secret_type="password_later",
        linked_integration_key="xtb",
        login_identifier=None,
        login_url="https://xstation5.xtb.com/",
        validation_url="https://xstation5.xtb.com/",
        domains=["xstation5.xtb.com", "xtb.com"],
        purpose="Podgląd konta inwestycyjnego i drafty decyzji. Handel automatyczny pozostaje zablokowany.",
        capabilities=["trusted_login", "browser_workflow", "read_metadata", "market_monitor", "trade_draft"],
        notes="Wyciągnięte z legacy projektu ATNOS Crypto Market Lab. Nie dodano uprawnienia do wykonywania transakcji.",
    ),
)


PROJECT_ACCOUNT_LINKS: tuple[dict[str, Any], ...] = (
    {
        "project_names": ["YT Finanse Short"],
        "provider_keys": ["youtube-studio", "int-mail", "gmail", "canva", "capcut", "elevenlabs", "artlist"],
        "connector_key_fallback": None,
        "action_families": ["browser_login", "browser_navigation", "session_maintenance", "content_edit", "publish_preview"],
        "publish_allowed": True,
        "connector_write_allowed": False,
        "local_render_allowed": True,
    },
    {
        "project_names": ["Modelka Shorts YT", "ATNOS Synthetic Creator Prompt Smoke", "ATNOS Synthetic Creator Live Smoke 3"],
        "provider_keys": [
            "youtube-studio",
            "ideogram",
            "canva",
            "capcut",
            "runway",
            "klap",
            "submagic",
            "elevenlabs",
            "huggingface",
            "replicate",
        ],
        "connector_key_fallback": None,
        "action_families": ["browser_login", "browser_navigation", "session_maintenance", "content_edit", "asset_generation", "publish_preview"],
        "publish_allowed": True,
        "connector_write_allowed": False,
        "local_render_allowed": True,
    },
    {
        "project_names": ["Modelka OnlyFans", "Cozy Tease", "Playful Brat", "Late-night E-Girl"],
        "provider_keys": ["onlyfans", "canva", "ideogram", "elevenlabs", "metricool"],
        "connector_key_fallback": None,
        "action_families": ["browser_login", "browser_navigation", "session_maintenance", "content_edit", "publish_preview"],
        "publish_allowed": True,
        "connector_write_allowed": False,
        "local_render_allowed": True,
    },
    {
        "project_names": ["ATNOS Crypto Market Lab"],
        "provider_keys": ["xtb", "tradingview"],
        "connector_key_fallback": None,
        "action_families": ["browser_login", "browser_navigation", "session_maintenance", "market_monitor", "trade_draft"],
        "publish_allowed": False,
        "connector_write_allowed": False,
        "local_render_allowed": False,
    },
)


LEGACY_BROWSER_MAPPING_DEFAULTS: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "login_url": "https://elevenlabs.io/",
        "validation_url": "https://elevenlabs.io/app",
        "domains": ["elevenlabs.io"],
        "purpose": "TTS/lektor i eksperymenty głosowe dla shortów.",
    },
    "capcut": {
        "login_url": "https://www.capcut.com/",
        "validation_url": "https://www.capcut.com/",
        "domains": ["www.capcut.com", "capcut.com"],
        "purpose": "Edycja, template'y i awaryjny montaż shortów.",
    },
    "canva": {
        "login_url": "https://www.canva.com/login",
        "validation_url": "https://www.canva.com/",
        "domains": ["www.canva.com", "canva.com"],
        "purpose": "Miniatury, grafiki i layouty dla shortów.",
    },
    "artlist": {
        "login_url": "https://artlist.io/",
        "validation_url": "https://artlist.io/",
        "domains": ["artlist.io"],
        "purpose": "Muzyka i SFX do produkcji wideo.",
    },
    "metricool": {
        "login_url": "https://metricool.com/",
        "validation_url": "https://app.metricool.com/",
        "domains": ["metricool.com", "app.metricool.com"],
        "purpose": "Planowanie publikacji i monitoring social/video.",
    },
    "onlyfans": {
        "login_url": "https://onlyfans.com/",
        "validation_url": "https://onlyfans.com/",
        "domains": ["onlyfans.com"],
        "purpose": "Zarządzanie kanałem modelki/synthetic creator i lejkiem monetyzacji.",
    },
    "runway": {
        "login_url": "https://app.runwayml.com/login",
        "validation_url": "https://app.runwayml.com/",
        "domains": ["runwayml.com", "app.runwayml.com"],
        "purpose": "Fallback/generowanie assetów wideo dla shortów.",
    },
    "klap": {
        "login_url": "https://klap.app/",
        "validation_url": "https://klap.app/",
        "domains": ["klap.app"],
        "purpose": "Clip workflow i szybkie przeróbki materiałów pod shorty.",
    },
    "submagic": {
        "login_url": "https://app.submagic.co/",
        "validation_url": "https://app.submagic.co/",
        "domains": ["submagic.co", "app.submagic.co"],
        "purpose": "Napisy, hook captions i stylowanie krótkich filmów.",
    },
    "slack": {
        "login_url": "https://slack.com/signin",
        "validation_url": "https://slack.com/signin",
        "domains": ["slack.com"],
        "purpose": "Powiadomienia operacyjne i alerty automatyzacji.",
    },
    "github": {
        "login_url": "https://github.com/login",
        "validation_url": "https://github.com/",
        "domains": ["github.com"],
        "purpose": "Repozytorium kodu, deploy i utrzymanie Atnosa.",
    },
}


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def ensure_account(db: sqlite3.Connection, spec: RequiredAccount) -> tuple[int, bool]:
    row = db.execute(
        """
        SELECT id FROM account_credentials
        WHERE provider_key = ? AND account_identifier = ?
        ORDER BY id LIMIT 1
        """,
        (spec.provider_key, spec.account_identifier),
    ).fetchone()
    created = False
    if row is None:
        cursor = db.execute(
            """
            INSERT INTO account_credentials (
                label, provider_key, account_identifier, secret_type, storage_backend,
                secret_ref, status, risk_level, allowed_capabilities_json,
                linked_integration_key, linked_project_id, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                spec.label,
                spec.provider_key,
                spec.account_identifier,
                spec.secret_type,
                spec.storage_backend,
                spec.status,
                spec.risk_level,
                dumps(spec.capabilities),
                spec.linked_integration_key,
                spec.notes,
                now(),
                now(),
            ),
        )
        account_id = int(cursor.lastrowid)
        created = True
    else:
        account_id = int(row["id"])
        db.execute(
            """
            UPDATE account_credentials
            SET label = ?, linked_integration_key = COALESCE(linked_integration_key, ?),
                allowed_capabilities_json = ?, notes = COALESCE(notes, ?), updated_at = ?
            WHERE id = ?
            """,
            (
                spec.label,
                spec.linked_integration_key,
                dumps(spec.capabilities),
                spec.notes,
                now(),
                account_id,
            ),
        )

    ensure_profile(
        db,
        account_id=account_id,
        login_identifier=spec.login_identifier,
        mapping={
            "purpose": spec.purpose,
            "primary_url": spec.login_url,
            "login_url": spec.login_url,
            "validation_url": spec.validation_url,
            "browser_session_service": spec.provider_key,
            "domains": spec.domains,
            "mode": "browser_first",
            "auto_send": False,
            "requires_human_login": True,
        },
    )
    return account_id, created


def ensure_profile(
    db: sqlite3.Connection,
    *,
    account_id: int,
    login_identifier: str | None,
    mapping: dict[str, Any],
) -> bool:
    existing = db.execute(
        "SELECT id, login_identifier, connector_mapping_json FROM credential_profiles WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if existing is None:
        db.execute(
            """
            INSERT INTO credential_profiles (
                account_id, login_identifier, password_secret_ref, totp_secret_ref,
                backup_codes_secret_ref, recovery_email, recovery_email_secret_ref,
                connector_mapping_json, status, notes, created_at, updated_at
            )
            VALUES (?, ?, NULL, NULL, NULL, NULL, NULL, ?, 'configured', NULL, ?, ?)
            """,
            (account_id, login_identifier, dumps(mapping), now(), now()),
        )
        return True

    current_mapping: dict[str, Any]
    try:
        current_mapping = json.loads(existing["connector_mapping_json"] or "{}")
    except json.JSONDecodeError:
        current_mapping = {}
    merged = {**mapping, **current_mapping}
    next_login = existing["login_identifier"] or login_identifier
    db.execute(
        """
        UPDATE credential_profiles
        SET login_identifier = ?, connector_mapping_json = ?, status = 'configured', updated_at = ?
        WHERE id = ?
        """,
        (next_login, dumps(merged), now(), int(existing["id"])),
    )
    return False


def project_ids_by_name(db: sqlite3.Connection, names: list[str]) -> list[int]:
    if not names:
        return []
    placeholders = ",".join("?" for _ in names)
    rows = db.execute(
        f"SELECT id FROM creator_projects WHERE name IN ({placeholders}) ORDER BY id",
        names,
    ).fetchall()
    return [int(row["id"]) for row in rows]


def accounts_by_provider(db: sqlite3.Connection, provider_key: str) -> list[sqlite3.Row]:
    return db.execute(
        """
        SELECT id, provider_key, linked_integration_key
        FROM account_credentials
        WHERE provider_key = ?
        ORDER BY id
        """,
        (provider_key,),
    ).fetchall()


def domains_for_account(db: sqlite3.Connection, account_id: int) -> list[str]:
    row = db.execute(
        "SELECT connector_mapping_json FROM credential_profiles WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        return []
    try:
        mapping = json.loads(row["connector_mapping_json"] or "{}")
    except json.JSONDecodeError:
        return []
    domains = mapping.get("domains") or mapping.get("host_fragments")
    if isinstance(domains, list):
        return [str(item) for item in domains if str(item).strip()]
    login_url = str(mapping.get("login_url") or mapping.get("primary_url") or "")
    if "://" in login_url:
        return [login_url.split("://", 1)[1].split("/", 1)[0]]
    return []


def ensure_grant(
    db: sqlite3.Connection,
    *,
    project_id: int,
    account_id: int,
    connector_key: str,
    domains: list[str],
    action_families: list[str],
    publish_allowed: bool,
    connector_write_allowed: bool,
    local_render_allowed: bool,
) -> bool:
    existing = db.execute(
        """
        SELECT id FROM trust_grants
        WHERE project_id = ? AND account_id = ? AND connector_key = ?
        ORDER BY id LIMIT 1
        """,
        (project_id, account_id, connector_key),
    ).fetchone()
    if existing is not None:
        db.execute(
            """
            UPDATE trust_grants
            SET domain_allowlist_json = ?, action_families_json = ?, login_allowed = 1,
                two_factor_allowed = 1, publish_allowed = ?, browser_automation_allowed = 1,
                connector_write_allowed = ?, local_render_allowed = ?, is_enabled = 1,
                updated_at = ?
            WHERE id = ?
            """,
            (
                dumps(domains),
                dumps(action_families),
                int(publish_allowed),
                int(connector_write_allowed),
                int(local_render_allowed),
                now(),
                int(existing["id"]),
            ),
        )
        return False

    db.execute(
        """
        INSERT INTO trust_grants (
            project_id, account_id, connector_key, mode, domain_allowlist_json,
            action_families_json, file_write_scope_json, login_allowed, two_factor_allowed,
            publish_allowed, browser_automation_allowed, connector_write_allowed,
            local_render_allowed, max_retries, cooldown_minutes, is_enabled, notes,
            created_at, updated_at
        )
        VALUES (?, ?, ?, 'trusted_auto', ?, ?, '[]', 1, 1, ?, 1, ?, ?, 2, 15, 1, ?, ?, ?)
        """,
        (
            project_id,
            account_id,
            connector_key,
            dumps(domains),
            dumps(action_families),
            int(publish_allowed),
            int(connector_write_allowed),
            int(local_render_allowed),
            "Imported from legacy Atnos project/integration inventory.",
            now(),
            now(),
        ),
    )
    return True


def backfill_known_profiles(db: sqlite3.Connection) -> dict[str, int]:
    changed = {"profiles_created": 0}
    rows = db.execute(
        """
        SELECT id, provider_key, account_identifier
        FROM account_credentials
        WHERE provider_key IN ('youtube-studio', 'gmail', 'int-mail')
        ORDER BY id
        """
    ).fetchall()
    defaults = {
        "youtube-studio": {
            "login_url": "https://studio.youtube.com/",
            "validation_url": "https://studio.youtube.com/",
            "domains": ["studio.youtube.com", "youtube.com", "accounts.google.com"],
            "purpose": "Zarządzanie kanałem YouTube i upload shortów przez browser workflow.",
        },
        "gmail": {
            "login_url": "https://mail.google.com/",
            "validation_url": "https://mail.google.com/",
            "domains": ["mail.google.com", "gmail.com", "accounts.google.com"],
            "purpose": "Poczta i komunikacja operacyjna Atnosa.",
        },
        "int-mail": {
            "login_url": "https://int.pl/",
            "validation_url": "https://int.pl/",
            "domains": ["int.pl", "poczta.int.pl"],
            "purpose": "Poczta bootstrap i odzyskiwanie kont Atnosa.",
        },
    }
    for row in rows:
        provider = str(row["provider_key"])
        meta = defaults[provider]
        created = ensure_profile(
            db,
            account_id=int(row["id"]),
            login_identifier=str(row["account_identifier"]) if "@" in str(row["account_identifier"]) else None,
            mapping={
                **meta,
                "primary_url": meta["login_url"],
                "browser_session_service": provider,
                "mode": "browser_first",
                "auto_send": False,
            },
        )
        if created:
            changed["profiles_created"] += 1
    return changed


def backfill_legacy_browser_mappings(db: sqlite3.Connection) -> dict[str, int]:
    changed = {"profiles_created": 0, "profiles_updated": 0}
    placeholders = ",".join("?" for _ in LEGACY_BROWSER_MAPPING_DEFAULTS)
    rows = db.execute(
        f"""
        SELECT id, provider_key, account_identifier
        FROM account_credentials
        WHERE provider_key IN ({placeholders})
        ORDER BY id
        """,
        tuple(LEGACY_BROWSER_MAPPING_DEFAULTS.keys()),
    ).fetchall()
    for row in rows:
        provider = str(row["provider_key"])
        meta = LEGACY_BROWSER_MAPPING_DEFAULTS[provider]
        existing = db.execute(
            "SELECT id FROM credential_profiles WHERE account_id = ?",
            (int(row["id"]),),
        ).fetchone()
        created = ensure_profile(
            db,
            account_id=int(row["id"]),
            login_identifier=str(row["account_identifier"]) if "@" in str(row["account_identifier"]) else None,
            mapping={
                **meta,
                "primary_url": meta["login_url"],
                "browser_session_service": provider,
                "mode": "browser_first",
                "auto_send": False,
                "requires_human_login": True,
            },
        )
        if created:
            changed["profiles_created"] += 1
        elif existing is not None:
            changed["profiles_updated"] += 1
    return changed


def run(db_path: Path, dry_run: bool) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        required = {"account_credentials", "credential_profiles", "creator_projects", "trust_grants"}
        missing = [name for name in required if not table_exists(db, name)]
        if missing:
            raise RuntimeError(f"Missing required tables: {missing}")

        db.execute("BEGIN")
        summary: dict[str, Any] = {
            "db_path": str(db_path),
            "accounts_created": [],
            "accounts_updated_or_existing": [],
            "profiles_created": 0,
            "profiles_updated_or_existing": 0,
            "grants_created": 0,
            "grants_updated_or_existing": 0,
        }
        summary["profiles_created"] += backfill_known_profiles(db)["profiles_created"]
        legacy_mapping_summary = backfill_legacy_browser_mappings(db)
        summary["profiles_created"] += legacy_mapping_summary["profiles_created"]
        summary["profiles_updated_or_existing"] += legacy_mapping_summary["profiles_updated"]
        for spec in REQUIRED_ACCOUNTS:
            account_id, created = ensure_account(db, spec)
            item = {"id": account_id, "provider_key": spec.provider_key, "label": spec.label}
            if created:
                summary["accounts_created"].append(item)
            else:
                summary["accounts_updated_or_existing"].append(item)

        for link in PROJECT_ACCOUNT_LINKS:
            project_ids = project_ids_by_name(db, list(link["project_names"]))
            for project_id in project_ids:
                for provider_key in list(link["provider_keys"]):
                    for account in accounts_by_provider(db, provider_key):
                        connector_key = str(account["linked_integration_key"] or account["provider_key"])
                        created = ensure_grant(
                            db,
                            project_id=project_id,
                            account_id=int(account["id"]),
                            connector_key=connector_key,
                            domains=domains_for_account(db, int(account["id"])),
                            action_families=list(link["action_families"]),
                            publish_allowed=bool(link["publish_allowed"]),
                            connector_write_allowed=bool(link["connector_write_allowed"]),
                            local_render_allowed=bool(link["local_render_allowed"]),
                        )
                        if created:
                            summary["grants_created"] += 1
                        else:
                            summary["grants_updated_or_existing"] += 1

        if dry_run:
            db.rollback()
            summary["dry_run"] = True
        else:
            db.commit()
            summary["dry_run"] = False
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy Atnos required account metadata.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.dry_run), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
