from atnos.production.comfy_channels.model_profiles import resolve_generation_tier


def test_style_trials_use_quality_for_physical_icons_and_complete_backgrounds():
    profile = {'fast_model': 'turbo.safetensors', 'quality_model': 'sdxl_base.safetensors',
               'quality_fallback_model': 'turbo.safetensors', 'fast_asset_types': ['icon', 'background']}
    for style in 'ABCD':
        for asset_type in ('icon', 'background'):
            tier = resolve_generation_tier(profile, channel='wealth_blueprint', asset_type=asset_type,
                                           visual_style_id=style)
            assert tier['tier'] == 'quality'
            assert tier['preferred_checkpoint'] == 'sdxl_base.safetensors'
            assert tier['quality_mode'] is True
            assert tier['fallback_checkpoint'] == 'turbo.safetensors'
            legacy = resolve_generation_tier(profile, channel='wealth_blueprint', asset_type=asset_type)
            assert legacy['tier'] == 'fast'
    other = resolve_generation_tier(profile, channel='dark_chronicles', asset_type='background', visual_style_id='D')
    assert other['tier'] == 'primary'
