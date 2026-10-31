"""Export completed production artifacts locally, without email or publication."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil

from atnos.production.order_store import file_hash
from atnos.production.publication_coordinator import verified_package


DELIVERABLES = {
    "video": "video.mp4",
    "portrait_cover": "cover_portrait.png",
    "landscape_thumbnail": "thumbnail_landscape.png",
    "captions": "captions.srt",
    "metadata": "metadata.json",
    "qa_report": "qa_report.json",
    "script": "script.json",
}


def verified_export_files(root: Path, manifest: dict) -> dict[str, Path]:
    root = root.resolve(strict=True)
    checked = {}
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or path in checked:
            raise ValueError("Invalid or duplicate package path")
        if not path.is_file() or file_hash(path) != entry["sha256"]:
            raise ValueError("Package evidence has changed: " + entry["path"])
        checked[path] = entry["sha256"]
    result = {}
    for key in DELIVERABLES:
        path = Path(manifest["deliverables"][key]).resolve(strict=True)
        if path not in checked:
            raise ValueError("Unverified deliverable: " + key)
        result[key] = path
    return result


def export_batch(store, jobs: list[tuple[str, str]], destination: Path) -> dict:
    """Fail before copying if any source fails QA; never replace an existing export."""
    labels = [label for _, label in jobs]
    if (not jobs or len(set(job for job, _ in jobs)) != len(jobs)
            or len({label.casefold() for label in labels}) != len(labels)
            or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", label) for label in labels)):
        raise ValueError("Distinct jobs and safe folder labels are required")
    if not destination.is_absolute():
        raise ValueError("An absolute export directory is required")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("Export already exists; choose a new directory")
    prepared = []
    for job_id, label in jobs:
        package = verified_package(store, job_id)
        root = store.context(job_id).artifact_dir.resolve()
        if destination.is_relative_to(root):
            raise ValueError("Export must be outside the source production directory")
        manifest = next(row["output"] for row in package["state"]["checkpoints"] if row["stage"] == "package")
        sources = verified_export_files(root, manifest)
        hashes = {(root / entry["path"]).resolve(): entry["sha256"] for entry in manifest["files"]}
        prepared.append((job_id, label, package, sources, hashes, root, manifest))

    destination.mkdir(parents=True, exist_ok=False)
    marker = destination / "INCOMPLETE.txt"
    marker.write_text("Export is not verified until results.json exists and this marker is removed.\n", encoding="utf-8")
    entries = []
    for job_id, label, package, sources, hashes, _, _ in prepared:
        folder = destination / label
        folder.mkdir()
        files = {}
        for key, name in DELIVERABLES.items():
            source = sources[key]
            expected = hashes[source]
            target = folder / name
            shutil.copyfile(source, target)
            if file_hash(target) != expected:
                raise ValueError("Copied file failed integrity verification: " + name)
            files[key] = {"path": target.relative_to(destination).as_posix(), "sha256": expected}
        description = folder / "description.txt"
        description.write_text(package["metadata"]["title"] + "\n\n" + package["metadata"]["description"] + "\n", encoding="utf-8")
        files["description"] = {"path": description.relative_to(destination).as_posix(), "sha256": file_hash(description)}
        qa = json.loads(sources["qa_report"].read_text(encoding="utf-8"))
        entries.append({"job_id": job_id, "title": package["metadata"]["title"],
                        "package_sha256": package["hash"], "status": "verified",
                        "duration_seconds": qa["media"]["probe"]["duration_s"],
                        "human_review": qa.get("human_review", "not_performed"), "files": files})
    # Recheck the source state after copying, including checkpoint fingerprints.
    for job_id, _, package, _, _, root, manifest in prepared:
        verified_export_files(root, manifest)
        if verified_package(store, job_id)["hash"] != package["hash"]:
            raise ValueError("Source changed while exporting")
    result = {"status": "verified", "verified_scope": "recorded QA gates and artifact integrity",
              "created_at": datetime.now(UTC).isoformat(), "email_sent": False,
              "published": False, "jobs": entries}
    (destination / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = ["# Wealth Blueprint: local results", "",
              "Content and context are recorded in each film's script and metadata.",
              "Recorded automated QA and file integrity verified. Human listening/review not performed.",
              "No publication or email. Paths work on this computer only.", ""]
    for entry in entries:
        readme.append(f"- [{entry['title']}]({entry['files']['video']['path']}): {entry['duration_seconds']:.2f} s")
    (destination / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    marker.unlink()
    return result
