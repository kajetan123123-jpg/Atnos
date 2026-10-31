from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Literal


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402


EvidenceLevel = Literal["direct_official", "market_index", "article_analysis", "blocked_direct_view"]


@dataclass(frozen=True, slots=True)
class LearningSource:
    title: str
    url: str
    evidence_level: EvidenceLevel
    useful_for: list[str]
    notes: str


@dataclass(frozen=True, slots=True)
class PatternCard:
    pattern_id: str
    name: str
    observed_signal: str
    structure: list[str]
    why_it_works: list[str]
    production_rules: list[str]
    risks: list[str]
    source_urls: list[str]


@dataclass(frozen=True, slots=True)
class RecreationBrief:
    brief_id: str
    based_on_pattern: str
    channel_fit: str
    original_topic: str
    hook: str
    beat_timeline: list[str]
    visual_direction: list[str]
    narration_direction: list[str]
    qa_requirements: list[str]
    forbidden_copying: list[str]


PROMPTIEX = "https://promptiex.com/most-watched-ai-videos-on-youtube/"
YOUTUBE_ANALYTICS = "https://support.google.com/youtube/answer/12942217?co=YOUTUBE._YTVideoType%3Dshorts&hl=en"
YOUTUBE_SHORTS = "https://support.google.com/youtube/answer/10059070?hl=en-uk"
TOMS_AI_CATS = "https://www.tomsguide.com/ai/ai-image-video/ai-cat-videos-are-suddenly-everywhere-heres-why-the-internet-cant-stop-watching"
VIRALFEED = "https://viralfeed.ai/faceless-creator-hub"


