from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.visual_semantic_audit import (
    audit_visual_semantics,
    extract_storyboard_contact_sheet,
    normalize_semantic_audit,
)


ALLOWED_STATUSES = {
    "verified",
    "prototype_ready",
    "needs_revision",
    "insufficient_data",
    "blocked_user_action",
    "blocked_technical",
    "pending_external_review",
    "rejected",
}


@dataclass(frozen=True, slots=True)
class ChannelTarget:
    key: str
    account_id: str
    oauth_account_id: int
    profile_name: str
    prototype_dir: Path


CHANNELS = (
    ChannelTarget(
        key="wealth_blueprint",
        account_id="youtube:account-c5034bf4@example.invalid",
        oauth_account_id=70,
        profile_name="wealth_blueprint.json",
        prototype_dir=Path("E:/Atnos/ColdData/production/youtube-atnos-int.pl/prod-b640a246cf1e"),
    ),
    ChannelTarget(
        key="dark_chronicles",
        account_id="youtube:account-d6add2f8@example.invalid",
        oauth_account_id=69,
        profile_name="dark_chronicles.json",
        prototype_dir=Path("E:/Atnos/ColdData/production/youtube-atnos2909-gmail.com/prod-33dd5097ac94"),
    ),
    ChannelTarget(
        key="sigma_squad",
        account_id="youtube:account-bfd050e9@example.invalid",
        oauth_account_id=71,
        profile_name="sigma_squad.json",
        prototype_dir=Path("E:/Atnos/ColdData/production/youtube-atomek3133-gmail.com/prod-75e9d8502f14"),
    ),
)


CREATIVE_DIRECTIONS: dict[str, list[dict[str, Any]]] = {
    "wealth_blueprint": [
        {
            "name": "Money Behavior Autopsy",
            "idea": "One concrete money behavior is dissected through a real-life object, consequence, mechanism, and one action.",
            "emotion": "recognition followed by control",
            "series": ["Receipt Autopsy", "Subscription Leak", "Habit vs Outcome"],
            "long_format": "6-8 minute evidence-led behavior breakdown",
            "short_format": "32-48 second concrete behavior case",
            "production_frequency": "3 shorts per week after validation",
            "difficulty": "medium",
            "cost": "low-to-medium",
            "automation": "high for research/script; medium for visual sourcing",
            "external_dependency": "public statistics and licensed object footage",
            "copyright_risk": "low when using original graphics and licensed footage",
            "scores": {"originality": 8, "interest": 8, "fit": 9, "quality": 8, "automation": 8, "cost": 8, "repeatability": 9, "scale": 8, "legal": 9, "atnos": 8},
            "selected_for_prototype": True,
        },
        {
            "name": "One Chart, One Consequence",
            "idea": "A verified chart is translated into one consequence visible in an everyday financial decision.",
            "emotion": "surprise and practical clarity",
            "series": ["One Chart", "What This Number Changes", "Data to Decision"],
            "long_format": "5-7 minute data explainer",
            "short_format": "30-45 second chart-to-action short",
            "production_frequency": "2 shorts per week",
            "difficulty": "medium",
            "cost": "low",
            "automation": "high, provided source verification succeeds",
            "external_dependency": "official datasets",
            "copyright_risk": "low with recreated charts and citations",
            "scores": {"originality": 7, "interest": 7, "fit": 9, "quality": 8, "automation": 9, "cost": 9, "repeatability": 9, "scale": 8, "legal": 9, "atnos": 9},
            "selected_for_prototype": True,
        },
        {
            "name": "Financial Myth Under 45",
            "idea": "A common money claim is tested against primary data without stock picks or promises.",
            "emotion": "skepticism and relief",
            "series": ["Money Myth", "True or Expensive", "Advice Audit"],
            "long_format": "8 minute source comparison",
            "short_format": "35-45 second myth verdict",
            "production_frequency": "1-2 shorts per week",
            "difficulty": "medium-high",
            "cost": "low",
            "automation": "medium because claims require careful review",
            "external_dependency": "primary sources and regulator guidance",
            "copyright_risk": "low",
            "scores": {"originality": 7, "interest": 8, "fit": 8, "quality": 8, "automation": 6, "cost": 9, "repeatability": 8, "scale": 7, "legal": 8, "atnos": 7},
            "selected_for_prototype": False,
        },
    ],
    "dark_chronicles": [
        {
            "name": "One Document, One Disturbing Fact",
            "idea": "Every episode starts from one visible archival source and reconstructs a verified unsettling event.",
            "emotion": "unease and discovery",
            "series": ["Archive After Dark", "The Record Says", "One Page From History"],
            "long_format": "7-10 minute source-led mini-documentary",
            "short_format": "40-58 second archival mystery",
            "production_frequency": "2 shorts per week",
            "difficulty": "high",
            "cost": "medium",
            "automation": "medium; source and rights checks remain mandatory",
            "external_dependency": "public-domain archives and academic sources",
            "copyright_risk": "medium unless provenance is recorded",
            "scores": {"originality": 9, "interest": 9, "fit": 10, "quality": 9, "automation": 6, "cost": 6, "repeatability": 8, "scale": 7, "legal": 7, "atnos": 7},
            "selected_for_prototype": True,
        },
        {
            "name": "Timeline to the Reveal",
            "idea": "Three escalating beats establish what happened, why it spread, and what evidence survives.",
            "emotion": "suspense and resolution",
            "series": ["Three Steps to Disaster", "Before the Panic", "The Final Record"],
            "long_format": "6-9 minute chronological reconstruction",
            "short_format": "38-55 second three-beat story",
            "production_frequency": "2-3 shorts per week",
            "difficulty": "medium-high",
            "cost": "medium",
            "automation": "medium-high after sources are approved",
            "external_dependency": "archives, maps, public-domain illustrations",
            "copyright_risk": "medium-low with source manifests",
            "scores": {"originality": 8, "interest": 9, "fit": 9, "quality": 8, "automation": 7, "cost": 7, "repeatability": 9, "scale": 8, "legal": 8, "atnos": 8},
            "selected_for_prototype": True,
        },
        {
            "name": "Myth vs Historical Record",
            "idea": "A popular historical claim is contrasted with what contemporary records actually support.",
            "emotion": "curiosity and correction",
            "series": ["Myth on Trial", "History Did Not Say That", "Record vs Legend"],
            "long_format": "8-12 minute evidence comparison",
            "short_format": "40-55 second myth correction",
            "production_frequency": "1 short per week",
            "difficulty": "high",
            "cost": "medium",
            "automation": "low-to-medium because expert review may be needed",
            "external_dependency": "primary and scholarly sources",
            "copyright_risk": "low for text; medium for archive imagery",
            "scores": {"originality": 8, "interest": 8, "fit": 9, "quality": 9, "automation": 5, "cost": 6, "repeatability": 7, "scale": 6, "legal": 8, "atnos": 6},
            "selected_for_prototype": False,
        },
    ],
    "sigma_squad": [
        {
            "name": "Brainrot Translator",
            "idea": "One current slang phrase is decoded through an original visual situation instead of recycled meme footage.",
            "emotion": "recognition and playful surprise",
            "series": ["Translate This", "NPC Dictionary", "What Bro Meant"],
            "long_format": "3-5 minute culture explainer",
            "short_format": "18-30 second original slang scenario",
            "production_frequency": "3 shorts per week after youth-policy decision",
            "difficulty": "medium",
            "cost": "low-to-medium",
            "automation": "medium; slang freshness and safety need review",
            "external_dependency": "trend metadata, not copied clips",
            "copyright_risk": "low with original characters and audio",
            "scores": {"originality": 8, "interest": 8, "fit": 8, "quality": 8, "automation": 7, "cost": 8, "repeatability": 8, "scale": 8, "legal": 8, "atnos": 7},
            "selected_for_prototype": True,
        },
        {
            "name": "Absurd Rule Escalation",
            "idea": "An original character faces three increasingly absurd rules with a clear visual payoff.",
            "emotion": "anticipation and absurd comedy",
            "series": ["Rule One Was Normal", "Ohio Rulebook", "NPC School Rules"],
            "long_format": "2-4 minute original sketch compilation",
            "short_format": "20-35 second three-beat sketch",
            "production_frequency": "2 shorts per week",
            "difficulty": "high",
            "cost": "medium",
            "automation": "medium; character consistency is difficult",
            "external_dependency": "none beyond trend research",
            "copyright_risk": "low with original assets",
            "scores": {"originality": 9, "interest": 9, "fit": 9, "quality": 7, "automation": 5, "cost": 6, "repeatability": 7, "scale": 7, "legal": 9, "atnos": 6},
            "selected_for_prototype": True,
        },
        {
            "name": "W/L Decision Game",
            "idea": "Viewers classify an original social scenario as W or L before a fast reveal.",
            "emotion": "participation and comic judgment",
            "series": ["W or L", "Bro Decision Review", "Choose Before Reveal"],
            "long_format": "3 minute interactive compilation",
            "short_format": "15-25 second decision loop",
            "production_frequency": "3-4 shorts per week",
            "difficulty": "medium",
            "cost": "low",
            "automation": "high after original template validation",
            "external_dependency": "none",
            "copyright_risk": "low",
            "scores": {"originality": 7, "interest": 8, "fit": 8, "quality": 7, "automation": 9, "cost": 9, "repeatability": 9, "scale": 9, "legal": 9, "atnos": 8},
            "selected_for_prototype": False,
        },
    ],
}


