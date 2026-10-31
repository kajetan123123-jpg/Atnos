"""Run DC visual QC fixture suite → metrics for the report."""

from __future__ import annotations

import json
import time
from pathlib import Path

from atnos.production.comfy_channels.dark_chronicles_visual_qc import (
    DcVisualQcPolicy,
    GoreProfile,
    adjust_request_for_failures,
    run_dc_visual_qc,
    write_dc_qc_fixture,
)
from atnos.production.comfy_channels.models import GenerationRequest

OUT = Path(r"E:\Atnos\App\data\generated\_dc_visual_qc")
GOOD = ("corridor", "forest", "motel", "basement", "house_exterior")
BAD = ("cartoon", "random_text", "overexposed", "excessive_gore")


def main() -> int:
    fixtures = OUT / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    for kind in (*GOOD, *BAD):
        write_dc_qc_fixture(kind, fixtures / f"{kind}.png")

    policy = DcVisualQcPolicy(gore_profile=GoreProfile.NONE, allow_ocr=True, allow_vision=False)
    rows = []
    t0 = time.perf_counter()
    for kind in (*GOOD, *BAD):
        started = time.perf_counter()
        result = run_dc_visual_qc(fixtures / f"{kind}.png", policy=policy)
        rows.append(
            {
                "kind": kind,
                "expected_pass": kind in GOOD,
                "passed": result.passed,
                "verdict": result.verdict,
                "failure_reasons": result.failure_reasons,
                "gore_detected": result.gore_detected,
                "categories": [c.as_dict() for c in result.categories],
                "seconds": round(time.perf_counter() - started, 4),
            }
        )
    elapsed = time.perf_counter() - t0

    good_rows = [r for r in rows if r["expected_pass"]]
    bad_rows = [r for r in rows if not r["expected_pass"]]
    good_pass = sum(1 for r in good_rows if r["passed"]) / max(len(good_rows), 1)
    bad_detect = sum(1 for r in bad_rows if not r["passed"]) / max(len(bad_rows), 1)
    false_pos = sum(1 for r in good_rows if not r["passed"]) / max(len(good_rows), 1)

    # Simulate quality loop on first bad (cartoon): max 3 adjustments — still fail without regen pixels
    req = GenerationRequest(channel="dark_chronicles", workflow="DC_LOCATION", subject="pastel room", seed=1)
    qc0 = run_dc_visual_qc(fixtures / "cartoon.png", policy=policy, request=req)
    retries = 0
    current = req
    while not qc0.passed and retries < 3:
        current = adjust_request_for_failures(current, qc0, attempt=retries)
        retries += 1
        # Fixtures do not regenerate pixels — loop counts policy only
        break

    summary = {
        "good_pass_rate": round(good_pass, 4),
        "bad_detection_rate": round(bad_detect, 4),
        "false_positive_rate": round(false_pos, 4),
        "generation_retries_policy_max": 3,
        "simulated_adjust_retries": retries,
        "average_qc_seconds": round(elapsed / max(len(rows), 1), 4),
        "average_accepted_asset_time_note": (
            "QC-only seconds on fixtures; live accepted asset time = gen_time + qc_time "
            "(see ChannelComfyGenerationService DC loop, max 3 gens)."
        ),
        "rows": rows,
    }
    (OUT / "fixture_suite_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in summary if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
