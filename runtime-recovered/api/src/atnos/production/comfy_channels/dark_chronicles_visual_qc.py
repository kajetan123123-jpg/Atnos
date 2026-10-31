"""Dark Chronicles post-generation visual quality gate.

Prompt alone is not style protection — every DC still is checked after generation.
Categories are scored separately (no single magic score). Optional Windows OCR /
Ollama vision enhance detection when already available; no new OCR stack.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from atnos.production.comfy_channels.models import GenerationRequest

SemanticCategory = Literal[
    "CINEMATIC_COMPOSITION",
    "LIGHTING",
    "ATMOSPHERE",
    "BELIEVABLE_GEOMETRY",
    "CARTOON_LEAKAGE",
    "TEXT_ARTIFACTS",
    "GORE_LEVEL",
    "AI_ARTIFACTS",
]

SEMANTIC_CATEGORIES: tuple[SemanticCategory, ...] = (
    "CINEMATIC_COMPOSITION",
    "LIGHTING",
    "ATMOSPHERE",
    "BELIEVABLE_GEOMETRY",
    "CARTOON_LEAKAGE",
    "TEXT_ARTIFACTS",
    "GORE_LEVEL",
    "AI_ARTIFACTS",
)

MAX_DC_VISUAL_QC_ATTEMPTS = 3


class GoreProfile(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


# Allowed maximum detected gore level per request profile.
_GORE_CEILING: dict[GoreProfile, float] = {
    GoreProfile.NONE: 0.18,
    GoreProfile.LOW: 0.35,
    GoreProfile.MODERATE: 0.60,
    GoreProfile.HIGH: 1.0,
}

# Categories where higher score = worse (invert for pass).
_HIGHER_IS_WORSE: frozenset[str] = frozenset(
    {"CARTOON_LEAKAGE", "TEXT_ARTIFACTS", "GORE_LEVEL", "AI_ARTIFACTS"}
)


@dataclass
class CategoryScore:
    name: SemanticCategory
    score: float  # 0..1
    passed: bool
    detail: str
    source: str = "heuristic"  # heuristic | ocr | vision

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "passed": self.passed,
            "detail": self.detail,
            "source": self.source,
        }


@dataclass
class DcVisualQcResult:
    passed: bool
    verdict: str  # PASS | FAIL_VISUAL_QC
    technical_checks: list[dict[str, Any]] = field(default_factory=list)
    categories: list[CategoryScore] = field(default_factory=list)
    gore_profile: GoreProfile = GoreProfile.NONE
    gore_detected: float = 0.0
    failure_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "gore_profile": self.gore_profile.value,
            "gore_detected": round(self.gore_detected, 4),
            "failure_reasons": list(self.failure_reasons),
            "technical_checks": list(self.technical_checks),
            "categories": [c.as_dict() for c in self.categories],
            "metrics": self.metrics,
        }


@dataclass
class DcVisualQcPolicy:
    gore_profile: GoreProfile = GoreProfile.NONE
    max_attempts: int = MAX_DC_VISUAL_QC_ATTEMPTS
    allow_ocr: bool = True
    allow_vision: bool = True
    # Prefer dark cinematic; reject Wonder Crew pastel leakage.
    cartoon_leakage_max: float = 0.42
    text_artifacts_max: float = 0.35
    ai_artifacts_max: float = 0.55
    min_cinematic: float = 0.28
    min_lighting: float = 0.28
    min_atmosphere: float = 0.32
    min_geometry: float = 0.25


def resolve_gore_profile(
    request: GenerationRequest | None = None,
    *,
    explicit: str | GoreProfile | None = None,
) -> GoreProfile:
    """Default NONE/LOW. HIGH only when request explicitly asks for gore."""
    if explicit is not None:
        return GoreProfile(str(explicit).strip().upper())
    blob = ""
    if request is not None:
        blob = " ".join(
            [
                request.subject or "",
                request.composition or "",
                request.lighting or "",
                request.emotion or "",
                request.asset_type or "",
            ]
        ).casefold()
    if any(tok in blob for tok in ("gore", "bloodbath", "dismember", "splatter")):
        return GoreProfile.MODERATE
    if "blood" in blob or "viscera" in blob:
        return GoreProfile.LOW
    return GoreProfile.NONE


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as img:
        img.load()
        rgb = img.convert("RGB")
        w, h = rgb.size
        small = rgb.resize((96, 96))
        arr = np.asarray(small, dtype=np.float32)
        lum = 0.2126 * arr[:, :, 0] + 0.7152 * arr[:, :, 1] + 0.0722 * arr[:, :, 2]
        mean_l = float(lum.mean())
        contrast = float(lum.std())
        # Saturation proxy
        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        sat = float(((mx - mn) / np.maximum(mx, 1.0)).mean())
        # Extreme exposure / darkness
        near_black = float((lum <= 8).mean())
        near_white = float((lum >= 247).mean())
        # Edge density on larger grid so structure survives downscale
        edge_src = np.asarray(rgb.resize((192, 192)).convert("L"), dtype=np.float32)
        gx = np.abs(np.diff(edge_src, axis=1)).mean()
        gy = np.abs(np.diff(edge_src, axis=0)).mean()
        edge = float((gx + gy) / 2.0)
        # Warm pastel / cartoon: high sat + high mean L + low edge chaos in flats
        pastel = float(((arr[:, :, 0] > 180) & (arr[:, :, 1] > 160) & (arr[:, :, 2] > 140)).mean())
        # Red / gore proxy
        red = arr[:, :, 0]
        green = arr[:, :, 1]
        blue = arr[:, :, 2]
        gore_mask = (red > 90) & (red > green * 1.35) & (red > blue * 1.35) & (sat > 0.25)
        gore = float(gore_mask.mean())
        # Duplicate-people heuristic: bilateral symmetry of mid-band
        mid = edge_src[64:128, :]
        left = mid[:, :96]
        right = np.fliplr(mid[:, 96:])
        sym = float(1.0 - np.mean(np.abs(left - right)) / 255.0)
        uniq = len({tuple(p) for p in small.getdata()})
        variance = float(arr.var())
        return {
            "width": w,
            "height": h,
            "mean_luminance": mean_l,
            "contrast": contrast,
            "saturation": sat,
            "near_black_ratio": near_black,
            "near_white_ratio": near_white,
            "edge_density": edge,
            "pastel_ratio": pastel,
            "gore_ratio": gore,
            "symmetry": sym,
            "unique_colors": uniq,
            "variance": variance,
        }


def run_technical_gate(path: Path, *, expected_width: int | None = None, expected_height: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    if not path.is_file():
        checks.append(_check("file_exists", False, str(path)))
        return checks, metrics
    checks.append(_check("file_exists", True, str(path)))
    size = path.stat().st_size
    checks.append(_check("file_integrity", size >= 2048, f"{size} bytes"))
    try:
        metrics = _image_metrics(path)
        w, h = int(metrics["width"]), int(metrics["height"])
        dims_ok = w >= 256 and h >= 256
        if expected_width and expected_height:
            dims_ok = dims_ok and w == expected_width and h == expected_height
        checks.append(_check("resolution", dims_ok, f"{w}x{h}"))
        blank = metrics["variance"] < 2.0 and metrics["unique_colors"] <= 4
        checks.append(
            _check("blank", not blank, f"unique={metrics['unique_colors']} var={metrics['variance']:.2f}")
        )
        over = metrics["near_white_ratio"] > 0.88
        checks.append(_check("extreme_exposure", not over, f"near_white={metrics['near_white_ratio']:.3f}"))
        dark = metrics["near_black_ratio"] > 0.92 or (
            metrics["mean_luminance"] < 12 and metrics["contrast"] < 8
        )
        checks.append(
            _check(
                "extreme_darkness",
                not dark,
                f"near_black={metrics['near_black_ratio']:.3f} mean_L={metrics['mean_luminance']:.1f}",
            )
        )
        # Unintended duplicate people: very high bilateral symmetry on environment plates is OK;
        # flag only when symmetry extreme AND edge density suggests multiple figures.
        dup_people = metrics["symmetry"] > 0.92 and metrics["edge_density"] > 12 and metrics["mean_luminance"] > 40
        checks.append(
            _check(
                "duplicate_people",
                not dup_people,
                f"symmetry={metrics['symmetry']:.3f} edge={metrics['edge_density']:.2f}",
            )
        )
        checks.append(_check("corruption", True, "decoded"))
    except OSError as exc:
        checks.append(_check("resolution", False, str(exc)))
        checks.append(_check("blank", False, str(exc)))
        checks.append(_check("extreme_exposure", False, str(exc)))
        checks.append(_check("extreme_darkness", False, str(exc)))
        checks.append(_check("duplicate_people", True, "skipped"))
        checks.append(_check("corruption", False, str(exc)))
    return checks, metrics


def _windows_ocr_available() -> bool:
    return os.name == "nt" and bool(shutil.which("powershell"))


def _run_windows_ocr(image_path: Path) -> str:
    """Reuse the same Windows OCR approach as video_generator — optional, never required."""
    if not _windows_ocr_available() or not image_path.is_file():
        return ""
    escaped = str(image_path.resolve()).replace("'", "''")
    script = f"""
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
function AwaitOperation($AsyncOperation, $ResultType) {{
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {{ $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 }} |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($AsyncOperation))
    $task.Wait()
    return $task.Result
}}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{ exit 2 }}
$file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped}')) ([Windows.Storage.StorageFile])
$stream = AwaitOperation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) {{ Write-Output $line.Text }}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _readable_text_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'$%]{2,}", value or "")
    out: list[str] = []
    for token in tokens:
        normalized = token.strip("'$%").upper()
        if len(normalized) < 3 or len(set(normalized)) <= 1:
            continue
        if normalized.isdigit() and len(normalized) < 4:
            continue
        out.append(token)
    return out[:20]


