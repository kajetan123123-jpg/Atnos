"""Decoded-frame sampling regressions: no models, orders, or live checkpoints."""
from pathlib import Path
import math
import subprocess
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from atnos.production import visual_semantic_audit as audit


def _fake_probe(monkeypatch, *, fps=30):
    monkeypatch.setattr(audit, 'resolve_ffmpeg', lambda: 'ffmpeg')
    monkeypatch.setattr(audit, 'resolve_ffprobe', lambda _: 'ffprobe')
    def probe(command, **kwargs):
        if command[0] == 'ffprobe':
            return SimpleNamespace(stdout='{"format":{"duration":"37.2"},"streams":[{"codec_type":"video","avg_frame_rate":"%d/1"}]}' % fps)
        raise AssertionError('Unexpected extraction for invalid/unknown request')
    monkeypatch.setattr(audit.subprocess, 'run', probe)


@pytest.mark.parametrize('window', [
    {'start_frame': 0},
    {'start_frame': 2, 'end_frame_exclusive': 2, 'frame_rate': 30},
    {'start_frame': -1, 'end_frame_exclusive': 2, 'frame_rate': 30},
    {'start_frame': 0, 'end_frame_exclusive': 2, 'frame_rate': 25},
    {'start_frame': 0, 'end_frame_exclusive': 2, 'frame_rate': float('nan')},
    {'start_frame': 0, 'end_frame_exclusive': 2, 'frame_rate': 'unknown'},
])
def test_invalid_or_unknown_explicit_frame_window_cannot_fall_back_to_seek(monkeypatch, tmp_path, window):
    _fake_probe(monkeypatch)
    video = tmp_path / 'source.mp4'
    video.write_bytes(b'fixture')
    assert audit.extract_storyboard_contact_sheet(video, tmp_path / 'sheet.png', **window) is None


def test_missing_exact_decoded_frame_does_not_produce_success(monkeypatch, tmp_path):
    _fake_probe(monkeypatch)
    probe = audit.subprocess.run
    video = tmp_path / 'source.mp4'
    video.write_bytes(b'fixture')
    def missing(command, **kwargs):
        if command[0] == 'ffprobe':
            return probe(command, **kwargs)
        # The decoder omitted required frames, even though it exited zero.
        assert '-ss' not in command
        Image.new('RGB', (270, 480), 'red').save(command[-1].replace('%03d', '001'))
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(audit.subprocess, 'run', missing)
    output = tmp_path / 'contact.png'
    assert audit.extract_storyboard_contact_sheet(video, output, frame_count=6,
        start_frame=0, end_frame_exclusive=226, frame_rate=30) is None
    assert not output.exists()
    assert not list(tmp_path.glob('qa-frames-*'))

@pytest.mark.parametrize('indices',[[0,1,2],[0,1,1,3,4,5],[-1,1,2,3,4,5],[0,1,2,3,4,30],[0,1,2,3,4,True],[0,1,2,3,4,5.1]])
def test_invalid_action_sample_indices_never_fall_back_to_uniform_sampling(monkeypatch,tmp_path,indices):
    _fake_probe(monkeypatch);video=tmp_path/'fixture.mp4';video.write_bytes(b'fixture')
    assert audit.extract_storyboard_contact_sheet(video,tmp_path/'sheet.png',frame_count=6,
        start_frame=0,end_frame_exclusive=30,frame_rate=30,sample_frame_indices=indices) is None

def test_exact_action_observation_indices_reach_the_decoder_unchanged(monkeypatch,tmp_path):
    _fake_probe(monkeypatch);video=tmp_path/'fixture.mp4';video.write_bytes(b'fixture');seen=[]
    def decode(source,target,ffmpeg,indices,fps):seen.append(indices);return target
    monkeypatch.setattr(audit,'_extract_indexed_contact_sheet',decode)
    indices=[0,11,15,16,24,29]
    assert audit.extract_storyboard_contact_sheet(video,tmp_path/'sheet.png',frame_count=6,
        start_frame=0,end_frame_exclusive=30,frame_rate=30,sample_frame_indices=indices)==tmp_path/'sheet.png'
    assert seen==[indices]


_SCENE_BOUNDARIES = [0., 7.544, 14.348, 21.231, 27.815, 34.018, 37.2]
_SCENE_FRAMES = [math.floor(value * 30 + .5) for value in _SCENE_BOUNDARIES[:-1]] + [math.ceil(_SCENE_BOUNDARIES[-1] * 30)]
_SCENE_COLORS = [(180, 30, 30), (30, 150, 30), (30, 30, 180),
                 (160, 130, 30), (140, 30, 140), (30, 130, 150)]


