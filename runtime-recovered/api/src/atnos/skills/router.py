from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata
from typing import Pattern

import yaml

from atnos.skills.models import Skill, SkillRouteDecision, SkillStatus
from atnos.skills.policies import SkillPolicyEvaluation, evaluate_skill_policies
from atnos.skills.registry import (
    DEFAULT_RULES_PATH,
    build_default_skill_registry,
)


STATUS_ORDER = {
    SkillStatus.AVAILABLE: 0,
    SkillStatus.EXTERNAL: 1,
    SkillStatus.PLANNED: 2,
    SkillStatus.UNAVAILABLE: 3,
}

MEMORY_OPT_OUT_PATTERN = re.compile(
    r"\b(?:do not|dont|never)\s+(?:save|store|write)\b.*\b(?:memory|obsidian|notes)\b"
    r"|\b(?:nie zapisuj|nie zapamietuj|bez zapisywania)\b.*\b(?:pamieci|obs[yi]dian(?:a|ie)?|notatek)\b",
    re.IGNORECASE | re.DOTALL,
)

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "design": ("design", "ux", "ui", "wcag", "accessibility", "copy", "handoff"),
    "documents": ("pdf", "docx", "word", "ppt", "pptx", "slides", "xlsx", "excel", "csv"),
    "brand-voice": ("brand voice", "glos marki", "głos marki", "tone of voice", "mail", "copy"),
    "sanity": ("sanity", "portable text", "groq", "schema"),
    "human-resources": ("hr", "onboarding", "rekrut", "recruit", "offer", "wynagrodz", "performance review"),
    "research": ("research", "link", "porown", "porówn", "market", "docs", "documentation", "latest"),
    "system": ("ssh", "ubuntu", "systemd", "chrome", "obsidian", "desktop", "browser"),
    "dev": ("repo", "code", "bug", "test", "cli", "github", "refactor"),
    "automation": ("youtube", "upload", "analytics", "pipeline", "render", "publish"),
}


@dataclass(frozen=True, slots=True)
class RoutingRule:
    id: str
    match_any: tuple[str, ...]
    match_all: tuple[str, ...]
    select: tuple[str, ...]
    prefer: tuple[str, ...]
    reject: tuple[str, ...]
    reason: str

    def matches(self, normalized_task: str) -> bool:
        any_match = not self.match_any or any(_contains_token(normalized_task, token) for token in self.match_any)
        all_match = all(_contains_token(normalized_task, token) for token in self.match_all)
        return any_match and all_match


def route_task(task: str) -> SkillRouteDecision:
    registry = build_default_skill_registry()
    skills = registry.list()
    # Folder names are operands, not user intent (e.g. browser/quote.txt).
    routing_task = re.sub(r'''(?i)(?:"[a-z]:[\\/][^"]+"|'[a-z]:[\\/][^']+'|[a-z]:[\\/][^\s"'`]+)''', lambda match: " local-file " + (match.group(0).rstrip("\"'").rsplit(".", 1)[-1] if "." in match.group(0) else ""), task)
    normalized_task = _normalize_text(routing_task)
    explicit_mentions = _find_explicit_skill_mentions(normalized_task, skills)
    all_rules = _load_rules()
    matched_rules = [
        rule
        for rule in all_rules
        if rule.matches(normalized_task) and (rule.match_any or rule.match_all)
    ]
    if MEMORY_OPT_OUT_PATTERN.search(normalized_task):
        matched_rules = [rule for rule in matched_rules if rule.id != "explicit-memory"]

    explicit_mentions_set = set(explicit_mentions)
    score_map = {
        skill.id: _score_skill(normalized_task, skill, explicit_mentions=explicit_mentions_set)
        for skill in skills
    }
    applied_rule_ids: list[str] = []
    selected_ids: list[str] = []
    rejected: list[str] = []

    for skill_id in explicit_mentions:
        selected_ids = _append_unique(selected_ids, skill_id)

    for rule in matched_rules:
        applied_rule_ids.append(rule.id)
        selected_ids = _append_unique(selected_ids, *rule.select)
        for preferred_skill in rule.prefer:
            score_map[preferred_skill] = score_map.get(preferred_skill, 0) + 20
        for rejected_skill in rule.reject:
            rejected.append(f"{rejected_skill}: odrzucony przez regule '{rule.id}'.")

    ranked_skills = _rank_skills_from_scores(skills=skills, score_map=score_map)
    if not selected_ids and not matched_rules:
        fallback_rule = next(
            (rule for rule in all_rules if not rule.match_any and not rule.match_all),
            None,
        )
        if fallback_rule is not None:
            matched_rules = [fallback_rule]
            applied_rule_ids.append(fallback_rule.id)
            selected_ids = _append_unique(selected_ids, *fallback_rule.select)

    if not selected_ids:
        for skill in ranked_skills:
            if score_map.get(skill.id, 0) > 0:
                selected_ids.append(skill.id)
            if len(selected_ids) >= 3:
                break

    if not selected_ids:
        selected_ids = ["atnos:local-llm"]

    selected_skills = [skill for skill in skills if skill.id in selected_ids]
    selected_skills.sort(key=lambda item: selected_ids.index(item.id))
    if len(selected_skills) > 3:
        keep_ids = {
            skill.id
            for skill in sorted(
                selected_skills,
                key=lambda item: (
                    -score_map.get(item.id, 0),
                    -item.priority,
                    STATUS_ORDER[item.status],
                    item.id,
                ),
            )[:3]
        }
        selected_ids = [skill_id for skill_id in selected_ids if skill_id in keep_ids]
        selected_skills = [skill for skill in selected_skills if skill.id in keep_ids]
    if not matched_rules and len(selected_skills) > 3:
        selected_skills = selected_skills[:3]
        selected_ids = [skill.id for skill in selected_skills]

    rejected.extend(_build_rejected_skills(normalized_task, skills, selected_ids, score_map))

    policy = evaluate_skill_policies(task, selected_skills)
    reasoning_summary = _build_reasoning_summary(
        task=task,
        selected_skills=selected_skills,
        matched_rules=matched_rules,
        explicit_mentions=explicit_mentions,
    )
    next_action = _build_next_action(selected_skills, policy)
    confidence = _build_confidence(
        score_map=score_map,
        selected_ids=selected_ids,
        matched_rules=matched_rules,
        explicit_mentions=explicit_mentions,
    )

    return SkillRouteDecision(
        task=task,
        selected_skills=selected_ids,
        rejected_skills=_dedupe(rejected),
        reasoning_summary=reasoning_summary,
        required_inputs=list(policy.required_inputs),
        risks=list(policy.risks),
        next_action=next_action,
        confidence=confidence,
    )


