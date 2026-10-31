from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import unicodedata
from typing import Any

from atnos.agents.short_production import (
    ApprovedHook,
    PlanRequest,
    ProductionFiles,
    ShortProductionAgent,
    ShortProductionError,
)
from atnos.agents.topic_hook import TopicHookAgent, TopicPackage, TopicRequest
from atnos.core.config import Settings
from atnos.skills.channel_ops_skill import ChannelOpsIntent, detect_channel_ops_intent
from channel_ops import (
    AnalyticsFeedbackService,
    ChannelOpsService,
    ContentItem,
    ContentStatus,
    DailyChannelRunner,
    DailyChannelRunResult,
)
from channel_ops.channel_registry import ChannelNotFoundError, ChannelOpsError


@dataclass(frozen=True, slots=True)
class ChannelOpsChatOperation:
    activated: bool
    assistant_content: str | None = None
    metadata: dict[str, Any] | None = None
    selected_provider_key: str = "channel-ops"
    selected_model_key: str = "channel-ops"
    routing_reason: str = "Handled by ChannelOpsService."


class ChannelOpsChatService:
    def __init__(
        self,
        settings: Settings,
        *,
        channel_ops: ChannelOpsService | None = None,
        analytics: AnalyticsFeedbackService | None = None,
        topic_hook_agent: TopicHookAgent | None = None,
        short_production_agent: ShortProductionAgent | None = None,
    ) -> None:
        self.settings = settings
        self.channel_ops = channel_ops or ChannelOpsService(settings)
        self.analytics = analytics or AnalyticsFeedbackService(settings, channel_ops=self.channel_ops)
        self.topic_hook_agent = topic_hook_agent or TopicHookAgent(settings)
        self.short_production_agent = short_production_agent or ShortProductionAgent(settings)

    def maybe_handle(self, content: str) -> ChannelOpsChatOperation:
        topic_hook_operation = self._maybe_handle_topic_hook(content)
        if topic_hook_operation.activated:
            return topic_hook_operation

        short_production_operation = self._maybe_handle_short_production(content)
        if short_production_operation.activated:
            return short_production_operation

        intent = detect_channel_ops_intent(content)
        if intent is None:
            return ChannelOpsChatOperation(activated=False)
        try:
            if intent.kind in {"prepare_shorts", "plan_channel"}:
                return self._prepare_shorts(intent)
            if intent.kind == "approval_queue":
                return self._approval_queue(intent)
            if intent.kind == "channel_analytics":
                return self._channel_analytics(intent)
            if intent.kind == "private_upload":
                return self._private_upload(intent)
        except (ChannelNotFoundError, ChannelOpsError, ValueError) as exc:
            return ChannelOpsChatOperation(
                activated=True,
                assistant_content=self._format_error(intent, exc),
                metadata={"channel_ops": {"intent": asdict(intent), "status": "failed", "error": str(exc)}},
            )
        return ChannelOpsChatOperation(activated=False)

    def _maybe_handle_short_production(self, content: str) -> ChannelOpsChatOperation:
        normalized = _slug_text(content)
        if not normalized:
            return ChannelOpsChatOperation(activated=False)

        try:
            if self._detect_short_plan_intent(normalized):
                channel = self._resolve_channel(_extract_channel_hint(normalized))
                topic_id = _extract_production_topic_id(content, normalized)
                plan = self._create_short_plan(channel.channel_id, topic_id)
                return ChannelOpsChatOperation(
                    activated=True,
                    assistant_content=self._format_short_plan(plan),
                    metadata={
                        "short_production": {
                            "kind": "short_asset_plan",
                            "status": "planned",
                            "plan": plan.model_dump(mode="json"),
                            "plan_path": str(
                                self.short_production_agent.plan_dir(plan.channel_id, plan.plan_id) / "plan.json"
                            ),
                        }
                    },
                    selected_provider_key="short-production-agent",
                    selected_model_key="short-production-planner",
                    routing_reason="Approved topic routed to ShortProductionAgent planner.",
                )
            if self._detect_short_qa_intent(normalized):
                plan_id = _extract_plan_id(normalized)
                files = _extract_production_files(content)
                if files is None:
                    raise ValueError(
                        "Podaj sciezki: short_file_path=..., narration_file_path=..., thumbnail_file_path=..."
                    )
                qa = self.short_production_agent.run_qa(plan_id, files)
                return ChannelOpsChatOperation(
                    activated=True,
                    assistant_content=self._format_short_qa(qa),
                    metadata={
                        "short_production": {
                            "kind": "quality_review",
                            "status": qa.recommendation,
                            "qa": qa.model_dump(mode="json"),
                        }
                    },
                    selected_provider_key="short-production-agent",
                    selected_model_key="short-production-qa",
                    routing_reason="Short QA command routed to ShortProductionAgent.",
                )
            if self._detect_review_package_intent(normalized):
                plan_id = _extract_plan_id(normalized, required=False)
                package = (
                    self.short_production_agent.build_review_package(plan_id)
                    if plan_id
                    else self.short_production_agent.latest_review_package()
                )
                if package is None:
                    raise ValueError("Brak review package. Najpierw uruchom QA i zbuduj paczke review.")
                return ChannelOpsChatOperation(
                    activated=True,
                    assistant_content=self._format_review_package(package),
                    metadata={
                        "short_production": {
                            "kind": "review_package",
                            "status": package.status,
                            "review_package": package.model_dump(mode="json"),
                        }
                    },
                    selected_provider_key="short-production-agent",
                    selected_model_key="review-package-builder",
                    routing_reason="Review package command routed to ShortProductionAgent.",
                )
            if self._detect_upload_approval_intent(normalized):
                review_id = _extract_review_id(normalized)
                package = self.short_production_agent.approve_for_upload(review_id)
                return ChannelOpsChatOperation(
                    activated=True,
                    assistant_content=self._format_review_package(package),
                    metadata={
                        "short_production": {
                            "kind": "review_package_approval",
                            "status": package.status,
                            "review_package": package.model_dump(mode="json"),
                        }
                    },
                    selected_provider_key="short-production-agent",
                    selected_model_key="review-package-approval",
                    routing_reason="Upload approval command routed to ShortProductionAgent.",
                )
        except (ShortProductionError, ValueError) as exc:
            return ChannelOpsChatOperation(
                activated=True,
                assistant_content=(
                    "Nie uruchomilem ShortProductionAgent.\n"
                    f"- problem: {exc}\n"
                    "- nastepny krok: upewnij sie, ze temat jest zatwierdzony i podaj poprawne ID."
                ),
                metadata={"short_production": {"status": "failed", "error": str(exc)}},
                selected_provider_key="short-production-agent",
                selected_model_key="short-production-agent",
                routing_reason="ShortProductionAgent request failed validation.",
            )
        return ChannelOpsChatOperation(activated=False)

    def _create_short_plan(self, channel_id: str, topic_id: str):
        topic = self.short_production_agent.get_approved_topic(channel_id, topic_id)
        hooks = topic.get("hooks") if isinstance(topic.get("hooks"), list) else []
        if not hooks or not isinstance(hooks[0], dict):
            raise ValueError(f"Topic `{topic_id}` has no approved hooks.")
        hook = ApprovedHook.model_validate(hooks[0])
        return self.short_production_agent.create_plan(
            PlanRequest(channel_id=channel_id, topic_id=topic_id, approved_hook=hook)
        )

    def _maybe_handle_topic_hook(self, content: str) -> ChannelOpsChatOperation:
        normalized = _slug_text(content)
        if not normalized:
            return ChannelOpsChatOperation(activated=False)

        try:
            if self._detect_topic_generation_intent(normalized):
                channel = self._resolve_channel(_extract_channel_hint(normalized))
                count = _extract_topic_count(normalized) or 10
                style_guide = self._read_style_guide(channel.style_guide_path)
                package = self.topic_hook_agent.run(
                    TopicRequest(
                        channel_id=channel.channel_id,
                        niche=channel.niche,
                        language=channel.language,
                        target_count=count,
                        style_guide=style_guide,
                    )
                )
                return ChannelOpsChatOperation(
                    activated=True,
                    assistant_content=self._format_topic_package(package),
                    metadata={
                        "topic_hook": {
                            "kind": "topic_package",
                            "status": "pending_approval",
                            "package": package.model_dump(mode="json"),
                            "history_path": str(self.topic_hook_agent.package_path(package.channel_id)),
                            "queue_path": str(self.topic_hook_agent.queue_path(package.channel_id)),
                        }
                    },
                    selected_provider_key="topic-hook-agent",
                    selected_model_key="topic-hook-agent",
                    routing_reason="Channel topic request routed to TopicHookAgent.",
                )
            if self._detect_topic_approval_intent(normalized):
                return self._handle_topic_status_change(normalized, approve=True)
            if self._detect_topic_reject_intent(normalized):
                return self._handle_topic_status_change(normalized, approve=False)
        except (ChannelNotFoundError, ValueError) as exc:
            return ChannelOpsChatOperation(
                activated=True,
                assistant_content=(
                    "Nie uruchomilem TopicHookAgent.\n"
                    f"- problem: {exc}\n"
                    "- nastepny krok: wskaz istniejacy kanal albo najpierw wygeneruj paczke tematow."
                ),
                metadata={"topic_hook": {"status": "failed", "error": str(exc)}},
                selected_provider_key="topic-hook-agent",
                selected_model_key="topic-hook-agent",
                routing_reason="TopicHookAgent request failed validation.",
            )
        return ChannelOpsChatOperation(activated=False)

    def _handle_topic_status_change(self, normalized: str, *, approve: bool) -> ChannelOpsChatOperation:
        package = self.topic_hook_agent.latest_package()
        if package is None:
            raise ValueError("Brak ostatniej paczki tematow.")
        topic_ids = _topic_ids_from_selection(normalized, package)
        if approve:
            updated = self.topic_hook_agent.approve(package.package_id, topic_ids)
            status = "approved"
            content = self._format_topic_status_change(updated, topic_ids, approved=True)
        else:
            updated = self.topic_hook_agent.reject(package.package_id, topic_ids)
            status = "rejected"
            content = self._format_topic_status_change(updated, topic_ids, approved=False)
        return ChannelOpsChatOperation(
            activated=True,
            assistant_content=content,
            metadata={
                "topic_hook": {
                    "kind": "topic_package_status_update",
                    "status": status,
                    "package": updated.model_dump(mode="json"),
                    "queue_path": str(self.topic_hook_agent.queue_path(updated.channel_id)),
                }
            },
            selected_provider_key="topic-hook-agent",
            selected_model_key="topic-hook-agent",
            routing_reason="Topic approval command routed to TopicHookAgent.",
        )

    def _prepare_shorts(self, intent: ChannelOpsIntent) -> ChannelOpsChatOperation:
        channel = self._resolve_channel(intent.channel_hint)
        count = max(1, min(intent.count or channel.daily_target_count or 1, channel.daily_target_count or intent.count or 1))
        runner = DailyChannelRunner(self.settings, channel_ops=self.channel_ops)
        result = runner.run(max_per_channel=count, run_youtube_probe=False)
        channel_results = [item for item in result.results if item.channel_id == channel.channel_id]
        content = self._format_daily_run(channel.channel_id, count, result, channel_results)
        return ChannelOpsChatOperation(
            activated=True,
            assistant_content=content,
            metadata={
                "channel_ops": {
                    "intent": asdict(intent),
                    "status": self._aggregate_status(channel_results),
                    "channel_id": channel.channel_id,
                    "requested_count": count,
                    "daily_run": result.to_dict(),
                }
            },
            selected_model_key="daily-channel-runner",
            routing_reason="Channel command routed to ChannelOps daily runner.",
        )

    def _approval_queue(self, intent: ChannelOpsIntent) -> ChannelOpsChatOperation:
        channel = self._resolve_channel(intent.channel_hint, allow_all=True)
        items = self._approval_items(channel.channel_id if channel is not None else None)
        content = self._format_approval_queue(items)
        return ChannelOpsChatOperation(
            activated=True,
            assistant_content=content,
            metadata={
                "channel_ops": {
                    "intent": asdict(intent),
                    "status": "approval_queue",
                    "items": [item.model_dump(mode="json") for item in items],
                }
            },
            selected_model_key="approval-queue",
            routing_reason="Channel command routed to ChannelOps approval queue.",
        )

    def _channel_analytics(self, intent: ChannelOpsIntent) -> ChannelOpsChatOperation:
        channel = self._resolve_channel(intent.channel_hint)
        report = self.analytics.build_learning_report(channel.channel_id)
        content = self._format_learning_report(report.to_dict())
        return ChannelOpsChatOperation(
            activated=True,
            assistant_content=content,
            metadata={
                "channel_ops": {
                    "intent": asdict(intent),
                    "status": "analytics_ready",
                    "channel_id": channel.channel_id,
                    "learning_report": report.to_dict(),
                }
            },
            selected_model_key="analytics-feedback",
            routing_reason="Channel command routed to ChannelOps analytics feedback.",
        )

    def _private_upload(self, intent: ChannelOpsIntent) -> ChannelOpsChatOperation:
        channel = self._resolve_channel(intent.channel_hint, allow_all=True)
        items = self._approval_items(channel.channel_id if channel is not None else None)
        if not items:
            content = (
                "Nie znalazlem gotowych pozycji do prywatnego uploadu.\n"
                "Najpierw potrzebuje itemu w stanie `approval_pending` albo `manual_upload_ready`.\n"
                "Publiczny upload jest nadal zablokowany bez osobnej zgody."
            )
        else:
            content = (
                "Upload prywatny wymaga zatwierdzenia. Nie publikuje publicznie.\n\n"
                + self._format_approval_queue(items)
            )
        return ChannelOpsChatOperation(
            activated=True,
            assistant_content=content,
            metadata={
                "channel_ops": {
                    "intent": asdict(intent),
                    "status": "upload_approval_required",
                    "items": [item.model_dump(mode="json") for item in items],
                }
            },
            selected_model_key="private-upload-approval",
            routing_reason="Channel private upload request converted to supervised approval queue.",
        )

    def _resolve_channel(self, hint: str | None, *, allow_all: bool = False):
        channels = self.channel_ops.list_channels()
        if hint:
            wanted = _slug(hint)
            for channel in channels:
                candidates = {
                    _slug(channel.channel_id),
                    _slug(channel.display_name),
                    _slug(channel.niche),
                }
                if wanted in candidates or any(wanted in candidate for candidate in candidates):
                    return channel
            raise ChannelNotFoundError(
                f"Nie znam kanalu `{hint}`. Dostepne kanaly: {', '.join(channel.channel_id for channel in channels) or 'brak'}."
            )
        if allow_all:
            return None
        enabled = [channel for channel in channels if channel.enabled]
        if len(enabled) == 1:
            return enabled[0]
        raise ChannelNotFoundError(
            "Podaj kanal, np. `Przygotuj 1 short na kanal lokalne-ai`. "
            f"Dostepne kanaly: {', '.join(channel.channel_id for channel in enabled) or 'brak'}."
        )

    def _approval_items(self, channel_id: str | None) -> list[ContentItem]:
        channels = [self.channel_ops.registry.get_channel(channel_id)] if channel_id else self.channel_ops.list_channels()
        selected: list[ContentItem] = []
        approval_statuses = {ContentStatus.APPROVAL_PENDING, ContentStatus.MANUAL_UPLOAD_READY}
        for channel in channels:
            for item in self.channel_ops.list_queue(channel.channel_id):
                if ContentStatus(item.status) in approval_statuses:
                    selected.append(item)
        return sorted(selected, key=lambda item: (item.updated_at, item.item_id))

    def _format_daily_run(
        self,
        channel_id: str,
        requested_count: int,
        result: DailyChannelRunResult,
        channel_results: list[Any],
    ) -> str:
        lines = [
            f"Uruchomilem ChannelOps dla kanalu `{channel_id}`.",
            f"Plan: {requested_count} short.",
            f"Status: `{self._aggregate_status(channel_results)}`.",
            "",
            "Artefakty i decyzje:",
        ]
        if not channel_results:
            lines.append("- Brak przetworzonych itemow dla tego kanalu.")
        for item in channel_results:
            lines.append(f"- `{item.status}`: {item.topic}")
            if item.final_mp4:
                lines.append(f"  final.mp4: `{item.final_mp4}`")
            if item.output_dir:
                lines.append(f"  output_dir: `{item.output_dir}`")
            if item.upload_approval_id:
                lines.append(f"  approval: `{item.upload_approval_id}`")
            if item.youtube_readiness_status and item.status == "manual_upload_ready":
                lines.append(f"  YouTube OAuth: `{item.youtube_readiness_status}` -> manual_upload_ready")
            if item.error:
                lines.append(f"  do poprawy: {self._repair_hint(item.error)}")
        lines.extend(["", f"Log dzienny: `{result.daily_log_path}`"])
        return "\n".join(lines)

    def _format_approval_queue(self, items: list[ContentItem]) -> str:
        if not items:
            return "Kolejka approval jest pusta. Nie ma gotowych shortow do upload review."
        lines = ["Kolejka upload approval:"]
        for item in items:
            lines.append(f"- `{item.status}` | `{item.channel_id}` | {item.topic}")
            if item.final_mp4:
                lines.append(f"  final.mp4: `{item.final_mp4}`")
            if item.upload_approval_id:
                lines.append(f"  approval: `{item.upload_approval_id}`")
            if item.quality_score is not None:
                lines.append(f"  quality_score: `{item.quality_score}`")
        return "\n".join(lines)

    def _format_learning_report(self, report: dict[str, Any]) -> str:
        recommendations = report.get("recommended_topics") or []
        lines = [
            f"Analytics learning dla kanalu `{report.get('channel_id')}`:",
            "",
            "Co dziala najlepiej:",
            _list_or_empty(report.get("winning_patterns")),
            "",
            "Slabe wzorce:",
            _list_or_empty(report.get("weak_patterns")),
            "",
            "Nastepne tematy:",
        ]
        if recommendations:
            for item in recommendations[:5]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('topic')} ({item.get('reason')})")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append("- Brak danych analytics; potrzebuje prywatnych uploadow i metryk.")
        do_not_publish = report.get("do_not_publish") or []
        lines.extend(["", "Czego nie publikowac dalej:", _list_or_empty(do_not_publish)])
        if report.get("obsidian_path"):
            lines.extend(["", f"Raport zapisany: `{report['obsidian_path']}`"])
        return "\n".join(lines)

    def _format_error(self, intent: ChannelOpsIntent, exc: Exception) -> str:
        return (
            "Nie uruchomilem ChannelOps.\n"
            f"- intent: `{intent.kind}`\n"
            f"- problem: {exc}\n"
            "- nastepny krok: wybierz istniejacy kanal albo zarejestruj kanal w ChannelOps."
        )

    def _format_topic_package(self, package: TopicPackage) -> str:
        lines = [
            f"Przygotowalem paczke tematow dla kanalu `{package.channel_id}`.",
            f"Status: `pending_approval`. Tematy nie trafily jeszcze do produkcji.",
            "",
            "Najmocniejsze propozycje:",
        ]
        for index, topic in enumerate(package.topics[:5], start=1):
            lines.append(f"{index}. {topic.title} - score `{topic.viral_score:.2f}`")
            if topic.hooks:
                lines.append(f"   hook: {topic.hooks[0].text}")
        lines.extend(
            [
                "",
                f"Pelny artefakt: `{self.topic_hook_agent.package_path(package.channel_id)}`",
                "Zatwierdzisz np.: `zatwierdz tematy 1, 3, 5 z ostatniej paczki`.",
            ]
        )
        return "\n".join(lines)

    def _format_topic_status_change(self, package: TopicPackage, topic_ids: list[str], *, approved: bool) -> str:
        selected = [topic for topic in package.topics if topic.id in set(topic_ids)]
        action = "zatwierdzone" if approved else "odrzucone"
        lines = [f"Tematy {action}: {len(selected)}."]
        for topic in selected:
            lines.append(f"- {topic.title} (`{topic.viral_score:.2f}`)")
        if approved:
            lines.append(f"Kolejka produkcyjna: `{self.topic_hook_agent.queue_path(package.channel_id)}`")
        else:
            lines.append(f"Historia paczek: `{self.topic_hook_agent.package_path(package.channel_id)}`")
        return "\n".join(lines)

    def _format_short_plan(self, plan: Any) -> str:
        plan_path = self.short_production_agent.plan_dir(plan.channel_id, plan.plan_id) / "plan.json"
        lines = [
            f"Zaplanowalem short dla kanalu `{plan.channel_id}`.",
            f"Plan: `{plan.plan_id}` | temat: `{plan.topic_id}` | status: `{plan.status}`.",
            "",
            "Najwazniejsze elementy:",
            f"- hook: {plan.script.hook}",
            f"- slowa: `{plan.script.total_words}` | duration estimate: `{plan.script.estimated_duration_s}s`",
            f"- narration: `{plan.narration.tool}` -> `{plan.narration.output_file}`",
            f"- visuals: `{len(plan.visuals)}` segmentow",
            f"- tool sequence: `{', '.join(plan.tool_sequence)}`",
            "",
            f"Artefakt planu: `{plan_path}`",
            "Nastepny krok: przygotuj assety w ElevenLabs/Runway/CapCut, potem uruchom QA ze sciezkami plikow.",
        ]
        return "\n".join(lines)

    def _format_short_qa(self, qa: Any) -> str:
        failed = [name for name, passed in qa.checks.model_dump().items() if passed is False]
        lines = [
            f"QA shorta zakonczone: `{qa.recommendation}`.",
            f"Overall: `{qa.scores.overall:.2f}` | hook: `{qa.scores.hook_strength:.2f}` | pacing: `{qa.scores.pacing:.2f}` | visuals: `{qa.scores.visual_quality:.2f}`",
            "",
            "Checks:",
        ]
        if failed:
            lines.extend(f"- FAIL: `{item}`" for item in failed)
        else:
            lines.append("- Wszystkie checks pass.")
        if qa.fix_instructions:
            lines.extend(["", "Co poprawic:"])
            lines.extend(f"- {item}" for item in qa.fix_instructions)
        return "\n".join(lines)

    def _format_review_package(self, package: Any) -> str:
        return "\n".join(
            [
                f"Review package: `{package.review_id}`.",
                f"Status: `{package.status}` | visibility: `{package.upload_metadata.visibility}`.",
                f"Title: {package.upload_metadata.title}",
                f"MP4: `{package.short_file_path}`",
                f"Thumbnail: `{package.thumbnail_file_path}`",
                "Publiczny upload nadal nie jest wykonywany automatycznie.",
            ]
        )

    def _read_style_guide(self, path: str | None) -> str | None:
        if not path:
            return None
        try:
            style_path = Path(path)
            if style_path.exists() and style_path.is_file():
                return style_path.read_text(encoding="utf-8")[:6000]
        except OSError:
            return None
        return None

    def _detect_topic_generation_intent(self, normalized: str) -> bool:
        has_topic = any(token in normalized for token in ("temat", "hook", "pomysl", "idea"))
        has_action = any(token in normalized for token in ("zaproponuj", "wygeneruj", "przygotuj", "znajdz"))
        return has_topic and has_action

    def _detect_topic_approval_intent(self, normalized: str) -> bool:
        return "zatwierdz" in normalized and "temat" in normalized

    def _detect_topic_reject_intent(self, normalized: str) -> bool:
        return any(token in normalized for token in ("odrzuc", "reject")) and "temat" in normalized

    def _detect_short_plan_intent(self, normalized: str) -> bool:
        return "zaplanuj" in normalized and "short" in normalized and "temat" in normalized

    def _detect_short_qa_intent(self, normalized: str) -> bool:
        return ("uruchom qa" in normalized or "run qa" in normalized) and "short" in normalized

    def _detect_review_package_intent(self, normalized: str) -> bool:
        return "review" in normalized and ("pokaz" in normalized or "paczke" in normalized or "package" in normalized)

    def _detect_upload_approval_intent(self, normalized: str) -> bool:
        return "zatwierdz" in normalized and "upload" in normalized and "review" in normalized

    def _aggregate_status(self, items: list[Any]) -> str:
        if not items:
            return "blocked"
        statuses = {str(item.status) for item in items}
        if "qa_failed" in statuses:
            return "qa_failed"
        if "blocked" in statuses:
            return "blocked"
        if "approval_pending" in statuses:
            return "approval_pending"
        if "manual_upload_ready" in statuses:
            return "manual_upload_ready"
        if "private_uploaded" in statuses:
            return "private_uploaded"
        return sorted(statuses)[0]

    def _repair_hint(self, error: str) -> str:
        hints = {
            "script_quality_gate_failed": "popraw hook, konkret, tempo i unikaj ogolnikow przed renderem",
            "render_qa_failed": "napraw render_qa: audio, pionowy format, duration, napisy albo thumbnail",
            "short_runtime_not_ready": "uzupelnij runtime shortow: Piper, whisper.cpp, FFmpeg, Ollama",
            "final_mp4_missing": "render nie zwrocil final.mp4",
            "duplicate_topic": "wybierz inny temat; ten juz jest w kolejce",
        }
        return hints.get(error, error)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", _replace_polish_letters(value or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9_-]+", "-", ascii_only).strip("-")


