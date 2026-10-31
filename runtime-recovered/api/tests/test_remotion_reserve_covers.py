"""Offline fixtures: cover meaning comes from actual Remotion, not legacy flows."""
import asyncio
from pathlib import Path
import pytest
from atnos.production import cover_visuals
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash
from atnos.production.project_manifest import save_project_manifest
from test_production_order_store import store, create
from test_production_order_script import script
from test_semantic_qa_remotion_scene_gate import _minimal_manifest


def prepare(ex, script):
    root=ex.context.artifact_dir
    source=root/'synthetic.mp4'; source.write_bytes(b'OFFLINE synthetic video fixture')
    manifest=_minimal_manifest(ex.job_id, [('s01',0,7)])
    manifest.scenes[0].narration=script.segments[0].text
    manifest.scenes[0].semantic_overlay_kind='reserve_readiness'
    path=root/'project_manifest.json'; save_project_manifest(path,manifest)
    ex.context.artifacts.update(script=script.model_dump(),
        edit={'renderer':'remotion','output_file':str(source),'manifest_path':str(path),'sha256':file_hash(source)},
        visuals={'scenes':[{'segment_id':'legacy','kind':'flow','labels':['RESERVE','GOAL']}],'assets':{}})
    return source


def test_cover_uses_manifest_frame_instead_of_false_legacy_transfer(store, script):
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); source=prepare(ex,script)
        choices=cover_visuals.choices(ex.context)
        assert len(choices)==1 and choices[0]['segment_id']==script.segments[0].id
        assert choices[0]['renderer']=='remotion_reserve_frame'
        assert choices[0]['source_sha256']==file_hash(source)
        assert choices[0]['crop']==[72,520,936,860]
        assert 3 < choices[0]['scene']['source_start'] < 7


def test_changed_actual_render_is_not_accepted_as_a_cover_source(store, script):
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); source=prepare(ex,script)
        source.write_bytes(b'changed video')
        with pytest.raises(ValueError,match='differs from its recorded render'):
            cover_visuals.choices(ex.context)


def test_continuous_shot_matches_all_adjacent_canonical_narration(store, script):
    from atnos.production.project_manifest import load_project_manifest
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); prepare(ex,script)
        path=ex.context.artifact_dir/'project_manifest.json'
        manifest=load_project_manifest(path,verify_hash=True)
        manifest.scenes[0].narration=' '.join(s.text for s in script.segments[:2])
        save_project_manifest(path,manifest)
        choices=cover_visuals.choices(ex.context)
        assert choices[0]['segment_ids']==[s.id for s in script.segments[:2]]
        manifest.scenes[0].narration=' '.join(s.text for s in script.segments[::2])
        save_project_manifest(path,manifest)
        with pytest.raises(ValueError,match='narration does not match canonical'):
            cover_visuals.choices(ex.context)

def test_educational_cover_tracks_actual_illustration_position(store, script):
    from atnos.production.project_manifest import load_project_manifest
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); prepare(ex,script)
        path=ex.context.artifact_dir/'project_manifest.json'
        manifest=load_project_manifest(path,verify_hash=True)
        manifest.brand.presentation='educational_illustration'
        save_project_manifest(path,manifest)
        assert cover_visuals.choices(ex.context)[0]['crop']==[72,460,936,860]


def test_changed_manifest_cannot_keep_selected_frame_provenance(store, script, tmp_path):
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); prepare(ex,script)
        selected=cover_visuals.choices(ex.context)[0]
        Path(selected['manifest_path']).write_text('{}',encoding='utf-8')
        with pytest.raises(ValueError,match='manifest changed'):
            cover_visuals.render(tmp_path/'output',headline='Fixture',detail='Fixture',visual=selected)
