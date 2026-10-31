"""Closed, versioned WB recipe frozen in each order's existing profile snapshot.

Candidate for production trials, never a claim of user approval of every film.
"""
from pathlib import Path
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.project_profile import content_hash

SET_ID='wb-editorial-v1'
SYSTEM_ID='wb_planning_desk_painted_v1'


def freeze_production_set(voice_candidate):
    from atnos.production.local_voice_lane import intake_voice_preset,MODEL_ID,MODEL_REVISION,NEUTRAL_INSTRUCTION,CLEAR_INSTRUCTION
    import hashlib
    from atnos.production.editorial_direction import EDITORIAL_PALETTE,PLANNING_MOTION_VERSION
    from atnos.production.remotion_renderer import _renderer_font_assets,_atnos_fonts_dir
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import renderer_source_fingerprint
    fonts=_renderer_font_assets()
    production_root=Path(__file__).resolve().parent
    composition_files=['editorial_direction.py','presenter_variant.py',
        'channels/wealth_blueprint/planning_desk_contract.py','channels/wealth_blueprint/manifest_adapters.py',
        'steps/order_delivery.py']
    root=_atnos_fonts_dir().resolve()
    if root.drive.casefold()!='e:' or any(not (root/v['filename']).is_file() or file_hash(root/v['filename'])!=v['sha256'] for v in fonts.values()):
        raise ProductionBlocked('WB production set requires its actual pinned fonts on E:; no system font fallback')
    body={'id':SET_ID,'version':1,'status':'candidate_for_repeatability_trials',
        'user_approved_as_channel_default':False,'visual_variant':'editorial_motion',
        'native_illustration_system':SYSTEM_ID,'palette':EDITORIAL_PALETTE,
        'fonts':fonts,'captions':{'style':'plain_phrase','active_word_highlight':False},
        'motion_version':PLANNING_MOTION_VERSION,'renderer_source':renderer_source_fingerprint(),
        'composition_sources':{name:file_hash(production_root/name) for name in composition_files},
        'voice':{'candidate':voice_candidate,'preset':intake_voice_preset(voice_candidate),
            'instruction_sha256':hashlib.sha256((NEUTRAL_INSTRUCTION if voice_candidate=='Ryan-neutral' else CLEAR_INSTRUCTION).encode()).hexdigest(),
            'model':MODEL_ID,'revision':MODEL_REVISION,'speed':1.,'cpu_threads':4}}
    return {**body,'digest':content_hash(body)}


def verify_production_set(profile):
    selected=profile.get('production_set')
    if selected is None:return None
    if selected!=freeze_production_set(selected.get('voice',{}).get('candidate')):
        raise ProductionBlocked('The frozen WB production set differs from the current fonts, renderer or voice recipe')
    return selected