EDITING_MANUALS: dict[str, dict[str, Any]] = {
    "wealth_blueprint": {
        "identity": "tactile finance documentary",
        "hook": "1.0-1.8 s: concrete object plus quantified consequence; no abstract promise",
        "cuts": "1.2-2.2 s average; one semantic change per spoken claim",
        "static_shot_max_s": 3.0,
        "visual_sources": ["original phone/receipt/desk footage", "recreated charts with citations", "faceless adult hands", "licensed B-roll"],
        "forbidden_visuals": ["generic plants as wealth metaphors", "blurred chart mush", "childish coins", "fake app text", "repeated AI still-life"],
        "subtitles": "3-5 words, Montserrat ExtraBold, white with emerald keyword, lower safe zone",
        "audio": "calm expert 135-150 wpm; narration -14 LUFS integrated; music at least 15 dB below voice",
        "export": "1080x1920, 30 fps, H.264 High, AAC 48 kHz, yuv420p",
    },
    "dark_chronicles": {
        "identity": "source-led gothic micro-documentary",
        "hook": "1.5-2.5 s: disturbing verified fact plus withheld cause",
        "cuts": "2.0-4.0 s average; faster only during escalation",
        "static_shot_max_s": 4.5,
        "visual_sources": ["public-domain archives", "maps", "documents", "restrained cinematic reconstructions", "etching animation"],
        "forbidden_visuals": ["historically implausible clothing", "AI anatomy errors", "repeated generic medieval streets", "gore as decoration"],
        "subtitles": "3-6 words, warm ivory, one matte-gold emphasis, no meme styling",
        "audio": "measured 130-150 wpm; -14 LUFS; restrained low drone 18-22 dB below voice",
        "export": "1080x1920, 30 fps, H.264 High, AAC 48 kHz, yuv420p",
    },
    "sigma_squad": {
        "identity": "original interactive absurd comedy",
        "hook": "0.4-0.8 s: original situation and readable rule, not visual noise",
        "cuts": "0.6-1.3 s average with a setup/payoff rhythm",
        "static_shot_max_s": 1.5,
        "visual_sources": ["original recurring characters", "original motion graphics", "licensed SFX", "screen-safe typography"],
        "forbidden_visuals": ["copied creator clips", "copyrighted meme audio", "illegible deep-fried frames", "adult imitation of youth slang", "random motion graphic fallback"],
        "subtitles": "2-4 words, one high-contrast block, never cover the main action",
        "audio": "energetic but intelligible 165-185 wpm; -14 LUFS; no clipping",
        "export": "1080x1920, 30 fps, H.264 High, AAC 48 kHz, yuv420p",
    },
}


