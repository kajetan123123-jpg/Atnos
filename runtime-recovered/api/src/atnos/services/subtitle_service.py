from __future__ import annotations

import math
import re
import wave
from pathlib import Path

from atnos.core.config import Settings
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


class SubtitleService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)

    def generate(
        self,
        *,
        text: str,
        audio_path: Path,
        conversation_id: int | None,
    ) -> ManagedArtifactDescriptor:
        segments = self._split_segments(text)
        duration_sec = self._audio_duration(audio_path)
        if not segments:
            segments = ["ATNOS wygenerowal material bez dodatkowych podpisow."]
        if duration_sec <= 0:
            duration_sec = max(4.0, len(segments) * 2.5)

        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".srt")
        chunk_duration = max(1.2, duration_sec / max(1, len(segments)))
        cursor = 0.0
        lines: list[str] = []
        for index, segment in enumerate(segments, start=1):
            start = cursor
            end = min(duration_sec, start + chunk_duration)
            lines.extend(
                [
                    str(index),
                    f"{self._format_time(start)} --> {self._format_time(end)}",
                    segment,
                    "",
                ]
            )
            cursor = end
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-subtitles.srt",
            media_type="application/x-subrip",
        )

    @staticmethod
    def _split_segments(text: str) -> list[str]:
        normalized = " ".join(text.replace("\r", "\n").split())
        if not normalized:
            return []
        raw_segments = re.split(r"(?<=[.!?])\s+", normalized)
        return [item.strip() for item in raw_segments if item.strip()]

    @staticmethod
    def _audio_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
        except wave.Error:
            return 0.0
        if rate <= 0:
            return 0.0
        return frames / float(rate)

    @staticmethod
    def _format_time(value: float) -> str:
        total_ms = max(0, int(math.floor(value * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
