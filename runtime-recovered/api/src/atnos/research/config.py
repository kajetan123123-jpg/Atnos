from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from atnos.core.paths import atnos_state_dir


CONFIG_PATH = atnos_state_dir() / "config.json"


def _load_local_dotenv() -> dict[str, str]:
    """Wczytaj proste wartości KEY=VALUE z lokalnego .env bez eksportowania sekretów do procesu."""
    candidates = [Path.cwd() / ".env"]
    candidates.extend(parent / ".env" for parent in Path(__file__).resolve().parents)

    values: dict[str, str] = {}
    for path in candidates:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in values:
                values[key] = value
        break
    return values


def load_research_config() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "serpapi_key": "",
        "brave_search_key": "",
        "default_geo": "PL",
        "default_language": "pl",
        "cache_ttl_hours": 6,
    }
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            stored = {}
        if isinstance(stored, dict) and isinstance(stored.get("research"), dict):
            defaults.update(stored["research"])

    dotenv = _load_local_dotenv()

    defaults["serpapi_key"] = os.getenv(
        "SERPAPI_KEY",
        str(defaults["serpapi_key"] or dotenv.get("SERPAPI_KEY") or ""),
    )
    defaults["brave_search_key"] = os.getenv(
        "BRAVE_SEARCH_KEY",
        str(defaults["brave_search_key"] or dotenv.get("BRAVE_SEARCH_KEY") or ""),
    )
    defaults["default_geo"] = os.getenv("ATNOS_RESEARCH_GEO", str(defaults["default_geo"] or "PL"))
    defaults["default_language"] = os.getenv(
        "ATNOS_RESEARCH_LANGUAGE",
        str(defaults["default_language"] or "pl"),
    )
    return defaults
