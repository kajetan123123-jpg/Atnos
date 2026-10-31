from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.comfyui.client import ComfyUIClient
from atnos.comfyui.workflow_builder import WorkflowBuilder
from atnos.pipeline.scene_renderer import SceneResult
from atnos.pipeline.video_assembler import VideoAssembler


def test_comfyui_client_downloads_image_outputs(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.status_code = 200
            self._payload = payload or {}
            self.content = content

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class FakeHttpClient:
        def __init__(self, *args, **kwargs) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url, json):
            calls.append(("post", url))
            assert json["client_id"]
            return FakeResponse({"prompt_id": "prompt-1"})

        def get(self, url, params=None):
            calls.append(("get", url))
            if "/history/" in url:
                return FakeResponse(
                    {
                        "prompt-1": {
                            "status": {"status_str": "success"},
                            "outputs": {
                                "9": {
                                    "images": [
                                        {"filename": "frame.png", "subfolder": "", "type": "output"}
                                    ]
                                }
                            },
                        }
                    }
                )
            return FakeResponse(content=b"PNGDATA")

    monkeypatch.setattr("atnos.comfyui.client.httpx.Client", FakeHttpClient)
    client = ComfyUIClient(base_url="http://comfy.local")

    prompt_id = client.queue_prompt({"1": {"class_type": "Noop", "inputs": {}}})
    paths = client.download_outputs(prompt_id, tmp_path)

    assert prompt_id == "prompt-1"
    assert (tmp_path / "frame.png").read_bytes() == b"PNGDATA"
    assert paths == [tmp_path / "frame.png"]
    assert calls[0] == ("post", "http://comfy.local/prompt")


def test_workflow_builder_builds_minimal_vertical_image_workflow() -> None:
    builder = WorkflowBuilder({"sd_model_checkpoint": "model.safetensors", "comfyui_steps": 20})

    workflow = builder.build_image_workflow(
        {
            "prompt": "cinematic Polish finance chart",
            "negative_prompt": "bad",
            "style_keywords": ["dark", "premium"],
        },
        "scene_01",
    )

    assert workflow["4"]["inputs"]["ckpt_name"] == "model.safetensors"
    assert workflow["5"]["inputs"] == {"width": 1080, "height": 1920, "batch_size": 1}
    assert "dark" in workflow["6"]["inputs"]["text"]
    assert workflow["7"]["inputs"]["text"] == "bad"
    assert workflow["9"]["inputs"]["filename_prefix"] == "scene_01"


def test_video_assembler_runs_ffmpeg_and_validates_final_mp4(tmp_path: Path, monkeypatch) -> None:
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png" * 1000)
    narration = tmp_path / "narration.mp3"
    narration.write_bytes(b"mp3" * 1000)
    subtitles = tmp_path / "narration.srt"
    subtitles.write_text("1\n00:00:00,000 --> 00:00:02,000\nTest\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(cmd, capture_output=False, text=False, timeout=None, check=False, **kwargs):
        commands.append(list(cmd))
        if "ffprobe.exe" in str(cmd[0]):
            payload = {
                "format": {"duration": "8.0"},
                "streams": [
                    {"codec_type": "video", "width": 1080, "height": 1920},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
                ],
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        output = Path(cmd[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video" * 2000)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("atnos.pipeline.video_assembler.resolve_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr("atnos.pipeline.video_assembler.resolve_ffprobe", lambda _ffmpeg=None: "ffprobe.exe")
    monkeypatch.setattr("atnos.pipeline.video_assembler.subprocess.run", fake_run)

    output = VideoAssembler().assemble(
        [SceneResult(scene_id="0", frames=[frame], duration_seconds=5, method="pil_fallback")],
        narration,
        subtitles,
        tmp_path,
    )

    assert output == tmp_path / "short.mp4"
    assert output.exists()
    assert any("subtitles=" in " ".join(command) for command in commands)
