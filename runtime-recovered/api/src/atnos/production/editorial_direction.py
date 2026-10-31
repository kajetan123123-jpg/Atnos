"""Closed, audio-bound shot direction for the research explainer renderer.

This is presentation of existing checks, never market data or a prediction.
Unsupported meaning stops at planning rather than falling back to a text card.
"""
import re
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import content_hash

VERSION = 1
PLANNING_MOTION_VERSION = 2

EDITORIAL_PALETTE={'background':'#081523','panel':'#102532','accent':'#EDB85B',
                  'accentSoft':'#60D4CD','text':'#F2EDDE','muted':'#9BAFB7'}


def scene_direction(scene):
    """The existing presentation binder and media QA use the same closed factory."""
    return planning_direction(scene) if (scene.visual_relation or {}).get('kind')=='reserve_readiness' else research_direction(scene)


def planning_direction(scene):
    from atnos.production.channels.wealth_blueprint.planning_desk_contract import planning_desk_action
    relation=scene.visual_relation or {}
    words=scene.caption_words
    start,end=scene.timing.start_sec,scene.timing.end_sec
    action=relation.get('planning_desk_action')
    if (relation.get('kind')!='reserve_readiness' or relation.get('stage') not in {'identify','separate','review'}
            or action!=planning_desk_action(relation.get('stage'),scene.narration) or not words
            or ' '.join(w.text for w in words).split()!=scene.narration.split()):
        raise ProductionBlocked('Editorial planning requires the current meaning and complete final voice alignment')
    previous=start
    for word in words:
        if not start<=word.start_sec<word.end_sec<=end+.001 or word.start_sec<previous-.001:
            raise ProductionBlocked('Editorial planning alignment is not monotonic within the scene')
        previous=word.end_sec
    objects=relation.get('objects',[])
    roles={o.get('role') for o in objects}
    if not {'reserve','goal'}<=roles or 'expense' in roles or any(o.get('label')=='PLANNED BILL FUND' for o in objects):
        raise ProductionBlocked('Editorial planning objects must be the named planning purposes, not an expense')
    if re.search(r'[\d$€£%]',scene.narration):
        raise ProductionBlocked('Qualitative planning cannot replace a numeric data composition')
    def cue(*tokens):
        word=next((w for w in words if re.sub('[^a-z]','',w.text.lower()) in tokens),None)
        if word is None:raise ProductionBlocked('Missing actual editorial planning cue: '+','.join(tokens))
        return word.start_sec-start
    if action=='write-purposes':
        if 'planning_notebook' not in roles:raise ProductionBlocked('Naming requires its actual planning notebook')
        cues={'open':cue('open'),'reserve':cue('emergency','reserve'),'name':cue('name'),'goal':cue('future','purchase','goal')}
        mode,first,second='purpose_naming',cues['reserve'],cues['goal']
        change='Expand the same two purpose cards into one planning page at Open; mark reserve then future goal at their phrases. Prompt a specific purchase without inventing its name.'
    elif action=='revisit-goal':
        if 'goal_timeline' not in roles or not relation.get('condition'):
            raise ProductionBlocked('Goal review requires the stated condition and an undated timeline')
        cues={'choice':cue('changes'),'condition':cue('used'),'review':cue('review','impact'),'timeline':cue('timeline')}
        mode,first,second='goal_review',cues['condition'],cues['timeline']
        change='Mark the conditional reserve use, focus the competing goal, then reveal its undated timeline as a question. Do not assert a delay or invent dates.'
    elif action=='shared-balance':
        if 'shared_portion' not in roles:raise ProductionBlocked('Shared claims require one common portion')
        if re.search(r'\bemergency\b',scene.narration,re.I) and re.search(r'\b(?:reduces?|less|decreases?)\b',scene.narration,re.I):
            if not relation.get('condition') or not re.search(r'\bif\b',scene.narration,re.I):
                raise ProductionBlocked('Availability effect requires an explicit conditional scenario')
            condition=cue('emergency')
            cues={'condition':condition,'effect':max(condition+.45,cue('reduces','reduce','less','decreases'))}
            for key,tokens in [('source_context',('same','funds','portion')),('claim_context',('two','competing','claims'))]:
                word=next((w for w in words if re.sub('[^a-z]','',w.text.lower()) in tokens),None)
                if word is not None and word.start_sec-start < condition:
                    cues[key]=word.start_sec-start
            mode,first,second='conditional_availability',cues['condition'],cues['effect']
            change='Guide attention to the same source, then both competing claims at their phrases. This is attention only, not spending. At emergency mark the reserve claim; only afterwards show less availability for the goal, without a numeric reduction or transfer.'
        elif re.search(r'\b(?:inspect|check|locate)\b',scene.narration,re.I) and relation.get('stage')=='review':
            cues={'inspect':cue('inspect','check'),'balance':cue('balance'),'portion':cue('portion'),'counted':cue('counted'),'both':cue('both')}
            mode,first,second='portion_audit',cues['portion'],cues['both']
            change='Inspect one portion in a planning ledger, then question its reserve and goal claims. Highlight possible overlap, never stamp a real account as checked or create another pot.'
        else:
            first=cue('promised','assigned','counted','same','overlap')
            matching=next((w for w in words if re.sub('[^a-z]','',w.text.lower()) in {'twice','both','two'} and w.start_sec-start>first),None)
            if matching is None:raise ProductionBlocked('Both real shared-claim cues are required')
            second=matching.start_sec-start
            cues={'first':first,'second':second,'portion':cue('savings','funds','money','portion')}
            if re.search(r'\bnot\s+spent\b',scene.narration,re.I):cues['not_spent']=cue('spent')
            mode='planning_claims'
            change='Show one unchanged portion, then its first and second claims at their actual phrases. Hold the common source; no amounts, allocation ratio or transfer.'
    else:raise ProductionBlocked('Unsupported editorial planning action; no generic fallback')
    if second+.6+.8>end-start:raise ProductionBlocked('Editorial planning needs reading time after its final change')
    return {'version':PLANNING_MOTION_VERSION,'mode':mode,'narration_hash':content_hash(scene.narration),
        'timing_source':'final_voice_word_alignment','duration_sec':end-start,'measurement':'qualitative_no_amounts',
        'hold_seconds':.8,'action_sec':first,'risk_sec':second,'planning_action':action,'cue_times':cues,'visible_change':change}


