from __future__ import annotations

from pathlib import Path
import re
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.steps.assembler import (  # noqa: E402
    ASS_SUBTITLE_FONT_SIZE,
    ASS_SUBTITLE_MAX_DURATION_S,
    ASS_SUBTITLE_MAX_WORDS,
    generate_ass_subtitles,
)


def test_generate_ass_subtitles_uses_short_large_mobile_cues(tmp_path: Path) -> None:
    output = tmp_path / "subtitles.ass"

    generate_ass_subtitles(
        [
            {
                "start": 0.0,
                "end": 4.2,
                "text": "Most people do not lose money because they are lazy they lose it because the system is quiet",
            }
        ],
        output,
    )

    text = output.read_text(encoding="utf-8")
    assert f"Montserrat ExtraBold,{ASS_SUBTITLE_FONT_SIZE}" in text
    assert r"\fad(80,60)" in text
    assert r"\pos(540,1510)" in text
    dialogue_lines = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue_lines) >= 4
    for line in dialogue_lines:
        parts = line.split(",", maxsplit=9)
        start = _ass_seconds(parts[1])
        end = _ass_seconds(parts[2])
        cue_text = re.sub(r"\{[^}]*\}", "", parts[-1]).replace(r"\N", " ")
        assert len(cue_text.split()) <= ASS_SUBTITLE_MAX_WORDS
        assert end - start <= ASS_SUBTITLE_MAX_DURATION_S + 0.02


def _ass_seconds(value: str) -> float:
    hours, minutes, seconds = value.split(":", maxsplit=2)
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
