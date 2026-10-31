"""Pinned external repositories. None of them is executed just because it is public.

Reviewed 2026-09-26 from GitHub metadata and selected files at the SHAs below.
Social SDK is not installed as a Node package. YouTube skill Python is not run.
Awesome is a catalog. OpenToonz is detected, not downloaded.
Scrapling parser is installed in the API venv; stealth/browser fetchers stay off.
"""
from __future__ import annotations

import json
import importlib.metadata
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REVIEWED_AT = "2026-09-26"
VENDOR_ROOT = Path(r"E:\Atnos\Tools\external-repos")
SOCIAL_SDK_BRIDGE = Path(r"E:\Atnos\Tools\social-sdk-bridge")
OPENTOONZ_TOOLS = Path(r"E:\Atnos\Tools\OpenToonz")
OPENTOONZ_SETUP_URL = "https://github.com/opentoonz/opentoonz/releases/download/v1.8.0/OpenToonzSetup.exe"
OPENTOONZ_SETUP_VERSION = "v1.8.0"

REPOS: tuple[dict[str, Any], ...] = (
    {
        "id": "social_sdk",
        "name": "Social SDK",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/opencoredev/social-sdk",
        "branch": "main",
        "commit": "63a41855d03044c3e37c1630cdef54ca13719524",
        "license": "MIT",
        "trust": "REVIEW_REQUIRED",
        "imported_at": REVIEWED_AT,
        "reviewed_at": REVIEWED_AT,
        "summary": "Typowana biblioteka social. Pakiet Node @opencoredev/social-sdk jest zainstalowany w social-sdk-bridge.",
        "docs_url": "https://social-sdk.dev",
        "status": "REQUIRES_SETUP",
        "local_path": str(VENDOR_ROOT / "social-sdk"),
        "runtime_path": str(SOCIAL_SDK_BRIDGE),
        "npm_package": "@opencoredev/social-sdk@0.5.0",
    },
    {
        "id": "awesome",
        "name": "Awesome",
        "type": "DISCOVERY_CATALOG",
        "url": "https://github.com/sindresorhus/awesome",
        "branch": "main",
        "commit": "bc98e517ddca672f55f9857d714fc3ea3c3540b2",
        "license": "CC0-1.0",
        "trust": "TRUSTED",
        "imported_at": REVIEWED_AT,
        "reviewed_at": REVIEWED_AT,
        "summary": "Katalog linków. Lokalny klon na E: + indeks RESOURCE_DISCOVERY.",
        "docs_url": "https://github.com/sindresorhus/awesome",
        "status": "REQUIRES_SETUP",
        "capability": "RESOURCE_DISCOVERY",
        "local_path": str(VENDOR_ROOT / "awesome"),
    },
    {
        "id": "youtube_skills_chatgpt",
        "name": "YouTube agent skills",
        "type": "SKILL_PACK",
        "url": "https://github.com/Jakeschincariol/chatgpt-youtube-agent-skill",
        "branch": "main",
        "commit": "6314d3c17dfa1e596096654204e8a09e28cdfd74",
        "license": "MIT",
        "trust": "REVIEW_REQUIRED",
        "imported_at": REVIEWED_AT,
        "reviewed_at": REVIEWED_AT,
        "summary": "Paczka skilli ze screena. Lokalny klon; title.py/hookscore.py w sandboxie.",
        "docs_url": "https://github.com/Jakeschincariol/chatgpt-youtube-agent-skill",
        "status": "REQUIRES_SETUP",
        "local_path": str(VENDOR_ROOT / "chatgpt-youtube-agent-skill"),
        "related_repo": {
            "url": "https://github.com/Jakeschincariol/youtube-agent-skill",
            "commit": "a2feb2104981a375ffd4f87ee04f4f5344ac43c6",
            "note": "Osobny pakiet pod Claude. Nieinstalowany, żeby nie dublować skilli.",
        },
    },
    {
        "id": "opentoonz",
        "name": "OpenToonz",
        "type": "LOCAL_APPLICATION",
        "url": "https://github.com/opentoonz/opentoonz",
        "branch": "master",
        "commit": "6b040d98c7cea0a5b906aac76590b686275d156d",
        "license": "BSD-3-Clause",
        "trust": "TRUSTED",
        "imported_at": REVIEWED_AT,
        "reviewed_at": REVIEWED_AT,
        "summary": "Aplikacja 2D. Źródło sklonowane; instalator tylko z oficjalnego release po zgodzie.",
        "docs_url": "https://opentoonz.github.io/",
        "status": "NOT_INSTALLED",
        "local_path": str(VENDOR_ROOT / "opentoonz"),
        "installer_url": OPENTOONZ_SETUP_URL,
        "installer_version": OPENTOONZ_SETUP_VERSION,
    },
    {
        "id": "scrapling",
        "name": "Scrapling",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/D4Vinci/Scrapling",
        "branch": "main",
        "commit": "e0d4d7563207b70c2cb38487c4e0dcbe0e2f04ca",
        "license": "BSD-3-Clause",
        "trust": "REVIEW_REQUIRED",
        "imported_at": REVIEWED_AT,
        "reviewed_at": REVIEWED_AT,
        "summary": "Adaptacyjny parser HTML (v0.4.15). Fetch przez Atnos httpx; stealth/browser extras wyłączone.",
        "docs_url": "https://scrapling.readthedocs.io/en/latest/",
        "status": "REQUIRES_SETUP",
        "capability": "WEB_PAGE_EXTRACT",
        "local_path": str(VENDOR_ROOT / "scrapling"),
        "pypi_package": "scrapling==0.4.15",
    },
    {
        "id": "whisperx",
        "name": "WhisperX",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/m-bain/whisperX",
        "branch": "main",
        "commit": "771b4a14a9486f8fd5aef18ef49e35d639523dd3",
        "license": "BSD-2-Clause",
        "trust": "REVIEW_REQUIRED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Forced alignment / word timestamps for captions synced to final TTS. GPU extras optional.",
        "docs_url": "https://github.com/m-bain/whisperX",
        "status": "REQUIRES_SETUP",
        "capability": "CAPTION_ALIGN",
        "local_path": str(VENDOR_ROOT / "whisperx"),
    },
    {
        "id": "stable_ts",
        "name": "stable-ts",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/jianfch/stable-ts",
        "branch": "main",
        "commit": "e312072cc024ae9fceb25b057d7d18524873a02b",
        "license": "MIT",
        "trust": "REVIEW_REQUIRED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Lżejszy align znanego transkryptu do audio (alternatywa dla WhisperX).",
        "docs_url": "https://github.com/jianfch/stable-ts",
        "status": "REQUIRES_SETUP",
        "capability": "CAPTION_ALIGN",
        "local_path": str(VENDOR_ROOT / "stable-ts"),
    },
    {
        "id": "youtube_transcript_api",
        "name": "youtube-transcript-api",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/jdepoix/youtube-transcript-api",
        "branch": "master",
        "commit": "8f150ba8836da30a36bcd40e8fca226ed179ba72",
        "license": "MIT",
        "trust": "TRUSTED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Publiczne transkrypty YouTube bez OAuth — research / evidence, nie publish.",
        "docs_url": "https://github.com/jdepoix/youtube-transcript-api",
        "status": "REQUIRES_SETUP",
        "capability": "YOUTUBE_TRANSCRIPT",
        "local_path": str(VENDOR_ROOT / "youtube-transcript-api"),
        "pypi_package": "youtube-transcript-api==1.2.4",
    },
    {
        "id": "yt_dlp",
        "name": "yt-dlp",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/yt-dlp/yt-dlp",
        "branch": "master",
        "commit": "c7fb478d21e9e59524befbe23f7801bb267fb880",
        "license": "Unlicense",
        "trust": "TRUSTED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Metadane YouTube (skip_download). Bez pobierania mediów w default path.",
        "docs_url": "https://github.com/yt-dlp/yt-dlp",
        "status": "REQUIRES_SETUP",
        "capability": "YOUTUBE_METADATA",
        "local_path": str(VENDOR_ROOT / "yt-dlp"),
        "pypi_package": "yt-dlp",
    },
    {
        "id": "remotion_trading",
        "name": "remotion-trading",
        "type": "SKILL_PACK",
        "url": "https://github.com/josebarnetche/remotion-trading",
        "branch": "master",
        "commit": "6f16d2223f20a7651ab2bbc4a3949e6a1536b93a",
        "license": "MIT",
        "trust": "REVIEW_REQUIRED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Wzorce chartów Remotion + realne API (Binance/CoinGecko). Nie zastępuje WB renderer.",
        "docs_url": "https://github.com/josebarnetche/remotion-trading",
        "status": "REQUIRES_SETUP",
        "capability": "FINANCE_CHART_SKILL",
        "local_path": str(VENDOR_ROOT / "remotion-trading"),
    },
    {
        "id": "pyscenedetect",
        "name": "PySceneDetect",
        "type": "INTEGRATION_LIBRARY",
        "url": "https://github.com/Breakthrough/PySceneDetect",
        "branch": "v0.7.1",
        "commit": "6ebb72392de8acfb6c539bf15d0aa912ce7ab6b2",
        "license": "BSD-3-Clause",
        "trust": "REVIEW_REQUIRED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Wykrywanie faktycznych cięć w finalnym filmie; raport doradczy QA, bez samodzielnego montażu.",
        "docs_url": "https://www.scenedetect.com/docs/latest/",
        "status": "HEALTHY",
        "capability": "FINAL_VIDEO_CUT_AUDIT",
        "local_path": str(VENDOR_ROOT / "pyscenedetect"),
        "pypi_package": "scenedetect-headless==0.7.1",
    },
    {
        "id": "lucide",
        "name": "Lucide Icons",
        "type": "ASSET_LIBRARY",
        "url": "https://github.com/lucide-icons/lucide",
        "branch": "main",
        "commit": "66d8f9fc394b8530377e5f6112f0b8908ba01280",
        "license": "ISC",
        "trust": "TRUSTED",
        "imported_at": "2026-09-27",
        "reviewed_at": "2026-09-27",
        "summary": "Programowe SVG ikony (finance/UI) zamiast generowanych AI stilli z liczbami.",
        "docs_url": "https://lucide.dev",
        "status": "REQUIRES_SETUP",
        "capability": "ICON_SVG_LIBRARY",
        "local_path": str(VENDOR_ROOT / "lucide"),
    },
)

