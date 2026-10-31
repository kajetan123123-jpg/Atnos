"""Priority-A vendor bridges: status + light ops. Heavy GPU stacks stay optional.

Pinned clones live under E:\\Atnos\\Tools\\external-repos.
WhisperX / stable-ts: vendor only until torch/align extras are explicitly installed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

VENDOR_ROOT = Path(r"E:\Atnos\Tools\external-repos")

PINS: dict[str, dict[str, str]] = {
    "whisperx": {
        "commit": "771b4a14a9486f8fd5aef18ef49e35d639523dd3",
        "license": "BSD-2-Clause",
        "capability": "CAPTION_ALIGN",
        "url": "https://github.com/m-bain/whisperX",
    },
    "stable_ts": {
        "commit": "e312072cc024ae9fceb25b057d7d18524873a02b",
        "license": "MIT",
        "capability": "CAPTION_ALIGN",
        "url": "https://github.com/jianfch/stable-ts",
        "dir": "stable-ts",
    },
    "youtube_transcript_api": {
        "commit": "8f150ba8836da30a36bcd40e8fca226ed179ba72",
        "license": "MIT",
        "capability": "YOUTUBE_TRANSCRIPT",
        "url": "https://github.com/jdepoix/youtube-transcript-api",
        "dir": "youtube-transcript-api",
        "pypi": "youtube-transcript-api==1.2.4",
    },
    "yt_dlp": {
        "commit": "c7fb478d21e9e59524befbe23f7801bb267fb880",
        "license": "Unlicense",
        "capability": "YOUTUBE_METADATA",
        "url": "https://github.com/yt-dlp/yt-dlp",
        "dir": "yt-dlp",
        "pypi": "yt-dlp",
    },
    "remotion_trading": {
        "commit": "6f16d2223f20a7651ab2bbc4a3949e6a1536b93a",
        "license": "MIT",
        "capability": "FINANCE_CHART_SKILL",
        "url": "https://github.com/josebarnetche/remotion-trading",
        "dir": "remotion-trading",
    },
    "lucide": {
        "commit": "66d8f9fc394b8530377e5f6112f0b8908ba01280",
        "license": "ISC",
        "capability": "ICON_SVG_LIBRARY",
        "url": "https://github.com/lucide-icons/lucide",
        "dir": "lucide",
    },
}

_VIDEO_ID = re.compile(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})|^([A-Za-z0-9_-]{11})$")


def _dir_for(repo_id: str) -> Path:
    meta = PINS[repo_id]
    folder = str(meta.get("dir") or repo_id)
    return VENDOR_ROOT / folder


def _clone_ok(repo_id: str) -> bool:
    root = _dir_for(repo_id)
    if not root.is_dir():
        return False
    if (root / "LICENSE").is_file() or (root / "LICENSE.md").is_file():
        return True
    return (root / ".git").exists()


def _import_ok(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:
        return False


def status_whisperx() -> dict[str, Any]:
    from atnos.integrations.caption_align_service import whisperx_runtime_status

    wx = whisperx_runtime_status()
    clone = _clone_ok("whisperx")
    loaded = _import_ok("whisperx")
    return {
        "id": "whisperx",
        "status": wx.get("status") or ("HEALTHY" if loaded else ("REQUIRES_SETUP" if clone else "NOT_INSTALLED")),
        "loaded": bool(loaded),
        "local_clone": clone,
        "local_path": str(_dir_for("whisperx")),
        "pinned_commit": PINS["whisperx"]["commit"],
        "capability": "CAPTION_ALIGN",
        "provider_preference": "whisperx",
        "detail": wx.get("detail")
        or (
            "WhisperX importable — ready for forced alignment."
            if loaded
            else "Vendor clone present; not installed (CUDA/AMD unsupported path)."
        ),
    }


def status_stable_ts() -> dict[str, Any]:
    from atnos.integrations.caption_align_service import managed_stable_ts_ready

    managed = managed_stable_ts_ready()
    loaded = bool(managed.get("ready")) or _import_ok("stable_whisper")
    clone = _clone_ok("stable_ts")
    return {
        "id": "stable_ts",
        "status": "HEALTHY" if managed.get("ready") else ("REQUIRES_SETUP" if clone else "NOT_INSTALLED"),
        "loaded": loaded,
        "managed_venv": bool(managed.get("ready")),
        "local_clone": clone,
        "local_path": str(_dir_for("stable_ts")),
        "pinned_commit": PINS["stable_ts"]["commit"],
        "capability": "CAPTION_ALIGN",
        "provider_preference": "stable_ts",
        "detail": managed.get("detail")
        or (
            "stable-ts importable."
            if loaded
            else "Vendor clone present; install into managed caption-align venv on E:."
        ),
        "torch": managed.get("torch"),
        "cuda": managed.get("cuda"),
    }


def status_youtube_transcript() -> dict[str, Any]:
    loaded = _import_ok("youtube_transcript_api")
    clone = _clone_ok("youtube_transcript_api")
    version = None
    if loaded:
        try:
            import youtube_transcript_api as mod

            version = getattr(mod, "__version__", None) or "1.2.4"
        except Exception:
            version = "1.2.4"
    return {
        "id": "youtube_transcript_api",
        "status": "HEALTHY" if loaded else ("REQUIRES_SETUP" if clone else "NOT_INSTALLED"),
        "loaded": loaded,
        "package_version": version,
        "local_clone": clone,
        "local_path": str(_dir_for("youtube_transcript_api")),
        "pinned_commit": PINS["youtube_transcript_api"]["commit"],
        "capability": "YOUTUBE_TRANSCRIPT",
        "detail": "youtube-transcript-api available." if loaded else "pip install youtube-transcript-api into API venv.",
    }


def status_yt_dlp() -> dict[str, Any]:
    loaded = _import_ok("yt_dlp")
    clone = _clone_ok("yt_dlp")
    version = None
    if loaded:
        try:
            import yt_dlp

            version = yt_dlp.version.__version__
        except Exception:
            version = None
    return {
        "id": "yt_dlp",
        "status": "HEALTHY" if loaded else ("REQUIRES_SETUP" if clone else "NOT_INSTALLED"),
        "loaded": loaded,
        "package_version": version,
        "local_clone": clone,
        "local_path": str(_dir_for("yt_dlp")),
        "pinned_commit": PINS["yt_dlp"]["commit"],
        "capability": "YOUTUBE_METADATA",
        "detail": "yt-dlp available (metadata-only ops; no media download by default)." if loaded else "pip install yt-dlp.",
    }


def status_remotion_trading() -> dict[str, Any]:
    root = _dir_for("remotion_trading")
    skill = (root / "SKILL.md").is_file()
    refs = (root / "references").is_dir()
    return {
        "id": "remotion_trading",
        "status": "HEALTHY" if skill else ("REQUIRES_SETUP" if root.is_dir() else "NOT_INSTALLED"),
        "loaded": skill,
        "local_clone": root.is_dir() and (root / ".git").exists(),
        "local_path": str(root),
        "pinned_commit": PINS["remotion_trading"]["commit"],
        "capability": "FINANCE_CHART_SKILL",
        "has_skill_md": skill,
        "has_references": refs,
        "detail": "Remotion trading skill pack present (patterns only — not a second renderer)." if skill else "Missing SKILL.md.",
    }


def status_lucide() -> dict[str, Any]:
    root = _dir_for("lucide")
    icons_dir = root / "icons"
    count = len(list(icons_dir.glob("*.svg"))) if icons_dir.is_dir() else 0
    return {
        "id": "lucide",
        "status": "HEALTHY" if count > 100 else ("REQUIRES_SETUP" if root.is_dir() else "NOT_INSTALLED"),
        "loaded": count > 0,
        "icon_count": count,
        "local_clone": root.is_dir() and (root / ".git").exists(),
        "local_path": str(root),
        "pinned_commit": PINS["lucide"]["commit"],
        "capability": "ICON_SVG_LIBRARY",
        "detail": f"Lucide SVG library with {count} icons." if count else "icons/ missing.",
    }


STATUS_FNS = {
    "whisperx": status_whisperx,
    "stable_ts": status_stable_ts,
    "youtube_transcript_api": status_youtube_transcript,
    "yt_dlp": status_yt_dlp,
    "remotion_trading": status_remotion_trading,
    "lucide": status_lucide,
}


def status_all() -> dict[str, dict[str, Any]]:
    return {key: fn() for key, fn in STATUS_FNS.items()}


def _video_id(raw: str) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    match = _VIDEO_ID.search(text)
    if not match:
        return None
    return match.group(1) or match.group(2)


def fetch_youtube_transcript(video: str, *, languages: list[str] | None = None) -> dict[str, Any]:
    """Fetch transcript for a public video id/url. No download of media."""
    from datetime import datetime, timezone

    if not _import_ok("youtube_transcript_api"):
        return {"ok": False, "status": "REQUIRES_SETUP", "detail": "youtube-transcript-api not installed"}
    vid = _video_id(video)
    if not vid:
        return {"ok": False, "status": "ERROR", "detail": "Pass a YouTube video id or URL"}
    langs = languages or ["en"]
    url = f"https://www.youtube.com/watch?v={vid}"
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            IpBlocked,
            NoTranscriptFound,
            RequestBlocked,
            TranscriptsDisabled,
            VideoUnavailable,
            YouTubeRequestFailed,
        )

        api = YouTubeTranscriptApi()
        fetched = api.fetch(vid, languages=langs)
        rows = []
        for item in fetched:
            # API 1.2.x returns FetchedTranscriptSnippet objects
            if hasattr(item, "text"):
                rows.append(
                    {
                        "text": str(item.text),
                        "start": float(getattr(item, "start", 0.0) or 0.0),
                        "duration": float(getattr(item, "duration", 0.0) or 0.0),
                    }
                )
            elif isinstance(item, dict):
                rows.append(
                    {
                        "text": str(item.get("text") or ""),
                        "start": float(item.get("start") or 0.0),
                        "duration": float(item.get("duration") or 0.0),
                    }
                )
        if not rows:
            return {
                "ok": False,
                "status": "NO_TRANSCRIPT",
                "detail": "Empty transcript",
                "video_id": vid,
                "url": url,
                "provider": "youtube_transcript_api",
                "fetched_at": fetched_at,
                "source_type": "youtube_caption",
            }
        language = langs[0]
        try:
            language = str(getattr(fetched, "language_code", None) or language)
        except Exception:
            pass
        return {
            "ok": True,
            "status": "SUCCESS",
            "video_id": vid,
            "url": url,
            "language": language,
            "segment_count": len(rows),
            "segments": rows[:400],
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
            "provenance": {
                "video_id": vid,
                "url": url,
                "provider": "youtube_transcript_api",
                "fetched_at": fetched_at,
                "language": language,
                "source_type": "youtube_caption",
            },
        }
    except TranscriptsDisabled as exc:
        return {
            "ok": False,
            "status": "TRANSCRIPTS_DISABLED",
            "detail": str(exc),
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }
    except NoTranscriptFound as exc:
        return {
            "ok": False,
            "status": "NO_TRANSCRIPT",
            "detail": str(exc),
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }
    except VideoUnavailable as exc:
        return {
            "ok": False,
            "status": "VIDEO_UNAVAILABLE",
            "detail": str(exc),
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }
    except (IpBlocked, RequestBlocked) as exc:
        return {
            "ok": False,
            "status": "RATE_LIMITED",
            "detail": str(exc),
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }
    except YouTubeRequestFailed as exc:
        return {
            "ok": False,
            "status": "ERROR",
            "detail": str(exc),
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": "ERROR",
            "detail": f"{type(exc).__name__}: {exc}",
            "video_id": vid,
            "url": url,
            "provider": "youtube_transcript_api",
            "fetched_at": fetched_at,
            "source_type": "youtube_caption",
        }


def fetch_youtube_metadata(video: str) -> dict[str, Any]:
    """Metadata-only via yt-dlp (skip_download). Never downloads media files."""
    from datetime import datetime, timezone

    if not _import_ok("yt_dlp"):
        return {"ok": False, "status": "REQUIRES_SETUP", "detail": "yt-dlp not installed"}
    target = str(video or "").strip()
    if not target:
        return {"ok": False, "status": "ERROR", "detail": "Pass a YouTube URL or video id"}
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", target):
        target = f"https://www.youtube.com/watch?v={target}"
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
        if not isinstance(info, dict):
            return {"ok": False, "status": "ERROR", "detail": "No metadata dict returned", "fetched_at": fetched_at}
        keep = {
            "id": info.get("id"),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date"),
            "description": (str(info.get("description") or "")[:500] or None),
            "webpage_url": info.get("webpage_url"),
            "formats_count": len(info.get("formats") or []) if isinstance(info.get("formats"), list) else None,
        }
        vid = str(keep.get("id") or "")
        url = str(keep.get("webpage_url") or target)
        return {
            "ok": True,
            "status": "SUCCESS",
            "metadata": keep,
            "video_id": vid,
            "url": url,
            "provider": "yt_dlp",
            "download": False,
            "fetched_at": fetched_at,
            "source_type": "youtube_metadata",
            "provenance": {
                "video_id": vid,
                "url": url,
                "provider": "yt_dlp",
                "fetched_at": fetched_at,
                "language": None,
                "source_type": "youtube_metadata",
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": "ERROR",
            "detail": f"{type(exc).__name__}: {exc}",
            "fetched_at": fetched_at,
            "provider": "yt_dlp",
            "source_type": "youtube_metadata",
        }


_WB_ICON_ALIASES: dict[str, tuple[str, ...]] = {
    "savings": ("piggy-bank", "landmark", "wallet"),
    "calendar": ("calendar", "calendar-days", "calendar-check"),
    "wallet": ("wallet", "wallet-cards"),
    "bank": ("landmark", "building-2", "university"),
    "chart": ("chart-line", "chart-column", "trending-up"),
    "notebook": ("notebook", "notebook-pen", "book-open"),
    "transfer": ("arrow-left-right", "banknote", "send"),
    "raise": ("trending-up", "badge-dollar-sign", "circle-dollar-sign"),
}


def list_lucide_icons(*, query: str = "", limit: int = 20) -> dict[str, Any]:
    root = _dir_for("lucide") / "icons"
    if not root.is_dir():
        return {"ok": False, "status": "REQUIRES_SETUP", "detail": "lucide icons/ missing"}
    needle = str(query or "").casefold().strip()
    names = sorted(p.stem for p in root.glob("*.svg"))
    if needle:
        aliases = _WB_ICON_ALIASES.get(needle, ())
        preferred = [n for n in aliases if (root / f"{n}.svg").is_file()]
        matched = [n for n in names if needle in n.casefold()]
        names = list(dict.fromkeys([*preferred, *matched]))
    selected = names[: max(1, min(int(limit), 100))]
    return {
        "ok": True,
        "status": "SUCCESS",
        "total_matching": len(names),
        "icons": selected,
        "paths": [str(root / f"{name}.svg") for name in selected],
        "provider": "lucide",
    }


def resolve_lucide_svg(
    *,
    query: str,
    brand_stroke: str = "#1F2A24",
    brand_fill: str = "none",
    size: int = 48,
) -> dict[str, Any]:
    """Pick one Lucide SVG, sanitize lightly, apply WB stroke styling. Keeps vector SVG."""
    listed = list_lucide_icons(query=query, limit=5)
    if not listed.get("ok") or not listed.get("paths"):
        return {"ok": False, "status": listed.get("status") or "FAILED", "detail": listed.get("detail") or "no icon"}
    path = Path(str(listed["paths"][0]))
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Strip scripts / foreignObject — Lucide ships clean icons; still sanitize.
    clean = re.sub(r"<script[\s\S]*?</script>", "", raw, flags=re.I)
    clean = re.sub(r"<foreignObject[\s\S]*?</foreignObject>", "", clean, flags=re.I)
    if 'stroke="' in clean:
        clean = re.sub(r'stroke="[^"]*"', f'stroke="{brand_stroke}"', clean)
    else:
        clean = clean.replace("<svg", f'<svg stroke="{brand_stroke}"', 1)
    if 'fill="' in clean:
        clean = re.sub(r'fill="[^"]*"', f'fill="{brand_fill}"', clean, count=1)
    if re.search(r'\bwidth="', clean):
        clean = re.sub(r'\bwidth="[^"]*"', f'width="{size}"', clean, count=1)
        clean = re.sub(r'\bheight="[^"]*"', f'height="{size}"', clean, count=1)
    return {
        "ok": True,
        "status": "SUCCESS",
        "icon": listed["icons"][0],
        "path": str(path),
        "svg": clean,
        "rasterized": False,
        "brand": {"stroke": brand_stroke, "fill": brand_fill, "size": size},
        "provider": "lucide",
        "query": query,
        "note": "SVG retained as vector — scene mechanism must still come from semantic contract, not icon alone.",
    }


def remotion_trading_skill_summary() -> dict[str, Any]:
    root = _dir_for("remotion_trading")
    skill = root / "SKILL.md"
    if not skill.is_file():
        return {"ok": False, "status": "REQUIRES_SETUP", "detail": "SKILL.md missing"}
    text = skill.read_text(encoding="utf-8", errors="replace")
    refs = []
    ref_dir = root / "references"
    if ref_dir.is_dir():
        refs = sorted(p.name for p in ref_dir.glob("*.md"))
    return {
        "ok": True,
        "status": "SUCCESS",
        "skill_chars": len(text),
        "skill_head": text[:400],
        "references": refs,
        "provider": "remotion_trading",
        "note": "Skill/reference pack only — Remotion WB remains the renderer.",
    }


def caption_align_availability() -> dict[str, Any]:
    """Report which aligner can run; does not pull models into the API venv."""
    from atnos.integrations.caption_align_service import availability

    return availability()


def youtube_research_bundle(video: str, *, languages: list[str] | None = None) -> dict[str, Any]:
    """Public video → YOUTUBE_METADATA + YOUTUBE_TRANSCRIPT → research-ready provenance bundle."""
    meta = fetch_youtube_metadata(video)
    transcript = fetch_youtube_transcript(video, languages=languages)
    ok = bool(meta.get("ok")) and bool(transcript.get("ok"))
    status = "SUCCESS" if ok else "DEGRADED"
    if not meta.get("ok") and not transcript.get("ok"):
        status = "ERROR"
    analysis = None
    if transcript.get("ok"):
        segments = transcript.get("segments") or []
        text = " ".join(str(s.get("text") or "") for s in segments)
        analysis = {
            "segment_count": len(segments),
            "char_count": len(text),
            "word_count": len(text.split()),
            "title": (meta.get("metadata") or {}).get("title") if meta.get("ok") else None,
            "channel": (meta.get("metadata") or {}).get("channel") if meta.get("ok") else None,
            "duration": (meta.get("metadata") or {}).get("duration") if meta.get("ok") else None,
            "insight_eligible": bool(transcript.get("provenance") and meta.get("provenance")),
        }
    return {
        "ok": ok,
        "status": status,
        "video_id": meta.get("video_id") or transcript.get("video_id"),
        "url": meta.get("url") or transcript.get("url"),
        "metadata": meta,
        "transcript": transcript,
        "research_analysis": analysis,
        "provenance": {
            "metadata": meta.get("provenance"),
            "transcript": transcript.get("provenance"),
        },
        "note": "Agent asks for YOUTUBE_METADATA / YOUTUBE_TRANSCRIPT capabilities — never yt-dlp by name.",
    }
