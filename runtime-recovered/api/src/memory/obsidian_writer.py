from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class ObsidianWriteResult:
    written: bool
    path: str | None
    reason: str | None = None


class ObsidianWriter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.atnos_write_obsidian_logs)

    def write_note(self, relative_dir: str, filename: str, markdown: str) -> ObsidianWriteResult:
        if not self.enabled:
            return ObsidianWriteResult(written=False, path=None, reason="obsidian_logging_disabled")
        if self.settings.obsidian_vault_path is None:
            return ObsidianWriteResult(written=False, path=None, reason="obsidian_vault_not_configured")

        vault = self.settings.obsidian_vault_path.expanduser().resolve()
        target_dir = self._resolve_safe_dir(vault, relative_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / filename).resolve()
        if not target.is_relative_to(vault):
            raise ValueError("Resolved Obsidian note path escapes the configured vault.")

        expected_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        target.write_text(markdown, encoding="utf-8")
        actual_hash = hashlib.sha256(target.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"Obsidian note write verification failed: {target}")
        return ObsidianWriteResult(written=True, path=str(target), reason=None)

    @staticmethod
    def _resolve_safe_dir(vault: Path, relative_dir: str) -> Path:
        normalized = relative_dir.replace("\\", "/").strip().strip("/")
        relative = Path(normalized or "Atnos/Agent Logs")
        if relative.is_absolute() or any(part == ".." for part in relative.parts):
            raise ValueError("Obsidian note directory must be relative to the vault.")
        target = (vault / relative).resolve()
        if not target.is_relative_to(vault):
            raise ValueError("Resolved Obsidian directory escapes the configured vault.")
        return target
