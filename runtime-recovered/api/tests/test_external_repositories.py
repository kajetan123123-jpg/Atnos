from atnos.integrations.capability_router import classify_intent, route_data_need
from atnos.integrations.external_repositories import (
    connect_social_account,
    credential_refs_for_platform,
    disconnect_social_account,
    discover_resources,
    inspect_skill,
    list_project_accounts,
    list_repositories,
    list_skills,
    list_social_accounts,
    opentoonz_status,
    parse_awesome_readme,
    request_publish,
    retention_from_rows,
    search_awesome,
    set_opentoonz_path,
    set_project_account,
    set_skill_enabled,
    sync_awesome,
    verify_social_connection,
)


def test_awesome_links_are_discovered_not_trusted():
    items = parse_awesome_readme("[Python](https://github.com/vinta/awesome-python)\n[Home](https://example.com)")
    assert items == [{"name": "Python", "url": "https://github.com/vinta/awesome-python", "trust": "DISCOVERED"}]


def test_sync_uses_supplied_markdown_without_network(tmp_path):
    result = sync_awesome(tmp_path, markdown="[Python](https://github.com/vinta/awesome-python)")
    assert result["status"] == "HEALTHY"
    assert search_awesome("python", tmp_path)[0]["trust"] == "DISCOVERED"
    discovered = discover_resources("python", tmp_path)
    assert discovered["capability"] == "RESOURCE_DISCOVERY"
    assert discovered["trust"] == "DISCOVERED"
    repos = {item["id"]: item for item in list_repositories(tmp_path)}
    assert repos["awesome"]["status"] == "HEALTHY"
    assert repos["awesome"]["items_discovered"] == 1


def test_social_rejects_raw_secrets_and_does_not_publish(tmp_path):
    blocked = connect_social_account("youtube", display_name="WB", credential_ref="ya29.secret", root=tmp_path)
    assert blocked["status"] == "ERROR"
    password_blocked = connect_social_account(
        "youtube",
        display_name="WB",
        credential_ref="vault://password-store",
        root=tmp_path,
    )
    assert password_blocked["status"] == "ERROR"
    saved = connect_social_account(
        "youtube",
        display_name="Wealth Blueprint",
        credential_ref="vault://youtube/wb",
        root=tmp_path,
    )
    assert saved["credential_stored"] is True
    second = connect_social_account(
        "youtube",
        display_name="Wonder Crew",
        credential_ref="oauth:youtube:wc",
        account_id="youtube-wonder-crew",
        root=tmp_path,
    )
    assert second["account_id"] == "youtube-wonder-crew"
    accounts = {item["id"]: item for item in list_social_accounts(tmp_path)}
    assert accounts["youtube"]["connected"] is True
    assert accounts["youtube"]["account_count"] == 2
    assert accounts["youtube"]["display_name"] == "Wealth Blueprint"
    assert "vault://" not in str(accounts["youtube"])
    assert "oauth:" not in str(accounts["youtube"].get("accounts"))
    assert accounts["youtube"]["password_accepted"] is False
    tested = verify_social_connection("youtube", account_id="youtube-wonder-crew", root=tmp_path)
    assert tested["status"] == "HEALTHY"
    assert tested["credential_stored"] is True
    assert "oauth:" not in str(tested)
    assert request_publish("youtube")["published"] is False
    assert request_publish("youtube")["status"] == "READY_FOR_APPROVAL"
    refs = credential_refs_for_platform("youtube", root=tmp_path)
    assert refs
    disconnect_social_account("youtube", account_id="youtube-wonder-crew", root=tmp_path)
    accounts = {item["id"]: item for item in list_social_accounts(tmp_path)}
    assert accounts["youtube"]["account_count"] == 1
    disconnect_social_account("youtube", root=tmp_path)
    accounts = {item["id"]: item for item in list_social_accounts(tmp_path)}
    assert accounts["youtube"]["connected"] is False


def test_skills_start_disabled_and_retention_does_not_invent_a_curve(tmp_path):
    assert all(item["enabled"] is False for item in list_skills(tmp_path))
    assert all(item["provider_locked"] is False for item in list_skills(tmp_path))
    enabled = set_skill_enabled("yt-retention", True, root=tmp_path)
    assert enabled["enabled"] is True
    assert set_skill_enabled("missing-skill", True, root=tmp_path)["status"] == "ERROR"
    inspected = inspect_skill("yt-retention", root=tmp_path)
    assert inspected["status"] == "OK"
    assert inspected["skill"]["capabilities"] == ["RETENTION_ANALYSIS"]
    assert retention_from_rows([])["status"] == "REQUIRES_DATA"
    assert retention_from_rows([])["curve"] is None
    assert retention_from_rows([(0.0, 100.0), (10.0, 80.0)])["status"] == "HEALTHY"


def test_projects_do_not_share_an_account_by_default(tmp_path):
    set_project_account("wealth_blueprint", "youtube:wb", root=tmp_path)
    other = set_project_account("wonder_crew", None, root=tmp_path)
    assert other["platform_id"] is None
    mapping = list_project_accounts(tmp_path)
    by_id = {item["project_id"]: item["platform_or_account"] for item in mapping["projects"]}
    assert by_id["wealth_blueprint"] == "youtube:wb"
    assert by_id["wonder_crew"] is None
    assert by_id["dark_chronicles"] is None
    assert set_project_account("unknown_channel", "youtube", root=tmp_path)["status"] == "ERROR"


def test_opentoonz_missing_is_not_installed(tmp_path):
    assert opentoonz_status(tmp_path)["status"] == "NOT_INSTALLED"
    assert set_opentoonz_path("C:/not-an-exe.txt", root=tmp_path)["status"] == "ERROR"
    repos = {item["id"]: item for item in list_repositories(tmp_path)}
    assert repos["opentoonz"]["status"] == "NOT_INSTALLED"
    assert repos["social_sdk"]["status"] == "REQUIRES_SETUP"
    assert repos["youtube_skills_chatgpt"]["commit"].startswith("6314d3c1")


def test_resource_discovery_routes_through_awesome_without_trust_escalation(tmp_path):
    sync_awesome(tmp_path, markdown="[Python](https://github.com/vinta/awesome-python)")
    capability, params, _fresh = classify_intent("find library python")
    assert capability == "RESOURCE_DISCOVERY"
    assert "python" in params["query"].lower()
    result = route_data_need(capability="RESOURCE_DISCOVERY", parameters={"query": "python"}, root=tmp_path)
    assert result["provider"] == "awesome"
    assert result["trust"] == "DISCOVERED"
    assert result["items"][0]["url"].startswith("https://github.com/")
    assert result["brain_provenance"]["trust"] == "DISCOVERED"
