from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from atnos.production import visual_semantic_audit as audit


@pytest.mark.parametrize('rate,expected', [
    (30, [27.267333, 31.515, 35.762667]),
    (25, [27.274, 31.515, 35.756]),
])
def test_contact_samples_include_initial_state_and_final_hold(monkeypatch, tmp_path, rate, expected):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'fixture')
    calls = []
    monkeypatch.setattr(audit, 'resolve_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(audit, 'resolve_ffprobe', lambda _: 'ffprobe')

    def run(command, **kwargs):
        if command[0] == 'ffprobe':
            return SimpleNamespace(stdout='{"format":{"duration":"41.04"},"streams":[{"codec_type":"video","duration":"41.04","avg_frame_rate":"%d/1"}]}' % rate)
        calls.append(command)
        Image.new('RGB', (270, 480), ['red', 'green', 'blue'][len(calls)-1]).save(command[-1])
        return SimpleNamespace(stdout='')

    monkeypatch.setattr(audit.subprocess, 'run', run)
    output = tmp_path / 'contact.png'
    assert audit.extract_storyboard_contact_sheet(video, output, frame_count=3,
                                                 start_sec=27.233, end_sec=35.797) == output
    times = [float(command[command.index('-ss')+1]) for command in calls]
    assert times == pytest.approx(expected)
    assert all(Path(command[-1]).parent.parent == tmp_path for command in calls)
    assert not list(tmp_path.glob('qa-frames-*'))
    with Image.open(output) as sheet:
        assert sheet.getpixel((10, 10)) == (255, 0, 0)
        assert sheet.getpixel((284, 10)) == (0, 128, 0)
        assert sheet.getpixel((558, 10)) == (0, 0, 255)


def test_missing_sample_does_not_produce_success(monkeypatch, tmp_path):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'fixture')
    monkeypatch.setattr(audit, 'resolve_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(audit, 'resolve_ffprobe', lambda _: 'ffprobe')
    monkeypatch.setattr(audit.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='{"format":{"duration":"2"},"streams":[{"codec_type":"video","avg_frame_rate":"25/1"}]}'))
    output = tmp_path / 'contact.png'
    assert audit.extract_storyboard_contact_sheet(video, output, frame_count=3) is None
    assert not output.exists()


def test_malformed_live_verdict_is_bounded_and_never_passes(monkeypatch, tmp_path):
    settings = SimpleNamespace(shorts_semantic_visual_audit_enabled=True, app_env='development',
        ollama_vision_model='qwen3-vl:8b-instruct', ollama_base_url='http://localhost:11436',
        shorts_semantic_visual_audit_timeout_s=120)
    image = tmp_path / 'contact.png'
    Image.new('RGB', (270, 480), 'navy').save(image)
    calls=[]
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, json):
            calls.append(json)
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:{
                'message':{'content':'{"contract_satisfied":"PASS"}'},
                'done_reason':'length','prompt_eval_count':2048,'eval_count':256})
    monkeypatch.setattr(audit.httpx, 'Client', Client)
    result=audit.audit_visual_contract_compliance(settings,image,required_visual_contract='Move the attention marker between the stationary objects.')
    assert len(calls)==2
    assert calls[0]['format']['properties']['contract_satisfied']=={'type':'boolean'}
    assert result['status']=='invalid_response' and result['contract_satisfied'] is None
    assert result['response_metadata']['done_reason']=='length'
    assert '"PASS"' in result['response_excerpt']
