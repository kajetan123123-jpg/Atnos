from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import unicodedata
from typing import Callable

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.decision_log import DecisionLogEntry
from atnos.db.models.enums import DecisionLogKind, DecisionLogStatus, ModelTask, ProviderPosture
from atnos.plugins.registry import PluginRegistry, build_default_plugin_registry
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.decision_log_repository import DecisionLogRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.workflow_llm_service import WorkflowLLMService


@dataclass(frozen=True, slots=True)
class DecisionLogRecall:
    entries: tuple[DecisionLogEntry, ...]
    overdue_entries: tuple[DecisionLogEntry, ...]


@dataclass(frozen=True, slots=True)
class DecisionContradiction:
    entry_id: int
    summary: str
    prior_date: datetime
    evidence_message_ids: tuple[int, ...]
    current_claim: str

    def build_prefix(self) -> str:
        evidence = ",".join(str(item) for item in self.evidence_message_ids) or "n/a"
        return (
            f"Note: w {self.prior_date.date().isoformat()} ustaliles {self.summary}. "
            f"Aktualnie mowisz {self.current_claim}. Zmiana? "
            f"[evidence:{evidence}]"
        )


WorkflowLlmFactory = Callable[[Session, Settings, ProviderRegistry, PluginRegistry], WorkflowLLMService]


