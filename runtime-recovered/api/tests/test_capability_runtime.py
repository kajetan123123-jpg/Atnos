from atnos.integrations.capability_runtime import classify_task, execute_capability, list_executable_capabilities
from atnos.integrations.external_repositories import set_skill_enabled, sync_awesome


def test_capability_map_only_lists_executable_bindings():
    caps = {item["capability"] for item in list_executable_capabilities()}
    assert "RESOURCE_DISCOVERY" in caps
    assert "YOUTUBE_PACKAGING" in caps
    assert "SOCIAL_READ" in caps
    assert "ANIMATION_2D_RENDER" in caps
    assert "WEB_PAGE_EXTRACT" in caps
    assert "SOCIAL_PUBLISH" not in caps  # no ungated publish capability


def test_disabled_skill_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # skills state lives under data_root which uses DATA_DIR/integrations when root is None;
    # pass root explicitly through execute via skill enable under tmp_path.
    from atnos.integrations import capability_runtime as runtime

    set_skill_enabled("yt-package", False, root=tmp_path)
    result = execute_capability(
        "YOUTUBE_PACKAGING",
        parameters={"script": "Set aside money every Friday."},
        root=tmp_path,
    )
    # execute_capability reads skills via repos.list_skills(root) — need to thread root.
    # Current execute_capability calls list_skills() without root when checking enabled —
    # fix by ensuring we pass and use root in execute_capability (already does list_skills(root)).
    assert result["status"] == "BLOCKED"


def test_packaging_skill_executes_when_enabled(tmp_path):
    set_skill_enabled("yt-package", True, root=tmp_path)
    result = execute_capability(
        "YOUTUBE_PACKAGING",
        parameters={"script": "Set aside a specific amount from your paycheck every Friday. Build security."},
        project_id="wealth_blueprint",
        root=tmp_path,
    )
    assert result["status"] == "SUCCESS"
    assert result["verification"]["ok"] is True
    assert result["output"]["titles"]
    assert result["mock_or_live"] == "LOCAL VERIFIED"
    assert "access_token" not in str(result).lower() or "[REDACTED]" in str(result)


def test_retention_requires_data(tmp_path):
    set_skill_enabled("yt-retention", True, root=tmp_path)
    empty = execute_capability("YOUTUBE_RETENTION_ANALYSIS", parameters={}, root=tmp_path)
    assert empty["status"] == "REQUIRES_DATA"
    assert empty["output"] is None
    filled = execute_capability(
        "YOUTUBE_RETENTION_ANALYSIS",
        parameters={"rows": [[0, 100], [5, 82], [10, 70]], "source": "export", "fetched_at": "2026-09-26"},
        root=tmp_path,
    )
    assert filled["status"] == "SUCCESS"
    assert filled["output"]["curve"] is None
    assert filled["output"]["drop_points"]


def test_awesome_discovery_and_review(tmp_path):
    sync_awesome(tmp_path, markdown="[Whisper](https://github.com/openai/whisper)\n[FFmpeg](https://github.com/FFmpeg/FFmpeg)")
    discovered = execute_capability("RESOURCE_DISCOVERY", parameters={"query": "whisper"}, root=tmp_path)
    assert discovered["status"] == "SUCCESS"
    assert discovered["output"]["trust"] == "DISCOVERED"
    assert discovered["output"]["install_allowed"] is False
    reviewed = execute_capability(
        "RESOURCE_REVIEW",
        parameters={"items": discovered["output"]["items"]},
        root=tmp_path,
    )
    assert reviewed["status"] == "SUCCESS"
    assert reviewed["output"]["candidates"][0]["trust"] == "DISCOVERED"
    assert reviewed["output"]["auto_install"] is False


def test_publish_prepare_stays_approval_gated(tmp_path):
    from atnos.integrations.external_repositories import connect_social_account, set_project_account

    connect_social_account("youtube", display_name="WB", credential_ref="vault://youtube/wb", root=tmp_path)
    set_project_account("wealth_blueprint", "youtube:youtube-wb", root=tmp_path)
    result = execute_capability(
        "SOCIAL_PUBLISH_PREPARE",
        parameters={"platform_id": "youtube", "title": "Test"},
        project_id="wealth_blueprint",
        root=tmp_path,
    )
    assert result["status"] == "SUCCESS"
    assert result["output"]["published"] is False
    assert result["output"]["approval"] == "READY_FOR_APPROVAL"


def test_opentoonz_skipped_when_missing(tmp_path):
    result = execute_capability("ANIMATION_2D_RENDER", parameters={}, root=tmp_path)
    assert result["status"] == "NOT_INSTALLED"
    assert "NOT_INSTALLED" in str(result["output"])


def test_social_read_requires_setup_without_live_oauth(tmp_path):
    from atnos.integrations.external_repositories import connect_social_account

    connect_social_account("youtube", display_name="WB", credential_ref="vault://youtube/wb", root=tmp_path)
    result = execute_capability("SOCIAL_READ", parameters={"platform_id": "youtube"}, root=tmp_path)
    assert result["status"] == "REQUIRES_SETUP"


def test_classify_packaging_intent():
    capability, _params = classify_task("Prepare better YouTube packaging for this film")
    assert capability == "YOUTUBE_PACKAGING"


def test_project_access_blocks_opentoonz_on_wealth(tmp_path):
    result = execute_capability(
        "ANIMATION_2D_RENDER",
        parameters={},
        project_id="wealth_blueprint",
        root=tmp_path,
    )
    assert result["status"] == "BLOCKED"


def test_web_page_extract_parses_html(tmp_path):
    result = execute_capability(
        "WEB_PAGE_EXTRACT",
        parameters={"html": "<html><title>Atnos</title><main><h1 class='x'>Hello</h1><p>Enough paragraph text for extraction quality checks here.</p></main></html>"},
        root=tmp_path,
    )
    assert result["status"] == "SUCCESS"
    assert result["provider"] == "scrapling"
    assert result["output"]["title"] == "Atnos"
    assert any(b.get("text") == "Hello" for b in result["output"]["blocks"])


def test_web_page_extract_blocks_localhost(tmp_path):
    result = execute_capability(
        "WEB_PAGE_EXTRACT",
        parameters={"url": "http://127.0.0.1/"},
        root=tmp_path,
    )
    assert result["status"] == "BLOCKED"
