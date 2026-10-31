"""Offline fixtures only: historical proof never authorizes an image request."""
from copy import copy, deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.intelligence.storage import connect, json_dumps
from atnos.production import wb_image_api as lane, wb_local_flux_trial as flux
from atnos.production import wb_flux_style_candidate as style
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain
from test_wb_local_flux_trial import local_trial, _failed_flux_evidence
from test_wb_flux_refinement import rejected_flux_parent
from test_wb_flux_style_candidate import rejected_plain_refinement


def _history_context(context):
    historical = copy(context)
    historical.artifacts = deepcopy(context.artifacts)
    archive = context.artifact_dir / 'history' / 'script.json'
    write_json(archive, historical.artifacts['script'])
    historical.artifacts['conditioning_replacement_history'] = {
        'script_path': str(archive), 'script_sha256': file_hash(archive)}
    return historical


def _forbid_writes(monkeypatch):
    monkeypatch.setattr(flux, 'write_json', lambda *a, **k: pytest.fail('readonly inspector wrote a receipt'))
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda *a: pytest.fail('inspector launched Flux'), raising=False)
    monkeypatch.setattr(flux, 'generate_flux_candidate', lambda *a: pytest.fail('inspector generated'))


def _absent(monkeypatch):
    def absent(_pid):
        raise flux.psutil.NoSuchProcess(_pid)
    monkeypatch.setattr(flux.psutil, 'Process', absent)
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **k: [])


def _runtime_files(context, receipt):
    folder = context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16]
    identity = {'pid': 654321, 'created': 1000., 'port': 8195, 'argv': ['E:/fixture/python.exe'],
        'cwd': 'E:/fixture', 'exe': 'E:/fixture/python.exe'}
    instance = write_json(folder / 'instance.json', {**identity, 'trial_digest': receipt['digest'], 'marker': receipt['version']})
    runtime = write_json(folder / 'runtime.json', {'identity': identity, 'trial_digest': receipt['digest']})
    return instance.artifact_path, runtime.artifact_path


@pytest.fixture
def history(rejected_flux_parent, monkeypatch):
    store, job, context, need, receipt, asset, image, _ = rejected_flux_parent
    context = _history_context(context)
    paths = _runtime_files(context, receipt)
    # Fixture setup only: bind the deliberately fake process records to this
    # fixture's completed checkpoint exactly like the normal producer does.
    row = next(r for r in store.get(job)['checkpoints'] if r['stage'].endswith(receipt['digest'][:16]))
    entries = row['files'] + [{'path': p.relative_to(context.artifact_dir).as_posix(), 'sha256': file_hash(p)} for p in paths]
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_stage_checkpoints SET files_json=? WHERE job_id=? AND stage=?',
                   (json_dumps(entries), job, row['stage']))
        db.commit()
    _absent(monkeypatch)
    return store, job, context, need, receipt, asset


def _inspect(history):
    store, job, context, need, _, _ = history
    return flux.verify_conditioning_replacement_history(store.settings, context, 's01', need,
                                                       lane.load_image_lane(store.settings, job))


def test_no_flux_returns_none_without_creating_any_action(local_trial, monkeypatch):
    store, job, context, need, _ = local_trial
    before = store.get(job)
    _forbid_writes(monkeypatch)
    assert flux.verify_conditioning_replacement_history(store.settings, context, 's01', need,
        lane.load_image_lane(store.settings, job)) is None
    assert store.get(job) == before


def test_rejected_historical_parent_is_stable_readonly_proof(history, monkeypatch):
    store, job, context, need, receipt, _ = history
    # Current in-memory writing differs. Only the actual hash-bound archive can
    # satisfy the old receipt; the inspector must not relabel this new text.
    context.artifacts['script']['segments'][0]['text'] = 'A different current canonical sentence.'
    before = store.get(job)
    files = {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}
    _forbid_writes(monkeypatch)
    result = _inspect(history)
    assert result == _inspect(history)
    assert result['lineage_digests'] == [receipt['digest']]
    assert result['historical_script_hash'] == receipt['script_hash']
    assert result['historical_need_hash'] == receipt['need_hash']
    assert result['current_need_hash'] == content_hash(need)
    assert result['new_generation_authorized'] is False and result['flux_retry_permitted'] is False
    assert result['lineage'][0]['terminal_proof']['status'] == 'verified_rejected_result'
    assert result['digest'] == content_hash({k: v for k, v in result.items() if k != 'digest'})
    assert store.get(job) == before
    assert files == {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['missing-binding', 'archive-hash', 'wrong-script', 'outside-root', 'subject', 'function', 'review', 'runtime', 'receipt'])