# Declared from the YouTube guide at the pinned Social SDK commit.
# Other platforms have a guide page. Their operations stay UNVERIFIED.
SOCIAL_PLATFORMS: tuple[dict[str, Any], ...] = (
    {
        "id": "youtube",
        "name": "YouTube",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [
            {"id": "SOCIAL_MEDIA_UPLOAD", "evidence": "DECLARED"},
            {"id": "SOCIAL_SCHEDULE", "evidence": "DECLARED"},
            {"id": "SOCIAL_ANALYTICS", "evidence": "DECLARED_SCOPE"},
        ],
    },
    {
        "id": "instagram",
        "name": "Instagram",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
    {
        "id": "tiktok",
        "name": "TikTok",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
    {
        "id": "threads",
        "name": "Threads",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
    {
        "id": "x",
        "name": "X",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
    {
        "id": "bluesky",
        "name": "Bluesky",
        "auth": "oauth",
        "password_accepted": False,
        "capabilities": [],
        "evidence": "PLATFORM_GUIDE_EXISTS",
    },
)

SKILLS: tuple[dict[str, Any], ...] = (
    {
        "id": "yt-script",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Script drafting cues that improve Atnos script QA — not a second script engine.",
        "capabilities": ["SCRIPT_DRAFT"],
        "input_schema": {"topic": "string", "audience": "string"},
        "output_schema": {"outline": "string", "beats": "list"},
    },
    {
        "id": "yt-package",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Title/thumbnail packaging prompts for existing packaging step.",
        "capabilities": ["PACKAGING"],
        "input_schema": {"script": "string"},
        "output_schema": {"titles": "list", "thumbnail_notes": "string"},
    },
    {
        "id": "yt-plan",
        "classification": "DUPLICATE",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Weekly planning overlaps Atnos content planning.",
        "capabilities": ["CONTENT_PLAN"],
        "input_schema": {"channel": "string"},
        "output_schema": {"week": "list"},
    },
    {
        "id": "yt-audit",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Channel audit checklist for Project Brain reviews.",
        "capabilities": ["CHANNEL_AUDIT"],
        "input_schema": {"channel_summary": "string"},
        "output_schema": {"findings": "list"},
    },
    {
        "id": "yt-seo",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "SEO phrasing for titles/descriptions already produced by Atnos.",
        "capabilities": ["SEO_COPY"],
        "input_schema": {"title": "string", "script": "string"},
        "output_schema": {"description": "string", "tags": "list"},
    },
    {
        "id": "yt-shorts",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Shorts beat compression guidance for WB short pipeline.",
        "capabilities": ["SHORTS_CUT"],
        "input_schema": {"script": "string"},
        "output_schema": {"beats": "list"},
    },
    {
        "id": "yt-chapters",
        "classification": "NEW_CAPABILITY",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Chapter markers from final narration timings.",
        "capabilities": ["CHAPTERS"],
        "input_schema": {"word_timestamps": "list"},
        "output_schema": {"chapters": "list"},
    },
    {
        "id": "yt-comment",
        "classification": "IMPROVES_EXISTING",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Comment reply drafts — requires live comments, not invented audience data.",
        "capabilities": ["COMMENT_DRAFT"],
        "input_schema": {"comments": "list"},
        "output_schema": {"replies": "list"},
    },
    {
        "id": "yt-edit",
        "classification": "NEW_CAPABILITY",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Edit notes for dead-air cuts; does not replace Remotion.",
        "capabilities": ["EDIT_NOTES"],
        "input_schema": {"transcript": "string"},
        "output_schema": {"cuts": "list"},
    },
    {
        "id": "yt-retention",
        "classification": "NEW_CAPABILITY",
        "execution": "requires_data",
        "permissions": ["read a user-supplied retention export"],
        "description": "Retention analysis only from a real export. Missing data → REQUIRES_DATA.",
        "capabilities": ["RETENTION_ANALYSIS"],
        "input_schema": {"rows": "list"},
        "output_schema": {"status": "string", "curve": "optional"},
    },
    {
        "id": "yt-viral",
        "classification": "LOW_VALUE",
        "execution": "manual",
        "permissions": ["read instructions"],
        "description": "Viral scoring heuristics — low trust without channel metrics.",
        "capabilities": ["VIRALITY_HEURISTIC"],
        "input_schema": {"niche": "string"},
        "output_schema": {"rank": "list"},
    },
)

PROJECTS = ("wealth_blueprint", "wonder_crew", "dark_chronicles")
_VAULT_REF = re.compile(r"^(?:vault://|aes_256_gcm:|os_keyring:|oauth:)[A-Za-z0-9_./:-]+$")
AWESOME_README = (
    "https://raw.githubusercontent.com/sindresorhus/awesome/"
    "bc98e517ddca672f55f9857d714fc3ea3c3540b2/readme.md"
)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def data_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    base = Path(os.environ.get("DATA_DIR") or r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data")
    return base / "integrations"


def _state_path(root: Path | None) -> Path:
    return data_root(root) / "external_repos_state.json"


def _load(root: Path | None) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save(root: Path | None, payload: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _platform_accounts(state: dict[str, Any], platform_id: str) -> list[dict[str, Any]]:
    """Normalize legacy single-account dict into a list of account records."""
    raw = state.get("accounts")
    if not isinstance(raw, dict):
        return []
    entry = raw.get(platform_id)
    if isinstance(entry, list):
        return [item for item in entry if isinstance(item, dict)]
    if isinstance(entry, dict) and entry.get("display_name"):
        return [
            {
                "account_id": str(entry.get("account_id") or f"{platform_id}-primary"),
                "display_name": entry.get("display_name"),
                "credential_ref": entry.get("credential_ref"),
                "connected_at": entry.get("connected_at"),
                "scopes": entry.get("scopes") or [],
                "last_validation": entry.get("last_validation"),
            }
        ]
    return []


def list_repositories(root: Path | None = None) -> list[dict[str, Any]]:
    state = _load(root)
    enabled = state.get("enabled") if isinstance(state.get("enabled"), dict) else {}
    last = state.get("last_execution") if isinstance(state.get("last_execution"), dict) else None
    rows = []
    for item in REPOS:
        row = dict(item)
        row["enabled"] = bool(enabled.get(item["id"], True))
        row["last_execution"] = last if last and last.get("provider") == item["id"] else None
        if item["id"] == "pyscenedetect":
            try:
                installed_version = importlib.metadata.version("scenedetect-headless")
            except importlib.metadata.PackageNotFoundError:
                installed_version = None
            row["package_version"] = installed_version
            row["local_clone"] = (VENDOR_ROOT / "pyscenedetect" / ".git").is_dir()
            row["status"] = "HEALTHY" if installed_version == "0.7.1" and row["local_clone"] else "REQUIRES_SETUP"
            row["runtime"] = "EXECUTABLE" if row["status"] == "HEALTHY" else "ADAPTER"
        if item["id"] == "opentoonz":
            ot = opentoonz_status(root)
            row["status"] = ot["status"]
            row["path"] = ot.get("path")
            row["runtime"] = "LOCAL_APPLICATION"
            installer = OPENTOONZ_TOOLS / "OpenToonzSetup.exe"
            row["installer_present"] = installer.is_file()
            row["installer_url"] = OPENTOONZ_SETUP_URL
            row["local_clone"] = (VENDOR_ROOT / "opentoonz" / ".git").exists()
        if item["id"] == "awesome":
            index = state.get("awesome_index")
            row["items_discovered"] = len(index) if isinstance(index, list) else 0
            row["last_sync"] = state.get("awesome_synced_at")
            row["status"] = "HEALTHY" if row["items_discovered"] else "REQUIRES_SETUP"
            row["runtime"] = "EXECUTABLE"
            row["local_clone"] = (VENDOR_ROOT / "awesome" / "readme.md").is_file()
            row["index_source"] = state.get("awesome_index_source")
        if item["id"] == "youtube_skills_chatgpt":
            skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
            row["skills_enabled"] = sum(1 for value in skills.values() if value)
            row["skills_total"] = len(SKILLS)
            row["runtime"] = "EXECUTABLE"
            row["local_clone"] = (VENDOR_ROOT / "chatgpt-youtube-agent-skill" / "skills").is_dir()
            if row["local_clone"]:
                row["status"] = "HEALTHY" if row["skills_enabled"] else "REQUIRES_SETUP"
        if item["id"] == "social_sdk":
            accounts = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
            connected = 0
            if isinstance(accounts, dict):
                for value in accounts.values():
                    if isinstance(value, list) and value:
                        connected += len(value)
                    elif isinstance(value, dict) and value.get("display_name"):
                        connected += 1
            row["connected_accounts"] = connected
            try:
                from atnos.integrations import social_sdk_bridge

                loaded = social_sdk_bridge.is_loaded()
                row["social_sdk_node"] = "LOADED" if loaded else "NOT_LOADED"
                row["package_version"] = social_sdk_bridge.package_version()
                row["runtime"] = "EXECUTABLE" if loaded else "ADAPTER"
                row["live_mode"] = "MOCK_READY" if loaded else "REQUIRES_SETUP"
                if loaded:
                    row["status"] = "HEALTHY" if connected else "REQUIRES_SETUP"
            except Exception:
                row["social_sdk_node"] = "NOT_LOADED"
                row["runtime"] = "ADAPTER"
                row["live_mode"] = "REQUIRES_SETUP"
        if item["id"] == "scrapling":
            from atnos.integrations import scrapling_bridge

            health = scrapling_bridge.status()
            last = state.get("last_execution") if isinstance(state.get("last_execution"), dict) else {}
            row["status"] = health["status"]
            row["package_version"] = health.get("package_version")
            row["runtime"] = "EXECUTABLE" if health.get("loaded") else "ADAPTER"
            row["local_clone"] = (VENDOR_ROOT / "scrapling" / "LICENSE").is_file()
            row["fetchers"] = health.get("fetchers")
            row["stealth"] = health.get("stealth") or "OFF"
            row["browser"] = health.get("browser") or "OFF"
            row["mode"] = health.get("mode") or "HTTP extraction"
            row["capability"] = "WEB_PAGE_EXTRACT"
            row["detail"] = health.get("detail")
            if last.get("capability") == "WEB_PAGE_EXTRACT":
                row["last_used"] = last.get("finished_at")
                row["last_execution"] = last
            else:
                row["last_used"] = None
        if item["id"] in {
            "whisperx",
            "stable_ts",
            "youtube_transcript_api",
            "yt_dlp",
            "remotion_trading",
            "lucide",
        }:
            from atnos.integrations import priority_vendor_bridges as pvb

            health = pvb.STATUS_FNS[item["id"]]()
            row["status"] = health.get("status") or row.get("status")
            row["local_clone"] = bool(health.get("local_clone"))
            row["runtime"] = "EXECUTABLE" if health.get("loaded") else "ADAPTER"
            row["detail"] = health.get("detail")
            row["capability"] = health.get("capability") or item.get("capability")
            if health.get("package_version"):
                row["package_version"] = health["package_version"]
            if health.get("icon_count") is not None:
                row["icon_count"] = health["icon_count"]
            if health.get("has_skill_md") is not None:
                row["has_skill_md"] = health["has_skill_md"]
        rows.append(row)
    return rows


def list_social_accounts(root: Path | None = None) -> list[dict[str, Any]]:
    state = _load(root)
    mapping = state.get("project_accounts") if isinstance(state.get("project_accounts"), dict) else {}
    rows = []
    for platform in SOCIAL_PLATFORMS:
        accounts = _platform_accounts(state, platform["id"])
        primary = accounts[0] if accounts else {}
        rows.append(
            {
                **platform,
                "connected": bool(accounts),
                "display_name": primary.get("display_name"),
                "credential_stored": bool(primary.get("credential_ref")),
                "account_count": len(accounts),
                "accounts": [
                    {
                        "account_id": item.get("account_id"),
                        "display_name": item.get("display_name"),
                        "credential_stored": bool(item.get("credential_ref")),
                        "scopes": item.get("scopes") or [],
                        "last_validation": item.get("last_validation"),
                        "connected_at": item.get("connected_at"),
                    }
                    for item in accounts
                ],
                "project_links": {
                    project: mapping.get(project)
                    for project in PROJECTS
                    if mapping.get(project) == platform["id"]
                    or (
                        isinstance(mapping.get(project), str)
                        and str(mapping.get(project)).startswith(f"{platform['id']}:")
                    )
                },
                "status": "HEALTHY" if accounts and primary.get("last_validation") == "ok" else ("REQUIRES_SETUP" if not accounts else "DEGRADED"),
                "publish": "READY_FOR_APPROVAL",
            }
        )
    return rows


def connect_social_account(
    platform_id: str,
    *,
    display_name: str,
    credential_ref: str,
    account_id: str | None = None,
    scopes: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if platform_id not in {item["id"] for item in SOCIAL_PLATFORMS}:
        return {"status": "ERROR", "detail": "Nieznana platforma."}
    platform = next(item for item in SOCIAL_PLATFORMS if item["id"] == platform_id)
    if platform.get("auth") == "oauth" and any(
        token in credential_ref.lower() for token in ("password", "passwd", "haslo")
    ):
        return {"status": "ERROR", "detail": "Dla OAuth Atnos nie przyjmuje hasła platformy."}
    if not display_name.strip() or not credential_ref.strip():
        return {"status": "REQUIRES_SETUP", "detail": "Podaj nazwę konta i referencję z sejfu, nie sekret."}
    cleaned_ref = credential_ref.strip()
    if _VAULT_REF.match(cleaned_ref):
        pass
    elif any(token in cleaned_ref.lower() for token in ("token", "secret", "password", "ya29.")):
        return {"status": "ERROR", "detail": "Do rejestru trafia credential_ref, nie wartość sekretu."}
    else:
        return {"status": "ERROR", "detail": "credential_ref musi wskazywać sejf (vault://, aes_256_gcm:, os_keyring: lub oauth:)."}
    state = _load(root)
    accounts_map = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    existing = _platform_accounts(state, platform_id)
    aid = (account_id or f"{platform_id}-{display_name.strip().casefold().replace(' ', '-')}")[:80]
    record = {
        "account_id": aid,
        "display_name": display_name.strip(),
        "credential_ref": cleaned_ref,
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "scopes": list(scopes or []),
        "last_validation": None,
    }
    replaced = False
    next_accounts = []
    for item in existing:
        if item.get("account_id") == aid:
            next_accounts.append(record)
            replaced = True
        else:
            next_accounts.append(item)
    if not replaced:
        next_accounts.append(record)
    accounts_map[platform_id] = next_accounts
    state["accounts"] = accounts_map
    _save(root, state)
    return {
        "status": "REQUIRES_SETUP",
        "display_name": display_name.strip(),
        "account_id": aid,
        "credential_stored": True,
        "detail": "Konto zapisane. Uruchom Test connection. Publikacja nadal wymaga zgody.",
    }


def disconnect_social_account(
    platform_id: str,
    *,
    account_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    state = _load(root)
    accounts_map = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    existing = _platform_accounts(state, platform_id)
    if account_id:
        remaining = [item for item in existing if item.get("account_id") != account_id]
        if remaining:
            accounts_map[platform_id] = remaining
        else:
            accounts_map.pop(platform_id, None)
    else:
        accounts_map.pop(platform_id, None)
    state["accounts"] = accounts_map
    _save(root, state)
    return {"status": "REQUIRES_SETUP", "disconnected": True, "account_id": account_id}


def credential_refs_for_platform(
    platform_id: str,
    *,
    account_id: str | None = None,
    root: Path | None = None,
) -> list[str]:
    refs = []
    for item in _platform_accounts(_load(root), platform_id):
        if account_id and item.get("account_id") != account_id:
            continue
        ref = item.get("credential_ref")
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
    return refs


def verify_social_connection(platform_id: str, *, account_id: str | None = None, root: Path | None = None) -> dict[str, Any]:
    state = _load(root)
    accounts = _platform_accounts(state, platform_id)
    if not accounts:
        return {"status": "REQUIRES_SETUP", "detail": "Brak podłączonego konta."}
    target = next((item for item in accounts if item.get("account_id") == account_id), accounts[0])
    ref = str(target.get("credential_ref") or "")
    if not _VAULT_REF.match(ref):
        return {"status": "ERROR", "detail": "credential_ref jest nieprawidłowy."}
    # Validate shape only — never resolve or return secret material here.
    ok = ref.startswith(("vault://", "aes_256_gcm:", "os_keyring:", "oauth:"))
    now = datetime.now(timezone.utc).isoformat()
    accounts_map = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    updated = []
    for item in accounts:
        row = dict(item)
        if row.get("account_id") == target.get("account_id"):
            row["last_validation"] = "ok" if ok else "failed"
            row["last_validation_at"] = now
        updated.append(row)
    accounts_map[platform_id] = updated
    state["accounts"] = accounts_map
    _save(root, state)
    return {
        "status": "HEALTHY" if ok else "ERROR",
        "platform_id": platform_id,
        "account_id": target.get("account_id"),
        "display_name": target.get("display_name"),
        "credential_stored": True,
        "detail": "Referencja sejfu wygląda poprawnie. Live read u dostawcy nie był wywoływany w tym teście.",
        "last_validation": now,
    }


def request_publish(platform_id: str, *, approved: bool = False, account_id: str | None = None) -> dict[str, Any]:
    if not approved:
        return {
            "status": "READY_FOR_APPROVAL",
            "published": False,
            "account_id": account_id,
            "detail": "Publikacja czeka na zgodę. Testowy post nie został wysłany.",
        }
    return {
        "status": "SKIPPED",
        "published": False,
        "account_id": account_id,
        "detail": "Zgoda jest zapisana, ale ten adapter nie publikuje bez osobnego live polecenia.",
    }


def list_skills(root: Path | None = None) -> list[dict[str, Any]]:
    state = _load(root)
    enabled = state.get("skills") if isinstance(state.get("skills"), dict) else {}
    last = state.get("last_execution") if isinstance(state.get("last_execution"), dict) else {}
    repo = next(item for item in REPOS if item["id"] == "youtube_skills_chatgpt")
    rows = []
    for skill in SKILLS:
        is_enabled = bool(enabled.get(skill["id"], False))
        rows.append(
            {
                **skill,
                "source_repo": repo["url"],
                "commit": repo["commit"],
                "license": repo["license"],
                "trust": "REVIEW_REQUIRED",
                "enabled": is_enabled,
                "provider_locked": False,
                "executable": is_enabled and skill.get("execution") in {"manual", "requires_data"},
                "requires_data": skill.get("execution") == "requires_data",
                "last_run": last if last.get("skill_id") == skill["id"] else None,
            }
        )
    return rows


def inspect_skill(skill_id: str, root: Path | None = None) -> dict[str, Any]:
    skills = {item["id"]: item for item in list_skills(root)}
    skill = skills.get(skill_id)
    if not skill:
        return {"status": "ERROR", "detail": "Nieznany skill."}
    return {"status": "OK", "skill": skill, "view_source": skill["source_repo"]}


def set_skill_enabled(skill_id: str, enabled: bool, root: Path | None = None) -> dict[str, Any]:
    if skill_id not in {item["id"] for item in SKILLS}:
        return {"status": "ERROR", "detail": "Nieznany skill."}
    state = _load(root)
    skills = state.get("skills") if isinstance(state.get("skills"), dict) else {}
    skills[skill_id] = bool(enabled)
    state["skills"] = skills
    _save(root, state)
    return {"id": skill_id, "enabled": bool(enabled)}


def retention_from_rows(rows: list[tuple[float, float]]) -> dict[str, Any]:
    if len(rows) < 2:
        return {"status": "REQUIRES_DATA", "curve": None, "detail": "Brak eksportu retencji. Krzywa nie została wygenerowana."}
    return {"status": "HEALTHY", "points": len(rows), "curve": None}


def parse_awesome_readme(markdown: str) -> list[dict[str, str]]:
    found = []
    for name, url in _LINK.findall(markdown):
        if "github.com" not in url:
            continue
        found.append({"name": name.strip(), "url": url, "trust": "DISCOVERED"})
    return found


def sync_awesome(root: Path | None = None, *, client: httpx.Client | None = None, markdown: str | None = None) -> dict[str, Any]:
    source = "supplied"
    if markdown is None:
        local_readme = VENDOR_ROOT / "awesome" / "readme.md"
        if local_readme.is_file():
            markdown = local_readme.read_text(encoding="utf-8")
            source = str(local_readme)
        else:
            owns = client is None
            http = client or httpx.Client(timeout=20.0)
            try:
                response = http.get(AWESOME_README)
                if response.is_error:
                    return {"status": "DEGRADED", "detail": f"Awesome zwrócił HTTP {response.status_code}."}
                markdown = response.text
                source = AWESOME_README
            except httpx.HTTPError:
                return {"status": "OFFLINE", "detail": "Nie udało się pobrać katalogu Awesome."}
            finally:
                if owns:
                    http.close()
    items = parse_awesome_readme(markdown)
    state = _load(root)
    state["awesome_index"] = items
    state["awesome_synced_at"] = datetime.now(timezone.utc).isoformat()
    state["awesome_commit"] = REPOS[1]["commit"]
    state["awesome_index_source"] = source
    _save(root, state)
    return {
        "status": "HEALTHY",
        "items_discovered": len(items),
        "trust": "DISCOVERED",
        "index_source": source,
        "local_clone": (VENDOR_ROOT / "awesome").is_dir(),
    }


def awesome_index_source(root: Path | None = None) -> str:
    state = _load(root)
    value = state.get("awesome_index_source")
    return str(value) if value else "unknown"


def search_awesome(query: str, root: Path | None = None) -> list[dict[str, str]]:
    index = _load(root).get("awesome_index")
    if not isinstance(index, list):
        return []
    needle = query.casefold()
    return [item for item in index if needle in str(item.get("name", "")).casefold() or needle in str(item.get("url", "")).casefold()][:20]


def discover_resources(query: str, root: Path | None = None) -> dict[str, Any]:
    """Capability RESOURCE_DISCOVERY — results stay DISCOVERED, never auto-trusted."""
    hits = search_awesome(query, root)
    return {
        "status": "HEALTHY" if hits else "REQUIRES_SETUP",
        "capability": "RESOURCE_DISCOVERY",
        "trust": "DISCOVERED",
        "items": hits,
        "index_source": awesome_index_source(root),
        "detail": "Linki z Awesome wymagają osobnego security/license review przed użyciem.",
    }


def opentoonz_status(root: Path | None = None) -> dict[str, Any]:
    state = _load(root)
    configured = state.get("opentoonz_path")
    installer = OPENTOONZ_TOOLS / "OpenToonzSetup.exe"
    candidates = []
    if isinstance(configured, str) and configured.strip():
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path(r"C:\Program Files\OpenToonz\OpenToonz.exe"),
            Path(r"C:\Program Files\OpenToonz 1.7\OpenToonz.exe"),
            Path(r"E:\Atnos\Tools\OpenToonz\OpenToonz.exe"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return {
                "status": "DEGRADED",
                "path": str(path),
                "detail": "Plik znaleziony. Automatyczny render nie został uruchomiony — CLI UNVERIFIED.",
                "install_allowed_without_consent": False,
                "installer_present": installer.is_file(),
                "installer_version": OPENTOONZ_SETUP_VERSION,
                "local_clone": (VENDOR_ROOT / "opentoonz" / ".git").exists(),
            }
    detail = "OpenToonz nie jest zainstalowany."
    if installer.is_file():
        detail += f" Oficjalny instalator {OPENTOONZ_SETUP_VERSION} jest pobrany — uruchom po zgodzie."
    else:
        detail += " Pobierz oficjalny OpenToonzSetup.exe po zgodzie użytkownika."
    return {
        "status": "NOT_INSTALLED",
        "path": None,
        "detail": detail,
        "install_allowed_without_consent": False,
        "installer_present": installer.is_file(),
        "installer_url": OPENTOONZ_SETUP_URL,
        "installer_version": OPENTOONZ_SETUP_VERSION,
        "local_clone": (VENDOR_ROOT / "opentoonz" / ".git").exists(),
    }


def prepare_opentoonz_installer(*, confirm_download: bool = False) -> dict[str, Any]:
    """Download the official Windows setup only after explicit confirmation."""
    target = OPENTOONZ_TOOLS / "OpenToonzSetup.exe"
    if target.is_file():
        return {
            "status": "READY",
            "path": str(target),
            "version": OPENTOONZ_SETUP_VERSION,
            "source": OPENTOONZ_SETUP_URL,
            "license": "BSD-3-Clause",
            "detail": "Instalator już jest na dysku E:.",
        }
    if not confirm_download:
        return {
            "status": "REQUIRES_APPROVAL",
            "source": OPENTOONZ_SETUP_URL,
            "version": OPENTOONZ_SETUP_VERSION,
            "license": "BSD-3-Clause",
            "install_location": str(OPENTOONZ_TOOLS),
            "detail": "Potwierdź pobranie oficjalnego OpenToonzSetup.exe (confirm_download=true).",
        }
    OPENTOONZ_TOOLS.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, follow_redirects=True) as http:
        response = http.get(OPENTOONZ_SETUP_URL)
        if response.is_error:
            return {"status": "FAILED", "detail": f"Download HTTP {response.status_code}"}
        target.write_bytes(response.content)
    return {
        "status": "READY",
        "path": str(target),
        "version": OPENTOONZ_SETUP_VERSION,
        "source": OPENTOONZ_SETUP_URL,
        "bytes": target.stat().st_size,
        "detail": "Instalator pobrany. Uruchomienie wymaga osobnego confirm_run=true.",
    }


def run_opentoonz_installer(*, confirm_run: bool = False) -> dict[str, Any]:
    target = OPENTOONZ_TOOLS / "OpenToonzSetup.exe"
    if not target.is_file():
        return {"status": "REQUIRES_SETUP", "detail": "Brak instalatora. Najpierw prepare z confirm_download."}
    if not confirm_run:
        return {
            "status": "REQUIRES_APPROVAL",
            "path": str(target),
            "detail": "Uruchomienie GUI instalatora wymaga confirm_run=true.",
        }
    import subprocess

    subprocess.Popen([str(target)], cwd=str(OPENTOONZ_TOOLS), shell=False)
    return {
        "status": "RUNNING",
        "path": str(target),
        "detail": "Uruchomiono oficjalny OpenToonzSetup.exe. Po instalacji wskaż OpenToonz.exe w Settings.",
    }


def set_opentoonz_path(path: str, root: Path | None = None) -> dict[str, Any]:
    cleaned = path.strip().strip('"')
    if cleaned and not cleaned.lower().endswith(".exe"):
        return {"status": "ERROR", "detail": "Wskaż plik OpenToonz.exe."}
    if cleaned and ".." in cleaned.replace("\\", "/"):
        return {"status": "ERROR", "detail": "Ścieżka jest nieprawidłowa."}
    state = _load(root)
    state["opentoonz_path"] = cleaned
    _save(root, state)
    return opentoonz_status(root)


def list_project_accounts(root: Path | None = None) -> dict[str, Any]:
    state = _load(root)
    mapping = state.get("project_accounts") if isinstance(state.get("project_accounts"), dict) else {}
    return {
        "projects": [
            {"project_id": project, "platform_or_account": mapping.get(project)}
            for project in PROJECTS
        ]
    }


def set_project_account(project_id: str, platform_id: str | None, root: Path | None = None) -> dict[str, Any]:
    if project_id not in PROJECTS:
        return {"status": "ERROR", "detail": "Nieznany projekt."}
    if platform_id:
        platform = platform_id.split(":", 1)[0]
        if platform not in {item["id"] for item in SOCIAL_PLATFORMS}:
            return {"status": "ERROR", "detail": "Nieznana platforma."}
    state = _load(root)
    mapping = state.get("project_accounts") if isinstance(state.get("project_accounts"), dict) else {}
    mapping[project_id] = platform_id
    state["project_accounts"] = mapping
    _save(root, state)
    return {"project_id": project_id, "platform_id": platform_id}

