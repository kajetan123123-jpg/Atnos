"""A rejected copied draft is evidence, not the next writer's template."""
from types import SimpleNamespace
from test_scoped_script_revision import four_thoughts, feedback
from test_production_order_script import script
from atnos.production.scoped_script_revision import revision_source
from atnos.production.steps.order_script import NumericClaim


def test_unchanged_qualitative_rejection_restarts_from_actual_brief(four_thoughts,feedback):
    context=SimpleNamespace(artifacts={'production_order':{'topic':'One balance, two promises','direction':'Name purposes and check double-counting','optional_notes':'Keep the exact requested CTA'},'project_profile':{}})
    source=revision_source(context,four_thoughts,feedback.model_dump(mode='json'),[{'code':'revision_unchanged'}])
    assert 'FRESH FULL REWRITE FROM THE ORIGINAL BRIEF' in source
    assert 'One balance, two promises' in source
    assert 'Name purposes and check double-counting' in source
    assert four_thoughts.segments[1].text not in source
    assert 'Hypothetical car breakdown' not in source


def test_unchanged_rejection_does_not_discard_numeric_claims(script,feedback):
    script=script.model_copy(update={'claims':[NumericClaim(id='c-preserve',operation='sum',operands=[10,20],result=30,unit='dollars',spoken_result='thirty dollars',assumptions='Isolated hypothetical addition fixture.')]})
    context=SimpleNamespace(artifacts={'production_order':{'topic':'Preserve arithmetic'},'project_profile':{}})
    source=revision_source(context,script,feedback.model_dump(mode='json'),[{'code':'revision_unchanged'}])
    assert source.startswith(' PREVIOUS SCRIPT: ')
    assert script.claims[0].id in source


def test_restored_example_gets_its_own_scene_in_six_thought_rewrite(four_thoughts,feedback):
    import json
    context=SimpleNamespace(artifacts={'production_order':{'topic':'Three stocks for a speculative watchlist'},'project_profile':{}})
    request={**feedback.model_dump(mode='json'),'restore_calculation_ids':['c1'],'target_segments':[6,6]}
    source=revision_source(context,four_thoughts,request,[{'code':'revision_unchanged'}])
    data=json.loads(source.split('FRESH FULL REWRITE FROM THE ORIGINAL BRIEF: ',1)[1])
    assert len(data['scene_functions'])==6
    assert 'hypothetical calculation' in data['scene_functions'][4]
    assert 'exact original requested CTA' in data['scene_functions'][5]


def test_targeted_numeric_feedback_preserves_unaffected_scene_source(four_thoughts,feedback):
    context=SimpleNamespace(artifacts={'production_order':{'topic':'Research'},'project_profile':{}})
    request={**feedback.model_dump(mode='json'),'restore_calculation_ids':['c1'],
             'issues':[{'code':'naturalness','segment_ids':[four_thoughts.segments[1].id],'detail':'Repair only the identified reversed instruction.'}]}
    source=revision_source(context,four_thoughts,request,[{'code':'unsupported_claim'}])
    assert source.startswith(' PREVIOUS SCRIPT: ')
    assert 'Preserve unaffected thoughts' in source
