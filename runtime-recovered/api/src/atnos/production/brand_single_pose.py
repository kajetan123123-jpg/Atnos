"""Explicitly authorized single-pose trial; no fabricated additional character poses."""
import asyncio
import json
from pathlib import Path
from atnos.production.branded_production import result
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.steps.common import write_json
from atnos.services.local_cutout_service import remove_background


async def produce_single_pose(executor,script,requests):
    ctx=executor.context;root=ctx.artifact_dir
    auth_path=root/'single-pose-authorization.json'
    auth=json.loads(auth_path.read_text(encoding='utf-8'))
    if auth.get('authorization')!='Tak, zrób lokalną próbę z jednym Alexem' or auth.get('job_id')!=ctx.job_id:
        raise ProductionBlocked('Single-pose trial requires exact scoped authorization')
    source=(root/auth['source_relative_file']).resolve()
    if not source.is_relative_to(root) or file_hash(source)!=auth['source_sha256']:
        raise ProductionBlocked('Authorized Alex source changed')
    async def cutout():
        target=root/'brand_assets/alex_single_pose.png'
        detail=await asyncio.to_thread(remove_background,source,target)
        return result(root/'brand_assets/alex_single_pose.json',{'status':'produced','detail':detail,
            'provider':'codex_imagegen_then_local_u2net','authorization':auth,'output_file':str(target),
            'limitations':'One thinking pose used throughout. No new expression, pointing gesture or lip-sync.'},[target,source,auth_path])
    asset=await executor.run('local_cutout',{'auth':auth,'cutout_contract':1},cutout,local_model_call=True)
    async def scope():
        return result(root/'trial-scope.json',{'status':'authorized','requested':'four poses with gestures',
            'trial':'one fixed thinking pose; animated diagrams and camera framing',
            'authorization':auth['authorization'],'identity_artifact_sha256':asset['detail']['sha256']},[auth_path])
    await executor.run('trial_scope',{'auth':auth,'asset':asset['detail']['sha256']},scope)
    from atnos.production.branded_finish import finish_branded
    images={key:Path(asset['output_file']) for key in ('thinking','explain','skeptical','pointing')}
    return await finish_branded(executor,script,requests,image_override=images,scope='user_authorized_single_pose_trial')
