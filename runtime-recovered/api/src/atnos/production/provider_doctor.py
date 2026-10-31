from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads, rows_to_dicts, utc_now_iso
from atnos.production.cost_guard import CostGuard
from atnos.production.money_machine import MoneyMachineStatusService
from atnos.production.public_quality_gate import build_public_quality_gate_status
from atnos.production.visual_backend_status import build_visual_backend_status
from atnos.production.voice_provider_status import build_voice_provider_status


class ProviderDoctorActionError(RuntimeError):
    """Raised when an operator action is unknown or cannot be safely executed."""


@dataclass(frozen=True, slots=True)
class ProviderDoctorCard:
    """Pojedyncza karta diagnostyczna dla warstwy produkcyjnej."""

    key: str
    label: str
    status: str
    summary: str
    next_action: str
    evidence: dict[str, Any]
    critical: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "summary": self.summary,
            "next_action": self.next_action,
            "evidence": self.evidence,
            "critical": self.critical,
        }


@dataclass(frozen=True, slots=True)
class ProviderDoctorAction:
    """Akcja operatorska, którą frontend może pokazać bez zgadywania logiki naprawy."""

    id: str
    card_key: str
    label: str
    kind: str
    method: str | None
    endpoint: str | None
    safe_to_run: bool
    requires_owner_action: bool
    reason: str
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "card_key": self.card_key,
            "label": self.label,
            "kind": self.kind,
            "method": self.method,
            "endpoint": self.endpoint,
            "safe_to_run": self.safe_to_run,
            "requires_owner_action": self.requires_owner_action,
            "reason": self.reason,
            "payload": self.payload or {},
        }


def build_provider_doctor_status(
    settings: Settings,
    *,
    session: Any | None = None,
    account_id: str | None = None,
    include_oauth: bool = True,
) -> dict[str, Any]:
    """Zwraca jeden operatorski snapshot blokad produkcyjnej jakości Shorts."""

    voice = build_voice_provider_status(settings)
    visual = build_visual_backend_status(settings)
    public_quality = build_public_quality_gate_status(settings, account_id=account_id)
    latest_failure = _latest_problem_job(settings, account_id=account_id)
    money_machine = _money_machine_status(
        settings,
        session=session,
        account_id=account_id,
        include_oauth=include_oauth,
    )
    cost_guard = _cost_guard_status(settings, money_machine)

    cards = [
        _voice_card(voice, latest_failure),
        _visual_card(visual, latest_failure),
        _youtube_oauth_card(money_machine, include_oauth=include_oauth),
        _public_upload_card(money_machine, settings),
        _cost_guard_card(cost_guard),
        _latest_failure_card(latest_failure),
    ]
    critical_blockers = [
        card.key
        for card in cards
        if card.critical and card.status == "blocked"
    ]
    warnings = [
        card.key
        for card in cards
        if card.status == "warning"
    ]
    operator_actions = _operator_actions(cards, latest_failure)

    return {
        "generated_at": utc_now_iso(),
        "status": "ready" if not critical_blockers else "needs_attention",
        "account_id": account_id,
        "critical_blockers": critical_blockers,
        "warnings": warnings,
        "cards": [card.to_dict() for card in cards],
        "operator_actions": [action.to_dict() for action in operator_actions],
        "public_quality": {
            "ready": bool(public_quality.get("ready")),
            "blockers": list(public_quality.get("blockers") or []),
            "next_actions": list(public_quality.get("next_actions") or []),
        },
        "money_machine": _compact_money_machine(money_machine),
    }


def provider_doctor_action_by_id(status: dict[str, Any], action_id: str) -> dict[str, Any]:
    """Return an operator action from a Provider Doctor payload by id."""
    for action in status.get("operator_actions") or []:
        if isinstance(action, dict) and str(action.get("id") or "") == action_id:
            return action
    raise ProviderDoctorActionError(f"Unknown Provider Doctor action: {action_id}")


