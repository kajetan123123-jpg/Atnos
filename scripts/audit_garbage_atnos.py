from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DATE = datetime.now().strftime("%Y%m%d")
REPORT_PATH = ROOT / f"audit_garbage_{DATE}.md"
ARTIFACT_DIR = ROOT / f"audit_garbage_{DATE}_artifacts"

IGNORE_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
}

RUNTIME_DIR_NAMES = {
    "data",
    "runtime",
    "logs",
    "outputs",
    "browser-profile",
    "browser-profiles",
    "browser_downloads",
    "browser-downloads",
    "playwright-browsers",
}

CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml"}
MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".wav", ".mp3", ".m4a", ".opus", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


def is_ignored(path: Path, *, include_runtime: bool = False) -> bool:
    parts = set(path.relative_to(ROOT).parts if path != ROOT else [])
    ignored = IGNORE_DIR_NAMES if include_runtime else IGNORE_DIR_NAMES | RUNTIME_DIR_NAMES
    return bool(parts & ignored)


def iter_files(*, include_runtime: bool = False) -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if path.is_file() and not is_ignored(path, include_runtime=include_runtime):
            yield path


def run_command(name: str, command: list[str], timeout_s: int = 180) -> CommandResult:
    started = datetime.now()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr += f"\nTIMEOUT after {timeout_s}s"
        returncode = 124
    duration = (datetime.now() - started).total_seconds()
    return CommandResult(name, command, returncode, stdout, stderr, duration)


def write_raw_result(result: CommandResult) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        f"$ {' '.join(result.command)}",
        f"returncode={result.returncode}",
        f"duration_s={result.duration_s:.2f}",
        "\n--- stdout ---\n",
        result.stdout,
        "\n--- stderr ---\n",
        result.stderr,
    ]
    (ARTIFACT_DIR / f"{result.name}.txt").write_text("\n".join(payload), encoding="utf-8")


def scan_todo_debt() -> list[str]:
    pattern = re.compile(r"\b(TODO|FIXME|HACK|XXX|NOQA|TEMP|WORKAROUND)\b", re.IGNORECASE)
    findings: list[str] = []
    for file in iter_files():
        if file.suffix != ".py":
            continue
        try:
            for index, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if pattern.search(line):
                    findings.append(f"{rel(file)}:{index}: {line.strip()}")
        except OSError:
            continue
    return findings


def scan_commented_code_blocks() -> list[dict[str, object]]:
    codeish = re.compile(r"^\s*#\s*(def |class |import |from |return\b|if |for |while |try:|except |=|.+\(.+\))")
    findings: list[dict[str, object]] = []
    for file in iter_files():
        if file.suffix != ".py":
            continue
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        block: list[tuple[int, str]] = []
        for index, line in enumerate(lines, start=1):
            if codeish.search(line):
                block.append((index, line.strip()))
                continue
            if len(block) >= 4:
                findings.append({"file": rel(file), "start": block[0][0], "end": block[-1][0], "lines": [item[1] for item in block[:8]]})
            block = []
        if len(block) >= 4:
            findings.append({"file": rel(file), "start": block[0][0], "end": block[-1][0], "lines": [item[1] for item in block[:8]]})
    return findings


def scan_orphaned_python_files() -> list[str]:
    py_files = [file for file in iter_files() if file.suffix == ".py"]
    module_by_file: dict[Path, str] = {}
    for file in py_files:
        module_by_file[file] = module_name_for(file)
    inbound = Counter()
    local_modules = set(module_by_file.values())
    for file in py_files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for item in imported:
                for module in local_modules:
                    if item == module or item.startswith(module + ".") or module.startswith(item + "."):
                        inbound[module] += 1
    candidates: list[str] = []
    for file, module in module_by_file.items():
        lowered = file.name.lower()
        rel_path = rel(file)
        if inbound[module] > 0:
            continue
        if lowered in {"__init__.py", "main.py", "app.py", "agent.py", "conftest.py"}:
            continue
        if any(part in {"tests", "scripts", "examples", "alembic"} for part in file.relative_to(ROOT).parts):
            continue
        candidates.append(rel_path)
    return sorted(candidates)


def scan_config_candidates() -> list[str]:
    code_text_cache: dict[Path, str] = {}
    code_files = [file for file in iter_files() if file.suffix in CODE_EXTENSIONS]
    for file in code_files:
        try:
            code_text_cache[file] = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            code_text_cache[file] = ""
    candidates: list[str] = []
    for file in iter_files(include_runtime=True):
        if file.suffix.casefold() not in CONFIG_EXTENSIONS and not file.name.startswith(".env."):
            continue
        if any(part in {".venv", "node_modules", "dist"} for part in file.relative_to(ROOT).parts):
            continue
        name = file.name
        stem = file.stem
        referenced = any(name in text or stem in text for text in code_text_cache.values())
        if not referenced:
            candidates.append(rel(file))
    return sorted(candidates)


