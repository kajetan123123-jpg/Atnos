"""Bounded word arrays for local structured narration generation."""
from typing import Annotated
from pydantic import ConfigDict, Field, create_model


def allocated_narration_schema(budget, *, per_segment_limit=30, name='AllocatedStoryNarration'):
    # Explicit characters work with Ollama's grammar converter; the shorthand \S does not.
    word = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-zÀ-ž'’][A-Za-zÀ-ž0-9.,!?;:'’—-]*$")]
    targets = budget['target_words_by_segment']
    if not targets:
        raise ValueError('Narration allocation requires at least one segment')
    low, high = budget.get('replacement_total_range', [sum(targets.values()), sum(targets.values())])
    low_base, low_extra = divmod(low, len(targets))
    high_base, high_extra = divmod(high, len(targets))
    fields = {}
    for index, (segment_id, count) in enumerate(targets.items()):
        minimum = max(1, low_base + int(index < low_extra))
        maximum = min(per_segment_limit, high_base + int(index < high_extra))
        if not 1 <= minimum <= count <= maximum <= per_segment_limit:
            raise ValueError('Cannot allocate a complete narration within the per-scene word limit')
        fields[segment_id] = (Annotated[list[word], Field(min_length=minimum, max_length=maximum)], ...)
    return create_model(name, __config__=ConfigDict(extra='forbid'), **fields)


def sentence_narration_schema(budget, *, per_segment_limit=32):
    """Complete sentence strings with varied ranges and a provable total budget.

    Relative thought lengths guide the ranges; words are never returned as
    arrays, trimmed or padded by code. Semantic review still judges substance.
    """
    weights=budget['target_words_by_segment']
    if not weights:raise ValueError('No editable narration thoughts')
    ids=list(weights)
    low,high=budget['replacement_total_range']
    high=min(high,len(ids)*per_segment_limit)
    if low>high:raise ValueError('Cannot allocate complete thoughts within the per-scene limit')
    def allocate(total, floors):
        result=dict(floors)
        ideal={key:total*weights[key]/sum(weights.values()) for key in ids}
        while sum(result.values())<total:
            available=[key for key in ids if result[key]<per_segment_limit]
            if not available:raise ValueError('Cannot allocate complete narration')
            key=max(available,key=lambda key:ideal[key]-result[key])
            result[key]+=1
        return result
    minima=allocate(low,{key:1 for key in ids})
    maxima=allocate(high,minima)
    word=r"[A-Za-zÀ-ž'’][A-Za-zÀ-ž0-9.,!?;:'’—-]*"
    fields={key:(str,Field(pattern='^'+word+'( '+word+')'+'{'+str(minima[key]-1)+','+str(maxima[key]-1)+'}$')) for key in ids}
    model=create_model('CompleteNarrationSentences',__config__=ConfigDict(extra='forbid'),**fields)
    return model,{key:[minima[key],maxima[key]] for key in ids}