def _operator_actions(
    cards: list[ProviderDoctorCard],
    latest_failure: dict[str, Any] | None,
) -> list[ProviderDoctorAction]:
    actions: list[ProviderDoctorAction] = []
    by_key = {card.key: card for card in cards}
    voice = by_key.get("voice_provider")
    if voice and voice.status != "ready":
        actions.append(
            ProviderDoctorAction(
                id="run_voice_provider_check",
                card_key="voice_provider",
                label="Testuj głos premium",
                kind="frontend_api",
                method="POST",
                endpoint="/api/production/voice-provider/check",
                safe_to_run=True,
                requires_owner_action=True,
                reason=(
                    "Uruchamia mały live probe TTS i zapisuje raport bez publikowania filmu. "
                    "Może wykorzystać limit skonfigurowanego dostawcy chmurowego, dlatego wymaga ręcznego kliknięcia właściciela."
                ),
            )
        )
    visual = by_key.get("visual_backend")
    if visual and visual.status != "ready":
        actions.append(
            ProviderDoctorAction(
                id="run_visual_backend_check",
                card_key="visual_backend",
                label="Testuj backend wideo",
                kind="frontend_api",
                method="POST",
                endpoint="/api/production/visual-backend/check",
                safe_to_run=True,
                requires_owner_action=False,
                reason="Sprawdza ComfyUI/OpenAI visual backend i zapisuje raport bez uploadu.",
            )
        )
    oauth = by_key.get("youtube_oauth")
    if oauth and oauth.status == "blocked":
        actions.extend(
            [
                ProviderDoctorAction(
                    id="open_youtube_oauth_status",
                    card_key="youtube_oauth",
                    label="Sprawdź YouTube OAuth",
                    kind="manual_endpoint",
                    method="GET",
                    endpoint="/oauth/youtube-studio/status",
                    safe_to_run=True,
                    requires_owner_action=False,
                    reason="Pokazuje szczegóły brakujących zgód i mapowania kont YouTube.",
                ),
                ProviderDoctorAction(
                    id="start_youtube_oauth",
                    card_key="youtube_oauth",
                    label="Uruchom zgodę YouTube",
                    kind="manual_endpoint",
                    method="POST",
                    endpoint="/oauth/youtube-studio/start",
                    safe_to_run=True,
                    requires_owner_action=True,
                    reason="Zwraca authorization_url; właściciel musi ręcznie zaakceptować zgody Google.",
                ),
            ]
        )
    public_upload = by_key.get("public_upload")
    if public_upload and public_upload.status == "blocked":
        actions.append(
            ProviderDoctorAction(
                id="review_public_upload_gate",
                card_key="public_upload",
                label="Review public upload gate",
                kind="manual_review",
                method=None,
                endpoint=None,
                safe_to_run=False,
                requires_owner_action=True,
                reason="Publiczny upload powinien zostać włączony dopiero po prywatnych próbkach i QA public-quality.",
            )
        )
    cost_guard = by_key.get("cost_guard")
    if cost_guard and cost_guard.status in {"blocked", "warning"}:
        actions.append(
            ProviderDoctorAction(
                id="review_cost_guard",
                card_key="cost_guard",
                label="Review budżetu API",
                kind="manual_review",
                method=None,
                endpoint=None,
                safe_to_run=False,
                requires_owner_action=True,
                reason="Budżet i wydatki muszą być zatwierdzane przez właściciela; Atnos nie przechowuje danych karty.",
            )
        )
    latest = by_key.get("latest_production_evidence")
    latest_job_id = str((latest_failure or {}).get("id") or "")
    if latest and latest.status == "blocked" and latest_job_id:
        actions.append(
            ProviderDoctorAction(
                id="regenerate_latest_failed_job",
                card_key="latest_production_evidence",
                label="Regeneruj ostatni nieudany job",
                kind="frontend_api",
                method="POST",
                endpoint=f"/api/production/jobs/{latest_job_id}/regenerate",
                safe_to_run=False,
                requires_owner_action=True,
                reason="Regeneracja ma sens dopiero po naprawie czerwonych providerów, inaczej powtórzy fallback.",
                payload={"reason": "provider_doctor_recovery", "run_in_background": True},
            )
        )
    return actions


