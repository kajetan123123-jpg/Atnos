from types import SimpleNamespace
from atnos.production.steps.order_script import editorial_review_feedback


def test_review_does_not_receive_obsolete_segment_targets_or_quotes():
    old = {'source': 'scoped_operator_feedback', 'feedback_hash': 'immutable-hash',
           'feedback': {'target_segments': [4, 5], 'issues': [{'code': 'temporal_logic',
               'segment_ids': ['s06'], 'detail': "The old version said Before accepting the raise."}]}}
    context = SimpleNamespace(artifacts={'project_profile': {'feedback_rules': [old, {'rule': 'Natural English'}]}})
    rules = editorial_review_feedback(context)
    assert rules[0]['feedback_hash'] == 'immutable-hash'
    assert rules[0]['prior_issue_categories'] == ['temporal_logic']
    assert 's06' not in str(rules) and 'Before accepting' not in str(rules)
    assert rules[1] == {'rule': 'Natural English'}
    assert old['feedback']['issues'][0]['segment_ids'] == ['s06']
