import json
from types import SimpleNamespace

import pytest

from atnos.production import local_export
from atnos.production.order_store import file_hash


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    files, deliverables = [], {}
    for key, name in local_export.DELIVERABLES.items():
        path = root / name
        content = {"media": {"probe": {"duration_s": 55}}, "human_review": "not_performed"} if key == "qa_report" else {"fixture": key}
        path.write_text(json.dumps(content), encoding="utf-8")
        files.append({"path": name, "sha256": file_hash(path)})
        deliverables[key] = str(path)
    manifest = {"files": files, "deliverables": deliverables}
    package = {"hash": "fixture-package", "metadata": {"title": "Title", "description": "Description"},
               "state": {"checkpoints": [{"stage": "package", "output": manifest}]}}
    monkeypatch.setattr(local_export, "verified_package", lambda *_: package)
    return root, manifest, SimpleNamespace(context=lambda _: SimpleNamespace(artifact_dir=root))


def test_export_copies_verified_files_and_never_overwrites(source, tmp_path):
    root, manifest, store = source
    target = tmp_path / "export"
    result = local_export.export_batch(store, [("prod-test", "01-test")], target)
    assert result["status"] == "verified"
    assert result["email_sent"] is False and result["published"] is False
    assert not (target / "INCOMPLETE.txt").exists()
    assert result["jobs"][0]["human_review"] == "not_performed"
    for entry in result["jobs"][0]["files"].values():
        assert file_hash(target / entry["path"]) == entry["sha256"]
    assert (target / "01-test" / "description.txt").read_text() == "Title\n\nDescription\n"
    with pytest.raises(FileExistsError):
        local_export.export_batch(store, [("prod-test", "01-test")], target)


def test_export_refuses_modified_evidence_before_copy(source, tmp_path):
    root, manifest, store = source
    (root / "cover_portrait.png").write_bytes(b"tampered")
    target = tmp_path / "export"
    with pytest.raises(ValueError, match="changed"):
        local_export.export_batch(store, [("prod-test", "01-test")], target)
    assert not target.exists()


def test_export_rejects_path_escape_and_duplicate_labels(source, tmp_path):
    root, manifest, store = source
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    manifest["files"].append({"path": "../outside.txt", "sha256": file_hash(outside)})
    with pytest.raises(ValueError, match="path"):
        local_export.verified_export_files(root, manifest)
    for jobs in [[("prod-test", "../escape")], [("a", "same"), ("b", "same")]]:
        with pytest.raises(ValueError, match="safe"):
            local_export.export_batch(store, jobs, tmp_path / "export")


def test_export_refuses_failed_qa(source, tmp_path, monkeypatch):
    _, _, store = source
    def blocked(*_):
        raise ValueError("QA blocked")
    monkeypatch.setattr(local_export, "verified_package", blocked)
    with pytest.raises(ValueError, match="QA blocked"):
        local_export.export_batch(store, [("prod-test", "01-test")], tmp_path / "export")
    assert not (tmp_path / "export").exists()


def test_corrupt_copy_never_produces_completed_export(source, tmp_path, monkeypatch):
    _, _, store = source
    monkeypatch.setattr(local_export.shutil, "copyfile", lambda _, target: target.write_bytes(b"bad copy"))
    target = tmp_path / "export"
    with pytest.raises(ValueError, match="Copied file"):
        local_export.export_batch(store, [("prod-test", "01-test")], target)
    assert (target / "INCOMPLETE.txt").is_file()
    assert not (target / "results.json").exists()
