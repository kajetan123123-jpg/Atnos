import copy
import asyncio

import pytest

from atnos.production.scene_contract import SceneResponse, ScenePlanResponse, RENDER_CAPABILITIES
from atnos.production.steps.order_script import generation_schema
from test_production_order_script import script
from test_production_order_assets import setup, store


def native():
    return {"segment_id": "s01", "kind": "flow", "asset_id": None, "source_start": 0,
        "labels": ["Receive pay rise", "Transfer to savings"], "claim_ids": [],
        "semantic_goal": "Two action cards linked with an arrow explain moving a pay rise into savings.",
        "match_reason": "The diagram shows the exact action instead of unrelated desk footage.", "transition": "cut"}


def test_native_can_represent_the_failed_real_paycheck_scene():
    result = SceneResponse.model_validate(native()).editorial()
    assert result.kind == "flow" and result.asset_id is None
    assert result.labels == ["Receive pay rise", "Transfer to savings"]
    assert "not blindly copy" in RENDER_CAPABILITIES


@pytest.mark.parametrize("defect", ["missing_fields", "native_as_footage", "unknown_kind", "missing_labels", "fake_native_asset", "number_no_claim"])
def test_invalid_scene_cannot_reach_renderer(defect):
    value = native()
    if defect == "missing_fields":
        value.pop("asset_id")
    if defect == "native_as_footage":
        value["kind"] = "footage"
    if defect == "unknown_kind":
        value["kind"] = "split_screen"
    if defect == "missing_labels":
        value["labels"] = []
    if defect == "fake_native_asset":
        value["asset_id"] = "invented"
    if defect == "number_no_claim":
        value["kind"], value["labels"] = "number", []
    with pytest.raises(ValueError):
        SceneResponse.model_validate(value)


def test_generation_schema_requires_asset_and_labels_even_after_compaction():
    schema = generation_schema(ScenePlanResponse)
    for name in ("FootageScene", "ActionScene", "NumberScene", "ComparisonScene"):
        assert {"kind", "asset_id", "source_start", "labels", "claim_ids"}.issubset(schema["$defs"][name]["required"])
    assert schema["$defs"]["FootageScene"]["properties"]["asset_id"]["type"] == "string"
    assert schema["$defs"]["ActionScene"]["properties"]["asset_id"]["type"] == "null"


def test_actual_decoder_rejects_real_four_labels_and_full_cta_used_as_one_label():
    from atnos.production.scene_contract import scene_decoding_schema
    import jsonschema
    schema=scene_decoding_schema(ScenePlanResponse)
    labels=schema['$defs']['ActionScene']['properties']['labels']
    assert labels['maxItems']==3
    for failed in (['Rocket Lab','Check execution','Delays','Financing gaps'],
                   ['Follow Wealth Blueprint. Build wealth on purpose.']):
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(failed,labels)
    jsonschema.validate(['Execution','Funding risk'],labels)


def practical_script(script):
    script.claims = []
    for segment in script.segments:
        segment.claim_ids = []
        segment.text = 'Check your savings progress and adjust the amount if needed.'
    script.segments[3].text = "Use your bank's app to schedule automatic transfers."
    return script


def test_no_data_means_no_chart_even_in_decoder_schema(script):
    from atnos.production.scene_contract import plan_response_for, planning_constraints
    script = practical_script(script)
    library = {'assets': [{'id': 'calculator', 'duration_seconds': 20}]}
    contract = plan_response_for(script, library)
    schema = generation_schema(contract)
    assert schema['$defs']['Actions_s01']['properties']['claim_ids']['maxItems'] == 0
    assert schema['$defs']['Footage_s01']['properties']['labels']['maxItems'] == 0
    kinds = {row['segment_id']: row['allowed_kinds'] for row in planning_constraints(script, library)}
    assert kinds['s04'] == ['flow', 'steps']
    assert kinds['s05'] == ['flow', 'footage', 'steps']
    response = {'scenes': {segment.id: {**native(), 'segment_id': segment.id} for segment in script.segments}}
    assert contract.model_validate(response).model_dump(mode='json', by_alias=True) == response
    for invented_ids in ([], ['claim_001']):
        failed = copy.deepcopy(response)
        failed['scenes']['s05'].update(kind='chart', labels=[], claim_ids=invented_ids)
        with pytest.raises(ValueError):
            contract.model_validate(failed)
    failed = copy.deepcopy(response)
    failed['scenes']['s04'].update(kind='footage', asset_id='calculator', labels=[])
    with pytest.raises(ValueError):
        contract.model_validate(failed)
    for defect in ('missing_segment', 'unknown_asset', 'wrong_slot', 'invented_claim'):
        failed = copy.deepcopy(response)
        if defect == 'missing_segment':
            failed['scenes'].pop('s01')
        elif defect == 'unknown_asset':
            failed['scenes']['s01'].update(kind='footage', asset_id='imaginary', labels=[])
        elif defect == 'wrong_slot':
            failed['scenes']['s01']['segment_id'] = 's02'
        else:
            failed['scenes']['s01']['claim_ids'] = ['claim_001']
        with pytest.raises(ValueError):
            contract.model_validate(failed)


