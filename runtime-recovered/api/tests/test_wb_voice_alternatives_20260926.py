from atnos.production.channels.wealth_blueprint.voice_audition import audition_variants


def test_wb_alternatives_change_voice_only_against_existing_v3_reference():
    base={'voice_id':'original','model':'eleven_multilingual_v2','provider':'elevenlabs',
          'reservation_usd_per_1000_characters':.4}
    original=dict(base)
    reference=audition_variants(base,'eric-model')[0][1]
    variants=audition_variants(base,'wb-alternatives')
    assert [name for name,_ in variants]==['Roger-v3','Brian-v3']
    assert len({config['voice_id'] for _,config in variants})==2
    for _,config in variants:
        assert config['voice_id'] != reference['voice_id']
        assert {k:v for k,v in config.items() if k!='voice_id'} == {k:v for k,v in reference.items() if k!='voice_id'}
    assert base==original  # Audition must never mutate the source profile/default.


def test_original_comparisons_are_not_replaced():
    base={'voice_id':'original','model':'eleven_multilingual_v2'}
    assert len(audition_variants(base,'voices'))==2
    assert len(audition_variants(base,'eric-model'))==1
    assert audition_variants(base,'voices')[0][1]['voice_id']=='original'