def _text_artifact_score(path: Path, *, allow_ocr: bool) -> CategoryScore:
    if allow_ocr and _windows_ocr_available():
        text = _run_windows_ocr(path)
        tokens = _readable_text_tokens(text)
        if tokens:
            score = min(1.0, 0.35 + 0.08 * len(tokens))
            return CategoryScore(
                "TEXT_ARTIFACTS",
                score,
                score <= 0.35,
                f"ocr_tokens={tokens[:8]}",
                source="ocr",
            )
        if text == "":
            # OCR available but empty — soft pass; still run edge heuristic lightly
            pass
        else:
            return CategoryScore("TEXT_ARTIFACTS", 0.05, True, "ocr_clean", source="ocr")
    # Lightweight text-like pattern: dense mid-frequency horizontal edges in upper/lower bands
    with Image.open(path) as img:
        gray = np.asarray(img.convert("L").resize((128, 128)), dtype=np.float32)
    bands = [gray[8:28, :], gray[100:120, :]]
    band_edges = [float(np.abs(np.diff(b, axis=1)).mean()) for b in bands]
    score = min(1.0, max(band_edges) / 40.0)
    return CategoryScore(
        "TEXT_ARTIFACTS",
        score,
        score <= 0.55,
        f"band_edge_max={max(band_edges):.2f}",
        source="heuristic",
    )


