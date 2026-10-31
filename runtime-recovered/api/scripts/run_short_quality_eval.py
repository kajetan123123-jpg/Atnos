from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.core.config import Settings  # noqa: E402
from content.script_generator import ScriptGenerator, ShortScript  # noqa: E402
from content.script_quality_gate import ScriptQualityGate  # noqa: E402


DEFAULT_CASES_PATH = API_ROOT / "data" / "quality" / "short_eval_cases.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic short script quality evals.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--live", action="store_true", help="Use the configured Ollama generator instead of fallback scripts.")
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings()
    min_score = float(args.min_score if args.min_score is not None else settings.shorts_min_quality_score)
    report = run_eval(
        settings=settings,
        cases_path=args.cases,
        live=bool(args.live),
        min_score=min_score,
    )

    output_path = args.output or _default_output_path(settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Short quality eval: {report['summary']['passed']}/{report['summary']['total']} passed")
    print(f"Average score: {report['summary']['average_score']}")
    print(f"Report: {output_path}")
    return 0 if report["summary"]["failed"] == 0 else 1


def run_eval(*, settings: Settings, cases_path: Path, live: bool, min_score: float) -> dict[str, Any]:
    cases = _load_cases(cases_path)
    generator = ScriptGenerator(settings)
    gate = ScriptQualityGate(min_score=min_score)
    results: list[dict[str, Any]] = []

    for case in cases:
        topic = str(case["topic"])
        try:
            script = generator.generate(topic=topic, brief=generator.build_brief(topic)) if live else _offline_script(topic)
            quality = gate.evaluate(script=script, topic=topic)
            results.append(
                {
                    "id": case["id"],
                    "topic": topic,
                    "expected_angle": case.get("expected_angle"),
                    "status": "PASSED" if quality.passed else "FAILED",
                    "score": quality.overall_score,
                    "quality": quality.to_dict(),
                    "script": script.to_dict(),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": case.get("id", topic),
                    "topic": topic,
                    "expected_angle": case.get("expected_angle"),
                    "status": "FAILED",
                    "score": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    scores = [float(item.get("score") or 0.0) for item in results]
    passed = sum(1 for item in results if item["status"] == "PASSED")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "live_ollama" if live else "offline_fallback",
        "min_score": min_score,
        "cases_path": str(cases_path),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "average_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        },
        "results": results,
    }


def _load_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Short eval cases file must contain a JSON list.")
    cases: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case #{index} must be an object.")
        if not str(item.get("id") or "").strip():
            raise ValueError(f"Case #{index} is missing id.")
        if not str(item.get("topic") or "").strip():
            raise ValueError(f"Case #{index} is missing topic.")
        cases.append(item)
    return cases


def _offline_script(topic: str) -> ShortScript:
    return ScriptGenerator._automation_fallback_script(topic)


def _default_output_path(settings: Settings) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return settings.data_dir / "reports" / f"short_quality_eval_{timestamp}.json"


if __name__ == "__main__":
    raise SystemExit(main())
