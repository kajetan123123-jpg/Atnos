"""Reference-conditioned local CTC word alignment for approved preview audio."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import psutil


ROOT = Path(r"E:\Atnos\Tools\models\wav2vec2-asr-base-960h")
PYTHON = Path(r"E:\Atnos\Tools\venvs\caption-align\Scripts\python.exe")
WORKER = Path(__file__).with_name("local_ctc_alignment_worker.py")
EXPECTED_WEIGHTS_BYTES = 377664473
EXPECTED_WEIGHTS_SHA256 = "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d"
ALIGNMENT_TIMEOUT_SECONDS = 360
PROCESS_REAP_TIMEOUT_SECONDS = 5
TOOLS_ROOT = Path(r"E:\Atnos\Tools").resolve()
CLEANUP_AUDIT_PATH = Path(r"E:\Atnos\App\reports\local-ctc-alignment\cleanup-events.jsonl")


class LocalCTCAlignmentError(RuntimeError):
    pass


class _OwnedProcessTree:
    """Identity snapshot for this service's worker and descendants only."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.root_create_time: float | None = None
        self.root_exe: str | None = None
        self.children: dict[int, tuple[float, str]] = {}


def _under_tools(executable: str) -> bool:
    try:
        resolved = os.path.normcase(str(Path(executable).resolve()))
        configured_worker_python = os.path.normcase(str(PYTHON.resolve()))
        if resolved == configured_worker_python:
            return True
        Path(resolved).relative_to(TOOLS_ROOT)
        return True
    except (OSError, ValueError):
        return False


def _record_owned_process_tree(state: _OwnedProcessTree) -> None:
    """Record descendants only after verifying root PID, start time, exe, and worker argv."""
    try:
        root = psutil.Process(state.pid)
        create_time, executable = root.create_time(), root.exe()
        command = [os.path.normcase(arg) for arg in root.cmdline()]
        marker = os.path.normcase(str(WORKER.resolve()))
        if not _under_tools(executable) or marker not in command:
            return
        if state.root_create_time is None:
            state.root_create_time, state.root_exe = create_time, os.path.normcase(str(Path(executable).resolve()))
        elif create_time != state.root_create_time or os.path.normcase(str(Path(executable).resolve())) != state.root_exe:
            return
        for child in root.children(recursive=True):
            try:
                child_create, child_exe = child.create_time(), child.exe()
                if _under_tools(child_exe):
                    state.children[child.pid] = (child_create, os.path.normcase(str(Path(child_exe).resolve())))
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return


def _kill_owned_process_tree(state: _OwnedProcessTree) -> bool:
    """Kill only recorded descendants whose PID, creation time and executable still match."""
    if state.root_create_time is None or state.root_exe is None:
        return False
    owned = []
    for pid, (created, executable) in state.children.items():
        try:
            candidate = psutil.Process(pid)
            if (candidate.create_time() == created and _under_tools(candidate.exe())
                    and os.path.normcase(str(Path(candidate.exe()).resolve())) == executable):
                owned.append(candidate)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    # psutil returns descendants parent-first; terminate leaves before parents.
    for child in reversed(owned):
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass
    if owned:
        psutil.wait_procs(owned, timeout=5)
    try:
        root = psutil.Process(state.pid)
        if (root.create_time() != state.root_create_time or not _under_tools(root.exe())
                or os.path.normcase(str(Path(root.exe()).resolve())) != state.root_exe):
            return False
        root.kill()
        root.wait(timeout=5)
        return True
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, psutil.TimeoutExpired, OSError):
        return False


async def _monitor_owned_process_tree(state: _OwnedProcessTree) -> None:
    while True:
        await asyncio.to_thread(_record_owned_process_tree, state)
        await asyncio.sleep(0.2)


async def _cleanup_owned_worker(proc: asyncio.subprocess.Process, state: _OwnedProcessTree) -> None:
    await asyncio.to_thread(_record_owned_process_tree, state)
    cleaned = await asyncio.to_thread(_kill_owned_process_tree, state)
    if not cleaned:
        _persist_cleanup_failure(proc.pid, "identity_unverified")
        raise LocalCTCAlignmentError(
            f"Worker cleanup could not verify process identity; PID {proc.pid} was left untouched."
        )
    try:
        await asyncio.wait_for(proc.wait(), timeout=PROCESS_REAP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        _persist_cleanup_failure(proc.pid, "owned_process_reap_timeout")
        raise LocalCTCAlignmentError(
            f"Owned worker PID {proc.pid} did not exit within {PROCESS_REAP_TIMEOUT_SECONDS}s after cleanup."
        ) from exc


def _persist_cleanup_failure(pid: int, reason: str) -> None:
    """Durably record cleanup failures without including audio, transcript or environment data."""
    try:
        CLEANUP_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time_utc": datetime.now(timezone.utc).isoformat(), "worker_pid": pid,
                 "worker_script": str(WORKER), "reason": reason}
        with CLEANUP_AUDIT_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        # Keep the process identity and cleanup error in the raised exception if
        # the diagnostic volume itself is unavailable.
        return