def explain_route(task: str) -> str:
    decision = route_task(task)
    skills = ", ".join(decision.selected_skills) or "brak"
    rejected = ", ".join(decision.rejected_skills) or "brak"
    required_inputs = ", ".join(decision.required_inputs) or "brak"
    risks = ", ".join(decision.risks) or "brak"
    return (
        f"Wybrane skille: {skills}.\n"
        f"Powod: {decision.reasoning_summary}\n"
        f"Wymagane inputy: {required_inputs}\n"
        f"Ryzyka: {risks}\n"
        f"Odrzucone skille: {rejected}\n"
        f"Nastepny krok: {decision.next_action}\n"
        f"Pewnosc: {decision.confidence:.2f}"
    )


def rank_skills(task: str, skills: list[Skill]) -> list[Skill]:
    normalized_task = _normalize_text(task)
    explicit_mentions = _find_explicit_skill_mentions(normalized_task, skills)
    score_map = {
        skill.id: _score_skill(normalized_task, skill, explicit_mentions=set(explicit_mentions))
        for skill in skills
    }
    return _rank_skills_from_scores(skills=skills, score_map=score_map)


def _rank_skills_from_scores(*, skills: list[Skill], score_map: dict[str, int]) -> list[Skill]:
    scored = [
        (
            -score_map.get(skill.id, 0),
            -skill.priority,
            STATUS_ORDER[skill.status],
            skill.id,
            skill,
        )
        for skill in skills
    ]
    scored.sort(key=lambda item: item[:4])
    return [skill for *_, skill in scored]


def _build_rejected_skills(
    normalized_task: str,
    skills: list[Skill],
    selected_ids: list[str],
    score_map: dict[str, int],
) -> list[str]:
    rejected: list[str] = []
    for skill in skills:
        if skill.id in selected_ids:
            continue
        if any(_contains_token(normalized_task, token) for token in skill.not_for):
            rejected.append(f"{skill.id}: task pasuje do 'not_for'.")
            continue
        score = score_map.get(skill.id, 0)
        if score <= 0:
            continue
        if skill.status == SkillStatus.UNAVAILABLE:
            rejected.append(f"{skill.id}: skill jest obecnie unavailable.")
        elif skill.status == SkillStatus.PLANNED:
            rejected.append(f"{skill.id}: skill jest planned i przegral z dostepnym skilllem.")
        elif skill.status == SkillStatus.EXTERNAL:
            rejected.append(f"{skill.id}: wymaga zewnetrznego execution path i przegral z lepszym kandydatem.")
        else:
            rejected.append(f"{skill.id}: nizszy wynik rankingu niz wybrane skille.")
    return rejected


def _build_reasoning_summary(
    *,
    task: str,
    selected_skills: list[Skill],
    matched_rules: list[RoutingRule],
    explicit_mentions: list[str],
) -> str:
    reasons: list[str] = []
    if explicit_mentions:
        reasons.append(f"Uzytkownik jawnie wspomnial: {', '.join(explicit_mentions)}.")
    if matched_rules:
        reasons.append(
            "Zadzialaly reguly: "
            + "; ".join(f"{rule.id} ({rule.reason})" for rule in matched_rules[:3])
            + "."
        )
    if not reasons:
        reasons.append("Wybor wynika z triggerow, good_for i priorytetow skilli.")
    reasons.append(
        "Wybrane skille: " + ", ".join(skill.id for skill in selected_skills) + "."
    )
    return " ".join(reasons)


