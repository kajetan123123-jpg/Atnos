"""Use a traceable scene visual in both cover formats; no additional generation calls."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

from atnos.production import editorial_film
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.order_store import file_hash


def _remotion_choices(context):
    """Derive covers from the actual rendered meaning, never the legacy clip plan."""
    from atnos.production.project_manifest import load_project_manifest
    edit = context.artifacts.get('edit') or {}
    if edit.get('renderer') != 'remotion':
        return None
    root = context.artifact_dir.resolve()
    source = Path(str(edit.get('output_file') or '')).resolve()
    manifest_path = Path(str(edit.get('manifest_path') or '')).resolve()
    if any(not path.is_relative_to(root) or not path.is_file() for path in (source, manifest_path)):
        raise ValueError('Remotion cover requires its actual in-order video and manifest')
    if edit.get('sha256') != file_hash(source):
        raise ValueError('Remotion cover video differs from its recorded render')
    manifest = load_project_manifest(manifest_path, verify_hash=True)
    if manifest.job_id != context.job_id:
        raise ValueError('Remotion cover manifest belongs to another order')
    segments = context.artifacts['script']['segments']
    result = []
    for scene in manifest.scenes:
        research=(scene.visual_relation or {}).get('kind')=='research_check'
        if scene.semantic_overlay_kind != 'reserve_readiness' and not research:
            continue
        # One continuous shot may contain several adjacent canonical thoughts.
        # Match the entire ordered narration, never a substring or a stale id.
        matches = [segments[start:end] for start in range(len(segments))
                   for end in range(start + 1, len(segments) + 1)
                   if ' '.join(s['text'] for s in segments[start:end]) == scene.narration]
        if len(matches) != 1:
            raise ValueError('Remotion cover narration does not match canonical')
        matched = matches[0]
        segment = matched[0]
        caption_ids = list(dict.fromkeys(word.segment_id for word in scene.caption_words if word.segment_id))
        if caption_ids and caption_ids != [s['id'] for s in matched]:
            raise ValueError('Remotion cover caption segments do not match canonical')
        duration = scene.timing.end_sec - scene.timing.start_sec
        moment = scene.timing.start_sec + max(0, duration - min(1.2, duration / 3))
        result.append({'segment_id':segment['id'], 'segment_ids':[s['id'] for s in matched],
          'renderer':'remotion_research_frame' if research else 'remotion_reserve_frame',
          'source':str(source),'source_sha256':file_hash(source),
          'manifest_path':str(manifest_path),'manifest_sha256':file_hash(manifest_path),
          'scene_id':scene.scene_id,'crop':([72,120,936,1400] if research else [72,460 if manifest.brand.presentation == 'educational_illustration' else 520,936,860]), 'brand':manifest.brand.model_dump(mode='json'),
          'scene':{'kind':'footage','segment_id':segment['id'],'source_start':moment,
                   'headline':scene.headline,'narration':scene.narration,'semantic_goal':scene.visual_prompt},
          'claims':context.artifacts['script']['claims'], 'credit':'Wealth Blueprint native Remotion',
          'license':'project_generated'})
    # Other scene families keep their existing cover capability until qualified.
    return result or None


def choices(context):
    remotion = _remotion_choices(context)
    if remotion is not None:
        return remotion
    visuals = context.artifacts.get('visuals', {})
    assets = visuals.get('assets', {})
    result = []
    root = context.artifact_dir.resolve()
    for scene in visuals.get('scenes', []):
        item = {'segment_id': scene['segment_id'], 'scene': scene,
                'claims': context.artifacts['script']['claims']}
        if scene['kind'] == 'footage':
            entry = assets.get(scene.get('asset_id'))
            if not entry:
                raise ValueError('Cover source has no staged asset evidence')
            source = Path(entry['staged_path']).resolve()
            if not source.is_relative_to(root) or not source.is_file() or file_hash(source) != entry['sha256']:
                raise ValueError('Cover footage is outside this order or its checksum changed')
            item.update(source=str(source), source_sha256=entry['sha256'],
                        credit=entry.get('credit'), license=entry.get('license'), page=entry.get('page'))
        result.append(item)
    return result


def render(directory, *, headline, detail, visual=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = []
    if visual:
        hero = directory / 'source_visual.png'
        scene = visual['scene']
        if scene['kind'] == 'footage':
            source = Path(visual['source'])
            if file_hash(source) != visual['source_sha256']:
                raise ValueError('Cover source changed after selection')
            ffmpeg = resolve_ffmpeg()
            if not ffmpeg:
                raise ValueError('FFmpeg is needed for a cover frame')
            native=visual.get('renderer') in {'remotion_reserve_frame','remotion_research_frame'}
            crop = visual.get('crop') if native else None
            filters = (f'crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},scale=1280:-2'
                       if crop in ([72,520,936,860],[72,460,936,860],[72,120,936,1400]) else 'scale=1280:-2')
            manifest = visual.get('manifest_path')
            if manifest and file_hash(Path(manifest)) != visual['manifest_sha256']:
                raise ValueError('Cover manifest changed after selection')
            subprocess.run([ffmpeg, '-y', '-v', 'error', '-ss', str(scene['source_start']), '-i', str(source),
                            '-frames:v', '1', '-vf', filters, str(hero)],
                           check=True, capture_output=True, timeout=45,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if file_hash(source) != visual['source_sha256']:
                raise ValueError('Cover source changed while extracting its frame')
            if manifest and file_hash(Path(manifest)) != visual['manifest_sha256']:
                raise ValueError('Cover manifest changed while extracting its frame')
        else:
            brief = {'schema_version': 'production-order-1', 'scenes': [scene], 'claims': visual['claims']}
            frame = editorial_film.draw_scene(brief, scene, 1, 0)
            frame.crop((60, 700, 1020, 1500)).save(hero)
        with Image.open(hero) as image:
            image.verify()
        evidence.append(hero)
    options = {'visual':hero} if visual else {}
    if visual and visual.get('renderer') in {'remotion_reserve_frame','remotion_research_frame'}:
        options['brand'] = visual['brand']
    paths = editorial_film.create_order_covers(directory, headline=headline, detail=detail, **options)
    return paths, evidence
