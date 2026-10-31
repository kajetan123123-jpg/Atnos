"""Exact process ownership for desktop lifecycle commands. Never manages models.

Ports and process names are not ownership evidence. Relative entrypoints must
resolve against the inspected process cwd, including Windows venv children.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import psutil


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("ATNOS_STATE_DIR") or os.environ.get("DATA_DIR") or
            r"E:\Atnos\Profiles\AtnosSystem\AppData\Roaming\Atnos\data")


def norm(path):
    return os.path.normcase(os.path.realpath(str(path)))


def entrypoint(argv, cwd):
    if len(argv) < 2:
        return None
    args = argv[1:]
    if Path(argv[0]).name.lower() in {"powershell.exe", "pwsh.exe"}:
        indices = [i for i, a in enumerate(args) if a.lower() == "-file"]
        return norm(Path(cwd) / args[indices[0] + 1]) if indices and indices[0] + 1 < len(args) else None
    # Only the Python script position counts, not a later argument mentioning it.
    while args and args[0] in {"-u", "-B", "-s", "-E", "-I"}:
        args = args[1:]
    return norm(Path(cwd) / args[0]) if args and not args[0].startswith("-") else None


def remotion_entry(argv, cwd, root=ROOT):
    """Resolve Node/tsx's actual script, never a matching later payload argument."""
    renderer = Path(root) / "runtime-recovered/remotion-renderer"
    if norm(cwd) != norm(renderer):
        return None
    args = list(argv[1:])
    while len(args) >= 2 and args[0] in {"--require", "-r", "--import"}:
        args = args[2:]
    if not args or args[0].startswith("-"):
        return None
    first = norm(Path(cwd) / args[0])
    modules = norm(renderer / "node_modules") + os.sep
    if first.startswith(modules) and Path(first).name == "cli.mjs" and "tsx" in Path(first).parts:
        args = args[1:]
    if not args:
        return None
    script = norm(Path(cwd) / args[0])
    if script in {norm(renderer / "scripts" / name) for name in ("render.ts", "render-browser.ts")}:
        return script, tuple(args[1:])
    return None


def classify(exe, argv, cwd, root=ROOT):
    root = Path(root)
    shell_paths = [root / n for n in ("Atnos Shell.exe", "Atnos Desktop.exe", "Atnos Portable.exe", "Atnos.exe")]
    if norm(exe) in {norm(p) for p in shell_paths}:
        return "shell"
    if norm(exe) == norm(root.parent / "Tools/NodeJS/node.exe") and remotion_entry(argv, cwd, root):
        return "render"
    if norm(exe) == norm(root.parent / "Tools/FFmpeg/bin/ffmpeg.exe") and len(argv) > 1:
        # FFmpeg's final positional output must belong to Atnos production data.
        output = norm(Path(cwd) / argv[-1])
        if not argv[-1].startswith("-") and output.startswith(norm(DATA / "production") + os.sep):
            return "render"
    entry = entrypoint(argv, cwd)
    roles = {
        norm(root / "runtime-recovered/api/scripts/run_atnos_api.py"): "api",
        norm(root / "frontend/serve_spa.py"): "web",
        norm(root / "runtime-recovered/api/scripts/run_production_order.py"): "worker",
        norm(root / "Launch-Atnos.ps1"): "launcher",
        norm(root / "AtnosRuntimeWatchdog.ps1"): "watchdog",
        norm(root / "runtime-recovered/api/scripts/atnos_api_watchdog.py"): "legacy_watchdog",
    }
    render_scripts = {
        "render_wb_visual_intent_scenes.py",
        "run_production_order.py",
        "render.py",
        "render_video.py",
        "remotion_render.py",
        "run_remotion_render.py",
    }
    if entry and entry.startswith(norm(root) + os.sep) and Path(entry).name.casefold() in render_scripts:
        role = "render" if Path(entry).name.casefold() != "run_production_order.py" else "worker"
    else:
        role = roles.get(entry)
    if role in {"api", "web", "worker", "render", "legacy_watchdog"}:
        # A shared interpreter alone never establishes ownership.
        python_roots = (root / "runtime-recovered", root.parent / "Tools", root.parent / "Storage")
        if Path(exe).name.lower() not in {"python.exe", "pythonw.exe"} or not any(
            norm(exe).startswith(norm(p) + os.sep) for p in python_roots
        ):
            return None
    elif role and Path(exe).name.lower() not in {"powershell.exe", "pwsh.exe"}:
        return None
    return role


