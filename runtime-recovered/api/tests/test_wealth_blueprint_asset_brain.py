from __future__ import annotations

from pathlib import Path

import pytest

from atnos.production.asset_brain import (
    ASSET_TYPES,
    AssetCatalog,
    AssetQuery,
    AssetRecord,
    WealthBlueprintAssetBrain,
    load_catalog,
)
from atnos.production.asset_brain.catalog import default_library_root


class FakeEmbeddings:
    def status(self, model: str) -> dict[str, object]:
        return {"model": model, "runtime": "fake"}

    def embed(self, texts, model: str):
        # Simple bag-of-words hashed vectors — online Ollama not required.
        vectors = []
        for text in texts:
            vec = [0.0] * 16
            for token in text.casefold().split():
                vec[hash(token) % 16] += 1.0
            vectors.append(vec)
        return vectors


def test_default_asset_embedding_expires_own_model_after_response():
    from types import SimpleNamespace

    library = WealthBlueprintAssetBrain(
        settings=SimpleNamespace(ollama_base_url='http://127.0.0.1:11435', semantic_knowledge_embedding_model='bge-m3'),
        catalog=AssetCatalog(schema_version='1.0', channel='wealth_blueprint', assets=[]),
    )
    client = library._resolve_embedding_client()
    assert client.keep_alive == 0
    assert client.base_url == 'http://127.0.0.1:11435'


@pytest.fixture()
def brain() -> WealthBlueprintAssetBrain:
    return WealthBlueprintAssetBrain(
        catalog=load_catalog(),
        embedding_client=FakeEmbeddings(),
        embedding_model="fake-embed",
    )


def test_catalog_covers_required_categories_and_example_alex_asset():
    from atnos.core.config import get_settings

    catalog = load_catalog()
    assert catalog.schema_version == "1.0"
    assert catalog.channel == "wealth_blueprint"
    types = {asset.type for asset in catalog.assets}
    assert "character" in types
    assert "icon" in types
    assert "charts" in types
    ref = next(asset for asset in catalog.assets if asset.id == "alex_explaining_right_v01")
    assert ref.character == "alex"
    assert ref.production_role == "reference_sheet"
    assert ref.is_production_selectable() is False
    head = next(asset for asset in catalog.assets if asset.id == "alex_front_cutout_v02")
    assert head.character == "alex"
    assert head.pose == "front"
    assert head.production_role == "character_cutout"
    alex = next(asset for asset in catalog.assets if asset.id == "alex_explaining_cutout_v03")
    assert alex.character == "alex"
    assert alex.pose == "explaining"
    assert alex.production_role == "character_cutout"
    assert alex.approved is True
    assert alex.is_production_selectable() is True
    geometric = next(asset for asset in catalog.assets if asset.id == "alex_explaining_right_cutout_v01")
    assert geometric.is_production_selectable() is False
    settings = get_settings()
    brain = WealthBlueprintAssetBrain(settings=settings, catalog=catalog)
    assert brain.resolve_file(alex).is_file()
    assert not Path(alex.file_path).is_absolute()


def test_select_best_returns_library_hit_for_alex_explaining(brain: WealthBlueprintAssetBrain):
    result = brain.select_best(
        AssetQuery(
            type="character",
            character="alex",
            pose="explaining",
            tags_any=["finance", "explain"],
            text="Alex explaining a savings habit",
        )
    )
    assert result.status == "found"
    assert result.match is not None
    assert result.match.asset.id == "alex_explaining_cutout_v03"
    assert result.match.asset.production_role == "character_cutout"
    assert result.match.asset.id != "alex_explaining_right_cutout_v01"
    assert result.match.asset.id != "alex_front_cutout_v02"
    assert result.generation_need is None


def test_unapproved_assets_are_skipped_by_default(brain: WealthBlueprintAssetBrain):
    result = brain.select_best(
        AssetQuery(
            type="investing",
            tags_any=["dca"],
            text="dollar cost averaging steps",
            require_approved=True,
            min_semantic_score=0.01,
        )
    )
    # Only unapproved DCA asset exists → missing / generation need
    assert result.status == "missing"
    assert result.generation_need is not None
    assert result.generation_need.suggested_type == "investing"
    assert "dca" in result.generation_need.suggested_tags


def test_tag_and_type_search(brain: WealthBlueprintAssetBrain):
    hits = brain.search(AssetQuery(type="icon", tags_any=["savings"], require_approved=True))
    assert hits
    assert hits[0].asset.id != "icon_piggy_bank_v01"
    assert "illust_" in hits[0].asset.id or hits[0].asset.source == "illustrative_editorial"
    assert hits[0].asset.is_production_selectable() is True


def test_missing_exotic_query_emits_generation_need(brain: WealthBlueprintAssetBrain):
    result = brain.select_best(
        AssetQuery(
            type="cars",
            tags_any=["supercar", "lambo"],
            text="luxury supercar flex shot",
            min_semantic_score=0.9,
        )
    )
    assert result.status == "missing"
    assert result.generation_need is not None
    assert result.generation_need.needed is True
    assert result.generation_need.suggested_type == "cars"


def test_asset_record_rejects_character_without_name():
    with pytest.raises(Exception):
        AssetRecord(
            id="bad_character",
            type="character",
            file_path="x.png",
            channel="wealth_blueprint",
            character=None,
            approved=True,
        )


def test_all_category_literals_are_documented():
    assert len(ASSET_TYPES) == 13
    assert "transitions" in ASSET_TYPES
    assert "decorations" in ASSET_TYPES


def test_default_library_root_is_under_config():
    root = default_library_root()
    assert root.name == "wealth_blueprint"
    assert (root / "catalog.json").is_file()


def test_catalog_roundtrip_unique_ids():
    catalog = load_catalog()
    dumped = AssetCatalog.model_validate(catalog.model_dump(mode="json"))
    assert len(dumped.assets) == len(catalog.assets)
