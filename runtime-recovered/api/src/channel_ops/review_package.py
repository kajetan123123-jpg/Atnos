from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Iterable

from .channel_models import ContentItem, ContentStatus


def write_item_review_package(
    *,
    item: ContentItem,
    result: Any,
    item_dir: Path,
) -> Path:
    """Write a human review checklist for one channel item."""
    item_dir.mkdir(parents=True, exist_ok=True)
    review_path = item_dir / "review.md"
    output_dir = _path_or_none(item.output_dir or getattr(result, "output_dir", None))
    final_mp4 = _path_or_none(item.final_mp4 or getattr(result, "final_mp4", None))
    qa = _read_json(output_dir / "render_qa.json") if output_dir else {}
    package = _read_json(output_dir / "youtube_upload_package.json") if output_dir else {}
    artifacts = dict(getattr(result, "artifacts", {}) or {})
    quality_score = item.quality_score if item.quality_score is not None else getattr(result, "quality_score", None)
    approval_id = item.upload_approval_id or getattr(result, "upload_approval_id", None)
    youtube_readiness = getattr(result, "youtube_readiness_status", None)
    status = str(getattr(result, "status", item.status))
    error = getattr(result, "error", None)

    lines = [
        f"# Review Package - {item.topic}",
        "",
        "## Decision",
        f"- channel_id: `{item.channel_id}`",
        f"- item_id: `{item.item_id}`",
        f"- status: `{status}`",
        f"- content_status: `{item.status}`",
        f"- quality_score: `{quality_score if quality_score is not None else 'unknown'}`",
        f"- youtube_readiness_status: `{youtube_readiness or 'not_checked'}`",
        f"- upload_approval_id: `{approval_id or 'none'}`",
        f"- error: `{error or 'none'}`",
        "",
        "## Files",
        f"- final_mp4: `{final_mp4 or 'missing'}`",
        f"- output_dir: `{output_dir or 'missing'}`",
        *_artifact_lines(artifacts),
        "",
        "## Quality Evidence",
        *_quality_lines(qa=qa, package=package),
        "",
        "## Manual Review Checklist",
        "- [ ] Obejrzyj `final.mp4` od poczatku do konca.",
        "- [ ] Sprawdz, czy hook jest zrozumialy w pierwszych 2 sekundach.",
        "- [ ] Sprawdz, czy napisy sa czytelne na telefonie.",
        "- [ ] Sprawdz, czy thumbnail nie wyglada jak losowy placeholder.",
        "- [ ] Sprawdz, czy film nie obiecuje czegos falszywego albo ryzykownego.",
        "- [ ] Zatwierdz tylko private upload, jesli jakosc jest realnie akceptowalna.",
        "",
        "## Recommended Action",
        _recommended_action(status=str(status), content_status=ContentStatus(item.status)),
        "",
    ]
    review_path.write_text("\n".join(lines), encoding="utf-8")
    return review_path


def write_daily_review_summary(
    *,
    daily_log_path: Path,
    results: Iterable[Any],
    ready_for_review: Iterable[Any],
) -> Path:
    """Write a Markdown summary next to the JSON daily run log."""
    review_path = daily_log_path.with_suffix(".md")
    result_list = list(results)
    ready_list = list(ready_for_review)
    blocked = [item for item in result_list if str(getattr(item, "status", "")) in {"blocked", "qa_failed"}]
    manual = [item for item in result_list if str(getattr(item, "status", "")) == "manual_upload_ready"]
    approvals = [item for item in result_list if str(getattr(item, "status", "")) == "approval_pending"]
    lines = [
        "# Daily Channel Ops Review",
        "",
        "## Summary",
        f"- processed_items: `{len(result_list)}`",
        f"- ready_for_review: `{len(ready_list)}`",
        f"- approval_pending: `{len(approvals)}`",
        f"- manual_upload_ready: `{len(manual)}`",
        f"- blocked_or_qa_failed: `{len(blocked)}`",
        "",
        "## Ready For Review",
        *_daily_result_lines(ready_list),
        "",
        "## Needs Repair",
        *_daily_result_lines(blocked),
        "",
        "## Next Actions",
        *_next_actions(approvals=approvals, manual=manual, blocked=blocked),
        "",
    ]
    review_path.write_text("\n".join(lines), encoding="utf-8")
    return review_path


def _path_or_none(value: str | Path | None) -> Path | None:
    if not value:
        return None
    return Path(value)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_lines(artifacts: dict[str, str]) -> list[str]:
    if not artifacts:
        return ["- artifacts: `none`"]
    return [f"- {name}: `{path}`" for name, path in sorted(artifacts.items())]


def _quality_lines(*, qa: dict[str, Any], package: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if qa:
        lines.extend(
            [
                f"- render_ok: `{qa.get('ok', 'unknown')}`",
                f"- youtube_shorts_ready: `{qa.get('youtube_shorts_ready', 'unknown')}`",
                f"- render_overall_score: `{qa.get('overall_score', qa.get('quality_score', 'unknown'))}`",
            ]
        )
        checks = qa.get("youtube_short_checks")
        if isinstance(checks, dict):
            duration = checks.get("duration_seconds", checks.get("duration_sec", "unknown"))
            for key in ("width", "height", "has_audio"):
                lines.append(f"- {key}: `{checks.get(key, 'unknown')}`")
            lines.append(f"- duration_sec: `{duration}`")
        warnings = qa.get("warnings")
        if isinstance(warnings, list) and warnings:
            for warning in warnings:
                lines.append(f"- render_warning: `{warning}`")
    else:
        lines.append("- render_qa: `missing`")

    if package:
        checks = package.get("checks")
        if isinstance(checks, dict):
            lines.extend(
                [
                    f"- script_quality_score: `{checks.get('script_quality_score', 'unknown')}`",
                    f"- script_quality_passed: `{checks.get('script_quality_passed', 'unknown')}`",
                ]
            )
        lines.append(f"- release_status: `{package.get('status', 'unknown')}`")
    else:
        lines.append("- youtube_upload_package: `missing`")
    return lines


def _recommended_action(*, status: str, content_status: ContentStatus) -> str:
    if status == "approval_pending" or content_status == ContentStatus.APPROVAL_PENDING:
        return "Review the video and approve private upload only if quality is acceptable."
    if status == "manual_upload_ready" or content_status == ContentStatus.MANUAL_UPLOAD_READY:
        return "YouTube OAuth is not ready. Review locally, then finish OAuth before private upload."
    if status == "qa_failed" or content_status == ContentStatus.QA_FAILED:
        return "Repair script/render quality before upload approval."
    if status == "blocked" or content_status == ContentStatus.BLOCKED:
        return "Resolve blocker before rerunning daily channel ops."
    return "Continue normal channel workflow."


def _daily_result_lines(items: list[Any]) -> list[str]:
    if not items:
        return ["- none"]
    lines: list[str] = []
    for item in items:
        payload = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item)
        lines.append(
            "- "
            f"`{payload.get('status')}` "
            f"{payload.get('channel_id')} / {payload.get('topic')} "
            f"-> `{payload.get('final_mp4') or payload.get('output_dir') or 'missing'}`"
        )
    return lines


def _next_actions(*, approvals: list[Any], manual: list[Any], blocked: list[Any]) -> list[str]:
    actions: list[str] = []
    if approvals:
        actions.append("- Review approval queue before private upload.")
    if manual:
        actions.append("- Finish YouTube OAuth, then create private upload approvals.")
    if blocked:
        actions.append("- Repair QA failures before creating any upload approval.")
    if not actions:
        actions.append("- Generate or enqueue more channel topics.")
    return actions
