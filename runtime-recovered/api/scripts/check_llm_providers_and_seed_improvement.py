from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path

from atnos.core.config import get_settings
from atnos.db.models.enums import (
    ModelTask,
    ProviderPosture,
    SelfImprovementDecision,
    SelfImprovementSeverity,
    SelfImprovementStatus,
)
from atnos.db.session import DatabaseManager
from atnos.providers.registry import build_default_provider_registry
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.repositories.provider_repository import ProviderRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.model_selection_service import ModelSelectionService
from atnos.services.provider_runtime_secret_sync_service import ProviderRuntimeSecretSyncService
from atnos.services.self_improvement_notes_service import SelfImprovementNotesService


QUALITY_TASK_MARKER = "content_quality_ux_self_improvement_v1"


@dataclass(frozen=True, slots=True)
class RoutingCheck:
    task: str
    posture: str
    status: str
    selected: str
    reason: str


def main() -> None:
    settings = get_settings()
    app_root = settings.app_root or Path(os.getenv("ATNOS_ROOT", Path(__file__).resolve().parents[3]))
    report_dir = (app_root / "data" / "reports").resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"atnos_llm_provider_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    db = DatabaseManager(settings)
    registry = build_default_provider_registry(app_root=app_root, settings=settings)

    with db.session_scope() as session:
        sync_results = ProviderRuntimeSecretSyncService(session, settings).sync_known_cloud_providers()
        providers = ProviderRepository(session).list_all()
        models = ModelConfigRepository(session).list_enabled_defaults()

        owner = UserRepository(session).get_owner()
        policy = ExecutionPolicyRepository(session).get_for_user(owner.id) if owner is not None else None
        routing_checks: list[RoutingCheck] = []
        if policy is not None:
            selector = ModelSelectionService(session, provider_registry=registry)
            for posture in (ProviderPosture.IMPROVE_WHEN_NEEDED, ProviderPosture.STRICT_LOCAL):
                for task in (
                    ModelTask.GENERAL_CHAT,
                    ModelTask.SYNTHESIS,
                    ModelTask.CODING,
                    ModelTask.SEARCH_RESEARCH,
                    ModelTask.VISION,
                ):
                    if posture == ProviderPosture.STRICT_LOCAL and task == ModelTask.GENERAL_CHAT:
                        continue
                    try:
                        selected = selector.select_for_task(
                            task=task,
                            execution_policy=policy,
                            provider_posture=posture,
                        )
                        routing_checks.append(
                            RoutingCheck(
                                task=task.value,
                                posture=posture.value,
                                status="PASSED",
                                selected=f"{selected.provider.key}/{selected.model_config.key}",
                                reason=selected.reason,
                            )
                        )
                    except Exception as exc:
                        routing_checks.append(
                            RoutingCheck(
                                task=task.value,
                                posture=posture.value,
                                status="FAILED",
                                selected="-",
                                reason=f"{type(exc).__name__}: {exc}",
                            )
                        )

        note = seed_quality_self_improvement_note(session)

        report_lines = [
            "# Atnos LLM Provider Check",
            "",
            f"- generated_at: {datetime.now().isoformat()}",
            f"- database: {settings.resolved_database_url}",
            f"- self_improvement_note_id: {note.id}",
            "",
            "## Secret Sync",
            "",
            "| provider | env var | status | reason |",
            "|---|---|---|---|",
        ]
        report_lines.extend(
            f"| {item.provider_key} | {item.env_var} | {item.status} | {item.reason} |"
            for item in sync_results
        )

        report_lines.extend(
            [
                "",
                "## Provider Runtime Readiness",
                "",
                "| provider | enabled | adapter | ready | reason |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for provider in providers:
            adapter = registry.get(provider.key)
            readiness = adapter.configuration_status(provider=provider) if adapter else None
            report_lines.append(
                "| {provider} | {enabled} | {adapter} | {ready} | {reason} |".format(
                    provider=provider.key,
                    enabled=str(provider.is_enabled),
                    adapter=str(adapter is not None),
                    ready=str(readiness.ready if readiness is not None else False),
                    reason=(readiness.reason if readiness is not None and readiness.reason else ""),
                )
            )

        report_lines.extend(
            [
                "",
                "## Enabled Default Models",
                "",
                "| task | model | provider | runtime model |",
                "|---|---|---|---|",
            ]
        )
        for model in models:
            report_lines.append(
                f"| {model.task.value} | {model.key} | {model.provider.key} | {model.runtime_model_name or ''} |"
            )

        report_lines.extend(
            [
                "",
                "## Routing Checks",
                "",
                "| posture | task | status | selected | reason |",
                "|---|---|---|---|---|",
            ]
        )
        report_lines.extend(
            f"| {item.posture} | {item.task} | {item.status} | {item.selected} | {item.reason} |"
            for item in routing_checks
        )

        report_lines.extend(
            [
                "",
                "## Self-Improvement Task",
                "",
                f"- marker: `{QUALITY_TASK_MARKER}`",
                f"- note_id: {note.id}",
                f"- status: {note.status.value}",
                f"- severity: {note.severity.value}",
                f"- decision: {note.decision.value}",
                "",
                "Atnos should use this selected note as the next improvement target for better content quality and everyday UX.",
            ]
        )

        report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"report={report_path}")
    print(f"self_improvement_note_id={note.id}")


def seed_quality_self_improvement_note(session):
    service = SelfImprovementNotesService(session)
    existing = next(
        (
            note
            for note in service.list_notes()
            if QUALITY_TASK_MARKER in (note.proposed_fix or "")
            or QUALITY_TASK_MARKER in note.friction
            or QUALITY_TASK_MARKER in (note.chosen_next_patch or "")
        ),
        None,
    )

    friction = (
        f"{QUALITY_TASK_MARKER}: Atnos ma juz routing do wielu LLM, ale codzienna jakosc nadal zalezy "
        "od tego, czy model dobiera sie do zadania i czy po wygenerowaniu contentu istnieje realny quality gate. "
        "Priorytet: mniej ogolnych odpowiedzi, lepszy hook w shortach, czytelny status pracy w Chacie i mniej tarcia "
        "dla uzytkownika przy zwyklym korzystaniu."
    )
    proposed_fix = (
        f"{QUALITY_TASK_MARKER}: Zrobic iteracyjny self-improvement loop dla contentu i UX: "
        "1) po kazdym shorcie zapisz ocene hook/clarity/retention/render, "
        "2) gdy score < 7 wymus repair pass, "
        "3) w Chacie pokazuj realny status pracy i artefakty, "
        "4) routing: zwykly chat lokalnie, content/kod/analiza przez najlepszy dostepny model cloud tylko gdy polityka pozwala, "
        "5) zapisz konkretne obserwacje z uzycia jako notatki self-improvement."
    )
    chosen_next_patch = (
        "Najpierw dopracuj content quality loop: automatyczna diagnoza slabych shortow, repair prompt, "
        "czytelny production_report.md i Chat summary pokazujace co realnie powstalo."
    )

    if existing is None:
        note = service.create_note(
            source="codex-provider-quality-audit",
            view="chat-content-ux",
            friction=friction,
            severity=SelfImprovementSeverity.P1,
            proposed_fix=proposed_fix,
        )
    else:
        note = existing
        note = service.update_triage(
            note.id,
            severity=SelfImprovementSeverity.P1,
            proposed_fix=proposed_fix,
            chosen_next_patch=chosen_next_patch,
            decision=SelfImprovementDecision.FOLLOW_UP,
            status=SelfImprovementStatus.SELECTED,
        )
        return note

    return service.update_triage(
        note.id,
        chosen_next_patch=chosen_next_patch,
        decision=SelfImprovementDecision.FOLLOW_UP,
        status=SelfImprovementStatus.SELECTED,
    )


if __name__ == "__main__":
    main()
