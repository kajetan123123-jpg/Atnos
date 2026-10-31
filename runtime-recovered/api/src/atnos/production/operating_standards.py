from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StandardItem:
    key: str
    label: str
    purpose: str
    status: str
    gate: str
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "purpose": self.purpose,
            "status": self.status,
            "gate": self.gate,
            "required": self.required,
        }


def _latest_side_research_report(data_dir: Path) -> Path | None:
    reports_dir = data_dir / "learning" / "side_research"
    if not reports_dir.exists():
        return None
    reports = sorted(
        [
            path
            for path in reports_dir.rglob("*.md")
            if path.name.casefold() not in {"readme.md"}
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return reports[0] if reports else None


def _project_type(project_id: str | None) -> str:
    normalized = str(project_id or "").strip().lower()
    if normalized.startswith("investment:") or "invest" in normalized:
        return "investment"
    if normalized.startswith("creator:") or "onlyfans" in normalized or "model" in normalized:
        return "synthetic_creator"
    return "shorts_channel"


def _shorts_items() -> list[StandardItem]:
    return [
        StandardItem(
            key="real_signal_research",
            label="Real signal research",
            purpose="Topic selection must come from YouTube/Trends/search signals, not from pure LLM guesses.",
            status="active",
            gate="topic_generation",
        ),
        StandardItem(
            key="style_guide_required",
            label="Channel style guide",
            purpose="Every hook, script, thumbnail and asset plan must use the account style guide.",
            status="active",
            gate="pre_render",
        ),
        StandardItem(
            key="private_qa_before_public",
            label="Private QA before public",
            purpose="Public upload stays blocked until private render, QA report and public-quality gate pass.",
            status="active",
            gate="publication",
        ),
        StandardItem(
            key="analytics_learning_loop",
            label="Analytics learning loop",
            purpose="Collect 24h, 72h and 7d performance before changing the content strategy.",
            status="active",
            gate="post_publish",
        ),
    ]


def _investment_items() -> list[StandardItem]:
    return [
        StandardItem(
            key="education_first",
            label="Education-first investing",
            purpose="Use SEC, FINRA, CFA, Khan Academy, Morningstar and Bogleheads as baseline learning sources.",
            status="active",
            gate="research",
        ),
        StandardItem(
            key="read_only_market_data",
            label="Read-only market data",
            purpose="Connect market data and watchlists before any broker execution capability.",
            status="active",
            gate="data_access",
        ),
        StandardItem(
            key="paper_trading_before_real_money",
            label="Paper trading before real money",
            purpose="Strategy experiments must run in a paper-trading sandbox before real capital is exposed.",
            status="blocked_until_configured",
            gate="execution",
        ),
        StandardItem(
            key="risk_policy_required",
            label="Risk policy required",
            purpose="No real trade action without max loss, position sizing, forbidden instruments and kill switch.",
            status="blocked_until_configured",
            gate="execution",
        ),
    ]


def _synthetic_creator_items() -> list[StandardItem]:
    return [
        StandardItem(
            key="fictional_persona_bible",
            label="Fictional persona bible",
            purpose="Synthetic creators need a consistent identity, boundaries, disclosure and forbidden likeness rules.",
            status="active",
            gate="persona_setup",
        ),
        StandardItem(
            key="asset_rights_ledger",
            label="Asset rights ledger",
            purpose="Track prompt, model, source, license and disclosure for every image and video asset.",
            status="active",
            gate="asset_generation",
        ),
        StandardItem(
            key="no_real_person_deepfake",
            label="No real-person deepfake",
            purpose="Do not imitate or face-swap real people without explicit rights and compliance review.",
            status="active",
            gate="safety",
        ),
        StandardItem(
            key="platform_policy_review",
            label="Platform policy review",
            purpose="Review platform rules before OnlyFans or social publishing workflows run.",
            status="blocked_until_configured",
            gate="publication",
        ),
    ]


def _forbidden_items(project_type: str) -> list[str]:
    common = [
        "No public autopost without QA evidence and configured platform permissions.",
        "No reused third-party video clips as core content without rights proof.",
        "No fake certainty, fake earnings claims or fabricated analytics.",
    ]
    if project_type == "investment":
        return [
            *common,
            "No real-money auto trading until risk policy, paper-trading validation and explicit approval exist.",
            "No guaranteed-profit language or direct buy/sell certainty.",
        ]
    if project_type == "synthetic_creator":
        return [
            *common,
            "No non-consensual real-person likeness, face-swap or impersonation workflow.",
            "No adult-content publishing automation before platform policy and consent rules are configured.",
        ]
    return [
        *common,
        "No low-quality fallback voice for public Shorts.",
        "No visual render that is unrelated to the script segment.",
    ]


def build_operating_standards(settings: Any, project_id: str | None = None) -> dict[str, Any]:
    data_dir = Path(settings.data_dir).resolve()
    report = _latest_side_research_report(data_dir)
    project_type = _project_type(project_id)

    sections: list[dict[str, Any]] = [
        {
            "key": "shorts_quality",
            "title": "Shorts quality system",
            "items": [item.to_dict() for item in _shorts_items()],
        },
    ]
    if project_type == "investment":
        sections.insert(
            0,
            {
                "key": "investment_safety",
                "title": "Investment safety system",
                "items": [item.to_dict() for item in _investment_items()],
            },
        )
    elif project_type == "synthetic_creator":
        sections.insert(
            0,
            {
                "key": "synthetic_creator_safety",
                "title": "Synthetic creator safety system",
                "items": [item.to_dict() for item in _synthetic_creator_items()],
            },
        )

    required_items = [
        item
        for section in sections
        for item in section["items"]
        if bool(item.get("required"))
    ]
    blocked_items = [
        item
        for section in sections
        for item in section["items"]
        if str(item.get("status")) == "blocked_until_configured"
    ]

    return {
        "status": "ready" if report else "baseline",
        "project_id": project_id,
        "project_type": project_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(report) if report else None,
            "exists": bool(report),
            "last_modified": datetime.fromtimestamp(report.stat().st_mtime, tz=timezone.utc).isoformat()
            if report
            else None,
        },
        "summary": (
            "Research report is available and mapped into operational quality standards."
            if report
            else "Using built-in baseline standards because no side research report was found."
        ),
        "sections": sections,
        "forbidden": _forbidden_items(project_type),
        "metrics": {
            "required_count": len(required_items),
            "blocked_until_configured_count": len(blocked_items),
        },
        "next_actions": [
            "Use these standards as pre-render and pre-publication gates.",
            "Attach the relevant checklist to every production job evidence bundle.",
            "Do not enable real-money trading or adult creator autopublishing until blocked standards are resolved.",
        ],
    }
