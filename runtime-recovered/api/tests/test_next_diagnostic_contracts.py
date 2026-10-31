"""Offline regressions for the rejected WB repair; never dispatch production."""
from types import SimpleNamespace
import pytest
from atnos.production.steps import order_script as scripts
from test_production_order_script import script


def context():
    return SimpleNamespace(artifacts={
        'studio_script_recovery': {'mode':'hypothetical_review', 'issues':[
            {'code':'structure','segment_ids':['s01','s02'],'detail':'Keep neutral alternatives.'}]},
        'production_order':{},
        'project_profile':{'production':{'language':'en','word_budget':[90,120],
            'minimum_practical_share':0.2,'narrative_rules':{}}}})


def test_bundle_uses_current_edit_scope_not_historical_targets(script):
    bundle=scripts._active_studio_script_bundle(context(),script,
        [{'code':'structure','segment_ids':['s01']}])
    assert bundle['allowed_segment_ids']==['s01']
    assert 's02' in bundle['locked_segment_ids']
    assert bundle['registered_acceptance_targets'][0]['segment_ids']==['s01','s02']


def test_full_replacement_grammar_restricts_segment_ids():
    schema=scripts.repair_generation_schema(scripts.AdaptedScriptRepair,set(),{'s01','s03'})
    assert schema['$defs']['ScriptSegment']['properties']['id']['enum']==['s01','s03']
    assert 'title' not in schema['properties']


def check(script, **overrides):
    row={'criterion_index':0,'satisfied':True,'segment_id':script.segments[0].id,
         'field':'text','quote':script.segments[0].text,'reason':'Current sentence satisfies the criterion.'}
    row.update(overrides)
    return row


def requirements(script):
    return [{'code':'structure','segment_ids':[script.segments[0].id], 'detail':'Keep the hook.'}]


def test_explicit_review_requires_all_criteria_before_pass(script):
    verdict=scripts.ScriptReview.model_validate({'passed':True,'issues':[]})
    with pytest.raises(ValueError,match='criterion'):
        scripts.validate_editorial_review(verdict,script,requirements(script))


def test_review_cannot_skip_second_segment_of_a_criterion(script):
    target=requirements(script)
    target[0]['segment_ids'].append(script.segments[1].id)
    verdict=scripts.ScriptReview.model_validate({'passed':True,'issues':[],'editorial_checks':[check(script)]})
    with pytest.raises(ValueError,match='every registered criterion'):
        scripts.validate_editorial_review(verdict,script,target)


@pytest.mark.parametrize('fault',['quote','segment','duplicate','unreported_failure'])
def test_review_rejects_unfounded_or_incomplete_criterion_evidence(script,fault):
    row=check(script)
    if fault=='quote': row['quote']='I am giving up a guaranteed return.'
    if fault=='segment': row['segment_id']=script.segments[1].id
    if fault=='unreported_failure': row['satisfied']=False
    verdict=scripts.ScriptReview.model_validate({'passed':True,'issues':[],
        'editorial_checks':[row,row] if fault=='duplicate' else [row]})
    with pytest.raises(ValueError):
        scripts.validate_editorial_review(verdict,script,requirements(script))


def test_grounded_complete_review_remains_acceptable(script):
    verdict=scripts.ScriptReview.model_validate({'passed':True,'issues':[], 'editorial_checks':[check(script)]})
    scripts.validate_editorial_review(verdict,script,requirements(script))


def test_patch_preserves_unflagged_segments_and_rejects_scope_expansion(script):
    replacement=script.segments[0].model_dump()
    replacement['text']='This small purchase has a second price: the alternative you give up.'
    result=scripts.apply_segment_repair(script,[replacement],{replacement['id']})
    assert result.segments[1:]==script.segments[1:]
    assert result.segments[0].text==replacement['text']
    with pytest.raises(ValueError,match='only explicitly flagged'):
        scripts.apply_segment_repair(script,[script.segments[1].model_dump()],{replacement['id']})


def test_failed_review_cannot_quote_an_absent_first_person_sentence(script):
    sid=script.segments[0].id
    verdict=scripts.ScriptReview.model_validate({'passed':False,
        'issues':[{'code':'structure','segment_ids':[sid],'detail':'First person narration.'}],
        'editorial_checks':[check(script,satisfied=False)],
        'issue_evidence':[{'issue_index':0,'segment_id':sid,'field':'text','quote':'I am giving up'}]})
    with pytest.raises(ValueError,match='Current issue quote'):
        scripts.validate_editorial_review(verdict,script,requirements(script))


def test_reference_content_changes_cache_key(tmp_path):
    from atnos.production.comfy_channels.cache import fingerprint_request
    reference=tmp_path/'reference.png'
    reference.write_bytes(b'first')
    first=fingerprint_request({'references':[str(reference)]})
    reference.write_bytes(b'second')
    assert first!=fingerprint_request({'references':[str(reference)]})


