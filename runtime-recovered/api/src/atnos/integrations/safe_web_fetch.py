"""Central Atnos HTTP fetch for public web research.

SSRF-aware, scheme-guarded, throttled, cached. No stealth / browser extras.
"""
from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from atnos.integrations.public_data import (
    DECLARED_USER_AGENT,
    INTERNAL_THROTTLE_S,
    _LAST_CALL,
    data_root,
)

FETCH_TIMEOUT_S = 20.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_REDIRECTS = 5
ALLOWED_SCHEMES = frozenset({"http", "https"})
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
# Soft allow for XML/docs pages that are still text for Scrapling.
SOFT_TEXT_TYPES = ("text/plain", "text/xml", "application/xml")

# Research pages change slower than FX; shorter than encyclopedic wiki cache.
WEB_PAGE_CACHE_TTL_S = {
    "CURRENT": 15 * 60,
    "RECENT": 60 * 60,
    "HISTORICAL": 6 * 60 * 60,
    "REFERENCE": 6 * 60 * 60,
}

_CACHE: dict[str, dict[str, tuple[float, dict[str, Any]]]] = {}
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 410, 451})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _throttle(hostname: str) -> None:
    key = f"web:{hostname.casefold()}"
    now = time.monotonic()
    previous = _LAST_CALL.get(key, 0.0)
    wait = INTERNAL_THROTTLE_S - (now - previous)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[key] = time.monotonic()


def _bucket(root: Any) -> dict[str, tuple[float, dict[str, Any]]]:
    return _CACHE.setdefault(str(data_root(root)), {})


def cache_get(root: Any, key: str, now: float | None = None) -> dict[str, Any] | None:
    row = _bucket(root).get(key)
    stamp = time.time() if now is None else now
    if row is None or row[0] <= stamp:
        return None
    return dict(row[1])


def cache_put(root: Any, key: str, value: dict[str, Any], ttl_s: float, now: float | None = None) -> None:
    stamp = time.time() if now is None else now
    _bucket(root)[key] = (stamp + ttl_s, dict(value))


def clear_cache(root: Any = None) -> None:
    if root is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(data_root(root)), None)


def validate_public_url(url: str) -> dict[str, Any]:
    """Return {ok:True, url, host} or {ok:False, status, detail}."""
    raw = (url or "").strip()
    if not raw:
        return {"ok": False, "status": "REQUIRES_DATA", "detail": "URL is required."}
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").casefold()
    if scheme in {"file", "ftp", "javascript", "data", "about", "blob"}:
        return {"ok": False, "status": "BLOCKED", "detail": f"Scheme {scheme or 'missing'} is not allowed."}
    if scheme not in ALLOWED_SCHEMES:
        return {"ok": False, "status": "BLOCKED", "detail": "Only http/https URLs are allowed."}
    host = parsed.hostname
    if not host:
        return {"ok": False, "status": "BLOCKED", "detail": "URL must include a host."}
    lowered = host.casefold()
    if lowered in {"localhost", "metadata.google.internal"} or lowered.endswith(".local"):
        return {"ok": False, "status": "BLOCKED", "detail": "Local and metadata hosts are blocked."}
    # Literal IP in URL
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if _unsafe_ip(ip):
            return {"ok": False, "status": "BLOCKED", "detail": "Private or reserved IP targets are blocked."}
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return {"ok": False, "status": "FAILED", "detail": f"DNS lookup failed for {host}."}
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if _unsafe_ip(ip):
            return {"ok": False, "status": "BLOCKED", "detail": "Private or reserved IP targets are blocked."}
    return {"ok": True, "url": raw, "host": host, "scheme": scheme}


def _unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _content_type_allowed(content_type: str) -> tuple[bool, str]:
    ct = (content_type or "").casefold().split(";")[0].strip()
    if not ct:
        return False, "missing"
    if ct.startswith("image/") or ct in {"application/pdf", "application/json", "application/octet-stream"}:
        return False, ct
    if any(ct == item or ct.startswith(item) for item in HTML_CONTENT_TYPES):
        return True, ct
    if any(ct == item for item in SOFT_TEXT_TYPES):
        return True, ct
    return False, ct


