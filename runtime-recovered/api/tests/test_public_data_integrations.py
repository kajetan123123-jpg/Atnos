from unittest.mock import MagicMock

import httpx

from atnos.integrations.public_data import list_public_data, probe_public_data, query_capability, set_enabled


def test_catalog_declares_cost_and_does_not_include_secrets():
    items = list_public_data()
    assert {item["id"] for item in items} == {"frankfurter", "world_bank", "sec_edgar", "wikimedia"}
    assert all(item["cost"] == "FREE" and item["auth_type"] == "none" for item in items)
    assert all("api_key" not in item for item in items)
    by_id = {item["id"]: item for item in items}
    assert by_id["frankfurter"]["capabilities"] == ["FX_RATES"]
    assert by_id["world_bank"]["capabilities"] == ["MACRO_INDICATORS"]
    assert by_id["frankfurter"]["freshness"] == "delayed"
    assert by_id["world_bank"]["freshness"] == "historical"
    assert by_id["frankfurter"]["throttle_kind"] == "INTERNAL_THROTTLE"
    assert by_id["frankfurter"]["provider_rate_limit"] == "UNVERIFIED"
    assert by_id["sec_edgar"]["provider_rate_limit"] == "10/s"
    assert "10 zapytań" in by_id["sec_edgar"]["limits"]


def test_frankfurter_health_reports_detail_not_a_bare_success(tmp_path):
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = {"rates": [{"quote": "EUR", "rate": 0.85}]}
    client = MagicMock()
    client.get.return_value = response
    result = probe_public_data("frankfurter", client=client, root=tmp_path)
    assert result["status"] == "HEALTHY"
    assert "USD/EUR" in result["detail"]
    assert "HTTP 200" not in result["detail"]
    client.close.assert_not_called()


def test_invalid_key_message_is_human(tmp_path):
    response = MagicMock(status_code=401, is_error=True)
    client = MagicMock()
    client.get.return_value = response
    result = probe_public_data("frankfurter", client=client, root=tmp_path)
    assert result["status"] == "ERROR"
    assert "invalid or expired" in result["detail"]


def test_timeout_does_not_raise(tmp_path):
    client = MagicMock()
    client.get.side_effect = httpx.TimeoutException("slow")
    result = probe_public_data("world_bank", client=client, root=tmp_path)
    assert result["status"] == "OFFLINE"


def test_fx_routes_to_frankfurter_with_provenance(tmp_path):
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = {"date": "2026-09-25", "base": "USD", "quote": "EUR", "rate": 0.877}
    client = MagicMock()
    client.get.return_value = response
    result = query_capability("FX_RATES", {"base": "usd", "quote": "eur"}, client=client, root=tmp_path)
    assert result["status"] == "HEALTHY"
    assert result["provider"] == "frankfurter"
    assert result["freshness"] == "delayed"
    assert result["value"]["rate"] == 0.877
    assert result["provenance"]["observation_date"] == "2026-09-25"
    assert result["provenance"]["requested"] == {"base": "USD", "quote": "EUR"}
    client.get.assert_called_once()
    assert "frankfurter.dev" in client.get.call_args.args[0]


def test_macro_routes_to_world_bank(tmp_path):
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = [
        {"page": 1},
        [{"date": "2024", "value": 28000000000000, "indicator": {"id": "NY.GDP.MKTP.CD"}}],
    ]
    client = MagicMock()
    client.get.return_value = response
    result = query_capability(
        "MACRO_INDICATORS",
        {"country": "US", "indicator": "NY.GDP.MKTP.CD"},
        client=client,
        root=tmp_path,
    )
    assert result["provider"] == "world_bank"
    assert result["freshness"] == "historical"
    assert result["value"]["observation_year"] == "2024"
    assert result["value"]["amount"] == 28000000000000


def test_disable_stops_routing_and_persists(tmp_path):
    client = MagicMock()
    set_enabled("frankfurter", False, root=tmp_path)
    result = query_capability("FX_RATES", {"base": "USD", "quote": "EUR"}, client=client, root=tmp_path)
    assert result["status"] == "DISABLED"
    client.get.assert_not_called()
    again = list_public_data(tmp_path)
    assert next(item for item in again if item["id"] == "frankfurter")["enabled"] is False
    set_enabled("frankfurter", True, root=tmp_path)
    assert next(item for item in list_public_data(tmp_path) if item["id"] == "frankfurter")["enabled"] is True


def test_identical_fx_query_uses_cache(tmp_path):
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = {"date": "2026-09-25", "rate": 0.5}
    client = MagicMock()
    client.get.return_value = response
    first = query_capability("FX_RATES", {"base": "USD", "quote": "EUR"}, client=client, root=tmp_path, now=1_000.0)
    second = query_capability("FX_RATES", {"base": "USD", "quote": "EUR"}, client=client, root=tmp_path, now=1_100.0)
    assert first["cache"] == "miss"
    assert second["cache"] == "hit"
    assert client.get.call_count == 1
    log = (tmp_path / "public_data_calls.jsonl").read_text(encoding="utf-8")
    assert "0.5" not in log
    assert "FX_RATES" in log


def test_company_filings_route_to_sec_and_keep_one_filing(tmp_path):
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = {
        "name": "Example Inc.",
        "filings": {"recent": {"form": ["10-K", "10-Q"], "filingDate": ["2026-01-02", "2025-10-01"]}},
    }
    client = MagicMock()
    client.get.return_value = response
    result = query_capability("COMPANY_FILINGS", {"cik": "320193"}, client=client, root=tmp_path)
    assert result["provider"] == "sec_edgar"
    assert result["value"]["name"] == "Example Inc."
    assert result["value"]["latest_form"] == "10-K"
    assert "data.sec.gov/submissions/CIK0000320193.json" in client.get.call_args.args[0]
    assert client.get.call_args.kwargs["headers"]["User-Agent"].startswith("Atnos/")


def test_sec_rejection_is_explained(tmp_path):
    response = MagicMock(status_code=403, is_error=True)
    client = MagicMock()
    client.get.return_value = response
    result = query_capability("COMPANY_FILINGS", {"cik": "320193"}, client=client, root=tmp_path)
    assert result["status"] == "DEGRADED"
    assert "User-Agent" in result["detail"]


