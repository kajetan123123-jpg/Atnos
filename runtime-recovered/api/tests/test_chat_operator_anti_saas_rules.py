from __future__ import annotations

from pathlib import Path

from atnos.tools.chat_agent_tools import CHAT_AGENT_OPERATIONAL_RULES, load_atnos_agent_identity


def test_operational_rules_forbid_saas_refusals_and_image_pivot() -> None:
    rules = CHAT_AGENT_OPERATIONAL_RULES.casefold()
    assert "desktop_app_open" in rules
    assert "zainstaluj" in rules
    assert "midjourney" in rules
    assert "dall-e" in rules
    assert "zakazane" in rules
    assert "ograniczają się do tekstu" in rules


def test_identity_forbids_text_only_pc_refusals() -> None:
    identity = load_atnos_agent_identity().casefold()
    assert "lokalny program windows" in identity or "desktop_app_open" in identity
    assert "midjourney" in identity or "dall-e" in identity
    assert "zainstalować" in identity or "uruchomić" in identity
    contract = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "atnos"
        / "prompts"
        / "interaction_contract.txt"
    ).read_text(encoding="utf-8").casefold()
    assert "false refusals" in contract or "midjourney" in contract
    assert "desktop" in contract
