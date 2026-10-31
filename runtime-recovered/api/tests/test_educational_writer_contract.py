from types import SimpleNamespace
from atnos.production.scoped_script_revision import scoped_writing_contract, revision_source
from atnos.production.steps.order_script import script_issues
from test_production_order_script import script
from pathlib import Path
from atnos.production.scoped_script_revision import _feedback_archive_target

def test_followup_archive_does_not_recursively_embed_previous_paths():
    entry={'path':'script_feedback/original/previous/files/'*12+'script.json', 'sha256':'a'*64}
    root=Path('E:/Atnos/data/production/job/script_feedback/original/current')
    target=_feedback_archive_target(root,entry,followup=True)
    assert target==root/'files'/('a'*64+'.json')
    assert _feedback_archive_target(root,{'path':'script.json','sha256':'a'*64},followup=False)==root/'files/script.json'

def context(native=True):
    return SimpleNamespace(artifacts={'project_profile':{'production':{'word_budget':[90,125],'minimum_practical_share':.33},
        **({'visual_presentation':'educational_illustration'} if native else {})},'production_order':{'topic':'Emergency readiness'}})

def test_native_writing_contract_does_not_force_static_raster():
    text=scoped_writing_contract(context())
    assert 'NATIVE LAYERED' in text and 'magnifying glass moves' in text
    assert 'STILL physical state' not in text
    assert 'STILL physical state' in scoped_writing_contract(context(False))

def test_educational_fidelity_rewrite_is_not_anchored_to_rejected_wording(script):
    source=revision_source(context(),script,{'issues':[{'code':'brief_fidelity'}]},[])
    assert 'FRESH FULL REWRITE' in source and 'Check where money is held' in source
    assert script.segments[0].text not in source

def test_real_three_questions_two_answers_response_cannot_pass(script):
    script.segments[2].text='Ask three questions: Is there a minimum balance? Are transfer fees applied to small amounts?'
    profile=context().artifacts['project_profile']
    request={'visual_presentation':'educational_illustration'}
    assert any(x['code']=='question_count_mismatch' for x in script_issues(script,profile,request=request))
    script.segments[2].text='Ask three questions: Where is it held? How do you access it? What conditions apply?'
    assert not any(x['code']=='question_count_mismatch' for x in script_issues(script,profile,request=request))
    assert not any(x['code']=='question_count_mismatch' for x in script_issues(script,profile))
