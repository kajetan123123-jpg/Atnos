"""Reject an explicit native access check that instead narrates balance adequacy.

This is a narrow mismatch detector, not a semantic approval or a financial gate.
The ordinary reviewer still assesses the complete story and its claims.
"""
import re


def double_counting_restriction_issues(segments):
    """Catch recorded whole-balance claims; different portions may serve different plans."""
    issues=[]
    for segment in segments:
        item=segment if isinstance(segment,dict) else segment.model_dump()
        text=item.get('text','')
        whole=re.search(r'\b(?:one|single|same)\s+(?:account|balance|fund)\b[^.!?]{0,60}\b(?:cannot|can.t)\b[^.!?]{0,40}\b(?:both|two)\s+(?:roles|plans|purposes|goals)\b',text,re.I)
        negated=whole and re.search(r'(?:do not|don.t)\s+assume\s*$',text[:whole.start()].strip(),re.I)
        guaranteed=re.search(r'\bif\s+(?:an?\s+)?emergency\s+(?:hits|happens|occurs)[,\s]+(?:your|the)\s+(?:future|planned)\s+purchase\s+plan\s+(?:fails|is lost|must fail)\b',text,re.I)
        if (whole and not negated) or guaranteed:
            issues.append({'code':'unsupported_categorical_restriction','segment_ids':[item['id']],
                'detail':'One sufficiently funded balance can contain different portions for different plans. The conflict is assigning the same portion twice. Do not assert that one account cannot pay two plans or that every emergency guarantees a future purchase plan fails.'})
    return issues


def access_mismatch_issues(segments):
    issues = []
    for segment in segments:
        item = segment if isinstance(segment, dict) else segment.model_dump()
        directed = item.get('visual_intent', '')
        native = re.match(r'\s*(?:native_reserve_readiness\s*:\s*)?(?:identify|access|separate|review)\s*\(', directed, re.I)
        text = item.get('text', '')
        if native:
            issues.extend(double_counting_restriction_issues([item]))
        if native and re.search(r'\bnative expense sheet\b|\b(?:belongs? to|into) the future goal notebook\b', text, re.I):
            issues.append({'code':'naturalness','segment_ids':[item['id']],
                'detail':'The spoken instruction leaks internal illustration terminology. Describe the real bill, money purpose or practical record, not a native scene asset or money belonging to a drawing.'})
        if native and re.search(r'\b(?:money|funds?)\b.*\b(?:future date|planned|earmarked)\b.*\b(?:cannot|can.t) be\b.*\b(?:safety net|emergency reserve)\b', text, re.I):
            issues.append({'code':'unsupported_categorical_restriction','segment_ids':[item['id']],
                'detail':'A planned purpose alone does not make money inaccessible for an emergency. Explain the competing obligation and the trade if it is used; do not invent an access restriction or require one allocation.'})
        if not re.match(r'\s*(?:native_reserve_readiness\s*:\s*)?access\s*\(', directed, re.I):
            continue
        # A clearly stated balance/target check cannot serve as an access check.
        balance = re.search(r'\b(?:balance|amount|target|reserve requirement)\b', text, re.I)
        adequacy = re.search(r'\b(?:meets?|sufficient|enough|covers?|adequate|requirement)\b', text, re.I)
        access = re.search(
            r'\b(?:access|withdraw\w*|account terms|conditions|waiting|notice period|fees?|penalt\w*|'
            r'how (?:to|you|it)|where (?:it|the money|your money)|when (?:you|it)|available|availability)\b', text, re.I)
        if balance and adequacy and not access:
            issues.append({'code': 'visual_narration_mismatch', 'segment_ids': [item['id']],
                'detail': 'Explicit access/timing/conditions illustration does not explain this balance adequacy or undefined reserve requirement. Correct the spoken check or choose an actually supported action matching its meaning; do not invent a reserve target.'})
    return issues