FORMAT_PLANS: dict[str, list[dict[str, Any]]] = {
    "wealth_blueprint": [
        {"name": "long", "duration": "6-8 min", "hook": "0-8 s", "segments": 8, "visual_changes": "at least 35", "acceptance": "all numerical claims cited"},
        {"name": "short", "duration": "90-150 s", "hook": "0-3 s", "segments": 6, "visual_changes": "at least 18", "acceptance": "one mechanism and one action"},
        {"name": "shorts", "duration": "32-48 s", "hook": "0-1.8 s", "segments": 7, "visual_changes": "at least 10", "acceptance": "no generic AI metaphor frames"},
        {"name": "test", "duration": "20-28 s", "hook": "0-1.2 s", "segments": 4, "visual_changes": "at least 6", "acceptance": "private review only"},
        {"name": "scalable", "duration": "35-45 s", "hook": "0-1.5 s", "segments": 6, "visual_changes": "at least 9", "acceptance": "approved source and visual templates"},
    ],
    "dark_chronicles": [
        {"name": "long", "duration": "7-10 min", "hook": "0-12 s", "segments": 10, "visual_changes": "at least 30", "acceptance": "source manifest and provenance"},
        {"name": "short", "duration": "2-3 min", "hook": "0-4 s", "segments": 7, "visual_changes": "at least 16", "acceptance": "context before speculation"},
        {"name": "shorts", "duration": "40-58 s", "hook": "0-2.2 s", "segments": 9, "visual_changes": "at least 10", "acceptance": "fact/source agent passes"},
        {"name": "test", "duration": "25-35 s", "hook": "0-2 s", "segments": 5, "visual_changes": "at least 6", "acceptance": "one public-domain source"},
        {"name": "scalable", "duration": "42-55 s", "hook": "0-2 s", "segments": 8, "visual_changes": "at least 9", "acceptance": "approved archive kit"},
    ],
    "sigma_squad": [
        {"name": "long", "duration": "3-5 min", "hook": "0-2 s", "segments": 10, "visual_changes": "at least 45", "acceptance": "original assets and youth policy review"},
        {"name": "short", "duration": "45-90 s", "hook": "0-1 s", "segments": 8, "visual_changes": "at least 18", "acceptance": "clear setup/payoff"},
        {"name": "shorts", "duration": "18-30 s", "hook": "0-0.8 s", "segments": 7, "visual_changes": "at least 14", "acceptance": "original audio and made-for-kids decision"},
        {"name": "test", "duration": "12-18 s", "hook": "first frame", "segments": 4, "visual_changes": "at least 8", "acceptance": "private review only"},
        {"name": "scalable", "duration": "20-25 s", "hook": "0-0.6 s", "segments": 6, "visual_changes": "at least 12", "acceptance": "validated original character template"},
    ],
}


MANUAL_VISUAL_FINDINGS = {
    "wealth_blueprint": [
        "The sampled storyboard repeats muted still-life compositions.",
        "Plant and hourglass metaphors are visually polished but weakly specific to the spoken financial mechanism.",
        "The sequence lacks receipts, banking UI, calculations, or other verifiable financial context.",
    ],
    "dark_chronicles": [
        "The storyboard shows a chronological human-action progression rather than abstract filler.",
        "Several reconstructions share a similar synthetic historical-drama look and need provenance or explicit reconstruction labeling.",
        "The sequence contains no visible primary-source document despite a source-first brand requirement.",
    ],
    "sigma_squad": [
        "The video manifest records LTX failures and motion-graphic fallbacks.",
        "The prior QA report detected insufficient visual variety and timeline change.",
        "The current concept depends on generic slang rather than a validated original recurring character system.",
    ],
}


