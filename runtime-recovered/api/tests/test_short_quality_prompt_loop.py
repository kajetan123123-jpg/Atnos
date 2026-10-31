from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from atnos.production.short_quality_prompt_loop import (
    choose_champion,
    evaluate_short_artifacts,
    review_recommendation,
)


def _write_fixture(root: Path, *, personal: bool, unique_tiles: bool) -> None:
    root.mkdir(parents=True)
    base = "You earn five dollars and your balance grows because you keep your money working. "
    impersonal = "The balance grows five percent and the account receives 5 dollars. "
    text = (base if personal else impersonal) * 2
    segments = [{"id": f"s{index}", "text": line.strip()} for index, line in enumerate([text] * 4)]
    (root / "script.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
    (root / "golden_qa.json").write_text(
        json.dumps(
            {
                "status": "technical_pass",
                "scene_count": 4,
                "render": {"duration_s": 40, "width": 1080, "height": 1920, "fps": 30},
                "renderer_integrity": {
                    "unique_composition_count": 4 if unique_tiles else 1,
                    "custom_graphic_scene_count": 4 if unique_tiles else 0,
                },
            }
        ),
        encoding="utf-8",
    )
    image = Image.new("RGB", (400, 100), "black")
    draw = ImageDraw.Draw(image)
    for index in range(4):
        color = (40 + index * 50, 180 - index * 35, 60 + index * 30) if unique_tiles else (80, 80, 80)
        draw.rectangle((index * 100, 0, (index + 1) * 100, 100), fill=color)
        if unique_tiles:
            draw.ellipse((index * 100 + 10, 10, index * 100 + 30 + index * 10, 80), fill="white")
    image.save(root / "qa_contact_sheet.jpg")
    (root / "media_qa.json").write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "final_short.mp4").write_bytes(b"fixture-video")


def test_candidate_wins_only_when_verified_and_better(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    _write_fixture(baseline_dir, personal=False, unique_tiles=False)
    _write_fixture(candidate_dir, personal=True, unique_tiles=True)

    baseline = evaluate_short_artifacts(baseline_dir)
    candidate = evaluate_short_artifacts(candidate_dir)

    assert baseline.status == "failed"
    assert candidate.status == "partially_verified"
    assert choose_champion(baseline, candidate) == "candidate"


def test_candidate_with_digits_in_spoken_text_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _write_fixture(root, personal=False, unique_tiles=True)

    score = evaluate_short_artifacts(root)

    assert score.status == "failed"
    assert "spoken_numbers_not_tts_safe" in score.blockers
    assert choose_champion(score, score) == "baseline"


def test_missing_media_evidence_cannot_pass(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _write_fixture(root, personal=True, unique_tiles=True)
    (root / "media_qa.json").unlink()
    score = evaluate_short_artifacts(root)
    assert score.status == "failed"
    assert "technical_media_qa_failed" in score.blockers


def test_self_declared_diversity_does_not_override_observed_frames(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    _write_fixture(root, personal=True, unique_tiles=False)
    path = root / "golden_qa.json"
    payload = json.loads(path.read_text())
    payload["renderer_integrity"]["unique_composition_count"] = 100
    path.write_text(json.dumps(payload))
    score = evaluate_short_artifacts(root)
    assert score.visual_diversity == 2.5
    assert score.evidence["visual_review_required"] is True


def test_visual_review_requires_explicit_apply() -> None:
    assert review_recommendation(
        {
            "status": "review_complete",
            "review_text": "Final recommendation: REVIEW_FIRST",
        }
    ) == "REVIEW_FIRST"
    assert review_recommendation(
        {
            "status": "review_complete",
            "review_text": "Review options are APPLY or SKIP. Final recommendation: APPLY",
        }
    ) == "APPLY"
    assert review_recommendation(
        {
            "status": "review_complete",
            "review_text": "Final recommendation: APPLY (z poprawkami)",
        }
    ) == "REVIEW_FIRST"
    assert review_recommendation(
        {
            "status": "review_complete",
            "review_text": (
                "Pięć konkretnych zmian do zrobienia. Materiał wymaga drobnych poprawek wizualnych. "
                "Po poprawach będzie lepszy. Finalna rekomendacja: APPLY"
            ),
        }
    ) == "REVIEW_FIRST"
    assert review_recommendation({"status": "failed", "review_text": "APPLY"}) == "INSUFFICIENT_EVIDENCE"


def test_explicit_verdict_is_not_overridden_by_explanatory_keywords():
    assert review_recommendation({"status": "review_complete", "review_text":
        "FINAL_DECISION: REVIEW_FIRST\nWhy: SKIP would be too harsh. Do not APPLY yet."}) == "REVIEW_FIRST"
    assert review_recommendation({"status": "review_complete", "review_text":
        "One could APPLY or SKIP this sample."}) == "INSUFFICIENT_EVIDENCE"
