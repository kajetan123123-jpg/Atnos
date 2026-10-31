import pytest
from pydantic import ValidationError
from atnos.production.narration_schema import sentence_narration_schema


def test_varied_sentence_string_bounds_guarantee_total_without_word_arrays():
    model,ranges=sentence_narration_schema({'target_words_by_segment':{'s02':18,'s03':24,'s04':28},
        'replacement_total_range':[70,96]})
    assert sum(v[0] for v in ranges.values())==70
    assert sum(v[1] for v in ranges.values())<=96
    assert len({v[0] for v in ranges.values()})>1
    # Controlled grammar fixture, not a quality-approved production narration.
    strings={key:' '.join(['Check']*(bounds[0]-1)+['conditions.']) for key,bounds in ranges.items()}
    value=model.model_validate(strings).model_dump()
    assert 70<=sum(len(text.split()) for text in value.values())<=96
    assert all(isinstance(text,str) for text in value.values())
    with pytest.raises(ValidationError):model.model_validate({**strings,'s02':'Too short.'})


def test_infeasible_thought_count_fails_instead_of_padding_or_clipping():
    with pytest.raises(ValueError,match='Cannot allocate'):
        sentence_narration_schema({'target_words_by_segment':{'s02':20,'s03':20},'replacement_total_range':[70,90]})
