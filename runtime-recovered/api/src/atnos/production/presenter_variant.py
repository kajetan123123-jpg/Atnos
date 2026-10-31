"""Registered presentation variants reuse canonical audio and owned Alex artwork."""
from pathlib import Path
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash


def bind_editorial_scene_directions(manifest):
    """Pure presentation binding reused by the registered path and audit copies."""
    from atnos.production.editorial_direction import scene_direction,EDITORIAL_PALETTE
    result=manifest.model_copy(deep=True)
    result.brand=result.brand.model_copy(update={**EDITORIAL_PALETTE,'visualVariant':'editorial_motion','captionStyle':'plain_phrase'})
    for scene in result.scenes:
        scene.visual_relation={**(scene.visual_relation or {}),'editorial_direction':scene_direction(scene)}
    return type(manifest).model_validate(result.model_dump(mode='json'))


def verified_presenter():
    from atnos.production.asset_brain.catalog import load_catalog, default_library_root
    asset=next(a for a in load_catalog().assets if a.id=='alex_explaining_cutout_v03')
    root=default_library_root().resolve()
    path=(root/asset.file_path).resolve()
    if (not path.is_relative_to(root) or not path.is_file() or not asset.approved
            or asset.production_role!='character_cutout' or asset.license_status!='owned'):
        raise ProductionBlocked('Existing owned Alex portrait evidence is unavailable')
    return {'asset_id':asset.id,'path':str(path),'sha256':file_hash(path),
            'usage':'framed portrait; original background retained',
            'animation':'caption-timed 2D mouth deformation, not phoneme inference',
            'user_appearance_approved':False}


def apply_variant(executor, manifest):
    refresh=executor.context.artifacts.get('_renderer_refresh') or {}
    from atnos.production.wb_production_set import verify_production_set
    recipe=verify_production_set(executor.context.artifacts.get('project_profile') or {})
    variant=refresh.get('visual_variant')
    if recipe is not None:
        if variant is not None and variant!=recipe['visual_variant']:
            raise ProductionBlocked('Presentation correction cannot silently replace the frozen WB production set')
        variant=recipe['visual_variant']
    if variant is None:return manifest
    if variant not in {'professional','professional_host','editorial_motion'}:
        raise ProductionBlocked('Unsupported registered visual variant')
    result=manifest.model_copy(deep=True)
    result.brand=result.brand.model_copy(update={'visualVariant':variant,'background':'#07111F',
        'panel':'#102239','accent':'#FFC247','accentSoft':'#00D5D8','text':'#F7FAFF','captionStyle':'plain_phrase'})
    if variant=='editorial_motion':
        result=bind_editorial_scene_directions(result)
    if variant=='professional_host':
        from atnos.production.project_manifest import SceneAsset, AssetProvenance, QAResult
        presenter=refresh.get('presenter')
        if presenter!=verified_presenter():
            raise ProductionBlocked('Registered Alex portrait has changed')
        asset=SceneAsset(asset_id='wb_registered_presenter',role='selected_image',path=presenter['path'],exists=True,
            provenance=AssetProvenance(provider='existing_owned_library',model='WB_ALEX_01',
                input_hash=content_hash(presenter),artifact_hash=presenter['sha256'],
                generation_parameters={'production_role':'presenter_portrait','fit':'contain',
                    'animation':presenter['animation'],'user_appearance_approved':False}),
            qa=QAResult(status='passed',action='PASS',reasons=['Existing owned library portrait hash verified; framed use preserves original image. Appearance remains candidate.']))
        for scene in result.scenes:scene.assets.append(asset.model_copy(deep=True))
    return type(manifest).model_validate(result.model_dump(mode='json'))
