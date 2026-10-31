"""The final QA gate resolves canonical action words, not renderer receipts."""
import pytest
from atnos.production.order_execution import StageExecutor
from atnos.production.project_manifest import load_project_manifest, save_project_manifest
from atnos.production.steps import order_delivery
from atnos.production.channels.wealth_blueprint.planning_desk_contract import planning_desk_action
from test_production_order_store import store, create
from test_production_order_script import script
from test_semantic_qa_remotion_scene_gate import _prepare_remotion_artifacts

CASES = [
    ('identify (actual need and reserve)',
     'Open your savings plan notebook to identify the emergency reserve and name one specific future purchase goal clearly.',
     'separate'),
    ('separate (reserve versus future goal)',
     'Inspect the single balance to locate any portion currently counted for both purposes simultaneously right now.',
     'review'),
]

@pytest.mark.parametrize('directed,text,resolved', CASES)
@pytest.mark.parametrize('correct_stage', [True, False])
@pytest.mark.parametrize('tamper_text', [True, False])
def test_gate_derives_stage_from_canonical_not_renderer_metadata(store, script, directed, text, resolved, correct_stage, tamper_text):
    job = create(store)['id']
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        _, path = _prepare_remotion_artifacts(ex, script, scene_specs=[('s01', 0., 3.)])
        ex.context.artifacts['project_profile']['native_illustration_system'] = 'wb_planning_desk_painted_v1'
        ex.context.artifacts['production_order']['topic'] = 'Emergency reserve'
        ex.context.artifacts['script'] = {'segments': [{'id': 's01', 'text': text, 'visual_intent': directed}]}
        manifest = load_project_manifest(path, verify_hash=True)
        manifest.brand.illustrationSystem = 'wb_planning_desk_painted_v1'
        scene = manifest.scenes[0]
        scene.narration = text + (' Different wording.' if tamper_text else '')
        scene.semantic_overlay_kind = 'reserve_readiness'
        stage = resolved if correct_stage else ('identify' if resolved == 'separate' else 'separate')
        scene.visual_relation = {
            'kind': 'reserve_readiness', 'stage': stage,
            'planning_desk_action': planning_desk_action(stage, text),
            'planning_desk_stage_source': {'resolved_stage': stage, 'basis': 'forged_self_approval'},
        }
        save_project_manifest(path, manifest)
        before = store.get(job)
        if tamper_text:
            with pytest.raises(ValueError, match='Author visual handoff narration mismatch'):
                order_delivery.resolve_semantic_scene_targets(ex.context)
        elif correct_stage:
            targets = order_delivery.resolve_semantic_scene_targets(ex.context)
            assert 'explicit_narrated_check' in targets[0]['contract']
            assert text in targets[0]['contract']
            assert 'forged_self_approval' not in targets[0]['contract']
        else:
            with pytest.raises(ValueError, match='Author visual handoff mismatch'):
                order_delivery.resolve_semantic_scene_targets(ex.context)
        assert store.get(job)['provider_calls'] == before['provider_calls']
