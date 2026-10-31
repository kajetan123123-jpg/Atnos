from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import json
from pathlib import Path
from statistics import median
from typing import Any

from atnos.core.config import Settings
from memory.obsidian_writer import ObsidianWriter

from .channel_logging import ChannelOpsLogger
from .channel_models import ContentItem, utc_now
from .channel_ops_service import ChannelOpsService
from .content_strategy import (
    StrategyPatternReport,
    TopicRecommendation,
    extract_format_signature,
    is_clickbait_like,
    topic_risk_level,
)


METRIC_KEYS = (
    "views",
    "averageViewDuration",
    "estimatedMinutesWatched",
    "likes",
    "shares",
    "subscribersGained",
    "subscribersLost",
)


@dataclass(frozen=True, slots=True)
class VideoPerformanceScore:
    overall_score: float
    retention_score: float
    engagement_score: float
    growth_score: float
    reach_score: float
    quality_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChannelLearningReport:
    channel_id: str
    generated_at: str
    winning_patterns: list[str]
    weak_patterns: list[str]
    risky_topics: list[str]
    repeated_formats: list[str]
    recommended_topics: list[TopicRecommendation]
    do_not_publish: list[str]
    obsidian_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recommended_topics"] = [asdict(item) for item in self.recommended_topics]
        return payload