@pytest.fixture
def encoded_scene_frame_numbers(tmp_path):
    """Actual inter-frame MP4 with scene colors and a decoded-frame barcode."""
    ffmpeg = audit.resolve_ffmpeg()
    if not ffmpeg:
        pytest.skip('Local FFmpeg is unavailable')
    output = tmp_path / 'fractional-scenes.mp4'
    command = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo',
               '-pixel_format', 'rgb24', '-video_size', '90x160', '-framerate', '30',
               '-i', 'pipe:0', '-an', '-c:v', 'libx264rgb', '-crf', '0', '-preset', 'ultrafast',
               '-g', '90', '-bf', '3', str(output)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for frame_number in range(_SCENE_FRAMES[-1]):
            scene_index = next(index for index in range(6)
                               if _SCENE_FRAMES[index] <= frame_number < _SCENE_FRAMES[index + 1])
            frame = Image.new('RGB', (90, 160), _SCENE_COLORS[scene_index])
            draw = ImageDraw.Draw(frame)
            for bit in range(12):
                draw.rectangle((20 + bit * 4, 60, 23 + bit * 4, 100),
                               fill='white' if frame_number & (1 << bit) else 'black')
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        process.stdin = None
        _, error = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert process.returncode == 0, error.decode(errors='replace')
    return output


def _read_tile_frame_number(sheet, index):
    tile_x = 4 + (index % 3) * 274
    tile_y = 4 + (index // 3) * 508
    return sum(1 << bit for bit in range(12)
               if sheet.getpixel((tile_x + (22 + bit * 4) * 3, tile_y + 210))[0] > 128)


def test_real_encoded_six_samples_stay_inside_each_fractional_scene(encoded_scene_frame_numbers, tmp_path):
    for scene_index, (start, end) in enumerate(zip(_SCENE_FRAMES, _SCENE_FRAMES[1:])):
        output = tmp_path / f'scene-{scene_index}.png'
        assert audit.extract_storyboard_contact_sheet(encoded_scene_frame_numbers, output, frame_count=6,
            start_sec=_SCENE_BOUNDARIES[scene_index], end_sec=_SCENE_BOUNDARIES[scene_index + 1],
            start_frame=start, end_frame_exclusive=end, frame_rate=30) == output
        expected = audit.storyboard_sample_frame_indices(start, end, 6)
        with Image.open(output) as sheet:
            actual = [_read_tile_frame_number(sheet, index) for index in range(6)]
            assert actual == expected
            assert actual[0] == start and actual[-1] == end - 1
            assert all(start <= frame < end for frame in actual)
            for index in range(6):
                x, y = 4 + (index % 3) * 274 + 12, 4 + (index // 3) * 508 + 12
                assert sheet.getpixel((x, y)) == _SCENE_COLORS[scene_index]


def test_real_encoded_exact_neighbors_are_distinct_scenes(encoded_scene_frame_numbers, tmp_path):
    for boundary in _SCENE_FRAMES[1:-1]:
        output = tmp_path / f'neighbors-{boundary}.png'
        assert audit.extract_storyboard_contact_sheet(encoded_scene_frame_numbers, output, frame_count=2,
            start_frame=boundary - 1, end_frame_exclusive=boundary + 1, frame_rate=30) == output
        with Image.open(output) as sheet:
            assert [_read_tile_frame_number(sheet, index) for index in range(2)] == [boundary - 1, boundary]


def test_real_encoded_out_of_range_index_fails_closed(encoded_scene_frame_numbers, tmp_path):
    output = tmp_path / 'past-eof.png'
    assert audit.extract_storyboard_contact_sheet(encoded_scene_frame_numbers, output, frame_count=2,
        start_frame=_SCENE_FRAMES[-1] - 1, end_frame_exclusive=_SCENE_FRAMES[-1] + 1,
        frame_rate=30) is None
    assert not output.exists()


def test_remotion_targets_match_absolute_js_round_and_final_ceil(tmp_path):
    from atnos.production.project_manifest import save_project_manifest
    from atnos.production.steps import order_delivery
    from test_semantic_qa_remotion_scene_gate import _minimal_manifest
    specs = [(f's{index + 1:02}', start, end) for index, (start, end)
             in enumerate(zip(_SCENE_BOUNDARIES, _SCENE_BOUNDARIES[1:]))]
    manifest = _minimal_manifest('fractional-fixture', specs)
    path = tmp_path / 'project_manifest.json'
    save_project_manifest(path, manifest)
    video = tmp_path / 'final.mp4'
    video.write_bytes(b'isolated-target-fixture')
    context = SimpleNamespace(artifact_dir=tmp_path, artifacts={
        'edit': {'renderer': 'remotion', 'manifest_path': str(path), 'output_file': str(video)},
        'project_profile': {'production': {'visual_rules': []}},
    })
    targets = order_delivery.resolve_semantic_scene_targets(context)
    assert [target['frame_window'] for target in targets] == [
        {'start_frame': start, 'end_frame_exclusive': end, 'frame_rate': 30}
        for start, end in zip(_SCENE_FRAMES, _SCENE_FRAMES[1:])]
    # A half-frame boundary distinguishes Math.round from Python bankers' round.
    manifest.scenes[0].timing.end_sec = 7.55  # 226.5 -> 227
    manifest.scenes[1].timing.start_sec = 7.55
    manifest.scenes[-1].timing.end_sec = 37.201  # final ceil -> 1117
    save_project_manifest(path, manifest)
    targets = order_delivery.resolve_semantic_scene_targets(context)
    assert targets[0]['frame_window']['end_frame_exclusive'] == 227
    assert targets[1]['frame_window']['start_frame'] == 227
    assert targets[-1]['frame_window']['end_frame_exclusive'] == 1117
