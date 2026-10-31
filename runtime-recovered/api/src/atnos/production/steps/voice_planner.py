from __future__ import annotations

from atnos.core.config import Settings
from atnos.production.models import ProductionContext, StepResult
from atnos.production.steps.common import write_json


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    script = context.artifacts["script"]
    assets_dir = context.artifact_dir / "assets"
    narration_path = assets_dir / "narration.wav"
    lines = [script["hook"], script["opening_line"], *script["body"], script["cta"]]
    payload = {
        "status": "planned",
        "tool": "local_tts_or_configured_provider",
        "voice_id": "channel_default",
        "sample_rate_hz": 44100,
        "channels": 2,
        "target_loudness_lufs": -14,
        "output_file": str(narration_path),
        "segments": [
            {
                "id": index,
                "text": line,
                "estimated_duration_s": max(2, round(len(str(line).split()) / 2.25, 1)),
            }
            for index, line in enumerate(lines)
        ],
        "render_required": True,
    }
    return write_json(context.artifact_dir / "voice_manifest.json", payload)
