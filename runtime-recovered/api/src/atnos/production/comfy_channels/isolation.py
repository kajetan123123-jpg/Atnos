"""Cross-channel isolation guards for Comfy profiles."""

from __future__ import annotations

from pathlib import Path

from atnos.production.comfy_channels.models import ChannelComfyProfile, ChannelId, GenerationRequest

FORBIDDEN_SUBJECTS: dict[ChannelId, set[str]] = {
    "wealth_blueprint": {"niko", "pif", "tika", "mira"},
    "wonder_crew": {"alex"},
    "dark_chronicles": {"alex", "niko", "pif", "tika", "mira"},
}

FORBIDDEN_STYLE_TOKENS: dict[ChannelId, set[str]] = {
    "wealth_blueprint": {"horror", "gore", "abandoned corridor", "jump scare"},
    "wonder_crew": {"horror", "gore", "alex", "finance chart", "juggernaut corporate"},
    "dark_chronicles": {"storybook", "soft magical", "ages 4-7", "alex explaining", "compound interest"},
}


class ChannelIsolationError(ValueError):
    pass


def assert_request_isolated(profile: ChannelComfyProfile, request: GenerationRequest) -> None:
    if request.channel != profile.channel_id:
        raise ChannelIsolationError(
            f"Request channel {request.channel} does not match profile {profile.channel_id}"
        )
    subject = (request.subject or "").strip().casefold()
    banned = FORBIDDEN_SUBJECTS.get(profile.channel_id) or set()
    if subject in banned:
        raise ChannelIsolationError(
            f"Subject '{subject}' is forbidden for channel {profile.channel_id} (cross-channel leakage)"
        )
    # Positive intent only — "Avoid: storybook" after QC regen is anti-leakage, not leakage.
    raw_parts = [
        request.subject,
        request.emotion,
        request.composition,
        request.lighting,
        request.camera,
        " ".join(request.references),
    ]
    scrubbed: list[str] = []
    for part in raw_parts:
        text = str(part or "")
        lower = text.casefold()
        for marker in (" avoid:", "\navoid:"):
            idx = lower.find(marker)
            if idx >= 0:
                text = text[:idx]
                lower = text.casefold()
        scrubbed.append(text)
    blob = " ".join(scrubbed).casefold()
    for token in FORBIDDEN_STYLE_TOKENS.get(profile.channel_id) or set():
        if token.casefold() in blob:
            raise ChannelIsolationError(
                f"Prompt/context contains forbidden token '{token}' for {profile.channel_id}"
            )


def assert_output_path_isolated(profile: ChannelComfyProfile, output_path: Path, generated_root: Path) -> None:
    resolved = output_path.resolve()
    expected_root = (generated_root / profile.output_subdir).resolve()
    if not resolved.is_relative_to(expected_root):
        raise ChannelIsolationError(
            f"Output {resolved} escapes channel root {expected_root}"
        )


def assert_reference_belongs(profile: ChannelComfyProfile, reference_path: Path) -> None:
    """Reference must be listed on the profile or live under the channel project references/."""
    allowed = {Path(p).resolve() for p in profile.reference_files}
    resolved = reference_path.resolve()
    if resolved in allowed:
        return
    project_refs = (Path(profile.comfy_project_dir) / "references").resolve()
    if project_refs.is_dir() and resolved.is_relative_to(project_refs):
        return
    # Allow channel_references folder only for matching channel prefix files already in profile list
    raise ChannelIsolationError(f"Reference {resolved} is not approved for {profile.channel_id}")
