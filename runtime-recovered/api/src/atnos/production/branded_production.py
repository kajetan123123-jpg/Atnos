"""Atnos-owned brand trial orchestration; Codex only relays explicit external image jobs."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, create_model

from atnos.production.brand_contract import verified_asset,active_request
from atnos.production.models import StepResult
from atnos.production.order_store import ProductionBlocked, file_hash, checkpoint_fingerprints
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_script import ask_model
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService


class BrandReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passed: bool
    issues: list[str] = Field(max_length=12)


def result(path, payload, files=()):
    record = write_json(path, payload)
    return StepResult(record.artifact_name, path, payload, record.message, tuple(files))


def script_schema(kit):
    fields = {b['id']:(str,Field(max_length=400)) for b in kit['beats'] if b['spoken']}
    fields.update({f'pose_{key}':(str,Field(min_length=40,max_length=800)) for key in kit['poses']})
    return create_model('AlexBrandScriptAndArtDirection', __config__=ConfigDict(extra='forbid'), **fields)


def validate_narration(raw, kit):
    import re
    for beat in kit['beats']:
        if not beat['spoken']:
            continue
        text = raw[beat['id']].strip()
        if not 1 <= len(text.split()) <= beat['max_words']:
            raise ValueError(f"{beat['id']}: maximum {beat['max_words']} words; rewrite briefly, never truncate")
        if not re.search(r'[.!?][\"\u201d]?$' ,text):
            raise ValueError(f"{beat['id']}: complete natural sentences with final punctuation required")
        if re.search(r'\b(subscribe|follow|comment|click|guaranteed|guarantee|risk.free)\b',text,re.I):
            raise ValueError('This brief has no CTA and no guaranteed financial outcomes')
    return raw


async def prepare(executor):
    ctx = executor.context
    settings = executor.store.settings
    frozen = ctx.artifacts['project_profile']['brand_kit']
    kit = frozen['contract']
    for source in frozen['sources'].values():
        if file_hash(Path(source['path'])) != source['sha256']:
            raise ProductionBlocked('User brand reference changed after order creation')
    schema = script_schema(kit)
    async def generate():
        prompt = ('You are Atnos, writing a first English Wealth Blueprint film from the supplied brand brief. '
            'Return only the schema JSON: one concise natural English narration for each spoken beat, plus four art directions. '
            'This is faithful adaptation of the provided storyboard, not an opportunity to invent a different finance lesson. '
            'No CTA. Do not speak headings or bracketed directions. Stay UNDER each beat max_words. '
            'Write only WORDS SPOKEN TO THE VIEWER in s01,s02,s03,s05,s06,s07,s08. NEVER include stage directions. '
            'Do not write Ask whether, Alex looks, Say only, Explain, The viewer, or any instructions to a writer. '
            'For s01 a suitable direct question is Think a bigger paycheck will make you richer? '
            'For s02 only Not necessarily. For s08 only It starts here. '
            's03 mentions spending increasing with income. s05 says earnings alone are not the whole problem. '
            's06 MUST include both how much of the raise you keep AND what that money does next. '
            'Use simple fluent complete sentences. Each pose_ field is a concise image-generation direction for the exact Alex identity, '
            'full body isolated on genuine transparent background, matching the attached visual reference. No text or diagrams in these character assets. '
            'The deterministic renderer animates the seven abstract tokens and typesets all English labels separately. '
            'BRAND CONTRACT: '+json.dumps(kit,ensure_ascii=False))
        raw = schema.model_validate(await ask_model(settings,ctx,prompt,schema)).model_dump()
        validate_narration(raw,kit)
        return write_json(ctx.artifact_dir/'brand_script_draft.json',raw)
    draft = await executor.run('script_draft', {'contract':frozen,'prompt_version':2},generate,local_model_call=True)
    async def review():
        prompt = ('Review this English adaptation against the supplied user storyboard. Reject grammar fragments, different meaning, '
            'CTA, guaranteed wealth, changed currency, or unnecessary claims. The illustrative budget is seven tokens: '
            'two housing, one car, one phone, one dining, one small extras, one retained. It is not investment advice. '
            'No narration for silent beats. Judge only the supplied words, not whether art has been generated. '
            'Return passed true only with empty issues. CONTRACT: '+json.dumps(kit)+' DRAFT: '+json.dumps(draft))
        verdict = BrandReview.model_validate(await ask_model(settings,ctx,prompt,BrandReview,reviewing=True))
        if not verdict.passed or verdict.issues:
            raise ValueError('Brand script rejected: '+json.dumps(verdict.issues))
        return write_json(ctx.artifact_dir/'brand_script_review.json',verdict.model_dump())
    verdict = await executor.run('script_review/0',{'draft':draft,'contract_hash':frozen['contract_hash'],'review_version':1},review,local_model_call=True)
    async def finalize():
        segments = [{**b,'text':draft[b['id']] if b['spoken'] else ''} for b in kit['beats']]
        return write_json(ctx.artifact_dir/'script.json',{'segments':segments,'text':' '.join(s['text'] for s in segments if s['text']),
            'language':'en','qa':{'status':'passed','scope':'brand storyboard script','review':verdict},
            'provenance':{'author':'Atnos / '+ctx.artifacts['project_profile']['production']['script_model']['generator'],
                          'brief_sha256':frozen['sources']['brief_file']['sha256']}})
    script = await executor.run('script',{'draft':draft,'review':verdict,'brand':frozen['contract_hash']},finalize)
    ctx.artifacts['script'] = script
    await asyncio.to_thread(executor.store.persist_legacy_artifact,ctx.job_id,executor.token,'script',script)
    requests=[]
    for pose in kit['poses']:
        async def make_request(pose=pose):
            payload={'asset_id':'alex_'+pose,'provider':kit['image_provider'],
                'reference_file':frozen['sources']['reference_file']['path'],
                'reference_sha256':frozen['sources']['reference_file']['sha256'],
                'prompt':('Use case: illustration-story. Asset type: full-body transparent character layer for a 9:16 animated film. '
                    'The attached image is a visual and identity reference, not an edit target. Draw ONLY one Alex, isolated on a genuine '
                    'transparent alpha background, portrait 2:3 framing. Entire body and both shoes visible with small margins, no cropped hands. '
                    'No words, symbols, extra objects, grid, background, floor or cast shadow. Preserve Alex exactly from the supplied reference. '
                    +kit['character']+' STYLE: '+kit['style']+' REQUIRED POSE: '+kit['poses'][pose]+
                    ' ATNOS ART DIRECTION: '+draft['pose_'+pose]),
                'billing':'Codex tool relay; external usage is not an Atnos API dollar charge. Amount unavailable.',
                'transport':'supervised; Atnos writes the request and validates the returned asset; Codex relays the tool call'}
            return write_json(ctx.artifact_dir/'image_requests'/(pose+'.json'),payload)
        req=await executor.run('image_request/'+pose,{'draft':draft['pose_'+pose],'contract':frozen,'request_version':1},
                               make_request,external_tool_call=True)
        requests.append(active_request(ctx.artifact_dir,pose,req))
    return script,requests


async def produce_branded(executor):
    ctx=executor.context
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.get(executor.store.settings.ollama_base_url.rstrip('/')+'/api/tags')
        response.raise_for_status()
    installed={m['name'] for m in response.json()['models']}
    models=ctx.artifacts['project_profile']['production']['script_model']
    if not {models['generator'],models['reviewer']}.issubset(installed):
        raise ProductionBlocked('Brand author or reviewer is not installed')
    lock=GpuRuntimeLockService(executor.store.settings)
    lease=await asyncio.to_thread(lock.acquire,owner=f'brand:{ctx.job_id}',timeout_s=600)
    try:
        script,requests=await prepare(executor)
        from atnos.production.branded_finish import prepare_voice
        await prepare_voice(executor,script)
    finally:
        await asyncio.to_thread(lock.release,lease)
    trial_path=ctx.artifact_dir/'single-pose-authorization.json'
    if trial_path.is_file():
        from atnos.production.brand_single_pose import produce_single_pose
        return await produce_single_pose(executor,script,requests)
    missing=[r['asset_id'] for r in requests if verified_asset(ctx.artifact_dir,r) is None]
    if missing:
        # Waiting is not a generation attempt. Repeated status/resume never creates fresh requests.
        await asyncio.to_thread(executor.store.fail,ctx.job_id,executor.token,'external_images',
            category='awaiting_external_images',message='Atnos zlecił grafiki; oczekuje na nadzorowany zwrot: '+', '.join(missing))
        return executor.store.get(ctx.job_id)
    from atnos.production.branded_finish import finish_branded
    return await finish_branded(executor,script,requests)
