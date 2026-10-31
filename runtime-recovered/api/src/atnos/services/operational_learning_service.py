from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any, Literal
from urllib.parse import urlparse

from atnos.core.config import Settings


LearningArea = Literal[
    "response_quality",
    "shorts_content",
    "agent_workflow",
    "product_ux",
    "integration",
]
SourceType = Literal[
    "official_docs",
    "tutorial",
    "benchmark",
    "market_example",
    "recording",
    "repo",
    "internal_qa",
]
SourceVerdict = Literal["use", "review", "reject"]
RecommendationPriority = Literal["P0", "P1", "P2", "P3"]


@dataclass(frozen=True, slots=True)
class LearningSource:
    title: str
    source_type: SourceType
    url: str | None = None
    published_at: str | None = None
    claims: tuple[str, ...] = ()
    practical_steps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceQualityReview:
    authority_score: float
    recency_score: float
    specificity_score: float
    evidence_score: float
    actionability_score: float
    hype_penalty: float
    overall_score: float
    verdict: SourceVerdict
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ImprovementRecommendation:
    priority: RecommendationPriority
    area: LearningArea
    title: str
    rationale: str
    implementation_steps: tuple[str, ...]
    acceptance_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OperationalLearningRun:
    run_id: str
    topic: str
    area: LearningArea
    created_at: str
    source_reviews: tuple[tuple[LearningSource, SourceQualityReview], ...]
    practical_findings: tuple[str, ...]
    rejected_hype: tuple[str, ...]
    recommendations: tuple[ImprovementRecommendation, ...]
    json_path: str | None = None
    report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "topic": self.topic,
            "area": self.area,
            "created_at": self.created_at,
            "source_reviews": [
                {"source": source.to_dict(), "review": review.to_dict()}
                for source, review in self.source_reviews
            ],
            "practical_findings": list(self.practical_findings),
            "rejected_hype": list(self.rejected_hype),
            "recommendations": [item.to_dict() for item in self.recommendations],
            "json_path": self.json_path,
            "report_path": self.report_path,
        }