class AnalyticsFeedbackService:
    def __init__(self, settings: Settings, *, channel_ops: ChannelOpsService | None = None) -> None:
        self.settings = settings
        self.channel_ops = channel_ops or ChannelOpsService(settings)
        self.root = settings.data_dir / "channel_ops" / "analytics"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logger = ChannelOpsLogger(settings.data_dir / "logs" / "channel_ops.jsonl")
        self.obsidian = ObsidianWriter(settings)

    def ingest_video_metrics(self, channel_id: str, video_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
        channel = self.channel_ops.registry.get_channel(channel_id)
        normalized_video_id = video_id.strip()
        if not normalized_video_id:
            raise ValueError("video_id cannot be empty.")
        normalized_metrics = self._normalize_metrics(metrics)
        score = self.score_video_performance(normalized_metrics)
        item = self._find_item_by_video_id(channel.channel_id, normalized_video_id)
        topic = item.topic if item else str(metrics.get("topic") or normalized_video_id)
        record = {
            "timestamp": utc_now(),
            "channel_id": channel.channel_id,
            "video_id": normalized_video_id,
            "topic": topic,
            "format_signature": extract_format_signature(topic),
            "risk_level": topic_risk_level(topic),
            "metrics": normalized_metrics,
            "score": score.to_dict(),
        }
        self._append_jsonl(self._metrics_path(channel.channel_id), record)
        if item:
            self.channel_ops.update_analytics(
                item.item_id,
                {
                    "video_id": normalized_video_id,
                    "metrics": normalized_metrics,
                    "score": score.to_dict(),
                },
            )
        self.logger.log(
            "video_metrics_ingested",
            {
                "channel_id": channel.channel_id,
                "video_id": normalized_video_id,
                "overall_score": score.overall_score,
            },
        )
        return record

    def score_video_performance(self, metrics: dict[str, Any]) -> VideoPerformanceScore:
        normalized = self._normalize_metrics(metrics)
        views = max(0.0, normalized["views"])
        average_view_duration = max(0.0, normalized["averageViewDuration"])
        estimated_minutes = max(0.0, normalized["estimatedMinutesWatched"])
        likes = max(0.0, normalized["likes"])
        shares = max(0.0, normalized["shares"])
        gained = max(0.0, normalized["subscribersGained"])
        lost = max(0.0, normalized["subscribersLost"])

        implied_duration = (estimated_minutes * 60.0 / views) if views > 0 else 0.0
        retention_seconds = max(average_view_duration, implied_duration)
        retention_score = _clamp((retention_seconds / 35.0) * 10.0)
        engagement_rate = ((likes + 2.0 * shares) / views) if views > 0 else 0.0
        engagement_score = _clamp(engagement_rate * 250.0)
        subscriber_rate = ((gained - lost) / views) if views > 0 else 0.0
        growth_score = _clamp(5.0 + subscriber_rate * 500.0)
        reach_score = _clamp(_log_like_score(views))

        overall = _clamp(
            retention_score * 0.35
            + engagement_score * 0.30
            + growth_score * 0.20
            + reach_score * 0.15
        )
        warning = None
        if views >= 200 and engagement_rate < 0.005:
            warning = "high_reach_low_engagement"
        if lost > gained and views >= 50:
            warning = "subscriber_loss"
        return VideoPerformanceScore(
            overall_score=round(overall, 2),
            retention_score=round(retention_score, 2),
            engagement_score=round(engagement_score, 2),
            growth_score=round(growth_score, 2),
            reach_score=round(reach_score, 2),
            quality_warning=warning,
        )

    def identify_winning_patterns(self, channel_id: str) -> list[str]:
        records = self._records(channel_id)
        if not records:
            return []
        threshold = max(7.0, self._score_median(records) + 0.5)
        return self._patterns_by_score(records, minimum_score=threshold)

    def identify_weak_patterns(self, channel_id: str) -> list[str]:
        records = self._records(channel_id)
        if not records:
            return []
        threshold = max(5.5, self._score_median(records) - 0.5)
        weak = self._patterns_by_score(records, maximum_score=threshold)
        weak.extend(
            sorted(
                {
                    str(record["format_signature"])
                    for record in records
                    if record.get("score", {}).get("quality_warning") in {"high_reach_low_engagement", "subscriber_loss"}
                }
            )
        )
        return sorted(set(weak))

    def recommend_next_topics(self, channel_id: str) -> list[TopicRecommendation]:
        channel = self.channel_ops.registry.get_channel(channel_id)
        records = self._records(channel.channel_id)
        pattern_report = self._pattern_report(channel.channel_id)
        winners = pattern_report.winning_patterns or ["why_explainer", "how_to", "story_case"]
        weak = set(pattern_report.weak_patterns)
        existing_topics = {item.topic.casefold() for item in self.channel_ops.list_queue(channel.channel_id)}
        recommendations: list[TopicRecommendation] = []

        for pattern in winners:
            if pattern in weak:
                continue
            candidate = self._topic_from_pattern(channel.niche, pattern)
            if candidate.casefold() in existing_topics:
                continue
            risk = topic_risk_level(candidate)
            recommendations.append(
                TopicRecommendation(
                    topic=candidate,
                    reason=f"Format `{pattern}` wypada najlepiej w danych kanalu.",
                    risk_level=risk,
                    based_on=self._top_video_ids_for_pattern(records, pattern),
                )
            )
            if len(recommendations) >= 5:
                break

        if not recommendations:
            fallback = f"Dlaczego {channel.niche} ma sens dla poczatkujacych"
            recommendations.append(
                TopicRecommendation(
                    topic=fallback,
                    reason="Fallback bez clickbaitu: konkretny explainer zgodny z nisza.",
                    risk_level=topic_risk_level(fallback),
                )
            )
        return recommendations

    def identify_weak_patterns_report(self, channel_id: str) -> StrategyPatternReport:
        return self._pattern_report(channel_id)

    def identify_weak_patterns_alias(self, channel_id: str) -> list[str]:
        return self.identify_weak_patterns(channel_id)

    def update_channel_style_guide(self, channel_id: str) -> dict[str, Any]:
        channel = self.channel_ops.registry.get_channel(channel_id)
        report = self.build_learning_report(channel.channel_id)
        style_path = Path(channel.style_guide_path).resolve() if channel.style_guide_path else self._style_guide_path(channel.channel_id)
        style_path.parent.mkdir(parents=True, exist_ok=True)
        style_path.write_text(self._style_guide_markdown(report), encoding="utf-8")
        updated = self.channel_ops.register_channel(
            **{
                **channel.model_dump(mode="json"),
                "style_guide_path": str(style_path),
            }
        )
        self.logger.log(
            "channel_style_guide_updated",
            {"channel_id": updated.channel_id, "style_guide_path": updated.style_guide_path},
        )
        return {
            "channel_id": updated.channel_id,
            "style_guide_path": updated.style_guide_path,
            "report": report.to_dict(),
        }

    def build_learning_report(self, channel_id: str) -> ChannelLearningReport:
        channel = self.channel_ops.registry.get_channel(channel_id)
        pattern_report = self._pattern_report(channel.channel_id)
        recommendations = self.recommend_next_topics(channel.channel_id)
        do_not_publish = self._do_not_publish(channel.channel_id, pattern_report)
        generated_at = utc_now()
        report = ChannelLearningReport(
            channel_id=channel.channel_id,
            generated_at=generated_at,
            winning_patterns=pattern_report.winning_patterns,
            weak_patterns=pattern_report.weak_patterns,
            risky_topics=pattern_report.risky_topics,
            repeated_formats=pattern_report.repeated_formats,
            recommended_topics=recommendations,
            do_not_publish=do_not_publish,
        )
        markdown = self._learning_report_markdown(report)
        report_path = self._report_path(channel.channel_id)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown, encoding="utf-8")
        obsidian = self.obsidian.write_note(
            self.settings.atnos_obsidian_shorts_dir,
            f"channel-learning-{channel.channel_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.md",
            markdown,
        )
        report = replace(report, obsidian_path=obsidian.path)
        self.logger.log(
            "channel_learning_report_created",
            {
                "channel_id": channel.channel_id,
                "report_path": str(report_path),
                "obsidian_status": "written" if obsidian.written else obsidian.reason,
            },
        )
        return report

    def _pattern_report(self, channel_id: str) -> StrategyPatternReport:
        records = self._records(channel_id)
        winning = self.identify_winning_patterns(channel_id)
        weak = self.identify_weak_patterns(channel_id)
        risky = sorted(
            {
                str(record.get("topic"))
                for record in records
                if record.get("risk_level") in {"medium", "high"} or is_clickbait_like(str(record.get("topic") or ""))
            }
        )
        repeated = self._repeated_formats(records)
        return StrategyPatternReport(
            winning_patterns=winning,
            weak_patterns=weak,
            risky_topics=risky,
            repeated_formats=repeated,
        )

    def _patterns_by_score(
        self,
        records: list[dict[str, Any]],
        *,
        minimum_score: float | None = None,
        maximum_score: float | None = None,
    ) -> list[str]:
        grouped: dict[str, list[float]] = {}
        for record in records:
            pattern = str(record.get("format_signature") or "unknown")
            score = _float(record.get("score", {}).get("overall_score"))
            grouped.setdefault(pattern, []).append(score)
        selected: list[str] = []
        for pattern, scores in grouped.items():
            avg = sum(scores) / len(scores)
            if minimum_score is not None and avg >= minimum_score:
                selected.append(pattern)
            if maximum_score is not None and avg <= maximum_score:
                selected.append(pattern)
        return sorted(set(selected))

    def _repeated_formats(self, records: list[dict[str, Any]]) -> list[str]:
        counts: dict[str, int] = {}
        for record in records[-10:]:
            pattern = str(record.get("format_signature") or "unknown")
            counts[pattern] = counts.get(pattern, 0) + 1
        return sorted(pattern for pattern, count in counts.items() if count >= 4)

    def _do_not_publish(self, channel_id: str, pattern_report: StrategyPatternReport) -> list[str]:
        blocked = []
        for pattern in pattern_report.weak_patterns:
            blocked.append(f"Nie powtarzaj formatu `{pattern}` bez zmiany hooka, tempa i wartosci.")
        for topic in pattern_report.risky_topics:
            blocked.append(f"Nie publikuj dalej tematu ryzykownego bez recenzji: {topic}")
        for pattern in pattern_report.repeated_formats:
            blocked.append(f"Ogranicz powtarzalny format `{pattern}` - grozi zmeczeniem widza.")
        return blocked or ["Nie publikuj wariantow opartych na fake engagement, obietnicach zysku albo clickbaicie."]

    def _topic_from_pattern(self, niche: str, pattern: str) -> str:
        templates = {
            "comparison": f"{niche}: lokalnie czy w chmurze - co wybrac",
            "how_to": f"Jak zaczac z {niche} bez chaosu",
            "why_explainer": f"Dlaczego {niche} oszczedza czas w codziennej pracy",
            "listicle": f"3 praktyczne zastosowania: {niche}",
            "mistake_warning": f"Najczestszy blad przy wdrazaniu: {niche}",
            "story_case": f"Historia jednego workflow: {niche} w praktyce",
            "explainer": f"{niche}: prosty przyklad, ktory rozumie kazdy",
        }
        return templates.get(pattern, templates["explainer"])

    def _top_video_ids_for_pattern(self, records: list[dict[str, Any]], pattern: str) -> tuple[str, ...]:
        matching = [
            record
            for record in records
            if str(record.get("format_signature")) == pattern
        ]
        matching.sort(key=lambda record: _float(record.get("score", {}).get("overall_score")), reverse=True)
        return tuple(str(record.get("video_id")) for record in matching[:3] if record.get("video_id"))

    def _score_median(self, records: list[dict[str, Any]]) -> float:
        scores = [_float(record.get("score", {}).get("overall_score")) for record in records]
        return float(median(scores)) if scores else 0.0

    def _normalize_metrics(self, metrics: dict[str, Any]) -> dict[str, float]:
        return {key: max(0.0, _float(metrics.get(key))) for key in METRIC_KEYS}

    def _find_item_by_video_id(self, channel_id: str, video_id: str) -> ContentItem | None:
        for item in self.channel_ops.list_queue(channel_id):
            if item.youtube_video_id == video_id:
                return item
        return None

    def _records(self, channel_id: str) -> list[dict[str, Any]]:
        path = self._metrics_path(channel_id)
        if not path.exists():
            return []
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                records.append(raw)
        return records

    def _metrics_path(self, channel_id: str) -> Path:
        return self.root / channel_id / "video_metrics.jsonl"

    def _report_path(self, channel_id: str) -> Path:
        return self.root / channel_id / "learning_report.md"

    def _style_guide_path(self, channel_id: str) -> Path:
        return self.channel_ops.registry.channel_dir(channel_id) / "style_guide.md"

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _learning_report_markdown(self, report: ChannelLearningReport) -> str:
        return "\n".join(
            [
                "# Channel Learning Loop",
                "",
                f"- channel_id: `{report.channel_id}`",
                f"- generated_at: `{report.generated_at}`",
                "",
                "## Winning patterns",
                _markdown_list(report.winning_patterns),
                "",
                "## Weak patterns",
                _markdown_list(report.weak_patterns),
                "",
                "## Recommended next topics",
                _markdown_list([f"{item.topic} - {item.reason} (risk: {item.risk_level})" for item in report.recommended_topics]),
                "",
                "## Do not publish",
                _markdown_list(report.do_not_publish),
            ]
        )

    def _style_guide_markdown(self, report: ChannelLearningReport) -> str:
        return "\n".join(
            [
                "# Channel Style Guide",
                "",
                f"Updated: `{report.generated_at}`",
                "",
                "## Use more",
                _markdown_list(report.winning_patterns),
                "",
                "## Avoid or repair",
                _markdown_list(report.weak_patterns + report.repeated_formats),
                "",
                "## Topic guardrails",
                _markdown_list(report.do_not_publish),
                "",
                "## Next topic candidates",
                _markdown_list([item.topic for item in report.recommended_topics]),
            ]
        )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, minimum: float = 0.0, maximum: float = 10.0) -> float:
    return max(minimum, min(maximum, value))


