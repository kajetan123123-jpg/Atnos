"""Renderable causal states for the raise pilot; no invented amounts or proportions."""
from __future__ import annotations
import re

STATES = ('raise', 'spending', 'costs', 'choice', 'goal', 'question')
DETAILS = {
    'raise': ('income arrives; savings unchanged', 'introduce income', 'raise', 'A higher income is not automatically more saved.'),
    'spending': ('income available', 'connect income to existing spending', 'costs', 'Income pays for existing needs.'),
    'costs': ('raise available', 'connect raise to a new recurring commitment', 'recurring', 'A recurring cost can absorb additional income.'),
    'choice': ('additional income would fund new costs', 'draw an alternative route to savings', 'aside', 'Planning first can leave an affordable amount available.'),
    'goal': ('money set aside', 'connect retained money to a future goal', 'goal', 'Retained money remains available; no investment growth is implied.'),
    'question': ('alternative routes visible', 'hold the decision and ask where the next raise goes', 'where', 'The next action is deciding before committing the money.'),
}

def plan_raise_story(text: str, *, previous: str | None, is_hook=False, is_last=False):
    """Use relationships in the sentence, never a generic money keyword or scene index."""
    t = text.casefold()
    if is_hook: state = 'raise'
    elif is_last and ('?' in t or 'ask' in t): state = 'question'
    elif re.search(r'(future|financial|your|a) goal', t) and re.search(r'(aside|keep|sav|link)', t): state = 'goal'
    elif re.search(r'(plan before|before adding|before committing|set aside|decide)', t): state = 'choice'
    elif (re.search(r'\b(?:choose|direct|allocate|prioritiz\w*)\b', t)
          and re.search(r'\bsavings?\b', t)
          and re.search(r'\b(?:part|portion|increase|income|raise|first)\b', t)):
        state = 'choice'
    elif (previous == 'choice' and re.search(r'\breview\b', t)
          and re.search(r'\bspending plan\b', t)):
        # Reviewing the already chosen allocation is a hold, not another split.
        state = 'choice'
    elif re.search(r'(new|recurring|subscription)', t) and re.search(r'(absorb|cost|expense|commitment)', t): state = 'costs'
    elif re.search(r'(income|money)', t) and re.search(r'(existing costs|spending|expenses|needs|bills)', t): state = 'spending'
    else:
        raise ValueError('WB visual action unsupported for raise pilot; revise scene meaning instead of repeating a generic diagram: ' + text)
    before, action, trigger, acceptance = DETAILS[state]
    if state == 'choice' and not re.search(r'\baside\b', t):
        # Alignment must use a word actually spoken, not the generic state's alias.
        spoken = re.search(r'\b(?:choose|direct|allocate|prioritiz\w*|review|plan|decide)\b', t)
        if spoken:
            trigger = spoken.group(0)
    return {'sequence': 'raise-pilot-v1', 'state': state, 'previous_state': previous,
            'initial_state': before, 'action': action, 'final_state': acceptance,
            'understanding': acceptance, 'acceptance': acceptance,
            'objects': ['income', 'savings', 'costs', 'goal'],
            'trigger_word': trigger, 'timing_source': 'pending_final_audio',
            'motion_function': 'consequence' if state == 'goal' else 'comparison' if state == 'choice' else 'introduce_information',
            'hold_seconds': 1.5}

def bind_story_audio(story, words, start, end):
    if not words:
        raise ValueError('WB visual story requires final spoken word timings')
    result = dict(story)
    trigger = result.pop('trigger_word')
    matching = [w for w in words if trigger in re.sub(r'[^a-z]', '', w.text.casefold())]
    if not matching:
        aliases = {'raise': ('income','money'), 'costs': ('spending','expenses'), 'aside': ('plan','decide'), 'recurring': ('costs','expenses')}
        matching = [w for w in words if re.sub(r'[^a-z]', '', w.text.casefold()) in aliases.get(trigger, ())]
    if not matching:
        raise ValueError(f'WB animation phrase not found in final audio: {trigger}')
    cue = matching[0].start_sec - start
    if cue + .65 + result['hold_seconds'] > end - start:
        raise ValueError('WB visual change leaves insufficient reading hold; revise narration or extend this scene')
    result.update(cue_sec=round(cue, 3), motion_seconds=.65, timing_source='final_voice_word_alignment',
                  trigger_text=matching[0].text, trigger_absolute_sec=matching[0].start_sec)
    return result
