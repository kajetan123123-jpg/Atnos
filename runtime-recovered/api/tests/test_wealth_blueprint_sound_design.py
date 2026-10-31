from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path

from atnos.production.sound_design import (
    SEMANTIC_SOUND_EVENTS,
    SFX_EVENTS,
    MusicBedRequest,
    SoundAssetRegistry,
    SoundCatalog,
    SoundEventRequest,
    SoundMixPolicy,
    VoiceTrackSpec,
    WealthBlueprintSoundDesignEngine,
    filter_sfx_for_pacing,
    load_catalog,
)


def _tone_wav(path: Path, *, seconds: float = 0.3, freq: float = 440.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24_000
    n = max(1, int(seconds * rate))
    samples = array("h")
    for i in range(n):
        envelope = min(1.0, i / 200) * min(1.0, (n - i) / 400)
        samples.append(int(8000 * envelope * math.sin(2 * math.pi * freq * (i / rate))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def _seed_library(root: Path) -> SoundCatalog:
    catalog = load_catalog()
    for asset in catalog.assets:
        _tone_wav(root / asset.file_path, seconds=min(0.8, asset.duration_sec or 0.3), freq=220 + hash(asset.id) % 400)
    return catalog


def test_catalog_covers_required_semantic_events():
    catalog = load_catalog()
    events = {asset.event for asset in catalog.assets if asset.approved}
    for event in SFX_EVENTS:
        assert event in events
    assert "bed_editorial" in events
    assert "ambience_soft" in events
    assert set(SEMANTIC_SOUND_EVENTS) >= events


def test_registry_selects_by_event_not_filepath(tmp_path: Path):
    root = tmp_path / "sounds"
    catalog = _seed_library(root)
    # Rewrite paths relative to temp root by saving catalog there
    from atnos.production.sound_design.catalog import save_catalog

    save_catalog(catalog, root / "catalog.json")
    registry = SoundAssetRegistry(library_root=root, catalog=catalog)
    asset = registry.select("soft_whoosh", seed="demo")
    assert asset.event == "soft_whoosh"
    assert asset.approved is True
    assert "soft_whoosh" in asset.id
    assert registry.resolve_file(asset).is_file()


def test_density_drops_spammy_sfx():
    policy = SoundMixPolicy(min_sfx_gap_sec=0.5, max_sfx_per_minute=4, max_sfx_total=4)
    events = [
        SoundEventRequest(event="pop", at_sec=0.0),
        SoundEventRequest(event="click", at_sec=0.1),
        SoundEventRequest(event="coin", at_sec=0.2),
        SoundEventRequest(event="money", at_sec=0.8),
        SoundEventRequest(event="notification", at_sec=1.4),
        SoundEventRequest(event="soft_whoosh", at_sec=2.2, priority=10),
        SoundEventRequest(event="rise", at_sec=2.9),
        SoundEventRequest(event="drop", at_sec=3.6),
    ]
    kept, dropped = filter_sfx_for_pacing(events, duration_sec=60.0, policy=policy)
    sfx_kept = [item for item in kept if item.event in SFX_EVENTS]
    assert len(sfx_kept) <= 4
    assert dropped
    assert any(item.event == "soft_whoosh" for item in sfx_kept)
    # Nearby click at 0.1 loses to pop at 0.0
    assert any(item.get("reason") == "min_gap" for item in dropped)


def test_mix_plan_ducks_music_and_limits(tmp_path: Path):
    root = tmp_path / "sounds"
    catalog = _seed_library(root)
    from atnos.production.sound_design.catalog import save_catalog

    save_catalog(catalog, root / "catalog.json")
    voice_path = tmp_path / "narration.wav"
    _tone_wav(voice_path, seconds=5.0, freq=180)

    engine = WealthBlueprintSoundDesignEngine(
        registry=SoundAssetRegistry(library_root=root, catalog=catalog),
        policy=SoundMixPolicy(music_duck_db=-12.0, limiter_ceiling_db=-1.5),
    )
    plan = engine.plan(
        voice=VoiceTrackSpec(
            file_path=str(voice_path),
            duration_sec=5.0,
            speech_windows=[(0.2, 4.5)],
        ),
        music=MusicBedRequest(event="bed_editorial", gain_db=-18.0),
        events=[
            SoundEventRequest(event="soft_whoosh", at_sec=1.0),
            SoundEventRequest(event="pop", at_sec=1.05),  # likely dropped by gap
            SoundEventRequest(event="rise", at_sec=3.2),
        ],
        seed="wb-demo",
    )
    assert plan.voice is not None
    assert plan.voice.layer == "VOICE"
    assert any(clip.layer == "MUSIC" and clip.ducked for clip in plan.clips)
    assert "alimiter" in plan.ffmpeg_filter_complex
    assert "loudnorm" in plan.ffmpeg_filter_complex
    assert "amix" in plan.ffmpeg_filter_complex
    # Music duck expression present
    assert "between(t" in plan.ffmpeg_filter_complex or "volume=" in plan.ffmpeg_filter_complex
    sfx_clips = [clip for clip in plan.clips if clip.layer == "SFX"]
    assert sfx_clips
    assert all(clip.gain_db <= -3.0 for clip in sfx_clips)


def test_scenes_never_require_hardcoded_paths():
    request = SoundEventRequest(event="coin", at_sec=2.5, gain_db=-10)
    payload = request.model_dump()
    assert "file_path" not in payload
    assert payload["event"] == "coin"


def test_render_mix_plan_executes_ffmpeg(tmp_path: Path):
    from atnos.production.sound_design import render_mix_plan
    from atnos.production.sound_design.catalog import save_catalog

    root = tmp_path / "sounds"
    catalog = _seed_library(root)
    save_catalog(catalog, root / "catalog.json")
    voice_path = tmp_path / "narration.wav"
    _tone_wav(voice_path, seconds=3.0, freq=180)
    engine = WealthBlueprintSoundDesignEngine(
        registry=SoundAssetRegistry(library_root=root, catalog=catalog),
        policy=SoundMixPolicy(music_duck_db=-12.0, limiter_ceiling_db=-1.5),
    )
    plan = engine.plan(
        voice=VoiceTrackSpec(file_path=str(voice_path), duration_sec=3.0, speech_windows=[(0.1, 2.8)]),
        music=MusicBedRequest(event="bed_editorial", gain_db=-18.0),
        events=[
            SoundEventRequest(event="soft_whoosh", at_sec=0.2),
            SoundEventRequest(event="pop", at_sec=1.0),
            SoundEventRequest(event="rise", at_sec=2.0),
        ],
        seed="wb-mix-exec",
    )
    assert len([c for c in plan.clips if c.layer == "SFX"]) >= 1
    assert any(c.layer == "MUSIC" for c in plan.clips)
    out = tmp_path / "mix.aac"
    result = render_mix_plan(plan, out)
    assert result["ok"] is True
    assert out.is_file() and out.stat().st_size > 500
