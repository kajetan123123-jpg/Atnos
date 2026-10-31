from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.workers.tools.image_gen_browser import ImageGenerationBrowserTool
from atnos.jarvis.workers.tools.llm_browser import LlmBrowserTool
from atnos.jarvis.workers.tools.local_tools import (
    LocalImageGenerationTool,
    LocalLlmTool,
    LocalTranscriptionTool,
    LocalVideoEditTool,
)
from atnos.jarvis.workers.tools.transcribe_browser import TranscribeBrowserTool
from atnos.jarvis.workers.tools.video_editor_browser import VideoEditorBrowserTool


@dataclass(slots=True)
class ToolChain:
    primary: Any
    fallback: Any | None = None


class WorkerRegistry:
    def __init__(self, settings: Settings, config_store: JarvisConfigStore, session_manager) -> None:
        self.local_image = LocalImageGenerationTool(settings, config_store)
        self.local_transcription = LocalTranscriptionTool(settings)
        self.local_video_edit = LocalVideoEditTool(settings, config_store)
        self.local_llm = LocalLlmTool(settings, config_store)
        self.image_browser = ImageGenerationBrowserTool(settings, config_store, session_manager, self.local_image)
        self.transcribe_browser = TranscribeBrowserTool(settings, config_store, session_manager, self.local_transcription)
        self.video_browser = VideoEditorBrowserTool(settings, config_store, session_manager, self.local_video_edit)
        self.llm_browser = LlmBrowserTool(settings, config_store, session_manager, self.local_llm)
        self._chains = {
            "image_gen": ToolChain(primary=self.local_image, fallback=self.image_browser),
            "transcription": ToolChain(primary=self.local_transcription, fallback=self.transcribe_browser),
            "video_edit": ToolChain(primary=self.local_video_edit, fallback=self.video_browser),
            "llm": ToolChain(primary=self.local_llm, fallback=self.llm_browser),
        }

    async def resolve(self, kind: str, **kwargs):
        chain = self._chains.get(kind)
        if chain is None:
            return None
        if await chain.primary.is_available(**kwargs):
            return chain.primary
        if chain.fallback is not None and await chain.fallback.is_available(**kwargs):
            return chain.fallback
        return chain.primary if chain.primary is not None else chain.fallback

    def describe(self) -> dict[str, Any]:
        return {
            key: {
                "primary": value.primary.describe(),
                "fallback": value.fallback.describe() if value.fallback is not None else None,
            }
            for key, value in self._chains.items()
        }
