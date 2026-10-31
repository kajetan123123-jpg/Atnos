# Wealth Blueprint Audio Pipeline

```text
script → TTS (F5-TTS) → narration.wav → Whisper alignment → word_timestamps.json
```

## Outputs

- `narration.wav`
- `word_timestamps.json` with:
  - `words`: `{ "word", "start", "end", "segment_id" }`
  - `segments`: sentence spans for renderer sync (text, numbers, charts, scene changes)

## Cache

Fingerprint = script + F5 voice identity (ref audio hash, speed, nfe).  
Same input restores cached WAV + timestamps without re-synthesis (`force_regenerate=True` to bypass).

## Validation

- audio exists
- duration readable / non-trivial
- no empty segments
- monotonic word timestamps
- large gaps → warning

## Usage

```python
from atnos.production.audio_pipeline import WealthBlueprintAudioPipeline

pipeline = WealthBlueprintAudioPipeline(settings)  # F5 + Whisper when available
result = await pipeline.run("Wealth compounds over time.", work_dir=job_dir)
```
