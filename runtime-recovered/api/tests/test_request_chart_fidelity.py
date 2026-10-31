from types import SimpleNamespace as Item
from atnos.production.request_fidelity import script_issues

def script(*, claim=True, attached=True, cta='Follow the channel. Build with purpose.'):
    return Item(title='Research watchlist',description='A conditional example.',
        claims=[Item(id='c1',operation='compound')] if claim else [],
        segments=[Item(id='s01',text='If the assumed return held, this deposit could grow.',type='explanation',
                       claim_ids=['c1'] if attached else [],on_screen='Hypothetical example'),
                  Item(id='s02',text=cta,type='cta',claim_ids=[],on_screen='Follow')])

ORDER={'direction':'Include one explicitly hypothetical compound chart. Exact CTA: Follow the channel. Build with purpose.'}

def test_explicit_chart_and_cta_are_checked_even_without_savings_cadence():
    assert script_issues(script(), ORDER)==[]
    assert {x['code'] for x in script_issues(script(claim=False,cta='Buy these stocks.'),ORDER)}=={'request_chart_omitted','request_cta_mismatch'}

def test_unused_claim_does_not_satisfy_requested_chart():
    assert [x['code'] for x in script_issues(script(attached=False),ORDER)]==['request_chart_omitted']

def test_generic_company_topic_does_not_force_chart_or_cta():
    assert script_issues(script(claim=False,cta='A different ending.'),{'topic':'Business growth'})==[]

def test_cta_is_compared_without_punctuation_or_case_changes():
    assert script_issues(script(cta='FOLLOW THE CHANNEL! Build with purpose.'),ORDER)==[]


def test_real_false_pass_cannot_hide_unqualified_growth_in_a_headline():
    candidate=script()
    candidate.segments[0].text='One hundred dollars at twenty six percent annually for ten years becomes about one thousand.'
    assert [i['code'] for i in script_issues(candidate,ORDER)]==['hypothetical_framing_missing']


def test_watchlist_must_not_instruct_viewers_to_ignore_thesis_risks():
    order={**ORDER,'optional_notes':'Speculative stocks watchlist with research checks and risks.'}
    candidate=script();candidate.segments[0].text='For example, consider the trade. Ignore delays or financing gaps immediately.'
    assert [i['code'] for i in script_issues(candidate,order)]==['brief_fidelity']
    candidate.segments[0].text='For example, consider the trade. Never ignore delays or financing gaps.'
    assert script_issues(candidate,order)==[]
