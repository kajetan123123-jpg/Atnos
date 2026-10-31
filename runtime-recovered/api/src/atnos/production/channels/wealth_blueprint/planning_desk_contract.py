"""Closed actions actually implemented by the painted planning-desk renderer."""
import re

DOUBLE_COUNT_CHECK = r'\b(?:portion|money|funds)\b.{0,40}\bcounted\s+for\s+both\b'
SHARED_CLAIM = r'\bsame\s+(?:money|funds|portion)\b.{0,48}\b(?:both|two(?:\s+competing)?\s+(?:plans|claims|purposes))\b'
DOUBLE_PROMISE_HOOK = r'\b(?:promised|assigned|counted)\b.{0,40}\btwice\b.{0,40}\b(?:goals?|purposes?|plans?)\b'

ACTION_CHANGES={
    'account-to-purposes':'The camera pulls back from the account phone to the already-open notebook in the same room, revealing two purposes. The phone remains present; neither phone-to-notebook morphing nor physical opening of the book is required.',
    'purpose-context':'The two purpose labels become visible together in the planning notebook.',
    'write-purposes':'The pencil hand moves between the two printed purpose headings and traces a teal then gold underline at their spoken cues. The headings already exist; the change is the drawing of the underlines, not writing new letters.',
    'existing-assignments':'Questions about existing assignments appear under the two purpose labels.',
    'shared-balance':'Hold both purposes close. Draw links from one portion at promised/twice or same/both cues. Only for a narrated emergency reduction, mark the reserve then less availability. No amounts, proportions or transfer arrows.',
    'revisit-goal':'The competing goal is marked, then attention moves closer to its undated timeline and REVIEW GOAL DATE question at the impact cue; no guaranteed delay or invented date is shown.',
    'purpose-distinction':'The two purpose marks and their separation are emphasized.',
    'review-both':'Both established purposes remain available for a joint review.',
}

def planning_desk_action(stage,text):
    text=text.lower()
    if stage=='identify':
        if re.search(DOUBLE_PROMISE_HOOK,text):return 'shared-balance'
        return 'account-to-purposes' if re.search(r'\b(?:account|balance)\b',text) else 'purpose-context'
    if stage=='separate':return 'write-purposes'
    if stage!='review':raise ValueError('Unsupported painted planning stage')
    if re.search(r'double[- ]count|\boverlap\b|\btwice\b',text) or re.search(DOUBLE_COUNT_CHECK,text) or re.search(SHARED_CLAIM,text):return 'shared-balance'
    if re.search(r'\b(already|committed|assigned)\b',text):return 'existing-assignments'
    if re.search(r'\bwhich plan\b.{0,20}\bchanges?\b|\bimpact\b.{0,35}\b(?:goal|timeline)\b',text):return 'revisit-goal'
    if re.search(r'confus|\bdistinct\b|\bdifferent\b|\bmix',text):return 'purpose-distinction'
    return 'review-both'

