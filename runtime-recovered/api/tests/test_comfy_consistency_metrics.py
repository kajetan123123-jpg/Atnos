"""Unit tests for consistency metrics (no Comfy)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from atnos.production.comfy_channels.consistency_metrics import compare_to_anchor, mean_scores


def test_compare_identical_images(tmp_path: Path) -> None:
    path = tmp_path / "a.png"
    Image.new("RGB", (128, 128), (40, 80, 120)).save(path)
    scores = compare_to_anchor(path, path)
    assert scores["perceptual_mse_sim"] > 0.99
    assert scores["identity_proxy"] > 0.7


def test_mean_scores_empty() -> None:
    assert mean_scores([])["identity_proxy"] == 0.0
