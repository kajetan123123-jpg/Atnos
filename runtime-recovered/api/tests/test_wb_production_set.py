"""Frozen recipe provenance, not an artwork quality approval."""
import copy
import pytest
from atnos.production.project_profile import ProductionOrder,profile_for_order
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.wb_production_set import freeze_production_set,verify_production_set
from test_production_order_store import store


@pytest.fixture
def fonts(tmp_path,monkeypatch):
    from atnos.production import remotion_renderer as renderer
    root=tmp_path/'fonts';root.mkdir()
    path=root/'fixture.ttf';path.write_bytes(b'OFFLINE FONT HASH FIXTURE')
    monkeypatch.setattr(renderer,'_atnos_fonts_dir',lambda:root)
    monkeypatch.setattr(renderer,'_renderer_font_assets',lambda:{'regular':{'filename':path.name,'sha256':file_hash(path)}})
    monkeypatch.setattr('atnos.production.channels.wealth_blueprint.remotion_order_edit.renderer_source_fingerprint',lambda:'a'*64)
    return path


def test_recipe_is_frozen_per_order_without_changing_project_defaults(store,fonts):
    order=ProductionOrder(topic='Planning with a native recipe',delivery_mode='local_only',voice_candidate='Ryan-neutral',
        provider_policy='strict_local',production_set_id='wb-editorial-v1')
    from atnos.production.project_profile import load_project_profile
    original=load_project_profile(store.settings,'wealth_blueprint');before=copy.deepcopy(original)
    profile=profile_for_order(original,order)
    restored=ProductionOrder.model_validate(order.model_dump(mode='json'))
    assert restored.native_illustration_system==profile['native_illustration_system']
    assert original==before and profile['production_set']==freeze_production_set('Ryan-neutral')
    assert profile['production_set']['captions']=={'style':'plain_phrase','active_word_highlight':False}
    assert profile['production_set']['voice']['cpu_threads']==4
    assert profile['production_set']['user_approved_as_channel_default'] is False
    assert verify_production_set(profile)==profile['production_set']
    fonts.write_bytes(b'CHANGED FONT')
    with pytest.raises(ProductionBlocked):verify_production_set(profile)


@pytest.mark.parametrize('changes',[{'voice_candidate':None},{'provider_policy':'project_default'},
    {'visual_style_id':'A'},{'project':'children_stories'},{'image_provider':'comfyui-local'}])
def test_conflicting_or_nonlocal_recipe_is_rejected(changes):
    data={'topic':'Explicit WB recipe','delivery_mode':'local_only','voice_candidate':'Ryan-neutral',
        'provider_policy':'strict_local','production_set_id':'wb-editorial-v1',**changes}
    with pytest.raises(ValueError):ProductionOrder(**data)


def test_legacy_order_has_no_implicit_recipe():
    assert 'production_set_id' not in ProductionOrder(topic='Legacy unchanged order').model_dump()
    assert verify_production_set({}) is None


def test_scene_factory_change_requires_an_explicit_recipe_update(fonts,monkeypatch):
    from atnos.production import wb_production_set as module
    frozen=freeze_production_set('Ryan-neutral')
    original=module.file_hash
    monkeypatch.setattr(module,'file_hash',lambda path:'f'*64 if path.name=='editorial_direction.py' else original(path))
    with pytest.raises(ProductionBlocked):verify_production_set({'production_set':frozen})


@pytest.mark.parametrize('tamper',[None,'request','recipe','job','voice'])
def test_normal_new_order_reaches_native_manifest_and_actual_qa_contract_without_faking_refresh(store,fonts,tmp_path,tamper):
    from types import SimpleNamespace as NS
    from test_editorial_planning_story import manifest
    from atnos.production.channels.wealth_blueprint.manifest_adapters import apply_native_illustration_system
    from atnos.production.presenter_variant import apply_variant
    from atnos.production.steps.order_delivery import _planning_desk_audit_contract
    from atnos.production.project_profile import load_project_profile
    order=ProductionOrder(topic='Two planning purposes',delivery_mode='local_only',voice_candidate='Ryan-neutral',
        provider_policy='strict_local',production_set_id='wb-editorial-v1')
    profile=profile_for_order(load_project_profile(store.settings,'wealth_blueprint'),order)
    ctx=NS(artifact_dir=tmp_path/'isolated',artifacts={'project_profile':profile,'production_order':order.model_dump(mode='json')})
    result=apply_native_illustration_system(manifest(),order.native_illustration_system)
    result=apply_variant(NS(context=ctx),result)
    assert '_renderer_refresh' not in ctx.artifacts
    if tamper=='request':ctx.artifacts['production_order'].pop('production_set_id')
    if tamper=='recipe':profile['production_set']['digest']='f'*64
    if tamper=='job':ctx.artifact_dir=tmp_path/'another-order'
    if tamper=='voice':ctx.artifacts['production_order']['voice_candidate']='Ryan-B'
    if tamper:
        with pytest.raises((ValueError,ProductionBlocked)):_planning_desk_audit_contract(ctx,result,result.scenes[0])
    else:
        for scene in result.scenes:
            contract=_planning_desk_audit_contract(ctx,result,scene)
            assert contract['presentation_variant']=='editorial_motion'
            assert 'status' not in contract and 'PASS' not in contract
