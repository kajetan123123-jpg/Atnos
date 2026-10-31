from __future__ import annotations

from typing import Any


def format_decision_markdown(
    *,
    task_id: str,
    created_at: str,
    decision: Any,
    reason: Any,
    alternatives: Any,
) -> str:
    return "\n".join(
        [
            "---",
            f'task_id: "{task_id}"',
            'type: "decision"',
            f'created_at: "{created_at}"',
            "---",
            "",
            "# Decision Log",
            "",
            "## Decision",
            _format_markdown_value(decision),
            "",
            "## Reason",
            _format_markdown_value(reason),
            "",
            "## Alternatives",
            _format_markdown_value(alternatives or []),
            "",
        ]
    )


def format_short_production_markdown(*, task_id: str, created_at: str, short_metadata: Any) -> str:
    return "\n".join(
        [
            "---",
            f'task_id: "{task_id}"',
            'type: "short_production"',
            f'created_at: "{created_at}"',
            "---",
            "",
            "# Short Production Note",
            "",
            "## Metadata",
            _format_markdown_value(short_metadata),
            "",
            "## Next steps",
            "- Review final.mp4 locally.",
            "- Upload manually or use YouTube private upload flow after approval.",
            "",
        ]
    )


def format_ubuntu_report_markdown(
    *,
    task_id: str,
    created_at: str,
    report_path: str,
    report_content: str | None,
) -> str:
    content = report_content or "Report file was not readable locally. Use the path below on the Ubuntu sandbox."
    return "\n".join(
        [
            "---",
            f'task_id: "{task_id}"',
            'type: "ubuntu_report"',
            f'created_at: "{created_at}"',
            "---",
            "",
            "# Ubuntu Report Note",
            "",
            "## Source report",
            report_path,
            "",
            "## Report",
            content,
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
        lines = ["```json"]
        import json

        lines.append(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        lines.append("```")
        return "\n".join(lines)
    return str(value)


def _format_inline(value: Any) -> str:
    if isinstance(value, dict):
        import json

        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)