def _log_like_score(value: float) -> float:
    if value <= 0:
        return 0.0
    if value < 100:
        return value / 20.0
    if value < 1000:
        return 5.0 + (value - 100.0) / 180.0
    return min(10.0, 10.0 + (value - 1000.0) / 5000.0)


def _markdown_list(items: list[str]) -> str:
    if not items:
        return "- brak danych"
    return "\n".join(f"- {item}" for item in items)


def ingest_video_metrics(channel_id: str, video_id: str, metrics: dict[str, Any]) -> dict[str, Any]:
    return AnalyticsFeedbackService(Settings()).ingest_video_metrics(channel_id, video_id, metrics)


def score_video_performance(metrics: dict[str, Any]) -> VideoPerformanceScore:
    return AnalyticsFeedbackService(Settings()).score_video_performance(metrics)


def identify_winning_patterns(channel_id: str) -> list[str]:
    return AnalyticsFeedbackService(Settings()).identify_winning_patterns(channel_id)


def identify_weak_patterns(channel_id: str) -> list[str]:
    return AnalyticsFeedbackService(Settings()).identify_weak_patterns(channel_id)


def recommend_next_topics(channel_id: str) -> list[TopicRecommendation]:
    return AnalyticsFeedbackService(Settings()).recommend_next_topics(channel_id)


def update_channel_style_guide(channel_id: str) -> dict[str, Any]:
    return AnalyticsFeedbackService(Settings()).update_channel_style_guide(channel_id)
