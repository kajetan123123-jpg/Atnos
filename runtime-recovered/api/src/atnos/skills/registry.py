from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import unicodedata

import yaml

from atnos.skills.models import Skill


API_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SKILLS_PATH = API_ROOT / "data" / "skills" / "skills_registry.yaml"
DEFAULT_RULES_PATH = API_ROOT / "data" / "skills" / "skill_routing_rules.yaml"


class SkillRegistry:
    def __init__(
        self,
        *,
        skills_path: str | Path | None = None,
        rules_path: str | Path | None = None,
    ) -> None:
        self.skills_path = Path(skills_path) if skills_path is not None else DEFAULT_SKILLS_PATH
        self.rules_path = Path(rules_path) if rules_path is not None else DEFAULT_RULES_PATH
        self._skills = _load_skills_file(self.skills_path)
        self._skills_by_id = {skill.id: skill for skill in self._skills}

    def list(self) -> list[Skill]:
        return list(self._skills)

    def list_skills(self, category: str | None = None) -> list[Skill]:
        if category is None:
            return self.list()
        return [skill for skill in self._skills if skill.category == category]

    def get_skill(self, skill_id: str) -> Skill | None:
        return self._skills_by_id.get(skill_id)

    def search_skills(self, query: str) -> list[Skill]:
        normalized_query = _normalize_text(query)
        if not normalized_query:
            return self.list()

        matches: list[tuple[tuple[int, int, str], Skill]] = []
        for skill in self._skills:
            haystacks = (
                skill.id,
                skill.namespace,
                skill.name,
                skill.description,
                skill.category,
                *skill.triggers,
                *skill.good_for,
                skill.notes,
            )
            score = 0
            for field in haystacks:
                normalized_field = _normalize_text(field)
                if normalized_query == normalized_field:
                    score += 5
                elif normalized_query in normalized_field:
                    score += 2
            if score > 0:
                matches.append(((-score, -skill.priority, skill.id), skill))
        return [skill for _, skill in sorted(matches, key=lambda item: item[0])]

    def validate_registry(self) -> list[str]:
        errors: list[str] = []
        raw_payload = _read_yaml(self.skills_path)
        raw_skills = raw_payload.get("skills")
        if not isinstance(raw_skills, list):
            return [f"Registry file '{self.skills_path}' must contain a top-level 'skills' list."]

        seen_ids: set[str] = set()
        alias_names: dict[str, str] = {}
        for index, raw_skill in enumerate(raw_skills):
            if not isinstance(raw_skill, dict):
                errors.append(f"Skill at index {index} must be an object.")
                continue
            skill_id = str(raw_skill.get("id", "")).strip()
            if not skill_id:
                errors.append(f"Skill at index {index} is missing 'id'.")
                continue
            if skill_id in seen_ids:
                errors.append(f"Duplicate skill id '{skill_id}'.")
            seen_ids.add(skill_id)

            notes = str(raw_skill.get("notes", "")).strip()
            if notes.lower().startswith("alias for "):
                alias_key = skill_id.lower()
                if alias_key in alias_names:
                    errors.append(f"Duplicate alias definition for '{skill_id}'.")
                alias_names[alias_key] = notes[10:].strip()

        for skill in self._skills:
            if skill.notes.lower().startswith("alias for "):
                alias_target = skill.notes[10:].strip()
                if alias_target and alias_target not in self._skills_by_id:
                    errors.append(
                        f"Alias skill '{skill.id}' points to missing canonical skill '{alias_target}'."
                    )

        rules_payload = _read_yaml(self.rules_path)
        raw_rules = rules_payload.get("rules")
        if not isinstance(raw_rules, list):
            errors.append(f"Rules file '{self.rules_path}' must contain a top-level 'rules' list.")
            return errors

        for index, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict):
                errors.append(f"Rule at index {index} must be an object.")
                continue
            for key in ("select", "prefer", "reject"):
                values = raw_rule.get(key)
                if values is None:
                    continue
                if not isinstance(values, list):
                    errors.append(f"Rule '{raw_rule.get('id', index)}' field '{key}' must be a list.")
                    continue
                for skill_id in values:
                    if str(skill_id) not in self._skills_by_id:
                        errors.append(
                            f"Rule '{raw_rule.get('id', index)}' references unknown skill '{skill_id}'."
                        )
        return errors


def load_skills(path: str | None = None) -> list[Skill]:
    registry = SkillRegistry(skills_path=path) if path is not None else build_default_skill_registry()
    return registry.list()


def get_skill(skill_id: str) -> Skill | None:
    return build_default_skill_registry().get_skill(skill_id)


def list_skills(category: str | None = None) -> list[Skill]:
    return build_default_skill_registry().list_skills(category=category)


def search_skills(query: str) -> list[Skill]:
    return build_default_skill_registry().search_skills(query=query)


def validate_registry() -> list[str]:
    return build_default_skill_registry().validate_registry()


@lru_cache(maxsize=1)
def build_default_skill_registry() -> SkillRegistry:
    return SkillRegistry()


def _load_skills_file(path: Path) -> list[Skill]:
    payload = _read_yaml(path)
    raw_skills = payload.get("skills", [])
    if not isinstance(raw_skills, list):
        raise ValueError(f"Registry file '{path}' must contain a top-level 'skills' list.")
    return [Skill.model_validate(item) for item in raw_skills]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Skill data file '{path}' does not exist.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Skill data file '{path}' must contain a mapping at the top level.")
    return payload


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())
