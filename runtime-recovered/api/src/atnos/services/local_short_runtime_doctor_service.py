from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import json
from pathlib import Path
from typing import Protocol

from atnos.core.config import Settings
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeSnapshot
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService


class LocalShortRuntimeProbe(Protocol):
    def snapshot(self) -> LocalShortRuntimeSnapshot:
        ...


@dataclass(frozen=True, slots=True)
class LocalShortRuntimeDoctorAction:
    priority: str
    component: str
    title: str
    details: str
    commands: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "priority": self.priority,
            "component": self.component,
            "title": self.title,
            "details": self.details,
            "commands": list(self.commands),
        }


@dataclass(frozen=True, slots=True)
class LocalShortRuntimeDoctorReport:
    generated_at: str
    status: str
    ready: bool
    reason: str
    missing_components: tuple[str, ...]
    components: tuple[dict[str, object], ...]
    recommended_actions: tuple[LocalShortRuntimeDoctorAction, ...]
    env_snippet: str
    production_blocking: tuple[str, ...] = ()
    production_readiness: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "missing_components": list(self.missing_components),
            "production_blocking": list(self.production_blocking),
            "components": list(self.components),
            "recommended_actions": [item.to_dict() for item in self.recommended_actions],
            "env_snippet": self.env_snippet,
            "production_readiness": self.production_readiness,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Local Short Runtime Doctor",
            "",
            f"- generated_at: `{self.generated_at}`",
            f"- status: `{self.status}`",
            f"- ready: `{self.ready}`",
            f"- reason: {self.reason}",
            "",
            "## Components",
            "| component | ready | path | reason |",
            "|---|---:|---|---|",
        ]
        for component in self.components:
            lines.append(
                "| {label} | {ready} | `{path}` | {reason} |".format(
                    label=component["label"],
                    ready=component["ready"],
                    path=component.get("path") or "not configured",
                    reason=component["reason"],
                )
            )
        lines.extend(["", "## Recommended Actions"])
        if self.recommended_actions:
            for action in self.recommended_actions:
                lines.extend(
                    [
                        f"### {action.priority} - {action.title}",
                        "",
                        f"- component: `{action.component}`",
                        f"- details: {action.details}",
                    ]
                )
                if action.commands:
                    lines.extend(["", "```powershell", *action.commands, "```"])
                lines.append("")
        else:
            lines.append("Runtime is ready. No action required.")
        if self.production_readiness:
            lines.extend(
                [
                    "",
                    "## Production Readiness",
                    "",
                    f"- status: `{self.production_readiness.get('status')}`",
                    f"- ready: `{self.production_readiness.get('ready')}`",
                    f"- blocking: `{', '.join(self.production_blocking) if self.production_blocking else 'none'}`",
                ]
            )
            next_actions = self.production_readiness.get("next_actions")
            if isinstance(next_actions, list) and next_actions:
                lines.extend(["", "### Production Next Actions"])
                for action in next_actions[:8]:
                    lines.append(f"- {action}")
        lines.extend(["", "## Suggested `.env` Snippet", "", "```env", self.env_snippet, "```", ""])
        return "\n".join(lines)


