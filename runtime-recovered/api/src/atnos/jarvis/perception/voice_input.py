from __future__ import annotations

import asyncio
import queue
from typing import Any

from atnos.core.config import Settings
from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent, VoiceCommand


class VoiceInputModule(JarvisModule):
    def __init__(self, *, event_bus, config_store, settings: Settings) -> None:
        super().__init__(name="voice_input", event_bus=event_bus, config_store=config_store)
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._available = True
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        if not self._dependencies_available():
            self._available = False
            self._last_error = "Voice input dependencies are unavailable."
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._listen_loop(), name="jarvis-voice-input")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "available": self._available,
            "last_error": self._last_error,
        }

    async def _listen_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                transcript = await asyncio.to_thread(self._listen_once)
                if transcript:
                    await self.event_bus.publish(JarvisEvent.VOICE_COMMAND, VoiceCommand(text=transcript, duration_s=0.0))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._available = False
                self._last_error = str(exc)
                await asyncio.sleep(2.0)

    def _listen_once(self) -> str | None:
        import numpy as np  # type: ignore
        import pvporcupine  # type: ignore
        import sounddevice as sd  # type: ignore
        from faster_whisper import WhisperModel  # type: ignore
        from scipy.io.wavfile import write as write_wav  # type: ignore
        import tempfile
        from pathlib import Path

        config = self.config_store.current().voice_input
        wake_word_model_path = (config.wake_word_model_path or config.model_path or "").strip()
        if not wake_word_model_path:
            raise RuntimeError("Wake word model path is not configured.")
        porcupine = pvporcupine.create(keyword_paths=[wake_word_model_path])
        audio_queue: queue.Queue[Any] = queue.Queue()
        silence_threshold = 0.01
        max_silence_frames = int(config.silence_duration_s * config.sample_rate / porcupine.frame_length)

        def callback(indata, frames, time, status) -> None:
            if status:
                return
            audio_queue.put(bytes(indata))

        with sd.RawInputStream(
            samplerate=porcupine.sample_rate,
            blocksize=porcupine.frame_length,
            dtype="int16",
            channels=1,
            callback=callback,
        ):
            recorded_frames: list[bytes] = []
            listening = False
            silent_frames = 0
            while not self._stop_event.is_set():
                chunk = audio_queue.get()
                pcm = np.frombuffer(chunk, dtype=np.int16)
                if not listening:
                    if porcupine.process(pcm) >= 0:
                        listening = True
                        recorded_frames.append(chunk)
                    continue
                recorded_frames.append(chunk)
                amplitude = float(np.abs(pcm).mean()) / 32768.0
                if amplitude < silence_threshold:
                    silent_frames += 1
                else:
                    silent_frames = 0
                if silent_frames >= max_silence_frames:
                    break
        if not recorded_frames:
            return None
        audio = np.frombuffer(b"".join(recorded_frames), dtype=np.int16)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as handle:
            temp_path = Path(handle.name)
        write_wav(temp_path.as_posix(), config.sample_rate, audio)
        try:
            model_path = (
                self.config_store.current().voice_input.stt_model
                or self.settings.voice_stt_model_path
                or "small"
            )
            model = WhisperModel(model_path, device="cpu")
            segments, _info = model.transcribe(temp_path.as_posix())
            transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
            return transcript or None
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _dependencies_available() -> bool:
        try:
            import faster_whisper  # type: ignore # noqa: F401
            import pvporcupine  # type: ignore # noqa: F401
            import sounddevice  # type: ignore # noqa: F401
            return True
        except Exception:
            return False