class DecisionLogService:
    MAX_MESSAGES = 20
    MIN_MESSAGES_FOR_EXTRACTION = 3
    COMMITMENT_RE = re.compile(
        r"\b("
        r"zrobie|przygotuje|wysle|dowioze|ogarne|sprawdze|zamkne|dostarcze|"
        r"i will|i ll|i'll|we will|we ll|we'll|i can deliver|i can send"
        r")\b",
        re.IGNORECASE,
    )
    DECISION_RE = re.compile(
        r"\b("
        r"ustalamy|decyzja|wybieramy|zostajemy przy|idziemy w|postanawiamy|"
        r"we choose|we are going with|we're going with|we'll use|we are using"
        r")\b",
        re.IGNORECASE,
    )
    PENDING_ACTION_RE = re.compile(
        r"\b("
        r"trzeba|musimy|do zrobienia|nastepny krok|next step|todo|follow-up|follow up"
        r")\b",
        re.IGNORECASE,
    )
    QUESTION_RE = re.compile(
        r"\b("
        r"czy|co|jak|kiedy|ktory|ktora|ktore|why|what|how|when|which|should|can we|do we"
        r")\b",
        re.IGNORECASE,
    )
    WEEKDAY_INDEX = {
        "poniedzialek": 0,
        "poniedzialku": 0,
        "monday": 0,
        "wtorek": 1,
        "wtorku": 1,
        "tuesday": 1,
        "sroda": 2,
        "srode": 2,
        "wednesday": 2,
        "czwartek": 3,
        "czwartku": 3,
        "thursday": 3,
        "piatek": 4,
        "piatku": 4,
        "friday": 4,
        "sobota": 5,
        "sobote": 5,
        "saturday": 5,
        "niedziela": 6,
        "niedziele": 6,
        "sunday": 6,
    }
    MONTH_INDEX = {
        "stycznia": 1,
        "styczen": 1,
        "january": 1,
        "lutego": 2,
        "luty": 2,
        "february": 2,
        "marca": 3,
        "marzec": 3,
        "march": 3,
        "kwietnia": 4,
        "kwiecien": 4,
        "april": 4,
        "maja": 5,
        "maj": 5,
        "may": 5,
        "czerwca": 6,
        "czerwiec": 6,
        "june": 6,
        "lipca": 7,
        "lipiec": 7,
        "july": 7,
        "sierpnia": 8,
        "sierpien": 8,
        "august": 8,
        "wrzesnia": 9,
        "wrzesien": 9,
        "september": 9,
        "pazdziernika": 10,
        "pazdziernik": 10,
        "october": 10,
        "listopada": 11,
        "listopad": 11,
        "november": 11,
        "grudnia": 12,
        "grudzien": 12,
        "december": 12,
    }

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        provider_registry: ProviderRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
        workflow_llm_factory: WorkflowLlmFactory | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry or build_default_plugin_registry()
        self.workflow_llm_factory = workflow_llm_factory or self._default_workflow_llm_factory
        self.decisions = DecisionLogRepository(session)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.projects = ProjectRepository(session)

    def extract_from_conversation(
        self,
        conversation_id: int,
        *,
        allow_llm: bool = False,
    ) -> list[DecisionLogEntry]:
        conversation = self.conversations.get_any(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} was not found.")
        messages = self.messages.list_for_conversation(conversation_id)
        from atnos.services.chat_tool_focus import memory_opt_out

        user_messages = [message for message in messages if message.role.value == 'user']
        if user_messages and memory_opt_out(user_messages[-1].content or ''):
            return []  # A read-only turn must not trigger writes of old history either.
        eligible = []
        turn_denied = False
        for message in messages:
            if message.role.value == 'user':
                turn_denied = memory_opt_out(message.content or '')
            if not turn_denied:
                eligible.append(message)
        # Exclude denied user turns and their replies on every later extraction,
        # including the optional LLM route. A later turn is not retroactive consent.
        messages = eligible[-self.MAX_MESSAGES :]
        if len(messages) < self.MIN_MESSAGES_FOR_EXTRACTION:
            return []
        extracted = self._extract_with_rules(messages)
        if allow_llm and not extracted:
            extracted = self._extract_with_llm(messages)

        created: list[DecisionLogEntry] = []
        existing = self.decisions.list_for_conversation(conversation_id, limit=100)
        existing_keys = {
            (item.kind.value, self._normalize_text(item.summary), tuple(item.evidence_message_ids or ()))
            for item in existing
        }
        eligible_ids = {message.id for message in messages}
        for candidate in extracted:
            if not set(candidate['evidence_message_ids']).issubset(eligible_ids):
                continue
            key = (
                candidate["kind"].value,
                self._normalize_text(str(candidate["summary"])),
                tuple(candidate["evidence_message_ids"]),
            )
            if key in existing_keys:
                continue
            created.append(
                self.decisions.create(
                    conversation_id=conversation_id,
                    project_id=conversation.project_id,
                    kind=candidate["kind"],
                    summary=str(candidate["summary"]),
                    made_at=candidate["made_at"],
                    status=DecisionLogStatus.ACTIVE,
                    due_at=candidate["due_at"],
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=list(candidate["evidence_message_ids"]),
                )
            )
            existing_keys.add(key)
        return created

    def recall_active(self, project_id: int | None) -> DecisionLogRecall:
        entries = self.decisions.list_active(project_id=project_id, limit=32)
        now = datetime.now(UTC)
        overdue = tuple(
            item
            for item in entries
            if item.due_at is not None and self._ensure_utc_datetime(item.due_at) <= now
        )
        return DecisionLogRecall(entries=tuple(entries[:10]), overdue_entries=overdue)

    def resolve(self, decision_id: int, *, resolution_summary: str | None = None) -> DecisionLogEntry:
        item = self.decisions.get(decision_id)
        if item is None:
            raise ValueError(f"Unknown decision_log id '{decision_id}'.")
        item.status = DecisionLogStatus.RESOLVED
        item.resolved_at = datetime.now(UTC)
        item.resolution_summary = self._optional_text(resolution_summary)
        return self.decisions.save(item)

    def detect_contradiction(
        self,
        *,
        project_id: int | None,
        user_message: str,
        allow_llm: bool = False,
    ) -> DecisionContradiction | None:
        normalized_message = self._normalize_text(user_message)
        if not normalized_message:
            return None
        recall = self.recall_active(project_id)
        if allow_llm:
            llm_result = self._detect_with_llm(recall.entries, user_message)
            if llm_result is not None:
                return llm_result
        if "arena" in normalized_message:
            for entry in recall.entries:
                if "chat i ustawienia" in self._normalize_text(entry.summary):
                    return DecisionContradiction(
                        entry_id=entry.id,
                        summary=entry.summary,
                        prior_date=entry.made_at,
                        evidence_message_ids=tuple(entry.evidence_message_ids or ()),
                        current_claim=user_message.strip(),
                    )
        return None

    def build_context_block(self, *, project_id: int | None) -> str | None:
        recall = self.recall_active(project_id)
        if not recall.entries:
            return None
        lines = ["Historical decisions and commitments (not verified current state):",
                 "Prefer the current user request and freshly read source notes over these summaries. "
                 "If they disagree, read the source or explicitly disclose the conflict. "
                 "A memory-test learning is not evidence of film production status."]
        for item in recall.entries[:6]:
            due_suffix = f" | due: {item.due_at.date().isoformat()}" if item.due_at is not None else ""
            evidence = ','.join(str(value) for value in (item.evidence_message_ids or ())) or 'unknown'
            lines.append(f"- [{item.kind.value}; recorded={item.made_at.isoformat()}; "
                         f"conversation={item.conversation_id}; messages={evidence}] {item.summary}{due_suffix}")
        return "\n".join(lines)

    def _extract_with_llm(self, messages) -> list[dict[str, object]]:
        if self.settings is None or self.provider_registry is None:
            return []
        try:
            service = self.workflow_llm_factory(
                self.session,
                self.settings,
                self.provider_registry,
                self.plugin_registry,
            )
            transcript = "\n".join(
                f"{message.role.value}:{message.id}:{message.content}"
                for message in messages
                if message.content.strip()
            )
            prompt = (
                "Extract a compact JSON array of decision log entries from this conversation. "
                "Only include meaningful decisions, commitments, open questions and pending actions. "
                "Return JSON only with keys: kind, summary, evidence_message_ids, due_hint.\n\n"
                f"{transcript}"
            )
            raw = service.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            ).content
            payload = json.loads(raw)
            if not isinstance(payload, list):
                return []
            by_id = {message.id: message for message in messages}
            extracted: list[dict[str, object]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                kind = self._parse_kind(item.get("kind"))
                summary = self._optional_text(item.get("summary"))
                evidence_ids = self._coerce_message_ids(item.get("evidence_message_ids"))
                if kind is None or summary is None or not evidence_ids:
                    continue
                first_message = by_id.get(evidence_ids[0])
                made_at = first_message.created_at if first_message is not None else datetime.now(UTC)
                due_at = self._parse_due_hint(self._optional_text(item.get("due_hint")), base_time=made_at)
                extracted.append(
                    {
                        "kind": kind,
                        "summary": summary,
                        "evidence_message_ids": evidence_ids,
                        "made_at": made_at,
                        "due_at": due_at,
                    }
                )
            return extracted
        except Exception:
            return []

    def _extract_with_rules(self, messages) -> list[dict[str, object]]:
        extracted: list[dict[str, object]] = []
        for message in messages:
            content = self._optional_text(message.content)
            if content is None:
                continue
            normalized = self._normalize_text(content)
            if not normalized:
                continue
            kind = self._classify_candidate(message.role.value, content, normalized)
            if kind is None:
                continue
            extracted.append(
                {
                    "kind": kind,
                    "summary": self._summarize_candidate(content),
                    "evidence_message_ids": [message.id],
                    "made_at": message.created_at,
                    "due_at": self._parse_due_hint(content, base_time=message.created_at),
                }
            )
        return extracted

    def _classify_candidate(
        self,
        role_value: str,
        content: str,
        normalized: str,
    ) -> DecisionLogKind | None:
        role = role_value.lower()
        if role not in {"user", "assistant"}:
            return None
        if role == "user" and self.COMMITMENT_RE.search(normalized):
            return DecisionLogKind.COMMITMENT
        if role == "user" and self.DECISION_RE.search(normalized):
            return DecisionLogKind.DECISION
        if role == "user" and self.PENDING_ACTION_RE.search(normalized):
            return DecisionLogKind.PENDING_ACTION
        if role == "user" and "?" in content and self._looks_like_open_question(normalized):
            return DecisionLogKind.OPEN_QUESTION
        return None

    def _detect_with_llm(self, entries: tuple[DecisionLogEntry, ...], user_message: str) -> DecisionContradiction | None:
        if not entries or self.settings is None or self.provider_registry is None:
            return None
        try:
            service = self.workflow_llm_factory(
                self.session,
                self.settings,
                self.provider_registry,
                self.plugin_registry,
            )
            history = "\n".join(
                f"{item.id}|{item.made_at.date().isoformat()}|{item.summary}|{','.join(str(mid) for mid in (item.evidence_message_ids or []))}"
                for item in entries[:8]
            )
            prompt = (
                "Check whether the latest user message contradicts one of the active decisions. "
                'Return JSON only: {"contradiction": bool, "entry_id": int|null}. '
                f"Active decisions:\n{history}\n\nUser message:\n{user_message}"
            )
            raw = service.generate_text(
                prompt=prompt,
                task=ModelTask.SYNTHESIS,
                provider_posture=ProviderPosture.PREFER_LOCAL,
            ).content
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not payload.get("contradiction"):
                return None
            entry_id = payload.get("entry_id")
            if not isinstance(entry_id, int):
                return None
            match = next((item for item in entries if item.id == entry_id), None)
            if match is None:
                return None
            return DecisionContradiction(
                entry_id=match.id,
                summary=match.summary,
                prior_date=match.made_at,
                evidence_message_ids=tuple(match.evidence_message_ids or ()),
                current_claim=user_message.strip(),
            )
        except Exception:
            return None

    @staticmethod
    def _default_workflow_llm_factory(
        session: Session,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
    ) -> WorkflowLLMService:
        return WorkflowLLMService(
            session,
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )

    @staticmethod
    def _parse_kind(value: object) -> DecisionLogKind | None:
        if not isinstance(value, str):
            return None
        try:
            return DecisionLogKind(value.strip().lower())
        except ValueError:
            return None

    @staticmethod
    def _coerce_message_ids(value: object) -> list[int]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, int)]

    @classmethod
    def _parse_due_hint(cls, text: str | None, *, base_time: datetime) -> datetime | None:
        normalized = cls._normalize_text(text or "")
        if not normalized:
            return None
        if re.search(r"\b(dzis|dzisiaj|today)\b", normalized):
            return base_time
        if re.search(r"\b(jutro|tomorrow)\b", normalized):
            return base_time + timedelta(days=1)
        if re.search(r"\b(pojutrze|the day after tomorrow)\b", normalized):
            return base_time + timedelta(days=2)

        iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", normalized)
        if iso_match:
            return cls._safe_datetime(
                year=int(iso_match.group(1)),
                month=int(iso_match.group(2)),
                day=int(iso_match.group(3)),
                base_time=base_time,
            )

        dotted_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", normalized)
        if dotted_match:
            year = int(dotted_match.group(3))
            year = year + 2000 if year < 100 else year
            return cls._safe_datetime(
                year=year,
                month=int(dotted_match.group(2)),
                day=int(dotted_match.group(1)),
                base_time=base_time,
            )

        month_match = re.search(
            r"\b(\d{1,2})\s+(stycznia|styczen|lutego|luty|marca|marzec|kwietnia|kwiecien|maja|maj|czerwca|czerwiec|lipca|lipiec|sierpnia|sierpien|wrzesnia|wrzesien|pazdziernika|pazdziernik|listopada|listopad|grudnia|grudzien|january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(20\d{2}))?\b",
            normalized,
        )
        if month_match:
            day = int(month_match.group(1))
            month_label = month_match.group(2)
            month = cls.MONTH_INDEX.get(month_label)
            year = int(month_match.group(3)) if month_match.group(3) else base_time.year
            if month is not None:
                candidate = cls._safe_datetime(year=year, month=month, day=day, base_time=base_time)
                if candidate is not None and candidate.date() < base_time.date() and month_match.group(3) is None:
                    return cls._safe_datetime(
                        year=base_time.year + 1,
                        month=month,
                        day=day,
                        base_time=base_time,
                    )
                return candidate

        for label, weekday in cls.WEEKDAY_INDEX.items():
            if re.search(rf"\b(do|by|on)\s+{re.escape(label)}\b", normalized):
                return cls._resolve_weekday(base_time=base_time, weekday=weekday, include_current=True)
            if re.search(rf"\b{re.escape(label)}\b", normalized):
                return cls._resolve_weekday(base_time=base_time, weekday=weekday, include_current=False)
        return None

    @staticmethod
    def _resolve_weekday(*, base_time: datetime, weekday: int, include_current: bool) -> datetime:
        delta = (weekday - base_time.weekday()) % 7
        if delta == 0 and not include_current:
            delta = 7
        return base_time + timedelta(days=delta)

    @staticmethod
    def _safe_datetime(*, year: int, month: int, day: int, base_time: datetime) -> datetime | None:
        try:
            return base_time.replace(year=year, month=month, day=day)
        except ValueError:
            return None

    @staticmethod
    def _looks_like_open_question(normalized: str) -> bool:
        compact = normalized.strip()
        if len(compact) < 12:
            return False
        return DecisionLogService.QUESTION_RE.search(compact) is not None

    @staticmethod
    def _summarize_candidate(content: str) -> str:
        cleaned = " ".join(content.strip().split())
        return cleaned[:280]

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = " ".join(value.strip().split())
        return cleaned or None

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
        ascii_text = ascii_text.replace("i ll", "i'll").replace("we ll", "we'll")
        return re.sub(r"\s+", " ", ascii_text).strip()

    @staticmethod
    def _ensure_utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
