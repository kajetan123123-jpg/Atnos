from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.prompts import build_prompt, negative_prompt_with_request


def request(subject):
    return GenerationRequest(channel='wealth_blueprint',workflow='wealth_background_v1',
        subject=subject,visual_style_id='D',visual_style_prompt='2D illustrated educational animation, warm cream and turquoise',
        visual_style_tags=['wb-style-d-test'])


def test_actual_notebook_request_does_not_add_plural_main_props():
    prompt=build_prompt(request('One open unprinted cream paper notebook with two colored divider tabs'))
    assert 'one main prop centered and fully visible' in prompt
    assert 'props centered and separated' not in prompt
    assert 'two colored divider tabs' in prompt


def test_single_card_preserves_its_quantity():
    prompt=build_prompt(request('One thin horizontal paper card'))
    assert 'one main prop centered and fully visible' in prompt


def test_card_and_bag_remain_a_two_prop_composition():
    prompt=build_prompt(request('One horizontal paper card beside one small shopping bag'))
    assert 'props centered and separated' in prompt
    assert 'one main prop centered' not in prompt


def test_plural_cards_are_not_reinterpreted_as_one_object():
    prompt=build_prompt(request('One pair of cream paper cards'))
    assert 'props centered and separated' in prompt


def test_single_smartphone_gets_single_prop_framing_and_duplicate_device_bans():
    req = request('One dark navy smartphone with two blank adjacent goal panels')
    prompt = build_prompt(req)
    negative = negative_prompt_with_request('wealth_blueprint', req).casefold()

    assert 'foreground still-life, close framing, one main prop centered and fully visible' in prompt
    assert 'props centered and separated' not in prompt
    assert 'two blank adjacent goal panels' in prompt
    assert 'multiple phones' in negative
    assert 'duplicate devices' in negative
    assert 'phone grid' in negative


def test_two_phone_composition_does_not_ban_a_second_phone():
    req = request('One phone beside one smartphone')
    negative = negative_prompt_with_request('wealth_blueprint', req).casefold()

    assert 'multiple phones' not in negative
    assert 'phone grid' not in negative
