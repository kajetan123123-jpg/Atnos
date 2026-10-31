from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re


class PromptWorkload(str, Enum):
    CODING = "coding"
    RESEARCH = "research"
    IMAGE = "image"
    VIDEO = "video"
    AUTOMATION = "automation"
    AGENTIC = "agentic"
    GENERAL = "general"


@dataclass(frozen=True, slots=True)
class PromptToolProfile:
    key: str
    label: str
    workload: PromptWorkload
    aliases: tuple[str, ...]
    setup_note: str | None = None
    validation_required: bool = False
    prefers_citations: bool = False


@dataclass(frozen=True, slots=True)
class PromptQualityReport:
    unclear_goal: bool = False
    missing_target_tool: bool = False
    vague_output_format: bool = False
    missing_constraints: bool = False
    unnecessary_verbosity: bool = False
    unsupported_reasoning_request: bool = False
    conflicting_instructions: bool = False
    missing_validation_steps: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "unclear_goal": self.unclear_goal,
            "missing_target_tool": self.missing_target_tool,
            "vague_output_format": self.vague_output_format,
            "missing_constraints": self.missing_constraints,
            "unnecessary_verbosity": self.unnecessary_verbosity,
            "unsupported_reasoning_request": self.unsupported_reasoning_request,
            "conflicting_instructions": self.conflicting_instructions,
            "missing_validation_steps": self.missing_validation_steps,
        }


@dataclass(frozen=True, slots=True)
class PromptOptimizationBrief:
    profile: PromptToolProfile
    goal: str | None
    source_prompt: str | None
    available_context: tuple[str, ...]
    output_format: str | None
    constraints: tuple[str, ...]
    success_criteria: tuple[str, ...]
    expected_workload: PromptWorkload
    quality: PromptQualityReport
    needs_clarification: bool
    clarifying_questions: tuple[str, ...]
    memory_context: str | None = None