def bind_planning_desk_intent(intent,narration):
    relation=intent.relation or {}
    stage=relation.get('stage')
    text=narration.lower()
    resolved_stage=stage
    if stage=='identify' and re.search(r'\bname\b',text) and 'reserve' in text and re.search(r'\b(?:future|purchase|goal)\b',text):
        resolved_stage='separate'
    elif stage=='separate' and re.search(r'\b(?:check|inspect)\b',text) and (re.search(r'\b(?:assigned|committed)\b',text) or re.search(DOUBLE_COUNT_CHECK,text)):
        resolved_stage='review'
    if resolved_stage!=stage:
        from .scene_relation import parse_scene_relation
        direction={'separate':'separate (reserve versus future goal)','review':'review (review both roles)'}[resolved_stage]
        updated=parse_scene_relation(narration,visual_context=direction,reserve_context=True)
        if updated.get('kind')!='reserve_readiness' or updated.get('stage')!=resolved_stage:
            raise ValueError('Narrated planning check does not resolve to a supported reserve action')
        relation={**updated,'planning_desk_stage_source':{'directed_stage':stage,'resolved_stage':resolved_stage,'basis':'explicit_narrated_check'}}
        intent=intent.model_copy(update={key:updated[key] or '' for key in ('viewer_should_understand','initial_state','action','final_state','compliance_criterion')})
    action=planning_desk_action(relation.get('stage'),narration)
    objects=relation.get('objects',[])
    comparison=(relation.get('stage') in {'separate','review'}
        and action in {'write-purposes','shared-balance','revisit-goal'}
        and any(o.get('role')=='goal' for o in objects)
        and any(o.get('role')=='reserve' for o in objects)
        and not any(o.get('label')=='PLANNED BILL FUND' or
                    o.get('role')=='expense' and o.get('label')!='UNEXPECTED NEED' for o in objects))
    if comparison:
        roles=[{'role':'reserve','label':'EMERGENCY RESERVE'}, {'role':'goal','label':'FUTURE GOAL'}]
        if action=='shared-balance':roles.insert(0,{'role':'shared_portion','label':'SAME PORTION'})
        if action=='write-purposes' and 'notebook' in text:roles.append({'role':'planning_notebook','label':'SAVINGS PLAN'})
        if action=='revisit-goal' and 'timeline' in text:roles.append({'role':'goal_timeline','label':'GOAL TIMELINE'})
        condition=re.search(r'\bif\b[^,.?!]*',narration,re.I)
        condition=condition.group(0).strip() if condition else None
        outcome={'write-purposes':'Mark two distinct planning purposes without inventing a specific purchase',
                 'shared-balance':'Inspect whether the same portion is counted for both purposes',
                 'revisit-goal':'Review the other plan and its undated timeline if the reserve is used'}[action]
        if action=='shared-balance' and condition:
            outcome='Only in the stated emergency scenario, less is available for the competing goal; no amount is asserted'
        relation={**relation,'objects':roles,'viewer_should_understand':outcome,
            'initial_state':'The reserve and future goal refer to the same established savings plan',
            'action':{'write-purposes':'mark_purpose_labels','shared-balance':'inspect_shared_claims','revisit-goal':'review_goal_timeline'}[action],
            'final_state':outcome,'condition':condition,
            'compliance_criterion':'Keep both purposes distinct; no invented expense, purchase name, date, amount, ratio or transfer'}
        intent=intent.model_copy(update={key:relation[key] for key in
            ('viewer_should_understand','initial_state','action','final_state','compliance_criterion')})
    if relation.get('stage') == 'identify' and re.search(DOUBLE_PROMISE_HOOK, text):
        # An old identify-need plan must not smuggle an essential expense into a
        # hook about competing claims. Rebuild the meaning, not just its action tag.
        outcome = 'Two purposes claim the same unchanged portion of savings'
        relation = {**relation, 'viewer_should_understand': outcome,
            'objects': [{'role':'shared_portion','label':'SAME PORTION'},
                        {'role':'reserve','label':'EMERGENCY RESERVE'},
                        {'role':'goal','label':'FUTURE GOAL'}],
            'initial_state':'One portion remains unchanged; no spending or transfer is shown',
            'action':'compare_claims', 'final_state':outcome,
            'compliance_criterion':'Show two claims on one portion; no invented expense, amounts, allocation ratio or transfer',
            'condition':None, 'source':None, 'destination':None, 'direction':None,
            'unsupported_gap':None}
        intent = intent.model_copy(update={key:relation[key] for key in
            ('viewer_should_understand','initial_state','action','final_state','compliance_criterion')})
    change=ACTION_CHANGES[action]
    if action=='shared-balance' and relation.get('stage')=='review':
        if (relation.get('condition') and re.search(r'\bemergency\b',text)
                and re.search(r'\b(?:reduces?|less|decreases?)\b',text)):
            change='In the explicit emergency scenario, mark the reserve claim first; only afterwards show less availability for the other purpose. No invented amount or transfer.'
        elif re.search(r'\b(?:inspect|check|locate)\b',text) and re.search(DOUBLE_COUNT_CHECK,text):
            change='Inspect one existing portion, then question its reserve and future-goal assignments in order. Reveal the possible overlap; do not mark an actual account as verified.'
    return intent.model_copy(update={'relation':{**relation,'planning_desk_action':action},
        'visible_change':change,'scene_mode':'comparison'})
