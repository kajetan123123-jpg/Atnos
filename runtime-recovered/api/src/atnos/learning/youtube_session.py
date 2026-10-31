from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from atnos.core.config import Settings
from atnos.db.session import DatabaseManager
from atnos.intelligence.youtube_api import (
    build_youtube_data_client,
    extract_best_thumbnail,
    parse_duration_seconds,
    parse_int,
    resolve_channel,
)
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.services.learning_mode_guard import (
    classify_blocked_http_action,
    youtube_write_block_response,
)


ATNOS_LEARNING_INSTRUCTION = """Jesteś Atnosem. Działasz w LEARNING_MODE.
Nie wolno Ci publikować, uploadować, edytować ani usuwać niczego na YouTube.
Nie tworzysz finalnych materiałów. Uczysz się wzorców, budujesz wiedzę i zapisujesz ją lokalnie.

Twoje zadanie w tej sesji:
- zbierać legalnie i ostrożnie publiczne metadane referencyjne,
- analizować referencyjne kanały i materiały,
- porządkować wzorce dla 3 ścieżek: Sigma Squad, Dark Chronicles, Wealth Blueprint,
- tworzyć profile kanałów,
- tworzyć topic taxonomy,
- tworzyć hook library,
- tworzyć structure playbook,
- tworzyć editing, subtitle, audio, thumbnail i metadata playbook,
- tworzyć pre-publish QA i post-publish analytics plan,
- zapisać wiedzę do plików kanałowych i, jeśli możliwe, do Obsidiana,
- odróżniać fakt od obserwacji i hipotezy.

Zasady:
1. Używaj wyłącznie lokalnego przetwarzania.
2. Nie wysyłaj danych YouTube do usług zewnętrznych.
3. Nie pobieraj pełnych cudzych filmów.
4. Nie zapisuj pełnych transkryptów konkurencji.
5. Nie gromadź danych osobowych dzieci.
6. Każdy wniosek oznacz epistemicznie.
7. Jeżeli czegoś nie da się ustalić bezpiecznie, oznacz lack_of_data.
8. Zawsze preferuj abstrakcyjne wzorce nad kopiowanie konkretnej treści.
9. Dla kanału dziecięcego skupiaj się na bezpieczeństwie, czytelności i prostocie.
10. Dla kanału dark stories skupiaj się na napięciu, rytmie, narracji i atmosferze.
11. Dla kanału finance skupiaj się na jasności, wiarygodności, edukacji i unikaniu obietnic gwarantowanego zysku.

Masz stworzyć wiedzę użyteczną później do:
- wymyślania tematów,
- pisania hooków,
- pisania scenariuszy,
- planowania montażu,
- projektowania miniaturek,
- oceny jakości przed publikacją,
- uczenia się z analityki po publikacji.

Nie wykonujesz fine-tuningu.
Nie generujesz finalnego contentu.
Pracujesz na danych, regułach, rubrykach i wiedzy."""


REQUIRED_RECORD_FIELDS = (
    "url",
    "video_id",
    "channel_id",
    "publish_date",
    "duration",
    "title",
    "description",
    "hashtags",
    "public_stats",
    "thumbnail_url",
    "transcript_available",
    "detected_niche",
    "topic",
    "hook_type",
    "structure_summary",
    "tempo_estimate",
    "audio_profile",
    "thumbnail_profile",
    "potential_success_factors",
    "potential_issues",
    "confidence_level",
)
EPISTEMIC_LABELS = {"fact", "observation", "hypothesis", "lack_of_data"}
CHANNEL_FILES = (
    "channel_profile.md",
    "topic_taxonomy.md",
    "topic_scoring_rubric.md",
    "hook_library.md",
    "hook_scoring_rubric.md",
    "shorts_structure_playbook.md",
    "script_structure_playbook.md",
    "editing_playbook.md",
    "subtitle_playbook.md",
    "audio_playbook.md",
    "thumbnail_playbook.md",
    "thumbnail_scoring_rubric.md",
    "metadata_playbook.md",
    "pre_publish_qa.md",
    "post_publish_analytics_plan.md",
    "reference_analysis_summary.md",
    "content_safety_rules.md",
    "channel_knowledge.json",
    "reference_video_dataset.jsonl",
)
GLOBAL_FILES = (
    "run_report.md",
    "source_manifest.json",
    "resume_state.json",
    "tooling_audit.md",
    "missing_tools_and_data.md",
    "fine_tuning_readiness.md",
    "rag_readiness.md",
    "learning_mode_security_report.md",
    "youtube_write_block_test_report.md",
    "shared_youtube_principles.md",
)


@dataclass(frozen=True, slots=True)
class ReferenceSource:
    track: str
    profile: str
    url: str
    source_type: str = "channel"


REFERENCES = (
    ReferenceSource("Sigma Squad", "kids_story_short_form_reference_track", "https://www.youtube.com/@CatMemeclub-cat/shorts"),
    ReferenceSource("Sigma Squad", "kids_story_short_form_reference_track", "https://www.youtube.com/@TwiShorts"),
    ReferenceSource("Sigma Squad", "kids_story_short_form_reference_track", "https://www.youtube.com/@LooneyTalesYT"),
    ReferenceSource("Dark Chronicles", "dark_story_reference_track", "https://www.youtube.com/@MrNightmare"),
    ReferenceSource("Dark Chronicles", "dark_story_reference_track", "https://www.youtube.com/@MrBallen"),
    ReferenceSource("Dark Chronicles", "dark_story_reference_track", "https://www.youtube.com/@de_explainerr"),
    ReferenceSource("Dark Chronicles", "dark_story_reference_track", "https://www.youtube.com/@LetsReadPodcast"),
    ReferenceSource("Wealth Blueprint", "finance_education_reference_track", "https://www.youtube.com/@alux"),
    ReferenceSource("Wealth Blueprint", "finance_education_reference_track", "https://youtu.be/ymwFntb1BzY", "video"),
    ReferenceSource("Wealth Blueprint", "finance_education_reference_track", "https://youtu.be/IofLx2wjKY0", "video"),
    ReferenceSource("Wealth Blueprint", "finance_education_reference_track", "https://www.youtube.com/@marktilbury"),
)


class LearningSessionError(RuntimeError):
    pass


class StorageSafetyError(LearningSessionError):
    pass


