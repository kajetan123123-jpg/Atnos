from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from atnos.production.asset_brain import (
    AssetCatalog,
    AssetGenerationRequest,
    AssetQuery,
    AssetRecord,
    WealthBlueprintAssetBrain,
    WealthBlueprintAssetProvisioner,
    load_catalog,
    request_fingerprint,
)
from atnos.production.asset_brain.generation_cache import GenerationCache
from atnos.production.asset_brain.prompts import build_prompt, resolve_workflow_id
from atnos.services.comfyui_image_generation_service import ComfyUiImageResult


def _png(width: int, height: int, color=(20, 40, 60)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=color).save(buf, format="PNG")
    return buf.getvalue()


class FakeComfy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_background(
        self,
        *,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str | None = None,
        steps_override: int | None = None,
        cfg_scale_override: float | None = None,
        quality_mode: bool = False,
    ) -> ComfyUiImageResult:
        self.calls.append(
            {
                "prompt": prompt,
                "output_path": output_path,
                "width": width,
                "height": height,
                "seed": seed,
                "negative_prompt": negative_prompt,
                "quality_mode": quality_mode,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_png(width, height))
        evidence = output_path.with_suffix(output_path.suffix + ".comfyui.json")
        evidence.write_text("{}", encoding="utf-8")
        return ComfyUiImageResult(
            output_path=output_path.resolve(),
            prompt_id="fake-prompt-1",
            filename=output_path.name,
            subfolder="",
            image_type="output",
            backend="fake_flux",
            evidence_path=evidence.resolve(),
        )


@pytest.fixture()
def library_root(tmp_path: Path) -> Path:
    root = tmp_path / "wealth_blueprint"
    root.mkdir()
    (root / "placeholders").mkdir()
    catalog = AssetCatalog(assets=[])
    (root / "catalog.json").write_text(
        catalog.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def brain(library_root: Path) -> WealthBlueprintAssetBrain:
    return WealthBlueprintAssetBrain(
        catalog=load_catalog(library_root / "catalog.json"),
        catalog_file=library_root / "catalog.json",
        library_root=library_root,
        embedding_client=None,
    )


def test_fingerprint_is_stable_and_changes_with_pose():
    a = AssetGenerationRequest(type="icon", tags=["savings"], text="piggy bank")
    b = AssetGenerationRequest(type="icon", tags=["savings"], text="piggy bank")
    c = AssetGenerationRequest(type="icon", tags=["savings"], text="piggy bank", pose="tilted")
    assert request_fingerprint(a) == request_fingerprint(b)
    assert request_fingerprint(a) != request_fingerprint(c)


def test_resolve_reuses_library_without_comfy(brain: WealthBlueprintAssetBrain, library_root: Path):
    png = library_root / "placeholders" / "icon.png"
    png.write_bytes(_png(64, 64))
    brain.catalog = AssetCatalog(
        assets=[
            AssetRecord(
                id="icon_piggy_v01",
                type="icon",
                file_path="placeholders/icon.png",
                channel="wealth_blueprint",
                tags=["savings", "money"],
                approved=True,
                source="test",
                license_status="owned",
            )
        ]
    )
    fake = FakeComfy()
    provisioner = WealthBlueprintAssetProvisioner(
        brain=brain,
        image_service=fake,
        cache=GenerationCache(library_root / "generation_cache.json"),
        persist_catalog=False,
    )
    result = provisioner.resolve_or_generate(
        AssetQuery(type="icon", tags_any=["savings"], text="piggy")
    )
    assert result.status == "reused_library"
    assert result.comfy_called is False
    assert fake.calls == []
    assert result.asset.id == "icon_piggy_v01"


def test_missing_icon_generates_registers_and_caches(brain: WealthBlueprintAssetBrain, library_root: Path):
    fake = FakeComfy()
    provisioner = WealthBlueprintAssetProvisioner(
        brain=brain,
        image_service=fake,
        cache=GenerationCache(library_root / "generation_cache.json"),
        persist_catalog=True,
    )
    query = AssetQuery(
        type="icon",
        tags_any=["rocket", "goal"],
        text="goal rocket icon",
        require_approved=True,
        min_semantic_score=0.99,
    )
    first = provisioner.resolve_or_generate(query)
    assert first.status == "generated"
    assert first.comfy_called is True
    assert len(fake.calls) == 1
    assert first.asset.generation is not None
    assert first.asset.generation.prompt
    assert first.asset.generation.workflow_id
    assert first.asset.generation.seed is not None
    assert first.asset.generation.model == "fake_flux"
    assert first.asset.generation.generated_at
    assert first.asset.generation.source_workflow
    assert first.asset.generation.channel == "wealth_blueprint"
    assert first.asset.generation.approval_state == "pending"
    assert first.asset.approved is False
    assert Path(brain.resolve_file(first.asset)).is_file()

    # Same request must hit cache — no second Comfy call
    second = provisioner.resolve_or_generate(query)
    assert second.status == "reused_cache"
    assert second.comfy_called is False
    assert len(fake.calls) == 1
    assert second.asset.id == first.asset.id


def test_alex_request_requires_anchors_and_stricter_prompt(brain: WealthBlueprintAssetBrain, library_root: Path):
    fake = FakeComfy()
    # Point channel refs exist on E:; provisioner will load them.
    provisioner = WealthBlueprintAssetProvisioner(
        brain=brain,
        image_service=fake,
        cache=GenerationCache(library_root / "generation_cache.json"),
        persist_catalog=False,
    )
    request = AssetGenerationRequest(
        type="character",
        character="alex",
        pose="explaining",
        emotion="confident",
        direction="right",
        tags=["finance"],
        text="Alex explaining a habit",
        width=512,
        height=768,
    )
    assert request.require_alex_anchors is True
    assert resolve_workflow_id(request) == "wb_alex_character_v1"

    result = provisioner.fulfill_request(request)
    assert result.status == "generated"
    assert fake.calls[0]["quality_mode"] is True
    prompt = fake.calls[0]["prompt"]
    assert "WB_ALEX_01" in prompt or "Alex" in prompt
    assert "reference" in prompt.casefold()
    assert result.asset.generation is not None
    assert result.asset.generation.reference_files
    assert result.asset.generation.approval_state == "pending"
    # Reject inventing look: provenance must cite anchors
    for ref in result.asset.generation.reference_files:
        assert Path(ref).is_file()


def test_video_media_kind_rejected():
    with pytest.raises(Exception):
        AssetGenerationRequest.model_validate(
            {"type": "icon", "media_kind": "video"}
        )


def test_build_prompt_icon_has_no_alex_lock():
    request = AssetGenerationRequest(type="icon", tags=["etf"], text="growth chart glyph")
    prompt, negative = build_prompt(request)
    assert "WB_ALEX" not in prompt
    assert "photorealism" in negative
