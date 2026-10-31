import re

from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style


def test_medium_and_literal_envelope_precede_long_composition_and_exclusions():
    style = freeze_visual_style('A')
    request = GenerationRequest(channel='wealth_blueprint', workflow='WEALTH_ICON_OR_SYMBOL',
        subject='an open cream paper pay envelope containing plain paper slips',
        composition='Large subject with a visible triangular paper flap. Do not show a leather wallet or graph. Avoid: printed currency.',
        visual_style_id='A', visual_style_prompt=style['prompt'], visual_style_tags=['wb-style-test'])
    positive, negative = build_prompt(request), negative_prompt_with_request('wealth_blueprint', request)
    assert positive.startswith('(an open cream paper pay envelope containing plain thin horizontal rectangular paper cards:1.25)')
    assert '2D ink and watercolor illustration' in positive
    assert positive.index('an open cream paper pay envelope') < 200
    assert 'leather wallet' not in positive and 'no photorealism' not in positive
    assert 'leather wallet' in negative and 'printed currency' in negative and 'photograph' in negative


def test_paper_slip_is_disambiguated_without_changing_the_required_objects():
    style=freeze_visual_style('D')
    request=GenerationRequest(channel='wealth_blueprint',workflow='WEALTH_BACKGROUND',
        subject='one blank-faced teal paper slip beside one small unbranded shopping bag',
        visual_style_id='D',visual_style_prompt=style['prompt'],visual_style_tags=['test'])
    positive=build_prompt(request)
    assert positive.startswith('(one unprinted teal thin horizontal rectangular paper card beside one small unbranded shopping bag:1.25)')
    assert 'paper slip' not in positive
    assert 'fashion model' in negative_prompt_with_request('wealth_blueprint',request)
    assert 'fashion model' not in positive


def test_four_media_remain_distinct_and_do_not_invent_other_scene_props():
    prompts = []
    for style_id in 'ABCD':
        style = freeze_visual_style(style_id)
        request = GenerationRequest(channel='wealth_blueprint', workflow='WEALTH_ICON_OR_SYMBOL',
            subject='a clear glass savings jar', visual_style_id=style_id,
            visual_style_prompt=style['prompt'], visual_style_tags=['wb-style-test'])
        prompt = build_prompt(request)
        assert 'a clear glass savings jar' in prompt
        assert not re.search(r'\b(?:desk|car|sketchbook)\b', prompt)
        prompts.append(prompt)
    assert len(set(prompts)) == 4
