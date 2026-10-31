from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = APP_ROOT / "data" / "content-research"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class SourceSeed:
    key: str
    label: str
    url: str
    category: str


SOURCE_SEEDS: tuple[SourceSeed, ...] = (
    SourceSeed(
        key="tools_technylo",
        label="Best AI tools for YouTube Shorts 2026",
        url="https://www.technylo.com/blogs/best-ai-tools-for-youtube-shorts-that-actually-save-time/",
        category="tools",
    ),
    SourceSeed(
        key="tools_comparegen",
        label="AI platforms for YouTube Shorts 2026",
        url="https://www.comparegen.ai/blog/best-ai-platforms-youtube-shorts-2026",
        category="tools",
    ),
    SourceSeed(
        key="niches_vidpal",
        label="Faceless YouTube niches 2026",
        url="https://www.vidpal.ai/blog/best-faceless-youtube-niches-2026",
        category="niches",
    ),
    SourceSeed(
        key="niches_miraflow",
        label="Faceless YouTube Shorts AI niches 2026",
        url="https://miraflow.ai/blog/faceless-youtube-shorts-ai-niches-2026",
        category="niches",
    ),
    SourceSeed(
        key="niches_eliro",
        label="Faceless Shorts niches 2026",
        url="https://eliro.pro/blog/faceless-youtube-shorts-niches-2026",
        category="niches",
    ),
    SourceSeed(
        key="youtube_ai_guardrails",
        label="YouTube AI and anti-slop direction",
        url="https://www.androidcentral.com/apps-software/youtube/youtube-will-help-creators-use-ai-in-2026-as-long-as-its-not-slop",
        category="policy",
    ),
)

TOOL_CANDIDATES = (
    "CapCut",
    "OpusClip",
    "Descript",
    "Submagic",
    "Klap",
    "Vizard",
    "Munch",
    "Runway",
    "Pika",
    "Kling",
    "HeyGen",
    "Synthesia",
    "Canva",
    "ElevenLabs",
    "Piper",
    "whisper.cpp",
    "FFmpeg",
    "ComfyUI",
)

NICHE_CANDIDATES = (
    "local AI / home lab automation",
    "AI tool demos and comparisons",
    "faceless myth vs truth explainers",
    "oddly satisfying build/process videos",
    "science and weird facts",
    "historical micro-stories",
    "money/business case studies",
    "productivity systems",
    "PC setup and troubleshooting",
    "absurd challenge storytelling",
    "before/after transformations",
    "AI safety and privacy explainers",
)

