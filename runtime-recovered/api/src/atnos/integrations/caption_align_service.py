"""CAPTION_ALIGN capability: providers, validation, phrase cues.

Providers:
- baseline: existing ElevenLabs/provider character word timestamps, or force_align_words
- stable_ts: managed venv on E: (CPU, known-script align)
- whisperx: marked unsupported on this Windows/AMD/no-CUDA host (not installed)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atnos.production.audio_pipeline.alignment import force_align_words, tokenize_words
from atnos.production.media_tools import resolve_ffmpeg

ALIGNMENT_VERSION = "caption-align-v1"
CAPTION_ALIGN_VENV_PY = Path(r"E:\Atnos\Tools\venvs\caption-align\Scripts\python.exe")
CAPTION_ALIGN_WORKER = Path(r"E:\Atnos\Tools\venvs\caption-align\worker_align.py")
MODEL_ROOT = Path(r"E:\Atnos\Models\whisper")
ARTIFACT_ROOT = Path(r"E:\Atnos\App\reports\priority_a_capabilities_e2e_20260927\align_artifacts")
DEFAULT_MODEL = "tiny"

# Do not end a cue on these alone (unless punctuation/sentence end).
_HANGING = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "to",
        "of",
        "on",
        "in",
        "at",
        "for",
        "from",
        "with",
        "as",
        "by",
        "into",
        "this",
        "that",
        "these",
        "those",
        "your",
        "our",
        "their",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
    }
)

_WORD_RE = re.compile(r"\S+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_token(text: str) -> str:
    return re.sub(r"[^\w]", "", str(text or "").casefold())


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        if rate <= 0:
            raise ValueError("Invalid WAV sample rate")
        return frames / float(rate)


def ensure_ffmpeg_on_path() -> str | None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return None
    bin_dir = str(Path(ffmpeg).parent)
    path = os.environ.get("PATH", "")
    if bin_dir.lower() not in path.lower():
        os.environ["PATH"] = bin_dir + os.pathsep + path
    return ffmpeg


def managed_stable_ts_ready() -> dict[str, Any]:
    py = CAPTION_ALIGN_VENV_PY
    if not py.is_file():
        return {"ready": False, "detail": f"Managed caption-align venv missing: {py}"}
    ffmpeg = ensure_ffmpeg_on_path()
    if not ffmpeg:
        return {"ready": False, "detail": "FFmpeg required for stable-ts audio load"}
    try:
        proc = subprocess.run(
            [str(py), "-c", "import stable_whisper, torch; print(torch.__version__); print(int(torch.cuda.is_available()))"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "HF_HOME": r"E:\Atnos\Models\huggingface", "WHISPER_CACHE_DIR": str(MODEL_ROOT)},
        )
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "detail": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {"ready": False, "detail": (proc.stderr or proc.stdout or "import failed")[:400]}
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return {
        "ready": True,
        "python": str(py),
        "torch": lines[0] if lines else None,
        "cuda": bool(int(lines[1])) if len(lines) > 1 else False,
        "ffmpeg": ffmpeg,
        "model_root": str(MODEL_ROOT),
        "detail": "stable-ts ready in managed caption-align venv (CPU).",
    }


def whisperx_runtime_status() -> dict[str, Any]:
    """WhisperX is not installed: CUDA-oriented stack conflicts with Atnos Windows/AMD CPU setup."""
    return {
        "id": "whisperx",
        "status": "REQUIRES_UNSUPPORTED_RUNTIME",
        "loaded": False,
        "local_clone": Path(r"E:\Atnos\Tools\external-repos\whisperx").is_dir(),
        "detail": (
            "WhisperX not installed. Dry-run requires torch~=2.8, pyannote, ctranslate2, lightning; "
            "host has no CUDA (AMD/Windows CPU). Left uninstalled to protect API + caption-align envs."
        ),
        "cuda_available": False,
        "amd_cpu_host": True,
    }


def write_worker_script() -> Path:
    CAPTION_ALIGN_WORKER.parent.mkdir(parents=True, exist_ok=True)
    CAPTION_ALIGN_WORKER.write_text(
        '''"""Managed-env worker for stable-ts known-script alignment. Invoked by Atnos CAPTION_ALIGN."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import stable_whisper


