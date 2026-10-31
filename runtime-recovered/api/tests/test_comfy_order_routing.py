"""Production Order ↔ Comfy channel routing — leakage, fail-closed, cache/reuse."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from atnos.core.config import Settings
from atnos.production.asset_brain import AssetCatalog, AssetRecord, WealthBlueprintAssetBrain, load_catalog
from atnos.production.comfy_channels.models import GenerationMetadata
from atnos.production.order_comfy_routing import (
    CHANNEL_COMFY_ROLE,
    OrderAssetRequest,
    OrderComfyAssetFulfiller,
    OrderComfyRoutingError,
    OrderRoutingMetrics,
    fulfill_order_asset_requests,
    resolve_order_channel,
    resolve_order_comfy_profile,
    routing_snapshot_for_order,
)
from atnos.production import order_comfy_routing


def _png(width: int = 64, height: int = 64) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), (30, 40, 50)).save(buf, format="PNG")
    return buf.getvalue()


class FakeChannelService:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.calls: list = []
        self.fail_timeouts_remaining = 0

    def generate(self, request) -> GenerationMetadata:
        self.calls.append(request)
        if self.fail_timeouts_remaining > 0:
            self.fail_timeouts_remaining -= 1
            from atnos.production.comfy_channels.service import ChannelComfyGenerationError

            raise ChannelComfyGenerationError("Comfy generation timed out waiting for prompt")
        out = self.output_root / request.channel / f"{len(self.calls)}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(_png(128, 128))
        return GenerationMetadata(
            request_id=f"req{len(self.calls):04d}",
            channel=request.channel,
            workflow_id=f"{request.channel}_wf",
            workflow_version="v1",
            backend="fake",
            model=f"{request.channel}_model",
            seed=request.seed or 1,
            prompt="fake",
            negative_prompt="",
            width=128,
            height=128,
            references=list(request.references),
            reference_paths=[],
            output_path=str(out),
            fingerprint=f"fp-{request.channel}-{len(self.calls)}",
            cache_hit=False,
            timestamp="2026-09-18T00:00:00+00:00",
            generation_settings={"asset_brain_id": None, "prompt_id": f"p{len(self.calls)}"},
        )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")


@pytest.fixture()
def fake_service(tmp_path: Path) -> FakeChannelService:
    return FakeChannelService(tmp_path / "generated")


def test_resolve_order_channel_aliases():
    assert resolve_order_channel("wealth_blueprint") == "wealth_blueprint"
    assert resolve_order_channel("children_stories") == "wonder_crew"
    assert resolve_order_channel("horror_stories") == "dark_chronicles"


def test_wb_bridge_does_not_truncate_composition_to_200(settings, fake_service):
    text = "An open navy wallet and a plain purchase receipt. " + "quiet background " * 15
    assert 200 < len(text) <= 500
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service)
    fulfiller.fulfill(OrderAssetRequest(channel_id="wealth_blueprint", asset_type="icon",
                                       subject="navy wallet", text=text))
    assert fake_service.calls[-1].composition == text.strip()


def test_fail_closed_missing_and_unknown_channel():
    with pytest.raises(OrderComfyRoutingError, match="FAIL_CLOSED"):
        resolve_order_channel(None)
    with pytest.raises(OrderComfyRoutingError, match="FAIL_CLOSED"):
        resolve_order_channel("")
    with pytest.raises(OrderComfyRoutingError, match="FAIL_CLOSED"):
        resolve_order_channel("meme_channel")
    with pytest.raises(OrderComfyRoutingError, match="FAIL_CLOSED"):
        resolve_order_channel("default")


def test_channel_leakage_fixtures_bind_correct_profiles(settings: Settings):
    """Three Production Order fixtures — WB/WC/DC profiles, no cross leakage."""
    fixtures = [
        ("wealth_blueprint", "wealth_blueprint", "FILM_STILL_ASSETS"),
        ("children_stories", "wonder_crew", "REFERENCE_ONLY"),
        ("horror_stories", "dark_chronicles", "CONCEPT"),
        ("wonder_crew", "wonder_crew", "REFERENCE_ONLY"),
        ("dark_chronicles", "dark_chronicles", "CONCEPT"),
    ]
    for project, expected_channel, expected_role in fixtures:
        channel_id, profile, role = resolve_order_comfy_profile(settings, project)
        assert channel_id == expected_channel
        assert profile.channel_id == expected_channel
        assert profile.output_subdir == expected_channel
        assert role == expected_role
        assert CHANNEL_COMFY_ROLE[channel_id] == expected_role
        snap = routing_snapshot_for_order(settings, project)
        assert snap["channel_id"] == expected_channel
        assert snap["profile_id"] == expected_channel
        assert snap["fail_closed"] is True
        # No silent default — profile id equals resolved channel
        assert snap["comfy_role"] == expected_role


def test_routing_snapshot_records_effective_model_profile(settings: Settings, monkeypatch):
    monkeypatch.delenv("ATNOS_WB_ILLUSTRATION_CANDIDATE", raising=False)
    effective = {
        "quality_model": "checkpoints/dreamshaper_sdxl_alpha2_wb_candidate.safetensors",
        "quality_fallback_model": "",
        "img2img_preset": "WB_IMG2IMG_V1",
        "reference_mode": "core_img2img",
        "reference_denoise": 0.4,
        "required_quality": True,
    }
    monkeypatch.setattr(order_comfy_routing, "load_model_profile", lambda *_: effective)

    snapshot = routing_snapshot_for_order(settings, "wealth_blueprint")

    assert snapshot["effective_model_profile"] == effective


def test_wc_rejects_film_final_comfy_renderer(settings: Settings, fake_service: FakeChannelService):
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service)
    with pytest.raises(OrderComfyRoutingError, match="REFERENCE_ONLY"):
        fulfiller.fulfill(
            OrderAssetRequest(
                channel_id="wonder_crew",
                asset_type="character",
                subject="niko",
                purpose="film_final",
            )
        )
    assert fake_service.calls == []


def test_dc_rejects_film_final_uses_concept(settings: Settings, fake_service: FakeChannelService):
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service)
    with pytest.raises(OrderComfyRoutingError, match="CONCEPT"):
        fulfiller.fulfill(
            OrderAssetRequest(
                channel_id="dark_chronicles",
                asset_type="location",
                subject="abandoned corridor",
                purpose="film_final",
            )
        )
    result = fulfiller.fulfill(
        OrderAssetRequest(
            channel_id="dark_chronicles",
            asset_type="location",
            subject="abandoned corridor",
            purpose="concept",
        )
    )
    assert result.profile_id == "dark_chronicles"
    assert result.comfy_role == "CONCEPT"
    assert result.usable_in_final_film is False
    assert fake_service.calls[-1].channel == "dark_chronicles"


def test_cross_channel_request_on_order_fail_closed(settings: Settings, fake_service: FakeChannelService):
    with pytest.raises(OrderComfyRoutingError, match="leaks"):
        fulfill_order_asset_requests(
            settings,
            project="wealth_blueprint",
            requests=[
                {
                    "channel_id": "wonder_crew",
                    "asset_type": "character",
                    "subject": "niko",
                    "purpose": "reference",
                }
            ],
            channel_service=fake_service,
        )
    assert fake_service.calls == []


def test_wb_asset_brain_hit_skips_comfy(settings: Settings, tmp_path: Path, fake_service: FakeChannelService):
    library = tmp_path / "lib_wb"
    library.mkdir(parents=True)
    png = library / "alex_approved.png"
    png.write_bytes(_png(128, 128))
    catalog = AssetCatalog(
        assets=[
            AssetRecord(
                id="alex_explaining_approved",
                type="character",
                file_path="alex_approved.png",
                channel="wealth_blueprint",
                character="alex",
                pose="explaining",
                tags=["alex"],
                approved=True,
                quality_status="APPROVED",
                source="test",
                license_status="owned",
                description="Approved Alex explaining",
            )
        ]
    )
    (library / "catalog.json").write_text(catalog.model_dump_json(indent=2) + "\n", encoding="utf-8")
    brain = WealthBlueprintAssetBrain(
        catalog=load_catalog(library / "catalog.json"),
        catalog_file=library / "catalog.json",
        library_root=library,
        settings=settings,
        embedding_client=None,
    )
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service, asset_brain=brain)
    result = fulfiller.fulfill(
        OrderAssetRequest(
            channel_id="wealth_blueprint",
            asset_type="character",
            subject="alex",
            pose="explaining",
            purpose="film_final",
        )
    )
    assert result.status == "reused_library"
    assert result.comfy_called is False
    assert result.usable_in_final_film is True
    assert result.asset_brain_id == "alex_explaining_approved"
    assert fake_service.calls == []
    assert fulfiller.metrics.brain_hits == 1
    assert fulfiller.metrics.comfy_calls == 0
    assert fulfiller.metrics.asset_cache_hit_rate == 1.0


def test_wb_miss_generates_alex_awaiting_approval(settings: Settings, tmp_path: Path, fake_service: FakeChannelService):
    library = tmp_path / "lib_wb_empty"
    library.mkdir(parents=True)
    empty = AssetCatalog(assets=[])
    (library / "catalog.json").write_text(empty.model_dump_json(indent=2) + "\n", encoding="utf-8")
    brain = WealthBlueprintAssetBrain(
        catalog=empty,
        catalog_file=library / "catalog.json",
        library_root=library,
        settings=settings,
        embedding_client=None,
    )
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service, asset_brain=brain)
    result = fulfiller.fulfill(
        OrderAssetRequest(
            channel_id="wealth_blueprint",
            asset_type="character",
            subject="alex",
            purpose="film_final",
            allow_generate=True,
        )
    )
    assert result.status == "awaiting_approval"
    assert result.usable_in_final_film is False
    assert result.comfy_called is True
    assert result.profile_id == "wealth_blueprint"
    assert fake_service.calls[0].channel == "wealth_blueprint"
    assert fake_service.calls[0].workflow in {"WB_CHARACTER", "wealth_character_v1"}


def test_wc_reference_routing(settings: Settings, fake_service: FakeChannelService):
    results, metrics = fulfill_order_asset_requests(
        settings,
        project="children_stories",
        requests=[
            OrderAssetRequest(
                channel_id="children_stories",
                asset_type="character",
                subject="niko",
                purpose="reference",
            )
        ],
        channel_service=fake_service,
    )
    assert len(results) == 1
    assert results[0].profile_id == "wonder_crew"
    assert results[0].comfy_role == "REFERENCE_ONLY"
    assert results[0].usable_in_final_film is False
    assert fake_service.calls[0].channel == "wonder_crew"
    assert metrics.comfy_calls == 1


def test_timeout_retry_checks_cache_without_force(settings: Settings, fake_service: FakeChannelService):
    fake_service.fail_timeouts_remaining = 1
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service)
    result = fulfiller.fulfill(
        OrderAssetRequest(
            channel_id="dark_chronicles",
            asset_type="atmosphere",
            subject="foggy pier",
            purpose="concept",
        )
    )
    assert result.status == "generated"
    assert len(fake_service.calls) == 2
    assert fake_service.calls[1].force_regenerate is False


def test_e2e_order_fixture_metadata(settings: Settings, tmp_path: Path, fake_service: FakeChannelService):
    """Production Order fixture → router → correct profile → output/cache → metadata."""
    library = tmp_path / "lib_e2e"
    library.mkdir(parents=True)
    (library / "catalog.json").write_text(AssetCatalog(assets=[]).model_dump_json(indent=2) + "\n", encoding="utf-8")
    brain = WealthBlueprintAssetBrain(
        catalog=AssetCatalog(assets=[]),
        catalog_file=library / "catalog.json",
        library_root=library,
        settings=settings,
        embedding_client=None,
    )
    metrics = OrderRoutingMetrics()
    fulfiller = OrderComfyAssetFulfiller(
        settings, channel_service=fake_service, metrics=metrics, asset_brain=brain
    )
    snap = routing_snapshot_for_order(settings, "wealth_blueprint")
    assert snap["router"].endswith("resolve_channel_id")

    result = fulfiller.fulfill(
        OrderAssetRequest(
            channel_id="wealth_blueprint",
            asset_type="icon",
            subject="piggy bank",
            text="savings icon",
            purpose="film_final",
            seed=42,
        )
    )

    assert result.channel_id == "wealth_blueprint"
    assert result.profile_id == "wealth_blueprint"
    assert result.output_path and Path(result.output_path).is_file()
    assert result.fingerprint
    assert result.comfy_called is True
    assert metrics.generated_assets_per_order >= 1
    assert metrics.as_dict()["generation_time_ms_total"] >= 0
