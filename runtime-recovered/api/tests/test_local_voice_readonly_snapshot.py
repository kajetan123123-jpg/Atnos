from pathlib import Path
from types import SimpleNamespace
import pytest
from atnos.production import local_voice_lane as lane


def fixture(monkeypatch,status):
    sample=Path('E:/Atnos/App/tmp/snapshot-fixture.wav').resolve()
    approval=Path('E:/Atnos/App/tmp/snapshot-fixture.json').resolve()
    receipt={'job_id':'fixture','decision':'approved_for_pilot_trial','preset':lane.PRESET,
        'channel_default_changed':False,'frozen_profile_changed':False,'final_film_approved':False,
        'scope':"This order's trial AV only — isolated fixture",'sample':str(sample),'sample_sha256':'sample-sha'}
    provenance={'model_id':lane.MODEL_ID,'revision':lane.MODEL_REVISION,'license':'apache-2.0','verified_files':True}
    monkeypatch.setattr(lane,'_read_json',lambda p:receipt if p==approval else provenance)
    monkeypatch.setattr(lane,'file_hash',lambda _: 'sample-sha')
    order={'status':status,'worker_live':False,'request':{'project':'wealth_blueprint','delivery_mode':'local_only'},'profile':{}}
    monkeypatch.setattr(lane,'ProductionOrderStore',lambda _:SimpleNamespace(get=lambda _:order))
    return approval,sample


def test_ready_record_can_only_be_read_as_an_explicit_snapshot(monkeypatch):
    approval,sample=fixture(monkeypatch,'ready')
    with pytest.raises(lane.LocalVoiceLaneError,match='completed or closed'):
        lane._lane_body(object(),'fixture',approval,sample,require_idle=False)
    result=lane._lane_body(object(),'fixture',approval,sample,require_idle=False,read_only_snapshot=True)
    assert result['job_id']=='fixture'
    with pytest.raises(lane.LocalVoiceLaneError,match='stopped, idle'):
        lane._lane_body(object(),'fixture',approval,sample,require_idle=True,read_only_snapshot=True)


@pytest.mark.parametrize('status',['cancelled','script_ready'])
def test_snapshot_does_not_reopen_closed_or_script_only_orders(monkeypatch,status):
    approval,sample=fixture(monkeypatch,status)
    with pytest.raises(lane.LocalVoiceLaneError,match='completed or closed'):
        lane._lane_body(object(),'fixture',approval,sample,require_idle=False,read_only_snapshot=True)
