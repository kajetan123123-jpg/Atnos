"""Scrapling parser bridge — extraction only. Network stays in safe_web_fetch."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any

VENDOR_ROOT = r"E:\Atnos\Tools\external-repos\scrapling"
PINNED_VERSION = "0.4.15"
PINNED_COMMIT = "e0d4d7563207b70c2cb38487c4e0dcbe0e2f04ca"
MAX_HTML_BYTES = 2_000_000
MAX_BLOCKS = 200
MAX_PAGE_TEXT_CHARS = 40_000
EXTRACTION_TIMEOUT_S = 8.0

_BOILERPLATE_TAGS = frozenset({"nav", "footer", "header", "aside", "script", "style", "noscript", "iframe", "form"})
_BOILERPLATE_RE = re.compile(
    r"\b(cookie|subscribe|newsletter|related posts|share this|advertisement|privacy policy|terms of service)\b",
    re.I,
)


def is_loaded() -> bool:
    try:
        from scrapling.parser import Selector  # noqa: F401

        return True
    except Exception:
        return False


def package_version() -> str | None:
    try:
        import scrapling

        version = getattr(scrapling, "__version__", None)
        return str(version) if version else PINNED_VERSION
    except Exception:
        return None


def status() -> dict[str, Any]:
    loaded = is_loaded()
    return {
        "status": "HEALTHY" if loaded else "REQUIRES_SETUP",
        "loaded": loaded,
        "package_version": package_version(),
        "pinned_version": PINNED_VERSION,
        "pinned_commit": PINNED_COMMIT,
        "local_path": VENDOR_ROOT,
        "mode": "HTTP extraction",
        "browser": "OFF",
        "stealth": "OFF",
        "fetchers": "NOT_INSTALLED",
        "capability": "WEB_PAGE_EXTRACT",
        "detail": (
            "Scrapling parser available (BSD-3-Clause). "
            "Fetch uses Atnos safe_web_fetch + DECLARED_USER_AGENT; stealth/browser extras OFF."
            if loaded
            else "pip install scrapling into the Atnos API venv on E:."
        ),
    }


def _blocked(status_code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "status": status_code, "detail": detail}


def _text_of(node: Any) -> str:
    if node is None:
        return ""
    value = getattr(node, "text", None)
    if isinstance(value, str) and value.strip():
        return re.sub(r"\s+", " ", value).strip()
    if hasattr(node, "get_all_text"):
        try:
            return re.sub(r"\s+", " ", str(node.get_all_text() or "")).strip()
        except Exception:
            pass
    return re.sub(r"\s+", " ", str(node or "")).strip()


def _meta(page: Any, name: str) -> str | None:
    try:
        value = page.css(f'meta[name="{name}"]::attr(content)').get()
        if value and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        value = page.css(f'meta[property="{name}"]::attr(content)').get()
        if value and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    return None


def _is_boilerplate_text(text: str) -> bool:
    if len(text) < 25 and _BOILERPLATE_RE.search(text):
        return True
    return bool(_BOILERPLATE_RE.search(text) and len(text) < 80)


def extract_structured(
    html: str,
    *,
    base_url: str | None = None,
    css: str | None = None,
    text_query: str | None = None,
    xpath: str | None = None,
    include_raw_diagnostics: bool = False,
) -> dict[str, Any]:
    """Parse HTML into structured main-content blocks. No network."""
    started = time.perf_counter()
    if not is_loaded():
        return _blocked("REQUIRES_SETUP", "Scrapling parser is not installed in the Atnos API venv.")
    if not isinstance(html, str) or not html.strip():
        return _blocked("EMPTY_CONTENT", "HTML body is empty.")
    encoded = html.encode("utf-8", errors="replace")
    if len(encoded) > MAX_HTML_BYTES:
        return _blocked("TRUNCATED", f"HTML exceeds {MAX_HTML_BYTES} bytes before parse.")

    from scrapling.parser import Selector

    try:
        page = Selector(html, url=base_url) if base_url else Selector(html)
    except TypeError:
        page = Selector(html)
    except Exception as exc:
        return _blocked("FAILED", f"Parser failed: {exc.__class__.__name__}")

    elapsed = time.perf_counter() - started
    if elapsed > EXTRACTION_TIMEOUT_S:
        return _blocked("TIMEOUT", "Extraction exceeded time budget.")

    title = ""
    try:
        title = str(page.css("title::text").get() or "").strip()
    except Exception:
        title = ""

    metadata: dict[str, Any] = {}
    for key, names in (
        ("description", ("description", "og:description")),
        ("author", ("author", "article:author")),
        ("published_at", ("article:published_time", "datePublished", "pubdate")),
        ("modified_at", ("article:modified_time", "dateModified")),
        ("language", ("og:locale",)),
    ):
        for name in names:
            value = _meta(page, name)
            if value:
                metadata[key] = value
                break
    try:
        lang = page.css("html::attr(lang)").get()
        if lang and str(lang).strip() and "language" not in metadata:
            metadata["language"] = str(lang).strip()
    except Exception:
        pass
    try:
        canonical = page.css('link[rel="canonical"]::attr(href)').get()
        if canonical and str(canonical).strip():
            metadata["canonical_url"] = str(canonical).strip()
    except Exception:
        pass

    # Optional targeted query path (still structured).
    if css or xpath or text_query:
        matches: list[str] = []
        mode = "css" if css else ("xpath" if xpath else "find_by_text")
        if css:
            selected = page.css(css)
            matches = [str(item).strip() for item in getattr(selected, "getall", lambda: [])() if str(item).strip()]
            if not matches and hasattr(selected, "get"):
                value = selected.get()
                if value:
                    matches = [str(value).strip()]
        elif xpath:
            selected = page.xpath(xpath)
            matches = [str(item).strip() for item in getattr(selected, "getall", lambda: [])() if str(item).strip()]
        else:
            found = page.find_by_text(text_query) if hasattr(page, "find_by_text") else None
            if isinstance(found, list):
                matches = [_text_of(item) for item in found if _text_of(item)]
            elif found is not None:
                value = _text_of(found)
                matches = [value] if value else []
        blocks = [
            {"id": f"block_{idx:03d}", "type": "match", "text": item[:2000], "level": None}
            for idx, item in enumerate(matches[:MAX_BLOCKS], start=1)
        ]
        return {
            "ok": True,
            "status": "SUCCESS" if blocks else "EMPTY_CONTENT",
            "output": {
                "title": title,
                "mode": mode,
                "blocks": blocks,
                "headings": [b for b in blocks if False],
                "paragraphs": blocks,
                "lists": [],
                "tables": [],
                "links": [],
                "page_text": "\n\n".join(b["text"] for b in blocks)[:MAX_PAGE_TEXT_CHARS],
                "metadata": metadata,
                "extraction_quality": {
                    "block_count": len(blocks),
                    "extracted_chars": sum(len(b["text"]) for b in blocks),
                    "main_content_confidence": 0.5 if blocks else 0.0,
                    "boilerplate_ratio": None,
                },
                "parser": "scrapling",
                "extraction_version": PINNED_VERSION,
                "package_version": package_version(),
                "content_hash": hashlib.sha256(encoded).hexdigest(),
                "truncated": False,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        }

    script_count = 0
    try:
        scripts = page.css("script")
        script_count = len(getattr(scripts, "getall", lambda: list(scripts))())
    except Exception:
        script_count = html.casefold().count("<script")

    blocks: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    paragraphs: list[dict[str, Any]] = []
    lists: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    skipped_boilerplate = 0

    def add_block(kind: str, text: str, *, level: int | None = None, href: str | None = None) -> None:
        nonlocal skipped_boilerplate
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return
        if _is_boilerplate_text(cleaned):
            skipped_boilerplate += 1
            return
        if len(blocks) >= MAX_BLOCKS:
            return
        block_id = f"block_{len(blocks) + 1:03d}"
        item = {"id": block_id, "type": kind, "text": cleaned[:2000], "level": level}
        if href:
            item["href"] = href
        blocks.append(item)
        if kind.startswith("h"):
            headings.append(item)
        elif kind == "p":
            paragraphs.append(item)
        elif kind == "li":
            lists.append(item)
        elif kind == "table_row":
            tables.append(item)
        elif kind == "a":
            links.append(item)

    # Prefer article/main when present; query from page with scoped CSS.
    scope_prefix = ""
    for selector in ("article", "main", "[role=main]"):
        try:
            found = page.css(selector)
            if found is not None and len(found) > 0:
                scope_prefix = f"{selector} "
                break
        except Exception:
            continue

    def scoped(css: str) -> Any:
        if scope_prefix:
            return page.css(f"{scope_prefix}{css}")
        return page.css(css)

    for level in (1, 2, 3):
        try:
            for node in scoped(f"h{level}"):
                add_block(f"h{level}", _text_of(node), level=level)
        except Exception:
            continue
    try:
        for node in scoped("p"):
            add_block("p", _text_of(node))
    except Exception:
        pass
    try:
        for node in scoped("li"):
            text = _text_of(node)
            if text:
                add_block("li", text)
    except Exception:
        pass
    try:
        for row in scoped("table tr"):
            cells = [_text_of(cell) for cell in row.css("th, td")]
            cells = [c for c in cells if c]
            if cells:
                add_block("table_row", " | ".join(cells))
    except Exception:
        pass
    try:
        for node in scoped("a[href]"):
            href = ""
            try:
                href = str(node.attrib.get("href") if getattr(node, "attrib", None) else "")
                if not href:
                    href = str(node.css("::attr(href)").get() or "").strip()
            except Exception:
                href = ""
            text = _text_of(node)
            if text and href and not href.startswith("#"):
                add_block("a", text, href=href)
    except Exception:
        pass

    # Fallback: if structure empty, use full text split into paragraphs.
    if not paragraphs and not headings:
        try:
            full = _text_of(page)
        except Exception:
            full = ""
        for chunk in re.split(r"\n{2,}|(?<=\.)\s+(?=[A-Z])", full):
            if len(chunk.strip()) >= 40:
                add_block("p", chunk.strip())

    page_text_parts = [b["text"] for b in blocks if b["type"] in {"h1", "h2", "h3", "p", "li", "table_row"}]
    page_text = "\n\n".join(page_text_parts)
    truncated = False
    if len(page_text) > MAX_PAGE_TEXT_CHARS:
        page_text = page_text[:MAX_PAGE_TEXT_CHARS]
        truncated = True

    extracted_chars = len(page_text)
    content_blocks = [b for b in blocks if b["type"] in {"h1", "h2", "h3", "p", "li", "table_row"}]
    total_considered = len(content_blocks) + skipped_boilerplate
    boilerplate_ratio = (skipped_boilerplate / total_considered) if total_considered else None
    main_confidence = 0.0
    if extracted_chars >= 400 and len(headings) >= 1 and len(paragraphs) >= 2:
        main_confidence = 0.85
    elif extracted_chars >= 200 and (paragraphs or headings):
        main_confidence = 0.65
    elif extracted_chars >= 80:
        main_confidence = 0.4
    elif extracted_chars > 0:
        main_confidence = 0.2

    js_heavy = script_count >= 8 and extracted_chars < 120
    if js_heavy:
        status_code = "REQUIRES_BROWSER"
        ok = False
    elif extracted_chars < 40:
        status_code = "EMPTY_CONTENT"
        ok = False
    elif truncated:
        status_code = "TRUNCATED"
        ok = True
    else:
        status_code = "SUCCESS"
        ok = True

    output: dict[str, Any] = {
        "title": title,
        "mode": "structured_main",
        "blocks": blocks[:MAX_BLOCKS],
        "headings": headings,
        "paragraphs": paragraphs,
        "lists": lists,
        "tables": tables,
        "links": links[:80],
        "page_text": page_text,
        "metadata": metadata,
        "extraction_quality": {
            "block_count": len(blocks),
            "heading_count": len(headings),
            "paragraph_count": len(paragraphs),
            "extracted_chars": extracted_chars,
            "script_count": script_count,
            "main_content_confidence": main_confidence,
            "boilerplate_ratio": round(boilerplate_ratio, 3) if boilerplate_ratio is not None else None,
            "skipped_boilerplate_blocks": skipped_boilerplate,
        },
        "parser": "scrapling",
        "extraction_version": PINNED_VERSION,
        "package_version": package_version(),
        "content_hash": hashlib.sha256(page_text.encode("utf-8", errors="replace")).hexdigest(),
        "raw_html_hash": hashlib.sha256(encoded).hexdigest(),
        "truncated": truncated,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if include_raw_diagnostics:
        output["diagnostics_raw_length"] = len(html)
        output["diagnostics_script_count"] = script_count

    return {"ok": ok, "status": status_code, "output": output, "detail": None if ok else status_code}


# Backwards-compatible aliases used by earlier WEB_PAGE_EXTRACT wiring.
def parse_html(
    html: str,
    *,
    css: str | None = None,
    text: str | None = None,
    xpath: str | None = None,
) -> dict[str, Any]:
    return extract_structured(html, css=css, text_query=text, xpath=xpath)


def fetch_and_parse(
    url: str,
    *,
    css: str | None = None,
    text: str | None = None,
    xpath: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper — prefer atnos.integrations.web_page_extract.extract_url."""
    from atnos.integrations.safe_web_fetch import fetch_public_html

    fetched = fetch_public_html(url)
    if not fetched.get("ok"):
        return {
            "ok": False,
            "status": fetched.get("status") or "FAILED",
            "detail": fetched.get("detail"),
        }
    parsed = extract_structured(
        str(fetched.get("html") or ""),
        base_url=str(fetched.get("final_url") or url),
        css=css,
        text_query=text,
        xpath=xpath,
    )
    if not isinstance(parsed.get("output"), dict):
        return parsed
    output = dict(parsed["output"])
    output["url"] = fetched.get("requested_url") or url
    output["final_url"] = fetched.get("final_url")
    output["http_status"] = fetched.get("http_status")
    output["content_type"] = fetched.get("content_type")
    return {"ok": parsed.get("ok"), "status": parsed.get("status"), "output": output, "detail": parsed.get("detail")}
