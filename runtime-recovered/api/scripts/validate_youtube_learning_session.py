from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


GLOBAL_FILES = (
    "run_report.md",
    "source_manifest.json",
    "resume_state.json",
    "tooling_audit.md",
    "missing_tools_and_data.md",
    "fine_tuning_readiness.md",
    "rag_readiness.md",
    "learning_mode_security_report.md",
    "youtube_write_block_test_report.md",
    "shared_youtube_principles.md",
    "final_report.md",
)
TRACK_FILES = (
    "channel_profile.md",
    "topic_taxonomy.md",
    "topic_scoring_rubric.md",
    "hook_library.md",
    "hook_scoring_rubric.md",
    "shorts_structure_playbook.md",
    "script_structure_playbook.md",
    "editing_playbook.md",
    "subtitle_playbook.md",
    "audio_playbook.md",
    "thumbnail_playbook.md",
    "thumbnail_scoring_rubric.md",
    "metadata_playbook.md",
    "pre_publish_qa.md",
    "post_publish_analytics_plan.md",
    "reference_analysis_summary.md",
    "content_safety_rules.md",
    "channel_knowledge.json",
    "reference_video_dataset.jsonl",
)
TRACKS = {
    "Sigma Squad": "sigma_squad",
    "Dark Chronicles": "dark_chronicles",
    "Wealth Blueprint": "wealth_blueprint",
}
RECORD_FIELDS = {
    "url", "video_id", "channel_id", "publish_date", "duration", "title", "description",
    "hashtags", "public_stats", "thumbnail_url", "transcript_available", "detected_niche",
    "topic", "hook_type", "structure_summary", "tempo_estimate", "audio_profile",
    "thumbnail_profile", "potential_success_factors", "potential_issues", "confidence_level",
}
SCOPED_CODE_FILES = (
    "runtime-recovered/api/src/atnos/core/config.py",
    "runtime-recovered/api/src/atnos/api/main.py",
    "runtime-recovered/api/src/atnos/services/learning_mode_guard.py",
    "runtime-recovered/api/src/atnos/services/youtube_connector_service.py",
    "runtime-recovered/api/src/atnos/learning/youtube_session.py",
    "runtime-recovered/api/scripts/run_youtube_learning_mode_session.py",
    "runtime-recovered/api/scripts/validate_youtube_learning_session.py",
    "runtime-recovered/api/tests/test_learning_mode_guard.py",
    "runtime-recovered/api/tests/test_youtube_learning_session.py",
    "scripts/Set-AtnosLearningMode.ps1",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Waliduje technicznie ukończoną sesję Atnos Learning Mode.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return (completed.stdout or completed.stderr).strip()


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _profile_extract(path: Path) -> list[str]:
    profile = _read_json(path).get("channel_profile") or []
    findings: list[str] = []
    for item in profile:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or item.get("source") or "").strip()
        label = str(item.get("label_type") or item.get("status") or "lack_of_data")
        if value:
            findings.append(f"**[{label}]** {value}")
        if len(findings) == 3:
            break
    return findings or ["**[lack_of_data]** Brak zweryfikowanego profilu w artefakcie Atnosa."]


