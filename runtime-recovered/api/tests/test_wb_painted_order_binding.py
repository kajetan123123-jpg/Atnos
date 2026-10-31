import pytest
from atnos.production.project_profile import ProductionOrder,profile_for_order
from atnos.production.channels.wealth_blueprint.manifest_adapters import apply_native_illustration_system
from atnos.production.project_manifest import SceneType
from test_semantic_qa_remotion_scene_gate import _minimal_manifest

def test_painted_order_is_explicit_local_and_keeps_the_channel_default_unchanged():
    base={'project':'wealth_blueprint','production':{}}
    order=ProductionOrder(topic='Two jobs for one balance',delivery_mode='local_only',native_illustration_system='wb_planning_desk_painted_v1')
    frozen=profile_for_order(base,order)
    assert frozen['native_illustration_system']=='wb_planning_desk_painted_v1'
    assert 'native_illustration_system' not in base
    assert 'native_illustration_system' not in ProductionOrder(topic='Old request').model_dump(mode='json')
    with pytest.raises(ValueError):ProductionOrder(topic='Two jobs',native_illustration_system='wb_planning_desk_painted_v1')

def test_painted_family_supports_real_comparison_and_cta_but_no_unrelated_fallback():
    manifest=_minimal_manifest('painted-test',[('s01',0,2),('s02',2,4)])
    manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':'separate','objects':[],'planning_desk_action':'write-purposes'}
    manifest.scenes[1].scene_type=SceneType.CTA
    result=apply_native_illustration_system(manifest,'wb_planning_desk_painted_v1')
    assert result.brand.illustrationSystem=='wb_planning_desk_painted_v1'
    manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':'access'}
    with pytest.raises(ValueError,match='Unsupported painted planning scene'):apply_native_illustration_system(manifest,'wb_planning_desk_painted_v1')

def test_painted_family_rejects_an_annual_bill_or_missing_meaning_contract():
    manifest=_minimal_manifest('painted-test',[('s01',0,2)])
    with pytest.raises(ValueError):apply_native_illustration_system(manifest,'wb_planning_desk_painted_v1')
    manifest.scenes[0].visual_relation={'kind':'reserve_readiness','stage':'separate','objects':[{'role':'goal','label':'PLANNED BILL FUND'}]}
    with pytest.raises(ValueError):apply_native_illustration_system(manifest,'wb_planning_desk_painted_v1')
