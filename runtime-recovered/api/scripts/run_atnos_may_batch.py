from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


APP_ROOT = Path(r"E:\Atnos\App").resolve()
API_ROOT = APP_ROOT / "runtime-recovered" / "api"
SRC_ROOT = API_ROOT / "src"


def _bootstrap_env() -> None:
    defaults = {
        "APP_ROOT": str(APP_ROOT).replace("\\", "/"),
        "OBSIDIAN_VAULT_PATH": "E:/Atnos/Vaults/obsydian/atnos",
        "DATA_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data",
        "UPLOADS_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/uploads",
        "ACCOUNT_SECRET_VAULT_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/account-secrets",
        "SQLITE_FILE": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/atnos.db",
        "QUARANTINE_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/quarantine",
        "BROWSER_PROFILE_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/browser-profile",
        "BROWSER_DOWNLOADS_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/browser-downloads",
        "BROWSER_TRACE_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/browser-traces",
        "PLAYWRIGHT_BROWSERS_PATH": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/playwright-browsers",
        "WORKSPACE_STAGING_DIR": "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/workspace-staging",
        "ATNOS_DESKTOP_SHELL": "E:/Atnos/App/Atnos Desktop.exe",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)

    os.chdir(API_ROOT)
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))


_bootstrap_env()

from atnos.core.config import Settings  # noqa: E402
from atnos.db.models.enums import (  # noqa: E402
    AttachmentKind,
    AttachmentSource,
    AttachmentStatus,
    MessageRole,
    MessageStatus,
)
from atnos.db.session import DatabaseManager  # noqa: E402
from atnos.repositories.attachment_repository import AttachmentRepository  # noqa: E402
from atnos.repositories.conversation_repository import ConversationRepository  # noqa: E402
from atnos.repositories.execution_run_repository import ExecutionRunRepository  # noqa: E402
from atnos.repositories.message_repository import MessageRepository  # noqa: E402
from atnos.repositories.project_repository import ProjectRepository  # noqa: E402
from atnos.repositories.trust_grant_repository import TrustGrantRepository  # noqa: E402
from atnos.services.creator_ops_service import CreatorOpsService  # noqa: E402
from atnos.services.output_artifact_service import OutputArtifactService  # noqa: E402
from atnos.services.profit_os_service import ProfitOsService  # noqa: E402
from atnos.services.trusted_auto_service import TrustedAutoService  # noqa: E402


@dataclass(frozen=True, slots=True)
class ShortSpec:
    topic: str
    brief: str


@dataclass(frozen=True, slots=True)
class CreatorPersonaSpec:
    name: str
    audience: str
    positioning: str
    monetization_goal: str
    content_boundaries: str
    niche: str


SHORT_SPECS = (
    ShortSpec(
        topic="Macro Fruit Crush",
        brief=(
            "Short ma klikac jak premium sensoryczny YouTube Short z 2026: natychmiastowy payoff, "
            "makro tekstury, silny replay loop, mocne pierwsze 2 sekundy, jeden dominujacy obiekt na kadr."
        ),
    ),
    ShortSpec(
        topic="Frozen Fruit Crack",
        brief=(
            "Short ma uderzac zimnym, czystym crackiem w pierwszych 2 sekundach. Ma byc bardzo glosny "
            "teksturalnie, z lodowym pylem, czystym tlaem i ostrym premium framingiem."
        ),
    ),
    ShortSpec(
        topic="Layered Jelly + Fruit Press Reveal",
        brief=(
            "Short ma zaczynac od dziwnego hipnotycznego kadru, potem powolny nacisk i mokry reveal. "
            "Premium kolory, czysta kompozycja, bez slajdowego looku."
        ),
    ),
)


