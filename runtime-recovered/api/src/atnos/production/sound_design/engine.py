from __future__ import annotations

from pathlib import Path

from atnos.production.sound_design.catalog import default_sounds_root, load_catalog
from atnos.production.sound_design.mixer import SoundMixEngine
from atnos.production.sound_design.models import (
    AmbienceRequest,
    MusicBedRequest,
    SoundEventRequest,
    SoundMixPlan,
    SoundMixPolicy,
    VoiceTrackSpec,
)
from atnos.production.sound_design.registry import SoundAssetRegistry


class WealthBlueprintSoundDesignEngine:
    """Scene Planner → semantic sound events → mix plan (FFmpeg-ready).

    Layers: VOICE (priority) · MUSIC (ducked) · SFX (sparse) · AMBIENCE
    """

    def __init__(
        self,
        *,
        registry: SoundAssetRegistry | None = None,
        policy: SoundMixPolicy | None = None,
        library_root: Path | None = None,
    ) -> None:
        if registry is not None:
            self.registry = registry
        else:
            root = library_root or default_sounds_root()
            self.registry = SoundAssetRegistry(
                library_root=root,
                catalog=load_catalog(root / "catalog.json"),
            )
        self.mixer = SoundMixEngine(self.registry, policy=policy)

    def plan(
        self,
        *,
        voice: VoiceTrackSpec,
        events: list[SoundEventRequest] | None = None,
        music: MusicBedRequest | None = None,
        ambience: AmbienceRequest | None = None,
        seed: str | None = None,
    ) -> SoundMixPlan:
        return self.mixer.build_plan(
            voice=voice,
            events=events,
            music=music,
            ambience=ambience,
            seed=seed,
        )

    def select_event(self, event: str, **kwargs):
        return self.registry.select(event, **kwargs)  # type: ignore[arg-type]