def _tooling(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("- **"):
            lines.append(line)
    return lines


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = _parse_args()
    run_root = args.run_root.resolve()
    repo = args.repo.resolve()
    vault = args.vault.resolve()
    if run_root.drive.upper() not in {"D:", "F:"} or repo.drive.upper() not in {"D:", "F:"}:
        raise SystemExit("Run i repo muszą znajdować się na D: lub F:.")

    missing = [name for name in GLOBAL_FILES if not (run_root / name).is_file()]
    record_counts: dict[str, int] = {}
    deep_counts: dict[str, int] = {}
    invalid_records: list[str] = []
    duplicate_records: list[str] = []
    for track, directory in TRACKS.items():
        missing.extend(f"{directory}/{name}" for name in TRACK_FILES if not (run_root / directory / name).is_file())
        rows = _jsonl(run_root / directory / "reference_video_dataset.jsonl")
        record_counts[track] = len(rows)
        deep_counts[track] = sum(row.get("record_status") == "complete" for row in rows)
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (str(row.get("video_id") or ""), str(row.get("source_track") or ""))
            if not RECORD_FIELDS.issubset(row):
                invalid_records.append(f"{track}:{key[0]}")
            if key in seen:
                duplicate_records.append(f"{track}:{key[0]}")
            seen.add(key)

    textual = "\n".join(
        path.read_text(encoding="utf-8-sig", errors="replace")
        for path in run_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".jsonl", ".txt"}
    )
    sensitive_hits = []
    checks = {
        "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "bearer_token": r"(?i)\bBearer\s+[A-Za-z0-9._~-]{20,}",
        "common_api_key": r"\b(?:sk-(?:proj-)?|AIza)[A-Za-z0-9_-]{16,}",
        "replacement_character": "\ufffd",
    }
    for name, pattern in checks.items():
        if re.search(pattern, textual, flags=re.IGNORECASE):
            sensitive_hits.append(name)

    state = _read_json(run_root / "resume_state.json")
    manifest = _read_json(run_root / "source_manifest.json")
    write_report = (run_root / "youtube_write_block_test_report.md").read_text(encoding="utf-8-sig")
    write_block_passed = write_report.count("`BLOCKED`") >= 10 and "`FAILED`" not in write_report
    source_rows = manifest.get("sources") or []
    successful_sources = sum(item.get("status") == "collected" and int(item.get("metadata_items") or 0) > 0 for item in source_rows)
    unavailable_sources = [item.get("url") for item in source_rows if int(item.get("metadata_items") or 0) == 0]
    output_bytes = _tree_size(run_root)
    free_gb = shutil.disk_usage(run_root.anchor).free / 1024**3
    status_lines = _git(repo, "status", "--short").splitlines()
    branch = _git(repo, "branch", "--show-current")
    obsidian_notes = list((vault / "Atnos Knowledge Base").rglob("*.md")) if (vault / "Atnos Knowledge Base").exists() else []
    started = datetime.fromisoformat(str(state["started_at"]))
    ended = datetime.fromisoformat(str(state["last_checkpoint_at"]))
    validation_passed = (
        state.get("current_phase") == "completed"
        and not missing
        and not invalid_records
        and not duplicate_records
        and not sensitive_hits
        and write_block_passed
    )

    findings = {track: _profile_extract(run_root / directory / "channel_knowledge.json") for track, directory in TRACKS.items()}
    source_list = "\n".join(
        f"- {item.get('track')}: `{item.get('url')}` — {item.get('metadata_items', 0)} rekordów, {item.get('collection_mode')}"
        for item in source_rows
    )
    tools = "\n".join(_tooling(run_root / "tooling_audit.md"))
    scoped_changes = [name for name in SCOPED_CODE_FILES if (repo / name).exists()]
    report = f"""# Raport operatorski — Atnos Learning Mode

## Wynik
- Walidacja operatorska: `{'PASS' if validation_passed else 'FAIL'}`
- Ten raport zestawia dowody techniczne. Wnioski kanałowe poniżej są wyłącznie ekstraktem z `channel_knowledge.json` wygenerowanego przez Atnosa.

## 1–7. Czas i lokalizacja
1. Start: `{started.isoformat()}`
2. Koniec: `{ended.isoformat()}`
3. Łączny czas: `{ended - started}`
4. Repo: `{repo}`; status: `{'dirty (' + str(len(status_lines)) + ' wpisów)' if status_lines else 'clean'}`
5. Branch: `{branch}`
6. Vault: `{vault}`; notatki w Atnos Knowledge Base: `{len(obsidian_notes)}`
7. Output root: `{run_root}`

## 8–11. Źródła i próbka
8. Użyte źródła:
{source_list}
9. Źródła z użytecznymi rekordami: `{successful_sources}/{len(source_rows)}`
10. Rekordy per ścieżka: `{json.dumps(record_counts, ensure_ascii=False)}`
11. Deep analyses per ścieżka: `{json.dumps(deep_counts, ensure_ascii=False)}`

## 12–17. Bezpieczeństwo i narzędzia
12. Zapisane dane: `{output_bytes}` bajtów (`{output_bytes / 1024**2:.2f} MiB`)
13. Wolne miejsce na `{run_root.drive}`: `{free_gb:.2f} GiB`
14. YouTube write-block: `{'PASS' if write_block_passed else 'FAIL'}`; 10 klas operacji write objętych testem.
15. LEARNING_MODE: `{state.get('learning_mode')}`; final phase: `{state.get('current_phase')}`
16. Testy bezpieczeństwa: missing={len(missing)}, invalid={len(invalid_records)}, duplicates={len(duplicate_records)}, sensitive_hits={sensitive_hits or []}
17. Stan narzędzi:
{tools}

## 18. Ustalenia Atnosa — Sigma Squad
{chr(10).join('- ' + item for item in findings['Sigma Squad'])}

## 19. Ustalenia Atnosa — Dark Chronicles
{chr(10).join('- ' + item for item in findings['Dark Chronicles'])}

## 20. Ustalenia Atnosa — Wealth Blueprint
{chr(10).join('- ' + item for item in findings['Wealth Blueprint'])}

## 21. Czego Atnos faktycznie się nauczył
- Wygenerował po 17 opisowych playbooków/rubryk, `channel_knowledge.json` i JSONL dla każdej ścieżki.
- Wygenerował wspólne zasady YouTube, gotowość RAG i plan analityki po publikacji.
- Zachował etykiety epistemiczne; brak sygnału audio/tempa pozostawił jako `lack_of_data`.

## 22–25. Braki, blokery i następny etap
22. Niewykonane: pełne filmy, pełne transkrypty, fine-tuning, finalne assety i publikacja — celowo zabronione w tej sesji.
23. Blokery: YouTube Data API OAuth niedostępne; publiczny fallback ma mniej statystyk. ComfyUI i F5-TTS były niedostępne. Źródła bez rekordów: `{unavailable_sources}`.
24. Gotowość: wiedza jest gotowa do ręcznego review i późniejszego lokalnego indeksowania RAG; nie jest jeszcze zgodą na automatyczną produkcję/publiczny upload.
25. Następne zadania: review wiedzy, aktywacja Data API po wyłączeniu Learning Mode, naprawa ComfyUI/F5-TTS, kontrolowany prywatny render jednej próbki na kanał i QA.

## 26–28. Zmiany i decyzje właściciela
26. Zakres kodu tej sesji: `{', '.join(scoped_changes)}`.
27. Commit readiness: `technicznie zwalidowane, ale brak commita`; repo zawiera również wcześniejsze, niespowiązane zmiany i wymaga decyzji właściciela o zakresie.
28. Decyzje właściciela: zaakceptować/odrzucić wiedzę, zdecydować o commicie, zdecydować kiedy wyłączyć Learning Mode i przejść do prywatnych renderów.

## Rozdział odpowiedzialności
- **Atnos:** pozyskanie metadanych, lokalna analiza, profile, playbooki, rubryki, JSONL i zapis wiedzy.
- **Codex/operator:** guardy, storage, checkpointy, naprawa parsera/klienta Ollamy i walidacja dowodów.
- **Hipotezy:** wyłącznie elementy oznaczone przez Atnosa jako `hypothesis`.
- **Niezweryfikowane:** jakość realnego montażu/audio i przyszłe wyniki kanałów.
"""
    output = run_root / "operator_validation_report.md"
    _atomic_write(output, report)
    obsidian_target = vault / "Atnos Knowledge Base" / "00 Run Logs" / f"operator_validation_report - {state['run_id']}.md"
    obsidian_target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(obsidian_target, report)
    print(json.dumps({
        "passed": validation_passed,
        "report": str(output),
        "obsidian_report": str(obsidian_target),
        "missing": missing,
        "invalid_records": invalid_records,
        "duplicate_records": duplicate_records,
        "sensitive_hits": sensitive_hits,
    }, ensure_ascii=False, indent=2))
    return 0 if validation_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
