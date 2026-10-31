from __future__ import annotations

from pathlib import Path

from atnos.production.sound_design.density import filter_sfx_for_pacing
from atnos.production.sound_design.models import (
    AmbienceRequest,
    MusicBedRequest,
    ResolvedSoundClip,
    SoundEventRequest,
    SoundMixPlan,
    SoundMixPolicy,
    VoiceTrackSpec,
)
from atnos.production.sound_design.registry import SoundAssetRegistry, SoundRegistryError


class SoundMixError(RuntimeError):
    pass


class SoundMixEngine:
    """Resolve semantic events → mix plan with ducking + limiter (voice always wins)."""

    def __init__(self, registry: SoundAssetRegistry, *, policy: SoundMixPolicy | None = None) -> None:
        self.registry = registry
        self.policy = policy or SoundMixPolicy()

    def build_plan(
        self,
        *,
        voice: VoiceTrackSpec,
        events: list[SoundEventRequest] | None = None,
        music: MusicBedRequest | None = None,
        ambience: AmbienceRequest | None = None,
        seed: str | None = None,
    ) -> SoundMixPlan:
        if not self.policy.voice_priority:
            raise SoundMixError("Voice-over must retain mix priority")

        events = list(events or [])
        paced, dropped = filter_sfx_for_pacing(events, duration_sec=voice.duration_sec, policy=self.policy)
        clips: list[ResolvedSoundClip] = []
        notes = [
            "Voice-over has absolute priority over MUSIC/SFX/AMBIENCE",
            "SFX are planner-declared only — never auto-attached to motion",
        ]

        voice_clip = ResolvedSoundClip(
            layer="VOICE",
            event="voice_over",
            asset_id="voice",
            file_path=voice.file_path,
            start_sec=0.0,
            end_sec=voice.duration_sec,
            gain_db=voice.gain_db,
            fade_in_sec=0.0,
            fade_out_sec=0.05,
            variant="primary",
            ducked=False,
        )

        if music is not None:
            clips.append(self._resolve_loopable(music.event, music, voice.duration_sec, seed=seed, layer_hint="MUSIC"))
            notes.append(f"Music ducked by {self.policy.music_duck_db} dB under narration windows")

        if ambience is not None:
            clips.append(
                self._resolve_loopable(ambience.event, ambience, voice.duration_sec, seed=seed, layer_hint="AMBIENCE")
            )

        for request in paced:
            try:
                asset = self.registry.select(
                    request.event,
                    prefer_variant=request.prefer_variant,
                    allow_random_variant=request.allow_random_variant,
                    seed=f"{seed or 'mix'}:{request.event}:{request.at_sec}",
                )
            except SoundRegistryError as exc:
                dropped.append({"event": request.event, "at_sec": request.at_sec, "reason": str(exc)})
                continue
            path = self.registry.resolve_file(asset)
            duration = request.duration_sec or asset.duration_sec or 0.45
            gain = request.gain_db if request.gain_db is not None else asset.default_gain_db
            # Voice priority: SFX cannot exceed -3 dB relative to voice
            gain = min(gain, voice.gain_db - 3.0)
            clips.append(
                ResolvedSoundClip(
                    layer=asset.layer,
                    event=asset.event,
                    asset_id=asset.id,
                    file_path=str(path),
                    start_sec=request.at_sec,
                    end_sec=min(voice.duration_sec, request.at_sec + duration),
                    gain_db=gain,
                    fade_in_sec=request.fade_in_sec,
                    fade_out_sec=request.fade_out_sec,
                    variant=asset.variant,
                )
            )

        filter_complex = build_ffmpeg_filter_complex(
            voice=voice_clip,
            clips=clips,
            speech_windows=voice.speech_windows or [(0.0, voice.duration_sec)],
            policy=self.policy,
        )
        return SoundMixPlan(
            duration_sec=voice.duration_sec,
            voice=voice_clip,
            clips=clips,
            dropped_events=dropped,
            policy=self.policy,
            ffmpeg_filter_complex=filter_complex,
            notes=notes,
        )

    def _resolve_loopable(self, event, request, duration_sec: float, *, seed: str | None, layer_hint: str) -> ResolvedSoundClip:
        asset = self.registry.select(
            event,
            allow_random_variant=request.allow_random_variant,
            seed=f"{seed or 'mix'}:{event}",
        )
        path = self.registry.resolve_file(asset)
        gain = min(request.gain_db, -6.0)  # beds stay under voice
        return ResolvedSoundClip(
            layer=asset.layer,
            event=asset.event,
            asset_id=asset.id,
            file_path=str(path),
            start_sec=0.0,
            end_sec=duration_sec,
            gain_db=gain,
            fade_in_sec=request.fade_in_sec,
            fade_out_sec=request.fade_out_sec,
            variant=asset.variant,
            ducked=True,
        )