def identity(process):
    with process.oneshot():
        return {"pid": process.pid, "created": process.create_time(),
                "role": classify(process.exe(), process.cmdline(), process.cwd())}


def inventory():
    result = []
    for process in psutil.process_iter():
        try:
            item = identity(process)
            if item["role"]:
                result.append(item)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return result


def _work_group_key(process, role):
    """Group launcher/child members without merging separate production jobs."""
    try:
        argv = [str(item) for item in process.cmdline()]
        cwd = norm(process.cwd())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return (role, process.pid)
    if role == "worker":
        for index, argument in enumerate(argv):
            if Path(argument).name.casefold() == "run_production_order.py":
                job_id = argv[index + 2] if len(argv) > index + 2 else "unknown"
                return (role, cwd, job_id)
    if role == "render":
        remotion = remotion_entry(argv, cwd)
        if remotion:
            return (role, cwd, remotion)
    return (role, cwd, tuple(argv))


def work_blockers():
    blockers = []
    seen_work = set()
    for process in psutil.process_iter():
        try:
            item = identity(process)
            role = item["role"]
            if role not in {"worker", "render"}:
                continue
            key = _work_group_key(process, role)
            if key in seen_work:
                continue
            seen_work.add(key)
            blockers.append("production_worker" if role == "worker" else "active_render")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    lock = DATA / "runtime/gpu-runtime.lock"
    if lock.exists():
        try:
            info = json.loads((lock / "owner.json").read_text(encoding="utf-8"))
            proc = psutil.Process(int(info["process_id"]))
            if abs(proc.create_time() - float(info["process_started_at_epoch"])) < 0.01:
                blockers.append("gpu_lease")
            else:
                blockers.append("gpu_lease_unknown")
        except psutil.NoSuchProcess:
            pass
        except (OSError, ValueError, KeyError, TypeError, psutil.AccessDenied):
            blockers.append("gpu_lease_unknown")
    return blockers


def stop_owned(process_id, expected_created, *, allow_api=False):
    proc = psutil.Process(process_id)
    info = identity(proc)
    if abs(info["created"] - expected_created) > 0.001 or not info["role"] or info["role"] in {"worker", "render"}:
        raise RuntimeError("Process identity changed or is not an owned service")
    if info["role"] == "api":
        if not allow_api:
            raise RuntimeError("Automatic API termination is disabled")
        blockers = work_blockers()
        if blockers:
            raise RuntimeError("API restart deferred: " + ", ".join(blockers))
        # Windows venv launcher parent does not execute Python; its child does.
        for child in proc.children():
            child_info = identity(child)
            if child_info["role"] == "api":
                proc, info = child, child_info
                break
        # The new API runner drains HTTP/stream/background requests itself.
        # An older runner cannot consume this request: timeout leaves it alive.
        target = DATA / "runtime/api-shutdown-request.json"
        if target.drive.upper() != 'E:':
            raise RuntimeError("Atnos shutdown state must remain on E:")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".shutdown-{os.getpid()}.tmp")
        staging.write_text(json.dumps({**info, "expires": time.time() + 55}), encoding="utf-8")
        os.replace(staging, target)
        try:
            proc.wait(timeout=60)
        except psutil.TimeoutExpired as exc:
            raise RuntimeError("API has not drained; left running, no forced kill") from exc
    else:
        # psutil's identity-aware terminate rechecks PID reuse before signalling.
        proc.terminate()
        proc.wait(timeout=10)
    return {"stopped": True, **info}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "list", "stop", "activity"])
    parser.add_argument("--pid", type=int)
    parser.add_argument("--created", type=float)
    parser.add_argument("--allow-api-stop", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "list":
            result = inventory()
        elif args.action == "activity":
            result = {"blockers": work_blockers()}
        elif args.action == "inspect":
            result = identity(psutil.Process(args.pid))
        else:
            if not args.pid or not args.created:
                raise RuntimeError("PID and creation time required")
            result = stop_owned(args.pid, args.created, allow_api=args.allow_api_stop)
        print(json.dumps(result))
        return 0
    except (psutil.Error, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
