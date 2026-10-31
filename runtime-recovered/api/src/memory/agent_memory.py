from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.services.trace_metadata_sanitizer import FORBIDDEN_KEY_TOKENS

from memory.decision_log_writer import (
    format_decision_markdown,
    format_short_production_markdown,
    format_ubuntu_report_markdown,
)
from memory.obsidian_memory_bridge import ObsidianMemoryBridge
from memory.obsidian_writer import ObsidianWriter
from memory.task_journal import TaskJournal

MAX_REPORT_CHARS = 12_000


class AgentMemory:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.journal = TaskJournal(self.settings.data_dir)
        self.obsidian = ObsidianWriter(self.settings)
        self.bridge = ObsidianMemoryBridge(self.settings)

    def write_agent_task_log(
        self,
        task: Any,
        plan: Any,
        tools_used: Any,
        result: Any,
        *,
        risks: Any = None,
        next_steps: Any = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = _utc_now()
        resolved_task_id = task_id or _new_task_id()
        safe_task = redact_sensitive(task)
        safe_plan = redact_sensitive(plan)
        safe_tools = redact_sensitive(tools_used)
        safe_result = redact_sensitive(result)
        safe_risks = redact_sensitive(risks or [])
        safe_next_steps = redact_sensitive(next_steps or [])

        markdown = _format_agent_task_markdown(
            task_id=resolved_task_id,
            created_at=created_at,
            task=safe_task,
            plan=safe_plan,
            tools_used=safe_tools,
            result=safe_result,
            risks=safe_risks,
            next_steps=safe_next_steps,
        )
        filename = _build_note_filename(created_at, resolved_task_id, str(safe_task))
        local_path = self.journal.write_note("agent_logs", filename, markdown)
        obsidian_result = self.obsidian.write_note(
            self.settings.atnos_obsidian_agent_dir,
            filename,
            markdown,
        )

        event = {
            "event": "agent_task_log",
            "task_id": resolved_task_id,
            "task": _compact(safe_task),
            "tools_used": safe_tools,
            "local_path": str(local_path),
            "obsidian_path": obsidian_result.path,
            "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
        }
        log_path = self.journal.append_event(event)
        memory_record = self.bridge.record(
            domain="task_state",
            source="agent_task_log",
            summary={
                "task": safe_task,
                "result": safe_result,
                "next_steps": safe_next_steps,
            },
            task_id=resolved_task_id,
            tags=["agent", "task", *[str(item) for item in safe_tools if isinstance(item, str)]],
            priority="medium",
            local_path=str(local_path),
            obsidian_path=obsidian_result.path,
        )
        return {
            "task_id": resolved_task_id,
            "local_path": str(local_path),
            "obsidian_path": obsidian_result.path,
            "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            "jsonl_path": str(log_path),
            "memory_id": memory_record.memory_id,
            "memory_index_path": str(self.bridge.index_path),
        }

    def write_decision(
        self,
        decision: Any,
        reason: Any,
        alternatives: Any = None,
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = _utc_now()
        resolved_task_id = task_id or _new_task_id()
        safe_decision = redact_sensitive(decision)
        safe_reason = redact_sensitive(reason)
        safe_alternatives = redact_sensitive(alternatives or [])
        markdown = format_decision_markdown(
            task_id=resolved_task_id,
            created_at=created_at,
            decision=safe_decision,
            reason=safe_reason,
            alternatives=safe_alternatives,
        )
        filename = _build_note_filename(created_at, resolved_task_id, str(safe_decision))
        local_path = self.journal.write_note("decision_log", filename, markdown)
        obsidian_result = self.obsidian.write_note(
            self.settings.atnos_obsidian_decision_dir,
            filename,
            markdown,
        )
        log_path = self.journal.append_event(
            {
                "event": "decision",
                "task_id": resolved_task_id,
                "decision": _compact(safe_decision),
                "local_path": str(local_path),
                "obsidian_path": obsidian_result.path,
                "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            }
        )
        memory_record = self.bridge.record(
            domain="decision",
            source="decision_log",
            summary={
                "decision": safe_decision,
                "reason": safe_reason,
                "alternatives": safe_alternatives,
            },
            task_id=resolved_task_id,
            tags=["decision"],
            priority="critical",
            local_path=str(local_path),
            obsidian_path=obsidian_result.path,
        )
        return {
            "task_id": resolved_task_id,
            "local_path": str(local_path),
            "obsidian_path": obsidian_result.path,
            "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            "jsonl_path": str(log_path),
            "memory_id": memory_record.memory_id,
            "memory_index_path": str(self.bridge.index_path),
        }

    def write_short_production_note(self, short_metadata: Any, *, task_id: str | None = None) -> dict[str, Any]:
        created_at = _utc_now()
        resolved_task_id = task_id or _new_task_id()
        safe_metadata = redact_sensitive(short_metadata)
        markdown = format_short_production_markdown(
            task_id=resolved_task_id,
            created_at=created_at,
            short_metadata=safe_metadata,
        )
        topic = safe_metadata.get("topic", "short") if isinstance(safe_metadata, dict) else "short"
        filename = _build_note_filename(created_at, resolved_task_id, str(topic))
        local_path = self.journal.write_note("shorts", filename, markdown)
        obsidian_result = self.obsidian.write_note(
            self.settings.atnos_obsidian_shorts_dir,
            filename,
            markdown,
        )
        log_path = self.journal.append_event(
            {
                "event": "short_production_note",
                "task_id": resolved_task_id,
                "local_path": str(local_path),
                "obsidian_path": obsidian_result.path,
                "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            }
        )
        memory_record = self.bridge.record(
            domain="operational_fact",
            source="short_production_note",
            summary=safe_metadata,
            task_id=resolved_task_id,
            tags=["shorts", "content", "production"],
            priority="high",
            local_path=str(local_path),
            obsidian_path=obsidian_result.path,
        )
        return {
            "task_id": resolved_task_id,
            "local_path": str(local_path),
            "obsidian_path": obsidian_result.path,
            "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            "jsonl_path": str(log_path),
            "memory_id": memory_record.memory_id,
            "memory_index_path": str(self.bridge.index_path),
        }

    def write_ubuntu_report_note(self, report_path: str | Path, *, task_id: str | None = None) -> dict[str, Any]:
        created_at = _utc_now()
        resolved_task_id = task_id or _new_task_id()
        safe_report_path = str(redact_sensitive(str(report_path)))
        report_content = _read_report_if_local(Path(str(report_path)))
        markdown = format_ubuntu_report_markdown(
            task_id=resolved_task_id,
            created_at=created_at,
            report_path=safe_report_path,
            report_content=redact_sensitive(report_content) if report_content is not None else None,
        )
        filename = _build_note_filename(created_at, resolved_task_id, "ubuntu-report")
        local_path = self.journal.write_note("ubuntu_reports", filename, markdown)
        obsidian_result = self.obsidian.write_note(
            self.settings.atnos_obsidian_agent_dir,
            filename,
            markdown,
        )
        log_path = self.journal.append_event(
            {
                "event": "ubuntu_report_note",
                "task_id": resolved_task_id,
                "report_path": safe_report_path,
                "local_path": str(local_path),
                "obsidian_path": obsidian_result.path,
                "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            }
        )
        memory_record = self.bridge.record(
            domain="operational_fact",
            source="ubuntu_report_note",
            summary={
                "report_path": safe_report_path,
                "report_excerpt": report_content[:1500] if report_content else None,
            },
            task_id=resolved_task_id,
            tags=["ubuntu", "sandbox", "report"],
            priority="medium",
            local_path=str(local_path),
            obsidian_path=obsidian_result.path,
        )
        return {
            "task_id": resolved_task_id,
            "local_path": str(local_path),
            "obsidian_path": obsidian_result.path,
            "obsidian_status": "written" if obsidian_result.written else obsidian_result.reason,
            "jsonl_path": str(log_path),
            "memory_id": memory_record.memory_id,
            "memory_index_path": str(self.bridge.index_path),
        }

    def search_recent_agent_logs(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.journal.search(query, limit=limit)

    def search_operational_memory(self, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
        return self.bridge.search(query, limit=limit)


def write_agent_task_log(task: Any, plan: Any, tools_used: Any, result: Any) -> dict[str, Any]:
    return AgentMemory().write_agent_task_log(task, plan, tools_used, result)


def write_decision(decision: Any, reason: Any, alternatives: Any = None) -> dict[str, Any]:
    return AgentMemory().write_decision(decision, reason, alternatives)


def write_short_production_note(short_metadata: Any) -> dict[str, Any]:
    return AgentMemory().write_short_production_note(short_metadata)


def write_ubuntu_report_note(report_path: str | Path) -> dict[str, Any]:
    return AgentMemory().write_ubuntu_report_note(report_path)


def search_recent_agent_logs(query: str) -> list[dict[str, Any]]:
    return AgentMemory().search_recent_agent_logs(query)


def search_operational_memory(query: str) -> list[dict[str, Any]]:
    return AgentMemory().search_operational_memory(query)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if isinstance(value, set):
        return sorted(redact_sensitive(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _format_agent_task_markdown(
    *,
    task_id: str,
    created_at: str,
    task: Any,
    plan: Any,
    tools_used: Any,
    result: Any,
    risks: Any,
    next_steps: Any,
) -> str:
    return "\n".join(
        [
            "---",
            f'task_id: "{task_id}"',
            'type: "agent_task"',
            f'created_at: "{created_at}"',
            "---",
            "",
            "# Agent Task Log",
            "",
            "## Task",
            _format_markdown_value(task),
            "",
            "## Plan",
            _format_markdown_value(plan),
            "",
            "## Tools used",
            _format_markdown_value(tools_used),
            "",
            "## Result",
            _format_markdown_value(result),
            "",
            "## Risks",
            _format_markdown_value(risks),
            "",
            "## Next steps",
            _format_markdown_value(next_steps),
            "",
        ]
    )


def _format_markdown_value(value: Any) -> str:
    if value is None:
        return "_none_"
    if isinstance(value, str):
        return value.strip() or "_empty_"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if not items:
            return "_none_"
        return "\n".join(f"- {_format_inline(item)}" for item in items)
    if isinstance(value, dict):
        return "\n".join(["```json", json.dumps(value, ensure_ascii=False, indent=2, default=str), "```"])
    return str(value)


def _format_inline(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _new_task_id() -> str:
    return f"task_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _build_note_filename(created_at: str, task_id: str, title: str) -> str:
    stamp = created_at.replace(":", "").replace("-", "").split(".", maxsplit=1)[0].replace("+0000", "Z")
    slug = _slugify(title)[:64] or "note"
    return f"{stamp}-{task_id}-{slug}.md"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-._")
    return normalized or "note"


def _compact(value: Any, *, limit: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _read_report_if_local(path: Path) -> str | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return content[:MAX_REPORT_CHARS]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in FORBIDDEN_KEY_TOKENS)


def _redact_string(value: str) -> str:
    redacted = value
    redacted = re.sub(
        r"(?i)[A-Z]:\\Users\\[^\\\s]+\\\.ssh\\[^\s\"'`]+",
        "[REDACTED_PRIVATE_KEY_PATH]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)/home/[^/\s]+/\.ssh/[^\s\"'`]+",
        "[REDACTED_PRIVATE_KEY_PATH]",
        redacted,
    )
    redacted = re.sub(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(r"\bsk-[A-Za-z0-9_\-]{16,}\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(r"\bAIza[A-Za-z0-9_\-]{20,}\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|client_secret|private_key|authorization|bearer)\b\s*[:=]\s*([^\s,;]+)",
        lambda match: f"{match.group(1)}=[REDACTED]",
        redacted,
    )
    return redacted