def fetch_public_html(
    url: str,
    *,
    root: Any = None,
    client: httpx.Client | None = None,
    freshness: str = "REFERENCE",
    now: float | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch public HTML with SSRF checks on every redirect hop."""
    started = time.perf_counter()
    checked = validate_public_url(url)
    if not checked.get("ok"):
        return {
            "ok": False,
            "status": checked["status"],
            "detail": checked["detail"],
            "requested_url": url,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    cache_key = f"WEB_PAGE_EXTRACT:{checked['url']}"
    ttl = WEB_PAGE_CACHE_TTL_S.get(freshness.upper(), WEB_PAGE_CACHE_TTL_S["REFERENCE"])
    if use_cache:
        cached = cache_get(root, cache_key, now)
        if cached is not None:
            hit = dict(cached)
            hit["cache"] = "hit"
            hit["cache_age_s"] = round((time.time() if now is None else now) - float(hit.get("_cached_at") or 0), 1)
            hit["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return hit

    owns = client is None
    http = client or httpx.Client(
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=False,
        headers={
            "User-Agent": DECLARED_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    current = checked["url"]
    redirect_chain: list[str] = []
    try:
        response: httpx.Response | None = None
        for _hop in range(MAX_REDIRECTS + 1):
            hop_check = validate_public_url(current)
            if not hop_check.get("ok"):
                return {
                    "ok": False,
                    "status": hop_check["status"],
                    "detail": f"Redirect/SSRF blocked: {hop_check['detail']}",
                    "requested_url": url,
                    "final_url": current,
                    "redirect_chain": redirect_chain,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            _throttle(str(hop_check["host"]))
            try:
                response = http.get(current)
            except httpx.TimeoutException:
                return {
                    "ok": False,
                    "status": "TIMEOUT",
                    "detail": "Fetch timed out.",
                    "requested_url": url,
                    "final_url": current,
                    "redirect_chain": redirect_chain,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except httpx.HTTPError as exc:
                return {
                    "ok": False,
                    "status": "FAILED",
                    "detail": f"Fetch failed: {exc.__class__.__name__}",
                    "requested_url": url,
                    "final_url": current,
                    "redirect_chain": redirect_chain,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return {
                        "ok": False,
                        "status": "FAILED",
                        "detail": "Redirect without Location header.",
                        "requested_url": url,
                        "http_status": response.status_code,
                        "redirect_chain": redirect_chain,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                next_url = str(httpx.URL(current).join(location))
                redirect_chain.append(next_url)
                current = next_url
                continue

            # Retry once for retryable statuses (respect Retry-After for 429).
            if response.status_code in RETRYABLE_STATUS:
                wait = _retry_after_seconds(response) if response.status_code == 429 else 0.25
                if wait is not None and wait <= 2.0:
                    time.sleep(max(wait, 0.05))
                    try:
                        response = http.get(current)
                    except httpx.HTTPError:
                        pass

            break
        else:
            return {
                "ok": False,
                "status": "BLOCKED",
                "detail": f"Too many redirects (>{MAX_REDIRECTS}).",
                "requested_url": url,
                "redirect_chain": redirect_chain,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        assert response is not None
        final_url = str(response.url) if not redirect_chain else current
        final_check = validate_public_url(final_url)
        if not final_check.get("ok"):
            return {
                "ok": False,
                "status": final_check["status"],
                "detail": f"Final URL blocked: {final_check['detail']}",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "status": "ACCESS_DENIED",
                "detail": f"HTTP {response.status_code}: access denied (no stealth bypass).",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        if response.status_code == 404:
            return {
                "ok": False,
                "status": "NOT_FOUND",
                "detail": "HTTP 404: page not found.",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": 404,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        if response.status_code == 429:
            return {
                "ok": False,
                "status": "RATE_LIMITED",
                "detail": "HTTP 429: rate limited; Retry-After respected once when short.",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": 429,
                "retry_after": _retry_after_seconds(response),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        if response.status_code >= 500:
            return {
                "ok": False,
                "status": "FAILED",
                "detail": f"HTTP {response.status_code}: upstream error.",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        if response.is_error:
            return {
                "ok": False,
                "status": "FAILED",
                "detail": f"HTTP {response.status_code}.",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        content_type = response.headers.get("content-type") or ""
        allowed, normalized_ct = _content_type_allowed(content_type)
        if not allowed:
            return {
                "ok": False,
                "status": "UNSUPPORTED_CONTENT_TYPE",
                "detail": f"Unsupported content-type: {normalized_ct}",
                "requested_url": url,
                "final_url": final_url,
                "redirect_chain": redirect_chain,
                "http_status": response.status_code,
                "content_type": normalized_ct,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }

        raw = response.content[: MAX_RESPONSE_BYTES + 1]
        truncated = len(raw) > MAX_RESPONSE_BYTES
        if truncated:
            raw = raw[:MAX_RESPONSE_BYTES]
        html = raw.decode(response.encoding or "utf-8", errors="replace")
        content_hash = hashlib.sha256(raw).hexdigest()
        fetched_at = _now()
        cross_domain = False
        try:
            cross_domain = urlparse(url).hostname != urlparse(final_url).hostname
        except Exception:
            cross_domain = bool(redirect_chain)

        payload = {
            "ok": True,
            "status": "SUCCESS",
            "requested_url": url,
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "cross_domain_redirect": cross_domain,
            "http_status": response.status_code,
            "content_type": normalized_ct,
            "html": html,
            "raw_bytes": len(raw),
            "truncated": truncated,
            "content_hash": content_hash,
            "fetched_at": fetched_at,
            "cache": "miss",
            "hostname": final_check["host"],
            "user_agent": DECLARED_USER_AGENT,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "_cached_at": time.time() if now is None else now,
        }
        if use_cache and not truncated:
            # Cache without huge html duplication risk: store copy for research reuse.
            cache_put(root, cache_key, payload, ttl, now)
        return payload
    finally:
        if owns:
            http.close()
