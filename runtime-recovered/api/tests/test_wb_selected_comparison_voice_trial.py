import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from atnos.production import local_voice_lane as lane
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash

def put(path,value):
    path.write_text(json.dumps(value),encoding='utf8');return path

@pytest.fixture
def selected(tmp_path,monkeypatch):
    sample=tmp_path/'B.wav';sample.write_bytes(b'isolated fixture, no model inference')
    sha=file_hash(sample);ih=hashlib.sha256(lane.CLEAR_INSTRUCTION.encode()).hexdigest()
    request={'project':'wealth_blueprint','delivery_mode':'local_only','series_key':'trial-fixture'}
    state={'request':request,'profile':{'unchanged':True},'status':'queued','worker_live':False,'checkpoints':[]}
    original=put(tmp_path/'original-request.json',{'source_script_sha256':'a'*64})
    generation={'status':'candidate_unapproved','sample_id':'ryan_clear','speaker':'Ryan','provider':'qwen_local',
        'model_id':lane.MODEL_ID,'revision':lane.MODEL_REVISION,'preset':'clear_conversation_v1',
        'instruction':lane.CLEAR_INSTRUCTION,'instruction_sha256':ih,'output_path':str(sample),'output_sha256':sha,
        'source_job_id':'source-fixture','source_script_sha256':'a'*64,
        'request_path':str(original),'request_sha256':file_hash(original)}
    gp=put(tmp_path/'generation.json',generation)
    selection=put(tmp_path/'selection.json',{'status':'USER_SELECTED_FOR_NEXT_PREVIEW','sample_id':'ryan_clear',
        'sample_sha256':sha,'sample_receipt_sha256':file_hash(gp),'instruction_sha256':ih})
    authority=put(tmp_path/'full-trial.json',{'status':'USER_REQUESTED_WB_FULL_TRIAL','series_key':'trial-fixture',
        'sample_id':'ryan_clear','sample_sha256':sha,'source_job_id':'source-fixture','source_script_sha256':'a'*64,
        'user_answer':'Use this image and Ryan B in a full trial','paid_calls_authorized':False,
        'final_film_approved':False,'channel_default_changed':False})
    receipt={'job_id':'trial-fixture','decision':'selected_comparison_candidate_for_trial','preset':lane.CLEAR_PRESET,
        'sample':str(sample),'sample_sha256':sha,'scope':"This order's trial AV only; selected candidate, no final approval.",
        'request_hash':content_hash(request),'channel_default_changed':False,'frozen_profile_changed':False,
        'final_film_approved':False,'new_sample_approved_by_user':False}
    for key,path in [('production_request',authority),('selection',selection),('sample_receipt',gp)]:
        receipt[key+'_path']=str(path);receipt[key+'_sha256']=file_hash(path)
    rp=put(tmp_path/'lane.json',receipt)
    monkeypatch.setattr(lane,'ProductionOrderStore',lambda _:SimpleNamespace(get=lambda _:state))
    read=lane._read_json
    monkeypatch.setattr(lane,'_read_json',lambda p:{'model_id':lane.MODEL_ID,'revision':lane.MODEL_REVISION,
        'license':'apache-2.0','verified_files':True} if p.name=='model-provenance.json' else read(p))
    return rp,sample,receipt,authority,gp,generation,state

def test_selected_candidate_has_exact_clear_instruction_and_preserves_old_profile(selected):
    rp,sample,_,_,_,_,state=selected
    body=lane._lane_body(None,'trial-fixture',rp,sample,require_idle=True)
    assert body['preset']==lane.CLEAR_PRESET
    assert lane.instruction_for_preset(body['preset'])==lane.CLEAR_INSTRUCTION
    assert state['profile']=={'unchanged':True}

def test_qwen_file_entry_point_loads_without_atnos_on_pythonpath_and_keeps_exact_preset():
    from atnos.services import qwen_tts_local_runtime_service as runtime
    assert runtime.COMPARISON_CLEAR_INSTRUCTION == lane.CLEAR_INSTRUCTION
    environment={**os.environ,'PYTHONPATH':''}
    result=subprocess.run([sys.executable,str(Path(runtime.__file__).resolve()),'--help'],
        env=environment,capture_output=True,text=True,timeout=20)
    assert result.returncode==0, result.stderr

@pytest.mark.parametrize('failure',['sample_changed','stale_authority','foreign_request','fake_approval','wrong_speaker','wrong_model','wrong_instruction','source_changed'])
def test_selected_trial_rejects_false_or_stale_evidence_before_inference(selected,failure):
    rp,sample,receipt,authority,gp,generation,state=selected
    if failure=='sample_changed':sample.write_bytes(b'other audio')
    elif failure=='stale_authority':put(authority,{})
    elif failure=='foreign_request':state['request']['series_key']='foreign'
    elif failure=='fake_approval':receipt['new_sample_approved_by_user']=True
    elif failure=='source_changed':
        data=json.loads(authority.read_text());data['source_script_sha256']='b'*64;put(authority,data)
        receipt['production_request_sha256']=file_hash(authority)
    else:
        generation[{'wrong_speaker':'speaker','wrong_model':'revision','wrong_instruction':'instruction'}[failure]]='other'
        put(gp,generation);receipt['sample_receipt_sha256']=file_hash(gp)
        sp=__import__('pathlib').Path(receipt['selection_path']);sel=json.loads(sp.read_text());sel['sample_receipt_sha256']=file_hash(gp)
        put(sp,sel);receipt['selection_sha256']=file_hash(sp)
    put(rp,receipt)
    with pytest.raises(lane.LocalVoiceLaneError):lane._lane_body(None,'trial-fixture',rp,sample,require_idle=True)