class OperationalLearningService:
    """Turns external/internal learning material into concrete product improvements."""

    OFFICIAL_DOMAINS = (
        "developers.openai.com",
        "platform.openai.com",
        "docs.langchain.com",
        "support.google.com",
        "ads.tiktok.com",
        "ai.google.dev",
        "docs.anthropic.com",
        "docs.github.com",
        "linear.app",
    )
    HYPE_MARKERS = (
        "viral",
        "guaranteed",
        "secret hack",
        "100%",
        "passive income",
        "make money fast",
        "no effort",
        "fully automatic without review",
        "explosive growth",
        "instant success",
    )
    SPECIFICITY_MARKERS = (
        "checklist",
        "metric",
        "threshold",
        "example",
        "steps",
        "workflow",
        "test",
        "qa",
        "acceptance",
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.learning_dir = settings.data_dir / "operational-learning"
        self.learning_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = settings.data_dir / "logs" / "operational_learning.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def evaluate_source(self, source: LearningSource) -> SourceQualityReview:
        text = _normalize_text(
            " ".join(
                (
                    source.title,
                    source.url or "",
                    " ".join(source.claims),
                    " ".join(source.practical_steps),
                    " ".join(source.evidence),
                    " ".join(source.risks),
                )
            )
        )
        domain = _domain(source.url)
        reasons: list[str] = []

        authority = 4.0
        if source.source_type == "official_docs" or any(domain.endswith(item) for item in self.OFFICIAL_DOMAINS):
            authority = 9.0
            reasons.append("official_or_primary_source")
        elif source.source_type in {"benchmark", "repo", "internal_qa"}:
            authority = 7.0
            reasons.append(f"source_type_{source.source_type}")
        elif source.source_type == "tutorial":
            authority = 5.5
            reasons.append("tutorial_needs_verification")
        else:
            reasons.append("low_authority_market_example")

        recency = self._recency_score(source.published_at)
        specificity = min(
            10.0,
            2.0
            + len(source.claims) * 1.0
            + len(source.practical_steps) * 1.4
            + sum(1.0 for marker in self.SPECIFICITY_MARKERS if marker in text),
        )
        evidence = min(10.0, 2.0 + len(source.evidence) * 2.0)
        if source.source_type == "benchmark":
            evidence += 1.5
        evidence = min(10.0, evidence)
        actionability = min(10.0, 2.0 + len(source.practical_steps) * 2.0)
        hype_hits = tuple(marker for marker in self.HYPE_MARKERS if marker in text)
        hype_penalty = min(5.0, len(hype_hits) * 1.5)
        if hype_hits:
            reasons.append("hype_markers:" + ", ".join(hype_hits[:3]))
        if not source.practical_steps:
            reasons.append("no_practical_steps")
        if not source.evidence:
            reasons.append("no_evidence")

        overall = round(
            authority * 0.24
            + recency * 0.14
            + specificity * 0.2
            + evidence * 0.18
            + actionability * 0.24
            - hype_penalty,
            2,
        )
        if overall >= 7.0 and hype_penalty <= 1.5:
            verdict: SourceVerdict = "use"
        elif overall >= 5.0:
            verdict = "review"
        else:
            verdict = "reject"
        return SourceQualityReview(
            authority_score=round(authority, 2),
            recency_score=round(recency, 2),
            specificity_score=round(specificity, 2),
            evidence_score=round(evidence, 2),
            actionability_score=round(actionability, 2),
            hype_penalty=round(hype_penalty, 2),
            overall_score=overall,
            verdict=verdict,
            reasons=tuple(reasons),
        )

    def run_research_to_improvement(
        self,
        *,
        topic: str,
        area: LearningArea,
        sources: tuple[LearningSource, ...],
        persist: bool = True,
    ) -> OperationalLearningRun:
        if not topic.strip():
            raise ValueError("topic cannot be empty")
        if not sources:
            raise ValueError("at least one learning source is required")

        reviews = tuple((source, self.evaluate_source(source)) for source in sources)
        practical_findings = self._extract_practical_findings(reviews)
        rejected_hype = self._extract_rejected_hype(reviews)
        recommendations = self._recommend(area=area, findings=practical_findings, sources=reviews)
        created_at = datetime.now(UTC).isoformat()
        run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{_slugify(area)}_{_slugify(topic)[:36]}"
        run = OperationalLearningRun(
            run_id=run_id,
            topic=topic.strip(),
            area=area,
            created_at=created_at,
            source_reviews=reviews,
            practical_findings=practical_findings,
            rejected_hype=rejected_hype,
            recommendations=recommendations,
        )
        if not persist:
            return run
        return self._persist_run(run)

    def build_baseline_improvement_plan(self, *, persist: bool = True) -> tuple[OperationalLearningRun, ...]:
        runs = (
            self.run_research_to_improvement(
                topic="Jakosc odpowiedzi Atnosa",
                area="response_quality",
                sources=(
                    LearningSource(
                        title="OpenAI evaluation best practices",
                        source_type="official_docs",
                        url="https://developers.openai.com/api/docs/guides/evaluation-best-practices",
                        published_at="2026",
                        claims=(
                            "Evals should be task-specific and reflect real production distributions.",
                            "Logs and human feedback should grow the eval set over time.",
                        ),
                        practical_steps=(
                            "Collect bad-tone and low-quality answers as eval cases.",
                            "Score answers against clarity, truthfulness, structure and usefulness.",
                            "Calibrate automated scoring with human feedback.",
                        ),
                        evidence=("official OpenAI documentation", "production eval workflow"),
                    ),
                    LearningSource(
                        title="Atnos response quality contract",
                        source_type="internal_qa",
                        claims=("Atnos already injects a response quality standard into provider prompts.",),
                        practical_steps=(
                            "Add regression tests for frustrated, overwhelmed and decision-seeking user turns.",
                            "Track wrong_tone feedback into improvement notes.",
                        ),
                        evidence=("response_quality_contract_service.py", "test_response_quality_contract_service.py"),
                    ),
                ),
                persist=persist,
            ),
            self.run_research_to_improvement(
                topic="Jakosc shortow i contentu AI",
                area="shorts_content",
                sources=(
                    LearningSource(
                        title="YouTube Shorts official eligibility and analytics guidance",
                        source_type="official_docs",
                        url="https://support.google.com/youtube/answer/15424877",
                        published_at="2026",
                        claims=(
                            "Square or vertical videos up to three minutes can be categorized as Shorts.",
                            "Analytics should be used to understand Shorts performance.",
                        ),
                        practical_steps=(
                            "Keep render QA strict on vertical/square aspect ratio and duration.",
                            "Feed Shorts analytics into ChannelOps learning loop.",
                        ),
                        evidence=("YouTube Help Center",),
                    ),
                    LearningSource(
                        title="TikTok creative best practices",
                        source_type="official_docs",
                        url="https://ads.tiktok.com/help/article/creative-best-practices",
                        published_at="2025",
                        claims=(
                            "Short-form creative should use sound, 9:16 orientation, safe zones, hook, captions and CTA.",
                            "Creators should continuously test and refresh creative.",
                        ),
                        practical_steps=(
                            "Generate hook variants before full render.",
                            "Add captions/text overlay readability checks.",
                            "Store winning hook and visual patterns per channel.",
                        ),
                        evidence=("TikTok Business Help Center",),
                    ),
                ),
                persist=persist,
            ),
            self.run_research_to_improvement(
                topic="Workflow agenta AI",
                area="agent_workflow",
                sources=(
                    LearningSource(
                        title="LangGraph overview",
                        source_type="official_docs",
                        url="https://docs.langchain.com/oss/python/langgraph/overview",
                        published_at="2026",
                        claims=(
                            "Reliable agents need durable execution, streaming, human-in-the-loop and memory.",
                            "Long-running agents need state across steps and sessions.",
                        ),
                        practical_steps=(
                            "Represent larger tasks as plan -> execute -> verify -> recover.",
                            "Log state transitions and human approval points.",
                            "Persist intermediate state so long jobs can be resumed or audited.",
                        ),
                        evidence=("LangChain docs",),
                    ),
                    LearningSource(
                        title="Atnos agent execution model",
                        source_type="internal_qa",
                        claims=("Atnos already has execution director, verification and failure recovery modules.",),
                        practical_steps=(
                            "Use quality runs after every large agent task.",
                            "Expose agent phase and blockers in Chat UI.",
                        ),
                        evidence=("agent/execution_director.py", "agent/failure_recovery.py"),
                    ),
                ),
                persist=persist,
            ),
        )
        return runs

    def _persist_run(self, run: OperationalLearningRun) -> OperationalLearningRun:
        target_dir = self.learning_dir / _slugify(run.area)
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / f"{run.run_id}.json"
        report_path = target_dir / f"{run.run_id}.md"
        materialized = OperationalLearningRun(
            **{
                **run.to_dict(),
                "source_reviews": run.source_reviews,
                "practical_findings": run.practical_findings,
                "rejected_hype": run.rejected_hype,
                "recommendations": run.recommendations,
                "json_path": json_path.as_posix(),
                "report_path": report_path.as_posix(),
            }
        )
        json_path.write_text(json.dumps(materialized.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(self._render_markdown(materialized), encoding="utf-8")
        self._log(
            {
                "event": "operational_learning_run_recorded",
                "run_id": materialized.run_id,
                "area": materialized.area,
                "topic": materialized.topic,
                "recommendations": len(materialized.recommendations),
                "report_path": materialized.report_path,
            }
        )
        return materialized

    @staticmethod
    def _extract_practical_findings(
        reviews: tuple[tuple[LearningSource, SourceQualityReview], ...]
    ) -> tuple[str, ...]:
        findings: list[str] = []
        for source, review in reviews:
            if review.verdict == "reject":
                continue
            for step in source.practical_steps:
                cleaned = " ".join(step.split()).strip()
                if cleaned and cleaned not in findings:
                    findings.append(cleaned)
        return tuple(findings)

    @staticmethod
    def _extract_rejected_hype(
        reviews: tuple[tuple[LearningSource, SourceQualityReview], ...]
    ) -> tuple[str, ...]:
        rejected: list[str] = []
        for source, review in reviews:
            if review.verdict == "reject" or review.hype_penalty >= 3:
                rejected.append(f"{source.title}: {', '.join(review.reasons) or 'low quality'}")
        return tuple(rejected)

    def _recommend(
        self,
        *,
        area: LearningArea,
        findings: tuple[str, ...],
        sources: tuple[tuple[LearningSource, SourceQualityReview], ...],
    ) -> tuple[ImprovementRecommendation, ...]:
        accepted_sources = sum(1 for _, review in sources if review.verdict == "use")
        source_note = f"Based on {accepted_sources} high-quality source(s) and {len(findings)} practical finding(s)."
        if area == "response_quality":
            return (
                ImprovementRecommendation(
                    priority="P0",
                    area=area,
                    title="Create response-quality eval cases from real failures",
                    rationale=f"{source_note} Atnos should not rely on vibe checks for answer quality.",
                    implementation_steps=(
                        "Collect examples tagged wrong_tone, too_long, hallucinated_capability and no_next_step.",
                        "Add deterministic tests for response contract and emotional intelligence routing.",
                        "Record quality runs after prompt/policy changes.",
                    ),
                    acceptance_checks=(
                        "At least 10 regression cases exist for common bad-answer modes.",
                        "Quality run records pass/warn/fail with evidence.",
                    ),
                ),
                ImprovementRecommendation(
                    priority="P1",
                    area=area,
                    title="Turn feedback into prompt and memory updates only after verification",
                    rationale="Single emotional turns should not permanently rewrite user preferences.",
                    implementation_steps=(
                        "Route repeated feedback into self-improvement notes.",
                        "Require Codex or human review before changing global response policy.",
                    ),
                    acceptance_checks=("No one-off complaint becomes permanent memory without review.",),
                ),
            )
        if area == "shorts_content":
            return (
                ImprovementRecommendation(
                    priority="P0",
                    area=area,
                    title="Add hook variants and asset plan before render",
                    rationale=f"{source_note} Short quality is decided before FFmpeg starts.",
                    implementation_steps=(
                        "Generate 3-5 hook variants per topic.",
                        "Score hooks for clarity, tension, specificity and platform fit.",
                        "Write ShortAssetPlan with visual beats, captions, b-roll prompts and SFX notes.",
                    ),
                    acceptance_checks=(
                        "Every short output has hook_variants.json and short_asset_plan.json.",
                        "Render is blocked if the selected hook score is below threshold.",
                    ),
                ),
                ImprovementRecommendation(
                    priority="P1",
                    area=area,
                    title="Use analytics feedback to update channel style guide",
                    rationale="Production should learn from retention and engagement, not only from theory.",
                    implementation_steps=(
                        "Ingest YouTube metrics after private/public review.",
                        "Identify winning hook, pacing and visual patterns.",
                        "Update per-channel style guide with proven patterns and weak patterns.",
                    ),
                    acceptance_checks=("Channel learning report changes the next daily plan recommendations.",),
                ),
            )
        if area == "agent_workflow":
            return (
                ImprovementRecommendation(
                    priority="P0",
                    area=area,
                    title="Make every major task traceable as plan-execute-verify-recover",
                    rationale=f"{source_note} Long-running agent work needs state and recovery, not hidden background magic.",
                    implementation_steps=(
                        "Record plan steps and current phase before tool execution.",
                        "Persist verification result and failure recovery recommendation.",
                        "Surface blocked/human-required states in Chat.",
                    ),
                    acceptance_checks=(
                        "Agent task has decision log, tool log, verification result and final status.",
                        "A failed tool call produces a recovery path instead of a vague failure.",
                    ),
                ),
                ImprovementRecommendation(
                    priority="P1",
                    area=area,
                    title="Keep high-risk work human-in-the-loop",
                    rationale="Useful autonomy requires explicit approval boundaries.",
                    implementation_steps=(
                        "Keep Ubuntu sandbox full-auto separate from main PC and publication actions.",
                        "Pause on upload, billing, secrets, account changes and destructive commands.",
                    ),
                    acceptance_checks=("High-risk non-sandbox actions require approval in policy tests.",),
                ),
            )
        return (
            ImprovementRecommendation(
                priority="P2",
                area=area,
                title="Convert research into one focused product change",
                rationale=source_note,
                implementation_steps=(
                    "Pick one actionable finding.",
                    "Add a test or QA check.",
                    "Implement the smallest patch that changes behavior.",
                ),
                acceptance_checks=("The change has evidence, a test, and a rollback path.",),
            ),
        )

    @staticmethod
    def _recency_score(published_at: str | None) -> float:
        if not published_at:
            return 5.0
        match = re.search(r"(20\d{2})", published_at)
        if match is None:
            return 5.0
        year = int(match.group(1))
        current_year = datetime.now(UTC).year
        age = max(0, current_year - year)
        if age <= 1:
            return 9.0
        if age == 2:
            return 7.0
        if age <= 4:
            return 5.5
        return 3.0

    @staticmethod
    def _render_markdown(run: OperationalLearningRun) -> str:
        lines = [
            "# Operational Learning Run",
            "",
            f"- run_id: `{run.run_id}`",
            f"- area: `{run.area}`",
            f"- topic: {run.topic}",
            f"- created_at: `{run.created_at}`",
            "",
            "## Source reviews",
        ]
        for source, review in run.source_reviews:
            lines.extend(
                [
                    f"- `{review.verdict}` {source.title} ({review.overall_score}/10)",
                    f"  - type: `{source.source_type}`",
                    f"  - url: {source.url or 'brak'}",
                    f"  - reasons: {', '.join(review.reasons) or 'brak'}",
                ]
            )
        lines.extend(["", "## Practical findings"])
        lines.extend(f"- {item}" for item in run.practical_findings) if run.practical_findings else lines.append("- brak")
        lines.extend(["", "## Rejected hype"])
        lines.extend(f"- {item}" for item in run.rejected_hype) if run.rejected_hype else lines.append("- brak")
        lines.extend(["", "## Recommendations"])
        for item in run.recommendations:
            lines.extend(
                [
                    f"### {item.priority} - {item.title}",
                    "",
                    item.rationale,
                    "",
                    "Implementation:",
                    *[f"- {step}" for step in item.implementation_steps],
                    "",
                    "Acceptance:",
                    *[f"- {check}" for check in item.acceptance_checks],
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    def _log(self, payload: dict[str, Any]) -> None:
        event = {"timestamp": datetime.now(UTC).isoformat(), **payload}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def _domain(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _normalize_text(value: str) -> str:
    translated = (value or "").casefold()
    translated = translated.translate(
        str.maketrans(
            {
                "ą": "a",
                "ć": "c",
                "ę": "e",
                "ł": "l",
                "ń": "n",
                "ó": "o",
                "ś": "s",
                "ż": "z",
                "ź": "z",
            }
        )
    )
    return " ".join(translated.split())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:90] or "operational-learning"