def test_changed_or_incomplete_history_blocks_before_any_operation(history, monkeypatch, damage):
    store, job, context, need, receipt, asset = history
    binding = context.artifacts['conditioning_replacement_history']
    if damage == 'missing-binding':
        del context.artifacts['conditioning_replacement_history']
    elif damage == 'archive-hash':
        binding['script_sha256'] = '0' * 64
    elif damage == 'wrong-script':
        path = Path(binding['script_path'])
        payload = json.loads(path.read_text('utf-8'))
        payload['segments'][0]['text'] = 'This is a different archived script.'
        write_json(path, payload)
        binding['script_sha256'] = file_hash(path)
    elif damage == 'outside-root':
        binding['script_path'] = str(context.artifact_dir.parent / 'script.json')
    elif damage == 'subject':
        need['asset_subject'] = 'one unrelated cup'
    elif damage == 'function':
        need['illustration_function'] = 'coffee_reassurance'
    elif damage == 'review':
        asset.reviewed_by = 'unproven rejection'
    elif damage == 'runtime':
        path = context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16] / 'runtime.json'
        path.write_text('{}', encoding='utf-8')
    else:
        (context.artifact_dir / 'flux_trial/s01/authorization.json').write_text('{}', encoding='utf-8')
    before = store.get(job)
    _forbid_writes(monkeypatch)
    with pytest.raises(ProductionBlocked):
        _inspect(history)
    assert store.get(job) == before


@pytest.mark.parametrize('status', ['running', 'pending', 'failed'])
def test_nonterminal_or_unreconciled_checkpoint_does_not_enable_replacement(history, monkeypatch, status):
    store, job, context, _, receipt, _ = history
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_stage_checkpoints SET status=? WHERE job_id=? AND stage=?',
            (status, job, f'assets/image_local/flux2-trial/s01/{receipt["digest"][:16]}'))
        db.commit()
    _forbid_writes(monkeypatch)
    with pytest.raises(ProductionBlocked):
        _inspect(history)


def test_live_or_reused_flux_process_blocks_replacement(history, monkeypatch):
    class Live:
        def create_time(self): return 1000.
    monkeypatch.setattr(flux.psutil, 'Process', lambda _pid: Live())
    with pytest.raises(ProductionBlocked, match='remains active'):
        _inspect(history)
    monkeypatch.setattr(Live, 'create_time', lambda _: 2000.)
    with pytest.raises(ProductionBlocked, match='reused'):
        _inspect(history)


def test_listener_or_denied_process_inspection_blocks_replacement(history, monkeypatch):
    monkeypatch.setattr(flux.psutil, 'net_connections', lambda **_: [SimpleNamespace(
        status=flux.psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8195))])
    with pytest.raises(ProductionBlocked, match='listener'):
        _inspect(history)
    def denied(_pid): raise flux.psutil.AccessDenied(_pid)
    monkeypatch.setattr(flux.psutil, 'Process', denied)
    with pytest.raises(ProductionBlocked, match='verify'):
        _inspect(history)


def test_orphan_flux_checkpoint_is_not_absence_of_history(local_trial, monkeypatch):
    store, job, context, need, _ = local_trial
    with store.lease(job) as token:
        store.begin(job, token, 'assets/image_local/flux2-trial/s01/' + 'f' * 16, {'fixture': 'orphan'})
    _forbid_writes(monkeypatch)
    with pytest.raises(ProductionBlocked, match='no registered lineage'):
        flux.verify_conditioning_replacement_history(store.settings, context, 's01', need,
            lane.load_image_lane(store.settings, job))


def test_existing_failed_fallback_readonly_mode_never_creates_missing_receipt(tmp_path, monkeypatch):
    context, scene, receipt, checkpoint = _failed_flux_evidence(tmp_path, monkeypatch)
    _absent(monkeypatch)
    with pytest.raises(ProductionBlocked, match='already registered'):
        flux.verify_failed_flux_allows_local_comfy_fallback(context, scene, receipt, checkpoint, read_only=True)
    assert not (context.artifact_dir / 'assets/image_local/flux_recovery').exists()
    result = flux.verify_failed_flux_allows_local_comfy_fallback(context, scene, receipt, checkpoint)
    _forbid_writes(monkeypatch)
    assert flux.verify_failed_flux_allows_local_comfy_fallback(context, scene, receipt, checkpoint, read_only=True) == result


def test_complete_three_receipt_lineage_keeps_current_loader_checks(rejected_plain_refinement, monkeypatch):
    store, job, context, need, original, parent, _, _, selected = rejected_plain_refinement
    with store.lease(job) as token:
        styled = style.register_flux_style_candidate(store.settings, job, 's01', parent['digest'], worker_token=token)
    context = _history_context(context)
    # Process ownership is tested above with actual fake instance files. This
    # lineage fixture uses an isolated producer stub with no real subprocess.
    monkeypatch.setattr(flux, '_conditioning_history_process_absence', lambda *a: {'process_state': 'fixture_no_process'})
    context.artifacts['script']['segments'][0]['text'] = 'New text which is not the historical Flux source.'
    before = store.get(job)
    _forbid_writes(monkeypatch)
    proof = flux.verify_conditioning_replacement_history(store.settings, context, 's01', need, selected)
    assert proof['lineage_digests'] == [original['digest'], parent['digest'], styled['digest']]
    assert proof['lineage'][-1]['terminal_proof']['status'] == 'not_dispatched'
    assert store.get(job) == before
    # The ordinary execution loader still rejects the different current text.
    with pytest.raises(ProductionBlocked):
        flux.load_flux_trial(store.settings, context, 's01', need, selected)


