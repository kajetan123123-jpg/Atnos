from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt, WB_MEDIUM


def request(style):
    return GenerationRequest(channel='wealth_blueprint', workflow='WEALTH_BACKGROUND',
        subject='one teal car', visual_style_id='D', visual_style_prompt=style,
        visual_style_tags=['test'])


def test_explicit_frozen_medium_and_palette_reach_sampler_prompt():
    prompt = build_prompt(request('2D rounded color-plane illustration, coral accents, mint shadows, no text'))
    assert '(2D rounded color-plane illustration:1.15)' in prompt
    assert 'coral accents' in prompt and 'mint shadows' in prompt
    assert WB_MEDIUM['D'] not in prompt
    assert 'no text' not in prompt


def test_historical_frozen_d_retains_its_original_medium():
    prompt = build_prompt(request('mature cinematic hand-drawn editorial rendering, warm practical light'))
    assert f"({WB_MEDIUM['D']}:1.15)" in prompt
    assert 'rounded color-plane' not in prompt


def test_new_style_does_not_force_garage_subject_into_an_office(monkeypatch):
    from types import SimpleNamespace
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _try_acquisition
    from atnos.production import order_comfy_routing
    captured = []
    class Fulfiller:
        def __init__(self, settings):
            pass
        def fulfill(self, req):
            captured.append(req)
            return SimpleNamespace(asset_brain_id=None, status='awaiting_approval',
                usable_in_final_film=False, reason='fixture', output_path=None)
    monkeypatch.setattr(order_comfy_routing, 'OrderComfyAssetFulfiller', Fulfiller)
    _try_acquisition(object(), {'type':'background', 'asset_subject':'one teal car',
        'asset_composition':'Car inside a garage.'}, allow_generate=True,
        visual_style={'id':'D', 'prompt':'2D rounded illustration, mint shadows',
                      'definition_hash':'0' * 64}, style_tags=['test'])
    assert 'garage' in captured[0].text
    assert 'home-office' not in captured[0].text


def test_car_production_request_uses_single_frame_and_compact_canvas(monkeypatch):
    from types import SimpleNamespace
    from atnos.production.channels.wealth_blueprint.scene_production_assets import _try_acquisition
    from atnos.production import order_comfy_routing
    captured=[]
    class Fulfiller:
        def __init__(self, settings): pass
        def fulfill(self, req):
            captured.append(req)
            return SimpleNamespace(asset_brain_id=None,status='awaiting_approval',usable_in_final_film=False,reason='fixture',output_path=None)
    monkeypatch.setattr(order_comfy_routing,'OrderComfyAssetFulfiller',Fulfiller)
    _try_acquisition(object(),{'type':'background','asset_subject':'one mint car',
        'illustration_function':'car_repair_garage'},allow_generate=True,
        visual_style={'id':'D','prompt':'2D rounded illustration, mint shadows','definition_hash':'0'*64},style_tags=['test'])
    assert (captured[0].width,captured[0].height)==(768,1024)
    assert 'Single frame' in captured[0].text and 'Avoid: collage' in captured[0].text
    assert 'Rounded flat 2D' in captured[0].text