class LocalOllama:
    """Minimalny klient wyłącznie do lokalnego Ollama HTTP API."""

    def __init__(self, base_url: str, *, timeout_s: float = 180.0) -> None:
        parsed = urlparse(base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LearningSessionError("Ollama must use a loopback address in LOCAL_PROCESSING_ONLY mode.")
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.text_model: str | None = None
        self.vision_model: str | None = None

    async def probe(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            names = [str(item.get("name") or "") for item in response.json().get("models", [])]
        self.vision_model = _first_matching(names, ("qwen3-vl", "qwen2.5vl", "llava", "gemma3"))
        self.text_model = _first_matching(names, ("qwen3:8b", "qwen3:14b", "qwen3", "gemma3", "deepseek-r1"))
        if self.text_model is None:
            raise LearningSessionError("Ollama responds locally but no supported text model is installed.")
        return {
            "status": "ready",
            "models": names,
            "text_model": self.text_model,
            "vision_model": self.vision_model,
        }

    async def json(self, prompt: str, *, image_path: Path | None = None) -> dict[str, Any]:
        model = self.vision_model if image_path is not None and self.vision_model else self.text_model
        if not model:
            raise LearningSessionError("Local Ollama model has not been selected by preflight.")
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if image_path is not None:
            message["images"] = [base64.b64encode(image_path.read_bytes()).decode("ascii")]
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": ATNOS_LEARNING_INSTRUCTION}, message],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0.15, "num_ctx": 16384},
        }
        async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
            response = await client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
        content = _ollama_message_text(response.json())
        return _parse_json_object(content)


class YouTubeLearningSession:
    """Bezpieczny executor autonomicznej sesji wiedzy, uruchamiany jako Atnos."""

    def __init__(
        self,
        settings: Settings,
        *,
        repo_path: Path,
        vault_path: Path,
        output_base: Path = Path("E:/Atnos/LearningRuns"),
        max_hours: float = 8.0,
        metadata_per_channel: int = 30,
        deep_per_channel: int = 5,
    ) -> None:
        self.settings = settings
        self.repo_path = repo_path.resolve()
        self.vault_path = vault_path.resolve()
        self.output_base = output_base.resolve()
        self.max_hours = min(max(float(max_hours), 0.1), 8.0)
        self.metadata_per_channel = min(max(metadata_per_channel, 20), 40)
        self.deep_per_channel = min(max(deep_per_channel, 5), 10)
        now = datetime.now().astimezone()
        self.run_id = f"learning_{now.strftime('%Y-%m-%dT%H-%M-%S%z')}"
        self.run_root = self.output_base / self.run_id
        self.started_at = now
        self.deadline = time.monotonic() + self.max_hours * 3600
        self.initial_output_bytes = 0
        self.database = DatabaseManager(settings)
        self.ollama = LocalOllama(settings.ollama_base_url)
        self.records: dict[str, list[dict[str, Any]]] = {track: [] for track in _tracks()}
        self.source_results: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.completed_steps: list[str] = []
        self.tooling: dict[str, Any] = {}
        self.youtube_source_mode = "youtube_data_api"
        self.current_phase = "created"
        self._checkpoint_lock = asyncio.Lock()
        self._last_checkpoint_monotonic = 0.0

    async def run(self) -> dict[str, Any]:
        self._validate_paths()
        self._create_layout()
        self._write_instruction()
        try:
            await self._preflight()
            self.current_phase = "metadata_collection"
            await self._collect_metadata()
            self.current_phase = "deep_local_analysis"
            await self._deep_analyze()
            self.current_phase = "knowledge_build"
            await self._build_knowledge()
            self.current_phase = "obsidian_writeback"
            self._write_obsidian()
            self.current_phase = "final_validation"
            validation = self._validate_outputs()
            self.completed_steps.append("final_validation")
            self.current_phase = "completed"
            await self._checkpoint(force=True, safe_to_resume=False)
            self._write_final_report(validation)
            return {
                "status": "completed",
                "run_id": self.run_id,
                "run_root": str(self.run_root),
                "records": {track: len(items) for track, items in self.records.items()},
                "validation": validation,
            }
        except Exception as exc:
            self.errors.append(_safe_error(exc))
            self.current_phase = "stopped"
            await self._checkpoint(force=True, safe_to_resume=True)
            self._append_report(f"STOP: {_safe_error(exc)}")
            raise
        finally:
            self.database.dispose()

    def _validate_paths(self) -> None:
        allowed_storage_drives = {"D:", "E:"}
        if self.repo_path.drive.upper() not in allowed_storage_drives or not (self.repo_path / ".git").exists():
            raise StorageSafetyError("Confirmed Atnos Git repository must be on an approved data drive (D: or E:).")
        if self.vault_path.drive.upper() not in allowed_storage_drives or not (self.vault_path / ".obsidian").exists():
            raise StorageSafetyError("Confirmed Obsidian vault must be on an approved data drive (D: or E:).")
        if self.output_base.drive.upper() not in allowed_storage_drives:
            raise StorageSafetyError("Learning output root must be on an approved data drive (D: or E:).")
        free_gb = _free_gb(self.output_base.anchor)
        if free_gb < 20.0:
            raise StorageSafetyError(f"Only {free_gb:.2f} GB is free; the hard minimum is 20 GB.")

    def _create_layout(self) -> None:
        self.run_root.mkdir(parents=True, exist_ok=False)
        (self.run_root / "thumbnails").mkdir()
        for track in _tracks():
            _channel_dir(self.run_root, track).mkdir()
        self.initial_output_bytes = _tree_size(self.run_root)
        self._atomic_json(self.run_root / "source_manifest.json", {"run_id": self.run_id, "sources": [], "policy": "metadata_first"})
        self._append_report("Sesja utworzona; brak operacji YouTube write.")

    def _write_instruction(self) -> None:
        self._atomic_text(self.run_root / "atnos_instruction.txt", ATNOS_LEARNING_INSTRUCTION + "\n")

    async def _preflight(self) -> None:
        self.current_phase = "preflight"
        required_flags = {
            "learning_mode": True,
            "youtube_write_block": True,
            "youtube_upload_enabled": False,
            "youtube_edit_enabled": False,
            "youtube_delete_enabled": False,
            "youtube_comment_enabled": False,
            "youtube_playlist_write_enabled": False,
            "youtube_scope_escalation_allowed": False,
            "external_ai_transfer_allowed": False,
            "cloud_llm_allowed": False,
            "local_processing_only": True,
        }
        wrong = [name for name, expected in required_flags.items() if bool(getattr(self.settings, name)) is not expected]
        if wrong:
            raise LearningSessionError(f"Learning Mode flags are unsafe: {', '.join(wrong)}")

        actions = {
            "upload": ("POST", "/api/production/jobs/test/upload-private"),
            "video_metadata_edit": ("PATCH", "/api/youtube/videos/test/metadata"),
            "thumbnail": ("POST", "/api/youtube/videos/test/thumbnail"),
            "video_delete": ("DELETE", "/api/youtube/videos/test/delete"),
            "comment": ("POST", "/api/youtube/comments"),
            "playlist": ("POST", "/api/youtube/playlists"),
            "subscription": ("POST", "/api/youtube/subscriptions"),
            "rating": ("POST", "/api/youtube/rating"),
            "scope_escalation": ("POST", "/oauth/youtube-studio/start"),
            "oauth_config": ("POST", "/oauth/youtube-studio/runtime-config"),
        }
        blocked = {
            name: bool(classify_blocked_http_action(method, path, self.settings))
            for name, (method, path) in actions.items()
        }
        contract = youtube_write_block_response(self.settings)
        if not all(blocked.values()) or not contract or contract.get("allowed") is not False:
            raise LearningSessionError("At least one YouTube write surface bypasses Learning Mode.")
        self._atomic_text(
            self.run_root / "youtube_write_block_test_report.md",
            _write_block_report(blocked, contract),
        )

        self.tooling["ollama"] = await self.ollama.probe()
        self.tooling["comfyui"] = await _http_probe(self.settings.comfyui_base_url, "/system_stats")
        f5_url = str(getattr(self.settings, "f5_tts_base_url", "http://127.0.0.1:7860"))
        f5_root = f5_url.split("/api/", 1)[0]
        self.tooling["f5_tts"] = await _http_probe(f5_root, "/")
        ffmpeg = resolve_ffmpeg()
        ffprobe = resolve_ffprobe(ffmpeg)
        self.tooling["ffmpeg"] = _binary_probe(ffmpeg, "-version")
        self.tooling["ffprobe"] = _binary_probe(ffprobe, "-version")
        self.tooling["whisper"] = {
            "status": "ready" if self.settings.whisper_cpp_exe and Path(self.settings.whisper_cpp_exe).exists() else "missing",
            "executable": str(self.settings.whisper_cpp_exe or ""),
            "model_ready": bool(self.settings.whisper_model_path and self.settings.whisper_model_path.exists()),
            "used_in_session": False,
            "reason": "No competitor transcripts or full audio are processed in metadata-first mode.",
        }
        self.database.initialize()
        try:
            with self.database.session_scope() as session:
                youtube = build_youtube_data_client(session, self.settings)
                response = await asyncio.to_thread(
                    lambda: youtube.channels().list(part="id", id="UC_x5XG1OV2P6uZZ5FSM9Ttw").execute()
                )
            self.tooling["youtube_data_api"] = {
                "status": "ready",
                "probe_items": len(response.get("items") or []),
                "collection_mode": "official_youtube_data_api",
            }
        except Exception as exc:
            fallback = await asyncio.to_thread(_probe_official_public_metadata, REFERENCES[0])
            if not fallback.get("ready"):
                raise LearningSessionError(
                    "YouTube Data API authorization failed and the official public metadata fallback is unavailable: "
                    f"{_safe_error(exc)}"
                ) from exc
            self.youtube_source_mode = "official_public_metadata"
            warning = (
                "YouTube Data API OAuth is unavailable; using official YouTube public pages and RSS read-only fallback. "
                "The fallback exposes fewer records and statistics."
            )
            self.warnings.append(warning)
            self.tooling["youtube_data_api"] = {
                "status": "degraded",
                "reason": _safe_error(exc),
                "fallback": "official_youtube_public_pages_and_rss",
                "fallback_probe": fallback,
            }
        self.tooling["youtube_analytics_api"] = {
            "status": "configured_not_queried",
            "reason": "Owner analytics are optional for the reference-learning pass and remain read-only.",
        }
        self.completed_steps.extend(("repo_verified", "vault_verified", "learning_mode_enabled", "write_block_tests_passed", "tooling_preflight_complete"))
        self._write_tool_reports()
        await self._checkpoint(force=True)

    async def _collect_metadata(self) -> None:
        with self.database.session_scope() as session:
            youtube = (
                build_youtube_data_client(session, self.settings)
                if self.youtube_source_mode == "youtube_data_api"
                else None
            )
            category_names = await asyncio.to_thread(_category_names, youtube) if youtube is not None else {}
            for source in REFERENCES:
                self._assert_can_continue()
                try:
                    if youtube is not None:
                        result = await asyncio.to_thread(
                            _fetch_reference_source,
                            youtube,
                            source,
                            self.metadata_per_channel,
                        )
                    else:
                        result = await asyncio.to_thread(
                            _fetch_reference_source_public,
                            source,
                            self.metadata_per_channel,
                        )
                    records = [self._base_record(item, source, category_names) for item in result["videos"]]
                    records = _dedupe_records(self.records[source.track], records)
                    self.records[source.track].extend(records)
                    self.source_results.append(
                        {
                            "track": source.track,
                            "url": source.url,
                            "source_type": source.source_type,
                            "channel_id": result.get("channel_id"),
                            "channel_title": result.get("channel_title"),
                            "metadata_items": len(records),
                            "status": "collected",
                            "collection_mode": result.get("collection_mode", self.youtube_source_mode),
                            "limitations": result.get("limitations", []),
                        }
                    )
                except Exception as exc:
                    safe = _safe_error(exc)
                    self.errors.append(f"{source.track}/{source.url}: {safe}")
                    self.source_results.append(
                        {"track": source.track, "url": source.url, "source_type": source.source_type, "status": "error", "error": safe}
                    )
                self._write_track_dataset(source.track)
                self._atomic_json(
                    self.run_root / "source_manifest.json",
                    {"run_id": self.run_id, "policy": "metadata_first", "sources": self.source_results},
                )
                await self._checkpoint()
        if not any(self.records.values()):
            raise LearningSessionError("YouTube Data API returned no usable public metadata.")
        self.completed_steps.append("metadata_collection")
        await self._checkpoint(force=True)

    def _base_record(self, item: dict[str, Any], source: ReferenceSource, categories: dict[str, str]) -> dict[str, Any]:
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        details = item.get("contentDetails") or {}
        video_id = str(item.get("id") or "").strip()
        title = _redact_personal_data(str(snippet.get("title") or "").strip())
        description = _redact_personal_data(str(snippet.get("description") or "").strip())[:2500]
        category_id = str(snippet.get("categoryId") or "").strip() or None
        missing = {"value": "Brak legalnego sygnału w trybie metadata-first.", "label_type": "lack_of_data"}
        record = {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "video_id": video_id,
            "channel_id": str(snippet.get("channelId") or "").strip() or None,
            "publish_date": str(snippet.get("publishedAt") or "").strip() or None,
            "duration": str(details.get("duration") or "").strip() or None,
            "title": title,
            "description": description,
            "hashtags": list(dict.fromkeys(re.findall(r"#[\w-]+", f"{title} {description}", flags=re.UNICODE))),
            "public_stats": {
                "view_count": parse_int(stats.get("viewCount")),
                "like_count": parse_int(stats.get("likeCount")),
                "comment_count": parse_int(stats.get("commentCount")),
            },
            "thumbnail_url": extract_best_thumbnail(snippet),
            "transcript_available": False,
            "detected_niche": source.profile,
            "topic": dict(missing),
            "hook_type": dict(missing),
            "structure_summary": dict(missing),
            "tempo_estimate": dict(missing),
            "audio_profile": dict(missing),
            "thumbnail_profile": dict(missing),
            "potential_success_factors": [],
            "potential_issues": [],
            "confidence_level": 0.25,
            "api_category_id": category_id,
            "api_category_name": categories.get(category_id or ""),
            "source_track": source.track,
            "source_type": (
                "official_youtube_data_api_metadata"
                if self.youtube_source_mode == "youtube_data_api"
                else "official_youtube_public_metadata"
            ),
            "evidence_labels": ["fact", "lack_of_data"],
            "record_status": "metadata_collected",
            "notes": "No transcript or competitor audiovisual copy stored.",
            "created_at": datetime.now().astimezone().isoformat(),
            "analyzer_version": "atnos-learning-pipeline-v1",
        }
        return record

    async def _deep_analyze(self) -> None:
        for source in REFERENCES:
            candidates = [item for item in self.records[source.track] if item.get("channel_id") in _source_channel_ids(self.source_results, source)]
            if source.source_type == "video":
                candidates = [item for item in self.records[source.track] if item["url"].split("v=")[-1] == _video_id(source.url)]
            selected = _select_deep_sample(candidates, self.deep_per_channel)
            for record in selected:
                self._assert_can_continue()
                if record.get("record_status") == "complete":
                    continue
                thumbnail = await self._download_thumbnail(record)
                try:
                    analysis = await self.ollama.json(
                        _per_video_prompt(record, source.track),
                        image_path=thumbnail,
                    )
                    _apply_local_analysis(record, analysis, vision_used=thumbnail is not None and self.ollama.vision_model is not None)
                except Exception as exc:
                    self.warnings.append(f"Local analysis {record['video_id']}: {_safe_error(exc)}")
                    record["record_status"] = "analysis_failed"
                    record["notes"] = f"Local model failed; metadata retained. {_safe_error(exc)}"
                self._write_track_dataset(source.track)
                await self._checkpoint()
        self.completed_steps.append("deep_local_analysis")
        await self._checkpoint(force=True)

    async def _download_thumbnail(self, record: dict[str, Any]) -> Path | None:
        url = str(record.get("thumbnail_url") or "")
        if not _allowed_thumbnail_url(url):
            return None
        target = self.run_root / "thumbnails" / _slug(str(record["source_track"])) / f"{record['video_id']}.jpg"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 1024:
            return target
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/") or len(response.content) > 8 * 1024 * 1024:
                return None
            _atomic_bytes(target, response.content)
        self._assert_storage_limits()
        return target

    async def _build_knowledge(self) -> None:
        track_knowledge: dict[str, dict[str, Any]] = {}
        for track, records in self.records.items():
            compact = [_compact_record(record) for record in records[:40]]
            payload = await self.ollama.json(_track_knowledge_prompt(track, compact))
            normalized = _normalize_knowledge_payload(track, payload, len(records))
            track_knowledge[track] = normalized
            self._write_channel_knowledge(track, normalized)
            await self._checkpoint(force=True)

        shared = await self.ollama.json(_shared_knowledge_prompt(track_knowledge))
        self._atomic_text(
            self.run_root / "shared_youtube_principles.md",
            _markdown_document("Wspólne zasady YouTube Shorts", shared, channel="shared"),
        )
        self._atomic_text(
            self.run_root / "fine_tuning_readiness.md",
            "# Gotowość fine-tuningu\n\nStatus: **nie wykonywać**. Ta sesja buduje lokalne dane RAG, rubryki i wiedzę; nie powstał dataset pełnych treści ani zgoda na fine-tuning.\n",
        )
        counts = {track: len(records) for track, records in self.records.items()}
        self._atomic_text(
            self.run_root / "rag_readiness.md",
            "# Gotowość RAG\n\n"
            f"Rekordy JSONL: `{json.dumps(counts, ensure_ascii=False)}`. "
            "Każdy rekord ma source_track i etykiety epistemiczne. Dane są gotowe do lokalnego indeksowania po akceptacji właściciela.\n",
        )
        self.completed_steps.extend(("channel_knowledge_built", "rag_sources_prepared"))
        await self._checkpoint(force=True)

    def _write_channel_knowledge(self, track: str, knowledge: dict[str, Any]) -> None:
        directory = _channel_dir(self.run_root, track)
        mapping = {
            "channel_profile.md": "channel_profile",
            "topic_taxonomy.md": "topic_taxonomy",
            "topic_scoring_rubric.md": "topic_scoring_rubric",
            "hook_library.md": "hook_library",
            "hook_scoring_rubric.md": "hook_scoring_rubric",
            "shorts_structure_playbook.md": "shorts_structure_playbook",
            "script_structure_playbook.md": "script_structure_playbook",
            "editing_playbook.md": "editing_playbook",
            "subtitle_playbook.md": "subtitle_playbook",
            "audio_playbook.md": "audio_playbook",
            "thumbnail_playbook.md": "thumbnail_playbook",
            "thumbnail_scoring_rubric.md": "thumbnail_scoring_rubric",
            "metadata_playbook.md": "metadata_playbook",
            "pre_publish_qa.md": "pre_publish_qa",
            "post_publish_analytics_plan.md": "post_publish_analytics_plan",
            "reference_analysis_summary.md": "reference_analysis_summary",
            "content_safety_rules.md": "content_safety_rules",
        }
        for filename, key in mapping.items():
            self._atomic_text(directory / filename, _markdown_document(key.replace("_", " ").title(), knowledge.get(key), channel=track))
        self._atomic_json(directory / "channel_knowledge.json", knowledge)
        self._write_track_dataset(track)

    def _write_obsidian(self) -> None:
        root = self.vault_path / "Atnos Knowledge Base"
        folders = (
            "00 Run Logs",
            "01 Shared YouTube Principles",
            "02 Sigma Squad/Topics", "02 Sigma Squad/Hooks", "02 Sigma Squad/Scripts", "02 Sigma Squad/Editing",
            "02 Sigma Squad/Audio", "02 Sigma Squad/Thumbnails", "02 Sigma Squad/Metadata", "02 Sigma Squad/Quality Control", "02 Sigma Squad/Reference Analysis",
            "03 Dark Chronicles/Topics", "03 Dark Chronicles/Hooks", "03 Dark Chronicles/Scripts", "03 Dark Chronicles/Editing",
            "03 Dark Chronicles/Audio", "03 Dark Chronicles/Thumbnails", "03 Dark Chronicles/Metadata", "03 Dark Chronicles/Quality Control", "03 Dark Chronicles/Reference Analysis",
            "04 Wealth Blueprint/Topics", "04 Wealth Blueprint/Hooks", "04 Wealth Blueprint/Scripts", "04 Wealth Blueprint/Editing",
            "04 Wealth Blueprint/Audio", "04 Wealth Blueprint/Thumbnails", "04 Wealth Blueprint/Metadata", "04 Wealth Blueprint/Quality Control", "04 Wealth Blueprint/Reference Analysis",
            "05 Evaluation Rubrics", "06 Data Schemas", "07 Tooling Audit", "08 Safety and Policy", "09 Next Steps",
        )
        for folder in folders:
            (root / folder).mkdir(parents=True, exist_ok=True)
        suffix = self.started_at.strftime("%Y-%m-%d")
        _copy_versioned(self.run_root / "shared_youtube_principles.md", root / "01 Shared YouTube Principles", suffix)
        _copy_versioned(self.run_root / "run_report.md", root / "00 Run Logs", suffix)
        _copy_versioned(self.run_root / "tooling_audit.md", root / "07 Tooling Audit", suffix)
        _copy_versioned(self.run_root / "learning_mode_security_report.md", root / "08 Safety and Policy", suffix)
        destinations = {
            "topic_taxonomy.md": "Topics", "topic_scoring_rubric.md": "Topics",
            "hook_library.md": "Hooks", "hook_scoring_rubric.md": "Hooks",
            "shorts_structure_playbook.md": "Scripts", "script_structure_playbook.md": "Scripts",
            "editing_playbook.md": "Editing", "subtitle_playbook.md": "Editing",
            "audio_playbook.md": "Audio", "thumbnail_playbook.md": "Thumbnails", "thumbnail_scoring_rubric.md": "Thumbnails",
            "metadata_playbook.md": "Metadata", "pre_publish_qa.md": "Quality Control", "post_publish_analytics_plan.md": "Quality Control",
            "content_safety_rules.md": "Quality Control", "channel_profile.md": "Reference Analysis", "reference_analysis_summary.md": "Reference Analysis",
        }
        prefixes = {"Sigma Squad": "02 Sigma Squad", "Dark Chronicles": "03 Dark Chronicles", "Wealth Blueprint": "04 Wealth Blueprint"}
        for track, prefix in prefixes.items():
            source_dir = _channel_dir(self.run_root, track)
            for filename, folder in destinations.items():
                _copy_versioned(source_dir / filename, root / prefix / folder, suffix)
        self.completed_steps.append("obsidian_writeback")

    def _write_tool_reports(self) -> None:
        lines = ["# Audyt narzędzi", ""]
        missing: list[str] = []
        for name, result in self.tooling.items():
            status = str(result.get("status") or "unknown")
            lines.append(f"- **{name}**: `{status}`")
            if status not in {"ready", "configured_not_queried"}:
                missing.append(f"- {name}: {result.get('reason') or status}")
        self._atomic_text(self.run_root / "tooling_audit.md", "\n".join(lines) + "\n")
        self._atomic_text(
            self.run_root / "missing_tools_and_data.md",
            "# Brakujące narzędzia i dane\n\n" + ("\n".join(missing) if missing else "Brak krytycznych braków dla sesji metadata-first.") + "\n",
        )
        self._atomic_text(
            self.run_root / "learning_mode_security_report.md",
            _security_report(self.settings, self.repo_path, self.vault_path, self.output_base),
        )

    def _write_track_dataset(self, track: str) -> None:
        path = _channel_dir(self.run_root, track) / "reference_video_dataset.jsonl"
        text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in self.records[track])
        self._atomic_text(path, text)

    async def _checkpoint(self, *, force: bool = False, safe_to_resume: bool = True) -> None:
        if not force and time.monotonic() - self._last_checkpoint_monotonic < 30 * 60:
            return
        async with self._checkpoint_lock:
            self._assert_storage_limits()
            state = {
                "run_id": self.run_id,
                "started_at": self.started_at.isoformat(),
                "last_checkpoint_at": datetime.now().astimezone().isoformat(),
                "learning_mode": True,
                "youtube_write_block_verified": True,
                "repo_path": str(self.repo_path),
                "vault_path": str(self.vault_path),
                "active_branch": _git_branch(self.repo_path),
                "storage": {
                    "allowed_roots": ["E:\\"],
                    "current_output_root": str(self.run_root),
                    "free_space_gb_estimate": _free_gb(self.output_base.anchor),
                    "hard_stop_below_gb": 20,
                    "session_size_gb": round((_tree_size(self.run_root) - self.initial_output_bytes) / 1024**3, 4),
                },
                "current_phase": self.current_phase,
                "channels": {
                    track: {
                        "status": "complete" if "channel_knowledge_built" in self.completed_steps else self.current_phase,
                        "reference_channels_discovered": sum(1 for item in self.source_results if item.get("track") == track and item.get("status") == "collected"),
                        "metadata_items_collected": len(records),
                        "deep_analyses_completed": sum(1 for item in records if item.get("record_status") == "complete"),
                        "last_processed_video_id": records[-1]["video_id"] if records else None,
                        "last_output_file": str(_channel_dir(self.run_root, track) / "reference_video_dataset.jsonl") if records else None,
                    }
                    for track, records in self.records.items()
                },
                "completed_steps": list(dict.fromkeys(self.completed_steps)),
                "pending_steps": _pending_steps(self.completed_steps),
                "errors": self.errors[-50:],
                "warnings": self.warnings[-50:],
                "safe_to_resume": safe_to_resume,
            }
            self._atomic_json(self.run_root / "resume_state.json", state)
            self._append_report(
                f"Checkpoint: phase={self.current_phase}; records={sum(len(items) for items in self.records.values())}; "
                f"free={state['storage']['free_space_gb_estimate']:.2f} GB; errors={len(self.errors)}."
            )
            self._last_checkpoint_monotonic = time.monotonic()

    def _validate_outputs(self) -> dict[str, Any]:
        missing = [name for name in GLOBAL_FILES if not (self.run_root / name).exists()]
        for track in _tracks():
            missing.extend(f"{_slug(track)}/{name}" for name in CHANNEL_FILES if not (_channel_dir(self.run_root, track) / name).exists())
        invalid_records: list[str] = []
        for track, records in self.records.items():
            for record in records:
                absent = [name for name in REQUIRED_RECORD_FIELDS if name not in record]
                if absent or not _valid_epistemic_record(record):
                    invalid_records.append(f"{track}:{record.get('video_id')}:missing={absent}")
        secret_hits = _scan_for_secret_patterns(self.run_root)
        return {
            "passed": not missing and not invalid_records and not secret_hits,
            "missing_files": missing,
            "invalid_records": invalid_records,
            "secret_pattern_hits": secret_hits,
            "record_counts": {track: len(items) for track, items in self.records.items()},
            "output_size_bytes": _tree_size(self.run_root),
            "free_space_gb": _free_gb(self.output_base.anchor),
        }

    def _write_final_report(self, validation: dict[str, Any]) -> None:
        end = datetime.now().astimezone()
        duration = end - self.started_at
        lines = [
            "# Raport końcowy sesji Learning Mode",
            "",
            f"- Start: {self.started_at.isoformat()}",
            f"- Koniec: {end.isoformat()}",
            f"- Czas: {duration}",
            f"- Repo: `{self.repo_path}`",
            f"- Branch: `{_git_branch(self.repo_path)}`",
            f"- Vault: `{self.vault_path}`",
            f"- Output: `{self.run_root}`",
            f"- LEARNING_MODE: `{self.settings.learning_mode}`",
            f"- YouTube write block: `{self.settings.youtube_write_block}`",
            f"- Walidacja: `{'PASS' if validation['passed'] else 'FAIL'}`",
            "",
            "## Praca wykonana przez Atnosa",
        ]
        for track, records in self.records.items():
            deep = sum(1 for item in records if item.get("record_status") == "complete")
            lines.append(f"- {track}: {len(records)} rekordów metadanych, {deep} analiz lokalnych.")
        lines.extend(
            [
                "- Atnos wygenerował lokalnie profile, rubryki, playbooki, JSONL i źródła RAG.",
                "",
                "## Praca operatora Codex",
                "- Przygotowanie guardów, ścieżek, limitów, checkpointów i testów bezpieczeństwa.",
                "- Walidacja kompletności, schematu, sekretów i braku operacji write.",
                "- Codex nie tworzył merytorycznych wniosków kanałowych.",
                "",
                "## Ograniczenia i braki",
                "- Nie pobierano pełnych filmów ani transkryptów konkurencji.",
                "- Tempo i audio bez legalnego sygnału są oznaczone jako lack_of_data.",
                *[f"- {item}" for item in self.warnings[-20:]],
                *[f"- Błąd: {item}" for item in self.errors[-20:]],
                "",
                "## Gotowość",
                f"- Pliki brakujące: {len(validation['missing_files'])}",
                f"- Rekordy nieprawidłowe: {len(validation['invalid_records'])}",
                f"- Trafienia wzorców sekretów: {len(validation['secret_pattern_hits'])}",
                "- Brak automatycznego commita; wymagana walidacja właściciela.",
            ]
        )
        self._atomic_text(self.run_root / "final_report.md", "\n".join(lines) + "\n")

    def _append_report(self, message: str) -> None:
        path = self.run_root / "run_report.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else "# Run report\n\n"
        self._atomic_text(path, existing + f"- {datetime.now().astimezone().isoformat()} — {message}\n")

    def _atomic_text(self, path: Path, value: str) -> None:
        _ensure_allowed_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, path)

    def _atomic_json(self, path: Path, value: Any) -> None:
        self._atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def _assert_can_continue(self) -> None:
        if time.monotonic() >= self.deadline:
            raise LearningSessionError("Maximum session duration reached.")
        self._assert_storage_limits()

    def _assert_storage_limits(self) -> None:
        free = _free_gb(self.output_base.anchor)
        new_gb = (_tree_size(self.run_root) - self.initial_output_bytes) / 1024**3 if self.run_root.exists() else 0.0
        if free < 20.0:
            raise StorageSafetyError(f"Free space dropped below 20 GB ({free:.2f} GB).")
        if new_gb > 70.0:
            raise StorageSafetyError(f"Session exceeded the 70 GB hard limit ({new_gb:.2f} GB).")
        if new_gb > 50.0 and "preferred_50gb_limit_exceeded" not in self.warnings:
            self.warnings.append("preferred_50gb_limit_exceeded")