CREATOR_PERSONAS = (
    CreatorPersonaSpec(
        name="Cozy Tease",
        audience="mezczyzni 21-38, ktorzy wracaja po vibe, miekkosc i premium intimacy mood",
        positioning="cute girlfriend soft tease brand z wysoka retencja i premium nastrojem",
        monetization_goal="stabilny recurring revenue z bundle, PPV i premium retention",
        content_boundaries=(
            "adult-only, legal, consent-based, privacy-safe, non-explicit, no impersonation, "
            "no scammy DMs, no deception"
        ),
        niche="soft luxury girlfriend energy, closeness, late-night comfort tease",
    ),
    CreatorPersonaSpec(
        name="Playful Brat",
        audience="mezczyzni 20-36, ktorzy dobrze reaguja na teasing, zaczepnosc i mocniejsza persone",
        positioning="zaczepna cute girlfriend persona z wyraznym fan pull i ostrzejszym CTA rhythm",
        monetization_goal="zwiekszenie intentu zakupowego i wyzsza konwersja z teaserow na PPV",
        content_boundaries=(
            "adult-only, legal, consent-based, privacy-safe, non-explicit, no coercion, "
            "no fake personal promises"
        ),
        niche="playful brat energy, selective attention, premium teasing tension",
    ),
    CreatorPersonaSpec(
        name="Late-night E-Girl",
        audience="odbiorcy 18-34 lubiacy internetowy, nocny, cozy-gamer vibe i silna identyfikowalna estetyke",
        positioning="nocna e-girl persona z internetowym charakterem, mocna seria contentowa i uzalezniajacym klimatem",
        monetization_goal="spojny fan funnel z teaserow short-form do premium bundles i comeback offers",
        content_boundaries=(
            "adult-only, legal, consent-based, privacy-safe, non-explicit, no impersonation, "
            "no platform policy bypass"
        ),
        niche="night setup mood, gamer comfort, neon spill, late replies and premium atmosphere",
    ),
)


def _now_slug() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d-%H-%M-%S")


def _print_step(message: str) -> None:
    print(f"[ATNOS-BATCH] {message}", flush=True)


def _find_or_create_project(session, *, name: str, description: str | None = None):
    projects = ProjectRepository(session).list_all()
    for item in projects:
        if item.name.strip().lower() == name.strip().lower():
            return item
    return ProjectRepository(session).create(name=name, description=description)


def _find_or_create_conversation(session, *, project_id: int, title: str):
    repository = ConversationRepository(session)
    for item in repository.list_recent(project_id):
        if item.conversation.title.strip().lower() == title.strip().lower():
            return item.conversation
    return repository.create(title=title, project_id=project_id)


def _pick_youtube_trust_grant(session, *, project_id: int):
    grants = TrustGrantRepository(session).list_for_project(project_id)
    preferred = [
        item
        for item in grants
        if item.is_enabled and item.mode == "trusted_auto" and "youtube" in (item.connector_key or "").lower()
    ]
    if preferred:
        return preferred[0]
    fallback = [item for item in grants if item.is_enabled and item.mode == "trusted_auto"]
    if fallback:
        return fallback[0]
    raise RuntimeError("Brak aktywnego trusted_auto trust grant dla shortowego projektu.")


def _write_output_file(
    artifacts: OutputArtifactService,
    *,
    conversation_id: int | None,
    filename: str,
    media_type: str,
    content: str,
) -> dict[str, Any]:
    extension = Path(filename).suffix.lower()
    target = artifacts.create_output_path(conversation_id=conversation_id, extension=extension)
    target.write_text(content, encoding="utf-8")
    descriptor = artifacts.finalize_output(
        absolute_path=target,
        filename=filename,
        media_type=media_type,
    )
    return {
        "filename": descriptor.filename,
        "storage_path": descriptor.storage_path,
        "media_type": descriptor.media_type,
        "file_extension": descriptor.file_extension,
        "size_bytes": descriptor.size_bytes,
    }


def _attachment_kind_for_path(filename: str) -> AttachmentKind:
    extension = Path(filename).suffix.lower()
    if extension in {".md", ".txt"}:
        return AttachmentKind.TEXT
    if extension in {".png", ".jpg", ".jpeg"}:
        return AttachmentKind.IMAGE
    if extension == ".wav":
        return AttachmentKind.AUDIO
    if extension == ".mp4":
        return AttachmentKind.VIDEO
    if extension in {".docx", ".pptx", ".xlsx", ".pdf"}:
        return AttachmentKind.DOCUMENT
    return AttachmentKind.OTHER


