from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import wave

import torch
import torchaudio
from local_ctc_alignment_text import build_alignment_targets, build_word_timestamps

MODEL_DIR = Path(r"E:\Atnos\Tools\models\wav2vec2-asr-base-960h")
WEIGHTS = MODEL_DIR / "weights.pth"
WEIGHTS_URL = "https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_base_ls960_asr_ls960.pth"
EXPECTED_BYTES = 377664473
EXPECTED_SHA256 = "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(audio_path: Path, reference: str, output: Path) -> dict:
    if not WEIGHTS.is_file() or WEIGHTS.stat().st_size != EXPECTED_BYTES:
        raise RuntimeError("Pinned WAV2VEC2 model is missing or has unexpected size.")
    digest = sha256(WEIGHTS)
    if digest != EXPECTED_SHA256:
        raise RuntimeError("Pinned WAV2VEC2 weights failed SHA-256 verification.")
    torch.set_num_threads(4)
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model(dl_kwargs={"model_dir": str(MODEL_DIR), "file_name": "weights.pth", "check_hash": False})
    labels = bundle.get_labels()
    char_to_id = {c: i for i, c in enumerate(labels)}
    chars, _word_ranges = build_alignment_targets(reference, bundle.get_labels())
    target_ids = []
    for c in chars:
        if c not in char_to_id or c == "-":
            raise RuntimeError(f"Reference contains unsupported character: {c!r}")
        target_ids.append(char_to_id[c])
    # Decode PCM WAV directly: this isolated TorchAudio install intentionally has
    # no optional TorchCodec or soundfile backend.
    with wave.open(str(audio_path), "rb") as stream:
        channels, width, sample_rate = stream.getnchannels(), stream.getsampwidth(), stream.getframerate()
        frames = stream.readframes(stream.getnframes())
    if width not in (1, 2, 3, 4) or not frames:
        raise RuntimeError("Only non-empty PCM WAV audio is supported.")
    import numpy as np
    if width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        ints = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        ints = (ints ^ 0x800000) - 0x800000
        data = ints.astype(np.float32) / 8388608.0
    else:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    data = data.reshape(-1, channels).mean(axis=1)
    waveform = torch.from_numpy(data.copy()).unsqueeze(0)
    if sample_rate != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sample_rate, bundle.sample_rate)
    duration = waveform.shape[1] / bundle.sample_rate
    with torch.inference_mode():
        emissions, _ = model(waveform)
        log_probs = torch.log_softmax(emissions, dim=-1)
        paths, scores = torchaudio.functional.forced_align(log_probs, torch.tensor([target_ids], dtype=torch.int32), blank=0)
    frame_count = emissions.shape[1]
    frame_sec = duration / frame_count
    target_index = 0
    last = 0
    char_frames: list[list[int]] = [[] for _ in target_ids]
    char_scores: list[list[float]] = [[] for _ in target_ids]
    for frame, raw in enumerate(paths[0].tolist()):
        token = int(raw)
        if token == 0:
            last = 0
            continue
        if token != last:
            if target_index >= len(target_ids) or token != target_ids[target_index]:
                raise RuntimeError("CTC path did not consume the full reference in order.")
            target_index += 1
        idx = target_index - 1
        char_frames[idx].append(frame)
        char_scores[idx].append(float(scores[0, frame]))
        last = token
    if target_index != len(target_ids):
        raise RuntimeError(f"Alignment omitted reference tokens ({target_index}/{len(target_ids)}).")
    token_time = []
    for frames, probs in zip(char_frames, char_scores):
        if not frames:
            raise RuntimeError("Reference token received an empty time span.")
        token_time.append((frames[0] * frame_sec, (frames[-1] + 1) * frame_sec, math.exp(sum(probs) / len(probs))))

    words = build_word_timestamps(reference, token_time, duration)
    expected_words = re.findall(r"\S+", reference)
    if len(words) != len(expected_words) or words[-1]["end_sec"] > duration:
        raise RuntimeError("Alignment coverage or duration validation failed.")
    result = {
        "schema_version": 1, "status": "aligned", "reference_text": reference,
        "word_timestamps": words, "duration_sec": round(duration, 4),
        "provenance": {"method": "TorchAudio forced_align with reference-conditioned CTC", "model": "WAV2VEC2_ASR_BASE_960H",
            "weights_url": WEIGHTS_URL, "weights_file": str(WEIGHTS), "weights_bytes": WEIGHTS.stat().st_size,
            "weights_sha256": digest, "license": "MIT", "torch": torch.__version__, "torchaudio": torchaudio.__version__,
            "torchaudio_source": "https://github.com/pytorch/audio/blob/v2.11.0/src/torchaudio/pipelines/_wav2vec2/impl.py",
            "torchaudio_alignment_api": "https://docs.pytorch.org/audio/2.11.0/generated/torchaudio.functional.forced_align.html",
            "audio_sha256": sha256(audio_path),
            "sample_rate_hz": bundle.sample_rate, "device": "cpu", "threads": torch.get_num_threads(),
            "confidence_gate": 0.025},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.audio, args.reference, args.output)
    except Exception as exc:
        print(f"ALIGNMENT_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2)
