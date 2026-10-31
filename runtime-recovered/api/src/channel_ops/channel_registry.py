from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings

from .channel_logging import ChannelOpsLogger
from .channel_models import ChannelProfile, utc_now


class ChannelOpsError(RuntimeError):
    pass


class ChannelNotFoundError(ChannelOpsError):
    pass


class ChannelRegistry:
    def __init__(self, settings: Settings, logger: ChannelOpsLogger | None = None) -> None:
        self.settings = settings
        self.root = (settings.data_dir / "channel_ops").resolve()
        self.channels_dir = self.root / "channels"
        self.channels_path = self.root / "channels.json"
        self.logger = logger or ChannelOpsLogger(settings.data_dir / "logs" / "channel_ops.jsonl")
        self.root.mkdir(parents=True, exist_ok=True)
        self.channels_dir.mkdir(parents=True, exist_ok=True)
        if not self.channels_path.exists():
            self._write_channels({})

    def register_channel(self, **kwargs: Any) -> ChannelProfile:
        existing = self._read_channels()
        channel = ChannelProfile(**kwargs)
        previous = existing.get(channel.channel_id)
        if previous:
            channel = ChannelProfile(
                **{
                    **channel.model_dump(mode="json"),
                    "created_at": previous.get("created_at", channel.created_at),
                    "updated_at": utc_now(),
                }
            )

        existing[channel.channel_id] = channel.model_dump(mode="json")
        self._write_channels(existing)
        self.channel_dir(channel.channel_id).mkdir(parents=True, exist_ok=True)
        self.logger.log(
            "channel_registered",
            {
                "channel_id": channel.channel_id,
                "upload_policy": channel.upload_policy,
                "automation_level": channel.automation_level,
            },
        )
        return channel

    def list_channels(self) -> list[ChannelProfile]:
        return [
            ChannelProfile(**data)
            for _, data in sorted(self._read_channels().items(), key=lambda item: item[0])
        ]

    def get_channel(self, channel_id: str) -> ChannelProfile:
        normalized_id = channel_id.strip().lower()
        data = self._read_channels().get(normalized_id)
        if data is None:
            raise ChannelNotFoundError(f"Channel not found: {channel_id}")
        return ChannelProfile(**data)

    def channel_dir(self, channel_id: str) -> Path:
        return self.channels_dir / channel_id.strip().lower()

    def _read_channels(self) -> dict[str, dict[str, Any]]:
        if not self.channels_path.exists():
            return {}
        try:
            raw = json.loads(self.channels_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelOpsError(f"Invalid channels registry JSON: {self.channels_path}") from exc
        if not isinstance(raw, dict):
            raise ChannelOpsError(f"Invalid channels registry shape: {self.channels_path}")
        return raw

    def _write_channels(self, channels: dict[str, dict[str, Any]]) -> None:
        self.channels_path.parent.mkdir(parents=True, exist_ok=True)
        self.channels_path.write_text(
            json.dumps(channels, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