def test_real_calculation_still_allows_chart_only_for_its_segment(script):
    from atnos.production.scene_contract import scene_response_for
    from atnos.production.steps.order_script import NumericClaim
    script = practical_script(script)
    claim = NumericClaim(id='weekly', operation='product', operands=[10, 20], result=200,
        unit='dollars', spoken_result='two hundred dollars', assumptions='Twenty weekly deposits with no return.')
    script.claims = [claim]
    script.segments[0].claim_ids = ['weekly']
    script.segments[0].text = 'Save ten dollars each week for twenty weeks to reach two hundred dollars.'
    numeric = {**native(), 'kind': 'chart', 'labels': [], 'claim_ids': ['weekly']}
    library = {'assets': []}
    contract = scene_response_for(script, script.segments[0], library)
    assert contract.model_validate(numeric).root.kind == 'chart'
    wrong = scene_response_for(script, script.segments[1], library)
    with pytest.raises(ValueError):
        wrong.model_validate({**numeric, 'segment_id': 's02'})
    script.segments[0].text = 'Save regularly to build your habit.'
    with pytest.raises(ValueError):
        scene_response_for(script, script.segments[0], library).model_validate(numeric)


def test_compound_claim_on_hook_does_not_allow_future_growth_chart(script):
    from atnos.production.scene_contract import scene_response_for
    from atnos.production.steps.order_script import NumericClaim
    from atnos.production.steps.order_assets import validate_plan, ScenePlan

    claim = NumericClaim(id='opportunity', operation='compound', operands=[10, .08, 30],
        result=10 * 1.08 ** 30, unit='dollars', spoken_result='one hundred one',
        assumptions='Hypothetical annual compounding; before fees, tax and inflation.')
    script.claims = [claim]
    segment = script.segments[0]
    segment.claim_ids = [claim.id]
    segment.text = "This ten-dollar purchase isn't costing you just ten dollars."
    value = {**native(), 'segment_id': segment.id, 'kind': 'chart',
             'labels': [], 'claim_ids': [claim.id]}
    with pytest.raises(ValueError):
        scene_response_for(script, segment, {'assets': []}).model_validate(value)
    scenes = [{**native(), 'segment_id': row.id} for row in script.segments]
    scenes[0] = value
    with pytest.raises(ValueError, match='matching time horizon'):
        validate_plan(ScenePlan.model_validate({'scenes': scenes}), script, {'assets': []})

    segment.text = ('If ten dollars earned an assumed eight percent a year for thirty years, '
                    'it could grow to roughly one hundred one dollars.')
    assert scene_response_for(script, segment, {'assets': []}).model_validate(value).root.kind == 'chart'
    validate_plan(ScenePlan.model_validate({'scenes': scenes}), script, {'assets': []})
    segment.text = 'Ten dollars could grow over twenty years.'
    with pytest.raises(ValueError):
        scene_response_for(script, segment, {'assets': []}).model_validate(value)


def test_planner_retains_single_call_and_original_editorial_plan_shape(setup, script, monkeypatch):
    from atnos.production.steps import order_assets
    store, _, context = setup
    script = practical_script(script)
    library = {'assets': [{'id': 'desk', 'duration_seconds': 20}]}
    response = {'scenes': {segment.id: {**native(), 'segment_id': segment.id} for segment in reversed(script.segments)}}
    calls = []
    async def ask(settings, ctx, prompt, schema, *, decoding_schema):
        assert decoding_schema['$defs']['Actions_s01']['properties']['labels']['maxItems']==3
        calls.append(prompt)
        return schema.model_validate(response).model_dump(mode='json', by_alias=True)
    monkeypatch.setattr(order_assets, 'ask_model', ask)
    result = asyncio.run(order_assets.plan_scenes(store.settings, context, script, library,
        {'method': 'fixture', 'candidates': {}}))
    assert len(calls) == 1
    assert [scene['segment_id'] for scene in result.payload['scenes']] == [segment.id for segment in script.segments]
    assert all(scene['kind'] == 'flow' for scene in result.payload['scenes'])
