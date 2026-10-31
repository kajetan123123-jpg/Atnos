from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration


@dataclass(frozen=True, slots=True)
class SyntheticReleasePack:
    platform_key: str
    connector_key: str
    publish_mode: str
    publish_path: str
    connector_status: dict[str, object]
    publish_result_preview: dict[str, object]
    analytics_followup: dict[str, object]
    metadata: dict[str, object]
    retention_hypothesis: tuple[str, ...]
    upload_checklist: tuple[str, ...]
    approval_package: dict[str, object]
    quality_notes: tuple[str, ...]
    generated_at: str
    recommended_variant_index: int | None
    best_score: float | None
    best_confidence: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "platform_key": self.platform_key,
            "connector_key": self.connector_key,
            "publish_mode": self.publish_mode,
            "publish_path": self.publish_path,
            "connector_status": dict(self.connector_status),
            "publish_result_preview": dict(self.publish_result_preview),
            "analytics_followup": dict(self.analytics_followup),
            "metadata": dict(self.metadata),
            "retention_hypothesis": list(self.retention_hypothesis),
            "upload_checklist": list(self.upload_checklist),
            "approval_package": dict(self.approval_package),
            "quality_notes": list(self.quality_notes),
            "generated_at": self.generated_at,
            "recommended_variant_index": self.recommended_variant_index,
            "best_score": self.best_score,
            "best_confidence": self.best_confidence,
        }


