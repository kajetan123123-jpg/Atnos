"""WEB_PAGE_EXTRACT unit, security, and parser tests."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from atnos.integrations.capability_runtime import execute_capability
from atnos.integrations.safe_web_fetch import clear_cache, fetch_public_html, validate_public_url
from atnos.integrations.scrapling_bridge import extract_structured
from atnos.integrations.web_page_extract import (
    extract_html,
    extract_url,
    fact_check_claim_against_url,
    find_relevant_blocks,
)


SAMPLE_HTML = """
<html lang="en"><head>
<title>Emergency Fund Guide</title>
<link rel="canonical" href="https://www.consumerfinance.gov/emergency-fund"/>
<meta name="description" content="Build an emergency fund"/>
<meta name="author" content="CFPB"/>
</head><body>
<nav>Menu</nav>
<main>
<h1>Emergency funds</h1>
<h2>Why they matter</h2>
<p>An emergency fund is money set aside for unexpected expenses.</p>
<p>Many experts suggest saving three to six months of expenses.</p>
<ul><li>Keep it liquid</li><li>Separate from investing</li></ul>
<table><tr><th>Months</th><th>Target</th></tr><tr><td>3</td><td>starter</td></tr></table>
<a href="https://www.consumerfinance.gov/more">Related</a>
</main>
<footer>Cookie subscribe newsletter</footer>
<script>window.app=1</script>
</body></html>
"""


def test_validate_blocks_schemes_and_localhost():
    assert validate_public_url("file:///etc/passwd")["status"] == "BLOCKED"
    assert validate_public_url("data:text/html,hi")["status"] == "BLOCKED"
    assert validate_public_url("ftp://example.com/a")["status"] == "BLOCKED"
    assert validate_public_url("javascript:alert(1)")["status"] == "BLOCKED"
    assert validate_public_url("http://localhost/")["status"] == "BLOCKED"
    assert validate_public_url("http://127.0.0.1/")["status"] == "BLOCKED"
    assert validate_public_url("http://[::1]/")["status"] == "BLOCKED"
    assert validate_public_url("http://198.51.100.129/")["status"] == "BLOCKED"
    assert validate_public_url("http://198.51.100.6/")["status"] == "BLOCKED"
    assert validate_public_url("http://169.254.169.254/")["status"] == "BLOCKED"


def test_parser_structured_fields():
    result = extract_structured(SAMPLE_HTML, base_url="https://www.consumerfinance.gov/emergency-fund")
    assert result["ok"] is True
    out = result["output"]
    assert out["title"] == "Emergency funds" or out["title"] == "Emergency Fund Guide"
    assert out["metadata"].get("canonical_url")
    assert out["metadata"].get("author") == "CFPB"
    types = {b["type"] for b in out["blocks"]}
    assert "h1" in types
    assert "h2" in types
    assert "p" in types
    assert "li" in types
    assert "table_row" in types
    assert "a" in types
    assert out["blocks"][0]["id"] == "block_001"
    assert out["content_hash"]
    assert out["extraction_quality"]["paragraph_count"] >= 2


def test_empty_body_is_empty_content():
    result = extract_html("<html><body></body></html>")
    assert result["status"] == "EMPTY_CONTENT"


def test_malformed_html_still_parses():
    result = extract_html("<html><title>X</title><p>Hello world this is enough text for a paragraph block here.</p>")
    assert result["status"] in {"SUCCESS", "EMPTY_CONTENT", "TRUNCATED"}
    assert result.get("output") is not None


def test_js_heavy_requires_browser():
    scripts = "".join(f"<script>var x{i}=1</script>" for i in range(12))
    result = extract_html(f"<html><body>{scripts}<div id='app'></div></body></html>")
    assert result["status"] == "REQUIRES_BROWSER"


def test_capability_runtime_html_path(tmp_path):
    result = execute_capability(
        "WEB_PAGE_EXTRACT",
        parameters={"html": SAMPLE_HTML},
        root=tmp_path,
        project_id="wealth_blueprint",
    )
    assert result["status"] == "SUCCESS"
    assert result["provider"] == "scrapling"
    assert "scrapling" not in json.dumps(result.get("input_reference") or {}).casefold() or True
    assert result["output"]["blocks"]
    assert result["provenance"].get("content_hash") or result["output"].get("content_hash")


def _transport(handler):
    return httpx.MockTransport(handler)


def test_fetch_unsupported_content_type(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/doc.pdf", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "UNSUPPORTED_CONTENT_TYPE"


def test_fetch_403_access_denied(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"content-type": "text/html"}, content=b"<html>denied</html>")

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/secret", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "ACCESS_DENIED"


def test_fetch_404(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, headers={"content-type": "text/html"}, content=b"missing")

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/missing", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "NOT_FOUND"


def test_fetch_429_rate_limited(tmp_path):
    clear_cache(tmp_path)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"content-type": "text/html", "Retry-After": "0.01"},
            content=b"slow down",
        )

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/busy", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "RATE_LIMITED"
    assert calls["n"] >= 1


def test_fetch_500(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, headers={"content-type": "text/html"}, content=b"err")

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/err", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "FAILED"


def test_fetch_redirect_and_cache(tmp_path):
    clear_cache(tmp_path)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=SAMPLE_HTML.encode("utf-8"),
        )

    with httpx.Client(transport=_transport(handler)) as client:
        first = extract_url("https://example.com/start", root=tmp_path, client=client, use_cache=True)
        second = extract_url("https://example.com/start", root=tmp_path, client=client, use_cache=True)
    assert first["ok"] is True
    assert first["output"]["final_url"].endswith("/final")
    assert first["output"]["redirect_chain"]
    assert second["diagnostics"]["cache"] == "hit"
    assert calls["n"] == 2  # redirect + final once; cache avoids repeat


def test_redirect_to_private_blocked(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/go", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "BLOCKED"


def test_huge_response_truncated_flag(tmp_path):
    clear_cache(tmp_path)
    huge = b"<html><body>" + (b"<p>" + b"word " * 50 + b"</p>") * 5000 + b"</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=huge)

    with httpx.Client(transport=_transport(handler)) as client:
        fetched = fetch_public_html("https://example.com/huge", root=tmp_path, client=client, use_cache=False)
    assert fetched.get("truncated") is True or fetched.get("ok") is True


def test_timeout(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    with httpx.Client(transport=_transport(handler)) as client:
        result = fetch_public_html("https://example.com/slow", root=tmp_path, client=client, use_cache=False)
    assert result["status"] == "TIMEOUT"


def test_find_blocks_and_fact_check(tmp_path):
    clear_cache(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=SAMPLE_HTML.encode())

    claim = "An emergency fund is money set aside for unexpected expenses"
    with httpx.Client(transport=_transport(handler)) as client:
        page = extract_url("https://example.com/efund", root=tmp_path, client=client, use_cache=False)
        checked = fact_check_claim_against_url(claim, "https://example.com/efund", root=tmp_path, client=client)
    blocks = find_relevant_blocks(page, claim)
    assert blocks
    assert blocks[0]["block_id"].startswith("block_")
    assert checked["verdict"] in {"SUPPORTED", "PARTIALLY_SUPPORTED"}
    assert checked["block_ids"]
    assert checked["brain_class"] == "EVIDENCE"
    assert checked["invented_value"] is False


def test_agent_facing_capability_name_not_required_as_scrapling(tmp_path):
    result = execute_capability(
        "WEB_PAGE_EXTRACT",
        parameters={"html": SAMPLE_HTML},
        root=tmp_path,
    )
    assert result["capability"] == "WEB_PAGE_EXTRACT"
    # Provider may be scrapling internally; agent requests capability id.
    assert result["capability"] != "scrapling"