def main() -> int:
    settings = Settings()
    created_at = datetime.now(UTC)
    run_id = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_ai_shorts_learning"
    learning_dir = settings.data_dir / "learning" / "faceless-ai-shorts" / run_id
    learning_dir.mkdir(parents=True, exist_ok=True)

    sources = _build_sources()
    patterns = _build_pattern_cards()
    briefs = _build_recreation_briefs()

    payload = {
        "run_id": run_id,
        "created_at": created_at.isoformat(),
        "objective": (
            "Atnos learns from public faceless/AI Shorts examples and creates legal "
            "format-recreation briefs without copying protected scripts, audio, characters, "
            "footage, logos or creator-specific style."
        ),
        "direct_video_view_status": {
            "status": "partial",
            "reason": (
                "Direct fetch of specific YouTube Shorts was blocked by online fetch throttling "
                "and YouTube consent redirects. This run uses public ranking/analysis pages and "
                "official YouTube docs instead of pretending full watch-through access."
            ),
        },
        "sources": [asdict(item) for item in sources],
        "pattern_cards": [asdict(item) for item in patterns],
        "recreation_briefs": [asdict(item) for item in briefs],
        "production_decision": {
            "best_next_sample": "brief-cat-agent-microstory",
            "reason": (
                "AI animal micro-stories have the strongest public signal in the ranking list and "
                "are currently described as highly shareable. The Atnos version must use original "
                "characters and original script."
            ),
        },
        "safety_boundaries": [
            "Do not clone exact scripts, titles, audio, character designs, logos, watermarks or footage.",
            "Do not use celebrity likenesses, named franchises or copyrighted characters as templates.",
            "Use pattern-level learning: hook shape, pacing, contrast, payoff, caption density and visual grammar.",
            "Any upload remains private/manual until QA and owner approval.",
        ],
    }

    json_path = learning_dir / "learning_session.json"
    report_path = learning_dir / "learning_session.md"
    briefs_path = learning_dir / "recreation_briefs.json"
    briefs_md_path = learning_dir / "recreation_briefs.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    briefs_path.write_text(
        json.dumps([asdict(item) for item in briefs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(_render_report(payload), encoding="utf-8")
    briefs_md_path.write_text(_render_briefs(briefs), encoding="utf-8")
    obsidian_path = _write_obsidian_note(settings=settings, run_id=run_id, report_text=report_path.read_text(encoding="utf-8"))
    _append_log(settings=settings, run_id=run_id, report_path=report_path, json_path=json_path, obsidian_path=obsidian_path)

    print(
        json.dumps(
            {
                "status": "recorded",
                "run_id": run_id,
                "learning_dir": str(learning_dir),
                "report_path": str(report_path),
                "json_path": str(json_path),
                "briefs_path": str(briefs_path),
                "obsidian_path": str(obsidian_path) if obsidian_path else None,
                "pattern_cards": len(patterns),
                "recreation_briefs": len(briefs),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _build_sources() -> list[LearningSource]:
    return [
        LearningSource(
            title="YouTube Help: Shorts analytics and performance metrics",
            url=YOUTUBE_ANALYTICS,
            evidence_level="direct_official",
            useful_for=["engaged views", "shown in feed", "swipe-vs-view", "top Shorts by views"],
            notes=(
                "Use YouTube's own metrics as the feedback loop: views, likes, subscribers, "
                "shown in feed, how many chose to view and top Shorts by views."
            ),
        ),
        LearningSource(
            title="YouTube Help: Shorts creation and engaged views",
            url=YOUTUBE_SHORTS,
            evidence_level="direct_official",
            useful_for=["format requirements", "privacy workflow", "monetization criteria"],
            notes=(
                "Shorts can be uploaded as vertical videos from desktop; engaged views remain the "
                "important monetization/eligibility metric."
            ),
        ),
        LearningSource(
            title="Promptiex: most watched AI videos / Shorts ranking",
            url=PROMPTIEX,
            evidence_level="market_index",
            useful_for=["high-view AI story examples", "animal/story pattern", "ranking candidates"],
            notes=(
                "Market index claims very high-view AI Shorts, led by Toon Stories and several "
                "kitten/animal narrative channels. Treat view counts as external index claims."
            ),
        ),
        LearningSource(
            title="Tom's Guide: AI cat videos going viral",
            url=TOMS_AI_CATS,
            evidence_level="article_analysis",
            useful_for=["AI animal story explanation", "shareability signal", "visual realism vs absurdism"],
            notes=(
                "Explains why AI-generated cat videos are spreading: familiar subject, absurd scenarios, "
                "improved generation quality and easy shareability."
            ),
        ),
        LearningSource(
            title="ViralFeed Faceless Creator Hub",
            url=VIRALFEED,
            evidence_level="market_index",
            useful_for=["faceless niches", "RPM hints", "automation suitability", "format list"],
            notes=(
                "Catalogues faceless Shorts niches such as what-if history, health/body science, horror, "
                "motivation/wisdom, space/science, geography, survival and finance."
            ),
        ),
        LearningSource(
            title="Direct YouTube Shorts fetch attempt",
            url="https://youtube.com/shorts/",
            evidence_level="blocked_direct_view",
            useful_for=["audit honesty"],
            notes=(
                "Specific YouTube Shorts fetches were blocked by online fetch throttling and consent "
                "redirects, so this run does not claim frame-by-frame viewing."
            ),
        ),
    ]


def _build_pattern_cards() -> list[PatternCard]:
    return [
        PatternCard(
            pattern_id="ai-animal-microstory",
            name="AI animal micro-story",
            observed_signal="Public ranking heavily features cat/kitten/animal narrative AI Shorts.",
            structure=[
                "0-2s: impossible/funny animal situation in one sentence",
                "2-8s: immediate visual proof of the situation",
                "8-22s: escalation with 2-3 quick beats",
                "22-35s: emotional or absurd payoff",
                "35-45s: quick loop-back or teaser for next episode",
            ],
            why_it_works=[
                "Animals are instantly legible with no host introduction.",
                "Absurdity creates curiosity and shareability.",
                "Serialized characters can build recognition without showing a human creator.",
            ],
            production_rules=[
                "Use one original recurring animal character.",
                "Keep each scene visually different; no static wallpaper.",
                "Caption only the current beat, not the full title repeated everywhere.",
                "Hook text must be readable in under one second.",
            ],
            risks=[
                "Can become low-value AI slop if the story has no payoff.",
                "Do not copy exact animal designs, titles or storylines from ranked examples.",
            ],
            source_urls=[PROMPTIEX, TOMS_AI_CATS],
        ),
        PatternCard(
            pattern_id="what-if-history-pov",
            name="What-if history / POV transformation",
            observed_signal="Faceless niche libraries rate alternate history and ancient POV as strong retention formats.",
            structure=[
                "0-2s: 'What if X happened in Y era?'",
                "2-12s: hard visual contrast between modern object and old world",
                "12-35s: three consequences, each with one visual beat",
                "35-50s: twist or human implication",
            ],
            why_it_works=[
                "Simple counterfactual creates instant curiosity.",
                "Visual contrast carries the short even without a human face.",
                "Comment bait can be natural if the premise invites debate.",
            ],
            production_rules=[
                "Avoid real copyrighted franchises/celebrities.",
                "Use factual grounding in captions to avoid pure nonsense.",
                "Put the contrast object on screen before explaining it.",
            ],
            risks=[
                "Historical misinformation if framed as fact.",
                "Can overuse generic cinematic ancient-city visuals.",
            ],
            source_urls=[VIRALFEED],
        ),
        PatternCard(
            pattern_id="science-body-challenge",
            name="Health/body science micro-explainer",
            observed_signal="Health/body science appears as a high-RPM faceless niche, but needs careful policy handling.",
            structure=[
                "0-2s: specific body claim framed as a question",
                "2-10s: visual body/organ animation",
                "10-35s: explain mechanism with 2-3 simple steps",
                "35-50s: practical takeaway with no medical overclaim",
            ],
            why_it_works=[
                "Specific body topics are searchable and shareable.",
                "Visual explainers help retention because the viewer tracks a process.",
            ],
            production_rules=[
                "No diagnosis, treatment promises or fearmongering.",
                "Use 'may/can' language and cite source in description when factual.",
                "Make thumbnail a clear 3-word question, not a panic claim.",
            ],
            risks=[
                "Medical misinformation and policy risk.",
                "Low trust if generated visuals imply false certainty.",
            ],
            source_urls=[VIRALFEED],
        ),
        PatternCard(
            pattern_id="finance-money-contrast",
            name="Finance/money contrast lesson",
            observed_signal="Finance/money is marked as high RPM and works well with faceless explainers.",
            structure=[
                "0-2s: contrast hook: 'Poor people do X, wealthy people do Y'",
                "2-12s: visual split-screen or object metaphor",
                "12-35s: one practical behavior difference",
                "35-45s: concrete next action, no get-rich promise",
            ],
            why_it_works=[
                "Contrast is easy to understand in the feed.",
                "Money habits are personally relevant and comment-friendly.",
            ],
            production_rules=[
                "No guaranteed income, trading calls or financial advice.",
                "Focus on behavior, budgeting, tools or concepts.",
                "Add disclaimer in description for finance-adjacent content.",
            ],
            risks=[
                "Can become clickbait or financial advice.",
                "Avoid crypto hype and false RPM claims.",
            ],
            source_urls=[VIRALFEED],
        ),
        PatternCard(
            pattern_id="survival-if-this-do-that",
            name="Survival / if-this-then-that",
            observed_signal="Survival/life-hack format is described as highly shareable and automation-friendly.",
            structure=[
                "0-2s: urgent but non-fake scenario",
                "2-15s: wrong action vs right action",
                "15-35s: three visual steps",
                "35-45s: save/share CTA",
            ],
            why_it_works=[
                "Clear utility creates share intent.",
                "If-this-then-that structure is easy to caption and remember.",
            ],
            production_rules=[
                "Only use safe, verifiable advice.",
                "Do not present dangerous hacks as certain life-saving facts.",
                "Avoid fake emergency footage.",
            ],
            risks=[
                "High harm risk if advice is wrong.",
                "Requires factual review before production.",
            ],
            source_urls=[VIRALFEED],
        ),
    ]


def _build_recreation_briefs() -> list[RecreationBrief]:
    return [
        RecreationBrief(
            brief_id="brief-cat-agent-microstory",
            based_on_pattern="ai-animal-microstory",
            channel_fit="faceless-ai-pl / AI content channel",
            original_topic="Kot w serwerowni odkrywa, że lokalny agent AI robi lepsze shorty niż człowiek bez planu.",
            hook="Ten kot znalazł największy błąd kanałów AI.",
            beat_timeline=[
                "0-2s: close-up kota przy migającym monitorze; hook na ekranie.",
                "2-7s: kot patrzy na folder z chaotycznymi plikami shortów.",
                "7-14s: ekran dzieli się na 'bez planu' vs 'z hookiem i QA'.",
                "14-24s: kot wciska duży przycisk 'QA' i odrzuca słaby tekst.",
                "24-34s: pojawia się poprawiony storyboard: hook, beat, payoff, CTA.",
                "34-42s: payoff: agent nie jest magiczny, tylko pilnuje procesu.",
            ],
            visual_direction=[
                "Cinematic AI cat, original design, no copied channel assets.",
                "Dark server-room palette: navy, orange accents, clean HUD panels.",
                "One active caption layer only; no repeated title blocks.",
                "Fast scene changes every 5-7 seconds.",
            ],
            narration_direction=[
                "Calm but amused narrator.",
                "Short sentences, no corporate tone.",
                "One clear lesson: quality comes from plan + QA, not automation alone.",
            ],
            qa_requirements=[
                "Hook visible and spoken before 2 seconds.",
                "No empty subtitle panels.",
                "No repeated title in header, center and subtitle at once.",
                "Thumbnail headline max 4 words: 'KOT ZROBIŁ QA'.",
            ],
            forbidden_copying=[
                "Do not copy any ranked AI cat short script or character.",
                "Do not imitate exact visuals, voice, captions or title from another channel.",
            ],
        ),
        RecreationBrief(
            brief_id="brief-what-if-local-ai-1870",
            based_on_pattern="what-if-history-pov",
            channel_fit="faceless-ai-pl / automation education",
            original_topic="Co by było, gdyby lokalny agent AI trafił do biura z 1870 roku?",
            hook="Co gdyby AI trafiło do biura z 1870?",
            beat_timeline=[
                "0-2s: old office, one glowing laptop on wooden desk.",
                "2-10s: clerk writes by hand; AI instantly sorts notes locally.",
                "10-20s: first consequence: fewer repeated tasks.",
                "20-30s: second consequence: better memory, but limited power.",
                "30-42s: twist: without approval gates, chaos still wins.",
            ],
            visual_direction=[
                "Sepia office + subtle futuristic blue glow.",
                "No famous historical people or copyrighted worlds.",
                "Use split-screen contrast and animated labels.",
            ],
            narration_direction=[
                "Curious documentary tone.",
                "One fact or implication per scene.",
            ],
            qa_requirements=[
                "Every claim framed as hypothetical.",
                "At least three visual scene changes.",
                "CTA invites comment: 'Które zadanie dałbyś mu pierwsze?'",
            ],
            forbidden_copying=[
                "Do not use real historical likenesses without need.",
                "Do not present fictional scenario as real history.",
            ],
        ),
        RecreationBrief(
            brief_id="brief-money-ai-automation-contrast",
            based_on_pattern="finance-money-contrast",
            channel_fit="faceless-ai-pl / AI productivity monetization",
            original_topic="Różnica między tanim automatem AI a systemem, który naprawdę oszczędza czas.",
            hook="Tani automat AI kosztuje najwięcej.",
            beat_timeline=[
                "0-2s: split screen: cheap bot chaos vs controlled agent dashboard.",
                "2-12s: cost 1: poprawianie błędów i śmieciowych outputów.",
                "12-22s: cost 2: brak QA przed publikacją.",
                "22-34s: better path: plan, render, QA, approval.",
                "34-45s: payoff: automatyzuj proces, nie chaos.",
            ],
            visual_direction=[
                "Clean UI comparison, money/time counters, no fake earnings.",
                "Strong contrast but no get-rich visuals.",
            ],
            narration_direction=[
                "Direct, critical, practical.",
                "No hype, no promise of income.",
            ],
            qa_requirements=[
                "No financial advice or revenue promise.",
                "Use 'oszczędza czas' not 'zarabia gwarantowanie'.",
                "Thumbnail max 4 words: 'AI KOSZTUJE WIĘCEJ'.",
            ],
            forbidden_copying=[
                "Do not copy finance influencer hooks verbatim.",
                "Do not imply guaranteed income.",
            ],
        ),
    ]


def _render_report(payload: dict[str, object]) -> str:
    lines = [
        "# Faceless AI Shorts Learning Session",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- created_at: `{payload['created_at']}`",
        "- status: `recorded`",
        "",
        "## Objective",
        "",
        str(payload["objective"]),
        "",
        "## Direct video viewing status",
        "",
        f"- status: `{payload['direct_video_view_status']['status']}`",
        f"- reason: {payload['direct_video_view_status']['reason']}",
        "",
        "## Sources",
        "",
    ]
    for source in payload["sources"]:
        lines.extend(
            [
                f"### {source['title']}",
                f"- url: {source['url']}",
                f"- evidence_level: `{source['evidence_level']}`",
                f"- useful_for: {', '.join(source['useful_for'])}",
                f"- notes: {source['notes']}",
                "",
            ]
        )
    lines.append("## Pattern cards")
    lines.append("")
    for pattern in payload["pattern_cards"]:
        lines.extend(
            [
                f"### {pattern['name']}",
                f"- pattern_id: `{pattern['pattern_id']}`",
                f"- observed_signal: {pattern['observed_signal']}",
                "",
                "Structure:",
                *[f"- {item}" for item in pattern["structure"]],
                "",
                "Why it works:",
                *[f"- {item}" for item in pattern["why_it_works"]],
                "",
                "Production rules:",
                *[f"- {item}" for item in pattern["production_rules"]],
                "",
                "Risks:",
                *[f"- {item}" for item in pattern["risks"]],
                "",
            ]
        )
    lines.extend(
        [
            "## Production decision",
            "",
            f"- best_next_sample: `{payload['production_decision']['best_next_sample']}`",
            f"- reason: {payload['production_decision']['reason']}",
            "",
            "## Safety boundaries",
            "",
            *[f"- {item}" for item in payload["safety_boundaries"]],
            "",
            "## Next use in Atnos",
            "",
            "- Feed one selected `RecreationBrief` into ShortAgent as the topic/brief.",
            "- Require visual QA to reject repeated title/caption layers and empty subtitle panels.",
            "- Keep upload private/manual until owner approves.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _render_briefs(briefs: list[RecreationBrief]) -> str:
    lines = ["# Faceless AI Shorts Recreation Briefs", ""]
    for brief in briefs:
        lines.extend(
            [
                f"## {brief.brief_id}",
                "",
                f"- based_on_pattern: `{brief.based_on_pattern}`",
                f"- channel_fit: {brief.channel_fit}",
                f"- original_topic: {brief.original_topic}",
                f"- hook: {brief.hook}",
                "",
                "### Beat timeline",
                *[f"- {item}" for item in brief.beat_timeline],
                "",
                "### Visual direction",
                *[f"- {item}" for item in brief.visual_direction],
                "",
                "### Narration direction",
                *[f"- {item}" for item in brief.narration_direction],
                "",
                "### QA requirements",
                *[f"- {item}" for item in brief.qa_requirements],
                "",
                "### Forbidden copying",
                *[f"- {item}" for item in brief.forbidden_copying],
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _write_obsidian_note(*, settings: Settings, run_id: str, report_text: str) -> Path | None:
    vault_path = getattr(settings, "obsidian_vault_path", None)
    export_enabled = bool(getattr(settings, "obsidian_export_enabled", False))
    if not export_enabled or not vault_path:
        return None
    vault = Path(vault_path)
    if not vault.exists():
        return None
    target = vault / "Atnos" / "Shorts" / f"{run_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_text, encoding="utf-8")
    return target


def _append_log(
    *,
    settings: Settings,
    run_id: str,
    report_path: Path,
    json_path: Path,
    obsidian_path: Path | None,
) -> None:
    log_path = settings.data_dir / "logs" / "faceless_shorts_learning.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "event": "faceless_ai_shorts_learning_session_recorded",
                    "run_id": run_id,
                    "report_path": str(report_path),
                    "json_path": str(json_path),
                    "obsidian_path": str(obsidian_path) if obsidian_path else None,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
