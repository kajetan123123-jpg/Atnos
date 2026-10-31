from atnos.integrations.skill_sandbox import lint_titles, run_safe_tool
from atnos.integrations.social_sdk_bridge import is_loaded, mock_read, package_version
from atnos.integrations.external_repositories import sync_awesome, VENDOR_ROOT


def test_social_sdk_package_is_loaded_and_mock_reads():
    assert is_loaded()
    assert package_version() == "0.5.0"
    result = mock_read()
    assert result["ok"] is True
    assert result["status"] == "SUCCESS"
    assert result["mock_or_live"] == "MOCK VERIFIED"
    assert result["output"]["identity"]["displayName"]
    assert "token" not in str(result).lower() or "[REDACTED]" in str(result)


def test_vendored_title_py_lints_titles():
    result = lint_titles(["Set aside money every Friday"], thumb="SAVE NOW")
    assert result["status"] == "SUCCESS"
    assert result["verification"]["ok"] is True
    ranked = result["output"]["ranked"]
    assert ranked[0]["title"]


def test_awesome_prefers_local_clone():
    assert (VENDOR_ROOT / "awesome" / "readme.md").is_file()
    result = sync_awesome()
    assert result["status"] == "HEALTHY"
    assert result["local_clone"] is True
    assert "external-repos\\awesome\\readme.md" in result["index_source"].replace("/", "\\")
