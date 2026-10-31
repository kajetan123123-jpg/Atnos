"""Comfy runtime reliability stress: STOP -> START -> HEALTH -> first generation."""
from __future__ import annotations

import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PORT = 8188
CYCLES = 10
APP = Path(r"E:\Atnos\App")
START = APP / "Start-ComfyUI.ps1"
STOP = APP / "Stop-ComfyUI.ps1"
OUT = Path(r"E:\Atnos\Storage\Data\comfy-reliability")
CKPT = "sd_xl_turbo_1.0_fp16.safetensors"
REQUIRED_NODES = (
    "CheckpointLoaderSimple",
    "KSampler",
    "LoadImage",
    "VAEEncode",
    "EmptyLatentImage",
)


def http_get(path: str, timeout: float = 10.0) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def http_post(path: str, body: str, timeout: float = 30.0) -> tuple[int, str]:
    data = body.encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def port_owners() -> list[dict]:
    ps = r"""
$rows=@()
Get-NetTCPConnection -LocalPort 8188 -State Listen -EA SilentlyContinue | ForEach-Object {
  $cim=Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -EA SilentlyContinue
  $cmd=[string]$cim.CommandLine
  $managed=($cmd -match 'disable-all-custom-nodes' -and $cmd -match 'extra-model-paths-config')
  $rows += [pscustomobject]@{pid=$_.OwningProcess; managed=[bool]$managed; argv=$cmd}
}
$rows | ConvertTo-Json -Compress
"""
    raw = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command", ps],
        text=True,
        encoding="utf-8",
        errors="replace",
    ).strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        return [data]
    return list(data)


def run_ps(script: Path, extra: list[str] | None = None, timeout: int = 200) -> tuple[int, float, str]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Port",
        str(PORT),
    ]
    if extra:
        cmd.extend(extra)
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, time.perf_counter() - t0, (proc.stdout or "") + (proc.stderr or "")


def full_health() -> tuple[bool, str]:
    try:
        status, _ = http_get("/system_stats", timeout=5)
        if status != 200:
            return False, "system_stats"
        for name in REQUIRED_NODES:
            status, body = http_get(f"/object_info/{name}", timeout=10)
            if status != 200 or name not in body:
                return False, f"missing_{name}"
        status, body = http_get("/object_info/CheckpointLoaderSimple", timeout=10)
        if CKPT not in body:
            return False, f"missing_checkpoint:{CKPT}"
        status, _ = http_get("/queue", timeout=5)
        if status != 200:
            return False, "queue"
        owners = port_owners()
        if not any(o.get("managed") for o in owners):
            return False, "port_not_managed"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def first_generation(cycle: int) -> tuple[bool, int, str]:
    seed = 1000 + cycle
    payload = {
        "prompt": {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CKPT}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": f"reliability {cycle}", "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 64, "height": 64, "batch_size": 1}},
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 1,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": f"atnos_reliability_c{cycle}", "images": ["6", 0]},
            },
        },
        "client_id": f"atnos-reliability-{cycle}",
    }
    status, body = http_post("/prompt", json.dumps(payload), timeout=60)
    ok = 200 <= status < 300
    if not ok:
        err_dir = OUT / "http400_bodies"
        err_dir.mkdir(parents=True, exist_ok=True)
        (err_dir / f"cycle_{cycle}_status_{status}.txt").write_text(body, encoding="utf-8")
    return ok, status, body


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    print(f"=== COMFY RELIABILITY STRESS cycles={CYCLES} ===", flush=True)
    for i in range(1, CYCLES + 1):
        print(f"\n----- CYCLE {i} / {CYCLES} -----", flush=True)
        cycle_t0 = time.perf_counter()
        stop_code, _, stop_out = run_ps(STOP, timeout=60)
        print(stop_out.strip(), flush=True)
        start_code, startup_sec, start_out = run_ps(
            START, extra=["-ReadyTimeoutSec", "180"], timeout=220
        )
        print(start_out.strip(), flush=True)
        t1 = time.perf_counter()
        health_ok, health_reason = full_health()
        health_sec = time.perf_counter() - t1
        gen_ok, gen_status, gen_body = (False, 0, "skipped")
        if health_ok:
            gen_ok, gen_status, gen_body = first_generation(i)
        owners = port_owners()
        managed = any(o.get("managed") for o in owners)
        passed = start_code == 0 and health_ok and gen_ok and managed
        row = {
            "cycle": i,
            "pass": passed,
            "stop_exit": stop_code,
            "start_exit": start_code,
            "startup_sec": round(startup_sec, 1),
            "health_ok": health_ok,
            "health_reason": health_reason,
            "health_sec": round(health_sec, 2),
            "first_gen_status": gen_status,
            "first_gen_ok": gen_ok,
            "managed_owner": managed,
            "owners": owners,
            "cycle_sec": round(time.perf_counter() - cycle_t0, 1),
            "first_gen_body_snip": (gen_body or "")[:300],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        results.append(row)
        print(
            f"RESULT cycle={i} pass={passed} startup={row['startup_sec']}s "
            f"health={health_reason} first_gen={gen_status} managed={managed}",
            flush=True,
        )

    startups = sorted(r["startup_sec"] for r in results)
    median = statistics.median(startups) if startups else 0
    pass_count = sum(1 for r in results if r["pass"])
    first_ok = sum(1 for r in results if r["first_gen_ok"])
    desktop_clean = all(r["managed_owner"] for r in results)
    summary = {
        "cycles": CYCLES,
        "pass": pass_count,
        "first_generation_success": f"{first_ok} / {CYCLES}",
        "median_startup_sec": median,
        "desktop_race_resolved": desktop_clean,
        "results": results,
    }
    (OUT / "stress_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===", flush=True)
    print(f"Restart cycles: {pass_count} / {CYCLES}", flush=True)
    print(f"First-generation success: {first_ok} / {CYCLES}", flush=True)
    print(f"Median startup: {median}s", flush=True)
    print(f"Desktop race clean: {desktop_clean}", flush=True)
    return 0 if pass_count == CYCLES else 1


if __name__ == "__main__":
    raise SystemExit(main())
