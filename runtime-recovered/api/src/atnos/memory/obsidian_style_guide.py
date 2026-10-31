from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from atnos.core.config import Settings


MAX_STYLE_GUIDE_CHARS = 6_000


@dataclass(frozen=True, slots=True)
class StyleGuideReadResult:
    content: str | None
    source: str | None
    status: str


@dataclass(frozen=True, slots=True)
class StyleGuideBootstrapResult:
    path: Path | None
    created: bool
    status: str
    message: str


def read_channel_style_guide(settings: Settings, account_id: str) -> StyleGuideReadResult:
    """Read channel style guidance with Obsidian as the source of truth.

    Local channel_ops files are legacy fallbacks. Production generation must prefer
    the Obsidian vault so the latest channel memory and learning notes affect new
    scripts before stale local config does.
    """
    safe_accounts = _style_guide_account_aliases(account_id)
    for safe_account in safe_accounts:
        obsidian = _read_obsidian_style(settings, safe_account)
        if obsidian.content:
            return obsidian
    for safe_account in safe_accounts:
        local = _read_local_channel_ops_style(settings, safe_account)
        if local.content:
            return local
    return StyleGuideReadResult(content=None, source=None, status="missing")


def ensure_channel_style_guide(
    settings: Settings,
    account_id: str,
    *,
    channel_name: str | None = None,
    niche: str = "finanse osobiste, edukacja finansowa, YouTube Shorts",
    overwrite: bool = False,
) -> StyleGuideBootstrapResult:
    """Create a safe default channel style guide inside the configured Obsidian vault."""
    vault = settings.obsidian_vault_path
    if vault is None:
        return StyleGuideBootstrapResult(
            path=None,
            created=False,
            status="obsidian_not_configured",
            message="OBSIDIAN_VAULT_PATH is not configured.",
        )
    vault_root = vault.resolve()
    if not vault_root.exists() or not vault_root.is_dir():
        return StyleGuideBootstrapResult(
            path=None,
            created=False,
            status="obsidian_missing",
            message=f"Obsidian vault does not exist: {vault_root}",
        )
    shorts_root = _resolve_inside_vault(vault_root, settings.atnos_obsidian_shorts_dir)
    if shorts_root is None:
        return StyleGuideBootstrapResult(
            path=None,
            created=False,
            status="path_escaped_vault",
            message="Configured shorts directory escapes the Obsidian vault.",
        )

    safe_account = _safe_id(account_id)
    target_dir = shorts_root / safe_account
    target_path = target_dir / "style_guide.md"
    if not _is_inside(target_path.resolve(), vault_root):
        return StyleGuideBootstrapResult(
            path=None,
            created=False,
            status="path_escaped_vault",
            message="Target style guide path escapes the Obsidian vault.",
        )
    if target_path.exists() and not overwrite:
        return StyleGuideBootstrapResult(
            path=target_path,
            created=False,
            status="exists",
            message="Style guide already exists.",
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        _default_style_guide_content(
            account_id=safe_account,
            channel_name=channel_name or safe_account,
            niche=niche,
        ),
        encoding="utf-8",
    )
    return StyleGuideBootstrapResult(
        path=target_path,
        created=True,
        status="created",
        message="Default style guide created.",
    )


def _read_local_channel_ops_style(settings: Settings, safe_account: str) -> StyleGuideReadResult:
    channel_file = settings.data_dir / "channel_ops" / "channels.json"
    if channel_file.exists():
        try:
            import json

            raw = json.loads(channel_file.read_text(encoding="utf-8"))
            channel = raw.get(safe_account) if isinstance(raw, dict) else None
            style_path = channel.get("style_guide_path") if isinstance(channel, dict) else None
            if style_path:
                path = Path(str(style_path)).resolve()
                if path.exists() and path.is_file():
                    return _read_markdown(path, status="local_channel_ops")
        except (OSError, ValueError, TypeError):
            pass

    direct = settings.data_dir / "channel_ops" / "channels" / safe_account / "style_guide.md"
    if direct.exists():
        return _read_markdown(direct, status="local_channel_ops")
    return StyleGuideReadResult(content=None, source=None, status="local_missing")