def words_from_result(result):
    out = []
    all_words = result.all_words() if hasattr(result, "all_words") else None
    if all_words:
        for w in all_words:
            item = {
                "word": (getattr(w, "word", None) or str(w)).strip(),
                "start": float(getattr(w, "start", 0) or 0),
                "end": float(getattr(w, "end", 0) or 0),
            }
            prob = getattr(w, "probability", None)
            if isinstance(prob, (int, float)):
                item["confidence"] = float(prob)
            out.append(item)
        return out
    for seg in getattr(result, "segments", None) or []:
        for w in getattr(seg, "words", None) or []:
            item = {
                "word": (getattr(w, "word", None) or str(getattr(w, "text", ""))).strip(),
                "start": float(getattr(w, "start", 0) or 0),
                "end": float(getattr(w, "end", 0) or 0),
            }
            prob = getattr(w, "probability", None)
            if isinstance(prob, (int, float)):
                item["confidence"] = float(prob)
            out.append(item)
    return out


def main() -> int:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    audio = payload["audio_path"]
    script = payload["script"]
    model_name = payload.get("model") or "tiny"
    download_root = payload.get("download_root")
    device = payload.get("device") or "cpu"
    out_path = Path(payload["out_path"])
    t0 = time.perf_counter()
    model = stable_whisper.load_model(model_name, device=device, download_root=download_root)
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    result = model.align(audio, script, language=payload.get("language") or "en")
    align_s = time.perf_counter() - t1
    words = words_from_result(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "ok": True,
                "provider": "stable_ts",
                "model": model_name,
                "device": device,
                "load_seconds": round(load_s, 3),
                "align_seconds": round(align_s, 3),
                "words": words,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    return CAPTION_ALIGN_WORKER


def _validate_monotonic(words: list[dict[str, Any]], *, duration: float) -> list[str]:
    issues: list[str] = []
    prev_start = -1.0
    for index, word in enumerate(words):
        start = float(word.get("start") if "start" in word else word.get("start_sec") or 0)
        end = float(word.get("end") if "end" in word else word.get("end_sec") or 0)
        if end < start:
            issues.append(f"end_before_start@{index}")
        if start + 1e-6 < prev_start:
            issues.append(f"non_monotonic@{index}")
        if start < -0.05 or end > duration + 0.25:
            issues.append(f"out_of_audio_range@{index}")
        prev_start = start
    return issues


def _coverage(script: str, words: list[dict[str, Any]]) -> dict[str, Any]:
    expected = [_norm_token(t) for t in tokenize_words(script) if _norm_token(t)]
    got = [_norm_token(str(w.get("word") or w.get("text") or "")) for w in words]
    got = [g for g in got if g]
    matched = 0
    gi = 0
    for token in expected:
        while gi < len(got) and got[gi] != token:
            gi += 1
        if gi < len(got) and got[gi] == token:
            matched += 1
            gi += 1
    ratio = matched / max(1, len(expected))
    status = "OK"
    if ratio < 0.85:
        status = "FAIL"
    elif ratio < 0.97:
        status = "DEGRADED"
    return {
        "expected_words": len(expected),
        "aligned_words": len(got),
        "matched_words": matched,
        "coverage_ratio": round(ratio, 4),
        "status": status,
    }


def _script_audio_mismatch(script: str, words: list[dict[str, Any]]) -> bool:
    expected = [_norm_token(t) for t in tokenize_words(script) if _norm_token(t)]
    got = [_norm_token(str(w.get("word") or w.get("text") or "")) for w in words if _norm_token(str(w.get("word") or w.get("text") or ""))]
    return expected != got


def semantic_phrase_cues(
    words: list[dict[str, Any]],
    *,
    max_words: int = 6,
    max_characters: int = 36,
) -> list[dict[str, Any]]:
    """Group words into phone-readable cues; avoid hanging function words."""
    normalized: list[dict[str, Any]] = []
    for item in words:
        text = str(item.get("text") or item.get("word") or "").strip()
        if not text:
            continue
        start = float(item.get("start_sec") if "start_sec" in item else item.get("start") or 0)
        end = float(item.get("end_sec") if "end_sec" in item else item.get("end") or 0)
        normalized.append(
            {
                "text": text,
                "start_sec": start,
                "end_sec": end,
                "segment_id": str(item.get("segment_id") or "scene"),
            }
        )
    if not normalized:
        return []

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
            current = []

    for word in normalized:
        trial = " ".join(w["text"] for w in current + [word])
        would_split = bool(current) and (
            len(trial) > max_characters
            or len(current) >= max_words
            or word["segment_id"] != current[-1]["segment_id"]
        )
        if would_split:
            last = current[-1]["text"]
            last_core = _norm_token(last)
            # Pull hanging function word into next cue when possible.
            if last_core in _HANGING and not last.endswith((".", "?", "!", ",", ";", ":")) and len(current) > 1:
                hang = current.pop()
                flush()
                current = [hang, word]
            else:
                flush()
                current = [word]
        else:
            current.append(word)
        if word["text"].endswith((".", "?", "!")):
            flush()
    flush()

    cues = []
    for group in groups:
        cues.append(
            {
                "text": " ".join(item["text"] for item in group),
                "start_sec": float(group[0]["start_sec"]),
                "end_sec": float(group[-1]["end_sec"]),
                "words": group,
                "line_estimate": 1 if len(" ".join(item["text"] for item in group)) <= 22 else 2,
            }
        )
    return cues


def _normalize_word_rows(words: list[dict[str, Any]], *, provider: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in words:
        text = str(item.get("word") or item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("start") if "start" in item else item.get("start_sec") or 0)
        end = float(item.get("end") if "end" in item else item.get("end_sec") or 0)
        row: dict[str, Any] = {
            "word": text,
            "text": text,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "source": provider,
        }
        if item.get("segment_id"):
            row["segment_id"] = item["segment_id"]
        conf = item.get("confidence")
        if conf is None:
            conf = item.get("probability")
        if isinstance(conf, (int, float)):
            row["confidence"] = float(conf)
        rows.append(row)
    return rows


def align_baseline(
    *,
    script: str,
    duration: float,
    existing_words: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if existing_words:
        rows = _normalize_word_rows(existing_words, provider="baseline")
        return {
            "ok": True,
            "provider": "baseline",
            "timing_source": "provider_character_alignment",
            "words": rows,
            "align_seconds": 0.0,
        }
    tokens = tokenize_words(script)
    aligned, _segments = force_align_words(words=tokens, duration_seconds=duration)
    rows = [
        {
            "word": item.word,
            "text": item.word,
            "start": item.start,
            "end": item.end,
            "start_sec": item.start,
            "end_sec": item.end,
            "source": "baseline_force_align",
            "segment_id": item.segment_id,
        }
        for item in aligned
    ]
    return {
        "ok": True,
        "provider": "baseline",
        "timing_source": "force_align_words",
        "words": rows,
        "align_seconds": 0.0,
        "note": "Character-weighted fallback — prefer provider or stable-ts when available.",
    }


def align_stable_ts(
    *,
    audio_path: Path,
    script: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 300.0,
) -> dict[str, Any]:
    ready = managed_stable_ts_ready()
    if not ready.get("ready"):
        return {"ok": False, "status": "REQUIRES_SETUP", "detail": ready.get("detail"), "provider": "stable_ts"}
    ensure_ffmpeg_on_path()
    write_worker_script()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    req_path = ARTIFACT_ROOT / f"stable_ts_req_{stamp}.json"
    out_path = ARTIFACT_ROOT / f"stable_ts_out_{stamp}.json"
    req_path.write_text(
        json.dumps(
            {
                "audio_path": str(audio_path),
                "script": script,
                "model": model,
                "download_root": str(MODEL_ROOT),
                "device": "cpu",
                "language": "en",
                "out_path": str(out_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "HF_HOME": r"E:\Atnos\Models\huggingface",
        "TORCH_HOME": r"E:\Atnos\Models\torch",
        "XDG_CACHE_HOME": r"E:\Atnos\Models\xdg-cache",
        "WHISPER_CACHE_DIR": str(MODEL_ROOT),
        "TRANSFORMERS_CACHE": r"E:\Atnos\Models\huggingface\transformers",
    }
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(CAPTION_ALIGN_VENV_PY), str(CAPTION_ALIGN_WORKER), str(req_path)],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
        env=env,
    )
    wall = time.perf_counter() - t0
    if proc.returncode != 0 or not out_path.is_file():
        return {
            "ok": False,
            "status": "ERROR",
            "provider": "stable_ts",
            "detail": (proc.stderr or proc.stdout or "stable-ts worker failed")[:800],
            "wall_seconds": round(wall, 3),
        }
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    rows = _normalize_word_rows(payload.get("words") or [], provider="stable_ts")
    return {
        "ok": True,
        "provider": "stable_ts",
        "timing_source": "stable_ts_known_script_align",
        "model": payload.get("model") or model,
        "device": payload.get("device") or "cpu",
        "load_seconds": payload.get("load_seconds"),
        "align_seconds": payload.get("align_seconds"),
        "wall_seconds": round(wall, 3),
        "words": rows,
        "artifact": str(out_path),
    }


def select_default_provider(
    *,
    has_existing_words: bool,
    stable_ready: bool,
    whisperx_status: str,
) -> str:
    if has_existing_words:
        return "baseline"
    if stable_ready:
        return "stable_ts"
    if whisperx_status == "HEALTHY":
        return "whisperx"
    return "baseline"


def run_caption_align(params: dict[str, Any]) -> dict[str, Any]:
    """Execute CAPTION_ALIGN with provider selection and validation."""
    audio_raw = str(params.get("audio_path") or params.get("audio") or "").strip()
    script = str(params.get("script") or params.get("text") or "").strip()
    provider = str(params.get("provider") or "auto").strip().casefold() or "auto"
    existing = params.get("word_timestamps") or params.get("existing_words")
    if existing is not None and not isinstance(existing, list):
        existing = None
    model = str(params.get("model") or DEFAULT_MODEL)

    if not audio_raw and not existing:
        return {"ok": False, "status": "MISSING_AUDIO", "detail": "audio_path required (or existing word_timestamps)"}
    if not script:
        return {"ok": False, "status": "MISSING_SCRIPT", "detail": "script/text required for known-script alignment"}

    audio_path = Path(audio_raw) if audio_raw else None
    duration = None
    audio_hash = None
    if audio_path is not None:
        if not audio_path.is_file():
            return {"ok": False, "status": "MISSING_AUDIO", "detail": f"Audio not found: {audio_path}"}
        suffix = audio_path.suffix.casefold()
        if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            return {"ok": False, "status": "UNSUPPORTED_FORMAT", "detail": f"Unsupported audio format: {suffix}"}
        try:
            if suffix == ".wav":
                duration = wav_duration_seconds(audio_path)
            else:
                duration = float(params.get("duration_seconds") or 0) or None
            audio_hash = _sha256_file(audio_path)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": "CORRUPT_AUDIO", "detail": f"{type(exc).__name__}: {exc}"}

    if duration is None:
        duration = float(params.get("duration_seconds") or 0)
    if not duration or duration <= 0:
        # Infer from existing words if present.
        if existing:
            duration = max(float(w.get("end_sec") or w.get("end") or 0) for w in existing) + 0.05
        else:
            return {"ok": False, "status": "ERROR", "detail": "Could not determine audio duration"}

    script_hash = _sha256_text(script)
    stable_info = managed_stable_ts_ready()
    wx = whisperx_runtime_status()

    if provider == "auto":
        provider = select_default_provider(
            has_existing_words=bool(existing),
            stable_ready=bool(stable_info.get("ready")),
            whisperx_status=str(wx.get("status")),
        )

    if provider == "whisperx":
        return {
            "ok": False,
            "status": "REQUIRES_UNSUPPORTED_RUNTIME",
            "provider": "whisperx",
            "detail": wx.get("detail"),
            "diagnostics": wx,
        }

    if provider == "stable_ts":
        raw = align_stable_ts(audio_path=audio_path, script=script, model=model)  # type: ignore[arg-type]
    elif provider == "baseline":
        raw = align_baseline(script=script, duration=float(duration), existing_words=existing)
    else:
        return {"ok": False, "status": "ERROR", "detail": f"Unknown provider: {provider}"}

    if not raw.get("ok"):
        return raw

    words = raw["words"]
    coverage = _coverage(script, words)
    mono_issues = _validate_monotonic(words, duration=float(duration))
    mismatch = _script_audio_mismatch(script, words)

    status = "SUCCESS"
    if mismatch:
        status = "SCRIPT_AUDIO_MISMATCH"
    if coverage["status"] == "FAIL":
        status = "FAIL"
    elif coverage["status"] == "DEGRADED" and status == "SUCCESS":
        status = "DEGRADED"
    if mono_issues and status == "SUCCESS":
        status = "DEGRADED"

    phrases = semantic_phrase_cues(words)
    over_two_lines = sum(1 for cue in phrases if int(cue.get("line_estimate") or 1) > 2)

    result = {
        "ok": status in {"SUCCESS", "DEGRADED", "SCRIPT_AUDIO_MISMATCH"},
        "status": status,
        "provider": raw.get("provider"),
        "timing_source": raw.get("timing_source"),
        "alignment_version": ALIGNMENT_VERSION,
        "audio_path": str(audio_path) if audio_path else None,
        "audio_hash": audio_hash,
        "script_hash": script_hash,
        "duration_seconds": round(float(duration), 3),
        "model": raw.get("model"),
        "device": raw.get("device"),
        "load_seconds": raw.get("load_seconds"),
        "align_seconds": raw.get("align_seconds"),
        "wall_seconds": raw.get("wall_seconds"),
        "word_count": len(words),
        "words": words,
        "phrases": phrases,
        "coverage": coverage,
        "monotonic_issues": mono_issues,
        "script_audio_mismatch": mismatch,
        "caption_line_over_limit": over_two_lines,
        "fetched_at": _utc_now(),
        "diagnostics": {
            "stable_ts": stable_info,
            "whisperx": wx,
            "default_would_be": select_default_provider(
                has_existing_words=bool(existing),
                stable_ready=bool(stable_info.get("ready")),
                whisperx_status=str(wx.get("status")),
            ),
            "artifact": raw.get("artifact"),
        },
    }
    return result


def availability() -> dict[str, Any]:
    stable = managed_stable_ts_ready()
    wx = whisperx_runtime_status()
    baseline_ok = True
    preferred = select_default_provider(
        has_existing_words=False,
        stable_ready=bool(stable.get("ready")),
        whisperx_status=str(wx.get("status")),
    )
    # Prefer healthy executable aligner for status.
    status = "HEALTHY" if stable.get("ready") or baseline_ok else "REQUIRES_SETUP"
    return {
        "ok": True,
        "status": status,
        "preferred": preferred,
        "providers": {
            "baseline": {"status": "HEALTHY", "detail": "ElevenLabs character times or force_align_words"},
            "stable_ts": {
                "status": "HEALTHY" if stable.get("ready") else "REQUIRES_SETUP",
                **stable,
            },
            "whisperx": wx,
        },
        "alignment_version": ALIGNMENT_VERSION,
        "detail": f"Default provider without existing timestamps: {preferred}",
        "last_alignment": None,
    }