def _slug_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", _replace_polish_letters(value or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return " ".join(re.sub(r"[^a-z0-9_, -]+", " ", ascii_only).split())


def _extract_topic_count(normalized: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\s+(?:temat|tematy|pomysl|pomysly|hook|hooki)\b", normalized)
    if not match:
        return None
    return max(1, min(50, int(match.group(1))))


def _extract_channel_hint(normalized: str) -> str | None:
    match = re.search(r"\b(?:kanal|kanale|kanalu|channel)\s+([a-z0-9][a-z0-9_-]*)", normalized)
    if match:
        return match.group(1).strip()
    return None


def _extract_production_topic_id(content: str, normalized: str) -> str:
    for pattern in (
        r"\b(topic-[a-zA-Z0-9_-]+)\b",
        r"\b(?:temat|tematu|topic|id)\s+([a-zA-Z0-9_-]+)\b",
    ):
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip().lower()
            if value:
                return value
    match = re.search(r"\b(topic-[a-z0-9_-]+)\b", normalized)
    if match:
        return match.group(1)
    raise ValueError("Nie znalazlem topic_id. Uzyj np. `zaplanuj short dla tematu topic-abc123 na kanal lokalne-ai`.")


def _extract_plan_id(normalized: str, *, required: bool = True) -> str:
    match = re.search(r"\b(plan-[a-z0-9_-]+)\b", normalized)
    if match:
        return match.group(1)
    if required:
        raise ValueError("Nie znalazlem plan_id, np. `plan-abc123`.")
    return ""


def _extract_review_id(normalized: str) -> str:
    match = re.search(r"\b(review-[a-z0-9_-]+)\b", normalized)
    if not match:
        raise ValueError("Nie znalazlem review_id, np. `review-abc123`.")
    return match.group(1)


def _extract_production_files(content: str) -> ProductionFiles | None:
    values: dict[str, str] = {}
    for key in ("short_file_path", "narration_file_path", "thumbnail_file_path"):
        match = re.search(rf"{key}\s*=\s*(\"[^\"]+\"|'[^']+'|\S+)", content)
        if match:
            values[key] = match.group(1).strip("\"'")
    if not values:
        return None
    missing = [key for key in ("short_file_path", "narration_file_path", "thumbnail_file_path") if key not in values]
    if missing:
        raise ValueError(f"Brakuje sciezek: {', '.join(missing)}")
    return ProductionFiles(**values)


def _topic_ids_from_selection(normalized: str, package: TopicPackage) -> list[str]:
    numbers = [int(value) for value in re.findall(r"\b\d{1,2}\b", normalized)]
    if not numbers:
        return [topic.id for topic in package.pending_topics]
    topic_ids: list[str] = []
    for number in numbers:
        index = number - 1
        if index < 0 or index >= len(package.topics):
            raise ValueError(f"Numer tematu poza zakresem: {number}")
        topic_ids.append(package.topics[index].id)
    return topic_ids


def _list_or_empty(values: Any) -> str:
    if not values:
        return "- Brak danych."
    return "\n".join(f"- {item}" for item in values)


def _replace_polish_letters(value: str) -> str:
    translation = {
        0x0105: "a",
        0x0107: "c",
        0x0119: "e",
        0x0142: "l",
        0x0144: "n",
        0x00F3: "o",
        0x015B: "s",
        0x017A: "z",
        0x017C: "z",
        0x0104: "A",
        0x0106: "C",
        0x0118: "E",
        0x0141: "L",
        0x0143: "N",
        0x00D3: "O",
        0x015A: "S",
        0x0179: "Z",
        0x017B: "Z",
    }
    return value.translate(translation)
