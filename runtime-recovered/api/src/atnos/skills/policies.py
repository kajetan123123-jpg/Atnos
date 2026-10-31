from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable

from atnos.skills.models import Skill, SkillRiskLevel


SYSTEM_COMMAND_SKILL_IDS = {
    "atnos:ubuntu-full-control",
    "atnos:codex-repo-work",
    "atnos:content-pipeline",
    "atnos:desktop-operator",
    "atnos:browser-operator",
}

FILE_REFERENCE_PATTERN = re.compile(
    r"([a-z]:\\|/|\.pdf\b|\.docx\b|\.pptx\b|\.xlsx\b|\.csv\b|\.md\b|zalacznik|plik)",
    re.IGNORECASE,
)
SSH_HINT_PATTERN = re.compile(
    r"\b(ssh|ubuntu|systemd|apt|hostname|host|ip|user|private[_ -]?key|klucz)\b",
    re.IGNORECASE,
)
FILE_REQUIREMENT_TOKENS = ("plik", "pdf", "docx", "pptx", "xlsx", "csv", "sciezka")
FLEXIBLE_FILE_ALTERNATIVE_TOKENS = (
    "brief",
    "opis",
    "cel",
    "zakres",
    "nazwa",
    "vault",
    "katalog",
    "repozytorium",
    "repo",
    "link",
    "url",
    "screenshot",
    "zrzut",
    "makieta",
    "flow",
    "html",
    "markdown",
    "notat",
)


@dataclass(slots=True, frozen=True)
class SkillPolicyEvaluation:
    requires_human_approval: bool
    requires_network: bool
    requires_credentials: bool
    requires_files: bool
    may_execute_system_commands: bool
    required_inputs: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    risks: tuple[str, ...]


def evaluate_skill_policies(
    task: str,
    skills: Iterable[Skill],
    *,
    attachments_present: bool = False,
) -> SkillPolicyEvaluation:
    skills = tuple(skills)
    required_inputs = _unique_in_order(
        item
        for skill in skills
        for item in skill.input_requirements
    )

    requires_human_approval = any(
        skill.requires_human_approval or skill.risk_level == SkillRiskLevel.HIGH
        for skill in skills
    )
    requires_network = any(skill.requires_network for skill in skills)
    requires_credentials = any(skill.requires_credentials for skill in skills)
    requires_files = any(skill.requires_files for skill in skills)
    may_execute_system_commands = any(skill.id in SYSTEM_COMMAND_SKILL_IDS for skill in skills)

    if requires_credentials:
        required_inputs = _append_unique(required_inputs, "skonfigurowane credentiale lub connector")

    normalized_task = _normalize_text(task)
    missing_inputs: list[str] = []
    if requires_files and not attachments_present and not FILE_REFERENCE_PATTERN.search(normalized_task):
        missing_inputs.extend(
            item
            for item in required_inputs
            if _input_requires_explicit_file(item)
        )

    if any(skill.id == "atnos:ubuntu-full-control" for skill in skills):
        ssh_requirements = ("host", "user", "private_key_path lub inne SSH credentials")
        required_inputs = _append_unique(required_inputs, *ssh_requirements)
        if not SSH_HINT_PATTERN.search(normalized_task):
            missing_inputs.extend(ssh_requirements)
        requires_human_approval = True
        may_execute_system_commands = True

    risks = list(
        _build_risks(
            skills,
            requires_network=requires_network,
            requires_credentials=requires_credentials,
            requires_files=requires_files,
        )
    )
    return SkillPolicyEvaluation(
        requires_human_approval=requires_human_approval,
        requires_network=requires_network,
        requires_credentials=requires_credentials,
        requires_files=requires_files,
        may_execute_system_commands=may_execute_system_commands,
        required_inputs=tuple(_unique_in_order(required_inputs)),
        missing_inputs=tuple(_unique_in_order(missing_inputs)),
        risks=tuple(_unique_in_order(risks)),
    )


def _build_risks(
    skills: Iterable[Skill],
    *,
    requires_network: bool,
    requires_credentials: bool,
    requires_files: bool,
) -> list[str]:
    risks: list[str] = []
    for skill in skills:
        if skill.risk_level == SkillRiskLevel.HIGH:
            risks.append(f"high-risk: {skill.id} moze wykonywac dzialania wrazliwe lub systemowe.")
    if requires_credentials:
        risks.append("Wymaga skonfigurowanych credentiali lub connectora przed wykonaniem.")
    if requires_network:
        risks.append("Wymaga dostepu do sieci lub aktualnych zrodel zewnetrznych.")
    if requires_files:
        risks.append("Pracuje na plikach uzytkownika; zakres plikow musi byc jawny.")
    return risks


def _append_unique(existing: Iterable[str], *items: str) -> list[str]:
    output = list(existing)
    seen = {_normalize_text(item) for item in output}
    for item in items:
        normalized = _normalize_text(item)
        if normalized and normalized not in seen:
            output.append(item)
            seen.add(normalized)
    return output


def _unique_in_order(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if normalized and normalized not in seen:
            output.append(value)
            seen.add(normalized)
    return output


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.casefold().split())


def _input_requires_explicit_file(required_input: str) -> bool:
    normalized = _normalize_text(required_input)
    if not normalized:
        return False
    if not any(_contains_token(normalized, token) for token in FILE_REQUIREMENT_TOKENS):
        return False
    if any(_contains_token(normalized, token) for token in FLEXIBLE_FILE_ALTERNATIVE_TOKENS):
        return False
    return True


def _contains_token(normalized_text: str, token: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None
