import asyncio
from pathlib import Path

from PIL import Image
import pytest

from atnos.production import cover_visuals, cover_repair
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash
from atnos.production.steps import order_delivery
from test_production_order_store import store, create
from test_production_order_script import script


def test_research_covers_use_actual_remotion_frame_instead_of_legacy_diagram(store,script,monkeypatch):
    from types import SimpleNamespace
    job=create(store)['id']
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token); root=ex.context.artifact_dir
        video=root/'actual-render-fixture.mp4';video.write_bytes(b'ISOLATED SYNTHETIC RENDER')
        manifest_path=root/'fixture-manifest.json';manifest_path.write_text('{}')
        segment=script.segments[1]
        scene=SimpleNamespace(semantic_overlay_kind=None,visual_relation={'kind':'research_check'},
            narration=segment.text,caption_words=[],timing=SimpleNamespace(start_sec=4,end_sec=12),
            scene_id='s02',headline=segment.on_screen,visual_prompt=segment.visual_intent)
        manifest=SimpleNamespace(job_id=job,scenes=[scene],brand=SimpleNamespace(model_dump=lambda **_:{}))
        monkeypatch.setattr('atnos.production.project_manifest.load_project_manifest',lambda *a,**k:manifest)
        ex.context.artifacts.update(script=script.model_dump(mode='json'),edit={
            'renderer':'remotion','output_file':str(video),'manifest_path':str(manifest_path),'sha256':file_hash(video)},visuals={'scenes':[]})
        choices=cover_visuals.choices(ex.context)
        assert len(choices)==1 and choices[0]['renderer']=='remotion_research_frame'
        assert choices[0]['source']==str(video) and choices[0]['crop']==[72,120,936,1400]
        video.write_bytes(b'changed')
        with pytest.raises(ValueError,match='differs from its recorded render'):cover_visuals.choices(ex.context)


@pytest.fixture(autouse=True)
def isolated_cover_prerequisites(monkeypatch):
    # Synthetic cover fixtures assume prior claim admission; not a LIVE receipt.
    from atnos.integrations import claim_evidence_gate
    monkeypatch.setattr(claim_evidence_gate,'ensure_media_claim_gate',lambda **kwargs: {'blocked':False})


def setup_visuals(executor, script, monkeypatch):
    root = executor.context.artifact_dir
    scenes, assets = [], {}
    for index, segment in enumerate(script.segments[:2]):
        source = root / f'fixture{index}.mp4'
        source.write_bytes(f'SYNTHETIC TEST SOURCE {index}'.encode())
        assets[source.stem] = {'staged_path': str(source), 'sha256': file_hash(source),
                              'credit': 'Test fixture', 'license': 'Synthetic'}
        scenes.append({'segment_id': segment.id, 'kind': 'footage', 'asset_id': source.stem,
            'source_start': 0, 'labels': [], 'claim_ids': [], 'headline': segment.on_screen,
            'narration': segment.text, 'semantic_goal': segment.visual_intent})
    executor.context.artifacts.update(script=script.model_dump(), visuals={'scenes': scenes, 'assets': assets})
    def fake_frame(args, **kwargs):
        color = '#dd2233' if 'fixture0' in args[args.index('-i') + 1] else '#2255dd'
        Image.new('RGB', (1280, 720), color).save(args[-1])
    monkeypatch.setattr(cover_visuals.subprocess, 'run', fake_frame)
    monkeypatch.setattr(cover_visuals, 'resolve_ffmpeg', lambda: 'synthetic-fixture-ffmpeg')
    return assets


def test_both_covers_include_the_actual_source_visual_and_its_evidence(store, script, monkeypatch):
    job = create(store)['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        setup_visuals(ex, script, monkeypatch)
        result = asyncio.run(order_delivery.produce_covers(ex))
        with Image.open(result['landscape']) as cover:
            assert cover.size == (1280, 720)
            assert cover.getpixel((920, 370)) == (221, 34, 51)
        with Image.open(result['portrait']) as cover:
            assert cover.getpixel((540, 1100)) == (221, 34, 51)
        assert result['visual']['source_sha256'] == file_hash(Path(result['visual']['source']))
        assert asyncio.run(order_delivery.produce_covers(ex)) == result
    state = store.get(job)
    assert state['provider_calls'] == 0
    files = next(row for row in state['checkpoints'] if row['stage'] == 'covers')['files']
    assert any(item['path'].endswith('source_visual.png') for item in files)


@pytest.mark.parametrize('defect', ['changed', 'outside'])
def test_cover_sources_cannot_escape_order_or_change_after_staging(store, script, monkeypatch, tmp_path, defect):
    job = create(store)['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        assets = setup_visuals(ex, script, monkeypatch)
        if defect == 'changed':
            Path(assets['fixture0']['staged_path']).write_bytes(b'changed source')
        else:
            outside = tmp_path / 'outside.mp4'
            outside.write_bytes(b'outside order')
            assets['fixture0'].update(staged_path=str(outside), sha256=file_hash(outside))
        with pytest.raises(ValueError, match='outside this order or its checksum changed'):
            cover_visuals.choices(ex.context)


def test_visual_cover_repair_changes_the_image_only_in_the_rejected_orientation(store, script, monkeypatch):
    job = create(store)['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        setup_visuals(ex, script, monkeypatch)
        original = asyncio.run(order_delivery.produce_covers(ex))
        async def select(*args, **kwargs):
            return {'headline_key': 'title', 'detail_key': 's03', 'visual_segment_id': 's02',
                    'reason': 'Replace the rejected image with the other approved scene visual.'}
        monkeypatch.setattr(cover_repair, 'ask_model', select)
        report = {'status': 'failed', 'sha256': original['sha256']['landscape'],
                  'verdict': {'status': 'fail', 'blockers': ['semantic_off_topic']}}
        repair = asyncio.run(cover_repair.repair_cover(ex, 'landscape', report))
        with Image.open(repair['path']) as cover:
            assert cover.getpixel((920, 370)) == (34, 85, 221)
        assert file_hash(Path(original['portrait'])) == original['sha256']['portrait']
        assert ex.context.artifacts['covers']['portrait'] == original['portrait']
        assert store.get(job)['provider_calls'] == 1


@pytest.mark.parametrize('renderer', ['remotion_reserve_frame', 'remotion_research_frame'])
def test_new_native_base_is_not_replaced_by_old_renderer_repair(renderer, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from atnos.production.project_profile import content_hash
    script_payload={'fixture': 'unchanged canonical'}
    covers={'portrait':'current-portrait.png','landscape':'current-landscape.png',
            'sha256':{'portrait':'new-p','landscape':'new-l'},'visual':{'renderer':renderer}}
    old={'stage':'cover_quality/portrait/1','output':{'script_hash':content_hash(script_payload),
        'visual_renderer_hash':'old-renderer','before_sha256':'old-before','sha256':'old-after','path':'old.png'},
        'files_json':'[]'}
    persisted=[]
    store=SimpleNamespace(_valid_files=lambda *args:True,
        persist_legacy_artifact=lambda *args:persisted.append(args))
    ex=SimpleNamespace(context=SimpleNamespace(artifacts={'covers':covers,'script':script_payload},
        artifact_dir=tmp_path,job_id='fixture'),store=store,token='fixture')
    monkeypatch.setattr(cover_repair,'_rows',lambda _,orientation:[old] if orientation=='portrait' else [])
    result=asyncio.run(cover_repair.apply_cover_repairs(ex))
    assert result==covers and len(persisted)==1