@pytest.fixture
def failed_style_history(rejected_plain_refinement, monkeypatch):
    store, job, context, need, original, parent, _, _, selected = rejected_plain_refinement
    with store.lease(job) as token:
        receipt = style.register_flux_style_candidate(store.settings, job, 's01', parent['digest'], worker_token=token)
    context = _history_context(context)
    folder = context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16]
    (folder / 'output').mkdir(parents=True)
    instance_path, runtime_path = _runtime_files(context, receipt)
    instance = json.loads(instance_path.read_text('utf-8'))
    instance['argv'] = ['E:/fixture/python.exe', '-s', 'E:/fixture/main.py', '--listen', '127.0.0.1',
        '--port', '8195', '--base-directory', str(folder), '--output-directory', str(folder / 'output')]
    write_json(instance_path, instance)
    identity = {k: v for k, v in instance.items() if k not in {'trial_digest', 'marker'}}
    write_json(runtime_path, {'identity': identity, 'trial_digest': receipt['digest']})
    write_json(folder / 'dispatch.json', {'status': 'queued', 'prompt_id': 'fixture-prompt',
        'trial_digest': receipt['digest'], 'workflow_sha256': receipt['workflow_sha256']})
    write_json(folder / 'failure.json', {'automatic_retry': False, 'dispatch_preserved': True,
        'error': 'ComfyUI runtime stopped while processing prompt fixture-prompt: [WinError 10061] connection refused',
        'error_type': 'ComfyUiImageGenerationError', 'status': 'stopped_nonretryable', 'trial_digest': receipt['digest']})
    stage = f'assets/image_local/flux2-trial/s01/{receipt["digest"][:16]}'
    with store.lease(job) as token:
        store.begin(job, token, stage, {'fixture': receipt['digest']})
        store.fail(job, token, stage, category='dependency_failure',
                   message='Flux trial stopped; outcome evidence preserved and no second POST permitted')
    checkpoint = next(row for row in store.get(job)['checkpoints'] if row['stage'] == stage)
    _absent(monkeypatch)
    fallback = flux.verify_failed_flux_allows_local_comfy_fallback(context, 's01', receipt, checkpoint)
    process_check = flux._conditioning_history_process_absence
    monkeypatch.setattr(flux, '_conditioning_history_process_absence', lambda ctx, rec, row:
        process_check(ctx, rec, row) if rec['digest'] == receipt['digest'] else {'process_state': 'fixture_no_process'})
    return store, job, context, need, receipt, selected, fallback


def test_full_chain_uses_existing_failed_style_fallback_without_writing(failed_style_history, monkeypatch):
    store, job, context, need, receipt, selected, fallback = failed_style_history
    before = store.get(job)
    before_files = {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}
    _forbid_writes(monkeypatch)
    result = flux.verify_conditioning_replacement_history(store.settings, context, 's01', need, selected)
    assert len(result['lineage_digests']) == 3
    last = result['lineage'][-1]
    assert last['digest'] == receipt['digest']
    assert last['terminal_proof']['status'] == 'verified_existing_fallback'
    assert last['terminal_proof']['fallback_digest'] == fallback['digest']
    assert result == flux.verify_conditioning_replacement_history(store.settings, context, 's01', need, selected)
    assert store.get(job) == before
    assert before_files == {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}


def test_historical_result_survives_updated_builder_but_execution_stays_pinned(failed_style_history, monkeypatch):
    store, job, context, need, _, selected, _ = failed_style_history
    rows = flux._conditioning_history_rows(store.settings, job, 's01')
    base = json.loads(rows['production_wb_flux_trials'][0]['payload_json'])
    builder = Path(base['runtime_files']['builder']['path'])
    builder.write_bytes(b'Updated current builder, not used to inspect past results')
    before = store.get(job)
    before_files = {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}
    _forbid_writes(monkeypatch)
    proof = flux.verify_conditioning_replacement_history(store.settings, context, 's01', need, selected)
    assert len(proof['lineage_digests']) == 3
    assert proof['new_generation_authorized'] is False
    assert proof['lineage'][-1]['terminal_proof']['status'] == 'verified_existing_fallback'
    with pytest.raises(ProductionBlocked, match='integrity'):
        flux.load_flux_trial(store.settings, context, 's01', need, selected)
    assert store.get(job) == before
    assert before_files == {str(p): file_hash(p) for p in context.artifact_dir.rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage', ['deleted', 'changed'])
def test_full_chain_missing_or_changed_fallback_is_never_recreated(failed_style_history, monkeypatch, damage):
    store, job, context, need, _, selected, fallback = failed_style_history
    path = Path(fallback['evidence_path'])
    if damage == 'deleted':
        path.unlink()
    else:
        path.write_text('{}', encoding='utf-8')
    _forbid_writes(monkeypatch)
    with pytest.raises(ProductionBlocked, match='fallback|Flux'):
        flux.verify_conditioning_replacement_history(store.settings, context, 's01', need, selected)
    assert path.exists() == (damage != 'deleted')
