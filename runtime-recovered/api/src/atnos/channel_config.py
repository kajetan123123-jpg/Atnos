from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from atnos.core.config import Settings


DEFAULT_CHANNEL_CONFIG: dict[str, Any] = {
    "channel_id": "",
    "channel_name": "YT Finanse PL",
    "language": "pl",
    "region": "PL",
    "format": "shorts",
    "max_duration_seconds": 60,
    "niches": [
        "finanse osobiste",
        "oszczedzanie pieniedzy",
        "zarabianie online",
    ],
    "target_audience": "Polacy 18-35, ktorzy chca prosto zrozumiec pieniadze i unikac kosztownych bledow.",
    "visual_style": "dark finance, kontrastowe wykresy, szybkie ciecia, czytelne polskie napisy",
    "tone": "konkretny, lekko dramatyczny, bez lania wody",
    "reference_channels": [
        "https://www.youtube.com/@alux",
    ],
    "competitor_channels": [],
    "publish_schedule": {
        "enabled": False,
        "frequency_per_week": 3,
        "days_of_week": ["mon", "wed", "fri"],
        "time_utc": "14:00",
    },
    "production": {
        "privacy_status": "private",
        "require_qa": True,
        "auto_upload_private": True,
        "allow_public_upload": False,
    },
    "monetization": {
        "estimated": False,
        "currency": "PLN",
        "notes": "Brak realnych danych zarobkowych do czasu podpiecia YouTube Analytics.",
    },
}


def channel_config_path(settings: Settings) -> Path:
    """Zwraca sciezke do edytowalnej konfiguracji kanalu."""
    return (settings.app_root / "config" / "channel_config.json").resolve()


def normalize_channel_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scala konfiguracje z domyslna struktura bez usuwania nieznanych pol."""
    config = deepcopy(DEFAULT_CHANNEL_CONFIG)
    if raw:
        _deep_merge(config, raw)

    config["niches"] = _normalize_string_list(config.get("niches"))
    config["reference_channels"] = _normalize_string_list(config.get("reference_channels"))
    config["competitor_channels"] = _normalize_string_list(config.get("competitor_channels"))
    config["channel_id"] = str(config.get("channel_id") or "").strip()
    config["channel_name"] = str(config.get("channel_name") or "").strip()
    config["language"] = str(config.get("language") or "pl").strip() or "pl"
    config["region"] = str(config.get("region") or "PL").strip().upper() or "PL"
    config["format"] = str(config.get("format") or "shorts").strip() or "shorts"

    try:
        config["max_duration_seconds"] = max(1, int(config.get("max_duration_seconds") or 60))
    except (TypeError, ValueError):
        config["max_duration_seconds"] = 60

    return config


def channel_config_warnings(config: dict[str, Any]) -> list[str]:
    """Zwraca nieblokujace ostrzezenia dla UI i automatyzacji."""
    warnings: list[str] = []
    if not config.get("niches"):
        warnings.append("Brak nisz. Atnos moze zapisac konfiguracje, ale nie powinien generowac tematow bez niszy.")
    if not str(config.get("channel_name") or "").strip():
        warnings.append("Brak nazwy kanalu. Uzupelnij ja w ustawieniach, zeby logi i raporty byly czytelne.")
    return warnings


def load_channel_config(settings: Settings) -> tuple[dict[str, Any], list[str], Path]:
    """Wczytuje konfiguracje kanalu; tworzy domyslny plik przy pierwszym uruchomieniu."""
    path = channel_config_path(settings)
    if not path.exists():
        config = normalize_channel_config()
        save_channel_config(settings, config)
        return config, channel_config_warnings(config), path

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Niepoprawny JSON w konfiguracji kanalu: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Konfiguracja kanalu musi byc obiektem JSON.")

    config = normalize_channel_config(raw)
    return config, channel_config_warnings(config), path


def save_channel_config(settings: Settings, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], Path]:
    """Zapisuje konfiguracje kanalu atomowo do config/channel_config.json."""
    config = normalize_channel_config(raw)
    path = channel_config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return config, channel_config_warnings(config), path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.splitlines() if "\n" in value else value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        return []
    return [str(item).strip() for item in items if str(item).strip()]