def score_semantic_categories(
    path: Path,
    metrics: dict[str, Any],
    *,
    policy: DcVisualQcPolicy,
) -> list[CategoryScore]:
    mean_l = float(metrics.get("mean_luminance") or 0)
    contrast = float(metrics.get("contrast") or 0)
    sat = float(metrics.get("saturation") or 0)
    edge = float(metrics.get("edge_density") or 0)
    pastel = float(metrics.get("pastel_ratio") or 0)
    gore = float(metrics.get("gore_ratio") or 0)
    near_black = float(metrics.get("near_black_ratio") or 0)
    near_white = float(metrics.get("near_white_ratio") or 0)

    # CINEMATIC: controlled contrast, usable edges, not blank
    cinematic = min(
        1.0,
        0.35 * min(1.0, contrast / 35.0)
        + 0.35 * min(1.0, edge / 10.0)
        + 0.30 * (1.0 - min(1.0, abs(mean_l - 55) / 80.0)),
    )
    # LIGHTING: low-key preferred, not blown out
    lighting = min(
        1.0,
        0.45 * (1.0 - near_white)
        + 0.35 * min(1.0, contrast / 30.0)
        + 0.20 * (1.0 if 18 <= mean_l <= 150 else 0.35),
    )
    # ATMOSPHERE: desaturated, darker bias
    atmosphere = min(
        1.0,
        0.40 * (1.0 - min(1.0, sat / 0.55))
        + 0.35 * (1.0 if mean_l < 130 else 0.4)
        + 0.25 * min(1.0, near_black * 2.5),
    )
    # Geometry: enough structure, not mush (dark plates still need edges)
    geometry = min(1.0, 0.55 * min(1.0, edge / 8.0) + 0.45 * min(1.0, contrast / 22.0))
    # Cartoon / Wonder Crew leakage
    cartoon = min(1.0, 0.45 * min(1.0, sat / 0.5) + 0.35 * pastel + 0.20 * (1.0 if mean_l > 150 else 0.2))
    # Gore
    gore_score = min(1.0, gore * 3.2)
    # AI artifacts: extreme clipping mix or chaotic edges with low structure
    ai = min(
        1.0,
        0.4 * max(near_white, near_black * 0.5)
        + 0.35 * (1.0 if edge > 35 and contrast < 20 else 0.15)
        + 0.25 * (1.0 if sat > 0.65 else 0.1),
    )

    text = _text_artifact_score(path, allow_ocr=policy.allow_ocr)

    cats = [
        CategoryScore(
            "CINEMATIC_COMPOSITION",
            cinematic,
            cinematic >= policy.min_cinematic,
            f"contrast={contrast:.1f} edge={edge:.2f}",
        ),
        CategoryScore(
            "LIGHTING",
            lighting,
            lighting >= policy.min_lighting,
            f"mean_L={mean_l:.1f} near_white={near_white:.3f}",
        ),
        CategoryScore(
            "ATMOSPHERE",
            atmosphere,
            atmosphere >= policy.min_atmosphere,
            f"sat={sat:.3f} mean_L={mean_l:.1f}",
        ),
        CategoryScore(
            "BELIEVABLE_GEOMETRY",
            geometry,
            geometry >= policy.min_geometry,
            f"edge={edge:.2f}",
        ),
        CategoryScore(
            "CARTOON_LEAKAGE",
            cartoon,
            cartoon <= policy.cartoon_leakage_max,
            f"sat={sat:.3f} pastel={pastel:.3f}",
        ),
        text,
        CategoryScore(
            "GORE_LEVEL",
            gore_score,
            gore_score <= _GORE_CEILING[policy.gore_profile],
            f"gore_ratio={gore:.4f} profile={policy.gore_profile.value}",
        ),
        CategoryScore(
            "AI_ARTIFACTS",
            ai,
            ai <= policy.ai_artifacts_max,
            f"near_white={near_white:.3f} edge={edge:.2f}",
        ),
    ]
    # Enforce TEXT policy threshold from OCR/heuristic score
    for i, cat in enumerate(cats):
        if cat.name == "TEXT_ARTIFACTS":
            cats[i] = CategoryScore(
                cat.name,
                cat.score,
                cat.score <= policy.text_artifacts_max,
                cat.detail,
                cat.source,
            )
    return cats


