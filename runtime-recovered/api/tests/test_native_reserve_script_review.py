from atnos.production.native_reserve_script_review import access_mismatch_issues
import pytest


def scene(text, intent='access (reserve and questions about access, timing and conditions)'):
    return {'id':'s05','text':text,'visual_intent':intent}


def test_recorded_balance_check_cannot_explain_the_access_scene():
    text='Verify that your current balance meets the reserve requirement before funding any new specific plan or future project.'
    assert access_mismatch_issues([scene(text)])[0]['segment_ids']==['s05']
    assert not access_mismatch_issues([scene(text, 'review (review both roles)')])


@pytest.mark.parametrize('text', [
    'Check your account terms before treating that balance as available.',
    'Check how you access the reserve and what conditions apply.',
    'Is the balance enough, and can you actually withdraw it when needed?',
    'Review where it is held and when you can use it.',
])
def test_actual_access_questions_remain_for_normal_semantic_review(text):
    assert not access_mismatch_issues([scene(text)])


def test_native_asset_terms_cannot_leak_into_spoken_instructions():
    assert access_mismatch_issues([scene('Open the native expense sheet and list your annual bills.', 'identify (actual need and reserve)')])
    assert access_mismatch_issues([scene('Money for scheduled costs belongs to the future goal notebook.', 'separate (reserve versus future goal)')])
    assert not access_mismatch_issues([scene('Write the due date in your notebook.', 'identify (actual need and reserve)')])


def test_a_planned_purpose_does_not_create_a_physical_access_restriction():
    text='If the money is strictly for a future date, it cannot be your safety net when an unexpected event occurs.'
    assert access_mismatch_issues([scene(text, 'review (review both roles)')])[0]['code']=='unsupported_categorical_restriction'
    assert not access_mismatch_issues([scene('Using money assigned to a planned bill means revisiting how that bill will be paid.', 'review (review both roles)')])

@pytest.mark.parametrize('text',[
    'That single balance cannot serve both roles at once.',
    'One balance cannot pay two plans at once.',
    'Using the same money for both creates a conflict. If an emergency hits, your future purchase plan fails.',
])
def test_recorded_categorical_claims_are_rejected_even_when_moved_to_another_scene(text):
    result=access_mismatch_issues([scene(text,'identify (actual need and reserve)')])
    assert result and result[0]['code']=='unsupported_categorical_restriction'

@pytest.mark.parametrize('text',[
    'The same portion cannot pay for two purchases.',
    'Different portions of one balance can support different plans.',
    "Don't assume one balance cannot pay two plans.",
    'If an emergency happens, review which plan would need to change.',
])
def test_actual_portion_distinction_and_conditional_review_are_not_rejected(text):
    assert not access_mismatch_issues([scene(text,'review (review both roles)')])
