import asyncio
from types import SimpleNamespace
import pytest
from atnos.production.narration_revision import NarrationRevisionRequired, timing_revision_report
from atnos.production.channels.wealth_blueprint.visual_story import plan_raise_story, bind_story_audio
from atnos.production.project_manifest import CaptionWord
from atnos.production.steps.order_script import shorten_spoken_text

def test_unsupported_relationship_is_not_a_repeated_money_icon():
    with pytest.raises(ValueError, match='unsupported'):
        plan_raise_story('Money matters to everyone.', previous='raise')

def test_audio_phrase_drives_change_and_preserves_reading_hold():
    story=plan_raise_story('Set aside an affordable amount.',previous='costs')
    words=[CaptionWord(text='aside',start_sec=12,end_sec=12.4)]
    bound=bind_story_audio(story,words,10,16)
    assert bound['cue_sec']==2
    assert bound['previous_state']=='costs'
    with pytest.raises(ValueError,match='reading hold'):
        bind_story_audio(story,words,10,13)

def test_measured_practical_share_requests_script_revision_without_relabelling():
    segments=[{'id':'s1','type':'explanation','text':'Income can pay existing costs.'},
              {'id':'s2','type':'practical','text':'Plan before adding commitments.'}]
    result=timing_revision_report(segments,[{'segment_id':'s1','duration_sec':8},{'segment_id':'s2','duration_sec':2}],10,
        {'duration_seconds':[8,20],'minimum_practical_share':.35})
    assert result['next_stage']=='script_repair'
    assert result['measured_practical_share']==.2
    assert segments[0]['type']=='explanation'

def test_narration_is_never_prefix_chopped_to_fit():
    text='A raise can leave your savings unchanged when new commitments absorb the extra income.'
    assert shorten_spoken_text(text,7)==text

def test_executor_does_not_repeat_identical_audio_assembly():
    from atnos.production.order_execution import StageExecutor
    calls=[]
    class Store:
        def context(self,job):return SimpleNamespace(artifacts={})
        def get(self,job):return {'checkpoints':[]}
        def begin(self,*args,**kwargs):calls.append('begin')
        def fail(self,*args,**kwargs):calls.append(kwargs['category'])
    async def fail():raise NarrationRevisionRequired({'reason':'too little practical content'})
    with pytest.raises(NarrationRevisionRequired):
        asyncio.run(StageExecutor(Store(),'job','token').run('voice',{},fail))
    assert calls==['begin','script_revision_required']


def test_renderer_uses_canonical_fonts_instead_of_interactive_appdata(tmp_path, monkeypatch):
    from atnos.production.remotion_renderer import _atnos_fonts_dir, _renderer_font_assets
    canonical = tmp_path / 'state'
    fonts = canonical / 'fonts'
    fonts.mkdir(parents=True)
    (fonts / 'Montserrat-Bold.ttf').write_bytes(b'canonical-bold')
    (fonts / 'Montserrat-ExtraBold.ttf').write_bytes(b'canonical-extra-bold')
    monkeypatch.delenv('ATNOS_FONTS_DIR', raising=False)
    monkeypatch.setenv('ATNOS_STATE_DIR', str(canonical))
    monkeypatch.setenv('APPDATA', str(tmp_path / 'wrong-profile'))
    assert _atnos_fonts_dir() == fonts
    assert _renderer_font_assets()['regular']['filename'] == 'Montserrat-Bold.ttf'
    override = tmp_path / 'explicit-fonts'
    monkeypatch.setenv('ATNOS_FONTS_DIR', str(override))
    assert _atnos_fonts_dir() == override


def test_both_renderer_backends_fingerprint_the_visual_story_sources():
    from atnos.production.remotion_renderer import _RENDERER_SOURCE_FILES, _renderer_package_root
    for script in ('scripts/render.ts','scripts/render-browser.ts'):
        text=(_renderer_package_root()/script).read_text(encoding='utf-8')
        for source in _RENDERER_SOURCE_FILES:
            assert repr(source) in text, (script, source)


@pytest.mark.parametrize('relabel_only', [False, True])
def test_measured_voice_routes_through_repair_and_independent_review(tmp_path, monkeypatch, relabel_only):
    from atnos.production.steps import order_voice, order_script, order_assets
    from atnos.production.order_execution import ProductionBlocked
    script = order_script.EditorialScript(title='Plan your raise', description='Qualitative savings example.', segments=[
        order_script.ScriptSegment(id=f's{i}', type='explanation', text='Income pays existing costs.',
            visual_intent='Show income flowing to existing costs.', on_screen='Existing costs') for i in range(6)])
    events=[]
    context=SimpleNamespace(artifact_dir=tmp_path, artifacts={'script':script.model_dump(mode='json')}, job_id='job')
    class Store:
        settings=object()
        def persist_legacy_artifact(self,*args):events.append('persist')
    class Executor:
        store=Store()
        token='token'
        async def run(self,name,inputs,operation,**kwargs):
            events.append(name)
            return (await operation()).payload
    executor=Executor()
    executor.context=context
    async def voice(_executor):
        events.append('voice')
        if events.count('voice')==1:
            raise NarrationRevisionRequired({'required_duration':[20,50], 'measured_duration':40,
                'measured_practical_share':.2, 'reason':'insufficient practical share'})
        return {'status':'passed'}
    async def repair(*args):
        events.append('repair')
        revised=script.model_copy(deep=True)
        revised.segments[2].type='practical'
        if not relabel_only:
            revised.segments[2].text='List your recurring costs before making another commitment.'
        return revised
    async def review(*args):
        events.append('review')
        return {'status':'passed'}
    async def assets(*args):events.append('assets')
    monkeypatch.setattr(order_voice,'_produce_voice_once',voice)
    monkeypatch.setattr(order_script,'repair',repair)
    monkeypatch.setattr(order_script,'review',review)
    monkeypatch.setattr(order_assets,'produce_assets',assets)
    if relabel_only:
        with pytest.raises(ProductionBlocked,match='relabelling'):
            asyncio.run(order_voice.produce_voice(executor))
        assert events==['voice','measured_script_repair/1','repair']
    else:
        assert asyncio.run(order_voice.produce_voice(executor))=={'status':'passed'}
        assert events==['voice','measured_script_repair/1','repair','measured_script_review/1','review','persist','assets','voice']