def _read_obsidian_style(settings: Settings, safe_account: str) -> StyleGuideReadResult:
    vault = settings.obsidian_vault_path
    if vault is None:
        return StyleGuideReadResult(content=None, source=None, status="obsidian_not_configured")
    vault_root = vault.resolve()
    if not vault_root.exists() or not vault_root.is_dir():
        return StyleGuideReadResult(content=None, source=None, status="obsidian_missing")
    shorts_root = _resolve_inside_vault(vault_root, settings.atnos_obsidian_shorts_dir)
    if shorts_root is None or not shorts_root.exists():
        return StyleGuideReadResult(content=None, source=None, status="obsidian_shorts_missing")

    candidates = [
        shorts_root / safe_account / "style_guide.md",
        shorts_root / safe_account / "Style Guide.md",
        shorts_root / f"style-guide-{safe_account}.md",
        shorts_root / f"channel-style-guide-{safe_account}.md",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and _is_inside(candidate.resolve(), vault_root):
            return _read_markdown(candidate, status="obsidian_channel_style")

    learning_notes = sorted(
        (
            path
            for path in shorts_root.glob(f"channel-learning-{safe_account}-*.md")
            if path.is_file() and _is_inside(path.resolve(), vault_root)
        ),
        key=lambda path: _learning_note_sort_key(path, safe_account=safe_account),
        reverse=True,
    )
    if learning_notes:
        return _read_markdown(learning_notes[0], status="obsidian_latest_learning")
    return StyleGuideReadResult(content=None, source=None, status="obsidian_missing")


def _read_markdown(path: Path, *, status: str) -> StyleGuideReadResult:
    try:
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return StyleGuideReadResult(content=None, source=str(path), status=f"{status}_unreadable")
    sanitized = _sanitize_content(content)
    if not sanitized:
        return StyleGuideReadResult(content=None, source=str(path), status=f"{status}_empty")
    return StyleGuideReadResult(content=sanitized, source=str(path), status=status)


def _learning_note_sort_key(path: Path, *, safe_account: str) -> tuple[str, float]:
    prefix = f"channel-learning-{safe_account}-"
    suffix = ".md"
    name = path.name
    if name.startswith(prefix) and name.endswith(suffix):
        return (name[len(prefix) : -len(suffix)], _safe_mtime(path))
    return ("", _safe_mtime(path))


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _sanitize_content(content: str) -> str:
    lines: list[str] = []
    in_frontmatter = False
    for index, raw_line in enumerate(content.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        line = raw_line.rstrip()
        if index == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
            continue
        if _looks_secret(line):
            continue
        lines.append(line)
    sanitized = "\n".join(lines).strip()
    if len(sanitized) > MAX_STYLE_GUIDE_CHARS:
        sanitized = sanitized[:MAX_STYLE_GUIDE_CHARS].rstrip()
    return sanitized


def _default_style_guide_content(*, account_id: str, channel_name: str, niche: str) -> str:
    profile = _default_channel_style_profile(account_id=account_id, channel_name=channel_name, niche=niche)
    return f"""---
type: channel-style-guide
account_id: {account_id}
channel_name: {channel_name}
niche: {niche}
---

# Channel Style Guide: {channel_name}

## Core Positioning
- Format: faceless YouTube Shorts, vertical 9:16, 30-60 seconds.
- Niche: {niche}.
- Language: English voiceover, English captions, English metadata.
- Promise: {profile["promise"]}
- Tone: {profile["tone"]}

## Script Rules
- Hook must land in the first 2.5 seconds: {profile["hook_rule"]}.
- Use short spoken English sentences. One idea per beat.
- No invented earnings, ROI, dates, subscriber counts, views, or market numbers.
- If a number is used, it must come from `intelligence.db`, YouTube Analytics, or visible public data.
- Never present speculation as fact. Use clear educational or fictional framing.
- End with one clear payoff or loop, not a generic subscribe line.

## Visual Rules
- First frame must contain a topic-specific object, not an abstract background.
- Visual rhythm: meaningful scene or motion change every 2-4 seconds.
- Visual style: {profile["visual_style"]}
- Required objects: {profile["required_objects"]}
- Avoid: copied thumbnails, readable text inside generated images, logos, watermarks, black mush, static chart wallpaper.
- Header/punch words: max 3 words, uppercase, emotionally clear.
- Subtitles: high contrast, 3-4 words per caption beat, no watermark in production by default.

## Audio Rules
- Voice: {profile["voice"]}
- Final audio target: 44100 Hz stereo, loudnorm around I=-14 / TP=-1.5 / LRA=11.
- Background music stays below narration; never let music dominate speech.

## QA Preferences
- Block upload if the hook is weak, audio is mono/low-rate, video is not 9:16, subtitles are missing, or Ollama QA rejects it.
- Block upload if visuals do not match the topic in the first 2.5 seconds.
- Treat low-confidence research as a reason to make the script more general, not as permission to invent data.

## Learning Loop
- After 24/48/72h, update channel learning notes with retention, views, CTR, and what to repeat or avoid.
- Prefer formats that keep retention after 3 seconds and do not rely on fake urgency.
"""


def _default_channel_style_profile(*, account_id: str, channel_name: str, niche: str) -> dict[str, str]:
    text = f"{account_id} {channel_name} {niche}".casefold()
    if any(marker in text for marker in ("historia", "history", "chronicles", "dark")):
        return {
            "promise": "one strange historical object, rule, or event explained with documentary tension.",
            "tone": "controlled suspense, serious, curious, never melodramatic.",
            "hook_rule": "open with a disturbing artifact, impossible-sounding law, vanished place, or hidden consequence",
            "visual_style": "dark historical documentary, readable shadows, parchment, maps, candlelight, archive tables, old streets, dramatic but legible.",
            "required_objects": "map, artifact, old document, candle, courtroom, battlefield marker, ship, castle, prison door, or period object tied to the topic.",
            "voice": "low-suspense narrator, deliberate pauses before reveals, no robotic monotone.",
        }
    if any(marker in text for marker in ("brainrot", "sigma", "meme", "absurd", "ai")):
        return {
            "promise": "one absurd AI meme situation that escalates fast and loops cleanly.",
            "tone": "fast, punchy, surreal, playful, child-safe, never creepy.",
            "hook_rule": "open with a visible avatar, phone, glitch, impossible creature, or absurd immediate situation",
            "visual_style": "bright neon meme animation, phone UI shapes, exaggerated avatars, glitch accents, high contrast, fast motion, no dark empty backgrounds.",
            "required_objects": "phone, avatar, reaction face shape, neon symbol, glitch portal, animal/object mashup, or clearly absurd scene element.",
            "voice": "high-energy creator delivery with crisp words, fast rhythm, and clear punchline emphasis.",
        }
    return {
        "promise": "one counterintuitive money behavior or practical finance rule explained without hype.",
        "tone": "confident, practical, status-aware, slightly tense, never scammy.",
        "hook_rule": "open with paycheck, receipt, debt, savings, rent, card, phone banking screen shape, or a clear money mistake",
        "visual_style": "premium dark finance editorial, charcoal/navy base, cyan or emerald accent, clean charts, receipts, desk, cash-flow metaphors, high readability.",
        "required_objects": "paycheck, receipt, wallet, card, phone finance app shape, bills, simple chart, cash-flow line, decision split, or budget notebook.",
        "voice": "calm but sharp premium narrator, confident first sentence, no flat reading.",
    }


def _looks_secret(line: str) -> bool:
    lowered = line.casefold()
    forbidden = ("api_key", "token", "secret", "password", "client_secret", "authorization", "bearer ")
    return any(item in lowered for item in forbidden)


def _style_guide_account_aliases(account_id: str) -> list[str]:
    """Return only the persisted account identifier, without channel-specific aliases."""
    return [_safe_id(account_id)]


def _resolve_inside_vault(vault_root: Path, relative: str) -> Path | None:
    candidate = (vault_root / Path(relative.strip())).resolve()
    return candidate if _is_inside(candidate, vault_root) else None


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    cleaned = "".join(char if char in allowed else "-" for char in normalized)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "default"
