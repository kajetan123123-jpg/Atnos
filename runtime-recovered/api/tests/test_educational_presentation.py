import pytest
from pydantic import ValidationError
from atnos.production.project_profile import ProductionOrder, profile_for_order
from atnos.production.project_manifest import BrandSpec

def test_presentation_is_order_scoped_and_preserves_budgets():
    original={'production':{'limit':42},'channel_default':'unchanged'}
    order=ProductionOrder(topic='Emergency readiness', delivery_mode='local_only', visual_presentation='educational_illustration')
    frozen=profile_for_order(original,order)
    assert frozen['visual_presentation']=='educational_illustration'
    assert frozen['production']==original['production']
    assert 'visual_presentation' not in original
    assert BrandSpec(presentation=frozen['visual_presentation']).presentation=='educational_illustration'

def test_other_orders_keep_original_profile():
    original={'production':{'limit':42}}
    assert profile_for_order(original,ProductionOrder(topic='Other topic')) is original
    assert 'visual_presentation' not in ProductionOrder(topic='Other topic').model_dump(mode='json')
    assert 'presentation' not in BrandSpec().model_dump(mode='json')
    assert BrandSpec(presentation='educational_illustration').model_dump(mode='json')['presentation']=='educational_illustration'

@pytest.mark.parametrize('extra',[{'delivery_mode':'project_policy'},{'visual_style_id':'A'},{'brand_kit':'other'}])
def test_presentation_cannot_silently_mix_other_scopes(extra):
    data={'topic':'Emergency readiness','delivery_mode':'local_only','visual_presentation':'educational_illustration',**extra}
    with pytest.raises(ValidationError): ProductionOrder(**data)
