"""Per-order comparison contract and normal caller, without loading a voice model."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from atnos.production.channels.wealth_blueprint import voice_audition
from atnos.production import local_voice_lane, order_feedback, order_store
from atnos.services import qwen_tts_local_runtime_service as qwen
from test_voice_comparison_request import comparison, _sha, _write_json


@pytest.fixture
def audition(comparison, monkeypatch):
    _, _, calls, root = comparison
    job = 'prod-current-fixture'
    source = _write_json(root / 'data' / 'production' / 'wealth_blueprint' / job / 'script.json', {
        'segments': [
            {'id': 's01', 'text': 'You may have promised the same savings twice.'},
            {'id': 's02', 'text': 'Name the reserve and the future purchase.'},
            {'id': 's03', 'text': 'Review both plans before changing either one.'},
        ]})
    settings = SimpleNamespace(data_dir=root / 'data')
    order = {'artifact_dir': str(source.parent), 'request': {
        'project': 'wealth_blueprint', 'delivery_mode': 'local_only'},
        'worker_live': False, 'status': 'ready', 'current_step': 'ready',
        'profile_hash': 'f' * 64, 'provider_calls': 68, 'reserved_cost_units': 0}
    feedback = [{'id': 'voice-feedback', 'source_job_id': job, 'project': 'wealth_blueprint',
                 'category': 'voice', 'scope': 'film', 'active': True,
                 'note': 'Keep the mastered voice but improve its interpretation.'}]
    lane = SimpleNamespace(model_id=qwen.MODEL_ID, model_revision=qwen.MODEL_REVISION,
                           local_only=True, preset=local_voice_lane.CLEAR_PRESET, digest='d' * 64)
    monkeypatch.setattr(order_store.ProductionOrderStore, 'get', lambda self, identity: order if identity == job else {})
    monkeypatch.setattr(order_feedback.ProductionFeedbackStore, 'list', lambda self, identity: feedback if identity == job else [])
    monkeypatch.setattr(local_voice_lane, 'load_local_voice_lane', lambda *args, **kwargs: lane)
    monkeypatch.setattr(qwen, 'AUDITION_REPORT_ROOT', root / 'reports')
    output = root / 'reports' / 'current-review' / 'voice-auditions' / job
    request = voice_audition.prepare_local_voice_audition(
        settings, job, output, segment_ids=['s01', 's02'], feedback_ids=['voice-feedback'])
    return SimpleNamespace(settings=settings, job=job, output=output, request=request,
                           request_file=output / 'request.json', source=source,
                           order=order, feedback=feedback, lane=lane, calls=calls, root=root)


def _authorize(audition, request=None):
    request = copy.deepcopy(request or audition.request)
    request['execution_authorization'] = {
        'status': 'authorized', 'user_response': 'Allow one local test, without media production.',
        'scope_sha256': qwen.audition_scope_sha256(request), 'max_new_synthesis_requests': 1}
    _write_json(audition.request_file, request)
    return request


def _run(audition, **kwargs):
    return asyncio.run(qwen.QwenTTSLocalRuntimeService(timeout_seconds=qwen.AUDITION_TIMEOUT_SECONDS).synthesize_comparison(
        request_path=audition.request_file, request_sha256=_sha(audition.request_file),
        sample_id='ryan_neutral', output_path=audition.output / 'samples' / 'ryan_neutral.wav',
        settings=kwargs.pop('settings', audition.settings), **kwargs))


def test_prepare_is_only_a_draft_and_execution_is_not_voice_acceptance(audition):
    request = audition.request
    assert audition.calls == []
    assert request['execution_authorization'] is None
    assert request['source_job_id'] != qwen.COMPARISON_SOURCE_JOB_ID
    assert request['source_snapshot']['baseline_preset'] == local_voice_lane.CLEAR_PRESET
    assert request['source_script_sha256'] == _sha(audition.source)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='execution authorization'):
        _run(audition)
    assert not (audition.output / 'samples').exists()
    _authorize(audition)
    result = _run(audition)
    assert len(audition.calls) == 1
    assert audition.calls[0]['instruction'] == local_voice_lane.NEUTRAL_INSTRUCTION
    assert audition.calls[0]['text'] == request['text']
    assert '\n\n' in request['text']  # Same paragraph separation as single-pass production.
    assert result['scope'] == 'voice_audition_only'
    assert result['schema_version'] == 2
    assert result['user_approved'] is False and result['production_enabled'] is False
    assert result['preserve_current_mastering'] is True
    assert result['cost_usd'] == 0 and result['provider_calls'] == 1
    assert 'quality_hold_sha256' not in result
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='already exists'):
        _run(audition)
    assert len(audition.calls) == 1


@pytest.mark.parametrize('change,message', [
    (lambda r: r.update(max_new_synthesis_requests=2), 'one neutral Ryan'),
    (lambda r: r.update(max_new_synthesis_requests=True), 'one neutral Ryan'),
    (lambda r: r.update(timeout_seconds=3600), 'one neutral Ryan'),
    (lambda r: r.update(max_cpu_threads=8), 'one neutral Ryan'),
    (lambda r: r.update(preserve_current_mastering=False), 'one neutral Ryan'),
    (lambda r: r.update(scope='production'), 'one neutral Ryan'),
    (lambda r: r['samples'][0].update(speaker='Aiden'), 'one neutral Ryan'),
    (lambda r: r['samples'][0].update(instruction='Arbitrary style'), 'one neutral Ryan'),
    (lambda r: r.update(samples=r['samples'] * 2), 'one neutral Ryan'),
    (lambda r: r.update(user_approved=True), 'unapproved'),
    (lambda r: r.update(production_enabled=True), 'unapproved'),
    (lambda r: r.update(cost_usd=1), 'unapproved'),
    (lambda r: r.update(model_revision='wrong'), 'unapproved'),
    (lambda r: r.update(instruction='Arbitrary style'), 'unapproved'),
    (lambda r: r.update(text='Invented text.'), 'exactly match'),
    (lambda r: r.update(segment_ids=['s02', 's01']), 'ordered'),
    (lambda r: r.update(source_script_sha256='0' * 64), 'source snapshot changed'),
    (lambda r: r.update(feedback_ids=[]), 'unique feedback'),
])
def test_tampered_scope_cannot_dispatch_even_if_reauthorized(audition, change, message):
    request = copy.deepcopy(audition.request)
    change(request)
    _authorize(audition, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match=message):
        _run(audition)
    assert audition.calls == []


def test_authorization_is_bound_to_exact_request_and_budget(audition):
    request = _authorize(audition)
    request['text'] += ' Changed.'
    _write_json(audition.request_file, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='execution authorization'):
        _run(audition)
    request = _authorize(audition)
    request['execution_authorization']['max_new_synthesis_requests'] = 2
    _write_json(audition.request_file, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='execution authorization'):
        _run(audition)
    assert audition.calls == []


@pytest.mark.parametrize('change,message', [
    (lambda a: a.order.update(worker_live=True), 'idle'),
    (lambda a: a.order.update(status='running'), 'idle'),
    (lambda a: a.order['request'].update(delivery_mode='publish'), 'idle'),
    (lambda a: a.order.update(provider_calls=69), 'source snapshot changed'),
    (lambda a: a.feedback[0].update(active=False), 'current voice feedback'),
    (lambda a: a.feedback[0].update(source_job_id='prod-another'), 'current voice feedback'),
    (lambda a: a.feedback[0].update(note='Different feedback.'), 'source snapshot changed'),
    (lambda a: setattr(a.lane, 'digest', 'c' * 64), 'source snapshot changed'),
])
def test_current_source_is_revalidated_before_inference(audition, change, message):
    _authorize(audition)
    change(audition)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match=message):
        _run(audition)
    assert audition.calls == []


def test_requires_settings_and_rejects_output_escape(audition):
    _authorize(audition)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='requires settings'):
        _run(audition, settings=None)
    other = audition.root / 'elsewhere' / audition.job / 'request.json'
    _write_json(other, json.loads(audition.request_file.read_text(encoding='utf-8')))
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='report folder'):
        asyncio.run(qwen.QwenTTSLocalRuntimeService().synthesize_comparison(
            request_path=other, request_sha256=_sha(other), sample_id='ryan_neutral',
            output_path=other.parent / 'samples' / 'ryan_neutral.wav', settings=audition.settings))
    assert audition.calls == []


def test_changed_source_during_integrity_check_cannot_dispatch(audition, monkeypatch):
    _authorize(audition)
    def changed():
        audition.feedback[0]['active'] = False
        return True
    monkeypatch.setattr(qwen, '_verified_model_files', changed)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='current voice feedback'):
        _run(audition)
    assert audition.calls == []
    receipt = json.loads((audition.output / 'samples' / 'ryan_neutral.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'failed'


def test_uncertain_child_never_retries_and_leaves_consumed_receipt(audition, monkeypatch):
    _authorize(audition)
    def timeout(*args):
        audition.calls.append({'attempt': 'dispatched'})
        raise subprocess.TimeoutExpired(args[0], 60)
    monkeypatch.setattr(qwen, '_run_qwen_child', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        _run(audition)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='already exists'):
        _run(audition)
    assert len(audition.calls) == 1
    receipt = json.loads((audition.output / 'samples' / 'ryan_neutral.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'failed'
    assert receipt['candidate_status'] == 'candidate_unapproved'


def test_same_authorization_cannot_be_replayed_in_another_valid_report_folder(audition):
    request = _authorize(audition)
    other = audition.root / 'reports' / 'different-report' / 'voice-auditions' / audition.job / 'request.json'
    _write_json(other, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='report folder'):
        asyncio.run(qwen.QwenTTSLocalRuntimeService(timeout_seconds=qwen.AUDITION_TIMEOUT_SECONDS).synthesize_comparison(
            request_path=other, request_sha256=_sha(other), sample_id='ryan_neutral',
            output_path=other.parent / 'samples' / 'ryan_neutral.wav', settings=audition.settings))
    assert audition.calls == []


def test_runtime_cannot_expand_time_budget(audition):
    _authorize(audition)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='runtime differs'):
        asyncio.run(qwen.QwenTTSLocalRuntimeService(timeout_seconds=3600).synthesize_comparison(
            request_path=audition.request_file, request_sha256=_sha(audition.request_file),
            sample_id='ryan_neutral', output_path=audition.output / 'samples' / 'ryan_neutral.wav',
            settings=audition.settings))
    assert audition.calls == []


def test_concurrent_callers_reserve_only_one_candidate(audition):
    _authorize(audition)
    async def concurrent():
        service = qwen.QwenTTSLocalRuntimeService(timeout_seconds=qwen.AUDITION_TIMEOUT_SECONDS)
        kwargs = dict(request_path=audition.request_file, request_sha256=_sha(audition.request_file),
                      sample_id='ryan_neutral', output_path=audition.output / 'samples' / 'ryan_neutral.wav',
                      settings=audition.settings)
        return await asyncio.gather(service.synthesize_comparison(**kwargs), service.synthesize_comparison(**kwargs),
                                    return_exceptions=True)
    results = asyncio.run(concurrent())
    assert len(audition.calls) == 1
    assert sum(isinstance(item, qwen.QwenTTSLocalRuntimeError) for item in results) == 1
    assert sum(isinstance(item, dict) and item['status'] == 'candidate_unapproved' for item in results) == 1


def test_evidence_changed_after_dispatch_cannot_be_accepted(audition, monkeypatch):
    _authorize(audition)
    child = qwen._run_qwen_child
    def changed(*args):
        result = child(*args)
        audition.feedback[0]['note'] = 'Changed while the child was synthesizing.'
        return result
    monkeypatch.setattr(qwen, '_run_qwen_child', changed)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='source snapshot changed'):
        _run(audition)
    receipt = json.loads((audition.output / 'samples' / 'ryan_neutral.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'failed'
    assert len(audition.calls) == 1


def test_normal_caller_uses_only_local_adapter_and_current_mastering(audition, monkeypatch):
    _authorize(audition)
    def never_paid(*args, **kwargs):
        pytest.fail('Local audition reached paid synthesis or an additional transcription model')
    monkeypatch.setattr(voice_audition.order_voice, 'synthesize', never_paid)
    monkeypatch.setattr(voice_audition.order_voice, 'transcribe', never_paid)
    async def normalize(raw, final):
        assert raw == audition.output / 'samples' / 'ryan_neutral.wav'
        final.write_bytes(raw.read_bytes())
        return {'status': 'normalized', 'loudness_target_applied': True, 'engine': 'offline_test'}
    monkeypatch.setattr(voice_audition.voice_synthesis, '_normalize_audio', normalize)
    before = audition.source.read_bytes(), copy.deepcopy(audition.order), copy.deepcopy(audition.feedback)
    result = asyncio.run(voice_audition.create_voice_audition(
        audition.settings, audition.job, audition.output, comparison='local-interpretation',
        request_sha256=_sha(audition.request_file)))
    assert len(audition.calls) == 1
    assert result[0]['user_approved'] is False
    assert result[0]['qa']['speech_match'] == 'not_performed'
    assert result[0]['time_stretch'] is False
    assert (audition.source.read_bytes(), audition.order, audition.feedback) == before
    with pytest.raises(ValueError, match='already exists'):
        asyncio.run(voice_audition.create_voice_audition(
            audition.settings, audition.job, audition.output, comparison='local-interpretation',
            request_sha256=_sha(audition.request_file)))
    assert len(audition.calls) == 1


def test_caller_does_not_accept_format_fallback_as_preserved_mastering(audition, monkeypatch):
    _authorize(audition)
    async def fallback(*args):
        return {'status': 'normalized', 'loudness_target_applied': False}
    monkeypatch.setattr(voice_audition.voice_synthesis, '_normalize_audio', fallback)
    with pytest.raises(ValueError, match='preserve the current loudness mastering'):
        asyncio.run(voice_audition.create_voice_audition(
            audition.settings, audition.job, audition.output, comparison='local-interpretation',
            request_sha256=_sha(audition.request_file)))
    assert len(audition.calls) == 1
    assert not (audition.output / 'audition.json').exists()


def test_cli_prepares_draft_and_refuses_to_execute_it(audition, monkeypatch, capsys):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'preview_wb_voice.py'
    spec = importlib.util.spec_from_file_location('voice_audition_cli', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'get_settings', lambda: audition.settings)
    output = audition.root / 'reports' / 'cli-review' / 'voice-auditions' / audition.job
    cli.main(['--source', audition.job, '--output', str(output), '--comparison', 'local-interpretation',
              '--prepare-local', '--segment', 's01', '--segment', 's02', '--feedback', 'voice-feedback'])
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'DRAFT_NOT_AUTHORIZED' and result['new_inference_calls'] == 0
    assert result['request_sha256'] == _sha(output / 'request.json')
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match='execution authorization'):
        cli.main(['--source', audition.job, '--output', str(output), '--comparison', 'local-interpretation',
                  '--request-sha256', result['request_sha256']])
    assert audition.calls == []


def test_cli_local_hash_cannot_accidentally_select_paid_default(audition, monkeypatch):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'preview_wb_voice.py'
    spec = importlib.util.spec_from_file_location('voice_audition_cli_guard', script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    def never_settings():
        pytest.fail('Malformed local execution reached settings or providers')
    monkeypatch.setattr(cli, 'get_settings', never_settings)
    with pytest.raises(SystemExit) as error:
        cli.main(['--source', audition.job, '--output', str(audition.output), '--request-sha256', '0' * 64])
    assert error.value.code == 2
