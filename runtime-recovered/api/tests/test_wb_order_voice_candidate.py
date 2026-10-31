import copy

import pytest
from pydantic import ValidationError

from atnos.production.channel_contract import ChannelContractError, _expected_voice
from atnos.production.project_profile import ProductionOrder, profile_for_order


def test_roger_is_scoped_to_one_order_and_does_not_mutate_default():
    base = {'project': 'wealth_blueprint', 'production': {'voice': {
        'provider': 'elevenlabs', 'voice_id': 'default', 'model': 'eleven_multilingual_v2',
        'stability': .7, 'similarity': .78, 'speed': 1.12, 'style': 0., 'speaker_boost': False,
    }}}
    before = copy.deepcopy(base)
    trial = profile_for_order(base, ProductionOrder(topic='Pay raise decision', delivery_mode='local_only', voice_candidate='Roger-v3'))
    assert base == before
    assert trial['production']['voice']['voice_id'] == 'CwhRBWXzGAHq8TQ4Fs17'
    assert trial['production']['voice']['model'] == 'eleven_v3'
    assert _expected_voice(trial, {'voice': {'provider': 'elevenlabs', 'voice': 'default'}}) == ('elevenlabs', 'CwhRBWXzGAHq8TQ4Fs17')
    trial['production']['voice']['voice_id'] = 'unrecognized'
    with pytest.raises(ChannelContractError):
        _expected_voice(trial, {'voice': {'provider': 'elevenlabs', 'voice': 'default'}})


def test_candidate_cannot_become_global_or_another_channel_voice():
    with pytest.raises(ValidationError):
        ProductionOrder(topic='Pay raise', voice_candidate='Roger-v3')
    with pytest.raises(ValidationError):
        ProductionOrder(project='children_stories', production_scope='script_preview', topic='Island', delivery_mode='local_only', voice_candidate='Roger-v3')