def maybe_enrich_with_vision(
    path: Path,
    categories: list[CategoryScore],
    *,
    settings: Any | None,
    policy: DcVisualQcPolicy,
) -> list[CategoryScore]:
    """Optional local Ollama vision — skipped if unavailable. Never blocks on missing model."""
    if not policy.allow_vision or settings is None:
        return categories
    model = getattr(settings, "ollama_vision_model", None)
    base = getattr(settings, "ollama_base_url", None)
    if not model or not base:
        return categories
    try:
        import httpx

        prompt = (
            "Score this horror/cinematic still. Return JSON only with keys: "
            "cinematic_composition, lighting, atmosphere, believable_geometry, "
            "cartoon_leakage, text_artifacts, gore_level, ai_artifacts — each 0..1. "
            "Higher cartoon_leakage/text_artifacts/gore_level/ai_artifacts is worse. "
            "Wonder Crew kids pastel/storybook style must raise cartoon_leakage."
        )
        image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                f"{str(base).rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content") or ""
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            return categories
        payload = json.loads(match.group(0))
        mapping = {
            "CINEMATIC_COMPOSITION": ("cinematic_composition", False),
            "LIGHTING": ("lighting", False),
            "ATMOSPHERE": ("atmosphere", False),
            "BELIEVABLE_GEOMETRY": ("believable_geometry", False),
            "CARTOON_LEAKAGE": ("cartoon_leakage", True),
            "TEXT_ARTIFACTS": ("text_artifacts", True),
            "GORE_LEVEL": ("gore_level", True),
            "AI_ARTIFACTS": ("ai_artifacts", True),
        }
        out: list[CategoryScore] = []
        by_name = {c.name: c for c in categories}
        for name, (key, worse) in mapping.items():
            base_cat = by_name[name]
            if key not in payload:
                out.append(base_cat)
                continue
            vision_score = float(payload[key])
            vision_score = min(1.0, max(0.0, vision_score))
            # Blend heuristic with vision (vision weight 0.55)
            blended = 0.45 * base_cat.score + 0.55 * vision_score
            if worse:
                passed = blended <= (
                    policy.cartoon_leakage_max
                    if name == "CARTOON_LEAKAGE"
                    else policy.text_artifacts_max
                    if name == "TEXT_ARTIFACTS"
                    else _GORE_CEILING[policy.gore_profile]
                    if name == "GORE_LEVEL"
                    else policy.ai_artifacts_max
                )
            else:
                floor = {
                    "CINEMATIC_COMPOSITION": policy.min_cinematic,
                    "LIGHTING": policy.min_lighting,
                    "ATMOSPHERE": policy.min_atmosphere,
                    "BELIEVABLE_GEOMETRY": policy.min_geometry,
                }[name]
                passed = blended >= floor
            out.append(
                CategoryScore(name, blended, passed, f"{base_cat.detail}; vision={vision_score:.2f}", source="vision")
            )
        return out
    except Exception:
        return categories


