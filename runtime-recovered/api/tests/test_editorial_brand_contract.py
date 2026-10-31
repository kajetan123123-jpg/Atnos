import pytest
from pydantic import ValidationError
from atnos.production.project_manifest import BrandSpec

def test_legacy_brand_does_not_acquire_candidate_field():
    assert 'illustrationSystem' not in BrandSpec().model_dump(mode='json')

def test_candidate_survives_the_normal_manifest_contract():
    brand = BrandSpec(illustrationSystem='wb_editorial_vector_v1')
    assert BrandSpec.model_validate(brand.model_dump(mode='json')).illustrationSystem == 'wb_editorial_vector_v1'

def test_unknown_style_is_not_silently_accepted():
    with pytest.raises(ValidationError):
        BrandSpec(illustrationSystem='random')
