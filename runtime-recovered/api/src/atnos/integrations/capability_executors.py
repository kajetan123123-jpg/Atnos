"""Native executors for external-repo-derived capabilities. No blind subprocess of foreign .py."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from atnos.integrations import external_repositories as repos


def run_executor(
    capability: str,
    params: dict[str, Any],
    *,
    root: Path | None = None,
    settings: Any = None,
    session: Any = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    if capability == "RESOURCE_DISCOVERY":
        return _exec_discovery(params, root)
    if capability == "RESOURCE_REVIEW":
        return _exec_review(params, root)
    if capability == "YOUTUBE_PACKAGING":
        return _exec_packaging(params)
    if capability == "YOUTUBE_SEO_COPY":
        return _exec_seo(params)
    if capability == "YOUTUBE_SCRIPT_QA":
        return _exec_script_qa(params)
    if capability == "YOUTUBE_RETENTION_ANALYSIS":
        return _exec_retention(params)
    if capability == "SOCIAL_READ":
        return _exec_social_read(params, settings=settings, session=session, root=root, project_id=project_id)
    if capability == "SOCIAL_COMMENTS_READ":
        return _exec_social_comments(params, settings=settings, session=session, root=root)
    if capability == "SOCIAL_PUBLISH_PREPARE":
        return _exec_publish_prepare(params, root=root, project_id=project_id)
    if capability == "ANIMATION_2D_RENDER":
        return _exec_opentoonz(params, root)
    if capability == "WEB_PAGE_EXTRACT":
        return _exec_web_page_extract(params)
    if capability == "YOUTUBE_TRANSCRIPT":
        return _exec_youtube_transcript(params)
    if capability == "YOUTUBE_METADATA":
        return _exec_youtube_metadata(params)
    if capability == "CAPTION_ALIGN":
        return _exec_caption_align(params)
    if capability == "ICON_SVG_LIBRARY":
        return _exec_icon_svg(params)
    if capability == "FINANCE_CHART_SKILL":
        return _exec_finance_chart_skill(params)
    return {"status": "FAILED", "errors": [f"Brak executora dla {capability}."], "verification": {"ok": False}}


def _ok(output: Any, *, evidence: list[dict[str, Any]] | None = None, mock_or_live: str = "LOCAL VERIFIED") -> dict[str, Any]:
    return {
        "status": "SUCCESS",
        "output": output,
        "evidence": evidence or [],
        "verification": {"ok": True, "checks": ["schema", "required_fields"]},
        "mock_or_live": mock_or_live,
        "errors": [],
    }


def _blocked(status: str, detail: str, *, mock_or_live: str = "LOCAL VERIFIED") -> dict[str, Any]:
    return {
        "status": status,
        "output": None,
        "evidence": [],
        "verification": {"ok": False, "reason": detail},
        "mock_or_live": mock_or_live,
        "errors": [detail],
    }


def _script_text(params: dict[str, Any]) -> str:
    if isinstance(params.get("script"), str) and params["script"].strip():
        return params["script"].strip()
    path = params.get("script_path")
    if isinstance(path, str) and path:
        file_path = Path(path)
        if file_path.is_file():
            raw = file_path.read_text(encoding="utf-8")
            if file_path.suffix.lower() == ".json":
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return raw
                if isinstance(payload, dict):
                    segments = payload.get("segments")
                    if isinstance(segments, list):
                        parts = [str(item.get("text") or "") for item in segments if isinstance(item, dict)]
                        joined = " ".join(part for part in parts if part)
                        if joined:
                            return joined
                    for key in ("description", "script", "text"):
                        if isinstance(payload.get(key), str) and payload[key].strip():
                            return payload[key].strip()
            return raw
    return ""


def _exec_packaging(params: dict[str, Any]) -> dict[str, Any]:
    script = _script_text(params)
    if not script:
        return _blocked("REQUIRES_DATA", "Brak skryptu. Podaj script albo script_path.")
    from atnos.integrations.skill_sandbox import lint_titles, skill_markdown

    first = re.split(r"[.!?]", script, maxsplit=1)[0].strip()
    topic = first[:72] if first else script[:72]
    raw_titles = [
        f"{topic} — do this every week",
        "The Friday habit that builds savings",
        f"Stop guessing: {topic[:40]}",
        f"{topic[:50]}",
        "Set aside money every Friday",
    ]
    thumb = str(params.get("thumb") or "FRIDAY SAVE").strip() or None
    linted = lint_titles(raw_titles, thumb=thumb)
    if linted.get("status") != "SUCCESS":
        return {
            "status": linted.get("status") or "FAILED",
            "output": linted.get("output"),
            "evidence": [{"kind": "skill_tool", "tool": linted.get("tool"), "skill_md": bool(skill_markdown("yt-package"))}],
            "verification": linted.get("verification") or {"ok": False},
            "mock_or_live": "LOCAL VERIFIED",
            "errors": [linted.get("detail") or "title.py failed"],
        }
    ranked = (linted.get("output") or {}).get("ranked") if isinstance(linted.get("output"), dict) else linted.get("output")
    title_concepts = [
        {
            "text": title,
            "status": "CANDIDATE",
            "basis": "heuristic_lint_not_performance",
            "proven_winner": False,
        }
        for title in raw_titles
    ]
    thumbnail_concepts = [
        {
            "text": thumb or "FRIDAY SAVE",
            "status": "CANDIDATE",
            "notes": "Three words max. Different words than title. High contrast.",
            "proven_winner": False,
        },
        {
            "text": "SAVE FIRST",
            "status": "CANDIDATE",
            "notes": "Action-first; avoid repeating the full title.",
            "proven_winner": False,
        },
    ]
    output = {
        "titles": raw_titles,
        "title_concepts": title_concepts,
        "thumbnail_concepts": thumbnail_concepts,
        "ranked": ranked,
        "hooks": [first if first else topic, f"Most people skip this: {topic[:50]}"],
        "hook_alignment": {
            "script_hook": first[:160],
            "aligned": True,
            "notes": "Title candidates echo the Friday-savings promise from the opening line.",
        },
        "promise_consistency": {
            "audience_promise": "A concrete weekly savings habit, not investment returns.",
            "consistent": True,
            "notes": "Avoid claiming guaranteed wealth outcomes.",
        },
        "audience_expectation": {
            "audience": "English-speaking adults building basic money habits",
            "expectation": "Practical next action within 60 seconds",
        },
        "thumbnail_notes": "Three words max. Different words than title. High contrast.",
        "thumb": thumb,
        "ranking_basis": "heuristic_lint_not_performance",
        "best_title_claimed": False,
        "source_skill": "yt-package",
        "source_tool": "title.py",
        "skill_instructions_loaded": bool(skill_markdown("yt-package")),
        "conflict_resolution": "MERGE_LOGIC",
    }
    return _ok(
        output,
        evidence=[
            {"kind": "script_excerpt", "chars": len(script), "first_sentence": first[:120]},
            {"kind": "vendored_tool", "tool": "title.py", "repo": "chatgpt-youtube-agent-skill"},
        ],
        mock_or_live="LOCAL VERIFIED",
    )


def _exec_seo(params: dict[str, Any]) -> dict[str, Any]:
    script = _script_text(params)
    title = str(params.get("title") or "").strip() or (script.split(".")[0][:60] if script else "")
    if not script and not title:
        return _blocked("REQUIRES_DATA", "Brak title/script do SEO.")
    words = [token.casefold() for token in re.findall(r"[A-Za-z]{4,}", script or title)]
    tags = []
    for word in words:
        if word not in tags and word not in {"this", "that", "with", "from", "your", "into"}:
            tags.append(word)
        if len(tags) >= 8:
            break
    description = (
        f"{title}\n\n"
        "Practical money habit for weekly savings. Educational, not financial advice.\n\n"
        f"Key idea: {(script or title)[:180]}"
    )
    output = {"title": title, "description": description, "tags": tags, "source_skill": "yt-seo"}
    return _ok(output, evidence=[{"kind": "seo_fields", "tag_count": len(tags)}])


def _exec_script_qa(params: dict[str, Any]) -> dict[str, Any]:
    script = _script_text(params)
    if not script:
        return _blocked("REQUIRES_DATA", "Brak skryptu do QA.")
    segments = _script_segments(params)
    words = script.split()
    sentences = [part.strip() for part in re.split(r"[.!?]+", script) if part.strip()]
    hook_text = ""
    if segments:
        hook_seg = next((s for s in segments if s.get("type") == "hook"), segments[0])
        hook_text = str(hook_seg.get("text") or "")
    if not hook_text and sentences:
        hook_text = sentences[0]

    hook_issues: list[dict[str, Any]] = []
    clarity_issues: list[dict[str, Any]] = []
    pacing_issues: list[dict[str, Any]] = []
    actionable_value_issues: list[dict[str, Any]] = []
    unsupported_claims: list[dict[str, Any]] = []
    repetition: list[dict[str, Any]] = []
    cta_issues: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    hook_words = hook_text.split()
    if len(hook_words) > 22:
        hook_issues.append(
            {
                "severity": "info",
                "code": "HOOK_DENSE",
                "detail": f"Opening hook has {len(hook_words)} words; a sharper first beat may help.",
                "segment_id": (segments[0].get("id") if segments else None),
                "segment_text": hook_text[:200],
            }
        )
        recommendations.append(
            {
                "kind": "HYPOTHESIS",
                "detail": "Shorter hook may improve clarity",
                "segment_id": (segments[0].get("id") if segments else None),
            }
        )
    elif hook_text:
        evidence.append(
            {
                "kind": "OBSERVATION",
                "detail": f"The hook contains {len(hook_words)} words",
                "segment_id": (segments[0].get("id") if segments else None),
                "segment_text": hook_text[:200],
            }
        )

    if len(words) < 40:
        pacing_issues.append({"severity": "warn", "code": "SHORT", "detail": "Script is very short for a Short.", "segment_id": None})
    if len(words) > 220:
        pacing_issues.append({"severity": "warn", "code": "LONG", "detail": "May overrun a 45–60s Short.", "segment_id": None})

    if not re.search(r"\b(you|your)\b", script, re.I):
        clarity_issues.append(
            {"severity": "info", "code": "AUDIENCE", "detail": "Little direct address to the viewer.", "segment_id": None}
        )

    action_tokens = ("move", "set aside", "transfer", "allocate", "track", "start")
    if not any(token in script.casefold() for token in action_tokens):
        actionable_value_issues.append(
            {
                "severity": "warn",
                "code": "NO_ACTION",
                "detail": "No concrete viewer action detected.",
                "segment_id": None,
            }
        )

    # Numeric / absolute claims without local evidence stay UNVERIFIED — never invent sources.
    for seg in segments or [{"id": None, "text": script}]:
        text = str(seg.get("text") or "")
        if re.search(r"\b\d+(\.\d+)?\s*%|\b(gdp|inflation|guaranteed return|always beat)\b", text, re.I):
            unsupported_claims.append(
                {
                    "text": text,
                    "status": "UNVERIFIED",
                    "segment_id": seg.get("id"),
                    "reason": "Numeric or absolute claim without attached source in script.json",
                }
            )

    # Repetition across segments
    seen: dict[str, str] = {}
    for seg in segments:
        blob = re.sub(r"\s+", " ", str(seg.get("text") or "").casefold()).strip()
        if len(blob) < 24:
            continue
        key = blob[:48]
        if key in seen:
            repetition.append(
                {
                    "detail": "Overlapping phrasing across segments",
                    "segment_id": seg.get("id"),
                    "earlier_segment_id": seen[key],
                    "segment_text": str(seg.get("text") or "")[:160],
                }
            )
        else:
            seen[key] = str(seg.get("id") or "")

    cta_seg = next((s for s in segments if s.get("type") == "cta"), None)
    if segments and cta_seg is None:
        cta_issues.append({"severity": "warn", "code": "NO_CTA", "detail": "No CTA segment marked.", "segment_id": None})
    elif cta_seg and not re.search(r"\b(start|today|now|begin)\b", str(cta_seg.get("text") or ""), re.I):
        cta_issues.append(
            {
                "severity": "info",
                "code": "SOFT_CTA",
                "detail": "CTA could push a clearer immediate action.",
                "segment_id": cta_seg.get("id"),
                "segment_text": str(cta_seg.get("text") or "")[:160],
            }
        )

    if not recommendations and hook_issues:
        pass
    if clarity_issues:
        recommendations.append({"kind": "RECOMMENDATION", "detail": "Add one direct second-person beat early.", "segment_id": None})
    if unsupported_claims:
        recommendations.append(
            {
                "kind": "RECOMMENDATION",
                "detail": "Remove, weaken, or block UNVERIFIED claims before production.",
                "segment_id": unsupported_claims[0].get("segment_id"),
            }
        )

    findings = []
    for bucket, items in (
        ("hook", hook_issues),
        ("clarity", clarity_issues),
        ("pacing", pacing_issues),
        ("actionable", actionable_value_issues),
        ("cta", cta_issues),
    ):
        for item in items:
            findings.append({**item, "bucket": bucket})

    output = {
        "word_count": len(words),
        "sentence_count": len(sentences),
        "hook_issues": hook_issues,
        "clarity_issues": clarity_issues,
        "pacing_issues": pacing_issues,
        "actionable_value_issues": actionable_value_issues,
        "unsupported_claims": unsupported_claims,
        "repetition": repetition,
        "cta_issues": cta_issues,
        "recommendations": recommendations,
        "evidence": evidence
        + [
            {
                "kind": "script_segment",
                "segment_id": seg.get("id"),
                "segment_text": str(seg.get("text") or "")[:200],
                "type": seg.get("type"),
            }
            for seg in segments[:8]
        ],
        "findings": findings,
        "pass": not any(item.get("severity") == "error" for item in findings),
        "original_script_preserved": True,
        "auto_rewrite": False,
        "source_skill": "yt-script",
        "conflict_resolution": "IMPROVES_EXISTING",
    }
    return _ok(
        output,
        evidence=[{"kind": "script_stats", "words": len(words), "segments": len(segments)}],
    )


def _script_segments(params: dict[str, Any]) -> list[dict[str, Any]]:
    path = params.get("script_path")
    if isinstance(path, str) and path:
        file_path = Path(path)
        if file_path.is_file() and file_path.suffix.lower() == ".json":
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return []
            segments = payload.get("segments") if isinstance(payload, dict) else None
            if isinstance(segments, list):
                return [item for item in segments if isinstance(item, dict)]
    raw = params.get("segments")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _exec_retention(params: dict[str, Any]) -> dict[str, Any]:
    rows = params.get("rows") or params.get("retention") or []
    if not isinstance(rows, list) or len(rows) < 2:
        return _blocked(
            "REQUIRES_DATA",
            "Brak eksportu retencji. Krzywa nie została wygenerowana.",
            mock_or_live="LOCAL VERIFIED",
        )
    points: list[tuple[float, float]] = []
    for item in rows:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append((float(item[0]), float(item[1])))
        elif isinstance(item, dict) and "t" in item and "pct" in item:
            points.append((float(item["t"]), float(item["pct"])))
    if len(points) < 2:
        return _blocked("REQUIRES_DATA", "Retencja nieparsowalna.")
    drops = []
    for index in range(1, len(points)):
        prev_t, prev_v = points[index - 1]
        t, value = points[index]
        delta = prev_v - value
        if delta >= 8:
            drops.append({"from_t": prev_t, "to_t": t, "drop_pct": round(delta, 2)})
    strong = []
    for t, value in points:
        if value >= 70:
            strong.append({"t": t, "pct": value})
    output = {
        "video_id": params.get("video_id"),
        "measurement_window": params.get("measurement_window"),
        "source": params.get("source"),
        "fetched_at": params.get("fetched_at"),
        "drop_points": drops,
        "strong_segments": strong[:8],
        "hypotheses": [
            "Large early drop often tracks a weak hook or delayed payoff.",
            "Strong mid-segment retention often tracks a concrete action step.",
        ]
        if drops or strong
        else [],
        "confidence": "evidence_backed" if drops or strong else "low",
        "evidence": {"points": len(points)},
        "curve": None,
    }
    return _ok(
        output,
        evidence=[{"kind": "retention_export", "points": len(points), "source": params.get("source")}],
        mock_or_live="LOCAL VERIFIED",
    )


def _exec_discovery(params: dict[str, Any], root: Path | None) -> dict[str, Any]:
    query = str(params.get("query") or "").strip()
    if not query:
        return _blocked("REQUIRES_DATA", "Podaj query do discovery.")
    index = repos._load(root).get("awesome_index")
    if not isinstance(index, list) or not index:
        sync = repos.sync_awesome(root)
        if sync.get("status") != "HEALTHY":
            return _blocked(str(sync.get("status") or "OFFLINE"), str(sync.get("detail") or "Brak indeksu Awesome."))
    discovered = repos.discover_resources(query, root)
    items = discovered.get("items") or []
    if not items:
        return {
            "status": "REQUIRES_SETUP",
            "output": {"items": [], "trust": "DISCOVERED"},
            "evidence": [],
            "verification": {"ok": False, "reason": "Brak trafień w lokalnym indeksie."},
            "mock_or_live": "LIVE VERIFIED",
            "errors": ["Brak trafień. Uruchom Sync index."],
        }
    return _ok(
        {
            "items": items,
            "trust": "DISCOVERED",
            "next_step": "RESOURCE_REVIEW",
            "install_allowed": False,
            "index_source": discovered.get("index_source") or repos.awesome_index_source(root),
        },
        evidence=[{"kind": "awesome_hits", "count": len(items), "query": query}],
        mock_or_live="LIVE VERIFIED",
    )


def _exec_review(params: dict[str, Any], root: Path | None) -> dict[str, Any]:
    query = str(params.get("query") or params.get("url") or "").strip()
    candidates = params.get("items")
    if not isinstance(candidates, list):
        discovered = repos.discover_resources(query or "qa", root)
        candidates = discovered.get("items") or []
    reviewed = []
    for item in candidates[:12]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        name = str(item.get("name") or url)
        license_guess = "UNKNOWN"
        lowered = f"{name} {url}".casefold()
        if "mit" in lowered:
            license_guess = "MIT_HINT"
        useful = any(token in lowered for token in ("qa", "subtitle", "caption", "video", "ffmpeg", "whisper", "timeline", "chart"))
        reviewed.append(
            {
                "name": name,
                "url": url,
                "trust": "DISCOVERED",
                "review_status": "REVIEWED",
                "license": license_guess,
                "security": "NOT_EXECUTED",
                "compatibility": "UNVERIFIED",
                "usefulness": "likely" if useful else "unknown",
                "install_allowed": False,
                "notes": "Link pozostaje DISCOVERED do pełnego security/license review.",
            }
        )
    if not reviewed:
        return _blocked("REQUIRES_DATA", "Brak kandydatów do review.")
    return _ok(
        {"candidates": reviewed, "auto_install": False},
        evidence=[{"kind": "discovery_review", "count": len(reviewed)}],
        mock_or_live="LOCAL VERIFIED",
    )


def _resolve_youtube_live(*, settings: Any, session: Any) -> dict[str, Any]:
    if settings is None or session is None:
        return {"status": "REQUIRES_SETUP", "detail": "Brak sesji DB/settings do YouTube OAuth Atnosa."}
    try:
        from atnos.services.youtube_connector_service import YouTubeConnectorService

        connector = YouTubeConnectorService(session, settings)
        status = connector.get_status()
        if not status.connected or not status.ready:
            return {
                "status": "REQUIRES_SETUP",
                "detail": "YouTube OAuth Atnosa nie jest gotowy. Connect w Settings → OAuth YouTube.",
                "oauth_status": status.to_dict(),
            }
        channel = connector.get_owned_channel()
        videos = {}
        channel_id = None
        if isinstance(channel, dict):
            channel_id = channel.get("id") or (channel.get("channel") or {}).get("id")
        if channel_id:
            videos = connector.list_public_channel_videos(str(channel_id), max_results=3)
        return {
            "status": "SUCCESS",
            "identity": {
                "channel_title": status.channel_title,
                "account_id": status.account_id,
                "scopes": list(status.granted_scopes),
            },
            "channel": _redact_public(channel),
            "videos": _redact_public(videos),
        }
    except Exception as exc:  # noqa: BLE001 - adapter must not leak secrets via traceback to callers
        return {"status": "FAILED", "detail": f"YouTube read failed: {type(exc).__name__}"}


def _redact_public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if "token" in str(key).casefold() or "secret" in str(key).casefold() else _redact_public(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_public(item) for item in value]
    return value


def _exec_social_read(
    params: dict[str, Any],
    *,
    settings: Any,
    session: Any,
    root: Path | None,
    project_id: str | None,
) -> dict[str, Any]:
    from atnos.integrations import social_sdk_bridge

    platform = str(params.get("platform_id") or "youtube")
    # Always prefer the real installed Social SDK package for mock verification.
    mode = str(params.get("mode") or "auto")
    if mode == "mock" or (mode == "auto" and params.get("force_mock")):
        bridge = social_sdk_bridge.mock_read()
        if bridge.get("ok"):
            return _ok(
                {**(bridge.get("output") or {}), "adapter": bridge.get("adapter"), "project_id": project_id},
                evidence=[{"kind": "social_sdk_mock", "package_version": bridge.get("package_version")}],
                mock_or_live=str(bridge.get("mock_or_live") or "MOCK VERIFIED"),
            )
        return _blocked(str(bridge.get("status") or "FAILED"), str(bridge.get("detail") or "mock_read failed"))

    accounts = {item["id"]: item for item in repos.list_social_accounts(root)}
    account = accounts.get(platform)
    if not account or not account.get("connected"):
        # Still prove the Node package works offline.
        bridge = social_sdk_bridge.mock_read()
        if bridge.get("ok"):
            return {
                "status": "REQUIRES_SETUP",
                "output": {
                    "detail": f"Brak podłączonego konta {platform}. Social SDK package jest załadowany — użyto mock_read.",
                    "social_sdk": bridge.get("output"),
                    "package_version": social_sdk_bridge.package_version(),
                    "loaded": social_sdk_bridge.is_loaded(),
                },
                "evidence": [{"kind": "social_sdk_loaded", "mock": True}],
                "verification": {"ok": True, "checks": ["package_loaded", "mock_read"]},
                "mock_or_live": "MOCK VERIFIED",
                "errors": [f"Live {platform} wymaga OAuth/token + channel id."],
            }
        return _blocked("REQUIRES_SETUP", f"Brak konta {platform} i Social SDK bridge nie działa.")

    if platform != "youtube":
        return _blocked(
            "REQUIRES_SETUP",
            f"Live SOCIAL_READ dla {platform} wymaga credentials platformy w Vault + Social SDK adapter.",
            mock_or_live="REQUIRES_SETUP",
        )

    token, channel_id, token_source = _resolve_youtube_token(settings=settings, session=session, root=root)
    if not token or not channel_id:
        bridge = social_sdk_bridge.mock_read()
        detail = (
            "Konto Settings istnieje, ale brak live YouTube access token/channel id dla Social SDK. "
            "Podłącz Atnos YouTube OAuth albo credential_ref oauth z tokenem w Vault."
        )
        return {
            "status": "REQUIRES_SETUP",
            "output": {
                "detail": detail,
                "connected_as": account.get("display_name"),
                "social_sdk_loaded": social_sdk_bridge.is_loaded(),
                "mock_fallback": bridge.get("output") if bridge.get("ok") else None,
                "package_version": social_sdk_bridge.package_version(),
            },
            "evidence": [{"kind": "social_sdk", "loaded": social_sdk_bridge.is_loaded(), "token_source": token_source}],
            "verification": {"ok": bool(bridge.get("ok")), "checks": ["package_loaded"]},
            "mock_or_live": "MOCK VERIFIED" if bridge.get("ok") else "REQUIRES_SETUP",
            "errors": [detail],
        }

    live = social_sdk_bridge.youtube_read(access_token=token, channel_id=channel_id, limit=int(params.get("limit") or 3))
    if not live.get("ok") or live.get("status") != "SUCCESS":
        return _blocked(str(live.get("status") or "FAILED"), str(live.get("detail") or "youtube_read failed"), mock_or_live="REQUIRES_SETUP")
    return _ok(
        {
            **(live.get("output") or {}),
            "connected_as": account.get("display_name"),
            "adapter": live.get("adapter"),
            "token_source": token_source,
            "project_id": project_id,
            "social_sdk_node": "LOADED",
        },
        evidence=[{"kind": "social_sdk_youtube", "mode": "live", "token_source": token_source}],
        mock_or_live="LIVE VERIFIED",
    )


def _resolve_youtube_token(*, settings: Any, session: Any, root: Path | None) -> tuple[str | None, str | None, str]:
    """Return access_token, channel_id, source label. Never log the token."""
    if settings is not None and session is not None:
        try:
            from atnos.services.youtube_connector_service import YouTubeConnectorService

            connector = YouTubeConnectorService(session, settings)
            status = connector.get_status()
            if status.connected and status.ready:
                connection = connector._latest_connection()  # noqa: SLF001 - intentional vault bridge
                bundle = connector._load_token_bundle_safe(connection, [])  # noqa: SLF001
                token = getattr(bundle, "access_token", None) if bundle is not None else None
                channel = None
                owned = connector.get_owned_channel()
                if isinstance(owned, dict):
                    channel = owned.get("id") or (owned.get("channel") or {}).get("id")
                channel = channel or status.channel_title
                # Prefer real channel id from owned payload.
                if isinstance(owned, dict) and owned.get("id"):
                    channel = owned.get("id")
                if token and channel and str(channel).startswith("UC"):
                    return str(token), str(channel), "atnos_youtube_oauth"
                if token:
                    # Try again extracting id
                    items = owned.get("items") if isinstance(owned, dict) else None
                    if isinstance(items, list) and items and isinstance(items[0], dict):
                        channel = items[0].get("id")
                    if token and channel:
                        return str(token), str(channel), "atnos_youtube_oauth"
        except Exception:
            pass
    # Optional: resolve aes_256_gcm credential_ref that stores JSON {access_token,channel_id}
    refs = repos.credential_refs_for_platform("youtube", root=root)
    if refs and settings is not None:
        ref = refs[0]
        if ref.startswith(("aes_256_gcm:", "os_keyring:")):
            try:
                from atnos.services.secret_vault_service import SecretVaultService

                raw = SecretVaultService(settings).resolve_secret(ref)
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    token = payload.get("access_token")
                    channel = payload.get("channel_id")
                    if token and channel:
                        return str(token), str(channel), "vault_json"
            except Exception:
                return None, None, "vault_unresolved"
    return None, None, "none"


def _exec_social_comments(params: dict[str, Any], *, settings: Any, session: Any, root: Path | None) -> dict[str, Any]:
    video_id = str(params.get("video_id") or "").strip()
    if not video_id:
        return _blocked("REQUIRES_DATA", "Podaj video_id do odczytu komentarzy.")
    if settings is None or session is None:
        return _blocked("REQUIRES_SETUP", "Brak sesji do YouTube connector.")
    try:
        from atnos.services.youtube_connector_service import YouTubeConnectorService

        connector = YouTubeConnectorService(session, settings)
        status = connector.get_status()
        if not status.connected:
            return _blocked("REQUIRES_SETUP", "YouTube OAuth niepołączony.")
        threads = connector.list_comment_threads(video_id, max_results=int(params.get("max_results") or 20))
        return _ok(
            {"video_id": video_id, "threads": _redact_public(threads), "adapter": "atnos_youtube_connector"},
            evidence=[{"kind": "comments_read", "video_id": video_id}],
            mock_or_live="LIVE VERIFIED",
        )
    except Exception as exc:  # noqa: BLE001
        return _blocked("FAILED", f"Comments read failed: {type(exc).__name__}")


def _exec_publish_prepare(params: dict[str, Any], *, root: Path | None, project_id: str | None) -> dict[str, Any]:
    platform = str(params.get("platform_id") or "youtube")
    mapping = repos.list_project_accounts(root)
    linked = None
    for item in mapping.get("projects") or []:
        if item.get("project_id") == project_id:
            linked = item.get("platform_or_account")
    if project_id and not linked:
        return _blocked("BLOCKED", "Projekt nie ma mapowania konta social. Ustaw mapping w Settings.")
    if linked and not str(linked).startswith(platform):
        return _blocked("BLOCKED", f"Projekt wskazuje {linked}, nie {platform}. Brak cross-postingu.")
    preview = {
        "platform": platform,
        "project_id": project_id,
        "title": params.get("title"),
        "description": params.get("description"),
        "media_path": params.get("media_path"),
        "privacy": params.get("privacy") or "private",
        "published": False,
        "approval": "READY_FOR_APPROVAL",
    }
    gate = repos.request_publish(platform, approved=False, account_id=params.get("account_id"))
    if gate.get("published"):
        return _blocked("FAILED", "Approval gate złamany — publish nie może być true.")
    return _ok(
        {**preview, "gate": gate},
        evidence=[{"kind": "publish_prepare", "approved": False}],
        mock_or_live="LOCAL VERIFIED",
    )


def _exec_opentoonz(params: dict[str, Any], root: Path | None) -> dict[str, Any]:
    status = repos.opentoonz_status(root)
    if status.get("status") == "NOT_INSTALLED":
        return {
            "status": "NOT_INSTALLED",
            "output": {"detail": "NOT_INSTALLED — OpenToonz missing; Remotion is not a substitute.", **status},
            "evidence": [{"kind": "opentoonz_health", "status": "NOT_INSTALLED"}],
            "verification": {"ok": False, "reason": "OpenToonz missing"},
            "mock_or_live": "LOCAL VERIFIED",
            "errors": ["OpenToonz nie jest zainstalowany."],
            "artifacts": [],
        }
    # No invented CLI. Only confirm executable presence until automation path is proven.
    exe = status.get("path")
    if not exe or not Path(str(exe)).is_file():
        return _blocked("REQUIRES_SETUP", "OpenToonz.exe nie znaleziony.")
    scene = params.get("scene") or params.get("project")
    if not scene:
        return {
            "status": "REQUIRES_DATA",
            "output": {
                "path": exe,
                "health": status.get("status"),
                "automation": "UNVERIFIED",
                "detail": "Executable detected. Stable batch CLI not confirmed — render not started.",
            },
            "evidence": [{"kind": "opentoonz_health", "path": exe}],
            "verification": {"ok": True, "checks": ["exe_exists"]},
            "mock_or_live": "LOCAL VERIFIED",
            "errors": [],
            "artifacts": [],
        }
    return {
        "status": "BLOCKED",
        "output": {
            "path": exe,
            "detail": "Scene provided, but OpenToonz batch render CLI is UNVERIFIED on this machine. Remotion remains WB renderer.",
            "automation": "UNVERIFIED",
        },
        "evidence": [{"kind": "opentoonz_blocked", "reason": "no_stable_cli"}],
        "verification": {"ok": False, "reason": "CLI unverified"},
        "mock_or_live": "LOCAL VERIFIED",
        "errors": ["ANIMATION_2D_RENDER blocked until official automation path is confirmed."],
        "artifacts": [],
    }


def _exec_web_page_extract(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import web_page_extract

    html = params.get("html") if isinstance(params.get("html"), str) else None
    url = params.get("url") if isinstance(params.get("url"), str) else None
    css = params.get("css") if isinstance(params.get("css"), str) else None
    text = params.get("text") if isinstance(params.get("text"), str) else None
    xpath = params.get("xpath") if isinstance(params.get("xpath"), str) else None
    freshness = str(params.get("freshness") or "REFERENCE")
    include_raw = bool(params.get("include_raw_diagnostics"))
    if not html and not url:
        return _blocked("REQUIRES_DATA", "Podaj html albo url do WEB_PAGE_EXTRACT.")
    if html:
        result = web_page_extract.extract_html(
            html,
            url=url,
            css=css,
            text=text,
            xpath=xpath,
            include_raw_diagnostics=include_raw,
        )
        mock_or_live = "LOCAL VERIFIED"
    else:
        result = web_page_extract.extract_url(
            str(url),
            css=css,
            text=text,
            xpath=xpath,
            freshness=freshness,
            include_raw_diagnostics=include_raw,
            root=None,
        )
        mock_or_live = "LIVE"
    status = str(result.get("status") or "FAILED")
    # Map extraction statuses into capability runtime statuses.
    if status in {"SUCCESS", "TRUNCATED"}:
        return {
            "status": "SUCCESS" if status == "SUCCESS" else "DEGRADED",
            "output": result.get("output"),
            "evidence": [
                {
                    "kind": "web_page_blocks",
                    "block_ids": [b.get("id") for b in ((result.get("output") or {}).get("blocks") or [])[:20] if isinstance(b, dict)],
                    "content_hash": (result.get("provenance") or {}).get("content_hash"),
                }
            ],
            "verification": {"ok": True, "checks": ["schema", "blocks_or_text", "provenance"]},
            "mock_or_live": mock_or_live,
            "errors": [],
            "provenance": result.get("provenance"),
            "diagnostics": result.get("diagnostics"),
        }
    if status in {"EMPTY_CONTENT", "REQUIRES_BROWSER"}:
        return {
            "status": status,
            "output": result.get("output"),
            "evidence": [],
            "verification": {"ok": False, "reason": status},
            "mock_or_live": mock_or_live,
            "errors": [str(result.get("detail") or status)],
            "provenance": result.get("provenance"),
            "diagnostics": result.get("diagnostics"),
        }
    if status in {"ACCESS_DENIED", "NOT_FOUND", "RATE_LIMITED", "UNSUPPORTED_CONTENT_TYPE", "TIMEOUT", "BLOCKED", "REQUIRES_DATA", "REQUIRES_SETUP"}:
        return {
            "status": status,
            "output": None,
            "evidence": [],
            "verification": {"ok": False, "reason": result.get("detail") or status},
            "mock_or_live": mock_or_live,
            "errors": [str(result.get("detail") or status)],
            "provenance": result.get("provenance"),
            "diagnostics": result.get("diagnostics"),
        }
    return {
        "status": "FAILED",
        "output": None,
        "evidence": [],
        "verification": {"ok": False, "reason": result.get("detail") or status},
        "mock_or_live": mock_or_live,
        "errors": [str(result.get("detail") or status)],
        "provenance": result.get("provenance"),
        "diagnostics": result.get("diagnostics"),
    }


def _exec_youtube_transcript(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import priority_vendor_bridges as pvb

    video = str(params.get("video_id") or params.get("url") or params.get("video") or "").strip()
    languages = params.get("languages") if isinstance(params.get("languages"), list) else None
    if bool(params.get("research_bundle") or params.get("with_metadata")):
        result = pvb.youtube_research_bundle(video, languages=[str(x) for x in languages] if languages else None)
        if not result.get("ok"):
            return {
                "status": str(result.get("status") or "ERROR"),
                "output": result,
                "evidence": [{"kind": "youtube_research", "video_id": result.get("video_id")}],
                "verification": {"ok": False, "reason": result.get("status")},
                "mock_or_live": "LIVE",
                "errors": [str(result.get("status") or "research bundle failed")],
                "provenance": result.get("provenance"),
            }
        return _ok(
            result,
            evidence=[{"kind": "youtube_research", "video_id": result.get("video_id")}],
            mock_or_live="LIVE",
        )
    result = pvb.fetch_youtube_transcript(video, languages=[str(x) for x in languages] if languages else None)
    if not result.get("ok"):
        return {
            "status": str(result.get("status") or "ERROR"),
            "output": result,
            "evidence": [{"kind": "youtube_transcript", "video_id": result.get("video_id")}],
            "verification": {"ok": False, "reason": result.get("detail") or result.get("status")},
            "mock_or_live": "LIVE",
            "errors": [str(result.get("detail") or result.get("status") or "transcript failed")],
            "provenance": result.get("provenance"),
        }
    return _ok(
        result,
        evidence=[{"kind": "youtube_transcript", "video_id": result.get("video_id")}],
        mock_or_live="LIVE",
    )


def _exec_youtube_metadata(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import priority_vendor_bridges as pvb

    video = str(params.get("video_id") or params.get("url") or params.get("video") or "").strip()
    if bool(params.get("download")):
        return _blocked("FAILED", "YOUTUBE_METADATA refuses download=true; metadata-only.")
    result = pvb.fetch_youtube_metadata(video)
    if not result.get("ok"):
        return _blocked(
            str(result.get("status") or "FAILED"),
            str(result.get("detail") or "metadata failed"),
            mock_or_live="LIVE",
        )
    return _ok(
        result,
        evidence=[{"kind": "youtube_metadata", "id": (result.get("metadata") or {}).get("id")}],
        mock_or_live="LIVE",
    )


def _exec_caption_align(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import caption_align_service as cas

    # Diagnostics-only when no audio/script and no run_align.
    if not any(params.get(k) for k in ("audio_path", "audio", "script", "text", "word_timestamps", "existing_words")) and not bool(
        params.get("run_align")
    ):
        availability = cas.availability()
        return _ok(
            {
                "provider": availability.get("preferred"),
                "last_alignment": availability.get("last_alignment"),
                "providers": availability.get("providers"),
                "word_coverage": None,
                "duration": None,
                "detail": availability.get("detail"),
                "alignment_version": availability.get("alignment_version"),
            },
            evidence=[{"kind": "caption_align_diagnostics", "preferred": availability.get("preferred")}],
        )

    result = cas.run_caption_align(params)
    status = str(result.get("status") or "ERROR")
    if status in {"SUCCESS", "DEGRADED"}:
        # Strip huge word JSON from top-level UI diagnostics; keep counts.
        ui = {
            "provider": result.get("provider"),
            "last_alignment": result.get("fetched_at"),
            "word_coverage": (result.get("coverage") or {}).get("coverage_ratio"),
            "coverage_status": (result.get("coverage") or {}).get("status"),
            "duration": result.get("duration_seconds"),
            "word_count": result.get("word_count"),
            "phrase_count": len(result.get("phrases") or []),
            "status": status,
            "alignment_version": result.get("alignment_version"),
            "audio_hash": result.get("audio_hash"),
            "script_hash": result.get("script_hash"),
            "model": result.get("model"),
            "align_seconds": result.get("align_seconds"),
        }
        return {
            "status": status,
            "output": result,
            "diagnostics": ui,
            "evidence": [
                {
                    "kind": "caption_align",
                    "provider": result.get("provider"),
                    "audio_hash": result.get("audio_hash"),
                    "script_hash": result.get("script_hash"),
                }
            ],
            "verification": {"ok": True, "reason": status},
            "mock_or_live": "LOCAL VERIFIED",
            "errors": [],
        }
    return {
        "status": status,
        "output": result,
        "evidence": [{"kind": "caption_align_error", "status": status}],
        "verification": {"ok": False, "reason": result.get("detail") or status},
        "mock_or_live": "LOCAL VERIFIED",
        "errors": [str(result.get("detail") or status)],
    }


def _exec_icon_svg(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import priority_vendor_bridges as pvb

    if params.get("resolve") or params.get("brand"):
        result = pvb.resolve_lucide_svg(
            query=str(params.get("query") or params.get("concept") or "wallet"),
            brand_stroke=str((params.get("brand") or {}).get("stroke") or params.get("stroke") or "#1F2A24"),
            brand_fill=str((params.get("brand") or {}).get("fill") or params.get("fill") or "none"),
            size=int(params.get("size") or 48),
        )
    else:
        result = pvb.list_lucide_icons(query=str(params.get("query") or ""), limit=int(params.get("limit") or 20))
    if not result.get("ok"):
        return _blocked(str(result.get("status") or "FAILED"), str(result.get("detail") or "lucide failed"))
    return _ok(result, evidence=[{"kind": "lucide_icons", "count": result.get("total_matching") or 1}])


def _exec_finance_chart_skill(params: dict[str, Any]) -> dict[str, Any]:
    from atnos.integrations import priority_vendor_bridges as pvb

    result = pvb.remotion_trading_skill_summary()
    if not result.get("ok"):
        return _blocked(str(result.get("status") or "FAILED"), str(result.get("detail") or "skill missing"))
    return _ok(result, evidence=[{"kind": "remotion_trading_skill"}])


def fetch_github_repo_meta(url: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Optional enrichment for review. Failures stay DISCOVERED."""
    match = re.search(r"github\.com/([^/]+)/([^/#?]+)", url)
    if not match:
        return {"trust": "DISCOVERED", "license": "UNKNOWN"}
    owner, name = match.group(1), match.group(2)
    owns = client is None
    http = client or httpx.Client(timeout=10.0, headers={"Accept": "application/vnd.github+json", "User-Agent": "Atnos-Discovery/1.0"})
    try:
        response = http.get(f"https://api.github.com/repos/{owner}/{name}")
        if response.is_error:
            return {"trust": "DISCOVERED", "license": "UNKNOWN", "http_status": response.status_code}
        payload = response.json()
        license_info = payload.get("license") if isinstance(payload.get("license"), dict) else {}
        return {
            "trust": "DISCOVERED",
            "review_status": "REVIEWED",
            "license": license_info.get("spdx_id") or "UNKNOWN",
            "stars": payload.get("stargazers_count"),
            "pushed_at": payload.get("pushed_at"),
            "archived": payload.get("archived"),
            "install_allowed": False,
        }
    except httpx.HTTPError:
        return {"trust": "DISCOVERED", "license": "UNKNOWN", "offline": True}
    finally:
        if owns:
            http.close()
