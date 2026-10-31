"""Local U2-Net background removal. Storage and model are explicitly bound to E:."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import onnxruntime as ort
from PIL import Image

from atnos.production.cutout_quality import (
    PostprocessMode,
    compose_rgba,
    improve_alpha_mask,
    run_alpha_qc,
)

MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
MODEL_MD5 = "60024c5c889badc19c04ad937298a77b"
_SESSION: ort.InferenceSession | None = None


def ensure_model() -> Path:
    path = Path("E:/Atnos/Models/background-removal/u2net.onnx")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        partial = path.with_suffix(".download")
        with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            count = 0
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    count += len(chunk)
                    if count > 200 * 1024 * 1024:
                        raise ValueError("Unexpected background-removal model size")
                    stream.write(chunk)
        if hashlib.md5(partial.read_bytes()).hexdigest() != MODEL_MD5:
            raise ValueError("Downloaded U2-Net does not match the upstream checksum")
        partial.replace(path)
    if hashlib.md5(path.read_bytes()).hexdigest() != MODEL_MD5:
        raise ValueError("Local U2-Net model checksum changed")
    return path


def _session() -> ort.InferenceSession:
    global _SESSION
    if _SESSION is None:
        model = ensure_model()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.inter_op_num_threads = 1
        _SESSION = ort.InferenceSession(str(model), sess_options=opts, providers=["CPUExecutionProvider"])
    return _SESSION


def predict_alpha(rgb: Image.Image) -> Image.Image:
    """Raw U2-Net alpha (0..255 L), resized to rgb.size — no postprocess."""
    session = _session()
    pixels = np.asarray(rgb.resize((320, 320), Image.Resampling.LANCZOS), dtype=np.float32)
    pixels /= max(float(pixels.max()), 1e-6)
    pixels = (pixels - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    prediction = session.run(
        None, {session.get_inputs()[0].name: pixels.transpose(2, 0, 1)[None].astype(np.float32)}
    )[0][0, 0]
    prediction = (prediction - prediction.min()) / max(float(prediction.max() - prediction.min()), 1e-6)
    mask = Image.fromarray((prediction * 255).clip(0, 255).astype("uint8")).resize(
        rgb.size, Image.Resampling.LANCZOS
    )
    return mask


def remove_background(
    source: Path,
    target: Path,
    *,
    postprocess: PostprocessMode = "improved",
    run_qc: bool = True,
) -> dict[str, Any]:
    from atnos.production.order_store import file_hash

    model = ensure_model()
    with Image.open(source) as image:
        rgb = image.convert("RGB")
    raw_mask = predict_alpha(rgb)
    mask = improve_alpha_mask(rgb, raw_mask, mode=postprocess)
    decontam = postprocess == "improved"
    result = compose_rgba(rgb, mask, decontaminate=decontam)
    target.parent.mkdir(parents=True, exist_ok=True)
    result.save(target)

    payload: dict[str, Any] = {
        "model": str(model),
        "model_sha256": file_hash(model),
        "model_source": MODEL_URL,
        "inference_contract_source": "https://github.com/danielgatis/rembg/blob/main/rembg/sessions/u2net.py",
        "source_sha256": file_hash(source),
        "sha256": file_hash(target),
        "method": "local U2-Net ONNX CPU",
        "postprocess": postprocess,
        "decontaminate": decontam,
        "foreground_rgb": "decontaminated_fringe" if decontam else "unaltered",
        "alpha_range": mask.getextrema(),
        "dimensions": list(result.size),
    }
    if run_qc:
        qc = run_alpha_qc(target, require_alpha=True)
        payload["alpha_qc"] = qc.as_dict()
    return payload