def test_reference_isolation_rejects_other_channel(tmp_path):
    from atnos.production.comfy_channels.isolation import assert_reference_belongs,ChannelIsolationError
    owned=tmp_path/'wealth_blueprint'
    (owned/'references').mkdir(parents=True)
    profile=SimpleNamespace(reference_files=[],comfy_project_dir=str(owned),channel_id='wealth_blueprint')
    assert_reference_belongs(profile,owned/'references'/'composition.png')
    with pytest.raises(ChannelIsolationError):
        assert_reference_belongs(profile,tmp_path/'wonder_crew'/'reference.png')


def test_missing_scene_reference_stops_before_acquisition(tmp_path,monkeypatch):
    from atnos.production import wb_image_api as api
    from atnos.production.channels.wealth_blueprint import scene_production_assets as assets
    monkeypatch.setattr(assets,'_try_acquisition',lambda *a,**kw:pytest.fail('Missing reference dispatched generation'))
    with pytest.raises(RuntimeError,match='reference is missing'):
        api._generate_local_candidate(None,None,{'composition_reference_path':str(tmp_path/'missing.png')},
            {},[],{},'test')


def test_local_adapter_passes_only_explicit_scene_reference(tmp_path,monkeypatch):
    from atnos.production import wb_image_api as api
    from atnos.production.channels.wealth_blueprint import scene_production_assets as assets
    reference=tmp_path/'composition.png'
    reference.write_bytes(b'test fixture, not production')
    captured=[]
    def acquire(*a,**kw):
        captured.append(kw)
        return None,{'reason':'stop before generation'}
    monkeypatch.setattr(assets,'_try_acquisition',acquire)
    with pytest.raises(RuntimeError):
        api._generate_local_candidate(None,None,{'composition_reference_path':str(reference)},
            {},[],{'reference_path':'brand-board.png','digest':'test'},'fingerprint')
    assert captured[0]['reference_files']==[str(reference.resolve())]


def test_acquisition_forwards_reference_to_existing_order_contract(monkeypatch,tmp_path):
    from atnos.production.channels.wealth_blueprint import scene_production_assets as assets
    from atnos.production import order_comfy_routing as routing
    captured=[]
    class Fulfiller:
        def __init__(self,*a): pass
        def fulfill(self,request):
            captured.append(request)
            raise RuntimeError('stop before generation')
    monkeypatch.setattr(routing,'OrderComfyAssetFulfiller',Fulfiller)
    reference=str(tmp_path/'composition.png')
    assets._try_acquisition(None,{'type':'background'},allow_generate=True,reference_files=[reference])
    assert captured[0].references==[reference]


@pytest.mark.parametrize('seed',[None,0,531990040])
def test_scene_acquisition_preserves_explicit_seed(monkeypatch,seed):
    from atnos.production.channels.wealth_blueprint import scene_production_assets as assets
    from atnos.production import order_comfy_routing as routing
    captured=[]
    class Fulfiller:
        def __init__(self,*a): pass
        def fulfill(self,request):
            captured.append(request)
            raise RuntimeError('stop before generation')
    monkeypatch.setattr(routing,'OrderComfyAssetFulfiller',Fulfiller)
    assets._try_acquisition(None,{'type':'background','seed':seed},allow_generate=True)
    assert captured[0].seed==seed


def test_registered_review_grammar_requires_complete_audit_receipt():
    requirements=[{'segment_ids':['s01','s02']},{'segment_ids':['s03']}]
    schema=scripts.editorial_review_generation_schema(requirements)
    assert {'passed','issues','editorial_checks','issue_evidence'} <= set(schema['required'])
    checks=schema['properties']['editorial_checks']
    assert checks['minItems']==checks['maxItems']==3
    entries=checks['items']['anyOf']
    assert entries[0]['properties']['criterion_index']['enum']==[0]
    assert entries[0]['properties']['segment_id']['enum']==['s01','s02']
    assert entries[1]['properties']['criterion_index']['enum']==[1]
    assert entries[1]['properties']['segment_id']['enum']==['s03']
    assert scripts.editorial_review_generation_schema([])==scripts.generation_schema(scripts.ScriptReview)


