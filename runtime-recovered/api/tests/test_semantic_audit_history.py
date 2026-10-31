"""Immutable evidence history, not a model verdict or LIVE quality acceptance."""
import asyncio,json
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_delivery import _archive_scene_audit_revision
from test_production_order_store import store,create

def test_changed_audit_contract_preserves_the_actual_negative_report_and_image(store,monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    job=create(store)['id'];root=store.context(job).artifact_dir
    report=root/'qa/s01.json';image=root/'qa/s01.png';image.parent.mkdir(parents=True,exist_ok=True);image.write_bytes(b'old pixel fixture')
    with store.lease(job) as token:
        store.begin(job,token,'semantic/s01',{'contract':'previous'})
        payload={'status':'failed','verdict':{'status':'fail','violations':['Actual recorded negative fixture']}}
        write_json(report,payload);store.complete(job,token,'semantic/s01',payload,[report,image])
        old=next(r for r in store.get(job)['checkpoints'] if r['stage']=='semantic/s01')
        ex=StageExecutor(store,job,token);before=store.get(job)
        history=asyncio.run(_archive_scene_audit_revision(ex,old))
        assert history['previous_output']==payload and history['quality_approval'] is False
        image.write_bytes(b'new pixel fixture');write_json(report,{'status':'not_assessed'})
        for entry in history['files']:
            assert file_hash(root/entry['archive_path'])==entry['sha256']
        after=store.get(job)
        assert next(r for r in after['checkpoints'] if r['stage']=='semantic/s01')==old
        assert after['provider_calls']==before['provider_calls']
