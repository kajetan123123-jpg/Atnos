"""Runtime enforcement for the per-channel ComfyUI and voice contracts.

The source manifest lives on the Atnos drive and is treated as immutable input
for an order.  A frozen copy is stored with the order profile so later edits to
the manifest cannot silently change a running job.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from atnos.production.project_profile import content_hash


PROJECTS = {"wealth_blueprint", "children_stories", "horror_stories"}
MANIFEST_NAME = "channel_comfy_projects.json"


class ChannelContractError(ValueError):
    """A production order cannot safely use the selected channel contract."""


def _manifest_candidates(settings) -> list[Path]:
    app_root = Path(settings.app_root).resolve()
    data_parent = Path(settings.data_dir).resolve().parent
    module_api = Path(__file__).resolve().parents[3]
    return [
        app_root / "config" / MANIFEST_NAME,
        data_parent / "config" / MANIFEST_NAME,
        module_api / "config" / MANIFEST_NAME,
    ]


def manifest_path(settings) -> Path | None:
    seen: set[Path] = set()
    for candidate in _manifest_candidates(settings):
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def _inside(base: Path, value: Path, label: str) -> Path:
    try:
        resolved = value.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise ChannelContractError(f"{label} escapes its configured root")
        return resolved
    except OSError as exc:
        raise ChannelContractError(f"Unable to resolve {label}: {exc}") from exc


def _resolve_config_path(raw: str, *, manifest: Path, settings) -> Path:
    value = Path(str(raw))
    if value.is_absolute():
        return value.resolve()
    return (manifest.parent / value).resolve()


_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def _sha256(path: Path) -> str:
    """Content hash with process-local cache keyed by path + mtime + size.

    validate_frozen runs on every stage begin; re-reading multi-GB checkpoints
    each time makes resume of long orders unusable without changing contract semantics.
    """
    resolved = path.resolve()
    stat = resolved.stat()
    key = (str(resolved), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _SHA_CACHE.get(key)
    if cached is not None:
        return cached
    with resolved.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _SHA_CACHE[key] = digest
    return digest


def _expected_voice(profile: dict[str, Any], project: dict[str, Any]) -> tuple[str, str]:
    configured = project.get("voice") if isinstance(project.get("voice"), dict) else {}
    expected_provider = str(configured.get("provider") or "").strip().casefold()
    expected_voice = str(configured.get("voice") or "").strip()
    actual = profile.get("production", {}).get("voice", {})
    actual_provider = str(actual.get("provider") or "").strip().casefold()
    actual_voice = str(actual.get("voice_id") or "").strip()
    candidate = profile.get('voice_candidate')
    if isinstance(candidate, dict) and candidate.get('id') == 'Ryan-B':
        from atnos.production.local_voice_lane import MODEL_ID, CLEAR_PRESET
        if (profile.get('project') != 'wealth_blueprint'
                or candidate != {'id': 'Ryan-B', 'scope': 'local_trial', 'preset': CLEAR_PRESET, 'user_approved_as_channel_default': False}
                or profile.get('provider_policy') != 'strict_local'
                or actual_provider != 'qwen_local' or actual_voice != 'Ryan' or actual.get('model') != MODEL_ID):
            raise ChannelContractError('Unknown or incompatible local Ryan selection')
        return actual_provider, actual_voice
    if candidate is not None:
        if (profile.get('project') != 'wealth_blueprint' or candidate != {
                'id': 'Roger-v3', 'scope': 'local_trial', 'user_approved_as_channel_default': False}
                or actual_provider != 'elevenlabs' or actual_voice != 'CwhRBWXzGAHq8TQ4Fs17'
                or actual.get('model') != 'eleven_v3'):
            raise ChannelContractError('Unknown or incompatible local voice candidate')
        return actual_provider, actual_voice
    # Story profiles use the channel contract as the source of their voice;
    # the legacy Wealth profile has an ElevenLabs voice id.  Both must be
    # explicit and the contract is copied into the order for later stages.
    if actual_provider and actual_voice and actual_provider == expected_provider and actual_voice == expected_voice:
        return expected_provider, expected_voice
    if not actual_provider or not actual_voice:
        return expected_provider, expected_voice
    raise ChannelContractError(
        f"Voice mismatch for channel: expected {expected_provider}/{expected_voice}, "
        f"profile has {actual_provider}/{actual_voice}"
    )


def load_and_validate(settings, project: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    """Load and validate the selected channel contract.

    A missing manifest is tolerated only for isolated test runtimes which do
    not ship the production configuration.  On the real E: runtime the
    manifest is present and every failure is a hard admission error.
    """
    key = str(project).strip().casefold().replace(" ", "_")
    if key not in PROJECTS:
        raise ChannelContractError(f"Unsupported channel project: {key}")
    source = manifest_path(settings)
    if source is None:
        return None
    try:
        manifest = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChannelContractError(f"Channel contract cannot be read: {exc}") from exc
    selected = manifest.get("projects", {}).get(key)
    if not isinstance(selected, dict):
        raise ChannelContractError(f"Channel contract is missing project {key}")

    project_dir = _resolve_config_path(selected.get("comfy_project_dir", ""), manifest=source, settings=settings)
    if not project_dir.is_dir() or not (project_dir / "PROJECT.md").is_file():
        raise ChannelContractError(f"ComfyUI project is missing for {key}: {project_dir}")

    reference_files: list[str] = []
    reference_hashes: dict[str, str] = {}
    for raw in ((selected.get("visual") or {}).get("reference_files") or []):
        reference = Path(str(raw)).resolve()
        if not reference.is_file() or reference.stat().st_size <= 0:
            raise ChannelContractError(f"Reference artwork is missing for {key}: {reference}")
        reference_files.append(str(reference))
        reference_hashes[str(reference)] = _sha256(reference)

    models_root = _resolve_config_path(manifest.get("comfy_models_root", ""), manifest=source, settings=settings)
    if not models_root.is_dir():
        raise ChannelContractError(f"ComfyUI models root is missing: {models_root}")
    model_names = list(selected.get("image_models") or [])
    fallback = str(selected.get("image_fallback") or "").strip()
    if fallback:
        model_names.append(fallback)
    model_files: list[dict[str, str]] = []
    for name in model_names:
        path = _inside(models_root, models_root / name, f"model {name}")
        if not path.is_file() or path.stat().st_size <= 0:
            raise ChannelContractError(f"Required model is missing for {key}: {path}")
        model_files.append({"name": name, "path": str(path), "sha256": _sha256(path)})

    provider, voice = _expected_voice(profile, selected)
    if not provider or not voice:
        raise ChannelContractError(f"Channel {key} has no explicit voice assignment")
    contract = {
        "manifest_path": str(source),
        "manifest_sha256": content_hash(manifest),
        "project": key,
        "project_dir": str(project_dir),
        "reference_files": reference_files,
        "reference_hashes": reference_hashes,
        "models_root": str(models_root),
        "model_files": model_files,
        "voice": copy.deepcopy(selected.get("voice") or {}),
        "visual": copy.deepcopy(selected.get("visual") or {}),
        "language": copy.deepcopy(selected.get("language") or {}),
        "ollama_models": copy.deepcopy(manifest.get("ollama_models") or {}),
        "production_gate": copy.deepcopy(manifest.get("production_gate") or {}),
        "style_contract_hash": content_hash({
            "visual": selected.get("visual") or {},
            "language": selected.get("language") or {},
            "project": key,
            "reference_hashes": reference_hashes,
        }),
        "voice_contract_hash": content_hash({"provider": provider, "voice": voice, "voice_config": selected.get("voice") or {}}),
        "workflow_contract_hash": content_hash({
            "project_dir": str(project_dir),
            "models": model_files,
        }),
    }
    contract["contract_hash"] = content_hash({
        "project": key,
        "style_contract_hash": contract["style_contract_hash"],
        "voice_contract_hash": contract["voice_contract_hash"],
        "workflow_contract_hash": contract["workflow_contract_hash"],
        "production_gate": contract["production_gate"],
    })
    return contract


def validate_frozen(settings, project: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    """Verify that a stored contract is complete and still points at E: files."""
    stored = profile.get("channel_contract")
    if stored is None:
        # No manifest means an isolated test profile.  A real runtime with a
        # manifest must never proceed without a frozen contract.
        if manifest_path(settings) is not None:
            raise ChannelContractError("Frozen channel contract is missing")
        return None
    if stored.get("project") != str(project).strip().casefold().replace(" ", "_"):
        raise ChannelContractError("Frozen channel contract belongs to another channel")
    for item in stored.get("model_files") or []:
        path = Path(str(item.get("path") or "")).resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise ChannelContractError(f"Frozen channel model is missing: {path}")
    project_dir = Path(str(stored.get("project_dir") or "")).resolve()
    if not project_dir.is_dir() or not (project_dir / "PROJECT.md").is_file():
        raise ChannelContractError("Frozen ComfyUI project is missing")
    current = load_and_validate(settings, project, profile)
    if current is None:
        return stored
    if current["style_contract_hash"] != stored.get("style_contract_hash"):
        raise ChannelContractError("Channel style contract changed after order creation")
    if current["voice_contract_hash"] != stored.get("voice_contract_hash"):
        raise ChannelContractError("Channel voice contract changed after order creation")
    if current["workflow_contract_hash"] != stored.get("workflow_contract_hash"):
        raise ChannelContractError("ComfyUI workflow contract changed after order creation")
    if current["contract_hash"] != stored.get("contract_hash"):
        raise ChannelContractError("Channel contract changed after order creation")
    return stored
