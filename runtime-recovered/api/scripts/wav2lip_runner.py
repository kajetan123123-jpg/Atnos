from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


MEL_STEP_SIZE = 16
FRAME_FPS = 30.0
WAV2LIP_BATCH_SIZE = 64
IMG_SIZE = 96


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ATNOS Wav2Lip runner")
    parser.add_argument("--custom-node-dir", required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--image", required=True)
    parser.add_argument("--audio")
    parser.add_argument("--output")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--face-detect-batch", type=int, default=4)
    parser.add_argument("--mode", choices=("sequential", "repetitive"), default="repetitive")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--verify-face-only", action="store_true")
    return parser.parse_args()


def audio_duration(audio_path: Path) -> float:
    import soundfile as sf

    return float(sf.info(str(audio_path)).duration or 0.0)


def build_mel_chunks(audio_path: Path):
    import numpy as np
    from Wav2Lip import audio as wav_audio

    wav = wav_audio.load_wav(str(audio_path), 16000)
    mel = wav_audio.melspectrogram(wav)
    if np.isnan(mel.reshape(-1)).sum() > 0:
        raise RuntimeError("Mel contains NaN values.")

    mel_chunks: list[object] = []
    mel_idx_multiplier = 80.0 / FRAME_FPS
    index = 0
    while True:
        start_idx = int(index * mel_idx_multiplier)
        if start_idx + MEL_STEP_SIZE > len(mel[0]):
            mel_chunks.append(mel[:, len(mel[0]) - MEL_STEP_SIZE :])
            break
        mel_chunks.append(mel[:, start_idx : start_idx + MEL_STEP_SIZE])
        index += 1
    return mel_chunks


def detect_face(frame, *, face_detect_batch: int, device_preference: str):
    import numpy as np
    import torch
    from Wav2Lip import face_detection

    devices = ordered_devices(device_preference=device_preference, torch_module=torch)
    images = [frame]
    predictions = None
    last_error = "Face detection failed."
    for device in devices:
        detector = None
        batch_size = max(1, face_detect_batch)
        while True:
            try:
                detector = face_detection.FaceAlignment(
                    face_detection.LandmarksType._2D,
                    flip_input=False,
                    device=device,
                )
                predictions = []
                for start in range(0, len(images), batch_size):
                    predictions.extend(detector.get_detections_for_batch(np.array(images[start : start + batch_size])))
            except RuntimeError as exc:
                last_error = str(exc)
                if detector is not None:
                    del detector
                if batch_size > 1:
                    batch_size = max(1, batch_size // 2)
                    continue
                if device == "cuda" and is_memory_error(exc):
                    break
                raise RuntimeError(f"Face detection failed on the portrait image: {exc}") from exc
            else:
                if detector is not None:
                    del detector
                break
        if predictions is not None:
            break

    rect = predictions[0] if predictions else None
    if predictions is None and last_error:
        raise RuntimeError(last_error)
    if rect is None:
        raise RuntimeError("Face not detected in portrait image.")
    y1 = max(0, rect[1] - 0)
    y2 = min(frame.shape[0], rect[3] + 10)
    x1 = max(0, rect[0] - 0)
    x2 = min(frame.shape[1], rect[2] + 0)
    face = frame[y1:y2, x1:x2]
    if face.size == 0:
        raise RuntimeError("Detected face crop is empty.")
    return face, (y1, y2, x1, x2)


def load_model(checkpoint_path: Path, *, device_preference: str):
    import torch
    from Wav2Lip.models import Wav2Lip as Wav2LipModel

    devices = ordered_devices(device_preference=device_preference, torch_module=torch)
    last_error = "Wav2Lip model failed to load."
    checkpoint_cache = None
    for device in devices:
        model = Wav2LipModel()
        checkpoint = checkpoint_cache or torch.load(
            str(checkpoint_path),
            map_location=None if device == "cuda" else (lambda storage, loc: storage),
        )
        checkpoint_cache = checkpoint
        state_dict = checkpoint["state_dict"]
        normalized_state = {key.replace("module.", ""): value for key, value in state_dict.items()}
        model.load_state_dict(normalized_state)
        try:
            model = model.to(device)
            return model.eval(), device
        except RuntimeError as exc:
            last_error = str(exc)
            if device == "cuda" and is_memory_error(exc):
                continue
            raise
    raise RuntimeError(last_error)


def generate_frames(frame, face, coords, mel_chunks, *, checkpoint_path: Path, device_preference: str):
    import cv2
    import numpy as np
    import torch

    model, device = load_model(checkpoint_path, device_preference=device_preference)
    generated_frames: list[object] = []
    img_batch = []
    mel_batch = []
    frame_batch = []
    coords_batch = []

    def _flush() -> None:
        if not img_batch:
            return
        images = np.asarray(img_batch)
        mels = np.asarray(mel_batch)
        masked = images.copy()
        masked[:, IMG_SIZE // 2 :] = 0
        images = np.concatenate((masked, images), axis=3) / 255.0
        mels = np.reshape(mels, [len(mels), mels.shape[1], mels.shape[2], 1])
        image_tensor = torch.FloatTensor(np.transpose(images, (0, 3, 1, 2))).to(device)
        mel_tensor = torch.FloatTensor(np.transpose(mels, (0, 3, 1, 2))).to(device)
        with torch.no_grad():
            prediction = model(mel_tensor, image_tensor)
        prediction = prediction.cpu().numpy().transpose(0, 2, 3, 1) * 255.0
        for patch, frame_item, coords_item in zip(prediction, frame_batch, coords_batch):
            y1, y2, x1, x2 = coords_item
            resized_patch = cv2.resize(patch.astype("uint8"), (x2 - x1, y2 - y1))
            composed = frame_item.copy()
            composed[y1:y2, x1:x2] = resized_patch
            generated_frames.append(composed)
        img_batch.clear()
        mel_batch.clear()
        frame_batch.clear()
        coords_batch.clear()

    face_resized = cv2.resize(face, (IMG_SIZE, IMG_SIZE))
    for mel in mel_chunks:
        img_batch.append(face_resized)
        mel_batch.append(mel)
        frame_batch.append(frame.copy())
        coords_batch.append(coords)
        if len(img_batch) >= WAV2LIP_BATCH_SIZE:
            _flush()
    _flush()
    return generated_frames


def load_frames_for_wav2lip(image_path: Path, *, max_processing_height: int = 512):
    import cv2

    original_frame = cv2.imread(str(image_path))
    if original_frame is None:
        raise RuntimeError("Unable to load portrait image for Wav2Lip.")
    height, width = original_frame.shape[:2]
    if height <= max_processing_height:
        return original_frame, original_frame.copy(), 1.0, 1.0
    scale = max_processing_height / float(height)
    processing_frame = cv2.resize(original_frame, (int(width * scale), int(height * scale)))
    scale_x = width / float(processing_frame.shape[1])
    scale_y = height / float(processing_frame.shape[0])
    return original_frame, processing_frame, scale_x, scale_y


def ordered_devices(*, device_preference: str, torch_module) -> list[str]:
    if device_preference == "cpu":
        return ["cpu"]
    if device_preference == "cuda":
        return ["cuda"]
    if torch_module.cuda.is_available():
        return ["cuda", "cpu"]
    return ["cpu"]


def is_memory_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "out of memory" in message or "hip out of memory" in message or "cuda out of memory" in message


def write_silent_video(frames, *, fps: int, output_path: Path) -> None:
    import cv2

    first = frames[0]
    height, width = first.shape[:2]
    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(fps),
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not create temporary Wav2Lip video.")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


def mux_audio(*, ffmpeg_path: Path, silent_video_path: Path, audio_path: Path, output_path: Path) -> None:
    commands = (
        [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(silent_video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "h264_amf",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ],
        [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(silent_video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ],
    )
    last_error = ""
    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
            return
        last_error = result.stderr.strip() or result.stdout.strip() or "ffmpeg mux failed"
    raise RuntimeError(last_error)


def main() -> None:
    args = parse_args()
    custom_node_dir = Path(args.custom_node_dir).resolve()
    checkpoint_path = Path(args.checkpoint).resolve() if args.checkpoint else None
    image_path = Path(args.image).resolve()
    audio_path = Path(args.audio).resolve() if args.audio else None
    output_path = Path(args.output).resolve() if args.output else None
    ffmpeg_path = Path(args.ffmpeg).resolve() if args.ffmpeg else None
    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "temp").mkdir(parents=True, exist_ok=True)

    if str(custom_node_dir) not in sys.path:
        sys.path.insert(0, str(custom_node_dir))
    if not args.verify_face_only and (checkpoint_path is None or not checkpoint_path.exists()):
        raise RuntimeError("Wav2Lip checkpoint is missing.")

    cwd_before = Path.cwd()
    try:
        os.chdir(str(work_dir))
        original_frame, processing_frame, scale_x, scale_y = load_frames_for_wav2lip(image_path)
        face, coords = detect_face(
            processing_frame,
            face_detect_batch=args.face_detect_batch,
            device_preference=args.device,
        )
        original_coords = (
            int(round(coords[0] * scale_y)),
            int(round(coords[1] * scale_y)),
            int(round(coords[2] * scale_x)),
            int(round(coords[3] * scale_x)),
        )
        if args.verify_face_only:
            print(json.dumps({"verified": True, "coords": list(original_coords)}))
            return
        assert audio_path is not None
        duration_sec = audio_duration(audio_path)
        if duration_sec <= 0.1:
            raise RuntimeError("Audio duration is too short for Wav2Lip.")
        mel_chunks = build_mel_chunks(audio_path)
        output_frames = generate_frames(
            original_frame,
            face,
            original_coords,
            mel_chunks,
            checkpoint_path=checkpoint_path,
            device_preference=args.device,
        )
    finally:
        os.chdir(str(cwd_before))

    if not output_frames:
        raise RuntimeError("Wav2Lip returned no output frames.")

    try:
        assert output_path is not None
        assert ffmpeg_path is not None
        assert audio_path is not None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        silent_video_path = work_dir / "silent.mp4"
        write_silent_video(output_frames, fps=args.fps, output_path=silent_video_path)
        mux_audio(
            ffmpeg_path=ffmpeg_path,
            silent_video_path=silent_video_path,
            audio_path=audio_path,
            output_path=output_path,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
