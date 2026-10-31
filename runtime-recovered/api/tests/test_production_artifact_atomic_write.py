from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading

import pytest

from atnos.production.steps import common


def test_reader_sees_previous_complete_artifact_until_replace(tmp_path, monkeypatch):
    path = tmp_path / "script.json"
    previous = {"text": "Poprzedni poprawny scenariusz"}
    next_payload = {"text": "Pełny następny scenariusz", "version": 2}
    common.write_json(path, previous)
    replace = common.os.replace

    def checked_replace(source, destination):
        assert Path(source).parent == path.parent
        assert json.loads(path.read_text(encoding="utf-8")) == previous
        assert json.loads(Path(source).read_text(encoding="utf-8")) == next_payload
        replace(source, destination)

    monkeypatch.setattr(common.os, "replace", checked_replace)
    result = common.write_json(path, next_payload)
    assert result.artifact_path == path
    assert result.payload == next_payload
    assert json.loads(path.read_text(encoding="utf-8")) == next_payload
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_failed_write_preserves_previous_bytes_and_reports_error(tmp_path, monkeypatch, failure_point):
    path = tmp_path / "script.json"
    common.write_json(path, {"version": 1})
    original = path.read_bytes()

    def disk_failure(*args):
        raise OSError("Simulated interrupted disk operation")

    monkeypatch.setattr(common.os, failure_point, disk_failure)
    with pytest.raises(OSError, match="interrupted"):
        common.write_json(path, {"version": 2, "large": "x" * 10000})
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_invalid_payload_cannot_truncate_existing_artifact(tmp_path):
    path = tmp_path / "script.json"
    common.write_json(path, {"valid": True})
    original = path.read_bytes()
    with pytest.raises(TypeError):
        common.write_json(path, {"invalid": object()})
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_writers_use_distinct_staging_files(tmp_path, monkeypatch):
    path = tmp_path / "script.json"
    common.write_json(path, {"version": 0})
    barrier = threading.Barrier(2, timeout=10)
    staging = []
    replace = common.os.replace

    def wait_before_publish(source, destination):
        staging.append(source)
        barrier.wait()
        replace(source, destination)

    monkeypatch.setattr(common.os, "replace", wait_before_publish)
    versions = [{"version": 1, "text": "a" * 5000}, {"version": 2, "text": "b" * 5000}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(common.write_json, path, value) for value in versions]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except PermissionError:
                # Windows may reject a concurrent rename; that failure must be
                # reported, not represented by a successful StepResult.
                pass
    assert results
    assert len(set(staging)) == 2
    assert json.loads(path.read_text()) in [result.payload for result in results]
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows extended filesystem path")
@pytest.mark.parametrize("interrupted", [False, True])
def test_long_evidence_directory_preserves_atomic_artifact(tmp_path, monkeypatch, interrupted):
    # Exercise a real MAX_PATH-sized directory, not a mocked path conversion.
    path = tmp_path / ("production-evidence-" * 4) / ("a" * 64) / ("b" * 64) / "comparison-format-2-validated.json"
    assert len(str(path.absolute())) > 300
    common.write_json(path, {"version": 1})
    readable = Path("\\\\?\\" + str(path.absolute()))
    if interrupted:
        def disk_failure(*args):
            raise OSError("Interrupted long-path artifact")
        monkeypatch.setattr(common.os, "fsync", disk_failure)
        with pytest.raises(OSError, match="Interrupted long-path"):
            common.write_json(path, {"version": 2})
        expected = 1
    else:
        result = common.write_json(path, {"version": 2})
        assert result.artifact_path == path
        expected = 2
    assert json.loads(readable.read_text(encoding="utf-8")) == {"version": expected}
    assert list(readable.parent.glob("*.tmp")) == []