def _voice_card(voice: dict[str, Any], latest_failure: dict[str, Any] | None) -> ProviderDoctorCard:
    report_stale = bool(voice.get("report_stale"))
    ready = bool(voice.get("public_voice_ready")) and not report_stale
    evidence = {
        "checked": bool(voice.get("checked")),
        "report_stale": report_stale,
        "report_age_hours": voice.get("report_age_hours"),
        "report_max_age_hours": voice.get("report_max_age_hours"),
        "selected_tts_backend": voice.get("selected_tts_backend"),
        "providers": _provider_errors(voice.get("providers")),
        "latest_job_voice": _latest_voice_evidence(latest_failure),
        "report_path": voice.get("report_path"),
    }
    if ready:
        return ProviderDoctorCard(
            key="voice_provider",
            label="Premium voice",
            status="ready",
            summary="Głos spełnia bramkę publicznej jakości.",
            next_action="generate_private_public_quality_short",
            evidence=evidence,
        )
    if report_stale:
        return ProviderDoctorCard(
            key="voice_provider",
            label="Premium voice",
            status="blocked",
            summary="Publiczny upload jest blokowany, bo raport glosu premium jest nieaktualny.",
            next_action="run_voice_provider_doctor",
            evidence=evidence,
        )
    return ProviderDoctorCard(
        key="voice_provider",
        label="Premium voice",
        status="blocked",
        summary="Publiczny upload jest blokowany, bo nie ma gotowego głosu premium.",
        next_action=str(voice.get("next_action") or "run_voice_provider_doctor"),
        evidence=evidence,
    )


def _visual_card(visual: dict[str, Any], latest_failure: dict[str, Any] | None) -> ProviderDoctorCard:
    ready = bool(visual.get("public_visual_ready"))
    evidence = {
        "checked": bool(visual.get("checked")),
        "selected_video_backend": visual.get("selected_video_backend"),
        "effective_video_backend": visual.get("effective_video_backend"),
        "providers": _provider_errors(visual.get("providers")),
        "latest_job_visual": _latest_visual_evidence(latest_failure),
        "report_path": visual.get("report_path"),
    }
    if ready:
        return ProviderDoctorCard(
            key="visual_backend",
            label="Public visual backend",
            status="ready",
            summary="Backend wideo/grafiki spełnia bramkę publicznej jakości.",
            next_action="generate_private_public_quality_short",
            evidence=evidence,
        )
    return ProviderDoctorCard(
        key="visual_backend",
        label="Public visual backend",
        status="blocked",
        summary="Publiczny upload jest blokowany, bo render używa fallbacku albo backend wideo nie jest gotowy.",
        next_action=str(visual.get("next_action") or "run_visual_backend_doctor"),
        evidence=evidence,
    )