class LocalShortRuntimeDoctorService:
    def __init__(
        self,
        settings: Settings,
        *,
        probe: LocalShortRuntimeProbe | None = None,
        include_production_readiness: bool = False,
    ) -> None:
        self.settings = settings
        self.probe = probe or LocalShortRuntimeProbeService(settings)
        self.include_production_readiness = include_production_readiness

    def build_report(self) -> LocalShortRuntimeDoctorReport:
        snapshot = self.probe.snapshot()
        missing = tuple(component.key for component in snapshot.components if not component.ready)
        production_readiness = self._build_production_readiness() if self.include_production_readiness else None
        production_blocking = (
            tuple(str(item) for item in production_readiness.get("blocking", ()))
            if isinstance(production_readiness, dict)
            else ()
        )
        if not snapshot.enabled:
            status = "disabled"
        elif snapshot.ready and not production_blocking:
            status = "ready"
        else:
            status = "blocked"
        reason = snapshot.reason
        if production_blocking:
            reason = f"{reason} Production blockers: {', '.join(production_blocking)}."
        return LocalShortRuntimeDoctorReport(
            generated_at=datetime.now(UTC).isoformat(),
            status=status,
            ready=snapshot.ready and not production_blocking,
            reason=reason,
            missing_components=missing,
            production_blocking=production_blocking,
            components=tuple(component.to_dict() for component in snapshot.components),
            recommended_actions=self._build_actions(missing, production_readiness=production_readiness),
            env_snippet=self._build_env_snippet(),
            production_readiness=production_readiness,
        )

    def write_report(self, output_path: Path | None = None) -> Path:
        report = self.build_report()
        path = output_path or self._default_report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.casefold() == ".json":
            path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text(report.to_markdown(), encoding="utf-8")
        return path

    def _default_report_path(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return self.settings.data_dir / "reports" / f"local_short_runtime_doctor_{timestamp}.md"

    def _build_actions(
        self,
        missing: tuple[str, ...],
        *,
        production_readiness: dict[str, object] | None = None,
    ) -> tuple[LocalShortRuntimeDoctorAction, ...]:
        actions: list[LocalShortRuntimeDoctorAction] = []
        if "ollama" in missing:
            actions.append(
                LocalShortRuntimeDoctorAction(
                    priority="P0",
                    component="ollama",
                    title="Start Ollama and pull the local synthesis model",
                    details="Short scripts are generated locally. Without Ollama the pipeline cannot produce a script.",
                    commands=(
                        "ollama serve",
                        "ollama pull qwen3:14b",
                    ),
                )
            )
        if "piper" in missing:
            actions.append(
                LocalShortRuntimeDoctorAction(
                    priority="P0",
                    component="piper",
                    title="Configure Piper Polish TTS",
                    details="Piper must point to the executable, Polish ONNX voice model and matching JSON config.",
                    commands=(
                        "python -m pip install piper-tts",
                        "# Download a Polish Piper voice, then set PIPER_PL_MODEL_PATH and PIPER_PL_CONFIG_PATH.",
                    ),
                )
            )
        if "whisper_cpp" in missing:
            actions.append(
                LocalShortRuntimeDoctorAction(
                    priority="P0",
                    component="whisper_cpp",
                    title="Configure whisper.cpp subtitles",
                    details="whisper.cpp must point to whisper-cli.exe and a local ggml model file.",
                    commands=(
                        "# Build or install whisper.cpp, then set WHISPER_CPP_EXE.",
                        "# Download a ggml model, then set WHISPER_MODEL_PATH.",
                    ),
                )
            )
        if "ffmpeg" in missing:
            actions.append(
                LocalShortRuntimeDoctorAction(
                    priority="P0",
                    component="ffmpeg",
                    title="Install FFmpeg renderer",
                    details="The final MP4 render depends on FFmpeg.",
                    commands=("winget install Gyan.FFmpeg",),
                )
            )
        if production_readiness and not production_readiness.get("ready"):
            next_actions = production_readiness.get("next_actions")
            details = "\n".join(str(item) for item in next_actions[:8]) if isinstance(next_actions, list) else ""
            actions.append(
                LocalShortRuntimeDoctorAction(
                    priority="P0",
                    component="production_readiness",
                    title="Fix full Shorts production readiness blockers",
                    details=details or "Run production readiness and fix blocking items before real short production.",
                    commands=("python -m pytest tests\\test_production_readiness.py -q",),
                )
            )
        return tuple(actions)

    def _build_production_readiness(self) -> dict[str, object] | None:
        def _run_check(loop: asyncio.AbstractEventLoop | None = None) -> dict[str, object]:
            from atnos.db.session import DatabaseManager
            from atnos.production.readiness import ShortProductionReadinessService

            database = DatabaseManager(self.settings)
            database.initialize()
            try:
                with database.session_scope() as session:
                    coroutine = ShortProductionReadinessService(self.settings, session=session).check(include_live_checks=True)
                    if loop is not None:
                        return loop.run_until_complete(coroutine)
                    return asyncio.run(coroutine)
            finally:
                database.dispose()

        try:
            return _run_check()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return _run_check(loop)
            finally:
                loop.close()
        except Exception as exc:
            return {
                "status": "error",
                "ready": False,
                "blocking": ["production_readiness"],
                "next_actions": [f"Production readiness check failed: {type(exc).__name__}: {exc}"],
            }

    def _build_env_snippet(self) -> str:
        piper_exe = self.settings.piper_exe or r"E:\Atnos\App\runtime-recovered\api\.venv\Scripts\piper.exe"
        whisper_exe = self.settings.whisper_cpp_exe or r"E:\Atnos\Tools\whispercpp\bin\Release\whisper-cli.exe"
        return "\n".join(
            (
                "ATNOS_LOCAL_SHORTS_ENABLED=true",
                f"SHORTS_OUTPUT_DIR={self.settings.shorts_output_dir}",
                f"SHORTS_MIN_QUALITY_SCORE={self.settings.shorts_min_quality_score}",
                f"OLLAMA_BASE_URL={self.settings.ollama_base_url}",
                f"OLLAMA_SYNTHESIS_MODEL={self.settings.ollama_synthesis_model or 'qwen3:14b'}",
                f"PIPER_EXE={piper_exe}",
                r"PIPER_PL_MODEL_PATH=E:\Atnos\App\data\models\piper\pl_PL-darkman-medium.onnx",
                r"PIPER_PL_CONFIG_PATH=E:\Atnos\App\data\models\piper\pl_PL-darkman-medium.onnx.json",
                f"WHISPER_CPP_EXE={whisper_exe}",
                r"WHISPER_MODEL_PATH=E:\Atnos\App\data\models\whisper\ggml-base.bin",
                f"WHISPER_LANGUAGE={self.settings.whisper_language or 'pl'}",
            )
        )