def _safe_obsidian_write(settings: Settings, *, relative_path: str, content: str) -> str:
    vault_root = settings.obsidian_vault_path
    if vault_root is None:
        raise RuntimeError("Obsidian vault path is not configured.")
    resolved_root = vault_root.resolve()
    target = (resolved_root / relative_path).resolve()
    target.relative_to(resolved_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target.relative_to(resolved_root).as_posix()


def _append_message_with_attachments(
    session,
    *,
    project_id: int,
    conversation_id: int,
    content: str,
    attachments: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> int:
    message = MessageRepository(session).create(
        conversation_id=conversation_id,
        role=MessageRole.ASSISTANT,
        content=content,
        status=MessageStatus.COMPLETED,
        metadata_json=metadata or {},
    )
    repository = AttachmentRepository(session)
    for item in attachments:
        repository.create(
            filename=str(item["filename"]),
            storage_path=str(item["storage_path"]),
            kind=_attachment_kind_for_path(str(item["filename"])),
            project_id=None,
            conversation_id=conversation_id,
            message_id=message.id,
            source=AttachmentSource.GENERATED,
            status=AttachmentStatus.AVAILABLE,
            media_type=str(item["media_type"]),
            file_extension=str(item["file_extension"]),
            size_bytes=int(item["size_bytes"]),
            metadata_json=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
        )
    return message.id


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _creator_markdown(spec: CreatorPersonaSpec, plan: dict[str, Any], operating_plan: dict[str, Any], calendar: dict[str, Any]) -> str:
    brand_core = plan.get("brand_core", {})
    voice = plan.get("voice_tone_pack", {})
    visual = plan.get("visual_identity", {})
    wardrobe = plan.get("wardrobe_scene_direction", {})
    funnel = plan.get("funnel", {})
    ppv = plan.get("ppv_bundle_strategy", {})
    fan_segmentation = plan.get("fan_segmentation", {})
    return f"""# {spec.name}

## Positioning
- persona: {spec.positioning}
- audience: {spec.audience}
- niche: {spec.niche}
- monetization_goal: {spec.monetization_goal}

## Brand Core
- persona_hook: {brand_core.get("persona_hook", "")}
- emotional_promise: {brand_core.get("emotional_promise", "")}
- business_goal: {brand_core.get("business_goal", "")}

## Bio Pack
{_markdown_list([str(item) for item in plan.get("bio_pack", [])])}

## Voice And Tone
- tone_words: {", ".join(str(item) for item in voice.get("tone_words", []))}
- sentence_style: {voice.get("sentence_style", "")}
- dm_energy: {voice.get("dm_energy", "")}
- brand_rule: {voice.get("brand_rule", "")}

## Visual Identity
- color_palette: {", ".join(str(item) for item in visual.get("color_palette", []))}
- lighting: {", ".join(str(item) for item in visual.get("lighting", []))}
- texture_keywords: {", ".join(str(item) for item in visual.get("texture_keywords", []))}
- camera_language: {", ".join(str(item) for item in visual.get("camera_language", []))}

## Wardrobe And Scenes
- wardrobe:
{_markdown_list([str(item) for item in wardrobe.get("wardrobe", [])])}
- scene_setups:
{_markdown_list([str(item) for item in wardrobe.get("scene_setups", [])])}
- props:
{_markdown_list([str(item) for item in wardrobe.get("props", [])])}

## Funnel
- teaser: {funnel.get("teaser", "")}
- retention_post: {funnel.get("retention_post", "")}
- paywall_trigger: {funnel.get("paywall_trigger", "")}
- comeback_post: {funnel.get("comeback_post", "")}

## Pricing Ladder
{_markdown_list([str(item) for item in plan.get("pricing_ladder", [])])}

## Upsell Ladder
{_markdown_list([str(item) for item in plan.get("upsell_ladder", [])])}

## CTA Library
{_markdown_list([str(item) for item in plan.get("cta_library", [])])}

## PPV And Bundle Strategy
- ppv_angle: {ppv.get("ppv_angle", "")}
- bundle_angle: {ppv.get("bundle_angle", "")}
- cadence_rule: {ppv.get("cadence_rule", "")}

## Fan Segmentation
- new: {fan_segmentation.get("new", "")}
- warm: {fan_segmentation.get("warm", "")}
- high_intent: {fan_segmentation.get("high_intent", "")}
- lapsed: {fan_segmentation.get("lapsed", "")}

## Churn Reduction
{_markdown_list([str(item) for item in plan.get("churn_reduction_playbook", [])])}

## Promo Angles
{_markdown_list([str(item) for item in plan.get("promo_angles", [])])}

## Prompt Pack
{_markdown_list([str(item) for item in plan.get("prompt_pack", [])])}

## Content Ideas
{_markdown_list([str(item) for item in plan.get("content_ideas", [])])}

## 14 Day Calendar
{_markdown_list([f"day {item.get('day')}: {item.get('focus')} -> {item.get('goal')}" for item in plan.get("content_calendar_14_day", [])])}

## Compliance
{_markdown_list([str(item) for item in plan.get("compliance_checklist", [])])}

## Privacy
{_markdown_list([str(item) for item in plan.get("privacy_checklist", [])])}

## Operating Plan Snapshot
- positioning: {operating_plan.get("positioning", "")}
- weekly_cadence: {operating_plan.get("weekly_cadence", "")}
- risks:
{_markdown_list([str(item) for item in operating_plan.get("risks", [])])}
- quality_checklist:
{_markdown_list([str(item) for item in operating_plan.get("quality_checklist", [])])}

## Calendar Preview
{_markdown_list([f"{item.get('planned_date')}: {item.get('title')} [{item.get('status')}]" for item in calendar.get("items", [])])}

## Boundaries
- content_boundaries: {plan.get("content_boundaries", "")}
- safety_notes:
{_markdown_list([str(item) for item in plan.get("safety_notes", [])])}
"""


def _market_markdown(plan: dict[str, Any], operating_plan: dict[str, Any], calendar: dict[str, Any]) -> str:
    risk = plan.get("risk_framework", {})
    journal = plan.get("journal_template", {})
    return f"""# ATNOS Crypto Market Lab

## Market Scope
- market: {plan.get("market", "")}
- strategy_hypothesis: {plan.get("strategy_hypothesis", "")}
- timeframe: {plan.get("timeframe", "")}
- watchlist: {", ".join(str(item) for item in plan.get("watchlist", []))}
- timeframes: {", ".join(str(item) for item in plan.get("timeframes", []))}

## Setup Families
{_markdown_list([str(item) for item in plan.get("setup_families", [])])}

## Data To Collect
{_markdown_list([str(item) for item in plan.get("data_to_collect", [])])}

## Risk Framework
- max_risk_per_trade: {risk.get("max_risk_per_trade", "")}
- max_daily_drawdown: {risk.get("max_daily_drawdown", "")}
- max_weekly_drawdown: {risk.get("max_weekly_drawdown", "")}
- no_averaging_down_without_rule: {risk.get("no_averaging_down_without_rule", "")}
- news_filter: {risk.get("news_filter", "")}

## Paper Trading Playbook
{_markdown_list([str(item) for item in plan.get("paper_trading_playbook", [])])}

## Entry Checklist
{_markdown_list([str(item) for item in plan.get("entry_checklist", [])])}

## Exit Checklist
{_markdown_list([str(item) for item in plan.get("exit_checklist", [])])}

## Journal Template
- thesis: {journal.get("thesis", "")}
- trigger: {journal.get("trigger", "")}
- invalidation: {journal.get("invalidation", "")}
- risk: {journal.get("risk", "")}
- outcome_review: {journal.get("outcome_review", "")}

## Weekly Review Template
{_markdown_list([str(item) for item in plan.get("weekly_review_template", [])])}

## Operating Plan Snapshot
- positioning: {operating_plan.get("positioning", "")}
- weekly_cadence: {operating_plan.get("weekly_cadence", "")}
- risks:
{_markdown_list([str(item) for item in operating_plan.get("risks", [])])}
- quality_checklist:
{_markdown_list([str(item) for item in operating_plan.get("quality_checklist", [])])}

## Calendar Preview
{_markdown_list([f"{item.get('planned_date')}: {item.get('title')} [{item.get('status')}]" for item in calendar.get("items", [])])}

## Safety Notes
{_markdown_list([str(item) for item in plan.get("safety_notes", [])])}
"""


def _shorts_batch_summary(results: list[dict[str, Any]]) -> str:
    lines = ["# ATNOS Shorts Batch", ""]
    for item in results:
        lines.append(f"## {item['topic']}")
        lines.append(f"- run_id: {item['run_id']}")
        lines.append(f"- run_status: {item['run_status']}")
        lines.append(f"- publish_status: {item['publish_status']}")
        lines.append(f"- video: {item['video_path']}")
        lines.append(f"- thumbnail: {item['thumbnail_path']}")
        lines.append(f"- subtitles: {item['subtitles_path']}")
        lines.append(f"- audio: {item['audio_path']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _summarize_completed_short_run(run) -> dict[str, Any] | None:
    output_payload = dict(run.output_json or {})
    if str(output_payload.get("status") or "") != "completed":
        return None
    artifacts_payload = dict(output_payload.get("artifacts") or {})
    video = dict(artifacts_payload.get("video") or {})
    if not video:
        return None
    thumbnail = dict(artifacts_payload.get("thumbnail") or {})
    audio = dict(artifacts_payload.get("audio") or {})
    subtitles = dict(artifacts_payload.get("subtitles") or {})
    return {
        "topic": str(output_payload.get("topic") or run.goal),
        "run_id": run.id,
        "run_status": run.status,
        "publish_status": dict(output_payload.get("publish_result") or {}).get("status"),
        "video_path": video.get("storage_path"),
        "thumbnail_path": thumbnail.get("storage_path"),
        "audio_path": audio.get("storage_path"),
        "subtitles_path": subtitles.get("storage_path"),
    }


def _find_existing_completed_short(session, *, project_id: int, topic: str) -> dict[str, Any] | None:
    for run in ExecutionRunRepository(session).list_runs_for_project(project_id, limit=80):
        if run.run_type != "campaign" or run.status != "done":
            continue
        output_payload = dict(run.output_json or {})
        if str(output_payload.get("topic") or "").strip().lower() != topic.strip().lower():
            continue
        summary = _summarize_completed_short_run(run)
        if summary is not None:
            return summary
    return None


def main() -> int:
    settings = Settings()
    settings.ensure_directories()
    database = DatabaseManager(settings)
    artifacts = OutputArtifactService(settings)
    timestamp = _now_slug()

    _print_step(f"Using database: {settings.sqlite_file}")
    _print_step("Preparing shorts project and conversation.")
    with database.session_scope() as session:
        shorts_project = _find_or_create_project(
            session,
            name="shorty",
            description="ATNOS satisfying shorts production project.",
        )
        shorts_conversation = _find_or_create_conversation(
            session,
            project_id=shorts_project.id,
            title=f"ATNOS batch shorts {timestamp}",
        )
        trust_grant = _pick_youtube_trust_grant(session, project_id=shorts_project.id)
        trusted_auto = TrustedAutoService(session, settings)

        short_results: list[dict[str, Any]] = []
        for index, spec in enumerate(SHORT_SPECS, start=1):
            existing_short = _find_existing_completed_short(
                session,
                project_id=shorts_project.id,
                topic=spec.topic,
            )
            if existing_short is not None:
                _print_step(f"Skipping regenerated render for {spec.topic}; completed run already exists.")
                short_results.append(existing_short)
                continue

            _print_step(f"Running short {index}/3: {spec.topic}")
            result = trusted_auto.run_job_now(
                project_id=shorts_project.id,
                job_type="campaign",
                goal=f"Prepare a premium sensory YouTube Short: {spec.topic}",
                connector_key=trust_grant.connector_key,
                platform_key="youtube",
                account_id=trust_grant.account_id,
                trust_grant_id=trust_grant.id,
                payload_json={
                    "conversation_id": shorts_conversation.id,
                    "topic": spec.topic,
                    "brief": spec.brief,
                    "project_slug": shorts_project.slug,
                    "publish_mode": "trusted_auto",
                    "peer_consultation": True,
                },
                requires_trusted_auto=True,
                execute_now=True,
            )
            run_payload = dict(result.get("run") or {})
            output_payload = dict(run_payload.get("output_json") or {})
            artifacts_payload = dict(output_payload.get("artifacts") or {})
            video = dict(artifacts_payload.get("video") or {})
            thumbnail = dict(artifacts_payload.get("thumbnail") or {})
            audio = dict(artifacts_payload.get("audio") or {})
            subtitles = dict(artifacts_payload.get("subtitles") or {})

            if run_payload.get("status") != "done":
                raise RuntimeError(
                    f"Short run for {spec.topic} did not finish cleanly. Status: {run_payload.get('status')}."
                )
            if not video:
                raise RuntimeError(f"Short run for {spec.topic} did not produce a video artifact.")

            short_results.append(
                {
                    "topic": spec.topic,
                    "run_id": run_payload.get("run_id"),
                    "run_status": run_payload.get("status"),
                    "publish_status": dict(output_payload.get("publish_result") or {}).get("status"),
                    "video_path": video.get("storage_path"),
                    "thumbnail_path": thumbnail.get("storage_path"),
                    "audio_path": audio.get("storage_path"),
                    "subtitles_path": subtitles.get("storage_path"),
                }
            )

        summary_md = _shorts_batch_summary(short_results)
        summary_artifact = _write_output_file(
            artifacts,
            conversation_id=shorts_conversation.id,
            filename="atnos-shorts-batch-summary.md",
            media_type="text/markdown",
            content=summary_md,
        )
        summary_note_path = _safe_obsidian_write(
            settings,
            relative_path=f"Atnos/Social/Batches/{timestamp}-shorts-batch-summary.md",
            content=summary_md,
        )
        _append_message_with_attachments(
            session,
            project_id=shorts_project.id,
            conversation_id=shorts_conversation.id,
            content=(
                "ATNOS zakonczyl batch 3 sensorycznych shortow.\n\n"
                f"- vault_note: {summary_note_path}\n"
                "- Wszystkie shorty zostaly przygotowane jako lokalne pakiety gotowe do recznego uploadu.\n"
                "- Connector YouTube nadal moze byc niegotowy do realnego publish, ale pakiety artefaktow sa gotowe."
            ),
            attachments=[
                {**summary_artifact, "metadata": {"vault_note_path": summary_note_path}},
            ],
            metadata={"vault_note_path": summary_note_path, "batch_type": "shorts"},
        )

    _print_step("Preparing creator monetization packs.")
    with database.session_scope() as session:
        creator_ops = CreatorOpsService(session, settings=settings)
        profit = ProfitOsService()
        creator_results: list[dict[str, Any]] = []
        for spec in CREATOR_PERSONAS:
            _print_step(f"Building creator pack: {spec.name}")
            existing = next((item for item in creator_ops.list_projects() if item.name.lower() == spec.name.lower()), None)
            if existing is None:
                creator_project = creator_ops.create_project(
                    name=spec.name,
                    project_type="creator_monetization",
                    niche=spec.niche,
                    target_audience=spec.audience,
                    monetization_goal=spec.monetization_goal,
                    content_boundaries=spec.content_boundaries,
                    platforms=["onlyfans"],
                    persona_type="brand",
                    disclosure_policy="not_required",
                    operating_mode="planning_only",
                    linked_account_ids=[],
                    notes="ATNOS creator monetization pack generated for a commercial 18+ brand system.",
                    safety_boundaries="No explicit generation. No deceptive DMs. No policy bypass.",
                    status="active",
                    approval_policy="always_require_approval",
                )
            else:
                creator_project = existing

            if creator_project.project_id is None:
                raise RuntimeError(f"Creator project {spec.name} has no canonical project id.")

            conversation = _find_or_create_conversation(
                session,
                project_id=creator_project.project_id,
                title=f"{spec.name} operating pack",
            )
            operating_plan = creator_ops.generate_operating_plan(creator_project.id)
            work_items = creator_ops.generate_work_items(creator_project.id, persist=False)
            calendar = creator_ops.generate_content_calendar_preview(creator_project.id, cadence="three_times_weekly")
            creator_plan = profit.build_creator_monetization_plan(
                platform="onlyfans",
                creator_positioning=spec.positioning,
                audience=spec.audience,
                content_boundaries=spec.content_boundaries,
                monetization_goal=spec.monetization_goal,
                posting_frequency="5x weekly",
                project_id=creator_project.project_id,
            )
            markdown = _creator_markdown(spec, creator_plan, operating_plan or {}, calendar or {"items": []})
            md_artifact = _write_output_file(
                artifacts,
                conversation_id=conversation.id,
                filename=f"{spec.name.lower().replace(' ', '-')}-creator-pack.md",
                media_type="text/markdown",
                content=markdown,
            )
            json_artifact = _write_output_file(
                artifacts,
                conversation_id=conversation.id,
                filename=f"{spec.name.lower().replace(' ', '-')}-creator-pack.json",
                media_type="application/json",
                content=_json_block(
                    {
                        "project": asdict(creator_project),
                        "operating_plan": operating_plan,
                        "work_items": work_items,
                        "calendar_preview": calendar,
                        "creator_plan": creator_plan,
                    }
                ),
            )
            vault_note_path = _safe_obsidian_write(
                settings,
                relative_path=f"Atnos/Business/CreatorPacks/{timestamp}-{spec.name.lower().replace(' ', '-')}.md",
                content=markdown,
            )
            message_id = _append_message_with_attachments(
                session,
                project_id=creator_project.project_id,
                conversation_id=conversation.id,
                content=(
                    f"ATNOS przygotowal creator pack dla {spec.name}.\n\n"
                    f"- vault_note: {vault_note_path}\n"
                    f"- audience: {spec.audience}\n"
                    f"- monetization_goal: {spec.monetization_goal}\n"
                    "- Pakiet jest agresywny komercyjnie, ale nadal non-explicit i compliance-safe."
                ),
                attachments=[
                    {**md_artifact, "metadata": {"vault_note_path": vault_note_path}},
                    json_artifact,
                ],
                metadata={"vault_note_path": vault_note_path, "batch_type": "creator_pack"},
            )
            creator_results.append(
                {
                    "name": spec.name,
                    "creator_project_id": creator_project.id,
                    "canonical_project_id": creator_project.project_id,
                    "conversation_id": conversation.id,
                    "message_id": message_id,
                    "vault_note_path": vault_note_path,
                    "markdown_artifact": md_artifact["storage_path"],
                }
            )

    _print_step("Preparing crypto market lab.")
    with database.session_scope() as session:
        creator_ops = CreatorOpsService(session, settings=settings)
        profit = ProfitOsService()
        existing = next((item for item in creator_ops.list_projects() if item.name.lower() == "atnos crypto market lab"), None)
        if existing is None:
            market_project = creator_ops.create_project(
                name="ATNOS Crypto Market Lab",
                project_type="market_lab",
                niche="trend continuation + range rejection + breakout retest on BTC ETH SOL",
                target_audience="operator paper trading research",
                monetization_goal="paper trading only",
                content_boundaries=None,
                platforms=["tradingview"],
                persona_type="none",
                disclosure_policy="not_required",
                operating_mode="planning_only",
                linked_account_ids=[],
                notes="ATNOS crypto research and paper trading operating pack.",
                safety_boundaries="No broker connection. No execution. No investment advice promises.",
                status="active",
                approval_policy="always_require_approval",
            )
        else:
            market_project = existing

        if market_project.project_id is None:
            raise RuntimeError("Market Lab project has no canonical project id.")

        conversation = _find_or_create_conversation(
            session,
            project_id=market_project.project_id,
            title="ATNOS crypto market lab pack",
        )
        operating_plan = creator_ops.generate_operating_plan(market_project.id)
        work_items = creator_ops.generate_work_items(market_project.id, persist=False)
        calendar = creator_ops.generate_content_calendar_preview(market_project.id, cadence="weekly")
        market_plan = profit.build_market_research_plan(
            market="crypto",
            strategy_hypothesis="Trade only continuation, range rejection and breakout retest setups on BTC ETH SOL after clean invalidation.",
            timeframe="D1/H4/H1",
            risk_budget="0.5-1% per paper trade, no live execution",
            project_id=market_project.project_id,
        )
        markdown = _market_markdown(market_plan, operating_plan or {}, calendar or {"items": []})
        md_artifact = _write_output_file(
            artifacts,
            conversation_id=conversation.id,
            filename="atnos-crypto-market-lab.md",
            media_type="text/markdown",
            content=markdown,
        )
        json_artifact = _write_output_file(
            artifacts,
            conversation_id=conversation.id,
            filename="atnos-crypto-market-lab.json",
            media_type="application/json",
            content=_json_block(
                {
                    "project": asdict(market_project),
                    "operating_plan": operating_plan,
                    "work_items": work_items,
                    "calendar_preview": calendar,
                    "market_plan": market_plan,
                }
            ),
        )
        vault_note_path = _safe_obsidian_write(
            settings,
            relative_path=f"Atnos/Finance/{timestamp}-crypto-market-lab.md",
            content=markdown,
        )
        _append_message_with_attachments(
            session,
            project_id=market_project.project_id,
            conversation_id=conversation.id,
            content=(
                "ATNOS przygotowal crypto market lab pod research i paper trading.\n\n"
                f"- vault_note: {vault_note_path}\n"
                "- Watchlista obejmuje BTC, ETH i SOL.\n"
                "- Zakres pozostaje research-only, bez live tradingu i bez polaczenia z brokerem."
            ),
            attachments=[
                {**md_artifact, "metadata": {"vault_note_path": vault_note_path}},
                json_artifact,
            ],
            metadata={"vault_note_path": vault_note_path, "batch_type": "market_lab"},
        )

    _print_step("Batch completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