def _fetch_reference_source(youtube: Any, source: ReferenceSource, limit: int) -> dict[str, Any]:
    if source.source_type == "video":
        video_id = _video_id(source.url)
        response = youtube.videos().list(part="snippet,statistics,contentDetails", id=video_id).execute()
        items = response.get("items") or []
        return {
            "channel_id": ((items[0].get("snippet") or {}).get("channelId")) if items else None,
            "channel_title": ((items[0].get("snippet") or {}).get("channelTitle")) if items else None,
            "videos": items,
            "limitations": ["Single explicitly supplied reference video."],
        }
    channel = resolve_channel(youtube, source.url)
    if not channel:
        raise LearningSessionError(f"Reference channel could not be resolved: {source.url}")
    channel_id = str(channel.get("id") or "")
    uploads = (((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
    if not uploads:
        raise LearningSessionError(f"Reference channel has no public uploads playlist: {source.url}")
    ids: list[str] = []
    token: str | None = None
    while len(ids) < max(limit * 3, 50):
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads,
            maxResults=min(50, max(limit * 3, 50) - len(ids)),
            pageToken=token,
        ).execute()
        ids.extend(str((item.get("contentDetails") or {}).get("videoId") or "") for item in response.get("items") or [])
        token = response.get("nextPageToken")
        if not token:
            break
    items: list[dict[str, Any]] = []
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(ids[start : start + 50]),
            maxResults=50,
        ).execute()
        items.extend(response.get("items") or [])
    short_items = [item for item in items if 0 < (parse_duration_seconds(str((item.get("contentDetails") or {}).get("duration") or "")) or 0) <= 180]
    selected = short_items[:limit]
    limitations: list[str] = []
    if len(selected) < 20:
        limitations.append(f"Only {len(selected)} public videos <=180 seconds were found in the recent uploads window.")
    return {
        "channel_id": channel_id,
        "channel_title": str((channel.get("snippet") or {}).get("title") or ""),
        "videos": selected,
        "limitations": limitations,
        "collection_mode": "official_youtube_data_api",
    }


def _probe_official_public_metadata(source: ReferenceSource) -> dict[str, Any]:
    """Checks the documented public YouTube surface without OAuth or an API key."""
    try:
        result = _fetch_reference_source_public(source, 1)
    except Exception as exc:
        return {"ready": False, "reason": _safe_error(exc)}
    return {
        "ready": bool(result.get("videos")),
        "mode": "official_youtube_public_pages_and_rss",
        "items": len(result.get("videos") or []),
    }


def _fetch_reference_source_public(source: ReferenceSource, limit: int) -> dict[str, Any]:
    """Reads only official public YouTube pages, oEmbed and channel RSS."""
    headers = {
        "User-Agent": "AtnosLearningMode/1.0 (+local metadata audit; no video download)",
        "Accept-Language": "en-US,en;q=0.8",
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, trust_env=False, headers=headers) as client:
        if source.source_type == "video":
            video_id = _video_id(source.url)
            item = _public_video_item(client, video_id)
            return {
                "channel_id": str((item.get("snippet") or {}).get("channelId") or "") or None,
                "channel_title": str((item.get("snippet") or {}).get("channelTitle") or "") or None,
                "videos": [item],
                "limitations": [
                    "Single explicitly supplied reference video.",
                    "Public oEmbed/watch-page metadata does not expose likes or comments.",
                ],
                "collection_mode": "official_youtube_public_page",
            }

        page_text, channel_id = _public_channel_page(client, source.url)
        page_items, page_channel_title = _parse_public_shorts_page(page_text, channel_id, source.url)
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        videos: list[dict[str, Any]] = []
        channel_title: str | None = None
        feed_available = False
        try:
            response = client.get(feed_url)
            response.raise_for_status()
            videos, channel_title = _parse_youtube_rss(response.content)
            feed_available = True
        except httpx.HTTPError:
            pass
        known_ids = {str(item.get("id") or "") for item in videos}
        for item in page_items:
            video_id = str(item.get("id") or "")
            if video_id and video_id not in known_ids:
                known_ids.add(video_id)
                videos.append(item)
        enriched = videos[:limit]
        channel_title = channel_title or page_channel_title
        return {
            "channel_id": channel_id,
            "channel_title": channel_title,
            "videos": enriched,
            "limitations": [
                f"Official public YouTube surfaces exposed {len(enriched)} verified channel items; the requested range was {limit}.",
                f"Official RSS availability: {feed_available}.",
                "Likes, comments and category metadata may be unavailable without YouTube Data API authorization.",
                "Public page/RSS data is recent-first and does not provide a popularity-ranked archive.",
            ],
            "collection_mode": "official_youtube_public_pages_and_rss",
        }


def _resolve_public_channel_id(client: httpx.Client, channel_url: str) -> str:
    _, channel_id = _public_channel_page(client, channel_url)
    return channel_id


def _public_channel_page(client: httpx.Client, channel_url: str) -> tuple[str, str]:
    shorts_url = channel_url.rstrip("/") if "/shorts" in urlparse(channel_url).path else channel_url.rstrip("/") + "/shorts"
    consent_safe_url = shorts_url + ("&" if "?" in shorts_url else "?") + "ucbcb=1"
    response = client.get(consent_safe_url)
    response.raise_for_status()
    # YouTube does not always return a charset header. HTTPX then falls back to
    # a legacy encoding, which corrupts non-ASCII titles despite UTF-8 content.
    page_text = response.content.decode("utf-8", errors="replace")
    patterns = (
        r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"',
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"',
        r'"browseId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"',
    )
    for pattern in patterns:
        match = re.search(pattern, page_text)
        if match:
            return page_text, match.group(1)
    raise LearningSessionError(f"Official YouTube page did not expose a channel id: {channel_url}")


def _extract_public_page_video_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', text)))


def _parse_public_shorts_page(
    text: str,
    channel_id: str,
    channel_url: str,
) -> tuple[list[dict[str, Any]], str | None]:
    title_match = re.search(r"<title>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    channel_title = (
        html_lib.unescape(re.sub(r"\s*-\s*YouTube\s*$", "", title_match.group(1))).strip()
        if title_match
        else None
    )
    pattern = re.compile(
        r'"shortsLockupViewModel"\s*:\s*\{'
        r'.*?"accessibilityText"\s*:\s*"((?:\\.|[^"\\])*)"'
        r'.*?"reelWatchEndpoint"\s*:\s*\{'
        r'.*?"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"'
        r'.*?"thumbnail"\s*:\s*\{.*?"url"\s*:\s*"((?:\\.|[^"\\])*)"',
        flags=re.DOTALL,
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in pattern.finditer(text):
        accessibility = _decode_json_string(match.group(1))
        video_id = match.group(2)
        thumbnail_url = _decode_json_string(match.group(3))
        if video_id in seen:
            continue
        seen.add(video_id)
        public_title = html_lib.unescape(accessibility.rsplit(",", 1)[0]).strip() or f"Public Short {video_id}"
        items.append(
            {
                "id": video_id,
                "snippet": {
                    "title": public_title,
                    "description": "",
                    "publishedAt": None,
                    "channelId": channel_id,
                    "channelTitle": channel_title or _youtube_handle(channel_url),
                    "thumbnails": {"high": {"url": thumbnail_url}},
                },
                "statistics": {},
                "contentDetails": {},
            }
        )
    return items, channel_title


def _youtube_handle(url: str) -> str | None:
    match = re.search(r"/@([^/?#]+)", url)
    return match.group(1) if match else None


def _parse_youtube_rss(content: bytes) -> tuple[list[dict[str, Any]], str | None]:
    root = ET.fromstring(content)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    channel_title = root.findtext("atom:title", default="", namespaces=namespaces) or None
    items: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", namespaces):
        video_id = entry.findtext("yt:videoId", default="", namespaces=namespaces).strip()
        channel_id = entry.findtext("yt:channelId", default="", namespaces=namespaces).strip()
        group = entry.find("media:group", namespaces)
        thumbnail = group.find("media:thumbnail", namespaces) if group is not None else None
        description = group.findtext("media:description", default="", namespaces=namespaces) if group is not None else ""
        community = group.find("media:community", namespaces) if group is not None else None
        statistics = community.find("media:statistics", namespaces) if community is not None else None
        views = statistics.attrib.get("views") if statistics is not None else None
        if not video_id:
            continue
        items.append(
            {
                "id": video_id,
                "snippet": {
                    "title": entry.findtext("atom:title", default="", namespaces=namespaces),
                    "description": description,
                    "publishedAt": entry.findtext("atom:published", default="", namespaces=namespaces),
                    "channelId": channel_id or None,
                    "channelTitle": channel_title,
                    "thumbnails": {"high": {"url": thumbnail.attrib.get("url")}} if thumbnail is not None else {},
                },
                "statistics": {"viewCount": views} if views is not None else {},
                "contentDetails": {},
            }
        )
    return items, channel_title


def _public_video_item(client: httpx.Client, video_id: str) -> dict[str, Any]:
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    oembed = client.get("https://www.youtube.com/oembed", params={"url": watch_url, "format": "json"})
    oembed.raise_for_status()
    basic = oembed.json()
    return {
        "id": video_id,
        "snippet": {
            "title": str(basic.get("title") or ""),
            "description": "",
            "publishedAt": None,
            "channelId": None,
            "channelTitle": str(basic.get("author_name") or ""),
            "authorUrl": str(basic.get("author_url") or ""),
            "thumbnails": {"high": {"url": str(basic.get("thumbnail_url") or "")}},
        },
        "statistics": {},
        "contentDetails": {},
    }


def _extract_json_string(text: str, key: str) -> str | None:
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    if not match:
        return None
    try:
        return _decode_json_string(match.group(1))
    except json.JSONDecodeError:
        return match.group(1)


def _decode_json_string(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except json.JSONDecodeError:
        return value


def _ollama_message_text(payload: dict[str, Any]) -> str:
    """Returns the model JSON without persisting or exposing hidden reasoning."""
    message = payload.get("message") or {}
    content = str(message.get("content") or "").strip()
    if content:
        return content
    # Some local Qwen vision builds return structured output in `thinking`
    # even when `think=false`. It is parsed in memory and never written raw.
    return str(message.get("thinking") or "").strip()


def _seconds_to_iso_duration(value: str | None) -> str | None:
    try:
        seconds = max(int(value or 0), 0)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = "PT"
    if hours:
        parts += f"{hours}H"
    if minutes:
        parts += f"{minutes}M"
    if seconds or parts == "PT":
        parts += f"{seconds}S"
    return parts


def _category_names(youtube: Any) -> dict[str, str]:
    try:
        response = youtube.videoCategories().list(part="snippet", regionCode="US").execute()
    except Exception:
        return {}
    return {str(item.get("id") or ""): str((item.get("snippet") or {}).get("title") or "") for item in response.get("items") or []}


def _per_video_prompt(record: dict[str, Any], track: str) -> str:
    compact = {
        "title": record["title"],
        "description": record["description"][:1200],
        "duration": record["duration"],
        "public_stats": record["public_stats"],
        "track": track,
    }
    return f"""Przeanalizuj WYŁĄCZNIE metadane i dołączoną miniaturę publicznego materiału. Nie zakładaj, że widziałeś film.
Zwróć tylko JSON z polami:
topic, hook_type, structure_summary, tempo_estimate, audio_profile, thumbnail_profile,
potential_success_factors, potential_issues, confidence_level.
Każde z pierwszych sześciu pól ma format {{"value": "...", "label_type": "observation|hypothesis|lack_of_data"}}.
Listy zawierają obiekty value + label_type. Tempo, audio i struktura filmu mają być lack_of_data, jeżeli metadane/miniatura nie są wystarczającym dowodem.
Nie identyfikuj osób ani dzieci. Nie kopiuj tekstu konkurencji. Dane: {json.dumps(compact, ensure_ascii=False)}"""


def _track_knowledge_prompt(track: str, records: list[dict[str, Any]]) -> str:
    keys = [Path(name).stem for name in CHANNEL_FILES if name.endswith(".md")]
    return f"""Na podstawie dostarczonych rekordów Atnosa z etykietami epistemicznymi zbuduj abstrakcyjną bazę wiedzy dla {track}.
Nie twórz finalnych filmów, tytułów publikacyjnych ani skryptów. Nie kopiuj tytułów źródłowych.
Każda zasada ma zawierać status fact/observation/hypothesis/lack_of_data i krótkie uzasadnienie źródłowe.
Zwróć wyłącznie JSON. Klucze obowiązkowe: {json.dumps(keys)}.
Każda wartość może być listą obiektów albo obiektem sekcji. Uwzględnij ograniczenia metadata-first.
Rekordy: {json.dumps(records, ensure_ascii=False)}"""


def _shared_knowledge_prompt(knowledge: dict[str, dict[str, Any]]) -> str:
    compact = {track: {key: value for key, value in data.items() if key in {"channel_profile", "pre_publish_qa", "content_safety_rules"}} for track, data in knowledge.items()}
    return "Zsyntetyzuj wyłącznie wspólne, abstrakcyjne zasady jakości Shorts. Zwróć JSON z sekcjami principles, shared_qa, limitations. Każdy punkt musi mieć epistemic_status. Dane: " + json.dumps(compact, ensure_ascii=False)


def _apply_local_analysis(record: dict[str, Any], payload: dict[str, Any], *, vision_used: bool) -> None:
    for key in ("topic", "hook_type", "structure_summary", "tempo_estimate", "audio_profile", "thumbnail_profile"):
        record[key] = _epistemic_value(payload.get(key), default_label="observation" if key in {"topic", "hook_type", "thumbnail_profile"} else "lack_of_data")
    record["potential_success_factors"] = _epistemic_list(payload.get("potential_success_factors"))
    record["potential_issues"] = _epistemic_list(payload.get("potential_issues"), default_label="hypothesis")
    try:
        confidence = float(payload.get("confidence_level", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    record["confidence_level"] = min(max(confidence, 0.0), 0.85)
    record["source_type"] = "official_metadata_plus_local_thumbnail_analysis" if vision_used else "official_metadata_plus_local_text_analysis"
    record["evidence_labels"] = sorted(_record_labels(record))
    record["record_status"] = "complete"
    record["notes"] = "Local Atnos analysis only; no full video or transcript was downloaded."


def _normalize_knowledge_payload(track: str, payload: dict[str, Any], record_count: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "channel": track,
        "record_count": record_count,
        "generated_by": "Atnos local Ollama",
        "generated_at": datetime.now().astimezone().isoformat(),
        "limitations": ["metadata-first", "no full competitor video", "no competitor transcript"],
    }
    for name in CHANNEL_FILES:
        if name.endswith(".md"):
            key = Path(name).stem
            result[key] = payload.get(key) or [{"value": "Brak wystarczających danych.", "epistemic_status": "lack_of_data"}]
    return result


def _markdown_document(title: str, content: Any, *, channel: str) -> str:
    frontmatter = (
        "---\n"
        f"title: \"{title}\"\nchannel: \"{channel}\"\ncategory: learning\n"
        f"sources: [official_youtube_metadata]\ndate: {datetime.now().date().isoformat()}\n"
        "confidence: bounded\nepistemic_status: mixed\n"
        "notes: \"Generated locally by Atnos in LEARNING_MODE\"\n"
        "practical_application: \"Future controlled Shorts planning and QA\"\n"
        "limitations: \"Metadata-first; no full videos or transcripts\"\n---\n\n"
    )
    lines = [frontmatter, f"# {title}", ""]
    if isinstance(content, dict):
        for key, value in content.items():
            lines.extend((f"## {str(key).replace('_', ' ').title()}", _markdown_value(value), ""))
    else:
        lines.append(_markdown_value(content))
    return "\n".join(lines).strip() + "\n"


def _markdown_value(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {_markdown_value(item)}" for item in value) or "- Brak danych."
    if isinstance(value, dict):
        label = value.get("epistemic_status") or value.get("label_type")
        text = value.get("value")
        if text is not None:
            return f"**[{label or 'lack_of_data'}]** {text}"
        return "; ".join(f"{key}: {_markdown_value(item)}" for key, item in value.items())
    return str(value or "Brak danych.")


def _security_report(settings: Settings, repo: Path, vault: Path, output: Path) -> str:
    flags = (
        "learning_mode", "youtube_write_block", "youtube_upload_enabled", "youtube_edit_enabled",
        "youtube_delete_enabled", "youtube_comment_enabled", "youtube_playlist_write_enabled",
        "youtube_scope_escalation_allowed", "external_ai_transfer_allowed", "cloud_llm_allowed", "local_processing_only",
    )
    lines = ["# Learning Mode security report", "", f"- Repo: `{repo}`", f"- Vault: `{vault}`", f"- Output base: `{output}`"]
    lines.extend(f"- {name}: `{getattr(settings, name)}`" for name in flags)
    lines.extend(("- Full competitor video download: `disabled by implementation`", "- Transcript download: `disabled by implementation`", "- Automatic commit: `disabled`"))
    return "\n".join(lines) + "\n"


def _write_block_report(blocked: dict[str, bool], contract: dict[str, Any]) -> str:
    lines = ["# YouTube write block test report", "", f"Contract: `{json.dumps(contract, ensure_ascii=False)}`", ""]
    lines.extend(f"- {name}: `{'BLOCKED' if value else 'FAILED'}`" for name, value in blocked.items())
    return "\n".join(lines) + "\n"


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_id": record["video_id"],
        "title": record["title"],
        "duration": record["duration"],
        "public_stats": record["public_stats"],
        "topic": record["topic"],
        "hook_type": record["hook_type"],
        "structure_summary": record["structure_summary"],
        "tempo_estimate": record["tempo_estimate"],
        "audio_profile": record["audio_profile"],
        "thumbnail_profile": record["thumbnail_profile"],
        "potential_success_factors": record["potential_success_factors"],
        "potential_issues": record["potential_issues"],
        "confidence_level": record["confidence_level"],
    }


def _select_deep_sample(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if len(records) <= count:
        return records
    by_views = sorted(records, key=lambda item: int((item.get("public_stats") or {}).get("view_count") or 0), reverse=True)
    by_date = sorted(records, key=lambda item: str(item.get("publish_date") or ""), reverse=True)
    candidates = [by_views[0], by_views[1], by_views[len(by_views) // 2], by_views[-1], by_date[0]]
    for item in by_views:
        if item not in candidates:
            candidates.append(item)
        if len(candidates) >= count:
            break
    return candidates[:count]


def _dedupe_records(existing: list[dict[str, Any]], incoming: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {(str(item.get("video_id")), str(item.get("source_track"))) for item in existing}
    result: list[dict[str, Any]] = []
    for item in incoming:
        key = (str(item.get("video_id")), str(item.get("source_track")))
        if key not in keys:
            keys.add(key)
            result.append(item)
    return result


def _valid_epistemic_record(record: dict[str, Any]) -> bool:
    for key in ("topic", "hook_type", "structure_summary", "tempo_estimate", "audio_profile", "thumbnail_profile"):
        value = record.get(key)
        if not isinstance(value, dict) or value.get("label_type") not in EPISTEMIC_LABELS:
            return False
    return all(item.get("label_type") in EPISTEMIC_LABELS for key in ("potential_success_factors", "potential_issues") for item in record.get(key, []) if isinstance(item, dict))


def _epistemic_value(value: Any, *, default_label: str) -> dict[str, str]:
    label = default_label
    if isinstance(value, dict):
        label = str(value.get("label_type") or default_label)
        raw_value = value.get("value")
    else:
        raw_value = value
    if label not in EPISTEMIC_LABELS:
        label = default_label
    return {"value": _epistemic_text(raw_value), "label_type": label}


def _epistemic_text(value: Any) -> str:
    if isinstance(value, list):
        parts = [_epistemic_text(item.get("value") if isinstance(item, dict) else item) for item in value]
        compact = [part for part in parts if part and part != "Brak wystarczających danych."]
        return "; ".join(dict.fromkeys(compact)) or "Brak wystarczających danych."
    if isinstance(value, dict):
        return _epistemic_text(value.get("value"))
    text = str(value or "").strip()
    return text or "Brak wystarczających danych."


def _epistemic_list(value: Any, *, default_label: str = "observation") -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    return [_epistemic_value(item, default_label=default_label) for item in value[:12]]


def _record_labels(record: dict[str, Any]) -> set[str]:
    labels = {"fact"}
    for key in ("topic", "hook_type", "structure_summary", "tempo_estimate", "audio_profile", "thumbnail_profile"):
        if isinstance(record.get(key), dict):
            labels.add(str(record[key].get("label_type")))
    for key in ("potential_success_factors", "potential_issues"):
        for item in record.get(key, []):
            if isinstance(item, dict):
                labels.add(str(item.get("label_type")))
    return labels & EPISTEMIC_LABELS


async def _http_probe(base_url: str, path: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return {"status": "blocked", "reason": "Non-local endpoint is not permitted."}
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get(f"{base_url.rstrip('/')}{path}")
        return {"status": "ready" if response.status_code < 500 else "degraded", "http_status": response.status_code}
    except Exception as exc:
        return {"status": "unavailable", "reason": _safe_error(exc)}


def _binary_probe(path: str | None, arg: str) -> dict[str, Any]:
    if not path:
        return {"status": "missing"}
    try:
        proc = subprocess.run([path, arg], capture_output=True, text=True, timeout=10, check=False)
        first = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""
        return {"status": "ready" if proc.returncode == 0 else "degraded", "path": path, "version": first[:300]}
    except Exception as exc:
        return {"status": "unavailable", "path": path, "reason": _safe_error(exc)}


def _allowed_thumbnail_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme == "https" and any(host == suffix or host.endswith(f".{suffix}") for suffix in ("ytimg.com", "ggpht.com", "googleusercontent.com"))


def _source_channel_ids(results: list[dict[str, Any]], source: ReferenceSource) -> set[str]:
    return {str(item.get("channel_id")) for item in results if item.get("url") == source.url and item.get("channel_id")}


def _video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname == "youtu.be":
        return parsed.path.strip("/")
    match = re.search(r"[?&]v=([^&#]+)", url)
    return match.group(1) if match else parsed.path.rstrip("/").split("/")[-1]


def _first_matching(names: list[str], preferences: tuple[str, ...]) -> str | None:
    lowered = [(name, name.casefold()) for name in names]
    for preference in preferences:
        for name, normalized in lowered:
            if preference.casefold() in normalized:
                return name
    return None


def _parse_json_object(value: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise LearningSessionError("Local Ollama returned no JSON object.")
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise LearningSessionError("Local Ollama response must be a JSON object.")
    return payload


def _tracks() -> tuple[str, ...]:
    return ("Sigma Squad", "Dark Chronicles", "Wealth Blueprint")


def _channel_dir(root: Path, track: str) -> Path:
    return root / _slug(track)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _pending_steps(completed: list[str]) -> list[str]:
    all_steps = ("metadata_collection", "deep_local_analysis", "channel_knowledge_built", "obsidian_writeback", "rag_sources_prepared", "final_validation")
    return [item for item in all_steps if item not in completed]


def _git_branch(repo: Path) -> str:
    proc = subprocess.run(["git", "branch", "--show-current"], cwd=repo, capture_output=True, text=True, timeout=10, check=False)
    return proc.stdout.strip() or "unknown"


def _free_gb(root: str) -> float:
    return round(shutil.disk_usage(root).free / 1024**3, 2)


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0


def _ensure_allowed_path(path: Path) -> None:
    if path.resolve().drive.upper() != "E:":
        raise StorageSafetyError(f"Project output outside the approved E: data drive is blocked: {path}")


def _atomic_bytes(path: Path, value: bytes) -> None:
    _ensure_allowed_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(value)
    os.replace(temp, path)


def _copy_versioned(source: Path, destination_dir: Path, suffix: str) -> None:
    if not source.exists():
        return
    target = destination_dir / f"{source.stem} - {suffix}{source.suffix}"
    _atomic_bytes(target, source.read_bytes())


def _redact_personal_data(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted-email]", value)
    value = re.sub(r"(?<!\d)(?:\+?\d[\s().-]?){8,15}(?!\d)", "[redacted-phone]", value)
    return value


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    text = re.sub(r"(?i)(access_token|refresh_token|api[_-]?key|authorization|password|client_secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text)
    text = re.sub(r"ya29\.[A-Za-z0-9._-]+", "[REDACTED_TOKEN]", text)
    return text[:1000]


def _scan_for_secret_patterns(root: Path) -> list[str]:
    patterns = (
        re.compile(r"(?i)(access_token|refresh_token|client_secret|api[_-]?key|password)\s*[:=]\s*(?!\[REDACTED)[^\s,;]{8,}"),
        re.compile(r"ya29\.[A-Za-z0-9._-]+"),
        re.compile(r"GOCSPX-[A-Za-z0-9_-]+"),
    )
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.casefold() not in {".md", ".json", ".jsonl", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(pattern.search(text) for pattern in patterns):
            hits.append(str(path.relative_to(root)))
    return hits
