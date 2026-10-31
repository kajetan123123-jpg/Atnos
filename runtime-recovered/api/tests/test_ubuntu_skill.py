from __future__ import annotations

from atnos.skills.ubuntu_skill import build_ubuntu_plan, detect_ubuntu_intent


def test_detects_ubuntu_system_report_request() -> None:
    intent = detect_ubuntu_intent("Sprawdź stan laptopa Ubuntu i zrób raport")

    assert intent.activated is True
    assert intent.action == "system_report"
    plan = build_ubuntu_plan(intent, "Sprawdź stan laptopa Ubuntu i zrób raport")
    assert [step.tool_name for step in plan] == ["ubuntu_check", "ubuntu_system_report"]


def test_detects_ubuntu_dependency_install_request() -> None:
    intent = detect_ubuntu_intent("Zainstaluj brakujące zależności na Ubuntu")

    assert intent.activated is True
    assert intent.action == "install_dependencies"
    plan = build_ubuntu_plan(intent, "Zainstaluj brakujące zależności na Ubuntu")
    assert [step.tool_name for step in plan] == ["ubuntu_bootstrap_workspace", "ubuntu_run"]


def test_detects_ubuntu_test_request() -> None:
    intent = detect_ubuntu_intent("Uruchom testy na laptopie")

    assert intent.activated is True
    assert intent.action == "run_tests"
    plan = build_ubuntu_plan(intent, "Uruchom testy na laptopie")
    assert plan[0].tool_name == "ubuntu_project_command"


def test_does_not_activate_without_explicit_ubuntu_context() -> None:
    intent = detect_ubuntu_intent("Uruchom testy")

    assert intent.activated is False