def _youtube_oauth_card(money_machine: dict[str, Any], *, include_oauth: bool) -> ProviderDoctorCard:
    accounts = money_machine.get("accounts") if isinstance(money_machine.get("accounts"), list) else []
    if not include_oauth:
        return ProviderDoctorCard(
            key="youtube_oauth",
            label="YouTube OAuth",
            status="unknown",
            summary="OAuth nie był sprawdzany w tym odczycie.",
            next_action="rerun_provider_doctor_with_oauth",
            evidence={"include_oauth": False},
            critical=False,
        )
    if not accounts:
        return ProviderDoctorCard(
            key="youtube_oauth",
            label="YouTube OAuth",
            status="warning",
            summary="Brak kont produkcyjnych do sprawdzenia OAuth.",
            next_action="configure_youtube_content_accounts",
            evidence={"accounts": []},
            critical=False,
        )
    blocked = [
        {
            "account_id": str(account.get("account_id") or ""),
            "channel_title": account.get("channel_title"),
            "next_action": account.get("next_action"),
            "blockers": list(account.get("blockers") or []),
        }
        for account in accounts
        if not bool((account.get("youtube_oauth") or {}).get("ready"))
    ]
    if not blocked:
        return ProviderDoctorCard(
            key="youtube_oauth",
            label="YouTube OAuth",
            status="ready",
            summary="Połączenie YouTube jest gotowe dla sprawdzonych kont.",
            next_action="private_upload_after_qa",
            evidence={"checked_accounts": len(accounts)},
        )
    return ProviderDoctorCard(
        key="youtube_oauth",
        label="YouTube OAuth",
        status="blocked",
        summary="Co najmniej jedno konto nie ma gotowego OAuth do uploadu prywatnego.",
        next_action="connect_youtube_oauth_for_blocked_accounts",
        evidence={"blocked_accounts": blocked},
    )


def _public_upload_card(money_machine: dict[str, Any], settings: Settings) -> ProviderDoctorCard:
    enabled = bool(money_machine.get("public_upload_enabled", settings.allow_public_upload))
    human_gate = bool(money_machine.get("human_approval_before_upload", settings.shorts_require_human_approval_before_upload))
    evidence = {
        "allow_public_upload": enabled,
        "human_approval_before_upload": human_gate,
    }
    if enabled:
        return ProviderDoctorCard(
            key="public_upload",
            label="Public upload gate",
            status="ready",
            summary="Publiczny upload może być użyty po QA i wymaganej akceptacji.",
            next_action="review_private_uploads_then_schedule_public",
            evidence=evidence,
            critical=False,
        )
    return ProviderDoctorCard(
        key="public_upload",
        label="Public upload gate",
        status="blocked",
        summary="ALLOW_PUBLIC_UPLOAD jest wyłączone. To poprawna blokada do czasu przejścia próbek jakości.",
        next_action="enable_allow_public_upload_only_after_owner_review",
        evidence=evidence,
    )


def _cost_guard_card(cost_guard: dict[str, Any]) -> ProviderDoctorCard:
    blocked = bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False
    warning = bool(cost_guard.get("warning"))
    if blocked:
        status = "blocked"
        summary = "Budżet płatnych API blokuje kolejne płatne operacje."
        next_action = "increase_or_reset_budget_after_owner_review"
    elif warning:
        status = "warning"
        summary = "Budżet płatnych API jest blisko limitu."
        next_action = "review_budget_before_next_paid_generation"
    else:
        status = "ready"
        summary = "Lokalny cost guard pozwala na zaplanowane operacje."
        next_action = "continue_with_cost_guard_enabled"
    return ProviderDoctorCard(
        key="cost_guard",
        label="Cost guard",
        status=status,
        summary=summary,
        next_action=next_action,
        evidence={
            "enabled": cost_guard.get("enabled"),
            "allowed": cost_guard.get("allowed"),
            "blocked": cost_guard.get("blocked"),
            "warning": cost_guard.get("warning"),
            "monthly_spent_pln": cost_guard.get("monthly_spent_pln"),
            "monthly_limit_pln": cost_guard.get("monthly_limit_pln"),
            "monthly_remaining_pln": cost_guard.get("monthly_remaining_pln"),
            "daily_spent_pln": cost_guard.get("daily_spent_pln"),
            "daily_limit_pln": cost_guard.get("daily_limit_pln"),
            "daily_remaining_pln": cost_guard.get("daily_remaining_pln"),
        },
        critical=blocked,
    )


