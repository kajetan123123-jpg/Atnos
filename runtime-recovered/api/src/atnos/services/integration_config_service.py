from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.integration_config_repository import IntegrationConfigRepository

OBSIDIAN_KEY = "obsidian"
OBSIDIAN_VAULT_NAME = "Atnos-Brain"


@dataclass(frozen=True, slots=True)
class ObsidianEffectiveConfig:
    vault_path: Path | None
    export_enabled: bool
    source: str
    path_exists: bool
    warnings: tuple[str, ...]


class IntegrationConfigService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = IntegrationConfigRepository(session)

    def get_obsidian_config(self) -> ObsidianEffectiveConfig:
        config = self.repository.get_by_key(OBSIDIAN_KEY)
        if config is not None:
            vault_path = Path(config.local_path) if config.local_path else None
            return self._build_effective_config(
                vault_path=vault_path,
                export_enabled=config.export_enabled,
                source="local_config",
            )

        return self._build_effective_config(
            vault_path=self.settings.obsidian_vault_path,
            export_enabled=self.settings.obsidian_export_enabled,
            source="env",
        )

    def update_obsidian_config(
        self,
        *,
        vault_path: str | None,
        export_enabled: bool,
    ) -> ObsidianEffectiveConfig:
        normalized_path = self._normalize_vault_path(vault_path)
        effective_export_enabled = export_enabled if normalized_path is not None else False
        config = self.repository.upsert(
            integration_key=OBSIDIAN_KEY,
            local_path=str(normalized_path) if normalized_path is not None else None,
            export_enabled=effective_export_enabled,
        )
        return self._build_effective_config(
            vault_path=Path(config.local_path) if config.local_path else None,
            export_enabled=config.export_enabled,
            source="local_config",
        )

    @staticmethod
    def _normalize_vault_path(value: str | None) -> Path | None:
        if value is None or not value.strip():
            return None

        raw_path = Path(value.strip()).expanduser()
        if ".." in raw_path.parts:
            raise ValueError("Obsidian vault path cannot contain traversal segments.")

        resolved = raw_path.resolve()
        if not resolved.exists():
            raise ValueError("Obsidian vault path does not exist.")
        if not resolved.is_dir():
            raise ValueError("Obsidian vault path must be a directory.")
        return resolved

    @staticmethod
    def _build_effective_config(
        *,
        vault_path: Path | None,
        export_enabled: bool,
        source: str,
    ) -> ObsidianEffectiveConfig:
        resolved_path = vault_path.resolve() if vault_path is not None else None
        path_exists = bool(resolved_path and resolved_path.exists() and resolved_path.is_dir())
        warnings: list[str] = []

        if resolved_path is None:
            warnings.append("OBSIDIAN_VAULT_PATH is not configured.")
        elif not path_exists:
            warnings.append("Configured Obsidian vault path is not an existing directory.")

        if not export_enabled:
            warnings.append("OBSIDIAN_EXPORT_ENABLED is false; confirm export will be unavailable.")

        return ObsidianEffectiveConfig(
            vault_path=resolved_path if path_exists else resolved_path,
            export_enabled=export_enabled,
            source=source,
            path_exists=path_exists,
            warnings=tuple(warnings),
        )
