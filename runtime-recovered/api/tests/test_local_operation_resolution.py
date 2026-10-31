import json
from copy import deepcopy
import pytest
from atnos.production.order_store import checkpoint_resolved


def evidence_rows():
    stage='assets/image_local/flux2-trial/s01/'+'a'*16
    old={'stage':stage,'status':'failed','attempts':1,'input_hash':'previous',
         'error_json':json.dumps({'category':'local_operation_abandoned','flux_retry_permitted':False,
             'evidence_sha256':'hash','evidence_path':'E:/order/assets/image_local/flux_trial/'+'a'*16+'/reconciliation.json'})}
    path='assets/image_local/flux_trial/'+'a'*16+'/reconciliation.json'
    fallback={'stage':'assets/image_local/s01/new','status':'done',
              'output_json':json.dumps({'asset_id':'qualified-by-separate-gates','image_sha256':'pixels','replaces_local_operation':{
                  'stage':stage,'input_hash':'previous','attempts':1,'reconciliation_sha256':'hash'}}),
              'files_json':json.dumps([{'path':path,'sha256':'hash'}])}
    gate={'stage':'image_generation','status':'done','output_json':json.dumps({'qualified_assets':[
        {'scene_id':'s01','asset_id':'qualified-by-separate-gates','quality_status':'APPROVED','image_sha256':'pixels'}]})}
    return old,[old,fallback,gate]


def test_only_completed_fallback_and_image_stage_resolve_consumed_operation():
    old,rows=evidence_rows()
    assert checkpoint_resolved(old,rows)
    assert not checkpoint_resolved(old,rows[:-1])


@pytest.mark.parametrize('defect',['input','attempts','receipt','file','pending','category','second_post','other_scene','rejected','different_pixels'])
def test_unbound_or_unfinished_fallback_does_not_resolve_operation(defect):
    old,rows=evidence_rows()
    output=json.loads(rows[1]['output_json'])
    error=json.loads(old['error_json'])
    if defect=='input':output['replaces_local_operation']['input_hash']='changed'
    if defect=='attempts':output['replaces_local_operation']['attempts']=2
    if defect=='receipt':output['replaces_local_operation']['reconciliation_sha256']='changed'
    if defect=='file':rows[1]['files_json']='[]'
    if defect=='pending':rows[1]['status']='running'
    if defect=='category':error['category']='execution_outcome_unknown'
    if defect=='second_post':error['flux_retry_permitted']=True
    if defect=='other_scene':rows[1]['stage']='assets/image_local/s02/new'
    if defect in {'rejected','different_pixels'}:
        gate=json.loads(rows[2]['output_json'])
        if defect=='rejected':gate['qualified_assets'][0]['quality_status']='REJECTED'
        else:gate['qualified_assets'][0]['image_sha256']='changed'
        rows[2]['output_json']=json.dumps(gate)
    rows[1]['output_json']=json.dumps(output)
    old['error_json']=json.dumps(error)
    assert not checkpoint_resolved(old,rows)