def run_dc_visual_qc(
    path: Path,
    *,
    request: GenerationRequest | None = None,
    policy: DcVisualQcPolicy | None = None,
    settings: Any | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> DcVisualQcResult:
    policy = policy or DcVisualQcPolicy(gore_profile=resolve_gore_profile(request))
    technical, metrics = run_technical_gate(path, expected_width=expected_width, expected_height=expected_height)
    tech_failed = [c["name"] for c in technical if not c["ok"]]

    categories: list[CategoryScore] = []
    if path.is_file() and "corruption" not in tech_failed:
        categories = score_semantic_categories(path, metrics, policy=policy)
        categories = maybe_enrich_with_vision(path, categories, settings=settings, policy=policy)

    failures: list[str] = list(tech_failed)
    for cat in categories:
        if not cat.passed:
            failures.append(cat.name)

    # Explicit rule: HIGH gore when profile is NONE/LOW → reject
    gore_cat = next((c for c in categories if c.name == "GORE_LEVEL"), None)
    gore_detected = float(gore_cat.score) if gore_cat else float(metrics.get("gore_ratio") or 0)
    if policy.gore_profile in {GoreProfile.NONE, GoreProfile.LOW} and gore_detected > _GORE_CEILING[GoreProfile.LOW]:
        if "GORE_LEVEL" not in failures:
            failures.append("GORE_LEVEL")
        if gore_cat is not None:
            gore_cat.passed = False

    passed = not failures
    return DcVisualQcResult(
        passed=passed,
        verdict="PASS" if passed else "FAIL_VISUAL_QC",
        technical_checks=technical,
        categories=categories,
        gore_profile=policy.gore_profile,
        gore_detected=gore_detected,
        failure_reasons=failures,
        metrics=metrics,
    )


def adjust_request_for_failures(
    request: GenerationRequest,
    result: DcVisualQcResult,
    *,
    attempt: int,
) -> GenerationRequest:
    """Controlled regeneration: change concrete params from failure reasons — no endless loop."""
    reasons = set(result.failure_reasons)
    updates: dict[str, Any] = {
        "force_regenerate": True,
        "seed": (request.seed or 700_000) + 17 * (attempt + 1) + len(reasons) * 3,
    }
    lighting = request.lighting or ""
    composition = request.composition or ""
    neg_extra: list[str] = []

    if "CARTOON_LEAKAGE" in reasons:
        composition = (composition + " cinematic photoreal environment, mature horror mood").strip()
        # Keep avoid-list out of positive composition tokens that isolation treats as leakage
        # ("storybook" / "wonder crew"). Prefer synonyms + channel negative_prompt already has cartoon.
        neg_extra += ["cartoon", "pastel children's illustration", "kids show aesthetic", "cute"]
    if "TEXT_ARTIFACTS" in reasons or "random text" in " ".join(reasons).casefold():
        composition = (composition + " no text no letters no signage no captions").strip()
        neg_extra += ["text", "letters", "watermark", "subtitles", "logo"]
    if "GORE_LEVEL" in reasons:
        composition = (composition + " no gore no blood no viscera, atmospheric dread only").strip()
        neg_extra += ["gore", "blood", "splatter", "dismemberment"]
    if "extreme_exposure" in reasons or "LIGHTING" in reasons and result.metrics.get("near_white_ratio", 0) > 0.5:
        lighting = (lighting + " low-key lighting, deep shadows, controlled contrast").strip()
        neg_extra += ["overexposed", "blown highlights"]
    if "extreme_darkness" in reasons:
        lighting = (lighting + " practical motivated light, readable silhouette, moonlight rim").strip()
    if "CINEMATIC_COMPOSITION" in reasons or "ATMOSPHERE" in reasons:
        composition = (composition + " wide environmental storytelling, film still composition").strip()
    if "AI_ARTIFACTS" in reasons or "blank" in reasons or "corruption" in reasons:
        updates["seed"] = int(updates["seed"]) + 101
    if "duplicate_people" in reasons:
        composition = (composition + " single clear subject or empty environment, no duplicated figures").strip()
        neg_extra += ["duplicate people", "cloned faces"]

    if lighting:
        updates["lighting"] = lighting[:200]
    if composition:
        updates["composition"] = composition[:200]
    # Encode extra negatives into composition tail (GenerationRequest has no negative field).
    # Prefix with " Avoid:" so isolation ignores anti-leakage tokens on regen.
    if neg_extra:
        updates["composition"] = (
            (updates.get("composition") or request.composition or "")
            + " Avoid: "
            + ", ".join(dict.fromkeys(neg_extra))
        )[:200]

    return request.model_copy(update=updates)


# ---------------------------------------------------------------------------
# Fixture helpers (tests / offline QC validation)
# ---------------------------------------------------------------------------


def write_dc_qc_fixture(kind: str, path: Path, *, size: tuple[int, int] = (768, 768)) -> Path:
    """Create controlled good/bad fixtures without Comfy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    if kind in {"corridor", "forest", "motel", "basement", "house_exterior"}:
        # Dark cinematic plate with enough structure for geometry/composition checks
        rng = np.random.default_rng(abs(hash(kind)) % (2**32))
        base = np.zeros((h, w, 3), dtype=np.float32)
        yy, xx = np.mgrid[0:h, 0:w]
        # vignette + fog gradient
        base[:, :, 0] = 22 + 18 * (yy / h)
        base[:, :, 1] = 24 + 14 * (yy / h)
        base[:, :, 2] = 28 + 10 * (yy / h)
        noise = rng.normal(0, 6, size=(h, w, 3))
        base = np.clip(base + noise, 0, 255)
        img = Image.fromarray(base.astype(np.uint8))
        draw = ImageDraw.Draw(img)
        for i in range(12):
            shade = 28 + i * 7
            inset = 30 + i * 28
            draw.rectangle(
                [inset, inset // 2, w - inset, h - inset // 2],
                outline=(shade, shade + 3, shade + 6),
                width=2,
            )
        # practical light
        draw.ellipse([w // 2 - 50, 70, w // 2 + 50, 170], fill=(85, 78, 55))
        draw.ellipse([w // 2 - 20, 95, w // 2 + 20, 135], fill=(140, 130, 90))
        if kind == "forest":
            for x in range(40, w, 70):
                draw.line([(x, h - 20), (x + rng.integers(-15, 15), 80)], fill=(28, 40, 26), width=10)
                draw.ellipse([x - 30, 60, x + 40, 140], outline=(34, 48, 32), width=2)
        if kind == "motel":
            draw.rectangle([60, h // 2 - 20, w - 60, h // 2 + 50], fill=(48, 42, 36))
            for x in range(100, w - 100, 90):
                draw.rectangle([x, h // 2, x + 50, h // 2 + 70], outline=(70, 60, 50), width=2)
                draw.rectangle([x + 10, h // 2 + 20, x + 40, h // 2 + 55], fill=(20, 18, 16))
        if kind == "basement":
            draw.rectangle([0, h - 140, w, h], fill=(10, 10, 12))
            for x in range(80, w, 120):
                draw.line([(x, 40), (x, h - 140)], fill=(50, 48, 42), width=4)
        if kind == "house_exterior":
            draw.polygon([(w // 2, 90), (140, 300), (w - 140, 300)], fill=(42, 44, 50))
            draw.rectangle([180, 300, w - 180, h - 60], fill=(36, 38, 44))
            draw.rectangle([w // 2 - 30, h - 160, w // 2 + 30, h - 60], fill=(18, 16, 14))
            for wx in (220, w // 2 - 20, w - 280):
                draw.rectangle([wx, 340, wx + 50, 400], fill=(70, 65, 40))
        if kind == "corridor":
            for y in range(200, h - 40, 40):
                draw.line([(80, y), (w - 80, y)], fill=(40, 42, 48), width=1)
        img.save(path)
        return path

    if kind == "cartoon":
        img = Image.new("RGB", (w, h), (255, 220, 240))
        draw = ImageDraw.Draw(img)
        draw.ellipse([180, 160, 420, 420], fill=(120, 220, 255), outline=(40, 40, 40), width=6)
        draw.ellipse([480, 200, 650, 380], fill=(255, 180, 80), outline=(40, 40, 40), width=6)
        draw.rectangle([100, 500, 700, 650], fill=(180, 255, 160))
        img.save(path)
        return path

    if kind == "random_text":
        img = Image.new("RGB", (w, h), (30, 32, 38))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arial.ttf", 48)
        except OSError:
            font = ImageFont.load_default()
        draw.text((80, 200), "MOTEL VACANCY", fill=(240, 240, 240), font=font)
        draw.text((80, 280), "ROOM 237 OPEN", fill=(220, 200, 80), font=font)
        draw.text((80, 360), "EXIT 12A", fill=(200, 200, 200), font=font)
        img.save(path)
        return path

    if kind == "overexposed":
        img = Image.new("RGB", (w, h), (252, 252, 250))
        draw = ImageDraw.Draw(img)
        draw.rectangle([200, 200, 560, 560], fill=(255, 255, 255), outline=(245, 245, 245))
        img.save(path)
        return path

    if kind == "excessive_gore":
        img = Image.new("RGB", (w, h), (20, 10, 10))
        draw = ImageDraw.Draw(img)
        for _ in range(40):
            x0 = int(np.random.randint(0, w - 80))
            y0 = int(np.random.randint(0, h - 80))
            draw.ellipse([x0, y0, x0 + 80, y0 + 60], fill=(160, 10, 10))
        draw.rectangle([0, h // 2, w, h], fill=(120, 0, 0))
        img.save(path)
        return path

    raise ValueError(f"Unknown fixture kind: {kind}")
