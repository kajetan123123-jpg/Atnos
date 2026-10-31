"""Public data capabilities. No API keys. Missing data stays an error, not a guess.

The one-second gap is an Atnos INTERNAL_THROTTLE. It is not a published provider quota.
Frankfurter's official FAQ says there is no daily or monthly cap; a numeric abuse
threshold is UNVERIFIED. Cache windows are Atnos policy, documented next to each
capability, because the sources themselves are daily or historical.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

from atnos.integrations.wikimedia_identity import (
    dump_state,
    load_state,
    read_identity,
    request_headers,
)

DECLARED_USER_AGENT = "Atnos/0.1 (local desktop research)"
READ_HEADERS = {
    "User-Agent": DECLARED_USER_AGENT,
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}

INTERNAL_THROTTLE_S = 1.0
_LAST_CALL: dict[str, float] = {}
_CACHE: dict[str, dict[str, tuple[float, dict[str, Any]]]] = {}

# Frankfurter docs: almost all rates are daily, not intraday, and the FAQ asks
# high-volume clients to cache. One hour is shorter than that daily cycle.
FX_CACHE_TTL_S = 60 * 60
# World Bank GDP-style indicators are annual. Seven days avoids repeat pulls
# during production without treating the series as realtime.
MACRO_CACHE_TTL_S = 7 * 24 * 60 * 60
# SEC submissions are disseminated with a typical delay under a second
# (data.sec.gov API page). Fifteen minutes only collapses repeats inside one
# research pass. It is not the SEC fair-access limit of 10 requests/second.
SEC_CACHE_TTL_S = 15 * 60
# MediaWiki etiquette says to cache reusable reads. Six hours is Atnos policy
# for an encyclopedic summary, not a published Wikimedia quota.
WIKI_CACHE_TTL_S = 6 * 60 * 60
_WIKI_LOCK = __import__("threading").Lock()
_LAST_HEALTH: dict[str, str] = {}

CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "frankfurter",
        "name": "Frankfurter",
        "type": "public_api",
        "category": "Data",
        "capabilities": ["FX_RATES"],
        "freshness": "delayed",
        "cost": "FREE",
        "auth_type": "none",
        "self_host": True,
        "permissions": ["read web"],
        "docs_url": "https://frankfurter.dev/",
        "summary": "Dzienne kursy walut z banków centralnych. Oficjalna dokumentacja: brak klucza API.",
        "limits": (
            "Oficjalny FAQ: brak dziennych i miesięcznych quota. "
            "Liczbowy próg anti-abuse jest UNVERIFIED. "
            "Atnos dokłada własny INTERNAL_THROTTLE 1 s; to nie jest limit Frankfurtera."
        ),
        "cache_ttl_s": FX_CACHE_TTL_S,
        "throttle_kind": "INTERNAL_THROTTLE",
    },
    {
        "id": "world_bank",
        "name": "World Bank Indicators",
        "type": "public_api",
        "category": "Data",
        "capabilities": ["MACRO_INDICATORS"],
        "freshness": "historical",
        "cost": "FREE",
        "auth_type": "none",
        "self_host": False,
        "permissions": ["read web"],
        "docs_url": "https://datahelpdesk.worldbank.org/knowledgebase/articles/889392",
        "summary": "Historyczne wskaźniki rozwoju, w tym PKB. Oficjalny help desk: klucze API nie są wymagane.",
        "limits": (
            "Dokumentacja V2 podaje limity długości URL i liczby wskaźników w jednym zapytaniu, "
            "nie dzienny quota. INTERNAL_THROTTLE 1 s jest polityką Atnosa."
        ),
        "cache_ttl_s": MACRO_CACHE_TTL_S,
        "throttle_kind": "INTERNAL_THROTTLE",
    },
    {
        "id": "sec_edgar",
        "name": "SEC EDGAR",
        "type": "public_api",
        "category": "Data",
        "capabilities": ["COMPANY_FILINGS", "SEC_FILINGS", "PUBLIC_COMPANY_FILINGS"],
        "freshness": "delayed",
        "cost": "FREE",
        "auth_type": "none",
        "self_host": False,
        "permissions": ["read web"],
        "docs_url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "summary": "Publiczne zgłoszenia amerykańskich spółek. Oficjalna strona SEC: API data.sec.gov nie wymaga klucza.",
        "limits": (
            "Oficjalny fair access: maksymalnie 10 zapytań na sekundę "
            "(sec.gov/os/accessing-edgar-data, strona z 26 czerwca 2024). "
            "Wymagany opisowy User-Agent. INTERNAL_THROTTLE 1 s jest surowszy niż ten limit i nie jest limitem SEC."
        ),
        "cache_ttl_s": SEC_CACHE_TTL_S,
        "throttle_kind": "INTERNAL_THROTTLE",
        "provider_rate_limit": "10/s",
    },
    {
        "id": "wikimedia",
        "name": "Wikipedia / Wikimedia",
        "type": "public_api",
        "category": "Research",
        "capabilities": ["ENCYCLOPEDIC_KNOWLEDGE"],
        "freshness": "reference",
        "cost": "FREE",
        "auth_type": "none",
        "self_host": False,
        "permissions": ["NETWORK_READ"],
        "docs_url": "https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy",
        "summary": "Skrót hasła z angielskiej Wikipedii. To kontekst encyklopedyczny, nie zamiennik danych SEC ani Banku Światowego.",
        "limits": (
            "Oficjalna etykieta MediaWiki: brak twardego liczbowego limitu odczytu. "
            "PROVIDER_LIMIT liczbowy: UNVERIFIED. "
            "OUR_INTERNAL_LIMIT: jedno wywołanie naraz i 1 s między wywołaniami. "
            "Konto Wikimedia nie jest wymagane do publicznego odczytu."
        ),
        "cache_ttl_s": WIKI_CACHE_TTL_S,
        "throttle_kind": "OUR_INTERNAL_LIMIT",
        "priority": 1,
    },
)

CAPABILITY_PROVIDER = {
    "FX_RATES": "frankfurter",
    "MACRO_INDICATORS": "world_bank",
    "COMPANY_FILINGS": "sec_edgar",
    "SEC_FILINGS": "sec_edgar",
    "PUBLIC_COMPANY_FILINGS": "sec_edgar",
    "ENCYCLOPEDIC_KNOWLEDGE": "wikimedia",
}

CAPABILITY_ALIASES = {
    "SEC_FILINGS": "COMPANY_FILINGS",
    "PUBLIC_COMPANY_FILINGS": "COMPANY_FILINGS",
}

CACHE_TTL_S = {
    "FX_RATES": FX_CACHE_TTL_S,
    "MACRO_INDICATORS": MACRO_CACHE_TTL_S,
    "COMPANY_FILINGS": SEC_CACHE_TTL_S,
    "SEC_FILINGS": SEC_CACHE_TTL_S,
    "PUBLIC_COMPANY_FILINGS": SEC_CACHE_TTL_S,
    "ENCYCLOPEDIC_KNOWLEDGE": WIKI_CACHE_TTL_S,
}


def data_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    base = Path(os.environ.get("DATA_DIR") or r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data")
    return base / "integrations"


def _state_path(root: Path | None) -> Path:
    return data_root(root) / "public_data_state.json"


def load_public_state(root: Path | None = None) -> dict[str, Any]:
    return load_state(_state_path(root))


def last_health(integration_id: str) -> str | None:
    return _LAST_HEALTH.get(integration_id)


def note_health(integration_id: str, status_name: str) -> None:
    _LAST_HEALTH[integration_id] = status_name


def record_event(root: Path | None, row: dict[str, Any]) -> None:
    _record(root, row)


def _log_path(root: Path | None) -> Path:
    return data_root(root) / "public_data_calls.jsonl"


def _read_enabled(root: Path | None) -> dict[str, bool]:
    path = _state_path(root)
    if not path.exists():
        return {item["id"]: True for item in CATALOG}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {item["id"]: True for item in CATALOG}
    flags = payload.get("enabled") if isinstance(payload, dict) else None
    if not isinstance(flags, dict):
        return {item["id"]: True for item in CATALOG}
    return {item["id"]: bool(flags.get(item["id"], True)) for item in CATALOG}


def known_ids() -> set[str]:
    return {item["id"] for item in CATALOG}


def _touch_usage(root: Path | None, provider_id: str | None, **fields: str | None) -> None:
    if not provider_id:
        return
    path = _state_path(root)
    state = load_state(path)
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    row = usage.get(provider_id) if isinstance(usage.get(provider_id), dict) else {}
    for key, value in fields.items():
        if value is not None:
            row[key] = value
    usage[provider_id] = row
    state["usage"] = usage
    dump_state(path, state)


def remember_health(root: Path | None, provider_id: str | None, status_name: str | None) -> None:
    """A connection test validates the provider. It is not production use."""
    _touch_usage(root, provider_id, last_health=status_name, last_health_at=_now())


def remember_use(root: Path | None, provider_id: str | None, status_name: str | None) -> None:
    """A capability call is real use. It does not replace the last health check."""
    _touch_usage(root, provider_id, last_used=_now(), last_use_status=status_name)


def list_public_data(root: Path | None = None) -> list[dict[str, Any]]:
    enabled = _read_enabled(root)
    usage = load_public_state(root).get("usage") if isinstance(load_public_state(root).get("usage"), dict) else {}
    rows = []
    for item in CATALOG:
        row = dict(item)
        row["enabled"] = enabled[item["id"]]
        row["provider_rate_limit"] = item.get("provider_rate_limit", "UNVERIFIED")
        row["priority"] = item.get("priority", 1)
        if item["id"] == "wikimedia":
            identity = read_identity(load_public_state(root))
            row["app_name"] = identity["app_name"]
            row["contact"] = identity["contact"]
            row["setup_status"] = "CONFIGURED" if identity["contact"] else "REQUIRES_SETUP"
            row["app_version"] = __import__("atnos").__version__
        used = usage.get(item["id"]) if isinstance(usage, dict) else None
        if isinstance(used, dict):
            row["last_health"] = used.get("last_health")
            row["last_used"] = used.get("last_used")
        rows.append(row)
    return rows


def set_enabled(integration_id: str, enabled: bool, root: Path | None = None) -> dict[str, Any]:
    if integration_id not in {item["id"] for item in CATALOG}:
        return {"id": integration_id, "status": "ERROR", "detail": "Nieznana integracja."}
    path = _state_path(root)
    state = load_state(path)
    flags = state.get("enabled") if isinstance(state.get("enabled"), dict) else {}
    flags = {item["id"]: bool(flags.get(item["id"], True)) for item in CATALOG}
    flags[integration_id] = bool(enabled)
    state["enabled"] = flags
    dump_state(path, state)
    return {"id": integration_id, "enabled": flags[integration_id]}


def _throttle(key: str) -> None:
    now = time.monotonic()
    previous = _LAST_CALL.get(key, 0.0)
    wait = INTERNAL_THROTTLE_S - (now - previous)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[key] = time.monotonic()


def _record(root: Path | None, row: dict[str, Any]) -> None:
    try:
        path = _log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return


def _result(integration_id: str, status: str, detail: str, http_status: int | None, **extra: Any) -> dict[str, Any]:
    payload = {
        "id": integration_id,
        "status": status,
        "detail": detail,
        "diagnostics": {"http_status": http_status},
        "source": "live_check",
    }
    payload.update(extra)
    return payload


def probe_public_data(
    integration_id: str,
    *,
    client: httpx.Client | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if integration_id not in {item["id"] for item in CATALOG}:
        return _result(integration_id, "ERROR", "Nieznana integracja danych publicznych.", None)
    if not _read_enabled(root).get(integration_id, True):
        result = _result(integration_id, "DISABLED", "Integracja jest wyłączona. Atnos nie wysyła do niej zapytań.", None)
        _record(root, _obs(integration_id, None, started, False, "DISABLED"))
        return result
    owns_client = client is None
    http = client or httpx.Client(timeout=8.0)
    try:
        if integration_id == "frankfurter":
            _throttle(integration_id)
            response = http.get("https://api.frankfurter.dev/v2/rates?base=USD&quotes=EUR")
            result = _frankfurter_probe(response)
        elif integration_id == "world_bank":
            _throttle(integration_id)
            response = http.get(
                "https://api.worldbank.org/v2/country/US/indicator/NY.GDP.MKTP.CD",
                params={"format": "json", "mrnev": 1, "per_page": 1},
            )
            result = _world_bank_probe(response)
        elif integration_id == "sec_edgar":
            _throttle(integration_id)
            response = http.get("https://data.sec.gov/submissions/CIK0000320193.json", headers=READ_HEADERS)
            result = _sec_probe(response)
        elif integration_id == "wikimedia":
            result = _wikimedia_probe(http, root)
        else:
            result = _result(integration_id, "ERROR", "Nieznana integracja danych publicznych.", None)
        note_health(integration_id, result["status"])
        remember_health(root, integration_id, result["status"])
        _record(root, _obs(integration_id, result.get("capability"), started, result["status"] == "HEALTHY", result["status"]))
        return result
    except httpx.TimeoutException:
        result = _result(integration_id, "OFFLINE", "Przekroczono czas połączenia.", None)
        _record(root, _obs(integration_id, None, started, False, "OFFLINE"))
        return result
    except httpx.HTTPError as exc:
        result = _result(integration_id, "OFFLINE", f"Sieć niedostępna: {type(exc).__name__}.", None)
        _record(root, _obs(integration_id, None, started, False, type(exc).__name__))
        return result
    finally:
        if owns_client:
            http.close()


def query_capability(
    capability: str,
    params: dict[str, Any],
    *,
    client: httpx.Client | None = None,
    root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    requested_capability = capability
    capability = CAPABILITY_ALIASES.get(capability, capability)
    provider = CAPABILITY_PROVIDER.get(requested_capability) or CAPABILITY_PROVIDER.get(capability)
    if provider is None:
        return {"status": "ERROR", "capability": capability, "detail": "Nieznana capability."}
    if not _read_enabled(root).get(provider, True):
        result = {
            "status": "DISABLED",
            "capability": capability,
            "provider": provider,
            "detail": "Integracja jest wyłączona. Atnos nie wysyła do niej zapytań.",
        }
        _record(root, _obs(provider, capability, started, False, "DISABLED"))
        return result
    cache_key = _cache_key(capability, params)
    cached = _cache_get(root, cache_key, now if now is not None else time.time())
    if cached is not None:
        hit = dict(cached)
        hit["cache"] = "hit"
        fetched_at = (hit.get("provenance") or {}).get("fetched_at")
        if isinstance(fetched_at, str):
            try:
                fetched = datetime.fromisoformat(fetched_at)
                hit["cache_age_s"] = round((datetime.now(timezone.utc) - fetched).total_seconds(), 1)
            except ValueError:
                pass
        _record(root, _obs(provider, capability, started, True, "CACHE_HIT"))
        return hit
    owns_client = client is None
    http = client or httpx.Client(timeout=8.0)
    try:
        _throttle(provider)
        if capability == "FX_RATES":
            result = _fetch_fx(http, params)
        elif capability == "MACRO_INDICATORS":
            result = _fetch_macro(http, params)
        elif capability == "COMPANY_FILINGS":
            result = _fetch_filing(http, params)
        elif capability == "ENCYCLOPEDIC_KNOWLEDGE":
            result = _fetch_wiki(http, params, root)
        else:
            result = {"status": "ERROR", "capability": capability, "provider": provider, "detail": "Nieobsługiwana capability."}
        result["capability"] = requested_capability
        note_health(provider, result["status"])
        if result["status"] == "HEALTHY":
            _cache_put(root, cache_key, result, now if now is not None else time.time(), _ttl(capability))
        _record(root, _obs(provider, capability, started, result["status"] == "HEALTHY", result["status"]))
        return result
    except httpx.TimeoutException:
        result = {"status": "OFFLINE", "capability": capability, "provider": provider, "detail": "Przekroczono czas połączenia."}
        _record(root, _obs(provider, capability, started, False, "OFFLINE"))
        return result
    except httpx.HTTPError as exc:
        result = {
            "status": "OFFLINE",
            "capability": capability,
            "provider": provider,
            "detail": f"Sieć niedostępna: {type(exc).__name__}.",
        }
        _record(root, _obs(provider, capability, started, False, type(exc).__name__))
        return result
    finally:
        if owns_client:
            http.close()


def _ttl(capability: str) -> float:
    return CACHE_TTL_S[capability]


def _cache_key(capability: str, params: dict[str, Any]) -> str:
    ordered = {key: params.get(key) for key in sorted(params)}
    return capability + ":" + json.dumps(ordered, sort_keys=True, default=str)


def _bucket(root: Path | None) -> dict[str, tuple[float, dict[str, Any]]]:
    return _CACHE.setdefault(str(data_root(root)), {})


def _cache_get(root: Path | None, key: str, now: float) -> dict[str, Any] | None:
    row = _bucket(root).get(key)
    if row is None or row[0] <= now:
        return None
    return row[1]


def _cache_put(root: Path | None, key: str, value: dict[str, Any], now: float, ttl: float) -> None:
    _bucket(root)[key] = (now + ttl, dict(value))


def _fetch_fx(http: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    base = str(params.get("base") or "").upper()
    quote = str(params.get("quote") or "").upper()
    if len(base) != 3 or len(quote) != 3 or not base.isalpha() or not quote.isalpha():
        return {"status": "ERROR", "capability": "FX_RATES", "provider": "frankfurter", "detail": "Podaj trzyliterowe kody walut."}
    source = f"https://api.frankfurter.dev/v2/rate/{base}/{quote}"
    response = http.get(source)
    if response.is_error:
        return _provider_http_error("FX_RATES", "frankfurter", response.status_code)
    payload = response.json()
    row = _first_rate(payload)
    if row is None:
        return {"status": "DEGRADED", "capability": "FX_RATES", "provider": "frankfurter", "detail": "Odpowiedź nie zawiera kursu."}
    fetched_at = _now()
    return {
        "status": "HEALTHY",
        "capability": "FX_RATES",
        "provider": "frankfurter",
        "freshness": "delayed",
        "cache": "miss",
        "detail": f"Kurs {base}/{quote} odczytany.",
        "value": {
            "base": base,
            "quote": quote,
            "rate": row.get("rate"),
            "observation_date": row.get("date"),
        },
        "provenance": {
            "provider": "frankfurter",
            "source": source,
            "fetched_at": fetched_at,
            "requested": {"base": base, "quote": quote},
            "observation_date": row.get("date"),
        },
        "diagnostics": {"http_status": response.status_code},
    }


def _fetch_macro(http: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    country = str(params.get("country") or "").upper()
    indicator = str(params.get("indicator") or "")
    if len(country) != 2 or not country.isalpha() or not indicator or any(ch.isspace() for ch in indicator):
        return {
            "status": "ERROR",
            "capability": "MACRO_INDICATORS",
            "provider": "world_bank",
            "detail": "Podaj kod kraju i identyfikator wskaźnika.",
        }
    source = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
    query = {"format": "json", "per_page": 1}
    year = str(params.get("year") or "")
    if len(year) == 4 and year.isdigit():
        query["date"] = year
    else:
        query["mrnev"] = 1
    response = http.get(source, params=query)
    if response.is_error:
        return _provider_http_error("MACRO_INDICATORS", "world_bank", response.status_code)
    payload = response.json()
    row = _latest_observation(payload)
    if row is None or row.get("value") is None:
        return {
            "status": "DEGRADED",
            "capability": "MACRO_INDICATORS",
            "provider": "world_bank",
            "detail": "World Bank nie zwrócił wartości wskaźnika.",
        }
    return {
        "status": "HEALTHY",
        "capability": "MACRO_INDICATORS",
        "provider": "world_bank",
        "freshness": "historical",
        "cache": "miss",
        "detail": f"Wskaźnik {indicator} dla {country} odczytany.",
        "value": {
            "country": country,
            "indicator": indicator,
            "amount": row.get("value"),
            "observation_year": row.get("date"),
        },
        "provenance": {
            "provider": "world_bank",
            "source": source,
            "fetched_at": _now(),
            "requested": {"country": country, "indicator": indicator},
            "observation_date": row.get("date"),
        },
        "diagnostics": {"http_status": response.status_code},
    }


def save_wikimedia_identity(app_name: str, contact: str, root: Path | None = None) -> dict[str, Any]:
    from atnos.integrations.wikimedia_identity import identity_payload

    checked = identity_payload(app_name, contact)
    if checked["status"] != "CONFIGURED":
        return {
            "status": "REQUIRES_SETUP",
            "detail": "Add a contact email, public URL, or Wikimedia user page before testing the connection.",
        }
    path = _state_path(root)
    state = load_state(path)
    state["wikimedia"] = {"app_name": checked["app_name"], "contact": checked["contact"]}
    dump_state(path, state)
    return {"status": "CONFIGURED", "app_name": checked["app_name"], "contact_kind": checked["contact_kind"]}


def _wikimedia_identity(root: Path | None) -> dict[str, str]:
    return read_identity(load_state(_state_path(root)))


def _wikimedia_probe(http: httpx.Client, root: Path | None) -> dict[str, Any]:
    identity = _wikimedia_identity(root)
    if not identity["contact"]:
        return _result(
            "wikimedia",
            "REQUIRES_SETUP",
            "Add a contact email, public URL, or Wikimedia user page before testing the connection.",
            None,
            capability="ENCYCLOPEDIC_KNOWLEDGE",
        )
    _throttle("wikimedia")
    with _WIKI_LOCK:
        response = _wiki_get(http, "https://en.wikipedia.org/api/rest_v1/page/summary/Compound_interest", identity)
    return _wiki_status(response)


def _fetch_wiki(http: httpx.Client, params: dict[str, Any], root: Path | None) -> dict[str, Any]:
    from urllib.parse import quote

    identity = _wikimedia_identity(root)
    if not identity["contact"]:
        return {
            "status": "REQUIRES_SETUP",
            "capability": "ENCYCLOPEDIC_KNOWLEDGE",
            "provider": "wikimedia",
            "detail": "Add a contact email, public URL, or Wikimedia user page before testing the connection.",
        }
    title = " ".join(str(params.get("title") or "").split())
    if not title or len(title) > 200 or any(ch in title for ch in "/?#"):
        return {"status": "ERROR", "capability": "ENCYCLOPEDIC_KNOWLEDGE", "provider": "wikimedia", "detail": "Podaj tytuł hasła."}
    source = "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_"), safe="()_-")
    with _WIKI_LOCK:
        response = _wiki_get(http, source, identity)
    if response.status_code == 403:
        return {
            "status": "ERROR",
            "capability": "ENCYCLOPEDIC_KNOWLEDGE",
            "provider": "wikimedia",
            "detail": "Wikimedia odrzuciła identyfikację klienta.",
            "diagnostics": {"http_status": 403},
        }
    if response.status_code == 404:
        return {
            "status": "DEGRADED",
            "capability": "ENCYCLOPEDIC_KNOWLEDGE",
            "provider": "wikimedia",
            "detail": "Wikipedia nie ma takiego hasła.",
            "diagnostics": {"http_status": 404},
        }
    if response.is_error:
        return _provider_http_error("ENCYCLOPEDIC_KNOWLEDGE", "wikimedia", response.status_code)
    payload = response.json()
    extract = payload.get("extract") if isinstance(payload, dict) else None
    if not extract:
        return {"status": "DEGRADED", "capability": "ENCYCLOPEDIC_KNOWLEDGE", "provider": "wikimedia", "detail": "Skrót hasła jest pusty."}
    page = ((payload.get("content_urls") or {}).get("desktop") or {}).get("page")
    return {
        "status": "HEALTHY",
        "capability": "ENCYCLOPEDIC_KNOWLEDGE",
        "provider": "wikimedia",
        "freshness": "reference",
        "cache": "miss",
        "detail": "Wikimedia API responded successfully.",
        "value": {"title": payload.get("title") or title, "extract": extract, "page": page, "language": "en", "project": "wikipedia"},
        "provenance": {
            "provider": "Wikimedia",
            "source": page or source,
            "fetched_at": _now(),
            "requested": {"title": title},
            "observation_date": payload.get("timestamp"),
            "language": "en",
            "project": "wikipedia",
            "page_id": payload.get("pageid"),
            "revision": payload.get("revision"),
        },
        "diagnostics": {"http_status": response.status_code},
    }


def _wiki_get(http: httpx.Client, url: str, identity: dict[str, str]) -> httpx.Response:
    headers = request_headers(identity["contact"], identity["app_name"])
    response = http.get(url, headers=headers)
    if response.status_code != 429:
        return response
    retry_after = _retry_after_seconds(response)
    if retry_after is None or retry_after > 2:
        return response
    time.sleep(retry_after)
    return http.get(url, headers=headers)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After") if response.headers is not None else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _wiki_status(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 403:
        return _result("wikimedia", "ERROR", "Wikimedia odrzuciła identyfikację klienta.", 403, capability="ENCYCLOPEDIC_KNOWLEDGE")
    if response.status_code == 429:
        return _result("wikimedia", "DEGRADED", "Wikimedia poprosiła o odczekanie przed kolejnym zapytaniem.", 429, capability="ENCYCLOPEDIC_KNOWLEDGE")
    if response.is_error:
        return _result("wikimedia", "DEGRADED", f"Wikimedia zwróciła HTTP {response.status_code}.", response.status_code, capability="ENCYCLOPEDIC_KNOWLEDGE")
    payload = response.json()
    ok = isinstance(payload, dict) and bool(payload.get("extract"))
    return _result(
        "wikimedia",
        "HEALTHY" if ok else "DEGRADED",
        "Wikimedia API responded successfully." if ok else "Odpowiedź Wikipedii nie zawiera skrótu.",
        response.status_code,
        capability="ENCYCLOPEDIC_KNOWLEDGE",
    )


def _fetch_filing(http: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
    raw = str(params.get("cik") or "").strip()
    if not raw.isdigit() or not 1 <= len(raw) <= 10:
        return {"status": "ERROR", "capability": "COMPANY_FILINGS", "provider": "sec_edgar", "detail": "Podaj numer CIK."}
    cik = raw.zfill(10)
    source = f"https://data.sec.gov/submissions/CIK{cik}.json"
    response = http.get(source, headers=READ_HEADERS)
    if response.status_code == 403:
        return {
            "status": "DEGRADED",
            "capability": "COMPANY_FILINGS",
            "provider": "sec_edgar",
            "detail": "SEC odrzucił zapytanie. Oficjalna polityka wymaga opisowego User-Agent.",
            "diagnostics": {"http_status": 403},
        }
    if response.is_error:
        return _provider_http_error("COMPANY_FILINGS", "sec_edgar", response.status_code)
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("name"):
        return {"status": "DEGRADED", "capability": "COMPANY_FILINGS", "provider": "sec_edgar", "detail": "SEC nie zwrócił nazwy podmiotu."}
    recent = (payload.get("filings") or {}).get("recent") if isinstance(payload.get("filings"), dict) else None
    form = _column(recent, "form")
    filed = _column(recent, "filingDate")
    return {
        "status": "HEALTHY",
        "capability": "COMPANY_FILINGS",
        "provider": "sec_edgar",
        "freshness": "delayed",
        "cache": "miss",
        "detail": f"Znaleziono {payload.get('name')}.",
        "value": {
            "cik": cik,
            "name": payload.get("name"),
            "latest_form": form,
            "latest_filing_date": filed,
        },
        "provenance": {
            "provider": "sec_edgar",
            "source": source,
            "fetched_at": _now(),
            "requested": {"cik": cik},
            "observation_date": filed,
        },
        "diagnostics": {"http_status": response.status_code},
    }


def _column(recent: Any, key: str) -> str | None:
    if not isinstance(recent, dict):
        return None
    values = recent.get(key)
    if isinstance(values, list) and values:
        return str(values[0])
    return None


def _sec_probe(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 403:
        return _result("sec_edgar", "DEGRADED", "SEC odrzucił zapytanie. Oficjalna polityka wymaga opisowego User-Agent.", 403, capability="COMPANY_FILINGS")
    if response.is_error:
        return _result("sec_edgar", "DEGRADED", f"SEC zwrócił HTTP {response.status_code}.", response.status_code, capability="COMPANY_FILINGS")
    payload = response.json()
    ok = isinstance(payload, dict) and bool(payload.get("name"))
    return _result(
        "sec_edgar",
        "HEALTHY" if ok else "DEGRADED",
        "Odczyt zgłoszeń spółki działa." if ok else "Odpowiedź SEC nie zawiera nazwy podmiotu.",
        response.status_code,
        capability="COMPANY_FILINGS",
    )


def _provider_http_error(capability: str, provider: str, http_status: int) -> dict[str, Any]:
    if http_status == 401:
        detail = "API key is invalid or expired."
        status_name = "ERROR"
    else:
        detail = f"{provider} zwrócił błąd HTTP."
        status_name = "DEGRADED"
    return {
        "status": status_name,
        "capability": capability,
        "provider": provider,
        "detail": detail,
        "diagnostics": {"http_status": http_status},
    }


def _first_rate(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict) and "rate" in payload:
        return payload
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and "rate" in row:
                return row
    if isinstance(payload, dict):
        rates = payload.get("rates")
        if isinstance(rates, list):
            for row in rates:
                if isinstance(row, dict) and "rate" in row:
                    return row
    return None


def _latest_observation(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], list) and payload[1]:
        row = payload[1][0]
        return row if isinstance(row, dict) else None
    return None


def _frankfurter_probe(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 401:
        return _result("frankfurter", "ERROR", "API key is invalid or expired.", response.status_code, capability="FX_RATES")
    if response.is_error:
        return _result("frankfurter", "DEGRADED", f"Frankfurter zwrócił HTTP {response.status_code}.", response.status_code, capability="FX_RATES")
    has_rate = _first_rate(response.json()) is not None
    return _result(
        "frankfurter",
        "HEALTHY" if has_rate else "DEGRADED",
        "Odczyt kursu USD/EUR działa." if has_rate else "Odpowiedź nie zawiera kursu.",
        response.status_code,
        capability="FX_RATES",
    )


def _world_bank_probe(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        return _result("world_bank", "DEGRADED", f"World Bank zwrócił HTTP {response.status_code}.", response.status_code, capability="MACRO_INDICATORS")
    payload = response.json()
    ok = isinstance(payload, list) and len(payload) >= 1
    return _result(
        "world_bank",
        "HEALTHY" if ok else "DEGRADED",
        "Odczyt wskaźnika PKB działa." if ok else "Odpowiedź World Bank nie ma oczekiwanego kształtu.",
        response.status_code,
        capability="MACRO_INDICATORS",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _obs(integration_id: str, capability: str | None, started: float, success: bool, code: str) -> dict[str, Any]:
    return {
        "integration_id": integration_id,
        "capability": capability,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "success": success,
        "error_code": None if success else code,
        "at": _now(),
    }