def scan_dependency_bloat() -> list[str]:
    pyproject = ROOT / "runtime-recovered" / "api" / "pyproject.toml"
    if not pyproject.exists():
        return []
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    deps = re.findall(r'"([A-Za-z0-9_.-]+)[<>=!~]', text)
    imports: set[str] = set()
    for file in iter_files():
        if file.suffix != ".py":
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    aliases = {
        "Pillow": "PIL",
        "python-dotenv": "dotenv",
        "python-multipart": "multipart",
        "python-docx": "docx",
        "python-pptx": "pptx",
        "PyYAML": "yaml",
        "pydantic-settings": "pydantic_settings",
        "google-api-python-client": "googleapiclient",
        "google-auth": "google",
        "google-auth-oauthlib": "google_auth_oauthlib",
        "huggingface_hub": "huggingface_hub",
        "edge-tts": "edge_tts",
        "imageio-ffmpeg": "imageio_ffmpeg",
    }
    unused: list[str] = []
    for dep in sorted(set(deps), key=str.casefold):
        normalized = aliases.get(dep, dep.replace("-", "_").split("[")[0])
        if normalized not in imports:
            unused.append(dep)
    return unused


def scan_artifacts() -> dict[str, list[str]]:
    pyc = []
    pycache = []
    logs = []
    tmp_bak = []
    oversized = []
    media = []
    empty_dirs = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            if path.name == "__pycache__":
                pycache.append(rel(path))
            if path != ROOT and not any(path.iterdir()) and ".git" not in path.parts:
                empty_dirs.append(rel(path))
            continue
        suffix = path.suffix.casefold()
        if suffix in {".pyc", ".pyo"}:
            pyc.append(rel(path))
        if suffix == ".log":
            logs.append(rel(path))
        if suffix in {".tmp", ".bak"}:
            tmp_bak.append(rel(path))
        if suffix in MEDIA_EXTENSIONS:
            media.append(rel(path))
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if suffix not in CODE_EXTENSIONS and size > 10 * 1024 * 1024:
            oversized.append(f"{rel(path)} ({size / (1024 * 1024):.1f} MB)")
    return {
        "pycache_dirs": sorted(pycache),
        "pyc_pyo_files": sorted(pyc),
        "log_files": sorted(logs),
        "tmp_bak_files": sorted(tmp_bak),
        "empty_dirs": sorted(empty_dirs),
        "oversized_non_code": sorted(oversized),
        "media_files": sorted(media),
    }