@pytest.mark.parametrize('reference_mode',['core_img2img','unsupported'])
def test_channel_adapter_feeds_reference_pixels_into_real_graph(tmp_path,monkeypatch,reference_mode):
    from atnos.core.config import Settings
    from atnos.production.comfy_channels.models import GenerationRequest
    from atnos.production.comfy_channels.service import ChannelComfyGenerationService
    from atnos.production.comfy_channels import service as channels, model_profiles
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    reference=tmp_path/'composition.png'
    reference.write_bytes(b'isolated fixture')
    workflow=SimpleNamespace(default_width=768,default_height=1344,require_references=False,
        asset_type='background',title='WB_ENVIRONMENT',workflow_id='fixture',workflow_version='1',backend_preference=[])
    profile=SimpleNamespace(channel_id='wealth_blueprint',reference_files=[str(reference)],
        output_subdir='wealth_blueprint',image_models=['DreamShaperXL.safetensors'])
    monkeypatch.setattr(channels,'load_profile',lambda *a:profile)
    monkeypatch.setattr(channels,'assert_request_isolated',lambda *a:None)
    monkeypatch.setattr(channels,'find_workflow',lambda *a:workflow)
    monkeypatch.setattr(channels,'validate_workflow_for_generation',lambda *a:{'ok':True})
    monkeypatch.setattr(model_profiles,'load_model_profile',lambda *a:{'reference_mode':reference_mode,'reference_denoise':0.4})
    monkeypatch.setattr(model_profiles,'resolve_generation_tier',lambda *a,**kw:{'preferred_checkpoint':'DreamShaperXL.safetensors'})
    settings=Settings(app_env='test',app_root=tmp_path,data_dir=tmp_path/'data')
    adapter=ChannelComfyGenerationService(settings)
    adapter.lock=SimpleNamespace(acquire=lambda *a,**kw:object(),release=lambda *a:None)
    images=object.__new__(ComfyUiImageGenerationService)
    images.settings=SimpleNamespace(comfyui_steps=30,comfyui_cfg_scale=6.5,
        comfyui_sampler_name='dpmpp_2m_sde',comfyui_scheduler='karras')
    captured=[]
    class StopBeforeDispatch(Exception): pass
    def generate(**kw):
        assert kw['reference_image']==reference
        # Real graph builder, network upload/generation intentionally absent.
        captured.append(images._build_checkpoint_img2img_workflow(
            checkpoint_name=kw['preferred_checkpoint'],prompt=kw['prompt'],negative_prompt=kw['negative_prompt'],
            width=kw['width'],height=kw['height'],seed=kw['seed'],steps_override=30,
            cfg_scale_override=6.5,quality_mode=False,image_name='fixture-upload.png',denoise=kw['reference_denoise']))
        raise StopBeforeDispatch()
    monkeypatch.setattr(adapter.images,'generate_background',generate)
    from atnos.production.comfy_channels.service import ChannelComfyGenerationError
    with pytest.raises(StopBeforeDispatch if reference_mode=='core_img2img' else ChannelComfyGenerationError):
        adapter.generate(GenerationRequest(channel='wealth_blueprint',workflow='WB_ENVIRONMENT',
            subject='plain paper card and shopping bag',references=[str(reference)],seed=531990040))
    if reference_mode=='core_img2img':
        assert captured[0]['7']['inputs']['latent_image']==['6',0]
        assert captured[0]['4']['inputs']['image']=='fixture-upload.png'
    else:
        assert captured==[]


def test_real_img2img_graph_uses_uploaded_reference_pixels():
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    service=object.__new__(ComfyUiImageGenerationService)
    service.settings=SimpleNamespace(comfyui_steps=30,comfyui_cfg_scale=6.5,
        comfyui_sampler_name='dpmpp_2m_sde',comfyui_scheduler='karras')
    graph=service._build_checkpoint_img2img_workflow(checkpoint_name='DreamShaperXL.safetensors',
        prompt='Two props only',negative_prompt='text, extra objects',width=768,height=1344,
        seed=531990040,steps_override=30,cfg_scale_override=6.5,quality_mode=False,
        image_name='composition-upload.png',denoise=0.4)
    assert graph['4']['inputs']['image']=='composition-upload.png'
    assert graph['5']['inputs']['image']==['4',0]
    assert graph['6']['inputs']=={'pixels':['5',0],'vae':['1',2]}
    assert graph['7']['inputs']['latent_image']==['6',0]
    assert graph['7']['inputs']['denoise']==0.4
    assert graph['9']['inputs']['images']==['8',0]