def build_ffmpeg_filter_complex(
    *,
    voice: ResolvedSoundClip,
    clips: list[ResolvedSoundClip],
    speech_windows: list[tuple[float, float]],
    policy: SoundMixPolicy,
) -> str:
    """Build a filtergraph: voice + ducked music/ambience + timed SFX + limiter.

    Input layout expected by the caller:
      [0]=voice, [1..N]=clips in the same order as `clips`
    """
    chains: list[str] = []
    labels: list[str] = []

    # Voice — priority reference path
    voice_gain = 10 ** (voice.gain_db / 20.0)
    chains.append(
        f"[0:a]atrim=0:{voice.end_sec:.3f},asetpts=PTS-STARTPTS,"
        f"volume={voice_gain:.6f},afade=t=out:st={max(0.0, voice.end_sec - voice.fade_out_sec):.3f}:"
        f"d={max(0.01, voice.fade_out_sec):.3f}[voice]"
    )
    labels.append("[voice]")

    for index, clip in enumerate(clips, start=1):
        label = f"c{index}"
        gain = 10 ** (clip.gain_db / 20.0)
        delay_ms = int(round(clip.start_sec * 1000))
        trim_end = max(0.05, clip.end_sec - clip.start_sec)
        fade_out_start = max(0.0, trim_end - clip.fade_out_sec)
        base = (
            f"[{index}:a]atrim=0:{trim_end:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={max(0.01, clip.fade_in_sec):.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={max(0.01, clip.fade_out_sec):.3f},"
            f"volume={gain:.6f}"
        )
        if clip.ducked and clip.layer in {"MUSIC", "AMBIENCE"}:
            duck_db = policy.music_duck_db if clip.layer == "MUSIC" else policy.ambience_duck_db
            duck_expr = _duck_volume_expression(speech_windows, duck_db=duck_db, rest_gain=1.0)
            base += f",volume={duck_expr}:eval=frame"
        base += f",adelay={delay_ms}|{delay_ms}[{label}]"
        chains.append(base)
        labels.append(f"[{label}]")

    mix_inputs = "".join(labels)
    count = len(labels)
    # normalize=0 keeps voice level; limiter protects peaks
    ceiling = 10 ** (policy.limiter_ceiling_db / 20.0)
    chains.append(
        f"{mix_inputs}amix=inputs={count}:duration=first:dropout_transition=0:normalize=0,"
        f"alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false,"
        f"loudnorm=I={policy.target_lufs}:TP={policy.limiter_ceiling_db}:LRA=11[mixout]"
    )
    return ";".join(chains)


def _duck_volume_expression(
    windows: list[tuple[float, float]],
    *,
    duck_db: float,
    rest_gain: float,
) -> str:
    """FFmpeg volume expression: duck during speech windows."""
    duck_gain = rest_gain * (10 ** (duck_db / 20.0))
    if not windows:
        return f"{duck_gain:.6f}"
    parts: list[str] = []
    for start, end in windows:
        parts.append(f"between(t\\,{start:.3f}\\,{end:.3f})")
    condition = "+".join(parts)
    return f"if(gt({condition}\\,0)\\,{duck_gain:.6f}\\,{rest_gain:.6f})"


def validate_plan_files(plan: SoundMixPlan, *, require_exist: bool = True) -> list[str]:
    issues: list[str] = []
    paths = [plan.voice.file_path] if plan.voice else []
    paths.extend(clip.file_path for clip in plan.clips)
    for path in paths:
        if require_exist and not Path(path).is_file():
            issues.append(f"missing audio file: {path}")
    return issues


def render_mix_plan(
    plan: SoundMixPlan,
    output_path: Path,
    *,
    ffmpeg: str | None = None,
) -> dict[str, object]:
    """Execute SoundMixPlan via FFmpeg filter_complex → mixed WAV/AAC for remux.

    Voice is input [0]; plan.clips are [1..N] in order. Fail-closed on missing files.
    """
    import subprocess

    from atnos.production.media_tools import resolve_ffmpeg

    exe = ffmpeg or resolve_ffmpeg()
    if not exe:
        raise SoundMixError("FFmpeg unavailable for sound mix")
    issues = validate_plan_files(plan, require_exist=True)
    if issues:
        raise SoundMixError("; ".join(issues))
    if not plan.voice:
        raise SoundMixError("Sound mix requires voice track")
    if not (plan.ffmpeg_filter_complex or "").strip():
        raise SoundMixError("Sound mix plan missing ffmpeg_filter_complex")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    command = [exe, "-y", "-i", plan.voice.file_path]
    for clip in plan.clips:
        command.extend(["-i", clip.file_path])
    command.extend(
        [
            "-filter_complex",
            plan.ffmpeg_filter_complex,
            "-map",
            "[mixout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ]
    )
    timeout_s = max(120, min(600, int(plan.duration_sec * 8 + 60)))
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        output.unlink(missing_ok=True)
        raise SoundMixError(f"sound mix timed out after {timeout_s}s: {exc}") from exc
    if process.returncode != 0 or not output.is_file() or output.stat().st_size < 256:
        output.unlink(missing_ok=True)
        tail = (process.stderr or process.stdout or "")[-500:]
        raise SoundMixError(f"sound mix ffmpeg failed rc={process.returncode}: {tail}")
    return {
        "ok": True,
        "output_file": str(output),
        "duration_sec": plan.duration_sec,
        "clip_count": len(plan.clips),
        "dropped_events": plan.dropped_events,
        "limiter_ceiling_db": plan.policy.limiter_ceiling_db,
        "music_duck_db": plan.policy.music_duck_db,
    }
