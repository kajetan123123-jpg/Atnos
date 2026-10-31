from unittest.mock import MagicMock

import httpx

from atnos.integrations.capability_router import classify_intent, route_data_need
from atnos.integrations.public_data import list_public_data, probe_public_data, query_capability, set_enabled
from atnos.integrations.wikimedia_identity import classify_contact, user_agent
from atnos.integrations.public_data import save_wikimedia_identity


def test_placeholders_are_rejected_and_one_real_contact_is_enough():
    assert classify_contact("example@example.com") is None
    assert classify_contact("test@test.com") is None
    assert classify_contact("http://localhost/me") is None
    assert classify_contact("User:AtnosDesk") == "wiki_user"
    assert classify_contact("account-2cc60cd7@example.invalid") == "email"
    agent = user_agent("account-2cc60cd7@example.invalid", "Atnos")
    assert agent.startswith("Atnos/0.1.0 (account-2cc60cd7@example.invalid) httpx/")
    assert agent.endswith(" bot")
    assert "python-requests" not in agent
    assert "Mozilla" not in agent


def test_missing_wikimedia_contact_does_not_call_network(tmp_path):
    client = MagicMock()
    result = probe_public_data("wikimedia", client=client, root=tmp_path)
    assert result["status"] == "REQUIRES_SETUP"
    assert "before testing the connection" in result["detail"]
    client.get.assert_not_called()


def test_saved_contact_survives_disable_and_is_not_logged(tmp_path):
    saved = save_wikimedia_identity("Atnos", "account-2cc60cd7@example.invalid", root=tmp_path)
    assert saved["status"] == "CONFIGURED"
    assert "account-2cc60cd7@example.invalid" not in str(saved)
    set_enabled("frankfurter", False, root=tmp_path)
    state = (tmp_path / "public_data_state.json").read_text(encoding="utf-8")
    assert "account-2cc60cd7@example.invalid" in state
    response = MagicMock(status_code=200, is_error=False)
    response.json.return_value = {
        "title": "Compound interest",
        "extract": "Interest on interest.",
        "pageid": 1,
        "timestamp": "2026-01-01T00:00:00Z",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Compound_interest"}},
    }
    response.headers = {}
    client = MagicMock()
    client.get.return_value = response
    result = query_capability("ENCYCLOPEDIC_KNOWLEDGE", {"title": "Compound interest"}, client=client, root=tmp_path)
    assert result["status"] == "HEALTHY"
    assert result["provenance"]["provider"] == "Wikimedia"
    assert result["provenance"]["page_id"] == 1
    assert "account-2cc60cd7@example.invalid" not in (tmp_path / "public_data_calls.jsonl").read_text(encoding="utf-8")
    sent = client.get.call_args.kwargs["headers"]["User-Agent"]
    assert sent.startswith("Atnos/0.1.0 (account-2cc60cd7@example.invalid)")


def test_router_picks_providers_by_capability(tmp_path):
    assert classify_intent("Sprawdź aktualny kurs USD/EUR")[0] == "FX_RATES"
    fx = route_data_need(intent="Sprawdź aktualny kurs USD/EUR", root=tmp_path, execute=False)
    assert fx["routing"]["selected_provider"] == "frankfurter"
    macro = route_data_need(intent="Znajdź PKB Polski w 2024", root=tmp_path, execute=False)
    assert macro["routing"]["selected_provider"] == "world_bank"
    filing = route_data_need(intent="Sprawdź Apple 10-K filing", root=tmp_path, execute=False)
    assert filing["routing"]["selected_provider"] == "sec_edgar"
    wiki = route_data_need(intent="What is compound interest?", root=tmp_path, execute=False)
    assert wiki["status"] == "NO_AVAILABLE_PROVIDER"
    assert wiki["detail"] == "Wikimedia requires setup."
    assert "frankfurter" not in (wiki["routing"]["candidates"])


def test_disabled_fx_provider_is_not_called(tmp_path):
    set_enabled("frankfurter", False, root=tmp_path)
    calls = []

    def invoke(provider_id, capability, params):
        calls.append(provider_id)
        return {"status": "HEALTHY"}

    result = route_data_need(capability="FX_RATES", parameters={"base": "USD", "quote": "EUR"}, root=tmp_path, invoke=invoke)
    assert result["status"] == "NO_AVAILABLE_PROVIDER"
    assert calls == []


def test_offline_does_not_crash_and_fallback_is_explicit(tmp_path):
    calls = []

    def invoke(provider_id, capability, params):
        calls.append(provider_id)
        if provider_id == "frankfurter":
            return {"status": "OFFLINE", "provider": provider_id, "capability": capability, "detail": "Przekroczono czas połączenia."}
        return {
            "status": "HEALTHY",
            "provider": provider_id,
            "capability": capability,
            "value": {"rate": 1},
            "provenance": {"provider": provider_id, "source": "backup", "fetched_at": "2026-09-26T00:00:00Z"},
        }

    result = route_data_need(
        capability="FX_RATES",
        parameters={"base": "USD", "quote": "EUR"},
        root=tmp_path,
        invoke=invoke,
        extra_providers=[{"id": "fx_backup", "capabilities": ["FX_RATES"], "enabled": True, "cost": "FREE", "freshness": "delayed", "priority": 2}],
    )
    assert calls == ["frankfurter", "fx_backup"]
    assert "PRIMARY_PROVIDER_FAILED" in result["routing"]["events"]
    assert "FALLBACK_PROVIDER_USED" in result["routing"]["events"]
    assert result["brain_provenance"]["provider"] == "fx_backup"