@pytest.mark.parametrize('fault',[None,'no_graph','wrong_hash','disconnected','wrong_loaded_image'])
def test_result_requires_real_reference_upload_and_connected_sampler(tmp_path,fault):
    import hashlib,json
    from atnos.production.wb_image_api import _verify_local_composition_conditioning
    reference=tmp_path/'composition.png'
    reference.write_bytes(b'reference fixture')
    source=tmp_path/'image.png'
    source.write_bytes(b'output fixture')
    evidence={'reference_upload':{'source_path':str(reference),'resolved_image_name':'upload.png',
        'source_sha256':hashlib.sha256(reference.read_bytes()).hexdigest()},'workflow':{
            '4':{'class_type':'LoadImage','inputs':{'image':'upload.png'}},
            '5':{'class_type':'ImageScale','inputs':{'image':['4',0]}},
            '6':{'class_type':'VAEEncode','inputs':{'pixels':['5',0]}},
            '7':{'class_type':'KSampler','inputs':{'latent_image':['6',0]}}}}
    if fault=='wrong_hash': evidence['reference_upload']['source_sha256']='bad hash'
    if fault=='disconnected': evidence['workflow']['7']['inputs']['latent_image']=['empty',0]
    if fault=='wrong_loaded_image': evidence['workflow']['4']['inputs']['image']='unrelated-upload.png'
    if fault!='no_graph':
        source.with_suffix('.png.comfyui.json').write_text(json.dumps(evidence),encoding='utf8')
    if fault:
        with pytest.raises(RuntimeError,match='conditioning is unproven'):
            _verify_local_composition_conditioning(source,reference)
    else:
        _verify_local_composition_conditioning(source,reference)


def test_upload_receipt_binds_returned_server_name_and_content(tmp_path,monkeypatch):
    import hashlib
    from PIL import Image
    from atnos.services import comfyui_image_generation_service as comfy
    reference=tmp_path/'composition.png'
    Image.new('RGB',(8,8),'navy').save(reference)
    seen=[]
    class Client:
        def __init__(self,**kw): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def post(self,url,files,data):
            seen.append(files['image'][1].read())
            return SimpleNamespace(raise_for_status=lambda:None,
                json=lambda:{'name':'server-renamed.png','subfolder':'references'})
    monkeypatch.setattr(comfy.httpx,'Client',Client)
    service=object.__new__(comfy.ComfyUiImageGenerationService)
    service.runtime=SimpleNamespace(base_url='http://isolated.invalid')
    actual=service._upload_input_image(reference)
    assert actual=='references/server-renamed.png'
    assert service._reference_upload_evidence['resolved_image_name']==actual
    assert seen==[reference.read_bytes()]
    assert service._reference_upload_evidence['source_sha256']==hashlib.sha256(seen[0]).hexdigest()


def test_windows_launcher_with_real_finite_child_preserves_e_environment(tmp_path,monkeypatch):
    """Actual Windows subprocesses; isolated fake launcher, no model/API restart."""
    import json, os, time
    from atnos.production import model_runtime_startup as startup
    if os.name!='nt': pytest.skip('Windows subprocess contract')
    root=tmp_path/'app'
    root.mkdir()
    env_file=tmp_path/'runtime.env'
    env_file.write_text('OLLAMA_BASE_URL=http://127.0.0.1:11436\n',encoding='utf8')
    marker=tmp_path/'child-result.json'
    child=tmp_path/'finite-child.ps1'
    child.write_text("Start-Sleep -Milliseconds 1200\n"
        "@{env_file=$env:ATNOS_ENV_FILE;temp=$env:TEMP;tmp=$env:TMP;"
        "root=$env:ATNOS_TEST_ROOT} | ConvertTo-Json | Set-Content -LiteralPath $env:ATNOS_TEST_MARKER\n",encoding='utf8')
    launcher=root/'Launch-Atnos.ps1'
    launcher.write_text("param([switch]$ModelsOnly,[switch]$NoErrorDialog)\n"
        "if (-not $ModelsOnly) {exit 7}\n"
        "$ps = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'\n"
        "Start-Process -FilePath $ps -ArgumentList @('-NoProfile','-NonInteractive','-File',"
        "$env:ATNOS_TEST_CHILD) -WindowStyle Hidden | Out-Null\nexit 0\n",encoding='utf8')
    monkeypatch.setenv('ATNOS_ENV_FILE',str(env_file))
    monkeypatch.setenv('ATNOS_TEST_CHILD',str(child))
    monkeypatch.setenv('ATNOS_TEST_MARKER',str(marker))
    monkeypatch.setenv('ATNOS_TEST_ROOT',str(root))
    monkeypatch.setenv('TEMP',str(tmp_path))
    monkeypatch.setenv('TMP',str(tmp_path))
    startup._launch_models_only(SimpleNamespace(app_root=root,ollama_base_url='http://127.0.0.1:11436'))
    deadline=time.monotonic()+10
    while not marker.is_file() and time.monotonic()<deadline: time.sleep(0.1)
    assert marker.is_file(),'Finite test child did not finish'
    result=json.loads(marker.read_text('utf-8-sig'))
    assert result=={'env_file':str(env_file.resolve()),'temp':str(tmp_path),'tmp':str(tmp_path),'root':str(root)}

