from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from atnos.skills.models import SkillRouteDecision
from atnos.skills.skill_prompt import SKILL_INSTRUCTION_FILES, load_skill_prompt_context


def _decision(*skill_ids: str) -> SkillRouteDecision:
    return SkillRouteDecision(
        task="Przygotuj stan kanału i zapisz notatkę",
        selected_skills=list(skill_ids),
        rejected_skills=[],
        reasoning_summary="Zadanie wymaga obsługi kanału i pamięci.",
        required_inputs=[],
        risks=[],
        next_action="Odczytaj bieżący stan i źródła.",
        confidence=0.9,
    )


def test_loads_only_selected_reviewed_instruction_packs_and_records_hash(tmp_path):
    (tmp_path / "channel-ops.md").write_text("Sprawdź aktualny etap.", encoding="utf-8")
    (tmp_path / "obsidian-memory.md").write_text("Pobieraj notatki tematycznie.", encoding="utf-8")
    (tmp_path / "desktop-operator.md").write_text("Nie powinno się wczytać.", encoding="utf-8")

    result = load_skill_prompt_context(
        "Przygotuj stan kanału i zapisz notatkę",
        _decision("atnos:channel-ops", "atnos:obsidian-memory"),
        instructions_root=tmp_path,
    )

    loaded = result.metadata["loaded_instructions"]
    assert [item["skill_id"] for item in loaded] == ["atnos:channel-ops", "atnos:obsidian-memory"]
    assert loaded[0]["sha256"] == hashlib.sha256("Sprawdź aktualny etap.".encode("utf-8")).hexdigest()
    assert "Sprawdź aktualny etap." in result.prompt
    assert "Pobieraj notatki tematycznie." in result.prompt
    assert "Nie powinno się wczytać." not in result.prompt
    assert result.metadata["instruction_status"] == "loaded"


def test_missing_instruction_pack_is_explicit_not_reported_as_loaded(tmp_path):
    result = load_skill_prompt_context(
        "Zrób lokalne zadanie",
        _decision("atnos:desktop-operator"),
        instructions_root=tmp_path,
    )

    assert result.metadata["instruction_status"] == "no_reviewed_pack_loaded"
    assert result.metadata["loaded_instructions"] == []
    assert result.metadata["missing_instruction_skills"] == ["atnos:desktop-operator"]
    assert "Nie twierdź, że ich procedury zostały zastosowane." in result.prompt


def test_unmapped_skill_cannot_read_arbitrary_file(tmp_path):
    (tmp_path / "secret.md").write_text("Do not expose", encoding="utf-8")
    result = load_skill_prompt_context(
        "Zadanie",
        _decision("attacker:secret"),
        instructions_root=tmp_path,
    )

    assert result.metadata["loaded_instructions"] == []
    assert result.metadata["missing_instruction_skills"] == ["attacker:secret"]
    assert "Do not expose" not in result.prompt


def test_partial_skill_context_reports_every_skill_omitted_by_size_limit(tmp_path, monkeypatch):
    import atnos.skills.skill_prompt as skill_prompt

    (tmp_path / "channel-ops.md").write_text("first", encoding="utf-8")
    (tmp_path / "obsidian-memory.md").write_text("second is too long", encoding="utf-8")
    monkeypatch.setattr(skill_prompt, "MAX_INSTRUCTION_CHARS", 8)

    result = load_skill_prompt_context(
        "Zadanie",
        _decision("atnos:channel-ops", "atnos:obsidian-memory", "atnos:desktop-operator"),
        instructions_root=tmp_path,
    )

    assert result.metadata["instruction_status"] == "partial"
    assert [item["skill_id"] for item in result.metadata["loaded_instructions"]] == [
        "atnos:channel-ops"
    ]
    assert result.metadata["missing_instruction_skills"] == [
        "atnos:obsidian-memory",
        "atnos:desktop-operator",
    ]
    assert result.metadata["missing_instruction_reasons"] == {
        "atnos:obsidian-memory": "instruction_context_limit",
        "atnos:desktop-operator": "instruction_context_limit",
    }


def test_reviewed_obsidian_skill_contains_bounded_retrieval_and_live_truth_rules():
    result = load_skill_prompt_context(
        "Przypomnij decyzję WB i zapisz nową preferencję w Obsidianie",
        _decision("atnos:obsidian-memory"),
    )

    assert result.metadata["instruction_status"] == "loaded"
    assert "atnos-chat/memory` and `atnos-chat/decision`" in result.prompt
    assert "at most four notes" in result.prompt
    assert "Do not retrieve local memory into a cloud-provider prompt" in result.prompt
    assert "report `UNKNOWN`" in result.prompt


def test_every_available_first_party_skill_has_a_reviewed_instruction_pack():
    api_root = Path(__file__).resolve().parents[1]
    registry_path = api_root / "data" / "skills" / "skills_registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    available_first_party = {
        item["id"]
        for item in registry["skills"]
        if item["namespace"] == "atnos" and item["status"] == "available"
    }

    assert available_first_party == set(SKILL_INSTRUCTION_FILES)
    for filename in SKILL_INSTRUCTION_FILES.values():
        content = (api_root / "data" / "skills" / "instructions" / filename).read_text(
            encoding="utf-8"
        )
        assert content.strip()


def test_new_first_party_skill_packs_are_loaded_and_hashed(tmp_path):
    skills = [
        "atnos:deep-research",
        "atnos:ubuntu-full-control",
        "atnos:image-generation",
        "atnos:file-analysis",
        "atnos:tool-selection",
        "atnos:browser-operator",
    ]
    for skill_id in skills:
        filename = SKILL_INSTRUCTION_FILES[skill_id]
        (tmp_path / filename).write_text(f"Instructions for {skill_id}", encoding="utf-8")

    result = load_skill_prompt_context("Sprawdź capabilities", _decision(*skills), instructions_root=tmp_path)

    assert [item["skill_id"] for item in result.metadata["loaded_instructions"]] == skills
    assert result.metadata["missing_instruction_skills"] == []
    for skill_id in skills:
        assert f"Instructions for {skill_id}" in result.prompt