def test_invalid_request_does_not_fallback(tmp_path):
    calls = []

    def invoke(provider_id, capability, params):
        calls.append(provider_id)
        return {"status": "ERROR", "provider": provider_id, "detail": "Podaj trzyliterowe kody walut."}

    result = route_data_need(
        capability="FX_RATES",
        parameters={},
        root=tmp_path,
        invoke=invoke,
        extra_providers=[{"id": "fx_backup", "capabilities": ["FX_RATES"], "enabled": True, "cost": "FREE", "freshness": "delayed", "priority": 2}],
    )
    assert calls == ["frankfurter"]
    assert result["routing"]["fallback"] is None


def test_wikimedia_403_is_a_client_identification_error(tmp_path):
    save_wikimedia_identity("Atnos", "account-2cc60cd7@example.invalid", root=tmp_path)
    response = MagicMock(status_code=403, is_error=True)
    response.headers = {}
    client = MagicMock()
    client.get.return_value = response
    result = probe_public_data("wikimedia", client=client, root=tmp_path)
    assert result["status"] == "ERROR"
    assert "identyfikację klienta" in result["detail"]


def test_source_quality_does_not_use_wikipedia_for_official_data(tmp_path):
    filing = route_data_need(intent="Apple 10-K filing", root=tmp_path, execute=False)
    fx = route_data_need(intent="USD/EUR exchange rate", root=tmp_path, execute=False)
    gdp = route_data_need(intent="Poland GDP 2015", root=tmp_path, execute=False)
    inflation = route_data_need(intent="US inflation", root=tmp_path, execute=False)
    assert "wikimedia" not in filing["routing"]["candidates"]
    assert "wikimedia" not in fx["routing"]["candidates"]
    assert gdp["routing"]["selected_provider"] == "world_bank"
    assert gdp["routing"]["freshness_requirement"] == "HISTORICAL"
    assert classify_intent("Poland GDP 2015")[1]["year"] == "2015"
    assert inflation["routing"]["requested_capability"] == "MACRO_INDICATORS"
    assert inflation["routing"]["selected_provider"] == "world_bank"


def test_fact_check_and_brain_do_not_invent_or_store_transient_rates(tmp_path):
    from atnos.integrations.capability_workflows import check_claim, evidence_record, lookup_class, wealth_blueprint_inflation_evidence

    unsourced = check_claim("a nice story about a dragon", root=tmp_path)
    assert unsourced["invented_value"] is False
    assert unsourced["value"] is None
    fx = {
        "status": "HEALTHY",
        "capability": "FX_RATES",
        "provider": "frankfurter",
        "value": {"rate": 1.1},
        "provenance": {"provider": "frankfurter", "source": "https://example.invalid/fx", "fetched_at": "2026-09-26T00:00:00+00:00"},
    }
    assert lookup_class("FX_RATES") == "transient_lookup"
    assert evidence_record(fx, project="wealth_blueprint", use_class="transient_lookup") is None
    macro = {
        "status": "HEALTHY",
        "capability": "MACRO_INDICATORS",
        "provider": "world_bank",
        "freshness": "historical",
        "value": {"amount": 1, "observation_year": "2015"},
        "provenance": {"provider": "world_bank", "source": "https://api.worldbank.org/example", "fetched_at": "2026-09-26T00:00:00+00:00", "observation_date": "2015"},
        "routing": {"fallback_used": False},
    }
    stored = evidence_record(macro, project="wealth_blueprint", use_class="reusable_project_knowledge")
    assert stored is not None
    assert stored["invented_value"] is False
    assert stored["source_url"].startswith("https://api.worldbank.org")
    client = MagicMock()
    client.get.side_effect = httpx.TimeoutException("down")
    research = wealth_blueprint_inflation_evidence(root=tmp_path, client=client)
    assert research["invented_value"] is False
    assert research["numbers"] == []
    assert "US inflation rate" in research["missing"]


def test_invalid_contact_stays_setup_and_save_is_not_healthy(tmp_path):
    rejected = save_wikimedia_identity("Atnos", "example@example.com", root=tmp_path)
    assert rejected["status"] == "REQUIRES_SETUP"
    assert not (tmp_path / "public_data_state.json").exists()
    client = MagicMock()
    probe_public_data("wikimedia", client=client, root=tmp_path)
    client.get.assert_not_called()
    saved = save_wikimedia_identity("Atnos", "account-2cc60cd7@example.invalid", root=tmp_path)
    assert saved["status"] == "CONFIGURED"
    assert saved["status"] != "HEALTHY"
    listed = list_public_data(tmp_path)
    wiki = next(item for item in listed if item["id"] == "wikimedia")
    assert wiki.get("last_health") == "REQUIRES_SETUP"
    assert not wiki.get("last_used")


def test_router_does_not_call_unconfigured_wikimedia(tmp_path):
    calls = []

    def invoke(provider_id, capability, params):
        calls.append(provider_id)
        return {"status": "HEALTHY"}

    result = route_data_need(intent="What is compound interest?", root=tmp_path, invoke=invoke)
    assert result["status"] == "NO_AVAILABLE_PROVIDER"
    assert result["detail"] == "Wikimedia requires setup."
    assert calls == []


def test_wikimedia_timeout_is_offline(tmp_path):
    save_wikimedia_identity("Atnos", "account-2cc60cd7@example.invalid", root=tmp_path)
    client = MagicMock()
    client.get.side_effect = httpx.TimeoutException("slow")
    result = query_capability("ENCYCLOPEDIC_KNOWLEDGE", {"title": "Inflation"}, client=client, root=tmp_path)
    assert result["status"] == "OFFLINE"
