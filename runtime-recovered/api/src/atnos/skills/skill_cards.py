from __future__ import annotations

from typing import Any

from atnos.skills.base import SkillReadiness
from atnos.skills.models import Skill, SkillExecutionType, SkillStatus
from atnos.skills.policies import evaluate_skill_policies
from atnos.skills.registry import build_default_skill_registry


UI_CATEGORY_MAP = {
    "design": "creation",
    "documents": "creation",
    "brand-voice": "creation",
    "sanity": "dev",
    "human-resources": "research",
    "research": "research",
    "system": "system",
    "dev": "dev",
    "memory": "system",
    "automation": "creation",
}


def build_skill_dashboard_payload(*, fast_mode: bool = False) -> dict[str, Any]:
    items = [build_skill_card_payload(skill, fast_mode=fast_mode) for skill in build_default_skill_registry().list()]
    categories = [
        {"key": "creation", "label": "Tworzenie", "count": sum(1 for item in items if item["category"] == "creation")},
        {"key": "system", "label": "System", "count": sum(1 for item in items if item["category"] == "system")},
        {"key": "research", "label": "Research", "count": sum(1 for item in items if item["category"] == "research")},
        {"key": "dev", "label": "Dev", "count": sum(1 for item in items if item["category"] == "dev")},
    ]
    summary = {
        "total_skills": len(items),
        "ready": sum(1 for item in items if item["readiness"] == SkillReadiness.READY.value),
        "partial": sum(1 for item in items if item["readiness"] == SkillReadiness.PARTIAL.value),
        "blueprint": sum(1 for item in items if item["readiness"] == SkillReadiness.BLUEPRINT.value),
        "future": sum(1 for item in items if item["readiness"] == SkillReadiness.FUTURE.value),
    }
    return {
        "summary": summary,
        "categories": categories,
        "items": items,
    }


def build_skill_card_payload(skill: Skill, *, fast_mode: bool = False) -> dict[str, Any]:
    readiness = _derive_readiness(skill)
    policy = evaluate_skill_policies(skill.id, [skill], attachments_present=True)
    status_reason = _build_status_reason(skill, fast_mode=fast_mode, readiness=readiness, policy=policy)
    return {
        "key": skill.id,
        "label": skill.id,
        "category": UI_CATEGORY_MAP.get(skill.category, "dev"),
        "readiness": readiness.value,
        "summary": skill.description,
        "repo_name": skill.namespace,
        "repo_url": "",
        "why_it_matters": skill.description,
        "amd_profile": _build_amd_profile(skill),
        "next_integration_step": _build_next_integration_step(skill),
        "related_modules": _build_related_modules(skill),
        "blockers": _build_blockers(skill),
        "tool_contracts": [],
        "learning_note_path": None,
        "status_reason": status_reason,
    }


def _derive_readiness(skill: Skill) -> SkillReadiness:
    if skill.status == SkillStatus.UNAVAILABLE:
        return SkillReadiness.FUTURE
    if skill.status in {SkillStatus.PLANNED, SkillStatus.EXTERNAL}:
        return SkillReadiness.BLUEPRINT
    if skill.execution_type in {SkillExecutionType.CONNECTOR, SkillExecutionType.EXTERNAL_API} or skill.requires_credentials:
        return SkillReadiness.PARTIAL
    return SkillReadiness.READY


def _build_status_reason(skill: Skill, *, fast_mode: bool, readiness: SkillReadiness, policy) -> str:
    if readiness == SkillReadiness.READY:
        base = "Skill jest gotowy do routingu i nie wymaga brakujacej warstwy wykonawczej."
    elif readiness == SkillReadiness.PARTIAL:
        base = "Skill jest routowalny, ale wymaga connectora, credentiali albo execution path poza samym routerem."
    elif readiness == SkillReadiness.BLUEPRINT:
        base = "Skill jest opisany kontraktem i moze byc wybrany, ale wykonanie nadal zalezy od przyszlej integracji."
    else:
        base = "Skill jest obecnie unavailable i pozostaje tylko planem produktu."
    suffixes: list[str] = []
    if policy.requires_credentials:
        suffixes.append("Wymaga credentiali.")
    if policy.requires_network:
        suffixes.append("Wymaga sieci.")
    if policy.requires_files:
        suffixes.append("Pracuje na plikach.")
    if fast_mode:
        suffixes.append("Fast settings mode pomija runtime probe.")
    return " ".join([base, *suffixes]).strip()


def _build_amd_profile(skill: Skill) -> str:
    if skill.execution_type == SkillExecutionType.LOCAL:
        return "Lokalny skill. Brak twardego blokera GPU na poziomie registry."
    if skill.execution_type == SkillExecutionType.EXTERNAL_API:
        return "Zalezy glownie od API i routingu, nie od lokalnego GPU."
    if skill.execution_type == SkillExecutionType.CONNECTOR:
        return "Zalezy od connectora i konta bardziej niz od lokalnego runtime."
    if skill.execution_type == SkillExecutionType.FUTURE:
        return "Blueprint przyszlej integracji; profil runtime zostanie doprecyzowany pozniej."
    return "Manualny lub hybrydowy skill; runtime zalezy od konkretnego workflow."


def _build_next_integration_step(skill: Skill) -> str:
    if skill.status == SkillStatus.AVAILABLE:
        return "Podpinaj execution path tylko tam, gdzie realny workflow tego wymaga."
    if skill.status == SkillStatus.EXTERNAL:
        return "Skonfiguruj connector lub API zanim skill przejdzie z routingu do wykonania."
    if skill.status == SkillStatus.PLANNED:
        return "Doprecyzuj adapter wykonawczy i test integracyjny dla tego skilla."
    return "Zostaw jako roadmap item, dopoki nie pojawi sie realny use-case."


def _build_related_modules(skill: Skill) -> list[str]:
    related: list[str] = ["skills/router.py", "skills/registry.py"]
    if skill.namespace == "atnos":
        related.append("services/chat_orchestration_service.py")
    return related


def _build_blockers(skill: Skill) -> list[str]:
    blockers: list[str] = []
    if skill.status == SkillStatus.PLANNED:
        blockers.append("Brak podlaczonego execution path.")
    if skill.status == SkillStatus.EXTERNAL:
        blockers.append("Brak skonfigurowanego API lub connectora.")
    if skill.status == SkillStatus.UNAVAILABLE:
        blockers.append("Skill pozostaje niedostepny w tym runtime.")
    if skill.requires_credentials:
        blockers.append("Wymaga credentiali lub zgody na konto.")
    return blockers
