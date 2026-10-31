from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
import torchaudio


def _soundfile_load(
    uri: str | Path,
    frame_offset: int = 0,
    num_frames: int = -1,
    normalize: bool = True,
    channels_first: bool = True,
    *_args: Any,
    **_kwargs: Any,
) -> tuple[torch.Tensor, int]:
    """Czyta lokalne WAV bez TorchCodec blokowanego przez Windows App Control."""

    dtype = "float32" if normalize else "int16"
    with sf.SoundFile(str(uri), mode="r") as source:
        source.seek(max(0, int(frame_offset)))
        frames = -1 if int(num_frames) < 0 else int(num_frames)
        samples = source.read(frames=frames, dtype=dtype, always_2d=True)
        sample_rate = int(source.samplerate)
    tensor = torch.from_numpy(np.asarray(samples).copy())
    if channels_first:
        tensor = tensor.transpose(0, 1)
    return tensor, sample_rate


def main() -> None:
    torchaudio.load = _soundfile_load
    runpy.run_module("f5_tts.infer.infer_gradio", run_name="__main__")


if __name__ == "__main__":
    main()
