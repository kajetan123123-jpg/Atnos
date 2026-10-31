from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings, load_runtime_env_files  # noqa: E402
from atnos.production.media_tools import resolve_ffmpeg  # noqa: E402
from atnos.production.reference_profile_builder import build_reference_shorts_profile  # noqa: E402


_STATE_PATH: Path | None = None


def main() -> int:
    global _STATE_PATH
    parser = argparse.ArgumentParser(description="Analyze active YouTube Shorts references configured in Atnos.")
    parser.add_argument("--per-channel", type=int, default=2)
    parser.add_argument("--scan-limit", type=int, default=12)
    parser.add_argument("--profile", default="", help="Limit sources to one configured channel profile, e.g. wealth_blueprint.")
    parser.add_argument("--state-file", default="", help="Durable progress file used by the Atnos UI.")
    parser.add_argument("--target-total", type=int, default=0, help="Stop after this many selected videos across channels.")
    args = parser.parse_args()
    _STATE_PATH = Path(args.state_file).resolve() if args.state_file.strip() else None

    load_runtime_env_files()
    settings = Settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    session_dir = settings.data_dir / "learning" / "active-reference-shorts" / timestamp
    sources_dir = session_dir / "sources"
    contact_dir = session_dir / "contact-sheets"
    transcripts_dir = session_dir / "transcripts"
    sources_dir.mkdir(parents=True, exist_ok=True)
    contact_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    profile_key = re.sub(r"[^a-z0-9_]+", "_", args.profile.casefold()).strip("_") if args.profile.strip() else "all"
    references = _load_reference_urls(settings, profile_key=None if profile_key == "all" else profile_key)
    if not references:
        raise RuntimeError("No active YouTube reference-channel URLs were found in Atnos databases or config.")
    target_total = max(0, int(args.target_total or 0)) or max(1, args.per_channel) * len(references)
    _update_state(
        status="running",
        phase="collecting",
        profile_key=profile_key,
        target_count=target_total,
        channel_count=len(references),
        selected_count=0,
        transcript_count=0,
        visual_count=0,
        failure_count=0,
        session_dir=str(session_dir),
        message="Atnos zbiera publiczne próbki referencyjne.",
    )

    failures: list[dict[str, str]] = []
    available_by_channel: list[tuple[dict[str, str], list[dict[str, Any]]]] = []
    for reference in references:
        try:
            entries = _list_shorts(reference["url"], limit=max(3, args.scan_limit))
        except Exception as exc:
            failures.append({"channel": reference["name"], "stage": "list", "error": str(exc)})
            continue
        available_by_channel.append((reference, entries))

    candidates = _select_balanced_candidates(
        available_by_channel,
        per_channel=max(1, args.per_channel),
        target_total=target_total,
    )
    if len(candidates) < target_total:
        _update_state(
            status="failed",
            phase="collecting",
            selected_count=len(candidates),
            failure_count=len(failures) + 1,
            message=f"Atnos znalazl tylko {len(candidates)} z wymaganych {target_total} unikalnych materialow.",
        )
        raise RuntimeError(
            f"Reference batch requires {target_total} unique Shorts, but only {len(candidates)} were available."
        )

    metadata: list[dict[str, Any]] = []
    downloaded: list[Path] = []
    transcript_results: list[dict[str, Any]] = []
    for item in candidates:
        metadata.append(item)
        channel_name = str(item["configured_channel"])
        video_path: Path | None = None
        try:
            video_path = _download_short(item, sources_dir)
            downloaded.append(video_path)
        except Exception as exc:
            failures.append(
                {"channel": channel_name, "video_id": str(item.get("id") or ""), "stage": "download", "error": str(exc)}
            )
        transcript = _collect_transcript(settings, item, video_path, transcripts_dir)
        transcript_results.append(transcript)
        if transcript.get("status") != "transcribed":
            failures.append(
                {
                    "channel": channel_name,
                    "video_id": str(item.get("id") or ""),
                    "stage": "transcript",
                    "error": str(transcript.get("reason") or "Transcript unavailable."),
                }
            )
        _update_state(
            status="running",
            phase="collecting",
            selected_count=len(metadata),
            transcript_count=sum(item.get("status") == "transcribed" for item in transcript_results),
            visual_count=len(downloaded),
            failure_count=len(failures),
            current_channel=channel_name,
            current_video_id=str(item.get("id") or ""),
            message=f"Atnos przeanalizował {len(metadata)} z {target_total} wybranych materiałów.",
        )

    if not downloaded:
        raise RuntimeError(f"No reference Shorts could be downloaded. Failures: {failures}")

    profile_path = session_dir / "reference_profile.json"
    _update_state(status="running", phase="building_profiles", message="Atnos agreguje reguły języka, obrazu i ruchu.")
    profile = build_reference_shorts_profile(settings, downloaded, output_path=profile_path)
    profile["source"] = "active_app_reference_channels"
    profile["configured_channels"] = references
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    for video in downloaded:
        _make_contact_sheet(video, contact_dir / f"{video.stem}.jpg")

    title_patterns = [_title_pattern(item) for item in metadata]
    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "configured_references": references,
        "selected_videos": metadata,
        "downloaded_files": [str(path) for path in downloaded],
        "title_patterns": title_patterns,
        "transcripts": transcript_results,
        "failures": failures,
    }
    (session_dir / "session_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    language_profile = _build_script_language_profile(profile_key, transcript_results)
    language_profile_path = session_dir / "script_language_profile.json"
    language_profile_path.write_text(json.dumps(language_profile, ensure_ascii=False, indent=2), encoding="utf-8")
    editorial_profile = _build_editorial_profile(profile_key, profile, language_profile, title_patterns)
    editorial_profile_path = session_dir / "editorial_profile.json"
    editorial_profile_path.write_text(json.dumps(editorial_profile, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(session_dir / "session_report.md", profile, metadata, failures)

    latest = settings.data_dir / "learning" / "active-reference-shorts" / "latest_reference_profile.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(profile_path, latest)
    latest_language = settings.data_dir / "learning" / "active-reference-shorts" / f"latest_script_language_profile_{profile_key}.json"
    shutil.copyfile(language_profile_path, latest_language)
    latest_editorial = settings.data_dir / "learning" / "active-reference-shorts" / f"latest_editorial_profile_{profile_key}.json"
    shutil.copyfile(editorial_profile_path, latest_editorial)
    _update_state(
        status="completed",
        phase="completed",
        selected_count=len(metadata),
        transcript_count=language_profile["sample_count"],
        visual_count=len(downloaded),
        failure_count=len(failures),
        completed_at=datetime.now(UTC).isoformat(),
        session_dir=str(session_dir),
        report_path=str(session_dir / "session_report.md"),
        evidence_path=str(session_dir / "session_evidence.json"),
        editorial_profile_path=str(latest_editorial),
        message=f"Atnos zakończył sesję: {len(metadata)} wybranych, {language_profile['sample_count']} transkrypcji, {len(downloaded)} analiz obrazu.",
    )
    print(json.dumps({"status": "completed", "session_dir": str(session_dir), "profile": str(latest), "language_profile": str(latest_language), "editorial_profile": str(latest_editorial), "samples": len(downloaded), "transcripts": language_profile["sample_count"]}, ensure_ascii=False))
    return 0


def _load_reference_urls(settings: Settings, profile_key: str | None = None) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if profile_key:
        profile_path = API_ROOT / "config" / "channel_profiles" / f"{profile_key}.json"
        if profile_path.exists():
            try:
                profile_payload = json.loads(profile_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                profile_payload = {}
            for value in profile_payload.get("reference_channels", []) if isinstance(profile_payload, dict) else []:
                if not isinstance(value, str) or not value.strip():
                    continue
                raw = value.strip()
                url = raw if raw.startswith("http") else f"https://www.youtube.com/{raw if raw.startswith('@') else '@' + raw}"
                found.append({"name": raw, "url": url})

    intelligence_db = settings.data_dir / "intelligence.db"
    if intelligence_db.exists() and not profile_key:
        with sqlite3.connect(intelligence_db) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(reference_channels)")}
            if "source_url" in columns:
                for title, url in db.execute("SELECT channel_title, source_url FROM reference_channels WHERE source_url IS NOT NULL"):
                    found.append({"name": str(title or url), "url": str(url)})

    config_path = settings.data_dir / "reels_pipeline" / "channel_config.json"
    if config_path.exists() and not profile_key:
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        for value in payload.get("reference_channels", []) if isinstance(payload, dict) else []:
            if isinstance(value, str):
                found.append({"name": value, "url": value})
            elif isinstance(value, dict) and value.get("url"):
                found.append({"name": str(value.get("name") or value["url"]), "url": str(value["url"])})

    unique: dict[str, dict[str, str]] = {}
    for item in found:
        url = item["url"].strip().rstrip("/")
        if "youtube.com" not in url and "youtu.be" not in url:
            continue
        unique[url.casefold()] = {"name": item["name"].strip() or url, "url": url}
    return list(unique.values())


def _select_balanced_candidates(
    available_by_channel: list[tuple[dict[str, str], list[dict[str, Any]]]],
    *,
    per_channel: int,
    target_total: int,
) -> list[dict[str, Any]]:
    """Keep source coverage first, then fill shortages from the remaining ranked pool."""
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    remaining: list[tuple[dict[str, str], list[dict[str, Any]]]] = []

    for reference, entries in available_by_channel:
        ranked = sorted(entries, key=lambda item: int(item.get("view_count") or 0), reverse=True)
        primary: list[dict[str, Any]] = []
        overflow: list[dict[str, Any]] = []
        for item in ranked:
            video_id = str(item.get("id") or "").strip()
            if not video_id or video_id in seen_ids:
                continue
            target = primary if len(primary) < per_channel else overflow
            target.append(item)
            seen_ids.add(video_id)
        for item in primary:
            selected.append(_configure_candidate(item, reference))
        remaining.append((reference, overflow))

    while len(selected) < target_total:
        added = False
        for reference, entries in remaining:
            if not entries or len(selected) >= target_total:
                continue
            selected.append(_configure_candidate(entries.pop(0), reference))
            added = True
        if not added:
            break
    return selected[:target_total]


def _configure_candidate(item: dict[str, Any], reference: dict[str, str]) -> dict[str, Any]:
    configured = dict(item)
    configured["configured_channel"] = reference["name"]
    configured["configured_url"] = reference["url"]
    return configured


def _collect_transcript(
    settings: Settings,
    item: dict[str, Any],
    video_path: Path | None,
    output_dir: Path,
) -> dict[str, Any]:
    video_id = str(item.get("id") or (video_path.stem if video_path else "unknown"))
    stem = f"{_slug(str(item.get('channel') or item.get('configured_channel') or 'channel'))}_{video_id}"
    transcript_path = output_dir / f"{stem}.txt"
    url = str(item.get("url") or f"https://www.youtube.com/watch?v={video_id}")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={video_id}"

    source = "youtube_captions"
    text = _download_caption_text(url, output_dir, stem)
    if len(text.split()) < 20 and video_path is not None and video_path.exists():
        source = "whisper_cpp"
        text = _transcribe_with_whisper(settings, video_path, output_dir / stem)
    if len(text.split()) < 20:
        return {
            "video_id": video_id,
            "channel": str(item.get("configured_channel") or item.get("channel") or "unknown"),
            "status": "unavailable",
            "source": source,
            "reason": "No readable English captions and local Whisper produced fewer than 20 words.",
        }

    transcript_path.write_text(text.strip() + "\n", encoding="utf-8")
    duration_sec = float(item.get("duration") or 0.0)
    if duration_sec <= 0:
        duration_sec = _caption_duration(output_dir, stem)
    if duration_sec <= 0 and video_path is not None:
        duration_sec = _probe_duration(video_path)
    metrics = _language_metrics(text, duration_sec=duration_sec)
    return {
        "video_id": video_id,
        "channel": str(item.get("configured_channel") or item.get("channel") or "unknown"),
        "title": str(item.get("title") or ""),
        "status": "transcribed",
        "source": source,
        "transcript_path": str(transcript_path),
        "metrics": metrics,
    }


def _download_caption_text(url: str, output_dir: Path, stem: str) -> str:
    template = output_dir / f"{stem}.%(ext)s"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*,en",
        "--sub-format",
        "vtt",
        "-o",
        str(template),
        url,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    candidates = sorted(output_dir.glob(f"{stem}*.vtt"), key=lambda path: ("orig" not in path.name, len(path.name)))
    for candidate in candidates:
        try:
            parsed = _parse_vtt(candidate.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if len(parsed.split()) >= 20:
            return parsed
    return ""


def _parse_vtt(payload: str) -> str:
    cues: list[str] = []
    has_word_timing = bool(re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", payload))
    for block in re.split(r"\r?\n\s*\r?\n", payload):
        if "-->" not in block:
            continue
        lines = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or "-->" in stripped or stripped.isdigit():
                continue
            if has_word_timing and not re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", stripped):
                # YouTube rolling captions repeat the previous line in an un-timed cue.
                continue
            clean = re.sub(r"<[^>]+>", "", stripped)
            clean = html.unescape(clean).replace("\u200b", "").strip()
            if clean:
                lines.append(clean)
        cue = " ".join(lines).strip()
        if cue and (not cues or cue.casefold() != cues[-1].casefold()):
            cues.append(cue)
    return _deduplicate_caption_rollup(" ".join(cues))


def _caption_duration(output_dir: Path, stem: str) -> float:
    duration = 0.0
    for candidate in output_dir.glob(f"{stem}*.vtt"):
        try:
            payload = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})", payload):
            hours, minutes, seconds, milliseconds = (int(value) for value in match.groups())
            duration = max(duration, hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0)
    return duration


def _deduplicate_caption_rollup(text: str) -> str:
    words = re.findall(r"\S+", text)
    result: list[str] = []
    for word in words:
        if len(result) >= 2 and word.casefold() == result[-1].casefold() == result[-2].casefold():
            continue
        result.append(word)
    return " ".join(result)


def _transcribe_with_whisper(settings: Settings, video_path: Path, output_prefix: Path) -> str:
    executable = Path(settings.whisper_cpp_exe) if settings.whisper_cpp_exe else None
    model = settings.whisper_model_path
    ffmpeg = resolve_ffmpeg()
    if not executable or not executable.exists() or not model or not model.exists() or not ffmpeg:
        return ""
    wav_path = output_prefix.with_suffix(".wav")
    audio_proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(video_path), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if audio_proc.returncode != 0 or not wav_path.exists():
        return ""
    proc = subprocess.run(
        [str(executable), "-m", str(model), "-f", str(wav_path), "-l", "en", "-otxt", "-of", str(output_prefix)],
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )
    txt_path = output_prefix.with_suffix(".txt")
    if proc.returncode != 0 or not txt_path.exists():
        return ""
    return " ".join(txt_path.read_text(encoding="utf-8", errors="replace").split())


def _language_metrics(text: str, *, duration_sec: float) -> dict[str, Any]:
    words = re.findall(r"[A-Za-z0-9']+", text)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    sentence_lengths = [len(re.findall(r"[A-Za-z0-9']+", sentence)) for sentence in sentences]
    contractions = [word for word in words if "'" in word]
    second_person = [word for word in words if word.casefold() in {"you", "your", "you're", "you've", "you'll"}]
    number_tokens = [word for word in words if any(char.isdigit() for char in word)]
    transitions = [
        marker
        for marker in ("but", "so", "because", "instead", "here's", "now", "then", "which means")
        if re.search(rf"\b{re.escape(marker)}\b", text, flags=re.IGNORECASE)
    ]
    word_count = len(words)
    short_sentences = sum(length <= 6 for length in sentence_lengths)
    medium_sentences = sum(7 <= length <= 14 for length in sentence_lengths)
    long_sentences = sum(length >= 15 for length in sentence_lengths)
    transition_count = sum(
        len(re.findall(rf"\b{re.escape(marker)}\b", text, flags=re.IGNORECASE))
        for marker in ("but", "so", "because", "instead", "here's", "now", "then", "which means")
    )
    sentence_count = max(1, len(sentences))
    return {
        "word_count": word_count,
        "sentence_count": len(sentences),
        "average_sentence_words": round(statistics.mean(sentence_lengths), 2) if sentence_lengths else 0.0,
        "median_sentence_words": round(statistics.median(sentence_lengths), 2) if sentence_lengths else 0.0,
        "sentence_length_stdev": round(statistics.pstdev(sentence_lengths), 2) if len(sentence_lengths) > 1 else 0.0,
        "short_sentence_ratio": round(short_sentences / sentence_count, 3),
        "medium_sentence_ratio": round(medium_sentences / sentence_count, 3),
        "long_sentence_ratio": round(long_sentences / sentence_count, 3),
        "fragment_sentence_ratio": round(sum(length <= 3 for length in sentence_lengths) / sentence_count, 3),
        "questions_per_100_sentences": round(100.0 * sum("?" in sentence for sentence in sentences) / max(1, len(sentences)), 2),
        "contractions_per_100_words": round(100.0 * len(contractions) / max(1, word_count), 2),
        "second_person_per_100_words": round(100.0 * len(second_person) / max(1, word_count), 2),
        "numbers_per_100_words": round(100.0 * len(number_tokens) / max(1, word_count), 2),
        "transitions_per_100_words": round(100.0 * transition_count / max(1, word_count), 2),
        "estimated_wpm": round(60.0 * word_count / duration_sec, 2) if duration_sec > 0 else None,
        "opening_pattern": _opening_pattern(text),
        "transition_types": transitions,
    }


def _opening_pattern(text: str) -> str:
    opening = " ".join(text.split()[:20]).casefold()
    if "?" in " ".join(text.split()[:30]):
        return "question"
    if re.match(r"^(stop|don't|never|imagine|look|take|start|try)\b", opening):
        return "imperative"
    if re.match(r"^(you|your|most people|here's|this)\b", opening):
        return "direct_address"
    if any(token in opening for token in ("but", "actually", "instead", "not what")):
        return "contrast"
    return "specific_claim"


def _build_script_language_profile(profile_key: str, transcripts: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [item for item in transcripts if item.get("status") == "transcribed" and isinstance(item.get("metrics"), dict)]
    metric_rows = [item["metrics"] for item in usable]

    def median(key: str, default: float = 0.0) -> float:
        values = [float(row[key]) for row in metric_rows if row.get(key) is not None]
        return round(statistics.median(values), 2) if values else default

    openings: dict[str, int] = {}
    transitions: dict[str, int] = {}
    for row in metric_rows:
        opening = str(row.get("opening_pattern") or "unknown")
        openings[opening] = openings.get(opening, 0) + 1
        for transition in row.get("transition_types") or []:
            transitions[str(transition)] = transitions.get(str(transition), 0) + 1
    target_sentence = min(14.0, max(6.0, median("median_sentence_words", 10.0)))
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "profile_key": profile_key,
        "sample_count": len(usable),
        "source_policy": "Aggregate linguistic measurements only. Never copy source sentences, signature phrases, stories, or claims.",
        "metrics": {
            "median_sentence_words": median("median_sentence_words"),
            "median_sentence_length_stdev": median("sentence_length_stdev"),
            "median_short_sentence_ratio": median("short_sentence_ratio"),
            "median_medium_sentence_ratio": median("medium_sentence_ratio"),
            "median_long_sentence_ratio": median("long_sentence_ratio"),
            "median_fragment_sentence_ratio": median("fragment_sentence_ratio"),
            "median_questions_per_100_sentences": median("questions_per_100_sentences"),
            "median_contractions_per_100_words": median("contractions_per_100_words"),
            "median_second_person_per_100_words": median("second_person_per_100_words"),
            "median_numbers_per_100_words": median("numbers_per_100_words"),
            "median_transitions_per_100_words": median("transitions_per_100_words"),
            "median_estimated_wpm": median("estimated_wpm"),
            "opening_patterns": openings,
            "transition_types": transitions,
        },
        "generation_rules": [
            f"Aim for roughly {target_sentence:.0f} words per spoken sentence, then vary sentence length for rhythm.",
            "Alternate short impact lines with complete explanatory sentences; do not turn one thought into disconnected fact cards.",
            "Use direct second-person language when it serves the idea, not in every line.",
            "Use natural contractions and spoken transitions such as but, so, because, or instead.",
            "Open on a concrete tension, question, command, or specific claim; never announce the topic.",
            "Build one causal chain: setup, friction, turn, payoff, one practical action.",
            "Give every sentence one job and every scene one visible proof of that sentence.",
            "Do not imitate a creator's signature phrase, anecdote, title, or wording.",
        ],
        "samples": [
            {
                "video_id": item.get("video_id"),
                "channel": item.get("channel"),
                "source": item.get("source"),
                "metrics": item.get("metrics"),
            }
            for item in usable
        ],
    }


def _build_editorial_profile(
    profile_key: str,
    visual_profile: dict[str, Any],
    language_profile: dict[str, Any],
    title_patterns: list[dict[str, Any]],
) -> dict[str, Any]:
    hook_types: dict[str, int] = {}
    for item in title_patterns:
        hook_type = str(item.get("hook_type") or "unknown")
        hook_types[hook_type] = hook_types.get(hook_type, 0) + 1
    motion_rules = [
        "Start with a meaningful visual state in the first frame; no blank or decorative intro.",
        "Use one continuous, semantic motion per scene and preserve position, scale, and exposure continuity.",
        "Cut only when the narration changes idea or proof; never insert transition-only filler frames.",
        "Use hard cuts or restrained match cuts. Reject crossfades, white flashes, random zoom resets, and light flicker.",
        "Keep captions and key objects stable enough to read; motion supports the claim instead of competing with it.",
    ]
    measured_rules = [
        str(rule).strip()
        for rule in visual_profile.get("production_rules_for_atnos") or []
        if str(rule).strip()
    ]
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "profile_key": profile_key,
        "sample_count": int(language_profile.get("sample_count") or 0),
        "visual_sample_count": int(visual_profile.get("sample_count") or 0),
        "source_policy": (
            "Derived aggregate measurements only. Never copy source scripts, frames, audio, branding, signature phrases, stories, or claims."
        ),
        "metrics": language_profile.get("metrics") or {},
        "generation_rules": language_profile.get("generation_rules") or [],
        "motion_metrics": {
            "median_first_cut_sec": visual_profile.get("median_first_cut_sec"),
            "median_avg_cut_interval_sec": visual_profile.get("median_avg_cut_interval_sec"),
            "median_cuts_per_10s": visual_profile.get("median_cuts_per_10s"),
            "median_frame_delta": visual_profile.get("median_frame_delta"),
            "median_p95_frame_delta": visual_profile.get("median_p95_frame_delta"),
            "median_brightness_delta": visual_profile.get("median_brightness_delta"),
            "median_p95_brightness_delta": visual_profile.get("median_p95_brightness_delta"),
            "median_flash_events_per_10s": visual_profile.get("median_flash_events_per_10s"),
        },
        "motion_rules": list(dict.fromkeys([*motion_rules, *measured_rules])),
        "observed_title_hook_types": hook_types,
        "qa_rejection_rules": [
            "Reject narration made from repeated fragments or equal-length fact-card sentences.",
            "Reject a scene whose movement does not explain or reveal the spoken point.",
            "Reject isolated brightness spikes, duplicated-frame stutter, random zoom resets, and transition filler.",
            "Reject any output that reuses source wording, anecdotes, frames, audio, branding, or claims.",
        ],
    }


def _list_shorts(channel_url: str, *, limit: int) -> list[dict[str, Any]]:
    url = channel_url if channel_url.rstrip("/").endswith("/shorts") else f"{channel_url.rstrip('/')}/shorts"
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--playlist-end", str(limit), "--dump-json", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:] or "yt-dlp list failed")
    entries = [json.loads(line) for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    return [item for item in entries if item.get("id")]


def _download_short(item: dict[str, Any], output_dir: Path) -> Path:
    video_id = str(item["id"])
    output_template = output_dir / f"{_slug(str(item.get('channel') or item.get('configured_channel') or 'channel'))}_{video_id}.%(ext)s"
    url = str(item.get("url") or f"https://www.youtube.com/watch?v={video_id}")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "-f",
        "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format",
        "mp4",
        "-o",
        str(output_template),
        url,
    ]
    ffmpeg = resolve_ffmpeg()
    if ffmpeg:
        cmd[4:4] = ["--ffmpeg-location", str(Path(ffmpeg).parent)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=360, check=False)
    candidates = sorted(output_dir.glob(f"*_{video_id}.*"), key=lambda path: (path.suffix.lower() != ".mp4", path.name))
    selected = next((path for path in candidates if _contains_video_stream(path)), None)
    if proc.returncode != 0 or selected is None:
        raise RuntimeError(proc.stderr[-1000:] or "yt-dlp download failed")
    return selected


def _contains_video_stream(path: Path) -> bool:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return path.suffix.lower() == ".mp4"
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe")
    if not ffprobe.exists():
        return path.suffix.lower() == ".mp4"
    proc = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.returncode == 0 and "video" in proc.stdout.casefold()


def _probe_duration(path: Path) -> float:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg or not path.exists():
        return 0.0
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe")
    if not ffprobe.exists():
        return 0.0
    proc = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(proc.stdout.strip()))
    except ValueError:
        return 0.0


def _make_contact_sheet(video: Path, target: Path) -> None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return
    cmd = [ffmpeg, "-y", "-i", str(video), "-vf", "fps=1/5,scale=180:320,tile=4x3", "-frames:v", "1", str(target)]
    subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)


def _title_pattern(item: dict[str, Any]) -> dict[str, Any]:
    title = str(item.get("title") or "")
    words = re.findall(r"[A-Za-z0-9']+", title)
    upper = {"EVER", "ACTUALLY", "ONLY", "MOST", "WORST", "BEST", "REAL", "TRUE", "EVERY"}
    emotions = [word for word in words if word.upper() in upper]
    lowered = title.casefold()
    hook = "question_hook" if "?" in title else "numbered_or_specific" if any(char.isdigit() for char in title) else "danger_tension" if any(term in lowered for term in ("warning", "avoid", "never", "danger", "cost")) else "hidden_truth" if any(term in lowered for term in ("secret", "truth", "actually", "really")) else "specific_claim"
    return {"video_id": item.get("id"), "title": title, "views": item.get("view_count"), "hook_type": hook, "opening_phrase": " ".join(words[:2]), "word_count": len(words), "emotion_words": emotions}


def _write_report(path: Path, profile: dict[str, Any], metadata: list[dict[str, Any]], failures: list[dict[str, str]]) -> None:
    channels = sorted({str(item.get("configured_channel") or item.get("channel") or "unknown") for item in metadata})
    lines = [
        "# Atnos - active reference Shorts learning session",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Channels: {', '.join(channels)}",
        f"Analyzed samples: {profile.get('sample_count', 0)}",
        "",
        "## Measured production floor",
        f"- First meaningful cut median: {profile.get('median_first_cut_sec')} s",
        f"- Average cut interval median: {profile.get('median_avg_cut_interval_sec')} s",
        f"- Cuts per 10 seconds median: {profile.get('median_cuts_per_10s')}",
        f"- Loudness median: {profile.get('median_lufs')} LUFS",
        f"- Brightness median: {profile.get('median_brightness')}",
        f"- Saturation median: {profile.get('median_saturation')}",
        "",
        "## Rules applied to Atnos QA",
        "- Compare the exact final render against measured hook timing and visual-change density.",
        "- Missing pacing evidence blocks QA instead of silently passing.",
        "- Use patterns as quality measurements only; do not copy scripts, imagery, branding, or narration.",
        "",
        f"Failures: {len(failures)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "channel"


def _update_state(**changes: Any) -> None:
    if _STATE_PATH is None:
        return
    payload: dict[str, Any] = {}
    if _STATE_PATH.exists():
        try:
            loaded = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload.update(changes)
    payload["updated_at"] = datetime.now(UTC).isoformat()
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, _STATE_PATH)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _update_state(
            status="failed",
            phase="failed",
            message=str(exc),
            completed_at=datetime.now(UTC).isoformat(),
        )
        raise
