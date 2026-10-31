"""Qualitative stock research diagrams, never invented quantitative company charts."""
import re

def _words(text):
    return re.findall(r'[a-z0-9]+',re.sub(r"['’]s\b",'',str(text).lower()).replace('&',' and '))

def research_check_relation(narration, headline, visual_intent, context):
    if not re.search(r'\bstocks?\b',context,re.I) or 'watchlist' not in context.lower():
        return None
    # A qualitative diagram cannot satisfy a request for a measured company series.
    if re.search(r'\b(?:bars?|line\s+(?:chart|graph)|price\s+(?:chart|graph))\b',visual_intent,re.I):
        return None
    if ':' not in headline:
        return None
    subject=headline.split(':',1)[0].strip()
    if not 2<=len(subject)<=45:
        return None
    subject_words=_words(subject)
    spoken=' '.join(_words(narration))
    pattern=r'\b'+r'\s+'.join(map(re.escape,subject_words))+r'(?:s)?\b'
    if subject_words==['hims','and','hers']:
        pattern=r'\bhims(?:\s+and\s+hers)?\b'
    if not re.search(pattern,spoken):
        return None
    sentences=[s.strip() for s in re.split(r'(?<=[.!?])\s+',narration) if s.strip()]
    if (len(sentences)<2 or not re.search(r'\b(?:check|watch|compare|track|monitor|assess|inspect)\b',sentences[0],re.I)
            or not re.search(r'\b(?:risk\w*|delay\w*|financ\w*|funding|loss\w*|regulat\w*|cost\w*|dilution|valuation|margin\w*)\b',narration,re.I)):
        return None
    return {'kind':'research_check','stage':'qualitative_comparison','action':'compare_check_and_risk',
            'objects':[{'role':'subject','label':subject}, {'role':'check','label':sentences[0]},
                       {'role':'risk','label':' '.join(sentences[1:])}],
            'initial_state':'The named company and research question are introduced.',
            'final_state':'The operating check and the stated thesis risk are both readable.',
            'viewer_should_understand':'Research '+subject+' by checking operating execution against the named risk, not a predicted price.',
            'compliance_criterion':'Use the exact narrated check and risk clauses; show no measured bar heights, historical company curve, price target or probability.',
            'unsupported_gap':None}

def bind_research_check(intent, narration, headline, visual_intent, context):
    relation=research_check_relation(narration,headline,visual_intent,context)
    if relation is None:
        return None
    return intent.model_copy(update={
        'relation':relation,'dominant_intent':'RELATIONSHIP','layout_family':'MECHANISM',
        'scene_mode':'comparison','viewer_should_understand':relation['viewer_should_understand'],
        'initial_state':relation['initial_state'],'action':relation['action'],
        'final_state':relation['final_state'],'compliance_criterion':relation['compliance_criterion'],
        'visible_change':relation['objects'][0]['label']+': reveal '+relation['objects'][1]['label']+' Then underline '+relation['objects'][2]['label']+' Both remain visible for comparison.'})
