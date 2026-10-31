from __future__ import annotations

from pathlib import Path

from PIL import Image

from youtube.youtube_metadata import (
    build_thumbnail_upload_descriptor,
    build_upload_body,
    generate_yt_description,
    validate_thumbnail_for_youtube,
)


def test_generate_description_keeps_keyword_in_preview_and_forces_english() -> None:
    description = generate_yt_description(
        topic="finanse osobiste",
        hook="Tego bledu z pieniedzmi wiekszosc osob nie widzi",
        tags=["finanse", "shorts", "edukacja", "pieniadze", "oszczedzanie", "extra"],
        language="pl",
    )

    assert "finanse osobiste" in description[:125].lower()
    assert "Topic:" in description
    assert "Subscribe" in description
    assert "Subskrybuj" not in description
    assert "#Shorts" in description
    assert "#Polska" not in description
    assert len(description) <= 5000


def test_build_upload_body_truncates_and_normalizes() -> None:
    body = build_upload_body(
        title="x" * 140,
        description="d" * 6000,
        tags=["AI", "AI", "bardzo dlugi tag"],
        privacy="invalid",
    )

    assert len(body["snippet"]["title"]) == 100
    assert len(body["snippet"]["description"]) == 5000
    assert body["snippet"]["tags"] == ["AI", "bardzo dlugi tag"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False


def test_validate_thumbnail_accepts_youtube_sized_image(tmp_path: Path) -> None:
    thumb_path = tmp_path / "thumbnail.png"
    Image.new("RGB", (1280, 720), color=(10, 10, 10)).save(thumb_path)

    result = validate_thumbnail_for_youtube(thumb_path)

    assert result["valid"] is True
    assert result["width"] == 1280
    assert result["height"] == 720


def test_thumbnail_descriptor_rejects_missing_video_id(tmp_path: Path) -> None:
    thumb_path = tmp_path / "thumbnail.png"
    Image.new("RGB", (1280, 720), color=(10, 10, 10)).save(thumb_path)

    result = build_thumbnail_upload_descriptor("", thumb_path)

    assert result["valid"] is False
    assert "missing_video_id" in result["issues"]
