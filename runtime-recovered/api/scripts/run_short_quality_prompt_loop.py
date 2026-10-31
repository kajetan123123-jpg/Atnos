from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

import requests

API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.short_quality_prompt_loop import (
    build_review_prompt,
    choose_champion,
    evaluate_short_artifacts,
    review_recommendation,
)


USER_FEEDBACK = (
    "Use natural personal narration, a meaningful 40-year horizon, and an honest high-stakes hook. "
    "The voice must not sound breathy or wavering. "
    "Pronounce every number naturally. Reject generic AI-looking visuals, irrelevant frames, repeated shots, "
    "frame jumps, and brightness flashes."
)


def _run_atnos_review(*, api_url: str, root: Path, prompt: str) -> dict[str, Any]:
    response = requests.post(
        f"{api_url.rstrip('/')}/agent/visual-qa/review",
        json={
            "goal": prompt[:2000],
            "area": "Wealth Blueprint Short quality loop",
            "extra_context": prompt[-4000:],
            "screenshot_path": str((root / "qa_contact_sheet.jpg").resolve()),
            "recording_path": str((root / "assets" / "final_short.mp4").resolve()),
            "capture_screenshot": False,
            "allow_cloud_review": True,
        },
        timeout=360,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("visual_qa") if isinstance(payload, dict) else {"status": "invalid_response"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded evidence-based quality loop for a rendered Short.")
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--skip-atnos-review", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    baseline = evaluate_short_artifacts(args.baseline_dir)
    candidate = evaluate_short_artifacts(args.candidate_dir)
    prompt = build_review_prompt(candidate, user_feedback=USER_FEEDBACK)
    (output_dir / "critic_prompt.md").write_text(prompt, encoding="utf-8")
    review: dict[str, Any] = {"status": "not_started"}
    if not args.skip_atnos_review:
        try:
            review = _run_atnos_review(api_url=args.api_url, root=args.candidate_dir.resolve(), prompt=prompt)
        except Exception as exc:
            review = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    champion = choose_champion(baseline, candidate)
    recommendation = review_recommendation(review)
    report = {
        "status": "partially_verified",
        "review_scope": "Technical heuristics and AI visual review do not establish audience appeal or audio intelligibility.",
        "created_at": datetime.now(UTC).isoformat(),
        "bounded_iterations": 1,
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
        "atnos_review": review,
        "atnos_recommendation": recommendation,
        "champion": champion,
        "public_upload": False,
        "rollback": "Keep baseline_dir unchanged and select baseline as champion.",
    }
    report_path = output_dir / "quality_loop_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "champion": champion, "status": report["status"]}, indent=2))


if __name__ == "__main__":
    main()
