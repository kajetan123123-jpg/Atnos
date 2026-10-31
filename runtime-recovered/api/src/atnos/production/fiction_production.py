"""Bounded, resumable story script preparation using the shared order store."""
import asyncio
from dataclasses import replace
import json

from atnos.production import fiction_script
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json


def with_evidence(result, context):
    paths = [*(context.artifact_dir / 'model_calls').glob('*.json'),
             *(context.artifact_dir / 'script').glob('cast-correction-*.json')]
    return replace(result, evidence_paths=tuple(sorted(paths)))


async def produce_script(executor):
    context, settings = executor.context, executor.store.settings
    config = {
        **context.artifacts['project_profile']['production'],
        # Keep the frozen channel contract in every author/reviewer prompt so
        # story scenes inherit the selected world, cast and voice rules.
        'channel_contract': context.artifacts['project_profile'].get('channel_contract', {}),
    }
    inputs = {'request': context.artifacts['production_order'], 'profile': config,
              'contract_version': fiction_script.CONTRACT_VERSION}
    if context.artifacts['project_profile'].get('feedback_rules'):
        inputs['feedback_rules'] = context.artifacts['project_profile']['feedback_rules']

    async def draft():
        script = await fiction_script.draft(settings, context)
        return with_evidence(write_json(context.artifact_dir / 'script' / 'draft.json', script.model_dump(mode='json')), context)

    raw = await executor.run('script_draft', inputs, draft, local_model_call=True)
    script = fiction_script.StoryScript.model_validate(raw)
    for revision in range(config['max_attempts_per_element']):
        async def review():
            verdict = await fiction_script.review(settings, context, script)
            verdict.update(script_hash=content_hash(script.model_dump(mode='json')), revision=revision)
            return with_evidence(write_json(context.artifact_dir / 'script' / f'review_{revision}.json', verdict), context)

        review_inputs = {**inputs, 'script': script.model_dump(mode='json')}
        stage = f'script_review/{revision}'
        verdict = await executor.run(stage, review_inputs, review, local_model_call=True)
        if verdict['status'] == 'passed':
            break
        if revision + 1 == config['max_attempts_per_element']:
            await asyncio.to_thread(executor.store.fail, context.job_id, executor.token, stage,
                                    category='script_quality_limit', message=json.dumps(verdict['issues']))
            raise ProductionBlocked('Story failed its bounded independent review')

        async def repair():
            fixed = await fiction_script.repair(settings, context, script, verdict['issues'])
            return with_evidence(write_json(context.artifact_dir / 'script' / f'revision_{revision+1}.json', fixed.model_dump(mode='json')), context)

        raw = await executor.run(f'script_repair/{revision+1}', {**review_inputs, 'issues': verdict['issues']},
                                 repair, local_model_call=fiction_script.repair_requires_model(script, verdict['issues']))
        script = fiction_script.StoryScript.model_validate(raw)

    async def finalize():
        payload = {**script.model_dump(mode='json'), 'text': ' '.join(s.text for s in script.segments),
                   'word_count': sum(len(s.text.split()) for s in script.segments), 'qa': verdict,
                   'provenance': {'basis': 'user_requested_original_fiction', 'language': 'en',
                                  'content_kind': config['content_kind'], 'audience': config['audience'],
                                  'generator': config['script_model']['generator'],
                                  'reviewer': config['script_model']['reviewer']},
                   'production_scope': 'script_preview', 'media_generated': False}
        return with_evidence(write_json(context.artifact_dir / 'script.json', payload), context)

    result = await executor.run('script', {'script': script.model_dump(mode='json'), 'review': verdict}, finalize)
    await asyncio.to_thread(executor.store.reconcile_script_checkpoints, context.job_id, executor.token)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, 'script', result)
    context.artifacts['script'] = result
    return result