def safe_cleanup(artifacts: dict[str, list[str]]) -> dict[str, list[str]]:
    deleted: dict[str, list[str]] = defaultdict(list)
    for item in artifacts["pycache_dirs"]:
        path = ROOT / item
        if path.is_dir() and path.name == "__pycache__":
            shutil.rmtree(path, ignore_errors=True)
            deleted["pycache_dirs"].append(item)
    for item in artifacts["pyc_pyo_files"] + artifacts["tmp_bak_files"]:
        path = ROOT / item
        if path.is_file() and path.suffix.casefold() in {".pyc", ".pyo", ".tmp", ".bak"}:
            try:
                path.unlink()
                deleted["files"].append(item)
            except OSError:
                pass
    for path in sorted((p for p in ROOT.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if path == ROOT or ".git" in path.parts:
            continue
        if is_ignored(path, include_runtime=True) and path.name not in {".pytest_cache", "__pycache__"}:
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
                deleted["empty_dirs"].append(rel(path))
        except OSError:
            pass
    return {key: sorted(value) for key, value in deleted.items()}


def autoflake_safe_cleanup() -> CommandResult:
    # Conservative: remove unused imports only in active source/script files, skipping __init__.py
    # and tests where imports may be fixtures or monkeypatch targets.
    files = [
        file
        for file in iter_files()
        if file.suffix == ".py"
        and file.name != "__init__.py"
        and "tests" not in file.relative_to(ROOT).parts
        and "alembic" not in file.relative_to(ROOT).parts
    ]
    if not files:
        return CommandResult("autoflake_cleanup", [], 0, "No candidate files", "", 0.0)
    started = datetime.now()
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    returncode = 0
    chunk_size = 40
    for offset in range(0, len(files), chunk_size):
        chunk = files[offset : offset + chunk_size]
        command = [
            sys.executable,
            "-m",
            "autoflake",
            "--remove-all-unused-imports",
            "--in-place",
            *[str(file) for file in chunk],
        ]
        result = run_command(f"autoflake_cleanup_{offset // chunk_size + 1}", command, timeout_s=120)
        stdout_parts.append(f"## chunk {offset // chunk_size + 1}\n{result.stdout}")
        stderr_parts.append(f"## chunk {offset // chunk_size + 1}\n{result.stderr}")
        if result.returncode != 0:
            returncode = result.returncode
    duration = (datetime.now() - started).total_seconds()
    return CommandResult(
        "autoflake_cleanup",
        [sys.executable, "-m", "autoflake", "--remove-all-unused-imports", "--in-place", "<chunked-files>"],
        returncode,
        "\n".join(stdout_parts),
        "\n".join(stderr_parts),
        duration,
    )


def module_name_for(file: Path) -> str:
    parts = list(file.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    for marker in ("src",):
        if marker in parts:
            parts = parts[parts.index(marker) + 1 :]
            break
    return ".".join(part for part in parts if part)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def truncate_lines(lines: list[str], limit: int = 120) -> str:
    if not lines:
        return "_None._"
    shown = lines[:limit]
    suffix = "" if len(lines) <= limit else f"\n\n... truncated {len(lines) - limit} more lines. See artifacts for raw output."
    return "\n".join(f"- `{line}`" for line in shown) + suffix


def command_summary(result: CommandResult, limit: int = 80) -> str:
    combined = (result.stdout + "\n" + result.stderr).strip().splitlines()
    body = "\n".join(combined[:limit]) if combined else "_No output._"
    if len(combined) > limit:
        body += f"\n\n... truncated {len(combined) - limit} lines. See `audit_garbage_{DATE}_artifacts/{result.name}.txt`."
    return f"Return code: `{result.returncode}` | Duration: `{result.duration_s:.1f}s`\n\n```text\n{body}\n```"


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    exclude = "*/.venv/*,*/node_modules/*,*/dist/*,*/build/*,*/data/*,*/runtime/*,*/logs/*,*/outputs/*"
    commands = [
        run_command("vulture", [sys.executable, "-m", "vulture", ".", "--min-confidence", "80", "--exclude", exclude], timeout_s=240),
        run_command(
            "autoflake_check",
            [
                sys.executable,
                "-m",
                "autoflake",
                "--recursive",
                "--remove-all-unused-imports",
                "--remove-unused-variables",
                "--check",
                ".",
                "--exclude",
                ".venv,node_modules,dist,build,data,runtime,logs,outputs",
            ],
            timeout_s=240,
        ),
        run_command(
            "pylint_duplicate_code",
            [
                sys.executable,
                "-m",
                "pylint",
                "--disable=all",
                "--enable=duplicate-code",
                "--ignore=.venv,node_modules,dist,build,data,runtime,logs,outputs",
                ".",
            ],
            timeout_s=360,
        ),
    ]
    for result in commands:
        write_raw_result(result)

    todo = scan_todo_debt()
    commented = scan_commented_code_blocks()
    orphaned = scan_orphaned_python_files()
    config_candidates = scan_config_candidates()
    dep_bloat = scan_dependency_bloat()
    artifacts = scan_artifacts()

    cleanup = safe_cleanup(artifacts)
    cleanup_result = autoflake_safe_cleanup()
    write_raw_result(cleanup_result)
    artifacts_after = scan_artifacts()

    metadata = {
        "root": str(ROOT),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "artifact_dir": str(ARTIFACT_DIR),
        "cleanup": cleanup,
        "artifacts_before_counts": {key: len(value) for key, value in artifacts.items()},
        "artifacts_after_counts": {key: len(value) for key, value in artifacts_after.items()},
    }
    (ARTIFACT_DIR / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / "todo_debt.txt").write_text("\n".join(todo), encoding="utf-8")
    (ARTIFACT_DIR / "commented_code_blocks.json").write_text(json.dumps(commented, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / "orphaned_python_candidates.txt").write_text("\n".join(orphaned), encoding="utf-8")
    (ARTIFACT_DIR / "config_unreferenced_candidates.txt").write_text("\n".join(config_candidates), encoding="utf-8")
    (ARTIFACT_DIR / "dependency_bloat_candidates.txt").write_text("\n".join(dep_bloat), encoding="utf-8")
    (ARTIFACT_DIR / "artifacts_before.json").write_text(json.dumps(artifacts, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / "artifacts_after.json").write_text(json.dumps(artifacts_after, ensure_ascii=False, indent=2), encoding="utf-8")

    report = [
        f"# Atnos Garbage Audit - {DATE}",
        "",
        f"Generated: `{metadata['generated_at']}`",
        f"Root: `{ROOT}`",
        f"Raw artifacts: `{ARTIFACT_DIR}`",
        "",
        "## Executive Summary",
        "",
        "- This audit scanned active Atnos source/config areas and excluded dependency/build/runtime vendors such as `.venv`, `node_modules`, `dist`, `data`, `runtime`, `logs`, and `outputs` from code-quality tools.",
        "- Automated deletion was limited to high-confidence garbage: `__pycache__`, `.pyc`, `.pyo`, `*.tmp`, `*.bak`, and empty directories.",
        "- Logs, database files, OAuth files, `.env`, generated videos, screenshots, and large binaries were reported but not deleted automatically.",
        "- Autoflake cleanup was conservative: unused imports only, no test files, no `__init__.py`, no Alembic migrations.",
        "",
        "## Cleanup Executed",
        "",
        f"- Deleted `__pycache__` dirs: `{len(cleanup.get('pycache_dirs', []))}`",
        f"- Deleted `.pyc/.pyo/*.tmp/*.bak` files: `{len(cleanup.get('files', []))}`",
        f"- Deleted empty dirs: `{len(cleanup.get('empty_dirs', []))}`",
        "",
        "### Autoflake Cleanup",
        "",
        command_summary(cleanup_result, limit=80),
        "",
        "## Phase 1.1 - Vulture Unused Python Symbols",
        "",
        command_summary(commands[0], limit=120),
        "",
        "## Phase 1.2 - Autoflake Dead Imports Check",
        "",
        command_summary(commands[1], limit=120),
        "",
        "## Phase 1.3 - Orphaned Files / Artifacts",
        "",
        "### Orphaned Python Candidates",
        "",
        truncate_lines(orphaned, limit=120),
        "",
        "### Unreferenced Config Candidates",
        "",
        truncate_lines(config_candidates, limit=120),
        "",
        "### Runtime Artifacts Before Cleanup",
        "",
        f"- `__pycache__` dirs: `{len(artifacts['pycache_dirs'])}`",
        f"- `.pyc/.pyo` files: `{len(artifacts['pyc_pyo_files'])}`",
        f"- `.log` files: `{len(artifacts['log_files'])}`",
        f"- `.tmp/.bak` files: `{len(artifacts['tmp_bak_files'])}`",
        f"- empty dirs: `{len(artifacts['empty_dirs'])}`",
        f"- non-code files >10 MB: `{len(artifacts['oversized_non_code'])}`",
        f"- media files: `{len(artifacts['media_files'])}`",
        "",
        "### Oversized Non-Code Files >10 MB",
        "",
        truncate_lines(artifacts["oversized_non_code"], limit=120),
        "",
        "### Media Files Present",
        "",
        truncate_lines(artifacts["media_files"], limit=120),
        "",
        "## Phase 1.4 - Duplicate Logic",
        "",
        command_summary(commands[2], limit=120),
        "",
        "## Phase 1.5 - TODO / FIXME / HACK / TEMP Debt",
        "",
        truncate_lines(todo, limit=160),
        "",
        "## Phase 1.6 - Commented-Out Code Heuristics",
        "",
        f"Candidate blocks: `{len(commented)}`",
        "",
        "```json",
        json.dumps(commented[:40], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Phase 1.7 - Dependency Bloat Candidates",
        "",
        "These are heuristic only. Some packages are used dynamically, through plugins, or kept for optional runtime paths.",
        "",
        truncate_lines(dep_bloat, limit=120),
        "",
        "## Phase 1.8 - Artifact State After Cleanup",
        "",
        f"- `__pycache__` dirs: `{len(artifacts_after['pycache_dirs'])}`",
        f"- `.pyc/.pyo` files: `{len(artifacts_after['pyc_pyo_files'])}`",
        f"- `.tmp/.bak` files: `{len(artifacts_after['tmp_bak_files'])}`",
        f"- empty dirs: `{len(artifacts_after['empty_dirs'])}`",
        "",
        "## Recommended Next Actions",
        "",
        "1. Review vulture findings manually before deleting code; many FastAPI endpoints, Pydantic models, and plugin entrypoints are reflection-used.",
        "2. Move root-level screenshots and old smoke-test images to an archive folder or delete them after product-owner review.",
        "3. Decide whether duplicate runtime directories (`runtime`, `runtime-recovered`) can be consolidated; this is too risky for autonomous deletion.",
        "4. Add a real `.git` repository or backup snapshot before any larger dead-code removal.",
        "5. Add a scheduled monthly cleanup for `__pycache__`, stale preview PID files, and temporary render artifacts.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