def research_direction(scene):
    relation = scene.visual_relation or {}
    kind = relation.get('kind')
    scene_type = getattr(scene.scene_type, 'value', scene.scene_type)
    duration = scene.timing.end_sec - scene.timing.start_sec
    words = scene.caption_words
    if duration < 2 or not words:
        raise ProductionBlocked('Editorial direction requires final voice alignment and reading time')
    if any(not (scene.timing.start_sec <= w.start_sec < w.end_sec <= scene.timing.end_sec + .05) for w in words):
        raise ProductionBlocked('Editorial direction word timing is outside the scene')
    base = {'version': VERSION, 'narration_hash': content_hash(scene.narration),
            'timing_source': 'final_voice_word_alignment', 'duration_sec': duration,
            'measurement': 'schematic_not_company_data', 'hold_seconds': .8}
    if scene_type == 'CTAScene':
        return {**base, 'mode': 'closing', 'action_sec': 0., 'risk_sec': 0.,
                'visible_change': 'The research-question motif resolves into the original channel CTA; hold it.'}
    if scene.verified_series is not None:
        return {**base, 'mode': 'verified_curve', 'action_sec': 0., 'risk_sec': 0.,
                'visible_change': 'Trace only the verified hypothetical compound series, with unit, years, rate and exclusions visible; never imply actual stock performance.'}
    if kind == 'research_check':
        objects = relation.get('objects') or []
        labels = {o.get('role'): o.get('label', '') for o in objects}
        if not all(labels.get(k) for k in ('subject', 'check', 'risk')):
            raise ProductionBlocked('Editorial research needs exact subject, check and risk clauses')
        if any(labels[k] not in scene.narration for k in ('check', 'risk')):
            raise ProductionBlocked('Editorial check/risk does not match canonical narration')
        check = labels['check'].lower()
        if 'neutron' in check:
            mode = 'execution_to_cash'
        elif 'monetization' in check and 'losses' in check:
            mode = 'per_share_tradeoff'
        elif 'repeat' in check and 'margins' in check:
            mode = 'repeat_business'
        else:
            raise ProductionBlocked('Unsupported editorial research mechanism; no generic card fallback')
        risk_tokens = {'delays', 'financing', 'valuation', 'dilution', 'regulation', 'acquisition'}
        cue = next((w for w in words if re.sub('[^a-z]', '', w.text.lower()) in risk_tokens
                    and w.start_sec > scene.timing.start_sec + .5), None)
        action_tokens = {'execution_to_cash': {'contracts'}, 'per_share_tradeoff': {'member', 'monetization'},
                         'repeat_business': {'repeat'}}[mode]
        action_cue = next((w for w in words if re.sub('[^a-z]', '', w.text.lower()) in action_tokens), None)
        # Some concise narration names execution without the contract clause.
        # In that case the exact first check word introduces the schematic.
        if action_cue is None:
            action_cue = words[0]
        if cue is None or cue.start_sec > scene.timing.end_sec - .8 or action_cue.start_sec > cue.start_sec:
            raise ProductionBlocked('Editorial risk cue missing or insufficient reading hold')
        return {**base, 'mode': mode, 'action_sec': action_cue.start_sec - scene.timing.start_sec,
                'risk_sec': cue.start_sec - scene.timing.start_sec,
                'subject': labels['subject'], 'check': labels['check'], 'risk': labels['risk'],
                'visible_change': {
                    'execution_to_cash': 'A technical launch vehicle separates into a contract-to-cash schematic. At the spoken delay/financing cue the connector is interrupted by a risk marker, not a claimed completed launch or actual cash flow.',
                    'per_share_tradeoff': 'The member-to-business flow meets the loan-loss counterflow. A dilution/per-share lens is revealed at its spoken cue; no invented bar heights, shares, values or success outcome.',
                    'repeat_business': 'A return-customer loop is traced, then the margin boundary is exposed to acquisition/regulation risk at its spoken cue. This is a research schematic, not measured retention or margins.'}[mode]}
    if scene_type == 'HookScene' and re.search(r'\bthree\b.*\bstocks?\b', scene.narration.lower()):
        return {**base, 'mode': 'question', 'action_sec': .35, 'risk_sec': 0.,
                'visible_change': 'The 10x question is revealed with three research subjects; a question mark and speculative qualification remain visible. No forecast curve.'}
    raise ProductionBlocked('Unsupported editorial direction; an explicit mechanism is required')