def runtime_availability(*, verify_hash: bool = True) -> dict[str, Any]:
    """Check local runtime and pinned model integrity without loading the model."""
    problems = []
    if not PYTHON.is_file():
        problems.append("python_missing")
    if not WORKER.is_file():
        problems.append("worker_missing")
    weights = ROOT / "weights.pth"
    digest = None
    if not weights.is_file() or weights.stat().st_size != EXPECTED_WEIGHTS_BYTES:
        problems.append("weights_missing_or_wrong_size")
    elif verify_hash:
        h = hashlib.sha256()
        with weights.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        digest = h.hexdigest()
        if digest != EXPECTED_WEIGHTS_SHA256:
            problems.append("weights_sha256_mismatch")
    return {"available": not problems, "problems": problems, "python": str(PYTHON),
            "worker": str(WORKER), "weights": str(weights), "weights_sha256": digest,
            "weights_sha256_expected": EXPECTED_WEIGHTS_SHA256,
            "integrity_check": "full_sha256" if verify_hash else "deferred_before_inference"}


async def align(wav_path: str | Path, reference_text: str, output_path: str | Path) -> dict[str, Any]:
    """Align every reference word to audio; failures never fall back to estimates."""
    audio, output = Path(wav_path), Path(output_path)
    if not audio.is_file() or not reference_text.strip():
        raise LocalCTCAlignmentError("Audio and a non-empty reference transcript are required.")
    # Writes are limited to the pilot report tree or a DATA_DIR production job
    # directory: production/<project>/<job_id>/...; reject traversal and other drives.
    resolved_output = output.resolve()
    pilot_root = Path(r"E:\Atnos\App\reports\wb-10-dollar-pilot-20261005\local-voice\alignment").resolve()
    data_root = Path(os.environ.get("DATA_DIR") or r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data").resolve()
    in_pilot = resolved_output.is_relative_to(pilot_root)
    try:
        rel = resolved_output.relative_to(data_root)
        in_job = (len(rel.parts) >= 4 and rel.parts[0] == "production"
                  and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rel.parts[1])
                  and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", rel.parts[2]))
    except ValueError:
        in_job = False
    if resolved_output.drive.upper() != "E:" or not (in_pilot or in_job):
        raise LocalCTCAlignmentError("Alignment output must be inside an approved E: pilot or production job directory.")
    available = runtime_availability()
    if not available["available"]:
        raise LocalCTCAlignmentError(f"Local alignment runtime unavailable: {available['problems']}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(r"E:\Atnos\App\tmp\caption-align")
    temp_root.mkdir(parents=True, exist_ok=True)
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    env = {"SYSTEMROOT": system_root, "SystemRoot": system_root,
           "PATH": str(PYTHON.parent) + os.pathsep + str(Path(system_root) / "System32"),
           "TEMP": str(temp_root), "TMP": str(temp_root), "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4",
           "TORCH_HOME": str(ROOT / "torch-cache"), "HF_HOME": str(ROOT / "hf-cache"),
           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    proc = await asyncio.create_subprocess_exec(
        str(PYTHON), str(WORKER), "--audio", str(audio), "--reference", reference_text,
        "--output", str(resolved_output), cwd=str(WORKER.parent), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    tree = _OwnedProcessTree(proc.pid)
    monitor = asyncio.create_task(_monitor_owned_process_tree(tree))
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=ALIGNMENT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await _cleanup_owned_worker(proc, tree)
        raise LocalCTCAlignmentError(f"Local alignment exceeded {ALIGNMENT_TIMEOUT_SECONDS} seconds.") from exc
    except asyncio.CancelledError:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await _cleanup_owned_worker(proc, tree)
        raise
    finally:
        if not monitor.done():
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
    if proc.returncode:
        raise LocalCTCAlignmentError((stderr or stdout).decode("utf-8", "replace")[-4000:])
    try:
        return json.loads(resolved_output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalCTCAlignmentError("Alignment worker returned no valid result.") from exc
