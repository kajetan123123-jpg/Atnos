from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
import stat
from typing import Any

from atnos.core.config import Settings
from atnos.services.output_artifact_service import OutputArtifactService
from atnos.tools.base import BaseTool


class FileInventoryTool(BaseTool):
    """Builds and independently validates a Markdown inventory of large files."""

    name = "file_inventory_report"
    description = (
        "Scan an allowed local Atnos directory for files above a byte threshold, "
        "write a managed Markdown report, and validate the result with a second scan."
    )
    parameters = {
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "description": "Absolute directory inside the configured Atnos app or data root.",
            },
            "minimum_size_bytes": {
                "type": "integer",
                "minimum": 1,
                "description": "Only files at least this large are included.",
            },
        },
        "required": ["root", "minimum_size_bytes"],
        "additionalProperties": False,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        root = self._allowed_root(str(kwargs.get("root") or ""))
        minimum_size_bytes = int(kwargs.get("minimum_size_bytes") or 0)
        if minimum_size_bytes < 1:
            raise ValueError("minimum_size_bytes must be greater than zero.")

        matches = self._scan(root, minimum_size_bytes)
        report_path = self.artifacts.create_output_path(conversation_id=None, extension=".md")
        report_path.write_text(
            self._render_report(root, minimum_size_bytes, matches),
            encoding="utf-8",
        )
        descriptor = self.artifacts.finalize_output(
            absolute_path=report_path,
            filename=f"file-inventory-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.md",
            media_type="text/markdown",
        )

        validation = self._validate(
            root=root,
            minimum_size_bytes=minimum_size_bytes,
            expected=matches,
            report_path=descriptor.absolute_path,
        )
        if not validation["verified"]:
            descriptor.absolute_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Independent file inventory validation failed: "
                + "; ".join(validation["errors"])
            )

        return {
            "root": str(root),
            "minimum_size_bytes": minimum_size_bytes,
            "match_count": len(matches),
            "matches": matches,
            "artifact": {
                "filename": descriptor.filename,
                "storage_path": descriptor.storage_path,
                "absolute_path": str(descriptor.absolute_path),
                "media_type": descriptor.media_type,
                "size_bytes": descriptor.size_bytes,
                "sha256": validation["report_sha256"],
            },
            "validation": validation,
        }

    def _allowed_root(self, raw_root: str) -> Path:
        candidate = Path(raw_root).expanduser().resolve()
        if not candidate.exists() or not candidate.is_dir():
            raise FileNotFoundError(f"Scan root does not exist or is not a directory: {candidate}")

        allowed_roots = {
            self.settings.app_root.resolve(),
            self.settings.data_dir.resolve(),
        }
        for allowed in allowed_roots:
            try:
                candidate.relative_to(allowed)
                return candidate
            except ValueError:
                continue
        raise PermissionError("Scan root is outside the configured Atnos app and data roots.")

    @staticmethod
    def _scan(
        root: Path,
        minimum_size_bytes: int,
        *,
        excluded_paths: set[Path] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = {item.resolve() for item in (excluded_paths or set())}
        matches: list[dict[str, Any]] = []
        scan_errors: list[str] = []

        def _record_walk_error(error: OSError) -> None:
            scan_errors.append(f"{error.filename or root}: {error}")

        for directory, dirnames, filenames in os.walk(
            root,
            followlinks=False,
            onerror=_record_walk_error,
        ):
            base = Path(directory)
            safe_dirnames: list[str] = []
            for dirname in dirnames:
                child = base / dirname
                try:
                    child_stat = child.lstat()
                except FileNotFoundError:
                    # Package managers can leave transient or platform-only
                    # directory links. They are not local files to inventory.
                    continue
                except OSError as exc:
                    scan_errors.append(f"{child}: {exc}")
                    continue
                file_attributes = int(getattr(child_stat, "st_file_attributes", 0))
                is_reparse_point = bool(
                    file_attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
                )
                if stat.S_ISLNK(child_stat.st_mode) or is_reparse_point:
                    # Never traverse directory links. Besides avoiding broken
                    # pnpm links, this prevents a scan from escaping the
                    # explicitly allowed root through a junction.
                    continue
                safe_dirnames.append(dirname)
            dirnames[:] = sorted(safe_dirnames, key=str.casefold)
            filenames.sort(key=str.casefold)
            for filename in filenames:
                path = (base / filename).resolve()
                if path in excluded:
                    continue
                try:
                    file_stat = path.stat()
                except FileNotFoundError:
                    # SQLite WAL/SHM files and atomic temporary files may
                    # legitimately disappear between os.walk and stat.
                    continue
                except OSError as exc:
                    scan_errors.append(f"{path}: {exc}")
                    continue
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size < minimum_size_bytes:
                    continue
                matches.append(
                    {
                        "path": str(path),
                        "relative_path": path.relative_to(root).as_posix(),
                        "size_bytes": file_stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            file_stat.st_mtime,
                            tz=UTC,
                        ).isoformat().replace("+00:00", "Z"),
                    }
                )
        if scan_errors:
            sample = "; ".join(scan_errors[:3])
            suffix = "" if len(scan_errors) <= 3 else f"; and {len(scan_errors) - 3} more"
            raise PermissionError(f"File inventory could not read every entry: {sample}{suffix}")
        matches.sort(key=lambda item: (-int(item["size_bytes"]), str(item["relative_path"]).casefold()))
        return matches

    @staticmethod
    def _render_report(
        root: Path,
        minimum_size_bytes: int,
        matches: list[dict[str, Any]],
    ) -> str:
        lines = [
            "# Atnos file inventory",
            "",
            f"- Scan root: `{root}`",
            f"- Minimum size: `{minimum_size_bytes}` bytes",
            f"- Matching files: `{len(matches)}`",
            f"- Generated at: `{datetime.now(UTC).isoformat().replace('+00:00', 'Z')}`",
            "",
            "| Size (bytes) | Relative path | Modified (UTC) |",
            "| ---: | --- | --- |",
        ]
        lines.extend(
            f"| {item['size_bytes']} | `{item['relative_path']}` | {item['modified_at']} |"
            for item in matches
        )
        if not matches:
            lines.append("| 0 | _No files matched the threshold._ | - |")
        lines.append("")
        return "\n".join(lines)

    def _validate(
        self,
        *,
        root: Path,
        minimum_size_bytes: int,
        expected: list[dict[str, Any]],
        report_path: Path,
    ) -> dict[str, Any]:
        observed = self._scan(
            root,
            minimum_size_bytes,
            excluded_paths={report_path},
        )
        expected_index = {
            (str(item["relative_path"]), int(item["size_bytes"]))
            for item in expected
        }
        observed_index = {
            (str(item["relative_path"]), int(item["size_bytes"]))
            for item in observed
        }
        errors: list[str] = []
        missing = sorted(expected_index - observed_index)
        unexpected = sorted(observed_index - expected_index)
        if missing:
            errors.append(f"{len(missing)} expected files disappeared or changed size")
        if unexpected:
            errors.append(f"{len(unexpected)} qualifying files were omitted")

        report_bytes = report_path.read_bytes() if report_path.exists() else b""
        report_text = report_bytes.decode("utf-8") if report_bytes else ""
        if not report_bytes:
            errors.append("report file is missing or empty")
        if f"- Matching files: `{len(expected)}`" not in report_text:
            errors.append("report count does not match the scanned file count")
        for item in expected:
            if f"`{item['relative_path']}`" not in report_text:
                errors.append(f"report does not contain {item['relative_path']}")
                break

        return {
            "verified": not errors,
            "errors": errors,
            "expected_count": len(expected),
            "observed_count": len(observed),
            "missing": [
                {"relative_path": path, "size_bytes": size}
                for path, size in missing
            ],
            "unexpected": [
                {"relative_path": path, "size_bytes": size}
                for path, size in unexpected
            ],
            "report_sha256": hashlib.sha256(report_bytes).hexdigest() if report_bytes else None,
        }