def _latest_failure_card(latest_failure: dict[str, Any] | None) -> ProviderDoctorCard:
    if not latest_failure:
        return ProviderDoctorCard(
            key="latest_production_evidence",
            label="Latest production evidence",
            status="unknown",
            summary="Brak ostatniego błędu produkcji do pokazania.",
            next_action="generate_private_sample_to_collect_evidence",
            evidence={},
            critical=False,
        )
    blockers = _latest_failure_blockers(latest_failure)
    status = "blocked" if blockers else "warning"
    return ProviderDoctorCard(
        key="latest_production_evidence",
        label="Latest production evidence",
        status=status,
        summary="Ostatni problem produkcji został podpięty jako dowód diagnostyczny.",
        next_action=_latest_failure_next_action(blockers),
        evidence={
            "job_id": latest_failure.get("id"),
            "account_id": latest_failure.get("account_id"),
            "status": latest_failure.get("status"),
            "topic_id": latest_failure.get("topic_id"),
            "current_step": latest_failure.get("current_step"),
            "updated_at": latest_failure.get("updated_at"),
            "blockers": blockers,
            "qa_blocking_failures": list((latest_failure.get("qa_report") or {}).get("blocking_failures") or []),
            "artifact_dir": latest_failure.get("artifact_dir"),
            "qa_report_path": latest_failure.get("qa_report_path"),
        },
        critical=bool(blockers),
    )


def _money_machine_status(
    settings: Settings,
    *,
    session: Any | None,
    account_id: str | None,
    include_oauth: bool,
) -> dict[str, Any]:
    if session is None:
        return {
            "status": "not_checked",
            "accounts": [],
            "public_upload_enabled": bool(settings.allow_public_upload),
            "human_approval_before_upload": bool(settings.shorts_require_human_approval_before_upload),
            "cost_guard": CostGuard(settings).snapshot(),
            "global_blockers": [],
            "next_actions": [],
        }
    return MoneyMachineStatusService(settings, session=session).build(
        account_id=account_id,
        include_oauth=include_oauth,
    )


def _cost_guard_status(settings: Settings, money_machine: dict[str, Any]) -> dict[str, Any]:
    from_status = money_machine.get("cost_guard") if isinstance(money_machine.get("cost_guard"), dict) else None
    if from_status:
        return from_status
    return CostGuard(settings).snapshot()


def _compact_money_machine(money_machine: dict[str, Any]) -> dict[str, Any]:
    accounts = money_machine.get("accounts") if isinstance(money_machine.get("accounts"), list) else []
    totals = money_machine.get("totals") if isinstance(money_machine.get("totals"), dict) else {}
    return {
        "status": money_machine.get("status"),
        "public_upload_enabled": bool(money_machine.get("public_upload_enabled")),
        "human_approval_before_upload": bool(money_machine.get("human_approval_before_upload")),
        "global_blockers": list(money_machine.get("global_blockers") or []),
        "next_actions": list(money_machine.get("next_actions") or []),
        "totals": totals,
        "accounts": [
            {
                "account_id": str(account.get("account_id") or ""),
                "channel_title": account.get("channel_title"),
                "oauth_ready": bool((account.get("youtube_oauth") or {}).get("ready")),
                "blockers": list(account.get("blockers") or []),
                "next_action": account.get("next_action"),
            }
            for account in accounts
        ],
    }


