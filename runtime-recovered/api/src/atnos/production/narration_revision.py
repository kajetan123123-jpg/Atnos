"""A measured editorial failure is not a retryable audio-assembly error."""
from atnos.production.order_store import ProductionBlocked

class NarrationRevisionRequired(ProductionBlocked):
    def __init__(self, report):
        self.report = report
        super().__init__('script_revision_required: ' + report['reason'])

def timing_revision_report(segments, timestamps, duration, profile):
    practical_ids = {s['id'] for s in segments if s['type'] == 'practical'}
    share = sum(t['duration_sec'] for t in timestamps if t['segment_id'] in practical_ids) / max(duration, .001)
    low, high = profile['duration_seconds']
    if low <= duration <= high and share >= profile['minimum_practical_share']:
        return None
    return {
        'status': 'script_revision_required', 'next_stage': 'script_repair',
        'reason': f'Measured duration={duration:.2f}s, practical_share={share:.3f}',
        'measured_duration': duration, 'measured_practical_share': share,
        'required_duration': [low, high], 'required_practical_share': profile['minimum_practical_share'],
        'segments': [{'id': s['id'], 'text': s['text'], 'declared_function': s['type'],
                      'measured_seconds': sum(t['duration_sec'] for t in timestamps if t['segment_id'] == s['id'])} for s in segments],
        'instructions': 'Rewrite concrete practical instruction or review segment function against its actual meaning. Preserve thresholds. Do not relabel unchanged explanatory text as practical. Regenerate only changed speech; never retry identical assembly.',
    }
