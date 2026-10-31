from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from atnos.skills.models import SkillRouteDecision


API_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INSTRUCTIONS_ROOT = API_ROOT / "data" / "skills" / "instructions"
MAX_INSTRUCTION_CHARS = 12_000

# Explicit allowlist: a routed skill can only load a reviewed first-party pack.
SKILL_INSTRUCTION_FILES = {
    "atnos:channel-ops": "channel-ops.md",
    "atnos:obsidian-memory": "obsidian-memory.md",
    "atnos:desktop-operator": "desktop-operator.md",
    "atnos:codex-repo-work": "codex-repo-work.md",
    "atnos:content-pipeline": "content-pipeline.md",
    "atnos:local-llm": "local-llm.md",
    "atnos:deep-research": "deep-research.md",
    "atnos:ubuntu-full-control": "ubuntu-full-control.md",
    "atnos:image-generation": "image-generation.md",
    "atnos:file-analysis": "file-analysis.md",
    "atnos:tool-selection": "tool-selection.md",
    "atnos:browser-operator": "browser-operator.md",
}


@dataclass(frozen=True)
class SkillPromptContext:
    prompt: str
    metadata: dict[str, object]


def load_skill_prompt_context(
    task: str,
    decision: SkillRouteDecision,
    *,
    instructions_root: str | Path | None = None,
) -> SkillPromptContext:
    """Load only reviewed instruction packs for the skills selected by the router."""
    root = Path(instructions_root) if instructions_root is not None else DEFAULT_INSTRUCTIONS_ROOT
    selected = ", ".join(decision.selected_skills) or "brak"
    required_inputs = ", ".join(decision.required_inputs) or "brak"
    risks = ", ".join(decision.risks) or "brak"
    sections = [
        f"Dla tego zadania wybrano: {selected}.",
        f"Powód: {decision.reasoning_summary}",
        f"Wymagane inputy: {required_inputs}.",
        f"Ryzyka: {risks}.",
        f"Następny krok: {decision.next_action}",
    ]
    loaded: list[dict[str, str]] = []
    missing: list[str] = []
    missing_reasons: dict[str, str] = {}
    remaining = MAX_INSTRUCTION_CHARS
    for index, skill_id in enumerate(decision.selected_skills):
        filename = SKILL_INSTRUCTION_FILES.get(skill_id)
        if filename is None:
            missing.append(skill_id)
            missing_reasons[skill_id] = "not_in_reviewed_allowlist"
            continue
        path = (root / filename).resolve()
        if path.parent != root.resolve():
            missing.append(skill_id)
            missing_reasons[skill_id] = "instruction_path_outside_root"
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError:
            missing.append(skill_id)
            missing_reasons[skill_id] = "instruction_file_unavailable"
            continue
        if not content:
            missing.append(skill_id)
            missing_reasons[skill_id] = "instruction_file_empty"
            continue
        if len(content) > remaining:
            for omitted_skill_id in decision.selected_skills[index:]:
                if omitted_skill_id not in missing:
                    missing.append(omitted_skill_id)
                missing_reasons.setdefault(omitted_skill_id, "instruction_context_limit")
            break
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        loaded.append({"skill_id": skill_id, "instruction_file": filename, "sha256": digest})
        sections.append(f"\n--- Instrukcje skillu {skill_id} ({filename}) ---\n{content}")
        remaining -= len(content)
    if missing:
        sections.append(
            "\nWybrane skille bez załadowanego pakietu: "
            + ", ".join(missing)
            + ". Nie twierdź, że ich procedury zostały zastosowane."
        )
    return SkillPromptContext(
        prompt="\n".join(sections),
        metadata={
            "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "instruction_status": (
                "partial"
                if loaded and missing
                else "loaded"
                if loaded
                else "no_reviewed_pack_loaded"
            ),
            "loaded_instructions": loaded,
            "missing_instruction_skills": missing,
            "missing_instruction_reasons": missing_reasons,
            "instruction_char_limit": MAX_INSTRUCTION_CHARS,
        },
    )


def build_skill_context_prompt(task: str, decision: SkillRouteDecision) -> str:
    """Backward-compatible prompt builder used by existing Atnos callers."""
    return load_skill_prompt_context(task, decision).prompt