def _build_next_action(selected_skills: list[Skill], policy: SkillPolicyEvaluation) -> str:
    if policy.missing_inputs:
        return "Popros uzytkownika o brakujace inputy: " + ", ".join(policy.missing_inputs) + "."
    if any(skill.id == "anthropic-skills:pdf" for skill in selected_skills):
        return "Wczytaj PDF i streść tresc."
    if any(skill.id == "atnos:ubuntu-full-control" for skill in selected_skills):
        return "Popros o jawne potwierdzenie i dane SSH, potem wykonaj provisioning."
    if any(skill.id == "atnos:codex-repo-work" for skill in selected_skills):
        return "Przejdz do repo, odtworz problem i przygotuj plan zmian oraz testow."
    if any(skill.id == "atnos:deep-research" for skill in selected_skills):
        return "Uruchom research webowy, zbierz cytaty i zsyntezuj wynik."
    if any(skill.id == "atnos:image-generation" for skill in selected_skills):
        return "Wygeneruj grafike i zwroc gotowy artefakt PNG."
    if any(skill.id == "atnos:content-pipeline" for skill in selected_skills):
        return "Uruchom pipeline shorta lub wideo, przygotuj artefakty do review i nie publikuj bez approval."
    if any(skill.id == "brand-voice:enforce-voice" for skill in selected_skills):
        return "Zbierz wytyczne glosu marki i przygotuj tresc zgodna z tonem."
    return "Wykonaj task zgodnie z wybranymi skillami albo popros o brakujacy kontekst."


def _build_confidence(
    *,
    score_map: dict[str, int],
    selected_ids: list[str],
    matched_rules: list[RoutingRule],
    explicit_mentions: list[str],
) -> float:
    top_score = max((score_map.get(skill_id, 0) for skill_id in selected_ids), default=0)
    ordered_scores = sorted(score_map.values(), reverse=True)
    next_score = ordered_scores[1] if len(ordered_scores) > 1 else 0
    delta = max(top_score - next_score, 0)
    confidence = 0.35
    if matched_rules:
        confidence += 0.25
    if explicit_mentions:
        confidence += 0.2
    confidence += min(delta / 100.0, 0.2)
    if top_score <= 0:
        confidence = 0.3
    return round(min(confidence, 0.99), 2)


def _score_skill(normalized_task: str, skill: Skill, *, explicit_mentions: set[str]) -> int:
    score = 0
    if skill.id in explicit_mentions:
        score += 25
    score += 15 * sum(1 for token in skill.triggers if _contains_token(normalized_task, token))
    score += 8 * sum(1 for token in skill.good_for if _contains_token(normalized_task, token))
    if any(_contains_token(normalized_task, token) for token in CATEGORY_HINTS.get(skill.category, ())):
        score += 4
    if any(_contains_token(normalized_task, token) for token in CATEGORY_HINTS.get(skill.namespace, ())):
        score += 4
    score -= 20 * sum(1 for token in skill.not_for if _contains_token(normalized_task, token))
    return score


def _find_explicit_skill_mentions(normalized_task: str, skills: list[Skill]) -> list[str]:
    mentions: list[str] = []
    for skill in skills:
        if skill.id and skill.id.casefold() in normalized_task:
            mentions.append(skill.id)
    return mentions


@lru_cache(maxsize=1)
def _load_rules() -> tuple[RoutingRule, ...]:
    payload = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8")) or {}
    raw_rules = payload.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError(f"Skill routing rules in '{DEFAULT_RULES_PATH}' must contain a top-level 'rules' list.")
    rules: list[RoutingRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            raise ValueError("Each routing rule must be an object.")
        rules.append(
            RoutingRule(
                id=str(item["id"]),
                match_any=tuple(str(token) for token in item.get("match_any", []) or ()),
                match_all=tuple(str(token) for token in item.get("match_all", []) or ()),
                select=tuple(str(token) for token in item.get("select", []) or ()),
                prefer=tuple(str(token) for token in item.get("prefer", []) or ()),
                reject=tuple(str(token) for token in item.get("reject", []) or ()),
                reason=str(item["reason"]),
            )
        )
    return tuple(rules)


def _contains_token(normalized_task: str, token: str) -> bool:
    pattern = _token_pattern(token)
    if pattern is None:
        return False
    return pattern.search(normalized_task) is not None


@lru_cache(maxsize=2048)
def _token_pattern(token: str) -> Pattern[str] | None:
    normalized_token = _normalize_text(token)
    if not normalized_token:
        return None
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalized_token)}(?![a-z0-9])")


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    collapsed = " ".join(ascii_only.casefold().split())
    collapsed = re.sub(r"[^a-z0-9:_./\\ -]+", " ", collapsed)
    return " ".join(collapsed.split())


def _append_unique(existing: list[str], *items: str) -> list[str]:
    seen = set(existing)
    output = list(existing)
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output