YOUTUBE_SEARCH_QUERIES = (
    "AI tools YouTube Shorts",
    "faceless AI shorts",
    "local AI shorts",
    "CapCut AI shorts",
    "oddly satisfying process shorts",
    "AI tool demo shorts",
    "faceless business case study shorts",
    "shorts hook examples",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "session"


def fetch_text(url: str, *, timeout: int = 18) -> tuple[bool, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(900_000)
            encoding = response.headers.get_content_charset() or "utf-8"
            if encoding.lower() != "utf-8":
                try:
                    return True, raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    pass
            return True, raw.decode(encoding, errors="replace")
    except (OSError, URLError, TimeoutError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?</style>", " ", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def summarize_text(text: str, *, limit: int = 1400) -> str:
    text = strip_html(text)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def count_mentions(text: str, candidates: tuple[str, ...]) -> dict[str, int]:
    lowered = text.lower()
    return {candidate: lowered.count(candidate.lower()) for candidate in candidates}


def fetch_google_trends(region: str = "US") -> list[dict[str, str]]:
    url = f"https://trends.google.com/trending/rss?geo={quote_plus(region)}"
    ok, payload = fetch_text(url)
    if not ok:
        return [{"title": "TREND_FETCH_FAILED", "source": url, "error": payload}]
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        return [{"title": "TREND_PARSE_FAILED", "source": url, "error": str(exc)}]
    items: list[dict[str, str]] = []
    for item in root.findall(".//item")[:20]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or url
        if title.strip():
            items.append({"title": title.strip(), "source": link})
    return items


def fetch_youtube_titles(query: str) -> list[str]:
    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    ok, payload = fetch_text(url)
    if not ok:
        return [f"YOUTUBE_FETCH_FAILED: {payload}"]
    titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"', payload)
    cleaned: list[str] = []
    seen: set[str] = set()
    for title in titles:
        if "\\u" in title:
            try:
                title = json.loads(f'"{title}"')
            except json.JSONDecodeError:
                pass
        normalized = html.unescape(title).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
        if len(cleaned) >= 12:
            break
    return cleaned or ["NO_PUBLIC_TITLES_EXTRACTED"]


def score_tools(source_text: str) -> list[dict[str, object]]:
    mentions = count_mentions(source_text, TOOL_CANDIDATES)
    rows: list[dict[str, object]] = []
    for tool in TOOL_CANDIDATES:
        local_fit = tool in {"Piper", "whisper.cpp", "FFmpeg", "ComfyUI"}
        manual_fit = tool in {"CapCut", "Canva", "Descript"}
        auto_fit = tool in {"OpusClip", "Klap", "Vizard", "Munch", "Submagic"}
        generative_fit = tool in {"Runway", "Pika", "Kling", "HeyGen", "Synthesia"}
        score = mentions[tool] * 2
        score += 4 if local_fit else 0
        score += 3 if manual_fit or auto_fit else 0
        score += 2 if generative_fit else 0
        rows.append(
            {
                "tool": tool,
                "score": score,
                "mentions": mentions[tool],
                "best_use": (
                    "local production runtime"
                    if local_fit
                    else "editing / packaging"
                    if manual_fit
                    else "auto clipping / captions"
                    if auto_fit
                    else "generative b-roll / avatar"
                    if generative_fit
                    else "supporting tool"
                ),
                "atnos_fit": (
                    "core/local"
                    if local_fit
                    else "manual-reference"
                    if manual_fit
                    else "optional-cloud"
                ),
            }
        )
    return sorted(rows, key=lambda item: (-int(item["score"]), str(item["tool"])))


def score_niches(source_text: str, trend_titles: list[str], youtube_titles: list[str]) -> list[dict[str, object]]:
    joined = " ".join([source_text, *trend_titles, *youtube_titles]).lower()
    rows: list[dict[str, object]] = []
    for niche in NICHE_CANDIDATES:
        tokens = [token for token in re.split(r"[^a-z0-9]+", niche.lower()) if len(token) > 2]
        relevance = sum(joined.count(token) for token in tokens)
        atnos_fit = 0
        if any(token in niche for token in ("AI", "ai", "PC", "local", "tool", "privacy")):
            atnos_fit += 4
        if any(token in niche for token in ("satisfying", "historical", "science", "weird", "case")):
            atnos_fit += 2
        production_difficulty = 3
        if "avatar" in niche or "business" in niche:
            production_difficulty += 1
        if "local AI" in niche or "AI tool" in niche:
            production_difficulty -= 1
        score = relevance + atnos_fit - production_difficulty
        rows.append(
            {
                "niche": niche,
                "score": score,
                "source_relevance": relevance,
                "atnos_fit": atnos_fit,
                "production_difficulty": production_difficulty,
                "recommended_angle": build_angle(niche),
            }
        )
    return sorted(rows, key=lambda item: (-int(item["score"]), str(item["niche"])))


def build_angle(niche: str) -> str:
    if "local AI" in niche:
        return "Pokazuj realne lokalne workflowy: problem -> komenda/narzedzie -> wynik -> ryzyko."
    if "tool" in niche:
        return "Porownuj narzedzia przez konkretny bottleneck: script, captions, b-roll, render, upload."
    if "satisfying" in niche:
        return "Uzywaj prostych procesow wizualnych, mocnych napisow i loopable ending."
    if "historical" in niche:
        return "Mikro-historia z konfliktem, jednym zaskoczeniem i szybka puenta."
    if "business" in niche:
        return "Mini case study: liczba -> decyzja -> blad -> lekcja."
    return "Hook w 2 sekundy, jeden problem, jeden zwrot, jeden konkretny wniosek."


def analyze_reel_patterns(youtube_titles: list[str]) -> list[dict[str, object]]:
    patterns: list[dict[str, object]] = []
    for title in youtube_titles[:40]:
        lowered = title.lower()
        hook_type = "curiosity"
        if any(token in lowered for token in ("how to", "jak", "tutorial", "guide")):
            hook_type = "how-to"
        elif any(token in lowered for token in ("best", "top", "ranked")):
            hook_type = "ranking"
        elif any(token in lowered for token in ("mistake", "problem", "wrong", "blad")):
            hook_type = "mistake"
        elif any(token in lowered for token in ("ai", "tool", "automation")):
            hook_type = "tool-demo"
        patterns.append(
            {
                "title": title,
                "hook_type": hook_type,
                "likely_structure": [
                    "0-2s: direct hook / result first",
                    "2-8s: proof or visual contrast",
                    "8-25s: 2-3 fast steps",
                    "last 2s: loop, question or concrete takeaway",
                ],
                "atnos_instruction": "Nie rob ogolnego eseju. Zrob jeden obrazowy problem i jeden konkretny wynik.",
            }
        )
    return patterns


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_markdown_report(
    path: Path,
    *,
    cycle: int,
    tools: list[dict[str, object]],
    niches: list[dict[str, object]],
    patterns: list[dict[str, object]],
    trends: list[dict[str, str]],
    sources: list[dict[str, object]],
) -> None:
    lines: list[str] = [
        f"# Shorts Research Cycle {cycle}",
        "",
        "## Najlepsze narzedzia do sprawdzenia",
        "",
    ]
    for item in tools[:10]:
        lines.append(
            f"- **{item['tool']}** | score={item['score']} | use={item['best_use']} | atnos_fit={item['atnos_fit']}"
        )
    lines += ["", "## Nisze do testow", ""]
    for item in niches[:10]:
        lines.append(
            f"- **{item['niche']}** | score={item['score']} | angle: {item['recommended_angle']}"
        )
    lines += ["", "## Wzorce rolek / shortow", ""]
    for item in patterns[:12]:
        lines.append(f"- **{item['hook_type']}**: {item['title']}")
    lines += ["", "## Trend signals", ""]
    for item in trends[:12]:
        lines.append(f"- {item.get('title')} ({item.get('source')})")
    lines += ["", "## Source status", ""]
    for item in sources:
        lines.append(f"- {item['key']}: {item['status']} | {item['url']}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def build_handoff(path: Path, *, session_dir: Path, latest_report: Path, cycle: int) -> None:
    content = f"""# Codex Handoff: Shorts Content Research Session

## Status
- Cycles completed: {cycle}
- Session dir: `{session_dir}`
- Latest report: `{latest_report}`

## What to review next
1. Pick top 3 niches from `reports/cycle_{cycle:03d}.json`.
2. Convert them into 10 concrete video briefs each.
3. Update Atnos short script generator with niche-specific hook templates.
4. Add quality gate checks for: concrete visual, proof, retention beat, non-generic CTA.

## Production rule
Do not publish automatically. First output should be local scripts and test renders only.
"""
    path.write_text(content, encoding="utf-8")


def run_cycle(session_dir: Path, cycle: int) -> dict[str, object]:
    source_records: list[dict[str, object]] = []
    source_text_chunks: list[str] = []
    for seed in SOURCE_SEEDS:
        ok, payload = fetch_text(seed.url)
        record = {
            "key": seed.key,
            "label": seed.label,
            "category": seed.category,
            "url": seed.url,
            "status": "ok" if ok else "failed",
            "fetched_at": utc_now().isoformat(),
            "summary": summarize_text(payload),
        }
        source_records.append(record)
        source_text_chunks.append(record["summary"])
        write_json(session_dir / "sources" / f"{seed.key}.json", record)

    trends_us = fetch_google_trends("US")
    trends_pl = fetch_google_trends("PL")
    trends = trends_us[:10] + trends_pl[:10]

    youtube_titles: list[str] = []
    for query in YOUTUBE_SEARCH_QUERIES:
        titles = fetch_youtube_titles(query)
        youtube_titles.extend([f"{query}: {title}" for title in titles])

    source_text = "\n".join(source_text_chunks)
    tools = score_tools(source_text)
    niches = score_niches(
        source_text=source_text,
        trend_titles=[item.get("title", "") for item in trends],
        youtube_titles=youtube_titles,
    )
    patterns = analyze_reel_patterns(youtube_titles)

    payload = {
        "cycle": cycle,
        "created_at": utc_now().isoformat(),
        "tools": tools,
        "niches": niches,
        "reel_patterns": patterns,
        "trends": trends,
        "sources": source_records,
        "youtube_title_samples": youtube_titles[:80],
    }
    report_json = session_dir / "reports" / f"cycle_{cycle:03d}.json"
    report_md = session_dir / "reports" / f"cycle_{cycle:03d}.md"
    write_json(report_json, payload)
    write_markdown_report(
        report_md,
        cycle=cycle,
        tools=tools,
        niches=niches,
        patterns=patterns,
        trends=trends,
        sources=source_records,
    )
    build_handoff(
        session_dir / "handoff_to_codex.md",
        session_dir=session_dir,
        latest_report=report_md,
        cycle=cycle,
    )
    return {"json": str(report_json), "markdown": str(report_md), "payload": payload}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Atnos shorts market/content research session.")
    parser.add_argument("--duration-minutes", type=int, default=180)
    parser.add_argument("--cycle-minutes", type=int, default=15)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()

    started_at = utc_now()
    deadline = started_at + timedelta(minutes=max(args.duration_minutes, 0))
    cycle_seconds = max(args.cycle_minutes, 1) * 60
    session_id = f"shorts-market-session-{started_at.strftime('%Y%m%d-%H%M%S')}"
    session_dir = args.output_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    stop_file = session_dir / "STOP"
    heartbeat_path = session_dir / "heartbeat.json"
    log_path = session_dir / "session.jsonl"

    config = {
        "session_id": session_id,
        "started_at": started_at.isoformat(),
        "deadline": deadline.isoformat(),
        "duration_minutes": args.duration_minutes,
        "cycle_minutes": args.cycle_minutes,
        "max_cycles": args.max_cycles,
        "stop_file": str(stop_file),
        "scope": [
            "AI apps/tools for short-form video production",
            "faceless/shorts niches",
            "public Shorts/Reels title-pattern analysis",
            "Atnos production recommendations",
        ],
        "safety": {
            "no_upload": True,
            "no_paid_api": True,
            "no_login_bypass": True,
            "public_sources_only": True,
        },
    }
    write_json(session_dir / "session_config.json", config)

    cycle = 0
    append_jsonl(log_path, {"event": "session_started", **config})
    while utc_now() < deadline:
        if stop_file.exists():
            append_jsonl(log_path, {"event": "session_stopped_by_file", "stopped_at": utc_now().isoformat()})
            break
        if args.max_cycles is not None and cycle >= args.max_cycles:
            append_jsonl(log_path, {"event": "session_max_cycles_reached", "cycle": cycle})
            break
        cycle += 1
        try:
            result = run_cycle(session_dir, cycle)
            heartbeat = {
                "status": "running" if utc_now() < deadline else "finishing",
                "session_id": session_id,
                "pid": os.getpid(),
                "cycle": cycle,
                "last_report": result["markdown"],
                "updated_at": utc_now().isoformat(),
                "deadline": deadline.isoformat(),
                "stop_file": str(stop_file),
            }
            write_json(heartbeat_path, heartbeat)
            append_jsonl(
                log_path,
                {
                    "event": "cycle_completed",
                    "cycle": cycle,
                    "markdown": result["markdown"],
                    "json": result["json"],
                    "completed_at": utc_now().isoformat(),
                },
            )
        except Exception as exc:
            append_jsonl(
                log_path,
                {
                    "event": "cycle_failed",
                    "cycle": cycle,
                    "error": f"{type(exc).__name__}: {exc}",
                    "failed_at": utc_now().isoformat(),
                },
            )
        if args.max_cycles is not None and cycle >= args.max_cycles:
            append_jsonl(log_path, {"event": "session_max_cycles_reached", "cycle": cycle})
            break
        if utc_now() >= deadline:
            break
        sleep_for = min(cycle_seconds, max(0, int((deadline - utc_now()).total_seconds())))
        if sleep_for > 0:
            time.sleep(sleep_for)

    final_heartbeat = {
        "status": "completed",
        "session_id": session_id,
        "pid": os.getpid(),
        "cycle": cycle,
        "finished_at": utc_now().isoformat(),
        "deadline": deadline.isoformat(),
        "session_dir": str(session_dir),
        "handoff": str(session_dir / "handoff_to_codex.md"),
    }
    write_json(heartbeat_path, final_heartbeat)
    append_jsonl(log_path, {"event": "session_completed", **final_heartbeat})
    print(json.dumps(final_heartbeat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
