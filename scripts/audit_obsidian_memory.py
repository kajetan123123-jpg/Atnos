from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "runtime-recovered" / "api"
SRC_ROOT = API_ROOT / "src"
DATE = datetime.now().strftime("%Y%m%d")
REPORT_PATH = ROOT / f"obsidian_memory_audit_{DATE}.md"
ARTIFACT_DIR = ROOT / f"obsidian_memory_audit_{DATE}_artifacts"

EXCLUDED_PARTS = {
    ".venv",
    "node_modules",
    "data",
    "dist",
    "build",
    "__pycache__",
    "site-packages",
    "tool-caches",
}


def main() -> int:
    _ensure_frontmatter()
    sys.path.insert(0, str(SRC_ROOT))

    from atnos.core.config import Settings
    from memory.obsidian_note_store import NoteSchema, cleanup_expired_notes, deduplicate_notes, read_notes, write_note

    settings = Settings()
    vault = _resolve_vault(settings)
    if getattr(settings, "obsidian_vault_path", None) is None:
        settings.obsidian_vault_path = vault
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    code_hits = _find_code_hits()
    vault_metrics = _measure_vault(vault)
    e2e = _run_e2e(settings, NoteSchema, write_note, read_notes, cleanup_expired_notes, deduplicate_notes)
    findings = _build_findings(code_hits=code_hits, vault_metrics=vault_metrics)

    (ARTIFACT_DIR / "code_hits.json").write_text(json.dumps(code_hits, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARTIFACT_DIR / "vault_metrics.json").write_text(
        json.dumps(vault_metrics, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (ARTIFACT_DIR / "e2e_results.json").write_text(json.dumps(e2e, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(_render_report(vault, code_hits, vault_metrics, findings, e2e), encoding="utf-8")
    print(f"REPORT={REPORT_PATH}")
    print(f"VAULT={vault}")
    print(f"E2E={json.dumps(e2e, ensure_ascii=False)}")
    return 0 if all(item["passed"] for item in e2e) else 1


def _ensure_frontmatter() -> None:
    try:
        import frontmatter  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "python-frontmatter>=1.1.0,<2.0.0",
                "-q",
            ]
        )


def _resolve_vault(settings: Any) -> Path:
    candidates = [
        getattr(settings, "obsidian_vault_path", None),
        os.environ.get("OBSIDIAN_VAULT_PATH"),
        Path("E:/Atnos/Knowledge/Obsidian/Atnos-Brain"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if path.exists():
            return path
    fallback = Path("E:/Atnos/Knowledge/Obsidian/Atnos-Brain").resolve()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _find_code_hits() -> dict[str, Any]:
    roots = [API_ROOT / "src", API_ROOT / "tests", ROOT / "scripts", ROOT / "docs"]
    pattern = re.compile(r"obsidian|obsydian|vault|\.md|note|memory", re.IGNORECASE)
    hits: list[dict[str, Any]] = []
    files_by_role: dict[str, list[str]] = {"writes": [], "reads": [], "both": []}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            lines: list[dict[str, Any]] = []
            text = path.read_text(encoding="utf-8", errors="replace")
            for index, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    lines.append({"line": index, "text": line.strip()[:300]})
            if not lines:
                continue
            rel = str(path.relative_to(ROOT))
            lowered = text.lower()
            writes = any(token in lowered for token in [".write_text(", "write_note(", "_write_obsidian_note", "write_decision("])
            reads = any(token in lowered for token in [".read_text(", "frontmatter.load", "search_operational_memory", "search_recent_agent_logs"])
            if writes and reads:
                files_by_role["both"].append(rel)
            elif writes:
                files_by_role["writes"].append(rel)
            elif reads:
                files_by_role["reads"].append(rel)
            hits.append({"file": rel, "hits": lines[:80], "hit_count": len(lines), "writes": writes, "reads": reads})
    return {
        "files_scanned": sum(1 for root in roots if root.exists() for _ in root.rglob("*.py")),
        "hit_files": hits,
        "files_by_role": files_by_role,
    }


def _measure_vault(vault: Path) -> dict[str, Any]:
    markdown_files = [path for path in vault.rglob("*.md") if "_archive" not in path.parts]
    frontmatter_files: list[Path] = []
    title_counts: Counter[str] = Counter()
    title_paths: dict[str, list[str]] = defaultdict(list)
    references: Counter[str] = Counter()
    note_stems: dict[str, Path] = {}
    sizes: list[int] = []
    stale_cutoff = datetime.now(UTC) - timedelta(days=30)

    for path in markdown_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sizes.append(path.stat().st_size)
        if text.startswith("---"):
            frontmatter_files.append(path)
            title = _extract_frontmatter_title(text)
            if title:
                title_counts[title] += 1
                title_paths[title].append(str(path))
        for link in re.findall(r"\[\[([^\]|#]+)", text):
            references[Path(link.strip()).stem] += 1
        note_stems[path.stem] = path

    orphan_notes: list[str] = []
    stale_orphans: list[str] = []
    for stem, path in note_stems.items():
        if references[stem] > 0:
            continue
        rel = str(path.relative_to(vault))
        orphan_notes.append(rel)
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < stale_cutoff:
            stale_orphans.append(rel)

    duplicate_titles = {
        title: paths
        for title, paths in title_paths.items()
        if title_counts[title] > 1
    }
    return {
        "note_count": len(markdown_files),
        "frontmatter_count": len(frontmatter_files),
        "without_frontmatter_count": len(markdown_files) - len(frontmatter_files),
        "average_note_size_bytes": round(sum(sizes) / len(sizes), 2) if sizes else 0,
        "total_note_size_bytes": sum(sizes),
        "duplicate_title_count": len(duplicate_titles),
        "duplicate_titles_sample": dict(list(duplicate_titles.items())[:25]),
        "orphan_note_count": len(orphan_notes),
        "orphan_notes_sample": orphan_notes[:50],
        "stale_orphan_count": len(stale_orphans),
        "stale_orphans_sample": stale_orphans[:50],
        "wikilink_reference_count": sum(references.values()),
    }


def _extract_frontmatter_title(text: str) -> str | None:
    match = re.match(r"---\s*(.*?)\s*---", text, flags=re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def _run_e2e(
    settings: Any,
    NoteSchema: Any,
    write_note: Any,
    read_notes: Any,
    cleanup_expired_notes: Any,
    deduplicate_notes: Any,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    def record(name: str, passed: bool, detail: str) -> None:
        results.append({"name": name, "passed": passed, "detail": detail})

    try:
        schema = NoteSchema(
            title=f"obsidian-audit-live-test-{now.strftime('%Y%m%dT%H%M%SZ')}",
            agent="atnos-audit",
            type="test",
            created_at=now.isoformat(),
            tags=["audit", "e2e"],
            status="done",
            linked_notes=[],
        )
        path = write_note(schema, "Live test note written by Atnos Obsidian audit.", settings=settings)
        record("write_note", path.exists(), str(path))
    except Exception as exc:
        record("write_note", False, f"{type(exc).__name__}: {exc}")
        return results

    try:
        notes = read_notes(type="test", agent="atnos-audit", settings=settings)
        record("read_notes", any(item.title == schema.title for item in notes), f"returned={len(notes)}")
    except Exception as exc:
        record("read_notes", False, f"{type(exc).__name__}: {exc}")

    try:
        expired = NoteSchema(
            title=f"obsidian-audit-expired-{now.strftime('%Y%m%dT%H%M%SZ')}",
            agent="atnos-audit",
            type="test",
            created_at=now.isoformat(),
            expires_at=(now - timedelta(seconds=1)).isoformat(),
            tags=["audit", "expired"],
            status="done",
            linked_notes=[],
        )
        expired_path = write_note(expired, "Expired test note.", settings=settings)
        archived = cleanup_expired_notes(settings=settings)
        record("cleanup_expired_notes", archived >= 1 and not expired_path.exists(), f"archived={archived}")
    except Exception as exc:
        record("cleanup_expired_notes", False, f"{type(exc).__name__}: {exc}")

    try:
        duplicate_title = f"obsidian-audit-duplicate-{now.strftime('%Y%m%dT%H%M%SZ')}"
        older = NoteSchema(
            title=duplicate_title,
            agent="atnos-audit-old",
            type="test",
            created_at=(now - timedelta(seconds=3)).isoformat(),
            tags=["audit", "duplicate"],
            status="done",
            linked_notes=[],
        )
        newer = NoteSchema(
            title=duplicate_title,
            agent="atnos-audit-new",
            type="test",
            created_at=(now - timedelta(seconds=1)).isoformat(),
            tags=["audit", "duplicate"],
            status="done",
            linked_notes=[],
        )
        write_note(older, "Older duplicate.", settings=settings)
        write_note(newer, "Newer duplicate.", settings=settings)
        removed = deduplicate_notes("test", settings=settings)
        remaining = [item for item in read_notes(type="test", settings=settings) if item.title == duplicate_title]
        record("deduplicate_notes", removed >= 1 and len(remaining) == 1, f"removed={removed}, remaining={len(remaining)}")
    except Exception as exc:
        record("deduplicate_notes", False, f"{type(exc).__name__}: {exc}")

    return results


def _build_findings(*, code_hits: dict[str, Any], vault_metrics: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if vault_metrics["without_frontmatter_count"] > 0:
        findings.append(
            {
                "severity": "CRITICAL",
                "issue": "Czesc notatek nie ma frontmatter",
                "detail": f"{vault_metrics['without_frontmatter_count']} / {vault_metrics['note_count']} notatek jest niequeryowalnych dla Dataview/agentow.",
            }
        )
    if code_hits["files_by_role"]["reads"] or code_hits["files_by_role"]["both"]:
        findings.append(
            {
                "severity": "WARN",
                "issue": "Istnieja odczyty lokalnych markdownow i indeksow poza jednym adapterem",
                "detail": "Dodano `memory.obsidian_note_store` jako centralny adapter dla nowych przeplywow; legacy zostawione bez lamania kompatybilnosci.",
            }
        )
    if vault_metrics["duplicate_title_count"] > 0:
        findings.append(
            {
                "severity": "WARN",
                "issue": "Duplikaty tytulow frontmatter",
                "detail": f"Wykryto {vault_metrics['duplicate_title_count']} zduplikowanych tytulow frontmatter.",
            }
        )
    if vault_metrics["orphan_note_count"] > 0:
        findings.append(
            {
                "severity": "INFO",
                "issue": "Sieroty bez wikilinkow",
                "detail": f"{vault_metrics['orphan_note_count']} notatek nie jest referencjonowanych przez wikilinki.",
            }
        )
    return findings


def _render_report(
    vault: Path,
    code_hits: dict[str, Any],
    vault_metrics: dict[str, Any],
    findings: list[dict[str, str]],
    e2e: list[dict[str, Any]],
) -> str:
    lines = [
        "# Atnos - Obsidian Memory Audit",
        f"Data: {datetime.now(UTC).isoformat()}",
        f"Vault: `{vault}`",
        "",
        "## Executive Summary",
        "- Dodano standardowy `NoteSchema` i centralny adapter `memory.obsidian_note_store`.",
        "- Dodano zapis frontmatter, indeksowany odczyt, cache 30s, expiry cleanup i deduplikacje.",
        "- Dodano read-back verification do legacy `ObsidianWriter`.",
        "",
        "## Current Integration Map",
        f"- Pliki z trafieniami Obsidian/memory: {len(code_hits['hit_files'])}",
        f"- Tylko zapis: {len(code_hits['files_by_role']['writes'])}",
        f"- Tylko odczyt: {len(code_hits['files_by_role']['reads'])}",
        f"- Odczyt i zapis: {len(code_hits['files_by_role']['both'])}",
        "",
        "### Key Write Paths",
        *[f"- `{item}`" for item in code_hits["files_by_role"]["writes"][:30]],
        "",
        "### Key Read/Write Paths",
        *[f"- `{item}`" for item in code_hits["files_by_role"]["both"][:30]],
        "",
        "## Vault Metrics",
        f"- Note count: {vault_metrics['note_count']}",
        f"- With frontmatter: {vault_metrics['frontmatter_count']}",
        f"- Without frontmatter: {vault_metrics['without_frontmatter_count']}",
        f"- Average note size: {vault_metrics['average_note_size_bytes']} B",
        f"- Wikilink references: {vault_metrics['wikilink_reference_count']}",
        f"- Duplicate title groups: {vault_metrics['duplicate_title_count']}",
        f"- Orphan notes: {vault_metrics['orphan_note_count']}",
        f"- Stale orphan notes >30d: {vault_metrics['stale_orphan_count']}",
        "",
        "## Findings",
    ]
    if findings:
        lines.extend(f"- **{item['severity']}** {item['issue']}: {item['detail']}" for item in findings)
    else:
        lines.append("- Brak istotnych problemow.")
    lines.extend(
        [
            "",
            "## Implemented Optimizations",
            "- `NoteSchema`: title, agent, type, created_at, expires_at, tags, status, linked_notes.",
            "- `write_note(schema, body)`: generuje frontmatter, zapisuje do `<agent>/<type>/<title>.md`, weryfikuje hash po odczycie.",
            "- `read_notes(...)`: czyta katalogi agent/type, filtruje frontmatter, sortuje po `created_at`, cache 30s.",
            "- `cleanup_expired_notes()`: przenosi wygasle notatki do `_archive`.",
            "- `deduplicate_notes(type)`: zostawia najnowszy tytul, starsze przenosi do `_archive`.",
            "- Legacy `ObsidianWriter.write_note(...)`: dodano read-back verification.",
            "",
            "## E2E Verification",
        ]
    )
    lines.extend(
        f"- {'PASS' if item['passed'] else 'FAIL'} `{item['name']}` - {item['detail']}" for item in e2e
    )
    lines.extend(
        [
            "",
            "## Artifacts",
            f"- `{ARTIFACT_DIR / 'code_hits.json'}`",
            f"- `{ARTIFACT_DIR / 'vault_metrics.json'}`",
            f"- `{ARTIFACT_DIR / 'e2e_results.json'}`",
            "",
            "## Next Step",
            "Przepiac nowe workery produkcyjne i researchowe z recznych `.write_text()` / prywatnych `_write_obsidian_note()` na `memory.obsidian_note_store.write_note()`, z typami notatek zgodnymi z Dataview.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
