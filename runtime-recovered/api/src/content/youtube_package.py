from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import json
from typing import Any

from content.script_generator import ShortScript, _clean_topic_for_script
from content.script_quality_gate import ScriptQualityReport


@dataclass(frozen=True, slots=True)
class YouTubeReleasePackage:
    status: str
    privacy: str
    requires_upload_approval: bool
    title: str
    description: str
    tags: tuple[str, ...]
    checks: dict[str, object]
    artifacts: dict[str, str]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "privacy": self.privacy,
            "requires_upload_approval": self.requires_upload_approval,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "checks": dict(self.checks),
            "artifacts": dict(self.artifacts),
            "warnings": list(self.warnings),
        }


def build_youtube_release_package(
    *,
    topic: str,
    script: ShortScript,
    output_dir: Path,
    render_qa: dict[str, object],
    quality_report: ScriptQualityReport,
    privacy: str = "private",
    requires_upload_approval: bool = True,
) -> YouTubeReleasePackage:
    warnings = tuple(str(item) for item in (render_qa.get("warnings") or ()))
    visual_report = _load_visual_report(output_dir / "visual_quality_report.json")
    visual_passed = bool(visual_report and visual_report.get("passed") is True)
    visual_score = visual_report.get("overall_score") if visual_report else None
    visual_issues = tuple(str(item) for item in ((visual_report or {}).get("issues") or ()))
    if not visual_report:
        visual_issues = ("visual_quality_report.json missing",)
    render_ok = bool(render_qa.get("ok"))
    youtube_shorts_ready = render_qa.get("youtube_shorts_ready")
    shorts_ready = youtube_shorts_ready is not False
    clean_topic = _clean_topic_for_script(topic)
    status = (
        "manual_upload_ready"
        if render_ok and quality_report.passed and shorts_ready and visual_passed
        else "needs_review_before_upload"
    )
    title = _build_title(script=script, topic=clean_topic)
    description = _build_description(
        topic=clean_topic,
        script=script,
        output_dir=output_dir,
        quality_report=quality_report,
        render_qa=render_qa,
        status=status,
    )
    return YouTubeReleasePackage(
        status=status,
        privacy=privacy,
        requires_upload_approval=requires_upload_approval,
        title=title,
        description=description,
        tags=tuple(_build_tags(topic=clean_topic, script=script)),
        checks={
            "script_quality_score": quality_report.overall_score,
            "script_quality_passed": quality_report.passed,
            "render_ok": render_ok,
            "youtube_shorts_ready": youtube_shorts_ready,
            "youtube_short_checks": render_qa.get("youtube_short_checks") or {},
            "visual_quality_passed": visual_passed,
            "visual_quality_score": visual_score,
        },
        artifacts={
            "script": str(output_dir / "script.md"),
            "narration": str(output_dir / "narration.wav"),
            "subtitles": str(output_dir / "subtitles.srt"),
            "thumbnail": str(output_dir / "thumbnail.png"),
            "visual_storyboard": str(output_dir / "visual_storyboard.json"),
            "visual_quality_report": str(output_dir / "visual_quality_report.json"),
            "final_mp4": str(output_dir / "final.mp4"),
            "render_qa": str(output_dir / "render_qa.json"),
            "production_report": str(output_dir / "production_report.md"),
        },
        warnings=(*warnings, *visual_issues),
    )


def _build_title(*, script: ShortScript, topic: str) -> str:
    title = " ".join((script.title or "").split()).strip()
    if not title:
        title = f"Short: {topic}"
    title = title.replace("\n", " ")
    if len(title) > 96:
        title = title[:93].rstrip(" -,.") + "..."
    return title[:100]


def _build_description(
    *,
    topic: str,
    script: ShortScript,
    output_dir: Path,
    quality_report: ScriptQualityReport,
    render_qa: dict[str, object],
    status: str,
) -> str:
    lines = [
        script.hook.strip(),
        "",
        f"Temat: {topic}",
        "Status: prywatny upload do review.",
        f"QA: script={quality_report.overall_score}/10, render={'OK' if render_qa.get('ok') else 'REVIEW'}",
        f"Pakiet lokalny: {output_dir}",
        "",
        "Checklist przed publikacja:",
        "- obejrzyj final.mp4 od poczatku do konca",
        "- sprawdz czy napisy sa czytelne i bez literowek",
        "- sprawdz czy thumbnail jest zgodny z tematem",
        "- publikuj publicznie tylko po recznej akceptacji",
        "",
        f"Atnos status: {status}",
    ]
    description = "\n".join(line for line in lines if line is not None).strip()
    return description[:5000]


def _build_tags(*, topic: str, script: ShortScript) -> list[str]:
    base = ["Atnos", "Shorts", "AI", "lokalne AI", "automatyzacja"]
    raw = " ".join((topic, script.title, script.hook))
    tokens = [
        token
        for token in re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", raw, flags=re.UNICODE)
        if 3 <= len(token) <= 24
    ]
    seen: set[str] = set()
    tags: list[str] = []
    for tag in [*base, *tokens]:
        normalized = tag.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        tags.append(tag)
        if len(tags) >= 15:
            break
    return tags


def _load_visual_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"passed": False, "overall_score": 0, "issues": ["visual_quality_report.json invalid"]}
    return raw if isinstance(raw, dict) else {"passed": False, "overall_score": 0, "issues": ["visual quality report shape invalid"]}