def _provider_errors(providers: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(providers, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        error = provider.get("error")
        status = provider.get("status")
        status_code = provider.get("status_code")
        live_ready = bool(provider.get("live_ready"))
        if error or status or status_code is not None or not live_ready:
            result[str(name)] = {
                "configured": bool(provider.get("configured")),
                "live_checked": bool(provider.get("live_checked")),
                "live_ready": live_ready,
                "status": status,
                "status_code": status_code,
                "error": error,
            }
    return result


def _latest_problem_job(settings: Settings, *, account_id: str | None) -> dict[str, Any] | None:
    where = "WHERE status NOT IN ('qa_passed', 'uploaded_private', 'published_public', 'done')"
    params: list[Any] = []
    if account_id:
        where += " AND account_id = ?"
        params.append(account_id)
    with connect(settings) as db:
        rows = db.execute(
            f"""
            SELECT
                id, account_id, topic_id, status, current_step, audio_timestamps,
                video_manifest, qa_report, error, artifact_dir, qa_report_path,
                upload_package_path, created_at, updated_at
            FROM pipeline_jobs
            {where}
            ORDER BY COALESCE(updated_at, created_at, '') DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchall()
    if not rows:
        return None
    row = rows_to_dicts(rows)[0]
    for key in ("audio_timestamps", "video_manifest", "qa_report"):
        parsed = json_loads(str(row.get(key) or ""), {})
        row[key] = parsed if isinstance(parsed, dict) else {}
    return row


def _latest_voice_evidence(latest_failure: dict[str, Any] | None) -> dict[str, Any]:
    if not latest_failure:
        return {}
    audio = latest_failure.get("audio_timestamps") if isinstance(latest_failure.get("audio_timestamps"), dict) else {}
    engines = audio.get("engines") if isinstance(audio.get("engines"), list) else []
    return {
        "quality_tier": audio.get("quality_tier"),
        "voice_backend_grade": audio.get("voice_backend_grade"),
        "engines": engines[:5],
        "backend": audio.get("backend"),
    }


def _latest_visual_evidence(latest_failure: dict[str, Any] | None) -> dict[str, Any]:
    if not latest_failure:
        return {}
    manifest = latest_failure.get("video_manifest") if isinstance(latest_failure.get("video_manifest"), dict) else {}
    clips = manifest.get("clips") if isinstance(manifest.get("clips"), list) else []
    return {
        "backend": manifest.get("backend"),
        "fallback_used": manifest.get("fallback_used"),
        "clip_backends": [str(clip.get("backend") or "") for clip in clips if isinstance(clip, dict)][:8],
        "reason": manifest.get("reason"),
    }


def _latest_failure_blockers(latest_failure: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    audio = latest_failure.get("audio_timestamps") if isinstance(latest_failure.get("audio_timestamps"), dict) else {}
    manifest = latest_failure.get("video_manifest") if isinstance(latest_failure.get("video_manifest"), dict) else {}
    qa = latest_failure.get("qa_report") if isinstance(latest_failure.get("qa_report"), dict) else {}
    if str(audio.get("quality_tier") or "").casefold() in {"fallback", "local_neural_fallback"}:
        blockers.append("latest_job_voice_not_public_quality")
    if str(audio.get("voice_backend_grade") or "").casefold() not in {"premium", "public_quality"}:
        blockers.append("latest_job_voice_backend_not_premium")
    if bool(manifest.get("fallback_used")):
        blockers.append("latest_job_visual_fallback_used")
    clip_backends = {
        str(clip.get("backend") or "").casefold()
        for clip in (manifest.get("clips") if isinstance(manifest.get("clips"), list) else [])
        if isinstance(clip, dict)
    }
    if "motion_graphic" in clip_backends or "placeholder" in clip_backends:
        blockers.append("latest_job_visual_backend_not_public_quality")
    if qa and not bool(qa.get("auto_uploadable")):
        blockers.append("latest_job_qa_not_uploadable")
    if str(latest_failure.get("status") or "").casefold() in {"qa_failed", "error", "failed"}:
        blockers.append("latest_job_failed")
    return list(dict.fromkeys(blockers))


def _latest_failure_next_action(blockers: list[str]) -> str:
    blocker_set = set(blockers)
    if any("voice" in blocker for blocker in blocker_set):
        return "regenerate_with_premium_voice_after_provider_fix"
    if any("visual" in blocker for blocker in blocker_set):
        return "regenerate_with_public_visual_backend_after_provider_fix"
    if any("qa" in blocker for blocker in blocker_set):
        return "rerun_qa_after_artifact_fix"
    return "review_latest_job_report"
