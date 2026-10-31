from __future__ import annotations

"""Deterministyczne, ograniczone weryfikacje projektu Atnos.

Ten serwis celowo nie przyjmuje komendy od uzytkownika. Zamiast tego udostepnia
male, jawnie zdefiniowane recepty dla kodu Atnosa. Dzieki temu Project Command
Chat moze uruchomic test lub build jako dowod, bez stawania sie nieograniczonym
wykonawca polecen systemowych.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter
from typing import Callable, Literal

from atnos.ai_workers.redaction import redact_text
from atnos.core.config import Settings


VerificationRecipe = Literal[
    "backend_project_command_tests",
    "frontend_project_command_tests",
    "frontend_build",
]


@dataclass(frozen=True, slots=True)
class ProjectVerificationResult:
    """Wynik jednej dozwolonej recepty weryfikacyjnej."""

    recipe: VerificationRecipe
    status: Literal["verified", "failed", "blocked_technical"]
    command: tuple[str, ...]
    cwd: str
    exit_code: int | None
    duration_ms: int
    summary: str
    output: str
    next_step: str

    def to_dict(self) -> dict[str, object]:
        return {
            "recipe": self.recipe,
            "status": self.status,
            "command": list(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "output": self.output,
            "next_step": self.next_step,
        }


CommandRunner = Callable[[list[str], Path, int, dict[str, str]], subprocess.CompletedProcess[str]]


class ProjectVerificationService:
    """Uruchamia tylko bezpieczne i wersjonowane recepty kontroli jakosci."""

    def __init__(self, settings: Settings, *, runner: CommandRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or self._run_command

    def run(self, recipe: VerificationRecipe) -> ProjectVerificationResult:
        started = perf_counter()
        try:
            command, cwd = self._resolve_recipe(recipe)
        except FileNotFoundError as exc:
            return ProjectVerificationResult(
                recipe=recipe,
                status="blocked_technical",
                command=(),
                cwd=str(self.settings.app_root),
                exit_code=None,
                duration_ms=_elapsed_ms(started),
                summary=f"Brakuje lokalnego zasobu do uruchomienia recepty: {exc}.",
                output="",
                next_step="Zainstaluj wymagane lokalne narzedzie lub popraw sciezke projektu, potem uruchom te sama recepta ponownie.",
            )
        try:
            completed = self.runner(
                list(command),
                cwd,
                self._timeout_for(recipe),
                self._command_environment(),
            )
        except FileNotFoundError as exc:
            return self._blocked(recipe, command, cwd, started, f"Nie znaleziono narzedzia: {exc.filename or exc}.")
        except subprocess.TimeoutExpired:
            return ProjectVerificationResult(
                recipe=recipe,
                status="failed",
                command=command,
                cwd=str(cwd),
                exit_code=124,
                duration_ms=_elapsed_ms(started),
                summary="Recepta weryfikacyjna przekroczyla limit czasu.",
                output="",
                next_step="Sprawdz log procesu i uruchom tylko test docelowy po usunieciu blokady.",
            )
        except OSError as exc:
            return self._blocked(recipe, command, cwd, started, f"Nie mozna uruchomic recepty: {exc}.")

        output = _sanitize_output(f"{completed.stdout or ''}\n{completed.stderr or ''}")
        passed = completed.returncode == 0
        return ProjectVerificationResult(
            recipe=recipe,
            status="verified" if passed else "failed",
            command=command,
            cwd=str(cwd),
            exit_code=int(completed.returncode),
            duration_ms=_elapsed_ms(started),
            summary="Weryfikacja zakonczona powodzeniem." if passed else "Weryfikacja wykryla blad.",
            output=output,
            next_step=(
                "Dowod testu jest aktualny; dopiero teraz mozna oznaczyc powiazana zmiane jako zweryfikowana."
                if passed
                else "Nie oznaczaj zmiany jako ukonczonej. Przejrzyj pierwsze bledy i przygotuj najmniejsza poprawke."
            ),
        )

    def _resolve_recipe(self, recipe: VerificationRecipe) -> tuple[tuple[str, ...], Path]:
        if recipe == "backend_project_command_tests":
            api_root = self._api_root()
            return (sys.executable, "-m", "pytest", "tests/test_project_command_api.py", "-q"), api_root
        frontend_root = self._frontend_root()
        pnpm = self._pnpm_path()
        if recipe == "frontend_project_command_tests":
            return (
                pnpm,
                "exec",
                "vitest",
                "run",
                "src/components/ProjectCommandPanel.test.tsx",
                "src/routes/Chat.test.tsx",
                "src/routes/ChatSlashCommands.test.tsx",
            ), frontend_root
        if recipe == "frontend_build":
            return (pnpm, "run", "build"), frontend_root
        raise ValueError(f"Nieznana recepta weryfikacyjna: {recipe}.")

    def _api_root(self) -> Path:
        for candidate in (
            self.settings.app_root,
            self.settings.app_root / "runtime-recovered" / "api",
            Path(__file__).resolve().parents[3],
        ):
            if (candidate / "src" / "atnos").is_dir() and (candidate / "tests").is_dir():
                return candidate.resolve()
        raise FileNotFoundError("Nie znaleziono katalogu API Atnosa z testami.")

    def _frontend_root(self) -> Path:
        for candidate in (
            self.settings.app_root / "frontend",
            self.settings.app_root.parent / "frontend",
            self.settings.app_root.parent.parent / "frontend",
            Path(__file__).resolve().parents[5] / "frontend",
        ):
            if (candidate / "package.json").is_file():
                return candidate.resolve()
        raise FileNotFoundError("Nie znaleziono katalogu frontendu Atnosa.")

    @staticmethod
    def _pnpm_path() -> str:
        found = shutil.which("pnpm") or shutil.which("pnpm.cmd")
        if found:
            return found
        bundled = (
            Path.home()
            / ".cache"
            / "codex-runtimes"
            / "codex-primary-runtime"
            / "dependencies"
            / "bin"
            / "fallback"
            / "pnpm.cmd"
        )
        if bundled.is_file():
            return str(bundled)
        raise FileNotFoundError("pnpm")

    @staticmethod
    def _command_environment() -> dict[str, str]:
        """Zapewnia node.exe dla pnpm.cmd uruchamianego przez backend Windows."""
        environment = dict(os.environ)
        node = shutil.which("node")
        if node:
            node_dir = str(Path(node).resolve().parent)
        else:
            bundled = (
                Path.home()
                / ".cache"
                / "codex-runtimes"
                / "codex-primary-runtime"
                / "dependencies"
                / "node"
                / "bin"
            )
            node_dir = str(bundled) if (bundled / "node.exe").is_file() else ""
        if node_dir:
            existing = environment.get("PATH", "")
            paths = existing.split(os.pathsep) if existing else []
            if node_dir not in paths:
                environment["PATH"] = node_dir + (os.pathsep + existing if existing else "")
        return environment

    @staticmethod
    def _timeout_for(recipe: VerificationRecipe) -> int:
        return 600 if recipe == "frontend_build" else 300

    @staticmethod
    def _run_command(
        command: list[str],
        cwd: Path,
        timeout_s: int,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            shell=False,
            env=environment,
        )

    @staticmethod
    def _blocked(
        recipe: VerificationRecipe,
        command: tuple[str, ...],
        cwd: Path,
        started: float,
        detail: str,
    ) -> ProjectVerificationResult:
        return ProjectVerificationResult(
            recipe=recipe,
            status="blocked_technical",
            command=command,
            cwd=str(cwd),
            exit_code=None,
            duration_ms=_elapsed_ms(started),
            summary=detail,
            output="",
            next_step="Napraw brakujace lokalne narzedzie lub sciezke, potem uruchom te sama recepta ponownie.",
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _sanitize_output(value: str) -> str:
    normalized = redact_text(value).strip()
    if len(normalized) <= 4_000:
        return normalized
    return normalized[:3_997].rstrip() + "..."
