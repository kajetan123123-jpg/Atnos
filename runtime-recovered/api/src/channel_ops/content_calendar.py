from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .channel_logging import ChannelOpsLogger
from .channel_models import ContentItem, ContentStatus
from .channel_registry import ChannelNotFoundError, ChannelOpsError, ChannelRegistry
from .topic_bank import TopicBank


class ContentItemNotFoundError(ChannelOpsError):
    pass


class ContentCalendar:
    def __init__(self, registry: ChannelRegistry, logger: ChannelOpsLogger) -> None:
        self.registry = registry
        self.logger = logger

    def enqueue_topic(self, channel_id: str, topic: str) -> ContentItem:
        channel = self.registry.get_channel(channel_id)
        queue = self._read_queue(channel.channel_id)
        item = ContentItem(channel_id=channel.channel_id, topic=topic)
        queue.append(item)
        self._write_queue(channel.channel_id, queue)
        self.logger.log(
            "content_topic_enqueued",
            {"channel_id": channel.channel_id, "item_id": item.item_id, "topic": item.topic},
        )
        return item

    def list_queue(self, channel_id: str) -> list[ContentItem]:
        channel = self.registry.get_channel(channel_id)
        return sorted(self._read_queue(channel.channel_id), key=lambda item: (item.created_at, item.item_id))

    def create_daily_plan(self, channel_id: str, count: int) -> list[ContentItem]:
        if count <= 0:
            return []

        channel = self.registry.get_channel(channel_id)
        queue = self._read_queue(channel.channel_id)
        plan_statuses = {
            ContentStatus.IDEA,
            ContentStatus.PLANNED,
            ContentStatus.SCRIPTED,
            ContentStatus.RENDERING,
            ContentStatus.RENDERED,
        }
        plan = [item for item in queue if ContentStatus(item.status) in plan_statuses]

        next_number = len(queue) + 1
        existing_topics = {item.topic for item in queue}
        while len(plan) < count:
            suggestion = TopicBank(channel_dir=self.registry.channel_dir(channel.channel_id)).suggest_topic(
                channel=channel,
                sequence_number=next_number,
                existing_topics=existing_topics,
            )
            topic = suggestion.topic
            next_number += 1
            if topic in existing_topics:
                continue
            item = ContentItem(channel_id=channel.channel_id, topic=topic)
            queue.append(item)
            plan.append(item)
            existing_topics.add(topic)
            self.logger.log(
                "content_topic_suggested",
                {
                    "channel_id": channel.channel_id,
                    "item_id": item.item_id,
                    "topic": item.topic,
                    "source": suggestion.source,
                    "risk_level": suggestion.risk_level,
                },
            )

        self._write_queue(channel.channel_id, queue)
        selected = plan[:count]
        self.logger.log(
            "daily_plan_created",
            {
                "channel_id": channel.channel_id,
                "count": count,
                "item_ids": [item.item_id for item in selected],
            },
        )
        return selected

    def find_item(self, item_id: str) -> ContentItem:
        for channel in self.registry.list_channels():
            for item in self._read_queue(channel.channel_id):
                if item.item_id == item_id:
                    return item
        raise ContentItemNotFoundError(f"Content item not found: {item_id}")

    def update_item(self, item: ContentItem) -> ContentItem:
        self.registry.get_channel(item.channel_id)
        queue = self._read_queue(item.channel_id)
        updated_queue: list[ContentItem] = []
        found = False
        for existing in queue:
            if existing.item_id == item.item_id:
                updated_queue.append(item)
                found = True
            else:
                updated_queue.append(existing)
        if not found:
            raise ContentItemNotFoundError(f"Content item not found: {item.item_id}")
        self._write_queue(item.channel_id, updated_queue)
        return item

    def queue_path(self, channel_id: str) -> Path:
        return self.registry.channel_dir(channel_id) / "queue.json"

    def _read_queue(self, channel_id: str) -> list[ContentItem]:
        path = self.queue_path(channel_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ChannelOpsError(f"Invalid channel queue JSON: {path}") from exc
        if not isinstance(raw, list):
            raise ChannelOpsError(f"Invalid channel queue shape: {path}")
        return [ContentItem(**item) for item in raw]

    def _write_queue(self, channel_id: str, queue: list[ContentItem]) -> None:
        try:
            self.registry.get_channel(channel_id)
        except ChannelNotFoundError:
            raise
        path = self.queue_path(channel_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: list[dict[str, Any]] = [item.model_dump(mode="json") for item in queue]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