class YouTubeChannelDevelopmentSession:
    """Evidence-first development and prototype review for Atnos YouTube channels."""

    def __init__(
        self,
        *,
        repo_root: Path,
        state_dir: Path,
        output_root: Path = Path("E:/Atnos/LearningRuns/youtube-channel-development"),
        api_base_url: str = "http://127.0.0.1:8000",
        semantic_cache_run: Path | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.api_dir = self.repo_root / "runtime-recovered" / "api"
        self.profile_dir = self.api_dir / "config" / "channel_profiles"
        self.state_dir = state_dir.resolve()
        self.output_root = output_root.resolve()
        self.intelligence_db = self.state_dir / "intelligence.db"
        self.api_base_url = api_base_url.rstrip("/")
        self.semantic_cache_run = semantic_cache_run.resolve() if semantic_cache_run else None
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = self.output_root / timestamp

    def run(self) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=False)
        inventory: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        for channel in CHANNELS:
            report = self._channel_report(channel)
            reports.append(report)
            inventory.append(report["inventory"])
            channel_dir = self.run_dir / channel.key
            channel_dir.mkdir(parents=True, exist_ok=True)
            self._write_json(channel_dir / "channel_report.json", report)
            self._write_json(channel_dir / "creative_directions.json", report["creative_directions"])
            self._write_json(channel_dir / "editing_manual.json", report["editing_manual"])
            self._write_json(channel_dir / "quality_agents.json", report["quality_agents"])
            self._write_json(channel_dir / "quality_coordinator.json", report["quality_coordinator"])
            (channel_dir / "channel_report.md").write_text(self._channel_markdown(report), encoding="utf-8")

        capability = self._capability_matrix(reports)
        aggregate = self._aggregate(reports, capability)
        self._write_json(self.run_dir / "inventory.json", inventory)
        self._write_json(self.run_dir / "capability_matrix.json", capability)
        self._write_json(self.run_dir / "aggregate_report.json", aggregate)
        (self.run_dir / "aggregate_report.md").write_text(self._aggregate_markdown(aggregate), encoding="utf-8")
        return self.run_dir

    def _channel_report(self, channel: ChannelTarget) -> dict[str, Any]:
        profile = self._read_json(self.profile_dir / channel.profile_name)
        prototype = self._prototype_evidence(channel, profile)
        intelligence = self._intelligence_evidence(channel.account_id)
        oauth = self._oauth_evidence(channel.oauth_account_id)
        policy_blockers: list[str] = []
        if channel.key == "sigma_squad" and "8-16" in str(profile.get("target_audience")):
            policy_blockers.append("made_for_kids classification requires an explicit owner decision")
        inventory_status = "verified" if oauth.get("ready") else "blocked_user_action"
        if policy_blockers:
            prototype["blocking_findings"].extend(policy_blockers)
            prototype["status"] = "blocked_user_action"
        agents = self._quality_agents(channel, profile, prototype)
        coordinator = self._coordinate_agents(channel, prototype, agents)
        readiness = "NEEDS_REVISION"
        if prototype["status"] in {"blocked_user_action", "blocked_technical"}:
            readiness = "BLOCKED"
        elif coordinator["p0_count"] == 0 and coordinator["p1_count"] == 0:
            readiness = "READY_FOR_NEXT_REVIEW"
        return {
            "channel_key": channel.key,
            "channel_name": profile.get("channel_name"),
            "status": prototype["status"],
            "readiness_decision": readiness,
            "inventory": {
                "status": inventory_status,
                "account_id": channel.account_id,
                "configured_name": profile.get("channel_name"),
                "configured_handle": profile.get("channel_handle"),
                "live_channel_title": oauth.get("channel_title"),
                "oauth": oauth,
                "niche": profile.get("niche"),
                "language": profile.get("language"),
                "target_audience": profile.get("target_audience"),
                "target_market": profile.get("target_market"),
                "publish_mode": profile.get("publish_mode"),
                "auto_publish_after_qa": profile.get("auto_publish_after_qa"),
                "analytics": intelligence,
                "legal_constraints": profile.get("forbidden_topics", []),
                "current_style": profile.get("style"),
                "formats": [item["name"] for item in FORMAT_PLANS[channel.key]],
                "evidence_paths": [str(self.profile_dir / channel.profile_name), str(self.intelligence_db)],
            },
            "creative_directions": CREATIVE_DIRECTIONS[channel.key],
            "selected_direction": next(item for item in CREATIVE_DIRECTIONS[channel.key] if item["selected_for_prototype"]),
            "creative_identity": self._creative_identity(channel.key, profile),
            "format_plans": FORMAT_PLANS[channel.key],
            "editing_manual": EDITING_MANUALS[channel.key],
            "capability_summary": self._channel_capabilities(prototype, intelligence, oauth),
            "prototype": prototype,
            "quality_agents": agents,
            "external_reviews": {
                "claude": {"status": "pending_external_review", "scope": "not submitted; no real response exists"},
                "kimi": {"status": "pending_external_review", "scope": "not submitted; no real response exists"},
            },
            "quality_coordinator": coordinator,
            "iteration": {
                "current": 1,
                "maximum": 3,
                "status": "needs_revision" if coordinator["p0_count"] or coordinator["p1_count"] else "prototype_ready",
                "next_iteration_allowed": not bool(policy_blockers),
                "comparison": [],
            },
            "policy_blockers": policy_blockers,
            "cost_and_time": {
                "status": "insufficient_data",
                "reason": "Historical manifests do not contain a complete wall-clock and energy/cost ledger for this prototype.",
            },
        }

    def _prototype_evidence(self, channel: ChannelTarget, profile: dict[str, Any]) -> dict[str, Any]:
        root = channel.prototype_dir
        final = root / "assets" / "final_short.mp4"
        script = self._read_json(root / "script.json")
        qa = self._read_json(root / "qa_report.json")
        video_manifest = self._read_json(root / "video_manifest.json")
        media = self._probe_media(final)
        decode = self._decode_check(final)
        audio = self._audio_analysis(final)
        storyboard = root / "development_storyboard.jpg"
        generated = extract_storyboard_contact_sheet(final, storyboard, frame_count=9)
        semantic_audit = self._cached_semantic_audit(channel.key, generated) or self._semantic_audit(
                generated,
                profile_key=channel.key,
                topic_title=(script.get("title_options") or [""])[0],
            )
        blockers = list(qa.get("blocking_failures") or [])
        if not media.get("valid"):
            blockers.append("final MP4 failed ffprobe validation")
        if not decode.get("passed"):
            blockers.append("final MP4 failed full decode validation")
        if channel.key in {"wealth_blueprint", "sigma_squad"}:
            blockers.append("manual visual review rejected the current visual direction")
        if channel.key == "dark_chronicles":
            blockers.append("source provenance and independent semantic review are incomplete")
        for blocker in semantic_audit.get("blockers") or []:
            blockers.append(f"local vision audit: {blocker}")
        source_files = [path for path in root.rglob("*") if path.is_file() and "source" in path.name.lower()]
        if not source_files:
            blockers.append("prototype has no dedicated source manifest")
        status = "needs_revision" if blockers else "prototype_ready"
        return {
            "status": status,
            "job_id": root.name,
            "root": str(root),
            "title": (script.get("title_options") or [""])[0],
            "language": script.get("language_code"),
            "segment_count": len(script.get("segments") or []),
            "script_word_count": int(script.get("total_words") or self._word_count(script.get("body"))),
            "final_video": str(final),
            "storyboard": str(generated) if generated else None,
            "semantic_storyboard_audit": semantic_audit,
            "media_probe": media,
            "decode_check": decode,
            "audio_analysis": audio,
            "subtitles": self._subtitle_evidence(root / "assets" / "subtitles.srt", media.get("duration_s")),
            "thumbnail": self._file_evidence(root / "thumbnail" / "thumbnail_v1.png"),
            "narration": self._file_evidence(root / "audio" / "narration.wav"),
            "qa_overall": qa.get("overall"),
            "qa_auto_uploadable": bool(qa.get("auto_uploadable")),
            "old_qa_blockers": qa.get("blocking_failures") or [],
            "video_backend": video_manifest.get("backend"),
            "fallback_used": video_manifest.get("fallback_used"),
            "source_manifest_files": [str(path) for path in source_files],
            "manual_visual_findings": MANUAL_VISUAL_FINDINGS[channel.key],
            "blocking_findings": list(dict.fromkeys(blockers)),
            "evidence_paths": [str(root / "script.json"), str(root / "qa_report.json"), str(root / "video_manifest.json")],
        }

    def _quality_agents(
        self,
        channel: ChannelTarget,
        profile: dict[str, Any],
        prototype: dict[str, Any],
    ) -> list[dict[str, Any]]:
        script = self._read_json(channel.prototype_dir / "script.json")
        segments = script.get("segments") or []
        durations = [float(item.get("duration_target_sec") or 0) for item in segments if isinstance(item, dict)]
        hook = str(script.get("opening_line") or script.get("hook") or "")
        findings = prototype["blocking_findings"]
        agents = [
            self._agent("script_and_narration", "script.json and subtitle transcript", [], [
                self._issue("00:00", "P1" if self._word_count(hook) > 24 else "P2", "Hook must be concise and concrete.", f"Opening has {self._word_count(hook)} words."),
            ], self._script_scores(script)),
            self._agent("facts_and_sources", "script.json and files whose names contain source", [], [
                self._issue("whole video", "P0", "No dedicated source manifest proves the script claims.", "Add claim-to-source mapping before approval.")
            ] if not prototype["source_manifest_files"] else [], {"value": 5, "readiness": 2}, missing=["independent primary-source verification"]),
            self._agent("visual_and_editing", "nine-frame storyboard, video manifest and prior QA", [], [
                self._issue("whole video", "P1", item, "Replace or re-plan the affected scene and re-audit the storyboard.")
                for item in prototype["manual_visual_findings"]
            ] + [
                self._issue("whole video", "P1", f"Local vision audit: {item}", "Rebuild the storyboard and repeat the local vision audit.")
                for item in prototype["semantic_storyboard_audit"].get("blockers") or []
            ], self._visual_scores(channel.key, prototype["semantic_storyboard_audit"])),
            self._agent("audio", "decoded audio stream and FFmpeg loudness/silence measurements", [], self._audio_issues(prototype["audio_analysis"]), self._audio_scores(prototype["audio_analysis"])),
            self._agent("retention", "script segment timing and rendered duration", [], self._retention_issues(durations, prototype), self._retention_scores(durations, prototype)),
            self._agent("channel_style", "channel profile, editing manual and storyboard findings", [], [
                self._issue("whole video", "P1", "The current render does not fully satisfy the channel-specific visual contract.", "; ".join(prototype["manual_visual_findings"]))
            ], {"coherence": 5, "originality": 4 if channel.key != "dark_chronicles" else 6, "readiness": 4}),
            self._agent("technical", "ffprobe JSON, full decode, subtitle and thumbnail files", [], self._technical_issues(prototype), self._technical_scores(prototype)),
            self._agent("critical_reviewer", "all local evidence, with instructions to search for publication blockers", [], [
                self._issue("whole video", "P0" if "source" in finding.lower() else "P1", finding, "Do not publish until evidence shows this blocker is resolved.")
                for finding in findings
            ], {"value": 5, "originality": 4, "readiness": 2}),
        ]
        return agents

    def _coordinate_agents(
        self,
        channel: ChannelTarget,
        prototype: dict[str, Any],
        agents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        unique: dict[str, dict[str, Any]] = {}
        for agent in agents:
            for issue in agent["problems"]:
                key = re.sub(r"[^a-z0-9]+", " ", issue["description"].lower()).strip()
                unique.setdefault(key, issue | {"reported_by": [agent["agent"]]})
                if agent["agent"] not in unique[key]["reported_by"]:
                    unique[key]["reported_by"].append(agent["agent"])
        ordered = sorted(unique.values(), key=lambda item: (int(item["priority"][1]), item["description"]))
        p0 = sum(item["priority"] == "P0" for item in ordered)
        p1 = sum(item["priority"] == "P1" for item in ordered)
        return {
            "status": "needs_revision" if p0 or p1 else "prototype_ready",
            "channel_key": channel.key,
            "prototype_job_id": prototype["job_id"],
            "p0_count": p0,
            "p1_count": p1,
            "deduplicated_issues": ordered,
            "conflicts": self._conflicts(prototype),
            "decision": "REBUILD" if p0 else ("REVISE" if p1 else "APPROVE_WITH_MINOR_FIXES"),
            "next_changes": [item["fix"] for item in ordered if item["priority"] in {"P0", "P1"}],
            "publication_blocked": True,
        }

    def _capability_matrix(self, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        any_valid = any(report["prototype"]["media_probe"].get("valid") for report in reports)
        image_generation = self._image_generation_evidence()
        youtube_ready = all(report["inventory"]["oauth"].get("ready") for report in reports)
        youtube_probe_evidence = "; ".join(
            f"{report['channel_name']}: {report['inventory']['oauth'].get('status', 'unknown')}"
            for report in reports
        )
        youtube_warnings = [
            warning
            for report in reports
            for warning in report["inventory"]["oauth"].get("warnings", [])
            if warning
        ]
        return [
            self._capability("research", "verified", "Intelligence SQLite and learning session", str(self.intelligence_db), "Competitor tables are empty."),
            self._capability("source verification", "needs_revision", "claim safety and research modules", "prototype source manifests", "Current prototypes lack dedicated claim-to-source manifests."),
            self._capability("outline and script", "verified", "local Ollama production steps", reports[0]["prototype"]["evidence_paths"][0], "Requires independent fact review."),
            self._capability("voice synthesis", "verified", "local TTS and FFmpeg normalization", reports[0]["prototype"]["narration"]["path"], "Voice naturalness still needs human listening."),
            self._capability(
                "image generation",
                image_generation["status"],
                "ComfyUI Flux 2 Klein 4B",
                image_generation["evidence"],
                image_generation["limitation"],
            ),
            self._capability(
                "image generation performance",
                "needs_revision" if image_generation["status"] == "verified" else "blocked_technical",
                "Comfy Desktop on AMD ROCm",
                image_generation["diagnosis"],
                "A verified 512x512 Flux prompt took 845 seconds; this is too slow for scalable scene-by-scene production.",
            ),
            self._capability("native video generation", "blocked_technical", "ComfyUI LTX/Wan", "GET /health", "Required video model set is incomplete and LTX is unstable on this Windows ROCm setup."),
            self._capability("editing and assembly", "verified" if any_valid else "blocked_technical", "FFmpeg", reports[1]["prototype"]["final_video"], "Visual quality is separate from technical validity."),
            self._capability("subtitles", "verified", "Whisper/SRT/ASS and FFmpeg", reports[1]["prototype"]["subtitles"]["path"], "Coverage is estimated from timestamps."),
            self._capability("MP4 validation", "verified", "ffprobe and full FFmpeg decode", reports[1]["prototype"]["final_video"], "Every new render must repeat this validation."),
            self._capability("thumbnail", "needs_revision", "Pillow/ComfyUI", reports[0]["prototype"]["thumbnail"]["path"], "Existing thumbnails are not yet approved by a channel-specific visual review."),
            self._capability(
                "YouTube read access",
                "verified" if youtube_ready else "blocked_user_action",
                "YouTube OAuth channels.list",
                youtube_probe_evidence,
                (
                    "All configured channel probes returned ready. Live writes remain globally blocked."
                    if youtube_ready
                    else "; ".join(dict.fromkeys(youtube_warnings))
                    or "At least one configured YouTube account did not pass its live connector probe."
                ),
            ),
            self._capability("YouTube publication", "blocked_user_action", "YouTube videos.insert", "profiles use private_only and auto_publish_after_qa=false", "Explicit owner consent and YouTube project compliance are required."),
        ]

    def _aggregate(self, reports: list[dict[str, Any]], capability: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "run_id": self.run_dir.name,
            "created_at": datetime.now(UTC).isoformat(),
            "status": "needs_revision",
            "publication_performed": False,
            "channel_reports": [
                {
                    "channel": report["channel_name"],
                    "selected_style": report["selected_direction"]["name"],
                    "prototype_status": report["status"],
                    "quality_score": self._quality_average(report["quality_agents"]),
                    "main_risk": (report["quality_coordinator"]["deduplicated_issues"] or [{"description": "none"}])[0]["description"],
                    "automation": report["selected_direction"]["automation"],
                    "readiness": report["readiness_decision"],
                    "report_path": str(self.run_dir / report["channel_key"] / "channel_report.md"),
                }
                for report in reports
            ],
            "capability_matrix": capability,
            "global_blockers": [
                "No current prototype has complete claim-to-source evidence.",
                "Flux still-image generation is verified but too slow for scalable production; Z-Image is blocked by Windows commit-memory error 1455.",
                "ComfyUI native video backend is incomplete/unstable; current prototypes rely on still-image animation or fallbacks.",
                "Sigma Squad requires an explicit made-for-kids classification decision.",
                "Claude and Kimi have not returned real reviews.",
                "No publication or channel mutation is authorized by this session.",
            ],
        }

    def _image_generation_evidence(self) -> dict[str, str]:
        diagnosis = self.output_root / "comfy-smoke" / "comfy-runtime-diagnosis.json"
        candidates = sorted(
            (self.output_root / "comfy-smoke").glob("*.png.comfyui.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return {
                "status": "blocked_technical",
                "evidence": str(diagnosis),
                "diagnosis": str(diagnosis),
                "limitation": "No evidence-backed ComfyUI image artifact exists.",
            }
        evidence_path = candidates[0]
        evidence = self._read_json(evidence_path)
        artifact = Path(str(evidence.get("artifact") or ""))
        expected_hash = str(evidence.get("artifact_sha256") or "").lower()
        if not artifact.is_file() or artifact.stat().st_size < 1_024 or not expected_hash:
            return {
                "status": "blocked_technical",
                "evidence": str(evidence_path),
                "diagnosis": str(diagnosis),
                "limitation": "ComfyUI evidence exists, but its image artifact is missing or incomplete.",
            }
        actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            return {
                "status": "blocked_technical",
                "evidence": str(evidence_path),
                "diagnosis": str(diagnosis),
                "limitation": "ComfyUI artifact hash does not match its generation evidence.",
            }
        return {
            "status": "verified",
            "evidence": str(evidence_path),
            "diagnosis": str(diagnosis),
            "limitation": (
                "Flux still-image generation is verified at 512x512, but measured prompt time was 845 seconds; "
                "Z-Image is blocked by Windows commit-memory error 1455 and native video is not verified."
            ),
        }

    def _intelligence_evidence(self, account_id: str) -> dict[str, Any]:
        if not self.intelligence_db.exists():
            return {"status": "insufficient_data", "reason": "intelligence.db missing"}
        with sqlite3.connect(self.intelligence_db) as connection:
            connection.row_factory = sqlite3.Row
            own = connection.execute(
                "SELECT COUNT(*) AS rows_count, COALESCE(SUM(views), 0) AS views, MAX(collected_at) AS latest "
                "FROM own_performance WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            account = connection.execute(
                "SELECT niches, last_refresh_at FROM intelligence_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            competitors = connection.execute(
                "SELECT COUNT(*) AS count FROM competitor_channels WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        rows_count = int(own["rows_count"] if own else 0)
        return {
            "status": "verified" if rows_count else "insufficient_data",
            "own_performance_rows": rows_count,
            "own_views_in_rows": int(own["views"] if own else 0),
            "latest_own_collection": own["latest"] if own else None,
            "niches": self._json_or_text(account["niches"] if account else None),
            "last_refresh_at": account["last_refresh_at"] if account else None,
            "competitor_channels": int(competitors["count"] if competitors else 0),
            "strategic_conclusion": "insufficient_data" if rows_count < 30 else "verified",
        }

    def _oauth_evidence(self, account_id: int) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.api_base_url}/connectors/youtube/probe",
                params={"account_id": account_id},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {"status": "blocked_technical", "ready": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "status": "verified" if payload.get("ready") else "blocked_user_action",
            "ready": bool(payload.get("ready")),
            "connected": bool(payload.get("connected")),
            "channel_title": payload.get("channel_title"),
            "warnings": list(payload.get("warnings") or []),
            "checked_via": "YouTube channels.list connector probe",
        }

    @staticmethod
    def _creative_identity(key: str, profile: dict[str, Any]) -> dict[str, Any]:
        manual = EDITING_MANUALS[key]
        return {
            "narration": {
                "hook": manual["hook"],
                "voice": profile.get("narration_style"),
                "notes": profile.get("narration_notes"),
                "ending": "Resolve the promised question and close with one channel-specific next action; no generic motivational CTA.",
            },
            "language": {
                "language": "English",
                "formality": "clear conversational English matched to the audience",
                "forbidden": ["AI filler", "As you may know", "In today's fast-paced world", "generic hype", "unsupported certainty"],
                "proper_names": "Store pronunciation overrides before TTS; do not improvise difficult names.",
            },
            "visual": {
                "style": profile.get("visual_style"),
                "sources": manual["visual_sources"],
                "forbidden": manual["forbidden_visuals"],
                "synthetic_labeling": "Record generated-asset provenance and apply YouTube altered-content disclosure when policy requires it.",
            },
            "audio": {"spec": manual["audio"], "music": "duck under speech; never compete with narration"},
            "recognition_markers": [manual["identity"], manual["subtitles"], manual["hook"]],
        }

    @staticmethod
    def _channel_capabilities(prototype: dict[str, Any], intelligence: dict[str, Any], oauth: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"element": "research", "status": intelligence.get("status"), "evidence": intelligence.get("latest_own_collection")},
            {"element": "script", "status": "verified" if prototype["script_word_count"] > 0 else "insufficient_data", "evidence": prototype["evidence_paths"][0]},
            {"element": "render", "status": "verified" if prototype["media_probe"].get("valid") else "blocked_technical", "evidence": prototype["final_video"]},
            {"element": "visual quality", "status": "needs_revision", "evidence": prototype["storyboard"]},
            {"element": "youtube read", "status": oauth.get("status"), "evidence": oauth.get("checked_via")},
            {"element": "youtube publication", "status": "blocked_user_action", "evidence": "No publication consent for this session."},
        ]

    @staticmethod
    def _capability(element: str, status: str, tool: str, evidence: str, limitation: str) -> dict[str, Any]:
        return {"element": element, "status": status, "tool": tool, "evidence": evidence, "limitation": limitation}

    @staticmethod
    def _agent(
        name: str,
        scope: str,
        strengths: list[str],
        problems: list[dict[str, Any]],
        scores: dict[str, int],
        *,
        missing: list[str] | None = None,
    ) -> dict[str, Any]:
        decision = "APPROVE"
        if any(item["priority"] == "P0" for item in problems):
            decision = "REBUILD"
        elif any(item["priority"] == "P1" for item in problems):
            decision = "REVISE"
        elif problems:
            decision = "APPROVE_WITH_MINOR_FIXES"
        return {
            "agent": name,
            "status": "needs_revision" if decision in {"REBUILD", "REVISE"} else "prototype_ready",
            "scope": scope,
            "strengths": strengths,
            "problems": problems,
            "missing": missing or [],
            "scores": scores,
            "decision": decision,
        }

    @staticmethod
    def _issue(timestamp: str, priority: str, description: str, fix: str) -> dict[str, Any]:
        return {"timestamp": timestamp, "priority": priority, "description": description, "impact": "publication readiness", "fix": fix}

    @staticmethod
    def _script_scores(script: dict[str, Any]) -> dict[str, int]:
        quality = script.get("script_quality") if isinstance(script.get("script_quality"), dict) else {}
        return {
            "hook": int(quality.get("hook_score") or 6),
            "script": int(quality.get("overall_score") or 6),
            "value": int(quality.get("value_score") or 6),
            "narration": 5,
            "readiness": 5,
        }

    @staticmethod
    def _retention_issues(durations: list[float], prototype: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if durations and max(durations) > 7:
            issues.append(YouTubeChannelDevelopmentSession._issue("script timeline", "P1", "At least one planned segment exceeds 7 seconds.", "Split it into a setup and payoff with distinct visuals."))
        if float(prototype["media_probe"].get("duration_s") or 0) > 59:
            issues.append(YouTubeChannelDevelopmentSession._issue("end", "P0", "Rendered duration exceeds the Shorts target.", "Re-cut below 59 seconds."))
        return issues

    @staticmethod
    def _retention_scores(durations: list[float], prototype: dict[str, Any]) -> dict[str, int]:
        long_segments = sum(value > 6 for value in durations)
        base = max(3, 7 - long_segments)
        return {"hook": base, "pacing": base, "value": 6, "readiness": min(base, 5)}

    @staticmethod
    def _audio_issues(audio: dict[str, Any]) -> list[dict[str, Any]]:
        if audio.get("status") != "verified":
            return [YouTubeChannelDevelopmentSession._issue("whole video", "P1", "Audio loudness could not be verified.", "Repeat FFmpeg loudness analysis and listening review.")]
        issues: list[dict[str, Any]] = []
        loudness = audio.get("input_i")
        if loudness is not None and not -16 <= loudness <= -12:
            issues.append(YouTubeChannelDevelopmentSession._issue("whole video", "P1", f"Integrated loudness is {loudness:.1f} LUFS, outside -14 +/-2.", "Normalize narration/mix and remeasure."))
        return issues

    @staticmethod
    def _audio_scores(audio: dict[str, Any]) -> dict[str, int]:
        loudness = audio.get("input_i")
        okay = loudness is not None and -16 <= loudness <= -12
        return {"audio": 8 if okay else 5, "narration": 5, "technical": 8 if audio.get("status") == "verified" else 3, "readiness": 5}

    @staticmethod
    def _technical_issues(prototype: dict[str, Any]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if not prototype["media_probe"].get("valid"):
            issues.append(YouTubeChannelDevelopmentSession._issue("whole file", "P0", "MP4 validation failed.", "Re-render and repeat ffprobe."))
        if not prototype["decode_check"].get("passed"):
            issues.append(YouTubeChannelDevelopmentSession._issue("whole file", "P0", "Full decode failed.", "Reject the artifact and re-render."))
        if not prototype["subtitles"].get("exists"):
            issues.append(YouTubeChannelDevelopmentSession._issue("whole video", "P0", "Subtitle file is missing.", "Generate and validate subtitles."))
        return issues

    @staticmethod
    def _technical_scores(prototype: dict[str, Any]) -> dict[str, int]:
        okay = prototype["media_probe"].get("valid") and prototype["decode_check"].get("passed")
        return {"technical": 9 if okay else 2, "subtitles": 8 if prototype["subtitles"].get("exists") else 1, "readiness": 8 if okay else 1}

    def _probe_media(self, path: Path) -> dict[str, Any]:
        ffmpeg = resolve_ffmpeg()
        ffprobe = resolve_ffprobe(ffmpeg)
        if not path.exists() or path.stat().st_size < 1024 or not ffprobe:
            return {"status": "blocked_technical", "valid": False, "path": str(path), "size_bytes": path.stat().st_size if path.exists() else 0}
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"status": "blocked_technical", "valid": False, "error": result.stderr[-1000:], "path": str(path)}
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        duration = float((payload.get("format") or {}).get("duration") or 0)
        valid = bool(video and audio and duration > 0 and path.stat().st_size >= 500_000)
        return {
            "status": "verified" if valid else "blocked_technical",
            "valid": valid,
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "duration_s": round(duration, 3),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": video.get("avg_frame_rate"),
            "video_codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name"),
            "audio_sample_rate": audio.get("sample_rate"),
        }

    def _decode_check(self, path: Path) -> dict[str, Any]:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg or not path.exists():
            return {"status": "blocked_technical", "passed": False, "reason": "ffmpeg or input missing"}
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return {"status": "verified" if result.returncode == 0 else "blocked_technical", "passed": result.returncode == 0, "stderr": result.stderr[-2000:]}

    def _audio_analysis(self, path: Path) -> dict[str, Any]:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg or not path.exists():
            return {"status": "blocked_technical", "reason": "ffmpeg or input missing"}
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        match = re.search(r"\{\s*\"input_i\".*?\}", result.stderr, re.DOTALL)
        if result.returncode != 0 or not match:
            return {"status": "blocked_technical", "reason": result.stderr[-1000:]}
        payload = json.loads(match.group(0))
        return {
            "status": "verified",
            "input_i": self._float(payload.get("input_i")),
            "input_tp": self._float(payload.get("input_tp")),
            "input_lra": self._float(payload.get("input_lra")),
            "input_thresh": self._float(payload.get("input_thresh")),
        }

    @staticmethod
    def _semantic_audit(
        storyboard: Path | None,
        *,
        profile_key: str,
        topic_title: str,
    ) -> dict[str, Any]:
        if storyboard is None:
            return {"status": "insufficient_data", "passed": False, "blockers": ["storyboard_missing"]}
        try:
            result = audit_visual_semantics(
                Settings(),
                storyboard,
                profile_key=profile_key,
                topic_title=topic_title,
                artifact_kind="nine-frame storyboard",
            )
            tool_status = str(result.get("status") or "")
            result["tool_status"] = tool_status
            if bool(result.get("passed")):
                result["status"] = "verified"
            elif tool_status in {"missing", "disabled", "skipped"}:
                result["status"] = "insufficient_data"
            elif tool_status in {"unavailable", "invalid_response"}:
                result["status"] = "blocked_technical"
            else:
                result["status"] = "needs_revision"
            return result
        except Exception as exc:
            return {
                "status": "blocked_technical",
                "passed": False,
                "blockers": ["semantic_visual_audit_unavailable"],
                "reason": f"{type(exc).__name__}: {exc}",
            }

    def _cached_semantic_audit(self, channel_key: str, storyboard: Path | None) -> dict[str, Any] | None:
        if self.semantic_cache_run is None or storyboard is None:
            return None
        report_path = self.semantic_cache_run / channel_key / "channel_report.json"
        cached = self._read_json(report_path).get("prototype", {}).get("semantic_storyboard_audit")
        if not isinstance(cached, dict) or not isinstance(cached.get("raw"), dict):
            return None
        result = normalize_semantic_audit(
            cached["raw"],
            settings=Settings(),
            path=storyboard,
            artifact_kind="nine-frame storyboard",
            model=str(cached.get("model") or "qwen3-vl:8b"),
        )
        result["tool_status"] = result.get("status")
        result["status"] = "verified" if result.get("passed") else "needs_revision"
        result["cache_source"] = str(report_path)
        return result

    @staticmethod
    def _visual_scores(channel_key: str, semantic_audit: dict[str, Any]) -> dict[str, int]:
        scores = semantic_audit.get("scores") if isinstance(semantic_audit.get("scores"), dict) else {}
        relevance = scores.get("topic_relevance_score")
        premium = scores.get("premium_style_score")
        clarity = scores.get("clarity_score")
        fallback = 6 if channel_key == "dark_chronicles" else 4
        return {
            "image": int(premium) if isinstance(premium, (int, float)) else fallback,
            "editing": 5,
            "coherence": int(relevance) if isinstance(relevance, (int, float)) else fallback,
            "clarity": int(clarity) if isinstance(clarity, (int, float)) else fallback,
        }

    @staticmethod
    def _subtitle_evidence(path: Path, duration_s: float | None) -> dict[str, Any]:
        if not path.exists():
            return {"status": "insufficient_data", "exists": False, "path": str(path)}
        text = path.read_text(encoding="utf-8", errors="replace")
        segments = len(re.findall(r"(?m)^\d+\s*$", text))
        return {"status": "verified" if segments else "needs_revision", "exists": True, "path": str(path), "segments": segments, "video_duration_s": duration_s}

    @staticmethod
    def _file_evidence(path: Path) -> dict[str, Any]:
        return {"status": "verified" if path.exists() and path.stat().st_size > 0 else "insufficient_data", "exists": path.exists(), "path": str(path), "size_bytes": path.stat().st_size if path.exists() else 0}

    @staticmethod
    def _conflicts(prototype: dict[str, Any]) -> list[dict[str, str]]:
        conflicts: list[dict[str, str]] = []
        if prototype.get("qa_auto_uploadable") and prototype.get("blocking_findings"):
            conflicts.append({"claim": "prior QA marked auto-uploadable", "evidence": "current independent review contains unresolved blockers", "resolution": "do not publish"})
        return conflicts

    @staticmethod
    def _quality_average(agents: list[dict[str, Any]]) -> float:
        values = [value for agent in agents for value in agent["scores"].values() if isinstance(value, (int, float))]
        return round(sum(values) / len(values), 1) if values else 0.0

    @staticmethod
    def _word_count(value: Any) -> int:
        if isinstance(value, dict):
            value = value.get("text") or value.get("hook") or ""
        return len(re.findall(r"\b[\w'-]+\b", str(value or "")))

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _json_or_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _channel_markdown(self, report: dict[str, Any]) -> str:
        inventory = report["inventory"]
        prototype = report["prototype"]
        coordinator = report["quality_coordinator"]
        directions = "\n".join(
            f"- **{item['name']}**: originality {item['scores']['originality']}/10, fit {item['scores']['fit']}/10, automation {item['scores']['automation']}/10; selected={item['selected_for_prototype']}"
            for item in report["creative_directions"]
        )
        issues = "\n".join(f"- `{item['priority']}` {item['description']} Fix: {item['fix']}" for item in coordinator["deduplicated_issues"])
        evidence = "\n".join(f"- `{path}`" for path in prototype["evidence_paths"] + [prototype["final_video"], prototype.get("storyboard") or "storyboard unavailable"])
        return f"""# {report['channel_name']} - channel development report

Status: `{report['status']}`  
Readiness: `{report['readiness_decision']}`  
Publication performed: `false`

## 1. Channel characteristics
- Account: `{inventory['account_id']}`
- Configured name: {inventory['configured_name']}
- Live title returned by YouTube: {inventory.get('live_channel_title')}
- Niche: {inventory['niche']}
- Language: {inventory['language']}
- Market: {inventory['target_market']}
- Audience: {inventory['target_audience']}
- OAuth: `{inventory['oauth']['status']}`
- Own analytics conclusion: `{inventory['analytics']['strategic_conclusion']}` ({inventory['analytics']['own_performance_rows']} rows)

## 2. Creative directions
{directions}

## 3. Selected direction
**{report['selected_direction']['name']}** - {report['selected_direction']['idea']}

## 4. Narrative identity
{json.dumps(report['creative_identity']['narration'], ensure_ascii=False, indent=2)}

## 5. Visual identity
{json.dumps(report['creative_identity']['visual'], ensure_ascii=False, indent=2)}

## 6. Audio identity
{json.dumps(report['creative_identity']['audio'], ensure_ascii=False, indent=2)}

## 7. Recognition markers
{chr(10).join('- ' + item for item in report['creative_identity']['recognition_markers'])}

## 8. Formats
{chr(10).join(f"- {item['name']}: {item['duration']}; hook {item['hook']}; acceptance: {item['acceptance']}" for item in report['format_plans'])}

## 9. Editing manual
{json.dumps(report['editing_manual'], ensure_ascii=False, indent=2)}

## 10. Prototype
- Job: `{prototype['job_id']}`
- Title: {prototype['title']}
- MP4: `{prototype['final_video']}`
- Technical validation: `{prototype['media_probe']['status']}`
- Full decode: `{prototype['decode_check']['status']}`
- Audio analysis: `{prototype['audio_analysis']['status']}`
- Storyboard: `{prototype.get('storyboard')}`

## 11. Independent agents
{chr(10).join(f"- {agent['agent']}: `{agent['decision']}`; status `{agent['status']}`" for agent in report['quality_agents'])}

## 12. Quality coordinator
- Decision: `{coordinator['decision']}`
- P0: {coordinator['p0_count']}
- P1: {coordinator['p1_count']}
- Publication blocked: `{str(coordinator['publication_blocked']).lower()}`

{issues}

## 13. External review
- Claude: `pending_external_review`
- Kimi: `pending_external_review`

## 14. Iteration
Current iteration: 1/3. A new render is not approved until P0/P1 fixes and required model/download readiness are proven.

## 15. Cost and time
`insufficient_data` - no complete wall-clock and production-cost ledger exists for this historical prototype.

## 16. Decisions requiring owner action
{chr(10).join('- ' + item for item in report['policy_blockers']) if report['policy_blockers'] else '- Any upload, public setting change, or channel mutation requires explicit approval.'}

## 17. Evidence
{evidence}
"""

    @staticmethod
    def _aggregate_markdown(aggregate: dict[str, Any]) -> str:
        rows = "\n".join(
            f"| {item['channel']} | {item['selected_style']} | {item['prototype_status']} | {item['quality_score']}/10 | {item['main_risk']} | {item['automation']} | {item['readiness']} |"
            for item in aggregate["channel_reports"]
        )
        blockers = "\n".join(f"- {item}" for item in aggregate["global_blockers"])
        return f"""# Atnos YouTube channel development - aggregate report

Run: `{aggregate['run_id']}`  
Status: `{aggregate['status']}`  
Publication performed: `{str(aggregate['publication_performed']).lower()}`

| Channel | Selected style | Prototype status | Quality | Main risk | Automation | Readiness |
|---|---|---|---:|---|---|---|
{rows}

## Global blockers
{blockers}

## Evidence rule
Only statuses backed by a file, SQLite query, live connector probe, ffprobe, or full decode are marked `verified`. No prototype is approved for upload by this report.
"""
