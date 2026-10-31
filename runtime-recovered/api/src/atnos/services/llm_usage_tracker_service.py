from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from atnos.core.config import Settings


WARSAW_TZ = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True, slots=True)
class LlmUsageSnapshot:
    date: str
    gemini_requests_today: int = 0
    ollama_main_requests_today: int = 0
    ollama_fast_requests_today: int = 0
    ollama_vision_requests_today: int = 0

    def to_dict(self) -> dict[str, int | str]:
        return {
            "date": self.date,
            "gemini_requests_today": self.gemini_requests_today,
            "ollama_main_requests_today": self.ollama_main_requests_today,
            "ollama_fast_requests_today": self.ollama_fast_requests_today,
            "ollama_vision_requests_today": self.ollama_vision_requests_today,
        }


class LlmUsageTrackerService:
    def __init__(self, settings: Settings, *, now: datetime | None = None) -> None:
        self.settings = settings
        self.path = settings.llm_usage_file
        self._now = now

    def today(self) -> str:
        value = self._now or datetime.now(WARSAW_TZ)
        if value.tzinfo is None:
            value = value.replace(tzinfo=WARSAW_TZ)
        return value.astimezone(WARSAW_TZ).date().isoformat()

    def get_today_stats(self) -> LlmUsageSnapshot:
        payload = self._read_payload()
        today = self.today()
        if payload.get("date") != today:
            return LlmUsageSnapshot(date=today)
        return LlmUsageSnapshot(
            date=today,
            gemini_requests_today=self._int(payload.get("gemini_requests_today")),
            ollama_main_requests_today=self._int(payload.get("ollama_main_requests_today")),
            ollama_fast_requests_today=self._int(payload.get("ollama_fast_requests_today")),
            ollama_vision_requests_today=self._int(payload.get("ollama_vision_requests_today")),
        )

    def record_request(self, *, provider_key: str, model_name: str) -> LlmUsageSnapshot:
        snapshot = self.get_today_stats()
        payload = snapshot.to_dict()
        counter_key = self._counter_key(provider_key=provider_key, model_name=model_name)
        if counter_key is not None:
            payload[counter_key] = int(payload.get(counter_key, 0)) + 1
        updated = LlmUsageSnapshot(
            date=str(payload["date"]),
            gemini_requests_today=self._int(payload.get("gemini_requests_today")),
            ollama_main_requests_today=self._int(payload.get("ollama_main_requests_today")),
            ollama_fast_requests_today=self._int(payload.get("ollama_fast_requests_today")),
            ollama_vision_requests_today=self._int(payload.get("ollama_vision_requests_today")),
        )
        self._write_payload(updated.to_dict())
        return updated

    def check_gemini_limit(self) -> str:
        limit = int(self.settings.gemini_daily_request_limit)
        if limit <= 0:
            return "ok"
        current = self.get_today_stats().gemini_requests_today
        if current >= limit:
            return "exceeded"
        warning_threshold = min(int(self.settings.gemini_daily_warning_threshold), limit)
        if current >= warning_threshold:
            return "warning"
        return "ok"

    def _counter_key(self, *, provider_key: str, model_name: str) -> str | None:
        provider = provider_key.strip().lower()
        model = model_name.strip().lower()
        if provider == "gemini-cloud":
            return "gemini_requests_today"
        if provider != "ollama-local":
            return None
        if "vl" in model or "vision" in model:
            return "ollama_vision_requests_today"
        fast_model = (self.settings.ollama_fast_model or "qwen3:8b").strip().lower()
        if model == fast_model:
            return "ollama_fast_requests_today"
        return "ollama_main_requests_today"

    def _read_payload(self) -> dict[str, object]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write_payload(self, payload: dict[str, int | str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = Path(f"{self.path}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _int(value: object) -> int:
        return value if isinstance(value, int) else 0