class SyntheticReleasePackService:
    TITLE_SUFFIX_BY_REEL: dict[str, str] = {
        "talking_hook": "szybki hook",
        "micro_story": "mikro historia",
        "cta_teaser": "teaser",
        "behind_the_scene": "kulisy",
    }

    STYLE_HASHTAGS: dict[str, tuple[str, ...]] = {
        "editorial_premium": ("editorial", "premiumlook"),
        "cozy_girlfriend": ("cozyvibes", "softlook"),
        "late_night_egirl": ("latenight", "internetmood"),
        "energetic_expert": ("highenergy", "creatorflow"),
    }

    REEL_HASHTAGS: dict[str, tuple[str, ...]] = {
        "talking_hook": ("hook", "verticalvideo"),
        "micro_story": ("microstory", "storyreel"),
        "cta_teaser": ("teaser", "verticalvideo"),
        "behind_the_scene": ("behindthescenes", "creatorprocess"),
    }

    def build_release_pack(
        self,
        *,
        project_name: str,
        preview: dict[str, Any],
        selection: dict[str, object] | None,
        score_payload: dict[str, object] | None,
        connector_status: dict[str, object] | None,
        analytics_learning_profile: SyntheticAnalyticsCalibration | dict[str, Any] | None = None,
        project_strategy_profile: dict[str, Any] | None = None,
    ) -> SyntheticReleasePack:
        calibration = self._coerce_calibration(analytics_learning_profile)
        strategy_profile = self._coerce_project_strategy_profile(project_strategy_profile)
        script = dict(preview.get("script_bundle") or {})
        style = dict(preview.get("style") or {})
        reel = dict(preview.get("reel_type") or {})
        artifacts = dict(preview.get("artifacts") or {})
        motion_reel = dict(artifacts.get("motion_reel") or {})
        motion_descriptor = dict(motion_reel.get("descriptor") or {})
        qa = dict(motion_reel.get("qa") or {})
        portrait = dict(artifacts.get("portrait") or {})
        audio = dict(artifacts.get("audio") or {})
        subtitles = dict(artifacts.get("subtitles") or {})
        report = dict(artifacts.get("report") or {})
        cutaways = dict(artifacts.get("cutaways") or {})
        cutaway_assets = list(cutaways.get("assets") or [])

        hook = self._clean_text(script.get("hook"))
        body_lines = [self._clean_text(item) for item in list(script.get("body") or []) if self._clean_text(item)]
        base_title = self._clean_text(script.get("title")) or hook or project_name
        style_key = self._clean_text(style.get("key"))
        style_label = self._clean_text(style.get("label")) or style_key or "Unknown style"
        reel_key = self._clean_text(reel.get("key"))
        reel_label = self._clean_text(reel.get("label")) or reel_key or "Unknown reel"
        connector = dict(connector_status or {})
        connector_ready = bool(connector.get("ready"))
        publish_path = "official_youtube_ready" if connector_ready else "manual_upload_ready"
        historical_feedback, historical_strengths, historical_risks = (
            calibration.feedback_for(style_key=style_key or None, reel_key=reel_key or None)
            if calibration.sample_count > 0
            else (0.0, (), ())
        )

        title_options = self._build_title_options(
            base_title=base_title,
            hook=hook,
            style_label=style_label,
            reel_key=reel_key,
            prefer_hook_first=(
                historical_feedback <= -2.0
                or calibration.strongest_title_strategy in {"hook_first_title", "test_hook_first_title"}
                or self._project_prefers_hook_first(strategy_profile)
            ),
        )
        hashtags = self._build_hashtags(style_key=style_key, reel_key=reel_key)
        tags = self._build_tags(project_name=project_name, style_key=style_key, reel_key=reel_key)
        project_strategy_policy = self._build_project_strategy_policy(strategy_profile)
        description = self._build_description(
            title=title_options[0],
            hook=hook,
            body_lines=body_lines,
            style_label=style_label,
            reel_label=reel_label,
            hashtags=hashtags,
            duration_sec=float(qa.get("duration_sec") or 0.0),
            historical_feedback=historical_feedback,
            project_strategy_policy=project_strategy_policy,
        )
        pinned_comment = self._build_pinned_comment(style_label=style_label, reel_label=reel_label)
        retention_hypothesis = self._build_retention_hypothesis(
            hook=hook,
            duration_sec=float(qa.get("duration_sec") or 0.0),
            reel_key=reel_key,
            cutaways_count=len(cutaway_assets),
            historical_feedback=historical_feedback,
            strongest_style_key=calibration.strongest_style_key,
            strongest_reel_key=calibration.strongest_reel_key,
        )
        upload_checklist = self._build_upload_checklist(
            duration_sec=float(qa.get("duration_sec") or 0.0),
            warnings=list(qa.get("warnings") or []),
            connector_ready=connector_ready,
            historical_feedback=historical_feedback,
            project_strategy_policy=project_strategy_policy,
        )
        publish_result_preview = self._build_publish_result_preview(connector_status=connector)
        analytics_followup = self._build_analytics_followup(connector_status=connector)
        historical_playbook = self._build_historical_playbook(
            calibration=calibration,
            historical_feedback=historical_feedback,
            style_key=style_key or None,
            reel_key=reel_key or None,
        )

        best_score = self._resolve_float((score_payload or {}).get("score"))
        best_confidence = self._clean_text((score_payload or {}).get("confidence")) or None
        risks = [self._clean_text(item) for item in list((score_payload or {}).get("risks") or []) if self._clean_text(item)]
        quality_notes = tuple(
            [*historical_risks, *risks][:3]
            or [self._clean_text(item) for item in list(qa.get("warnings") or []) if self._clean_text(item)][:3]
            or ("Brak krytycznych ostrzezen technicznych.",)
        )

        assets = {
            "video": motion_descriptor,
            "thumbnail_candidate": portrait,
            "subtitles": subtitles,
            "narration": audio,
            "report": report,
            "cutaways": {
                "count": len(cutaway_assets),
                "engine": cutaways.get("engine"),
            },
        }
        metadata = {
            "title": title_options[0],
            "title_options": list(title_options),
            "description": description,
            "tags": list(tags),
            "hashtags": [f"#{item}" for item in hashtags],
            "pinned_comment": pinned_comment,
            "privacy_status": "private",
            "style": style_label,
            "reel_type": reel_label,
            "publish_path": publish_path,
            "historical_playbook": list(historical_playbook),
            "project_strategy_policy": list(project_strategy_policy),
        }
        approval_summary = (
            f"Tytul: {title_options[0]}\n"
            f"Hook: {hook or 'brak'}\n"
            f"Styl: {style_label}\n"
            f"Reel: {reel_label}\n"
            f"Dlugosc: {self._format_duration(self._resolve_float(qa.get('duration_sec')))}\n"
            f"Score: {best_score if best_score is not None else 'brak'}\n"
            f"History feedback: {historical_feedback if calibration.sample_count > 0 else 'brak'}\n"
            f"Project strategy policy: {project_strategy_policy[0] if project_strategy_policy else 'brak'}\n"
            f"Publish path: {publish_path}"
        ).strip()
        approval_package = {
            "title": title_options[0],
            "connector_key": "youtube-studio",
            "platform_key": "youtube",
            "summary": approval_summary,
            "artifacts": assets,
            "metadata": metadata,
            "render_qa": qa,
            "publish_mode": "approval_required",
            "publish_result_preview": publish_result_preview,
            "connector_status": connector,
            "analytics_followup": analytics_followup,
            "historical_playbook": list(historical_playbook),
            "project_strategy_policy": list(project_strategy_policy),
            "reason": (
                "Synthetic creator release pack requires explicit human approval before official YouTube upload."
                if connector_ready
                else "Synthetic creator release pack is ready for manual upload while official YouTube connector is not ready."
            ),
        }

        return SyntheticReleasePack(
            platform_key="youtube",
            connector_key="youtube-studio",
            publish_mode="approval_required",
            publish_path=publish_path,
            connector_status=connector,
            publish_result_preview=publish_result_preview,
            analytics_followup=analytics_followup,
            metadata=metadata,
            retention_hypothesis=retention_hypothesis,
            upload_checklist=upload_checklist,
            approval_package=approval_package,
            quality_notes=quality_notes,
            generated_at=datetime.now(tz=UTC).isoformat(),
            recommended_variant_index=self._resolve_int((selection or {}).get("best_variant_index")),
            best_score=best_score,
            best_confidence=best_confidence,
        )

    def build_markdown(self, pack: SyntheticReleasePack) -> str:
        metadata = dict(pack.metadata)
        approval = dict(pack.approval_package)
        assets = dict(approval.get("artifacts") or {})
        video = dict(assets.get("video") or {})
        thumbnail = dict(assets.get("thumbnail_candidate") or {})
        subtitles = dict(assets.get("subtitles") or {})
        connector = dict(pack.connector_status)
        publish_result_preview = dict(pack.publish_result_preview)
        analytics_followup = dict(pack.analytics_followup)
        lines = [
            f"# Synthetic Release Pack - {metadata.get('title') or 'ATNOS release pack'}",
            "",
            f"- platform: {pack.platform_key}",
            f"- connector_key: {pack.connector_key}",
            f"- publish_mode: {pack.publish_mode}",
            f"- publish_path: {pack.publish_path}",
            f"- connector_ready: {connector.get('ready')}",
            f"- connector_channel: {connector.get('channel_title') or 'brak'}",
            f"- recommended_variant_index: {pack.recommended_variant_index if pack.recommended_variant_index is not None else 'brak'}",
            f"- best_score: {pack.best_score if pack.best_score is not None else 'brak'}",
            f"- best_confidence: {pack.best_confidence or 'brak'}",
            "",
            "## Metadata",
            "",
            f"- title: {metadata.get('title') or 'brak'}",
            f"- title_options: {', '.join(str(item) for item in list(metadata.get('title_options') or [])) or 'brak'}",
            f"- tags: {', '.join(str(item) for item in list(metadata.get('tags') or [])) or 'brak'}",
            f"- hashtags: {' '.join(str(item) for item in list(metadata.get('hashtags') or [])) or 'brak'}",
            f"- pinned_comment: {metadata.get('pinned_comment') or 'brak'}",
            "",
            "## Description",
            "",
            str(metadata.get("description") or "brak"),
            "",
            "## Assets",
            "",
            f"- video: {video.get('storage_path') or 'brak'}",
            f"- thumbnail_candidate: {thumbnail.get('storage_path') or 'brak'}",
            f"- subtitles: {subtitles.get('storage_path') or 'brak'}",
            "",
            "## Upload checklist",
            "",
            *[f"- {item}" for item in pack.upload_checklist],
            "",
            "## Publish readiness",
            "",
            f"- status: {publish_result_preview.get('status') or 'brak'}",
            f"- reason: {publish_result_preview.get('reason') or 'brak'}",
            f"- message: {publish_result_preview.get('message') or 'brak'}",
            "",
            "## Analytics follow-up",
            "",
            f"- status: {analytics_followup.get('status') or 'brak'}",
            f"- trigger: {analytics_followup.get('trigger') or 'brak'}",
            f"- message: {analytics_followup.get('message') or 'brak'}",
            "",
            "## Retention hypothesis",
            "",
            *[f"- {item}" for item in pack.retention_hypothesis],
            "",
            "## Historical playbook",
            "",
            *[f"- {item}" for item in list(metadata.get("historical_playbook") or [])],
            "",
            "## Project strategy policy",
            "",
            *[f"- {item}" for item in list(metadata.get("project_strategy_policy") or [])],
            "",
            "## Quality notes",
            "",
            *[f"- {item}" for item in pack.quality_notes],
            "",
            "## Approval summary",
            "",
            str(approval.get("summary") or "brak"),
            "",
        ]
        return "\n".join(lines).strip() + "\n"

    def _build_title_options(
        self,
        *,
        base_title: str,
        hook: str,
        style_label: str,
        reel_key: str,
        prefer_hook_first: bool,
    ) -> tuple[str, ...]:
        hook_fragment = self._clip_text(hook or base_title, limit=54)
        suffix = self.TITLE_SUFFIX_BY_REEL.get(reel_key, "short")
        candidates = [
            self._clip_text(base_title, limit=70),
            self._clip_text(f"{hook_fragment} | {suffix}", limit=70),
            self._clip_text(f"{style_label}: {hook_fragment}", limit=70),
        ]
        if prefer_hook_first:
            candidates = [candidates[1], candidates[0], candidates[2]]
        unique: list[str] = []
        for item in candidates:
            cleaned = item.strip(" -|")
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        return tuple(unique[:3] or [self._clip_text(base_title or "ATNOS reel", limit=70)])

    def _build_description(
        self,
        *,
        title: str,
        hook: str,
        body_lines: list[str],
        style_label: str,
        reel_label: str,
        hashtags: tuple[str, ...],
        duration_sec: float,
        historical_feedback: float,
        project_strategy_policy: tuple[str, ...],
    ) -> str:
        lines = [
            title,
            "",
            hook,
            *body_lines[:3],
            "",
            f"Styl: {style_label}",
            f"Format: {reel_label}",
            f"Dlugosc: {self._format_duration(duration_sec)}",
            (
                "Historycznie ten format dowozil slabsze retention, wiec ten upload testuje mocniejszy pierwszy payoff."
                if historical_feedback <= -2.0
                else (
                    "Historycznie ten format dowozil lepsze retention, wiec utrzymujemy jego mocny pacing."
                    if historical_feedback >= 2.0
                    else ""
                )
            ),
            project_strategy_policy[0] if project_strategy_policy else "",
            "",
            "Jesli chcesz kolejny wariant albo mocniejszy montaz, daj follow i napisz ktory styl mam dopchnac dalej.",
            "",
            "Wyprodukowane lokalnie przez ATNOS.",
            " ".join(f"#{item}" for item in hashtags),
        ]
        return "\n".join(item for item in lines if item is not None).strip()

    def _build_hashtags(self, *, style_key: str, reel_key: str) -> tuple[str, ...]:
        raw = ["shorts", "verticalvideo", "creator"]
        raw.extend(self.STYLE_HASHTAGS.get(style_key, ()))
        raw.extend(self.REEL_HASHTAGS.get(reel_key, ()))
        unique: list[str] = []
        for item in raw:
            normalized = self._normalize_token(item)
            if normalized and normalized not in unique:
                unique.append(normalized)
        return tuple(unique[:6])

    def _build_tags(self, *, project_name: str, style_key: str, reel_key: str) -> tuple[str, ...]:
        raw = ["atnos", "shorts", "synthetic_creator", style_key, reel_key]
        raw.extend(project_name.split())
        unique: list[str] = []
        for item in raw:
            normalized = self._normalize_token(item).replace("-", "_")
            if normalized and normalized not in unique:
                unique.append(normalized[:30])
        return tuple(unique[:12])

    @staticmethod
    def _build_pinned_comment(*, style_label: str, reel_label: str) -> str:
        return (
            f"Ten wariant leci w klimacie {style_label}. "
            f"Jesli chcesz kolejny {reel_label.lower()} albo mocniejszy cutaway edit, napisz `next`."
        )

    def _build_retention_hypothesis(
        self,
        *,
        hook: str,
        duration_sec: float,
        reel_key: str,
        cutaways_count: int,
        historical_feedback: float,
        strongest_style_key: str | None,
        strongest_reel_key: str | None,
    ) -> tuple[str, ...]:
        notes: list[str] = []
        hook_words = len([item for item in hook.split() if item.strip()])
        if hook_words <= 12:
            notes.append("Hook jest wystarczajaco krotki, zeby wejsc szybko w pierwsze sekundy.")
        else:
            notes.append("Hook nadal jest ciezszy niz idealny, ale powinien utrzymac uwage przy mocnym pierwszym kadrze.")
        target = {
            "talking_hook": (12.0, 18.0),
            "micro_story": (18.0, 24.0),
            "cta_teaser": (14.0, 20.0),
            "behind_the_scene": (16.0, 22.0),
        }.get(reel_key, (14.0, 22.0))
        if target[0] <= duration_sec <= target[1]:
            notes.append("Dlugosc rolki miesci sie w dobrym oknie pod retention proxy dla tego typu formatu.")
        else:
            notes.append("Dlugosc odbiega od idealnego okna, wiec warto monitorowac retention po publikacji.")
        if cutaways_count >= 2:
            notes.append("Cutawaye powinny podtrzymac rytm i zmniejszyc zmeczenie jednym kadrem.")
        else:
            notes.append("Rolka ma za malo wspierajacych cutawayow, co moze splaszczyc pacing.")
        if historical_feedback <= -2.0:
            notes.append("Historyczne analytics sugeruja, ze ten format potrzebuje ostrzejszego wejscia i szybszego payoffu.")
        elif historical_feedback >= 2.0:
            notes.append("Historyczne analytics wspieraja ten styl lub typ rolki, wiec warto zachowac ten pacing.")
        elif strongest_style_key or strongest_reel_key:
            notes.append(
                f"Porownaj wynik z historycznym liderem: {strongest_style_key or 'brak stylu'} / {strongest_reel_key or 'brak typu'}."
            )
        return tuple(notes[:3])

    @staticmethod
    def _build_upload_checklist(
        *,
        duration_sec: float,
        warnings: list[str],
        connector_ready: bool,
        historical_feedback: float,
        project_strategy_policy: tuple[str, ...],
    ) -> tuple[str, ...]:
        checklist = [
            "Sprawdz pierwsze 2 sekundy na telefonie przed publikacja.",
            (
                "Wrzuć jako private przez official connector i porownaj trzy opcje tytulu zanim odpalisz public."
                if connector_ready
                else "Wrzuć recznie jako private i porownaj trzy opcje tytulu zanim odpalisz public."
            ),
            "Zweryfikuj czy napisy nie zaslaniaja ust i glownego hooka.",
            f"Potwierdz finalna dlugosc: {SyntheticReleasePackService._format_duration(duration_sec)}.",
        ]
        if project_strategy_policy:
            checklist.append(f"Trzymaj projektowa polityke metadata: {project_strategy_policy[0]}")
        if historical_feedback <= -2.0:
            checklist.append("Ten format historycznie byl slabszy, wiec zacznij od najbardziej hook-first opcji tytulu.")
        elif historical_feedback >= 2.0:
            checklist.append("Ten format historycznie dowozil, wiec trzymaj pierwsza opcje tytulu jako glowna.")
        if warnings:
            checklist.append("QA zglosilo ostrzezenia - obejrzyj finalny export klatka po klatce przed uploadem.")
        return tuple(checklist[:5])

    @staticmethod
    def _build_publish_result_preview(*, connector_status: dict[str, object]) -> dict[str, object]:
        ready = bool(connector_status.get("ready"))
        if ready:
            return {
                "status": "awaiting_approval",
                "reason": "human_review_required",
                "message": "Official YouTube connector is ready. After approval ATNOS can upload through the connector.",
            }
        warnings = list(connector_status.get("warnings") or [])
        return {
            "status": "manual_upload_required",
            "reason": "youtube_not_ready",
            "message": "; ".join(str(item) for item in warnings if str(item).strip()) or "Official YouTube connector is not ready yet.",
        }

    @staticmethod
    def _build_analytics_followup(*, connector_status: dict[str, object]) -> dict[str, object]:
        ready = bool(connector_status.get("ready"))
        if ready:
            return {
                "status": "ready_after_publish",
                "trigger": "post_publish",
                "message": "After upload poll video status and fetch YouTube Analytics for views and average view percentage.",
                "recommended_metrics": ["views", "averageViewDuration", "averageViewPercentage"],
                "recommended_review_window_hours": 48,
            }
        return {
            "status": "blocked_by_connector",
            "trigger": "after_connector_setup",
            "message": "Analytics sync will become available after official YouTube connector setup or after linking the uploaded video.",
            "recommended_metrics": ["views", "averageViewDuration", "averageViewPercentage"],
            "recommended_review_window_hours": 48,
        }

    @staticmethod
    def _build_historical_playbook(
        *,
        calibration: SyntheticAnalyticsCalibration,
        historical_feedback: float,
        style_key: str | None,
        reel_key: str | None,
    ) -> tuple[str, ...]:
        if calibration.sample_count <= 0:
            return ()
        notes: list[str] = [f"Analytics sample count: {calibration.sample_count}."]
        if historical_feedback <= -2.0:
            notes.append(
                f"Aktualna kombinacja {style_key or 'style?'} / {reel_key or 'reel?'} historycznie wymaga mocniejszego hooka i szybszego payoffu."
            )
        elif historical_feedback >= 2.0:
            notes.append(
                f"Aktualna kombinacja {style_key or 'style?'} / {reel_key or 'reel?'} historycznie dowozila lepszy retention."
            )
        if calibration.strongest_style_key:
            notes.append(f"Najmocniejszy styl historycznie: {calibration.strongest_style_key}.")
        if calibration.strongest_reel_key:
            notes.append(f"Najmocniejszy typ rolki historycznie: {calibration.strongest_reel_key}.")
        return tuple(notes[:4])

    @staticmethod
    def _format_duration(value: float | None) -> str:
        if value is None or value <= 0:
            return "brak"
        return f"{value:.2f}s"

    @staticmethod
    def _clip_text(value: str, *, limit: int) -> str:
        cleaned = SyntheticReleasePackService._clean_text(value)
        if len(cleaned) <= limit:
            return cleaned
        clipped = cleaned[:limit].rsplit(" ", 1)[0].strip()
        return clipped or cleaned[:limit].strip()

    @staticmethod
    def _clean_text(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _normalize_token(value: object) -> str:
        cleaned = SyntheticReleasePackService._clean_text(value).lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
        return normalized

    @staticmethod
    def _resolve_float(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _resolve_int(value: object) -> int | None:
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def _coerce_calibration(
        value: SyntheticAnalyticsCalibration | dict[str, Any] | None,
    ) -> SyntheticAnalyticsCalibration:
        if isinstance(value, SyntheticAnalyticsCalibration):
            return value
        if isinstance(value, dict):
            return SyntheticAnalyticsCalibration.from_dict(value)
        return SyntheticAnalyticsCalibration.empty()

    @staticmethod
    def _coerce_project_strategy_profile(value: dict[str, Any] | None) -> dict[str, Any]:
        return dict(value or {})

    @staticmethod
    def _project_prefers_hook_first(profile: dict[str, Any]) -> bool:
        title_policy = dict(profile.get("title_policy") or {})
        strategy_key = SyntheticReleasePackService._clean_text(title_policy.get("strategy_key"))
        return strategy_key in {"hook_first_title", "test_hook_first_title"}

    @staticmethod
    def _build_project_strategy_policy(profile: dict[str, Any]) -> tuple[str, ...]:
        lines: list[str] = []
        for key in ("hook_template", "pacing_policy", "title_policy"):
            policy = dict(profile.get(key) or {})
            instruction = SyntheticReleasePackService._clean_text(policy.get("instruction"))
            if instruction:
                lines.append(instruction)
        return tuple(lines[:3])
