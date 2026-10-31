from types import SimpleNamespace
import pytest
from atnos.production import presenter_variant as variant
from atnos.production.project_manifest import BrandSpec
from atnos.production.visual_semantic_audit import _profile_contract
from atnos.production.order_store import ProductionBlocked
from test_semantic_qa_remotion_scene_gate import _minimal_manifest

def test_absent_variant_preserves_legacy_brand_serialization():
    assert 'visualVariant' not in BrandSpec().model_dump(mode='json')

def test_professional_variant_changes_only_presentation():
    manifest=_minimal_manifest('fixture', [('s01',0,4)])
    before=manifest.model_dump(mode='json')
    ex=SimpleNamespace(context=SimpleNamespace(artifacts={'_renderer_refresh':{'visual_variant':'professional'}}))
    result=variant.apply_variant(ex,manifest)
    assert result.brand.visualVariant=='professional'
    assert result.brand.accentSoft=='#00D5D8'
    assert result.model_dump(mode='json')['scenes']==before['scenes']
    assert manifest.model_dump(mode='json')==before

def test_host_rejects_changed_portrait_binding(monkeypatch):
    manifest=_minimal_manifest('fixture', [('s01',0,4)])
    ex=SimpleNamespace(context=SimpleNamespace(artifacts={'_renderer_refresh':{'visual_variant':'professional_host','presenter':{'sha256':'old'}}}))
    monkeypatch.setattr(variant,'verified_presenter',lambda:{'sha256':'current'})
    with pytest.raises(ProductionBlocked,match='has changed'):variant.apply_variant(ex,manifest)

def test_professional_review_uses_requested_style_without_removing_quality_rules():
    assert 'hand-drawn' in _profile_contract('wealth_blueprint')
    assert 'square corners' in _profile_contract('wealth_blueprint','professional')
    assert 'invented data' in _profile_contract('wealth_blueprint','professional_host')
    with pytest.raises(ValueError):_profile_contract('wealth_blueprint','always_pass')
