"""Only bound, already-completed grounding answers can recover this review."""
import json

from PIL import Image
import pytest

from atnos.production import wb_image_api as lane
from atnos.production import wb_image_review as review
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
from atnos.production.order_autonomy import enable,policy,recover_due
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_script import generation_schema
from test_wb_image_api_production import trial,isolated_brain,authorize
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_review_grounding import observation,grounded_answer


def record(prompt,schema,response,revision,image=None):
    decoding=generation_schema(schema)
    for name,definition in schema.model_json_schema().get('properties',{}).items():
        if definition.get('type')=='array' and 'maxItems' in definition:
            decoding['properties'][name]['maxItems']=definition['maxItems']
    inputs={'prompt':prompt,'schema':decoding,'model_revision':revision,'image_sha256':file_hash(image) if image else None,
            'options':{'temperature':0,'num_ctx':8192,'num_predict':2200},'think':False}
    return {'inputs':inputs,'input_hash':content_hash(inputs),'status':'response_received',
            'response':{'done':True,'done_reason':'stop','message':{'content':json.dumps(response)}}}


@pytest.fixture
def format_block(trial,isolated_brain,monkeypatch):
    store,job,reference=trial
    authorize(trial)
    authorization=lane.register_local_image_lane(store.settings,job,authorized=True)
    context=store.context(job)
    context.artifacts['script']=next(row['output'] for row in store.get(job)['checkpoints'] if row['stage']=='script')
    plan=lane.image_selection_plan(context)
    need=lane._preflight_image_plan(plan)[0][1]
    style=context.artifacts['project_profile']['visual_style']
    image=context.artifact_dir/'generated-scene.png'
    Image.new('RGB',(768,1344),'#132938').save(image)
    revision={'name':'qwen3-vl:8b-instruct','digest':'isolated-image-review','ollama_version':'0.32.0'}
    identity={'image_sha256':file_hash(image),'reference_sha256':file_hash(reference),
              'need':need,'style':style,'model_revision':revision,'contract':2,
              'observation_contract':review.OBSERVATION_CONTRACT}
    key=content_hash(identity)
    directory=context.artifact_dir/'assets/automatic-review'/key
    directory.mkdir(parents=True)
    observed=observation()
    answer=grounded_answer(observed,'failed')
    # The old mandatory word copy rejected a truthful negative explanation.
    answer['required_objects_and_counts']['evidence']='A plain upright canvas does not establish the required banknote-shaped prop.'
    prompt='Compare the actual bound pixels. '+json.dumps({'candidate_observation':observed,
        'reference_observation':observed,'required_scene':need,'style_direction':style})
    records={
        'observation':record(review.OBSERVATION_PROMPT,review.IllustrationObservation,observed,revision,image),
        'reference':record(review.OBSERVATION_PROMPT,review.IllustrationObservation,observed,revision,reference),
        **{name:record(prompt,review.GroundedWBIllustrationChecks,answer,revision)
           for name in ('comparison','comparison-format-1','comparison-format-2')},
    }
    with store.lease(job) as token:
        for name,data in records.items():
            stage=f'assets/image_review/{key[:16]}/{name}'
            store.begin(job,token,stage,{'identity':identity,'prompt':data['inputs']['prompt'],'validation_contract':2},local_model_call=True)
            write_json(directory/(name+'.json'),data)
            payload=observed if name in {'observation','reference'} else {'_review_format_error':'Evidence must copy exactly one observed string.'}
            output=write_json(directory/(name+'-validated.json'),payload)
            store.complete(job,token,stage,payload,[output.artifact_path])
        store.block(job,token,'image_generation',category='execution_failure',
                    message='WB image review exhausted three distinct format attempts; evidence retained, no approval')
    asset=AssetRecord(id='candidate-for-cached-grounding',type='background',channel='wealth_blueprint',
        production_role='background',source='comfyui-local',quality_status='AWAITING_REVIEW',
        file_path=str(image),tags=style_asset_tags(style,job,plan['scenes'][0]['scene_id']))
    isolated_brain.append(asset)
    enable(store.settings,job,reason='User authorizes bounded recovery using the completed image critic evidence after correction.',max_recoveries=1)
    monkeypatch.setattr(review,'_infer',lambda *args,**kwargs:pytest.fail('Monitor submitted another model request'))
    return store,job,directory,asset,records


def test_completed_contract_two_recovers_with_negative_checks_and_no_inference(format_block,monkeypatch):
    store,job,directory,asset,_=format_block
    before=store.get(job)
    dispatched=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self,jid:dispatched.append(jid) or {'id':jid})
    assert recover_due(store.settings)==[{'id':job}]
    assert recover_due(store.settings)==[]
    after=store.get(job)
    assert dispatched==[job]
    assert after['provider_calls']==before['provider_calls']==5
    assert after['profile_hash']==before['profile_hash']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert [row['attempts'] for row in after['checkpoints']]==[row['attempts'] for row in before['checkpoints']]
    assert asset.approved is False and asset.quality_status=='AWAITING_REVIEW'
    assert not (directory/'review.json').exists()
    assert policy(store.settings,job)['recoveries_used']==1
    evidence=next(event['detail']['evidence'] for event in after['history'] if event['event']=='autonomous_recovery_reserved')
    assert evidence['new_model_requests']==0 and evidence['approval_written'] is False
    assert set(evidence['checks'].values())=={'failed'}
    assert len(evidence['raw_traces'])==5


@pytest.mark.parametrize('tamper',['unfinished','input_hash','observation_file','image','need','style','missing_third','schema'])
def test_changed_or_unbound_review_cannot_trigger_automatic_recovery(format_block,monkeypatch,tamper):
    store,job,directory,asset,records=format_block
    if tamper=='observation_file':
        (directory/'observation-validated.json').write_text('{}',encoding='utf-8')
    elif tamper=='image':
        Image.new('RGB',(768,1344),'#ffffff').save(asset.file_path)
    elif tamper=='missing_third':
        (directory/'comparison-format-2.json').unlink()
    else:
        names=('comparison','comparison-format-1','comparison-format-2') if tamper in {'need','style','schema'} else ('comparison-format-2',)
        for name in names:
            data=json.loads(json.dumps(records[name]))
            if tamper=='unfinished':
                data['status']='started'
            elif tamper=='input_hash':
                data['input_hash']='mismatched'
            elif tamper=='schema':
                data['inputs']['schema']={}
                data['input_hash']=content_hash(data['inputs'])
            else:
                prompt=data['inputs']['prompt'];start=prompt.find('{"candidate_observation":')
                body=json.loads(prompt[start:])
                if tamper=='need':
                    body['required_scene']={'different':'scene'}
                else:
                    body['style_direction']={'different':'style'}
                data['inputs']['prompt']=prompt[:start]+json.dumps(body)
                data['input_hash']=content_hash(data['inputs'])
            write_json(directory/(name+'.json'),data)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *args:pytest.fail('Unbound or changed evidence was treated as resumable'))
    assert recover_due(store.settings)==[]
    assert policy(store.settings,job)['recoveries_used']==0
    assert store.get(job)['provider_calls']==5
