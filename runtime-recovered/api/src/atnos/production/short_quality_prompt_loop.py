from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image, ImageStat


DIRECT_ADDRESS_RE = re.compile(r"\b(?:you|your|yours|yourself)\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")
REVIEW_RECOMMENDATION_RE = re.compile(r"\b(APPLY|REVIEW_FIRST|SKIP)\b", re.IGNORECASE)
FINAL_DECISION_RE = re.compile(r"\b(?:FINAL_DECISION|Final recommendation|Finalna rekomendacja)\s*:\s*(APPLY|REVIEW_FIRST|SKIP)\b", re.IGNORECASE)
CONDITIONAL_APPLY_RE = re.compile(
    r"APPLY\s*(?:\([^)]*(?:changes|corrections|popraw)[^)]*\)|(?:WITH|Z)\s+(?:CHANGES|CORRECTIONS|POPRAW))",
    re.IGNORECASE,
)
REQUIRED_REPAIRS_RE = re.compile(
    r"(?:"
    r"(?:wymaga|wymagane|należy|trzeba)\b[^.\n]{0,100}\b(?:zmian|popraw|napraw|usun|doda)"
    r"|\b(?:zmiany|poprawki|naprawy)\s+do\s+zrobienia\b"
    r"|\bpo\s+(?:tych\s+)?popraw(?:ach|kach)\b"
    r"|\b(?:must|required|needs?\s+to)\b[^.\n]{0,100}\b(?:change|fix|repair|replace|remove|add)"
    r"|\bafter\s+(?:these\s+)?(?:changes|fixes|repairs)\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ShortQualityScore:
    status: str
    overall: float
    script: float
    visual_diversity: float
    technical: float
    blockers: tuple[str, ...]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_short_artifacts(root: Path) -> ShortQualityScore:
    root = root.resolve()
    script = _load_json(root / "script.json")
    qa = _load_json(root / "golden_qa.json")
    media_qa = _load_optional_json(root / "media_qa.json")
    segments = script.get("segments") if isinstance(script.get("segments"), list) else []
    spoken = " ".join(str(item.get("text") or "") for item in segments if isinstance(item, dict)).strip()
    scene_count = max(1, int(qa.get("scene_count") or len(segments) or 1))

    direct_address_count = len(DIRECT_ADDRESS_RE.findall(spoken))
    words = spoken.split()
    sentences = [part.strip() for part in SENTENCE_RE.findall(spoken) if part.strip()]
    sentence_lengths = [len(sentence.split()) for sentence in sentences]
    safe_for_tts = not any(marker in spoken for marker in ("$", "%")) and not re.search(r"\d", spoken)
    natural_sentence_ratio = (
        sum(4 <= length <= 20 for length in sentence_lengths) / len(sentence_lengths)
        if sentence_lengths
        else 0.0
    )
    word_count_score = _band_score(len(words), ideal_min=105, ideal_max=135, hard_min=80, hard_max=160)
    script_score = round(
        10
        * (
            0.10 * min(1.0, direct_address_count / 2)
            + 0.25 * float(safe_for_tts)
            + 0.30 * natural_sentence_ratio
            + 0.35 * word_count_score
        ),
        2,
    )

    renderer = qa.get("renderer_integrity") if isinstance(qa.get("renderer_integrity"), dict) else {}
    contact_sheet_columns = int(renderer.get("contact_sheet_columns") or 4)
    visual = _contact_sheet_diversity(
        root / "qa_contact_sheet.jpg",
        scene_count=scene_count,
        columns=contact_sheet_columns,
    )
    declared_compositions = int(renderer.get("unique_composition_count") or 0)
    custom_graphics = int(renderer.get("custom_graphic_scene_count") or 0)
    visual_ratio = float(visual["unique_ratio"])
    visual_score = round(10 * visual_ratio, 2)

    render = qa.get("render") if isinstance(qa.get("render"), dict) else {}
    duration = float(render.get("duration_s") or 0)
    width = int(render.get("width") or 0)
    height = int(render.get("height") or 0)
    fps = float(render.get("fps") or 0)
    media_status = str(media_qa.get("status") or media_qa.get("overall") or "").casefold()
    technical_checks = {
        "qa_pass": str(qa.get("status") or "").casefold() in {"technical_pass", "passed", "verified"},
        "duration": 35 <= duration <= 45,
        "vertical_hd": width == 1080 and height == 1920,
        "fps": 29 <= fps <= 31,
        "media_qa": bool(media_qa) and media_status in {"passed", "pass", "verified", "technical_pass"},
        "video_exists": (root / "assets" / "final_short.mp4").is_file(),
        "contact_sheet_exists": visual["status"] == "measured",
    }
    technical_score = round(10 * sum(technical_checks.values()) / len(technical_checks), 2)

    blockers: list[str] = []
    if word_count_score == 0:
        blockers.append("script_length_outside_short_budget")
    if not safe_for_tts:
        blockers.append("spoken_numbers_not_tts_safe")
    for name, passed in technical_checks.items():
        if not passed:
            blockers.append(f"technical_{name}_failed")

    overall = round(0.30 * script_score + 0.35 * visual_score + 0.35 * technical_score, 2)
    return ShortQualityScore(
        status="partially_verified" if not blockers else "failed",
        overall=overall,
        script=script_score,
        visual_diversity=visual_score,
        technical=technical_score,
        blockers=tuple(blockers),
        evidence={
            "root": str(root),
            "word_count": len(words),
            "narration_transcript": spoken,
            "direct_address_count": direct_address_count,
            "tts_safe": safe_for_tts,
            "natural_sentence_ratio": round(natural_sentence_ratio, 3),
            "contact_sheet": visual,
            "declared_unique_compositions": declared_compositions,
            "custom_graphic_scene_count": custom_graphics,
            "technical_checks": technical_checks,
            "score_scope": "Technical checks and editorial heuristics only; not an audience or aesthetic rating",
            "visual_review_required": True,
            "warnings": ["similar_compositions_require_contextual_review"] if visual_ratio < 0.65 else [],
        },
    )


def choose_champion(
    baseline: ShortQualityScore,
    candidate: ShortQualityScore,
    *,
    minimum_gain: float = 0.2,
) -> str:
    if candidate.status not in {"verified", "partially_verified"} or candidate.blockers:
        return "baseline"
    if baseline.status not in {"verified", "partially_verified"} or baseline.blockers:
        return "candidate"
    return "candidate" if candidate.overall >= baseline.overall + minimum_gain else "baseline"


def review_recommendation(review: dict[str, Any]) -> str:
    if str(review.get("status") or "").casefold() != "review_complete":
        return "INSUFFICIENT_EVIDENCE"
    review_text = str(review.get("review_text") or "")
    if CONDITIONAL_APPLY_RE.search(review_text) or REQUIRED_REPAIRS_RE.search(review_text):
        return "REVIEW_FIRST"
    matches = FINAL_DECISION_RE.findall(review_text.replace("**", ""))
    return matches[-1].upper() if matches else "INSUFFICIENT_EVIDENCE"


def build_review_prompt(score: ShortQualityScore, *, user_feedback: str) -> str:
    return (
        "You are the strict visual director for a 35-45 second Wealth Blueprint YouTube Short.\n"
        "Judge only observable evidence. Do not reward generic stock footage, random phone numbers, "
        "repeated shots, fake UI, AI-looking imagery, brightness jumps, or decorative frames unrelated "
        "to narration. Prefer direct second-person language, exact TTS-safe number pronunciation, human "
        "actions, and bespoke arithmetic graphics.\n\n"
        "Your visual evidence is a contact sheet. It cannot establish audio delivery or animation smoothness. "
        "Use the supplied narration transcript for wording. Count-up frames can contain intermediate values; "
        "check final values before calling arithmetic wrong. A continuing curve is intentional continuity, "
        "not necessarily repeated footage. Cite the specific visible scene for every finding and do not invent values.\n\n"
        f"User feedback:\n{user_feedback.strip()}\n\n"
        f"Deterministic score:\n{json.dumps(score.to_dict(), ensure_ascii=False, indent=2)}\n\n"
        "Return: biggest problem, up to five concrete repairs, and what to preserve. End with exactly "
        "FINAL_DECISION: APPLY only when no repair is required; FINAL_DECISION: REVIEW_FIRST when any "
        "repair is required; or FINAL_DECISION: SKIP when the candidate should be rejected."
    )


def _contact_sheet_diversity(path: Path, *, scene_count: int, columns: int = 4) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "missing", "unique_count": 0, "scene_count": scene_count, "unique_ratio": 0.0}
    image = Image.open(path).convert("RGB")
    columns = max(1, columns)
    rows = max(1, (scene_count + columns - 1) // columns)
    tile_width = image.width // columns
    tile_height = image.height // rows
    signatures: list[tuple[int, ...]] = []
    for index in range(scene_count):
        x = (index % columns) * tile_width
        y = (index // columns) * tile_height
        tile = image.crop((x, y, x + tile_width, y + tile_height)).resize((16, 16)).convert("L")
        mean = ImageStat.Stat(tile).mean[0]
        signatures.append(tuple(1 if pixel >= mean else 0 for pixel in tile.getdata()))
    clusters: list[tuple[int, ...]] = []
    for signature in signatures:
        if not any(_hamming(signature, existing) <= 18 for existing in clusters):
            clusters.append(signature)
    unique_count = len(clusters)
    return {
        "status": "measured",
        "unique_count": unique_count,
        "scene_count": scene_count,
        "unique_ratio": round(unique_count / scene_count, 3),
    }


def _hamming(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return sum(a != b for a, b in zip(left, right, strict=True))


def _band_score(value: int, *, ideal_min: int, ideal_max: int, hard_min: int, hard_max: int) -> float:
    if ideal_min <= value <= ideal_max:
        return 1.0
    if value < hard_min or value > hard_max:
        return 0.0
    if value < ideal_min:
        return (value - hard_min) / (ideal_min - hard_min)
    return (hard_max - value) / (hard_max - ideal_max)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _load_optional_json(path: Path) -> dict[str, Any]:
    return _load_json(path) if path.is_file() else {}
