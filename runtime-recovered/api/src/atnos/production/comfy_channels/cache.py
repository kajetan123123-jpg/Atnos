"""Deterministic generation cache fingerprints (channel-scoped)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def fingerprint_request(payload: dict[str, Any]) -> str:
    # A reference edited in place must not reuse pixels generated from its old content.
    if payload.get('references'):
        payload = {**payload, 'reference_content_sha256': [
            hashlib.sha256(Path(reference).read_bytes()).hexdigest()
            for reference in payload['references']]}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ChannelGenerationCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        data = self._load()
        entry = data.get(fingerprint)
        return entry if isinstance(entry, dict) else None

    def put(self, fingerprint: str, entry: dict[str, Any]) -> None:
        data = self._load()
        data[fingerprint] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
