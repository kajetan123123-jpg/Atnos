"""Run the normal image-candidate stage only, retaining the order review hold."""
from __future__ import annotations
import argparse
import asyncio
import json

from atnos.core.config import get_settings
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionOrderStore
from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
from atnos.production.wb_image_api import load_image_lane, produce_image_candidates


async def run(job_id):
    settings = get_settings()
    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    if state['worker_live'] or state['status'] != 'blocked':
        raise RuntimeError('Candidate-only command requires a stopped blocked order')
    if load_image_lane(settings, job_id) is None:
        raise RuntimeError('No explicitly authorized image API lane')
    context = store.context(job_id)
    script = next((row for row in state['checkpoints'] if row['stage'] == 'script'), None)
    if (not script or script['status'] != 'done' or script['output'].get('qa', {}).get('status') != 'passed'
            or not store._valid_files(context.artifact_dir, script['files_json'])):
        raise RuntimeError('Requires an intact current script that passed real Atnos review')
    from atnos.production.scoped_script_revision import load_feedback
    feedback = load_feedback(store, job_id)
    if feedback and script['output'].get('qa', {}).get('feedback_hash') != feedback['feedback_hash']:
        raise RuntimeError('Current editorial feedback has not passed; an older accepted script cannot generate media')
    # Canonical script must represent the current scoped feedback, not an old
    # accepted draft while a later editorial request is still rejected.
    with store.lease(job_id) as token:
        executor = StageExecutor(store, job_id, token)
        executor.context.artifacts['script'] = script['output']
        try:
            result = await produce_image_candidates(executor)
        except SceneAssetsNotReady as exc:
            store.block(job_id, token, 'asset_review', category='awaiting_asset_review',
                message=f"One image candidate awaits actual review: {context.artifact_dir / 'image_api_review.json'}")
            result = {'waiting': exc.waiting, 'audio_created': False, 'mp4_created': False}
        else:
            store.block(job_id, token, 'scripting', category='image_preview_complete',
                message='Required illustrations reviewed; resume normal production for audio and film.')
        print(json.dumps(result, ensure_ascii=False, indent=2))


parser = argparse.ArgumentParser()
parser.add_argument('job_id')
args = parser.parse_args()
asyncio.run(run(args.job_id))