@dataclass(frozen=True, slots=True)
class PromptOptimizationResult:
    activated: bool
    brief: PromptOptimizationBrief | None = None
    response_text: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class PromptOptimizerService:
    _TOOL_PROFILES: tuple[PromptToolProfile, ...] = (
        PromptToolProfile(
            key="codex",
            label="Codex",
            workload=PromptWorkload.CODING,
            aliases=("codex", "openai codex"),
            setup_note="Open the target repository or workspace before running this prompt.",
            validation_required=True,
        ),
        PromptToolProfile(
            key="claude-code",
            label="Claude Code",
            workload=PromptWorkload.CODING,
            aliases=("claude code",),
            setup_note="Open the target repository or workspace before running this prompt.",
            validation_required=True,
        ),
        PromptToolProfile(
            key="cursor",
            label="Cursor",
            workload=PromptWorkload.CODING,
            aliases=("cursor", "cursor ai"),
            setup_note="Open the target repository or workspace before running this prompt.",
            validation_required=True,
        ),
        PromptToolProfile(
            key="chatgpt",
            label="ChatGPT / OpenAI",
            workload=PromptWorkload.GENERAL,
            aliases=("chatgpt", "openai", "gpt", "gpt-5"),
        ),
        PromptToolProfile(
            key="gemini",
            label="Gemini",
            workload=PromptWorkload.GENERAL,
            aliases=("gemini", "google gemini"),
        ),
        PromptToolProfile(
            key="github-copilot",
            label="GitHub Copilot",
            workload=PromptWorkload.CODING,
            aliases=("github copilot", "copilot"),
            setup_note="Open the target repository or workspace before using this prompt.",
            validation_required=True,
        ),
        PromptToolProfile(
            key="windsurf",
            label="Windsurf",
            workload=PromptWorkload.CODING,
            aliases=("windsurf", "codeium windsurf"),
            setup_note="Open the target repository or workspace before using this prompt.",
            validation_required=True,
        ),
        PromptToolProfile(
            key="lovable",
            label="Lovable",
            workload=PromptWorkload.AGENTIC,
            aliases=("lovable",),
            setup_note="Have the target product context, design direction, and any existing schema ready.",
        ),
        PromptToolProfile(
            key="v0",
            label="v0",
            workload=PromptWorkload.AGENTIC,
            aliases=("v0", "vercel v0"),
            setup_note="Have the target product context, design direction, and any existing schema ready.",
        ),
        PromptToolProfile(
            key="bolt",
            label="Bolt",
            workload=PromptWorkload.AGENTIC,
            aliases=("bolt", "bolt.new"),
            setup_note="Have the target product context, design direction, and any existing schema ready.",
        ),
        PromptToolProfile(
            key="perplexity",
            label="Perplexity",
            workload=PromptWorkload.RESEARCH,
            aliases=("perplexity",),
            prefers_citations=True,
        ),
        PromptToolProfile(
            key="midjourney",
            label="Midjourney",
            workload=PromptWorkload.IMAGE,
            aliases=("midjourney", "mj"),
        ),
        PromptToolProfile(
            key="dalle",
            label="DALL-E / image generation",
            workload=PromptWorkload.IMAGE,
            aliases=("dall-e", "dalle", "image generation", "image model"),
        ),
        PromptToolProfile(
            key="sora",
            label="Sora / video generation",
            workload=PromptWorkload.VIDEO,
            aliases=("sora", "video generation", "video model"),
        ),
        PromptToolProfile(
            key="zapier-make",
            label="Zapier / Make",
            workload=PromptWorkload.AUTOMATION,
            aliases=("zapier", "make", "make.com"),
            setup_note="Have the source app, target app, auth, and trigger details ready.",
        ),
        PromptToolProfile(
            key="unknown",
            label="Unknown / universal AI tool",
            workload=PromptWorkload.GENERAL,
            aliases=("unknown ai", "other ai"),
        ),
    )
    _UNKNOWN_PROFILE = _TOOL_PROFILES[-1]
    _TOOL_ALIASES: tuple[tuple[str, PromptToolProfile], ...] = tuple(
        sorted(
            (
                (alias, profile)
                for profile in _TOOL_PROFILES[:-1]
                for alias in profile.aliases
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
    )
    _PROMPT_REQUEST_HINTS = (
        "write a prompt",
        "create a prompt",
        "generate a prompt",
        "draft a prompt",
        "make a prompt",
        "improve this prompt",
        "improve my prompt",
        "optimize this prompt",
        "optimize my prompt",
        "rewrite this prompt",
        "adapt this prompt",
        "convert this prompt",
        "prompt for",
        "prompt to",
        "ulepsz prompt",
        "napisz prompt",
        "stworz prompt",
        "stwórz prompt",
        "przerob prompt",
        "dopasuj prompt",
    )
    _PROMPT_DIAGNOSIS_HINTS = (
        "why is this prompt weak",
        "why is my prompt weak",
        "what is wrong with this prompt",
        "diagnose this prompt",
        "fix this prompt",
        "co jest zle z tym promptem",
        "co jest zle w tym promptcie",
    )
    _REASONING_LEAK_PATTERNS = (
        r"chain[- ]of[- ]thought",
        r"show (?:your|the) reasoning",
        r"reveal (?:your|the) reasoning",
        r"reason step by step",
        r"think step by step and show",
        r"explain your reasoning",
        r"pokaz tok rozumowania",
        r"pokaż tok rozumowania",
    )
    _CONSTRAINT_MARKERS = ("must", "should", "without", "only", "do not", "don't", "max ")
    _OUTPUT_MARKERS = ("json", "markdown", "table", "list", "bullets", "yaml", "xml")
    _SECTION_MARKERS = {
        "context": ("context:", "background:", "available context:", "kontekst:", "tlo:"),
        "output": ("output:", "format:", "return:", "wynik:", "format odpowiedzi:"),
        "constraints": ("constraints:", "requirements:", "must:", "ograniczenia:", "wymagania:"),
        "success": ("success:", "acceptance:", "definition of done:", "kryteria sukcesu:", "acceptance criteria:"),
    }

    def maybe_optimize(
        self,
        *,
        content: str,
        memory_context: str | None = None,
    ) -> PromptOptimizationResult:
        normalized = self._normalize_spaces(content)
        if not self._should_activate(normalized):
            return PromptOptimizationResult(activated=False)

        profile = self._detect_profile(normalized)
        source_prompt = self._extract_source_prompt(content)
        goal = self._extract_goal(content=content, profile=profile, source_prompt=source_prompt)
        available_context = self._extract_section_items(content, "context")
        output_format = self._extract_output_format(content)
        constraints = self._extract_constraints(content)
        success_criteria = self._extract_success_criteria(content)
        quality = self._build_quality_report(
            content=content,
            profile=profile,
            goal=goal,
            source_prompt=source_prompt,
            output_format=output_format,
            constraints=constraints,
        )
        questions = self._build_clarifying_questions(
            profile=profile,
            goal=goal,
            source_prompt=source_prompt,
            output_format=output_format,
            quality=quality,
        )
        brief = PromptOptimizationBrief(
            profile=profile,
            goal=goal,
            source_prompt=source_prompt,
            available_context=available_context,
            output_format=output_format,
            constraints=constraints,
            success_criteria=success_criteria,
            expected_workload=profile.workload,
            quality=quality,
            needs_clarification=bool(questions),
            clarifying_questions=questions,
            memory_context=memory_context.strip() if memory_context else None,
        )
        response_text = (
            self._render_clarifying_questions(questions)
            if questions
            else self._render_prompt_response(brief)
        )
        return PromptOptimizationResult(
            activated=True,
            brief=brief,
            response_text=response_text,
            metadata={
                "profile_key": profile.key,
                "profile_label": profile.label,
                "workload": profile.workload.value,
                "quality": quality.to_dict(),
                "clarifying_question_count": len(questions),
                "memory_context_used": bool(memory_context and memory_context.strip()),
            },
        )

    def should_optimize(self, content: str) -> bool:
        return self._should_activate(self._normalize_spaces(content))

    def _should_activate(self, normalized: str) -> bool:
        lowered = normalized.lower()
        if not lowered:
            return False
        if any(hint in lowered for hint in self._PROMPT_REQUEST_HINTS):
            return True
        if any(hint in lowered for hint in self._PROMPT_DIAGNOSIS_HINTS):
            return True
        for alias, _profile in self._TOOL_ALIASES:
            if alias in lowered and "prompt" in lowered:
                return True
        return False

    def _detect_profile(self, content: str) -> PromptToolProfile:
        lowered = content.lower()
        for alias, profile in self._TOOL_ALIASES:
            if alias in lowered:
                return profile
        return self._UNKNOWN_PROFILE

    def _extract_source_prompt(self, content: str) -> str | None:
        fenced = re.findall(r"```(?:[\w+-]+)?\s*(.*?)```", content, flags=re.DOTALL)
        if fenced:
            candidate = self._normalize_spaces(fenced[0])
            return candidate or None

        for marker in ("prompt:", "weak prompt:", "current prompt:", "here is the prompt:"):
            index = content.lower().find(marker)
            if index >= 0:
                candidate = self._normalize_spaces(content[index + len(marker) :])
                return candidate or None

        if ":" in content:
            head, tail = content.split(":", 1)
            if "prompt" in head.lower():
                candidate = self._normalize_spaces(tail)
                if len(candidate.split()) >= 4:
                    return candidate
        return None

    def _extract_goal(
        self,
        *,
        content: str,
        profile: PromptToolProfile,
        source_prompt: str | None,
    ) -> str | None:
        lowered = content.lower()
        patterns = (
            r"(?:write|create|generate|draft|make)\s+(?:me\s+)?(?:a\s+)?(?:better\s+)?(?:[\w.+/-]+\s+)?prompt\s+(?:to|for)\s+(.+)",
            r"(?:ulepsz|napisz|stworz|stwórz|zrob|zrób)\s+prompt\s+(?:do|dla|na)\s+(.+)",
            r"(?:adapt|convert|rewrite|optimize|improve)\s+(?:this|my)?\s*prompt\s+(?:for|to)\s+(.+)",
        )
        for pattern in patterns:
            match = re.search(pattern, lowered, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = content[match.start(1) : match.end(1)]
            return self._cleanup_goal(candidate, source_prompt=source_prompt, profile=profile)

        if source_prompt is not None:
            if any(hint in lowered for hint in self._PROMPT_DIAGNOSIS_HINTS):
                return "Improve the existing prompt and make it production-ready."
            if any(token in lowered for token in ("improve", "optimize", "rewrite", "adapt", "convert", "ulepsz")):
                return "Improve the existing prompt for the target tool."

        if "prompt" not in lowered:
            return None

        candidate = content
        for alias in profile.aliases:
            candidate = re.sub(re.escape(alias), "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(
            r"\b(write|create|generate|draft|make|improve|optimize|rewrite|adapt|convert|ulepsz|napisz|stworz|stwórz|prompt)\b",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        return self._cleanup_goal(candidate, source_prompt=source_prompt, profile=profile)

    def _cleanup_goal(
        self,
        candidate: str,
        *,
        source_prompt: str | None,
        profile: PromptToolProfile,
    ) -> str | None:
        goal = self._normalize_spaces(candidate.strip(" -:\n\t"))
        if source_prompt is not None:
            goal = goal.replace(source_prompt, "").strip()
        for alias in profile.aliases:
            if goal.lower().startswith(alias):
                goal = goal[len(alias) :].strip(" -:")
        if goal.lower().startswith("prompt"):
            goal = goal[6:].strip(" -:")
        goal = self._sanitize_prompt_seed(goal)
        if len(goal) < 6:
            return None
        return goal

    def _extract_section_items(self, content: str, section: str) -> tuple[str, ...]:
        markers = self._SECTION_MARKERS[section]
        items: list[str] = []
        for line in content.splitlines():
            lowered = line.lower().strip()
            for marker in markers:
                if lowered.startswith(marker):
                    value = self._normalize_spaces(line.split(":", 1)[1] if ":" in line else "")
                    if value:
                        items.append(value)
                    break
        return tuple(dict.fromkeys(items))

    def _extract_output_format(self, content: str) -> str | None:
        explicit = self._extract_section_items(content, "output")
        if explicit:
            return explicit[0]
        lowered = content.lower()
        for marker in self._OUTPUT_MARKERS:
            if marker in lowered:
                return marker
        if "copy-paste" in lowered or "copy paste" in lowered:
            return "single copy-paste-ready block"
        return None

    def _extract_constraints(self, content: str) -> tuple[str, ...]:
        explicit = list(self._extract_section_items(content, "constraints"))
        lowered = content.lower()
        snippets: list[str] = []
        for marker in self._CONSTRAINT_MARKERS:
            index = lowered.find(marker)
            if index >= 0:
                tail = self._normalize_spaces(content[index:])
                if tail:
                    snippets.append(tail[:180].rstrip(" .,;"))
        if "production-ready" in lowered:
            snippets.append("The prompt must drive a production-ready outcome, not a rough draft.")
        if "local-first" in lowered:
            snippets.append("Preserve local-first behavior and avoid unnecessary cloud or telemetry dependencies.")
        return tuple(dict.fromkeys(item for item in (*explicit, *snippets) if item))

    def _extract_success_criteria(self, content: str) -> tuple[str, ...]:
        explicit = self._extract_section_items(content, "success")
        if explicit:
            return explicit
        lowered = content.lower()
        criteria: list[str] = []
        if "production-ready" in lowered:
            criteria.append("The result should be ready to use without another rewrite pass.")
        if "copy-paste" in lowered:
            criteria.append("The prompt should be concise and directly copy-paste ready.")
        if "specific tool" in lowered or "optimized for" in lowered:
            criteria.append("The prompt should match the target tool instead of staying generic.")
        return tuple(dict.fromkeys(criteria))

    def _build_quality_report(
        self,
        *,
        content: str,
        profile: PromptToolProfile,
        goal: str | None,
        source_prompt: str | None,
        output_format: str | None,
        constraints: tuple[str, ...],
    ) -> PromptQualityReport:
        lowered = content.lower()
        unsupported_reasoning = any(
            re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self._REASONING_LEAK_PATTERNS
        )
        conflicting = (
            ("short" in lowered or "concise" in lowered)
            and ("detailed" in lowered or "comprehensive" in lowered or "very long" in lowered)
        ) or ("json" in lowered and "essay" in lowered)
        return PromptQualityReport(
            unclear_goal=(goal is None and source_prompt is None),
            missing_target_tool=(profile.key == self._UNKNOWN_PROFILE.key and source_prompt is None),
            vague_output_format=(output_format is None and profile.workload in {PromptWorkload.GENERAL, PromptWorkload.RESEARCH}),
            missing_constraints=(not constraints and profile.workload in {PromptWorkload.CODING, PromptWorkload.AUTOMATION, PromptWorkload.AGENTIC}),
            unnecessary_verbosity=("extremely detailed" in lowered and "simple" in lowered),
            unsupported_reasoning_request=unsupported_reasoning,
            conflicting_instructions=conflicting,
            missing_validation_steps=profile.validation_required,
        )

    def _build_clarifying_questions(
        self,
        *,
        profile: PromptToolProfile,
        goal: str | None,
        source_prompt: str | None,
        output_format: str | None,
        quality: PromptQualityReport,
    ) -> tuple[str, ...]:
        questions: list[str] = []
        if quality.unclear_goal:
            questions.append("What should the prompt make the target tool actually produce or do?")
        if quality.missing_target_tool:
            questions.append("Which AI tool should this prompt target?")
        if output_format is None and source_prompt is None and profile.workload in {
            PromptWorkload.GENERAL,
            PromptWorkload.RESEARCH,
        }:
            questions.append("What output format do you want from the target tool: bullets, paragraphs, JSON, table, or something else?")
        if quality.conflicting_instructions:
            questions.append("You asked for conflicting output behavior. Which matters more: short or detailed?")
        if goal is None and source_prompt is not None and profile.key == self._UNKNOWN_PROFILE.key:
            questions.append("If the tool is unknown, what is the exact outcome you want this improved prompt to drive?")
        return tuple(questions[:3])

    def _render_clarifying_questions(self, questions: tuple[str, ...]) -> str:
        lead = "I need a couple of details before I can write a strong prompt:"
        lines = [lead, *[f"{index}. {question}" for index, question in enumerate(questions, start=1)]]
        return "\n".join(lines)

    def _render_prompt_response(self, brief: PromptOptimizationBrief) -> str:
        prompt_body = self._build_prompt_body(brief)
        strategy_note = self._build_strategy_note(brief)
        parts = [
            f"Target tool: {brief.profile.label}",
            "```text",
            prompt_body.strip(),
            "```",
            f"Strategy: {strategy_note}",
        ]
        if brief.profile.setup_note is not None:
            parts.append(f"Setup: {brief.profile.setup_note}")
        return "\n".join(parts)

    def _build_prompt_body(self, brief: PromptOptimizationBrief) -> str:
        if brief.profile.workload is PromptWorkload.IMAGE:
            return self._build_image_prompt(brief)
        if brief.profile.workload is PromptWorkload.VIDEO:
            return self._build_video_prompt(brief)

        sections: list[str] = []
        headline = self._sanitize_prompt_seed(
            brief.goal or "Improve the provided prompt into a clean, production-ready instruction."
        )
        sections.append(headline)

        context_items = list(brief.available_context)
        if brief.source_prompt:
            context_items.append(f"Starting point: {self._sanitize_prompt_seed(brief.source_prompt)}")
        if brief.memory_context:
            context_items.append(f"Local context: {self._trim_memory_context(brief.memory_context)}")
        if context_items:
            sections.append(self._render_bullets("Context", context_items))

        instructions = self._build_instruction_block(brief)
        sections.append(self._render_bullets("Instructions", instructions))

        deliverables = self._build_deliverable_block(brief)
        sections.append(self._render_bullets("Deliverable", deliverables))

        if brief.constraints:
            sections.append(self._render_bullets("Constraints", list(brief.constraints)))

        success_items = list(brief.success_criteria) or self._build_default_success_criteria(brief)
        sections.append(self._render_bullets("Success criteria", success_items))

        if brief.profile.validation_required:
            sections.append(self._render_bullets("Validation", self._build_validation_block(brief)))

        sections.append(
            self._render_bullets(
                "Rules",
                [
                    "Do not expose internal reasoning. Give only the answer, code, or explanation that is actually useful.",
                    "Do not add filler, marketing tone, or unrelated theory.",
                ],
            )
        )
        return "\n\n".join(section for section in sections if section.strip())

    def _build_instruction_block(self, brief: PromptOptimizationBrief) -> list[str]:
        instructions = [
            "First understand the real goal, the existing constraints, and the most likely failure points.",
        ]
        if brief.profile.workload in {PromptWorkload.CODING, PromptWorkload.AGENTIC}:
            instructions.extend(
                [
                    "Inspect the relevant repository, files, or product surface before changing anything.",
                    "Use the existing architecture and keep the change set as small as possible.",
                    "If critical information is missing, ask no more than 3 concise questions before implementing.",
                ]
            )
        elif brief.profile.workload is PromptWorkload.RESEARCH:
            instructions.extend(
                [
                    "Separate facts from inference.",
                    "Prefer crisp synthesis over long background explanation.",
                ]
            )
            if brief.profile.prefers_citations:
                instructions.append("Include sources or citations for claims that depend on external facts.")
        elif brief.profile.workload is PromptWorkload.AUTOMATION:
            instructions.extend(
                [
                    "Design the workflow around the real trigger, inputs, actions, outputs, and failure cases.",
                    "Keep the automation idempotent where possible and call out approval points for risky actions.",
                ]
            )
        else:
            instructions.append("Make the prompt concrete, specific, and easy to execute without extra interpretation.")
        return instructions

    def _build_deliverable_block(self, brief: PromptOptimizationBrief) -> list[str]:
        deliverables: list[str] = []
        if brief.profile.workload is PromptWorkload.CODING:
            deliverables.extend(
                [
                    "Implement the requested change using the existing patterns in the codebase.",
                    "List the files changed and summarize why each change was needed.",
                    "Report validation steps and any remaining risks or follow-up work.",
                ]
            )
        elif brief.profile.workload is PromptWorkload.AGENTIC:
            deliverables.extend(
                [
                    "Produce the requested implementation or UI output directly.",
                    "Keep the UX clean, decisive, and production-oriented rather than experimental.",
                    "State assumptions only when they materially affect the result.",
                ]
            )
        elif brief.profile.workload is PromptWorkload.RESEARCH:
            deliverables.extend(
                [
                    "Return a concise answer first, then supporting evidence.",
                    "Highlight uncertainty instead of guessing.",
                ]
            )
        elif brief.profile.workload is PromptWorkload.AUTOMATION:
            deliverables.extend(
                [
                    "Return the trigger, apps, key mappings, filters, failure handling, and test steps.",
                    "Flag anything that needs user confirmation or app-level credentials.",
                ]
            )
        else:
            target_format = brief.output_format or self._default_output_format(brief.profile)
            deliverables.append(f"Return the result as {target_format}.")
            deliverables.append("Keep the answer direct and ready to use.")
        return deliverables

    def _build_default_success_criteria(self, brief: PromptOptimizationBrief) -> list[str]:
        criteria = [
            "The prompt is specific enough that the target tool can act on it without a second rewrite.",
            "The prompt reflects the real goal, not just generic prompt-engineering language.",
        ]
        if brief.profile.validation_required:
            criteria.append("The prompt includes explicit validation or self-check steps before completion.")
        if brief.profile.workload is PromptWorkload.IMAGE:
            criteria.append("The prompt gives a concrete visual direction instead of vague adjectives.")
        if brief.profile.workload is PromptWorkload.VIDEO:
            criteria.append("The prompt specifies the scene, camera behavior, motion, and output feel.")
        return criteria

    def _build_validation_block(self, brief: PromptOptimizationBrief) -> list[str]:
        checks = [
            "Run the existing relevant tests, lint, typecheck, and build commands if they exist.",
            "If the repo has no standard command for one of those checks, say that clearly and run the closest relevant validation instead.",
            "Do not close the task with a partial patch or an unverified breaking change.",
        ]
        if brief.goal and "atnos" in brief.goal.lower():
            checks.append("Preserve Atnos local-first behavior and avoid introducing unnecessary cloud or telemetry dependencies.")
        return checks

    def _build_image_prompt(self, brief: PromptOptimizationBrief) -> str:
        base = brief.source_prompt or brief.goal or "Create a polished image prompt."
        segments = [
            self._sanitize_prompt_seed(base),
            "clear focal subject",
            "strong composition",
            "intentional lighting",
            "high detail",
            "clean background separation",
        ]
        if brief.constraints:
            segments.extend(brief.constraints[:3])
        if brief.output_format:
            segments.append(f"output preference: {brief.output_format}")
        segments.append("avoid text artifacts, watermark, and muddy composition")
        return ", ".join(segment for segment in segments if segment)

    def _build_video_prompt(self, brief: PromptOptimizationBrief) -> str:
        base = brief.source_prompt or brief.goal or "Create a polished video prompt."
        lines = [
            self._sanitize_prompt_seed(base),
            "Scene: make the setting and subject explicit.",
            "Motion: define what moves, how fast, and with what rhythm.",
            "Camera: specify framing, lens feel, and camera movement.",
            "Lighting and mood: state the palette, contrast, and atmosphere.",
            "Output: cinematic, coherent, artifact-free motion.",
            "Avoid: text overlays, broken anatomy, flicker, and chaotic camera behavior.",
        ]
        if brief.constraints:
            lines.append(f"Constraints: {'; '.join(brief.constraints[:3])}")
        return "\n".join(lines)

    def _build_strategy_note(self, brief: PromptOptimizationBrief) -> str:
        notes: list[str] = []
        if brief.source_prompt:
            notes.append("I tightened the existing prompt instead of rewriting the goal from scratch.")
        else:
            notes.append("I turned the rough request into a tool-specific prompt with a clearer outcome.")
        if brief.profile.validation_required:
            notes.append("I added validation steps because coding agents fail hard when the prompt stops at implementation.")
        if brief.quality.unsupported_reasoning_request:
            notes.append("I removed the request to expose internal reasoning and replaced it with explicit output checks.")
        if brief.profile is self._UNKNOWN_PROFILE:
            notes.append("I kept the structure universal so it still works even without a custom tool profile.")
        if brief.memory_context:
            notes.append("I folded in relevant local context from Atnos memory.")
        return " ".join(notes)

    @staticmethod
    def _render_bullets(title: str, items: list[str]) -> str:
        rendered = "\n".join(f"- {item}" for item in items if item)
        return f"{title}:\n{rendered}"

    @staticmethod
    def _normalize_spaces(value: str) -> str:
        return " ".join((value or "").strip().split())

    @staticmethod
    def _sanitize_prompt_seed(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value.strip())
        sanitized_patterns = (
            r"(?:show|reveal)\s+(?:(?:your|the)\s+)?chain[- ]of[- ]thought.*",
            r"(?:show|reveal|explain)\s+(?:(?:your|the|its)\s+)?reasoning(?:\s+step by step)?.*",
            r"think step by step(?: and show it)?",
            r"take a deep breath",
        )
        for pattern in sanitized_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip(" ,.;") or "Use the provided goal and context."

    @staticmethod
    def _trim_memory_context(memory_context: str, limit: int = 420) -> str:
        trimmed = PromptOptimizerService._normalize_spaces(memory_context)
        if len(trimmed) <= limit:
            return trimmed
        return f"{trimmed[:limit].rstrip()}..."

    @staticmethod
    def _default_output_format(profile: PromptToolProfile) -> str:
        if profile.workload is PromptWorkload.RESEARCH:
            return "a concise synthesis with evidence"
        if profile.workload is PromptWorkload.AUTOMATION:
            return "a step-by-step automation plan"
        return "a concise, copy-paste-ready answer"
