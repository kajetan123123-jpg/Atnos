"""Audyt higieny Fazy 1 dla Atnosa.

Skrypt nie czyta ani nie wypisuje zawartosci sekretow. Sprawdza tylko, czy
niebezpieczne pliki i artefakty runtime nadal leza w katalogu glownym backendu.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "client_secret*.json",
    "*_token*.json",
    "*_token*.pickle",
    "token*.pickle",
    "oauth*_authorization_url*.txt",
    "multi_account_oauth_links*.json",
)

RUNTIME_PATTERNS = (
    "*.log",
    "*.pid",
    "*.tmp",
    "*.bak",
    "*TEMP_MPY*.mp4",
    "tmp-*.png",
    "tmp_*.png",
    "tmp-*.mp4",
    "tmp_*.mp4",
)

SAFE_ROOT_FILES = {
    ".env.example",
    "README.md",
    "alembic.ini",
    "package-lock.json",
    "package.json",
    "pyproject.toml",
    "uv.lock",
}


@dataclass(frozen=True)
class Finding:
    """Pojedynczy problem wykryty w katalogu glownym backendu."""

    category: str
    path: str
    size_bytes: int
    action: str


def _matches(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend(path for path in root.glob(pattern) if path.is_file())
    return sorted(set(files))


def scan_backend_root(root: Path) -> list[Finding]:
    """Zwroc liste sekretow i artefaktow runtime znalezionych w rootcie."""

    findings: list[Finding] = []

    for path in _matches(root, SECRET_PATTERNS):
        if path.name == ".env.example":
            continue
        findings.append(
            Finding(
                category="secret_or_oauth_artifact",
                path=str(path.relative_to(root)),
                size_bytes=path.stat().st_size,
                action="move_outside_repo_or_secure_config_store",
            )
        )

    for path in _matches(root, RUNTIME_PATTERNS):
        if path.name in SAFE_ROOT_FILES:
            continue
        findings.append(
            Finding(
                category="runtime_artifact",
                path=str(path.relative_to(root)),
                size_bytes=path.stat().st_size,
                action="move_to_logs_data_or_outputs",
            )
        )

    return sorted(findings, key=lambda item: (item.category, item.path.lower()))


def main() -> int:
    """Uruchom audyt i zakoncz kodem 1, jesli sa blokery Fazy 1."""

    parser = argparse.ArgumentParser(description="Atnos Phase 1 hygiene audit")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Backend root directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    findings = scan_backend_root(root)
    payload = {
        "root": str(root),
        "passed": not findings,
        "finding_count": len(findings),
        "findings": [asdict(finding) for finding in findings],
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        status = "PASS" if payload["passed"] else "FAIL"
        print(f"Atnos Phase 1 hygiene audit: {status}")
        print(f"Root: {root}")
        print(f"Findings: {len(findings)}")
        for finding in findings:
            print(
                f"- [{finding.category}] {finding.path} "
                f"({finding.size_bytes} bytes) -> {finding.action}"
            )

    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
