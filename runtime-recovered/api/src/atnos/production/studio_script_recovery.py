"""Explicit Studio repair of qualitative or typed hypothetical WB scripts.

The user submits defects and a note, never replacement narration. Registrations
are durable and idempotent; archives, stage attempts and call/cost counters remain
evidence, including when a later normal review overwrites a working JSON file.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shutil

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from typing import Literal

from atnos.core.logging import redact_secrets
from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.dispatch_lock import dispatch_lock
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, _event, _same_process, cost_units, file_hash
from atnos.production.project_profile import content_hash

TABLE = 'production_studio_script_recovery'
USER_EDITORIAL_REASSESSMENT_MESSAGE = (
    'Existing automatic script assessment omitted user editorial criteria; '
    'explicit user reassessment is required before production.')
MAX_ROUNDS = 3
SCRIPT_ONLY_REPAIR_VERSION = 2
HYPOTHETICAL_EDITORIAL_CODES = frozenset({
    'practical_share', 'word_budget', 'incomplete_sentence', 'sentence_punctuation',
    'structure', 'naturalness', 'long_segment', 'repetition', 'unsupported_visual_intent',
    'revision_unchanged', 'tts_numbers',
    'hypothetical_assumption_wording',
})


class StudioEditorialIssue(BaseModel):
    """User review targets, never replacement narration or financial edits."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    code: Literal['naturalness', 'structure', 'repetition', 'practical_share', 'unsupported_visual_intent']
    segment_ids: list[str] = Field(min_length=1, max_length=14)
    detail: str = Field(min_length=20, max_length=1200)


class StudioRepairBudget(BaseModel):
    model_config = ConfigDict(extra='forbid')
    writes: StrictInt = Field(ge=1, le=9)
    reviews: StrictInt = Field(ge=1, le=9)


class StudioSemanticCriterion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    segment_ids: list[str] = Field(min_length=1, max_length=14)
    detail: str = Field(min_length=20, max_length=1200)
    fields: list[Literal['text', 'on_screen', 'visual_intent']] = Field(default_factory=lambda: ['text'], min_length=1, max_length=3)


class StudioScriptRepair(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    note: str = Field(default='', max_length=2000)
    request_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    editorial_issues: list[StudioEditorialIssue] = Field(default_factory=list, max_length=12)
    execution_scope: Literal['production', 'script_only'] = 'production'
    operation_budget: StudioRepairBudget | None = None
    acceptance_criteria: list[StudioSemanticCriterion] = Field(default_factory=list, max_length=20)
    expected_canonical_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    expected_plan_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def explicit_script_scope(self):
        if self.execution_scope == 'script_only':
            if not (self.editorial_issues and self.operation_budget and self.acceptance_criteria
                    and self.expected_canonical_sha256 and self.expected_plan_sha256):
                raise ValueError('Script-only repair requires targets, explicit call budget, semantic criteria and both base hashes')
            ids = [item.id for item in self.acceptance_criteria]
            if len(set(ids)) != len(ids) or any(len(set(item.segment_ids)) != len(item.segment_ids)
                                               for item in self.acceptance_criteria) or any(len(set(item.fields)) != len(item.fields) for item in self.acceptance_criteria):
                raise ValueError('Semantic criteria and their target segments must be unique')
            if sum(len(item.segment_ids) * len(item.fields) for item in self.acceptance_criteria) > 280:
                raise ValueError('Semantic criteria exceed the supported independent assessment size')
        elif any((self.operation_budget, self.acceptance_criteria,
                  self.expected_canonical_sha256, self.expected_plan_sha256)):
            raise ValueError('Controlled repair fields require script_only scope')
        return self


class StudioSemanticCheck(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    criterion_id: str
    segment_id: str
    field: Literal['text', 'on_screen', 'visual_intent']
    quote: str = Field(min_length=1, max_length=400)
    satisfied: StrictBool
    reason: str = Field(min_length=20, max_length=1200)


class StudioCandidateAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer: str = Field(min_length=3, max_length=150)
    evidence_source: Literal['independent_operator']
    confirmation: Literal['I reviewed the entire current candidate independently']
    decision: Literal['accept', 'reject']
    checks: list[StudioSemanticCheck] = Field(min_length=1, max_length=280)


def _allowed_root(store, state):
    root = Path(state['artifact_dir']).resolve()
    storage = Path(store.settings.data_dir).resolve()
    if (os.name == 'nt' and (root.drive.casefold() != 'e:' or storage.drive.casefold() != 'e:')) or not root.is_relative_to(storage / 'production'):
        raise ProductionBlocked('Studio repair storage must stay inside production storage on E:')
    return root


def _records(settings, job_id):
    with connect(settings) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return []
        return [dict(row) for row in db.execute(f'SELECT * FROM {TABLE} WHERE job_id=? ORDER BY rowid', (job_id,))]


def _load_record(store, state, row):
    root = _allowed_root(store, state)
    path = (root / row['receipt_path']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != row['receipt_sha256']:
        raise ProductionBlocked('Studio repair request evidence is missing or changed')
    payload = json.loads(path.read_text('utf-8'))
    if (content_hash(payload) != row['request_digest'] or payload['job_id'] != state['id']
            or payload['original_request_hash'] != content_hash(state['request'])
            or payload['profile_hash'] != state['profile_hash']):
        raise ProductionBlocked('Studio repair request does not match its frozen production')
    for entry in payload['archive']:
        saved = (root / entry['archive_path']).resolve()
        if not saved.is_relative_to(root) or not saved.is_file() or file_hash(saved) != entry['sha256']:
            raise ProductionBlocked('Studio repair checkpoint archive is missing or changed')
    return payload


def load_recovery_request(store, job_id):
    rows = _records(store.settings, job_id)
    return _load_record(store, store.get(job_id), rows[-1]) if rows else None


def repair_execution_scope(store, job_id):
    """Verified persisted scope, including for legacy `run` and autonomy callers."""
    request = load_recovery_request(store, job_id)
    return (request or {}).get('execution_scope', 'production')


def _evidence_source(root, entry, archived, scoped=None):
    path = (root / entry['path']).resolve()
    if not path.is_relative_to(root):
        raise ProductionBlocked('Script evidence leaves the production directory')
    if path.is_file() and file_hash(path) == entry['sha256']:
        return path
    # Only a byte-identical previously recorded archive may replace an old
    # mutable working file; never relabel new bytes with an old hash.
    for saved in archived:
        if saved['sha256'] == entry['sha256']:
            candidate = (root / saved['archive_path']).resolve()
            if candidate.is_relative_to(root) and candidate.is_file() and file_hash(candidate) == entry['sha256']:
                return candidate
    if entry['path'] == 'script_feedback/request.json' and scoped:
        from atnos.production.scoped_script_revision import _original_evidence_source
        # The old canonical script may depend on an earlier registration
        # pointer. The scoped workflow already preserved and verified those
        # exact bytes. This exception applies only to that known pointer.
        try:
            return _original_evidence_source(root, entry, scoped)
        except OrderConflict as exc:
            raise ProductionBlocked(str(exc)) from exc
    raise ProductionBlocked('Script checkpoint evidence is missing or changed')


def _check_script_evidence(store, state, payloads):
    root = _allowed_root(store, state)
    archived = [entry for payload in payloads for entry in payload['archive']]
    from atnos.production.scoped_script_revision import load_feedback
    scoped = load_feedback(store, state['id'])
    for row in state['checkpoints']:
        if row['stage'].startswith('script') and row['status'] == 'done':
            if not row['files']:
                raise ProductionBlocked('Completed script checkpoint has no tangible evidence')
            matched_output = False
            for entry in row['files']:
                source = _evidence_source(root, entry, archived, scoped)
                if source.suffix.casefold() == '.json' or '/bytes/' in source.as_posix():
                    try:
                        matched_output |= json.loads(source.read_text('utf-8')) == row['output']
                    except (UnicodeError, ValueError):
                        pass
            if not matched_output:
                raise ProductionBlocked('Script checkpoint output does not match its hashed JSON evidence')


def _decoded(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _issue_rows(value):
    value = _decoded(value)
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict) and item.get('code')]
    if isinstance(value, dict):
        for key in ('issues', 'message', 'pending_editorial_issues'):
            rows = _issue_rows(value.get(key))
            if rows:
                return rows
    return []


ISSUE_MESSAGES = {
    'practical_share': 'Za mało treści zawiera konkretny krok, który widz może wykonać.',
    'word_budget': 'Długość wypowiedzi jest poza zakresem ustalonym dla tego filmu.',
    'incomplete_sentence': 'Wypowiedź zawiera urwane lub niepełne zdanie.',
    'structure': 'Funkcje segmentów nie realizują wstępu, rozwinięcia i zakończenia.',
    'unsupported_visual_intent': 'Opis obrazu wymaga elementów, których ten sposób ilustracji nie obsługuje.',
    'naturalness': 'Tekst wymaga bardziej naturalnych zdań i intonacji.',
    'revision_unchanged': 'Autor powtórzył poprzednio odrzucony tekst.',
    'hypothetical_assumption_wording': 'Założoną stopę przykładu błędnie opisano jako potwierdzoną średnią. Atnos musi poprawić tę wypowiedź.',
}


def _issue_message(item, state):
    if item['code'] == 'word_budget':
        detail = str(item.get('detail') or '')
        actual = re.search(r'\b(\d{1,4})\s+(?:spoken\s+)?words\b', detail)
        required = re.search(r'\b(?:expected|required)\s+(\d{1,4})\s*[-–]\s*(\d{1,4})\b', detail)
        low, high = (required[1], required[2]) if required else state['profile']['production']['word_budget']
        if actual:
            return f'Ostatnia odrzucona wypowiedź ma {actual[1]} słów; wymagany zakres to {low}–{high}. Przepisz pełne myśli, bez urywania zdań.'
        return f'Długość wypowiedzi musi mieścić się w zakresie {low}–{high} słów. Zachowaj pełne myśli.'
    return ISSUE_MESSAGES.get(str(item['code']), 'Ten element scenariusza wymaga poprawy.')


def _latest_issues(state):
    # The current blocked cause wins over an earlier unrelated rejected draft.
    current = _issue_rows(state.get('error'))
    if current:
        return current
    error = _decoded(state.get('error'))
    if (isinstance(error, dict) and error.get('category') == 'FACT_CHECK_BLOCKED'
            and (state['request'].get('intake') or {}).get('kind') == 'user_hypothetical'):
        try:
            evidence = _hypothetical_repair_evidence(state)
            return evidence.get('source_issues', [])
        except (ValueError, KeyError, TypeError):
            return []  # Unsafe facts remain unavailable to this selective repair.
    message = str(error.get('message', '')) if isinstance(error, dict) else ''
    length = re.search(r'\bRepair still has (\d{1,4}) spoken words; required (\d{1,4})[-–](\d{1,4})\b', message)
    if str(state.get('current_step') or '').startswith('script_repair/') and length:
        ids = list(dict.fromkeys(re.findall(r"['\"](s\d{2})['\"]\s*:", message)))
        if not ids:
            candidate = next((row['output'] for row in reversed(state['checkpoints'])
                if row['stage'].startswith(('script_draft', 'script_repair/')) and row['output'].get('segments')), {})
            ids = [str(segment['id']) for segment in candidate.get('segments', [])]
        return [{'code': 'word_budget', 'segment_ids': ids,
                 'detail': f'{length[1]} spoken words in the latest rejected repair; required {length[2]}-{length[3]}. Rewrite complete thoughts within the frozen range.'}]
    # Resuming updates an existing row. Insertion order is not chronology:
    # old review/2 must not override freshly updated review/0 findings.
    for row in sorted(state['checkpoints'],key=lambda item:item.get('updated_at') or '',reverse=True):
        if row['stage'].startswith(('script_review/', 'script_scoped/')):
            if row['output'].get('status') == 'failed' and row['output'].get('issues'):
                return _issue_rows(row['output'])
        if row['stage'].startswith('script'):
            rows = _issue_rows(row.get('error_json'))
            if rows:
                return rows
    # Deterministic repair errors may be prose, not the structured old review.
    return []


def _scope_blocker(state, *, issues=None):
    if state['worker_live']:
        return 'Produkcja ma aktywnego workera. Poczekaj na zakończenie jego pracy.'
    if state['status'] not in {'blocked', 'failed', 'interrupted', 'stopped'}:
        return 'Naprawa jest dostępna dla zatrzymanej produkcji z odrzuconym scenariuszem.'
    request = state['request']
    if request.get('project') != 'wealth_blueprint' or request.get('delivery_mode') != 'local_only':
        return 'Ta naprawa obsługuje lokalne próby Wealth Blueprint.'
    hypothetical = (request.get('intake') or {}).get('kind') == 'user_hypothetical'
    if request.get('intake') is not None and not hypothetical:
        return 'Scenariusz z załączonego briefu wymaga osobnej obsługi wejścia.'
    if hypothetical:
        if any(item['code'] not in HYPOTHETICAL_EDITORIAL_CODES
               for item in (_latest_issues(state) if issues is None else issues)):
            return 'Ta naprawa zachowuje hipotetyczne obliczenia. Nieznane problemy danych lub faktów wymagają osobnej oceny.'
        try:
            _hypothetical_repair_evidence(state, canonical_only=issues is not None)
        except (ValueError, KeyError, TypeError) as exc:
            return 'Nie można bezpiecznie poprawić tego przykładu bez zmiany danych: ' + redact_secrets(str(exc))[:500]
    for row in state['checkpoints']:
        stage, output = row['stage'], row['output']
        if stage.startswith(('voice', 'captions', 'render', 'remotion', 'semantic', 'media_qa', 'covers', 'package')):
            if row['status'] == 'done' or row['files'] or output.get('audio_path'):
                return 'Powstał już głos lub film. Ta naprawa działa wyłącznie przed produkcją mediów.'
        if stage.startswith('script'):
            if not hypothetical and (output.get('claims') or (stage == 'script_calculation_plan' and output.get('examples'))):
                return 'Scenariusz zawiera obliczenia finansowe. Wymaga osobnej naprawy z weryfikacją danych.'
    config = state['profile']['production']
    unlimited = state.get('local_work_unlimited', False)
    if not unlimited and state['provider_calls'] >= state['max_provider_calls']:
        return 'Wyczerpano łączny limit wywołań. Naprawa scenariusza nie resetuje tego limitu.'
    if not unlimited and state['reserved_cost_units'] >= cost_units(config['max_cost_usd']):
        return 'Wyczerpano limit kosztu. Naprawa scenariusza nie resetuje budżetu.'
    # An exhausted stage is not advertised as available. Authorization must be
    # explicit and separate; a new digest cannot reset its durable counter.
    for row in state['checkpoints']:
        if (not unlimited and row['stage'] in {'script_draft', 'script_review/0', 'script_repair/1', 'script'}
                and row['attempts'] >= config['max_attempts_per_element']
                and row['status'] != 'done'):
            return f"Etap {row['stage']} wyczerpał limit {row['attempts']} prób. Potrzebna jest osobna zgoda na dodatkową próbę."
    return None


def _hypothetical_repair_evidence(state, *, canonical_only=False):
    """Validate current source content; filesystem hashes are checked separately."""
    calculations = next((row for row in state['checkpoints']
        if row['stage'] == 'script_calculations' and row['status'] == 'done'), None)
    if not calculations:
        raise ValueError('Brakuje zapisanego wyniku kalkulatora dla tego scenariusza.')
    canonical = next((row for row in state['checkpoints'] if row['stage'] == 'script' and row['status'] == 'done'), None)
    candidates = [row for row in state['checkpoints'] if row['status'] == 'done'
                  and (row['stage'] == 'script_draft' or row['stage'].startswith('script_repair/'))
                  and row['output'].get('segments')]
    # A rejected follow-up may already contain a valid financial correction.
    # Use its latest actual bytes, not an older canonical script. All original
    # evidence and hashes still have to match before registration.
    # Explicit user notes refer to the reviewed canonical script, not a newer
    # rejected draft with potentially different segment meanings. Recovery of
    # validator failures keeps the existing latest-draft selection above.
    source = (canonical if canonical_only else
              max([*candidates, *([canonical] if canonical else [])],
                  key=lambda row: row.get('updated_at') or '', default=None))
    if not source:
        raise ValueError('Brakuje rzeczywistego tekstu, który Atnos może poprawić selektywnie.')
    from atnos.production.hypothetical_claim_evidence import editorial_script_payload, verified_binding, bind_ledger_claims, script_binding_issues
    from atnos.integrations.claim_evidence_gate import extract_claims_from_script, evaluate_gate
    payload = editorial_script_payload(source['output'])
    binding = verified_binding(payload, state['request']['intake'], calculations['output'])
    verdict = evaluate_gate(bind_ledger_claims(extract_claims_from_script(payload), payload, binding))
    issues = script_binding_issues(payload, state['request']['intake'], calculations['output'])
    if not verdict['production_allowed']:
        if not issues or any(item['code'] != 'hypothetical_assumption_wording' for item in issues):
            raise ValueError('Tekst zawiera fakt rynkowy, gwarancję albo liczbę niezgodną z hipotetycznym przykładem.')
    return {'source_stage': source['stage'], 'source_script': payload, 'source_script_hash': content_hash(payload),
            'calculator_receipt': calculations['output'], 'calculator_receipt_hash': content_hash(calculations['output']),
            'source_issues': issues, 'source_gate_passed': verdict['production_allowed']}


def recovery_snapshot(store, job_id):
    state = store.get(job_id)
    error = _decoded(state.get('error'))
    category = str(error.get('category', '')) if isinstance(error, dict) else str(error or '')
    user_reassessment = (isinstance(error, dict)
                         and error.get('message') == USER_EDITORIAL_REASSESSMENT_MESSAGE)
    contract_reassessment = category == 'script_review_contract_changed'
    step = str(state.get('current_step') or '')
    image_failed = step.startswith(('assets/image_', 'assets/style_preview/')) and category in {'dependency_failure', 'resource_unavailable', 'dependency_circuit_open'}
    kind = ('resource' if image_failed or category in {'resource_unavailable', 'dependency_circuit_open', 'dependency_failure'} or step in {'waiting_for_runtime', 'preflight'}
            else 'assets' if step.startswith(('asset', 'image', 'visual')) or category.startswith(('WB_STYLE_', 'ASSET_', 'BQA_'))
            else 'script' if step.startswith(('script', 'scripting')) or category.startswith(('script_', 'editorial_'))
            else 'integrity' if 'integrity' in category or 'hash' in category else 'other')
    if category == 'FACT_CHECK_BLOCKED' and any(item['code'] == 'hypothetical_assumption_wording' for item in _latest_issues(state)):
        kind = 'script'
    issues = _latest_issues(state) if kind == 'script' else []
    blocker = _scope_blocker(state)
    try:
        payloads = [_load_record(store, state, row) for row in _records(store.settings, job_id)]
        from atnos.production.scoped_script_revision import load_feedback
        load_feedback(store, job_id)
        _check_script_evidence(store, state, payloads)
    except (ProductionBlocked, ValueError, OSError, KeyError) as exc:
        kind, blocker = 'integrity', 'Dowody produkcji są niekompletne lub zmienione. Sprawdź pliki przed wznowieniem.'
        issues = [{'code': 'evidence_integrity', 'segment_ids': [], 'detail': redact_secrets(str(exc))}]
    if user_reassessment and kind != 'integrity':
        # The refusal concerns a missing user assessment, not a validator
        # verdict on the current text. Do not resurface failed reviews of an
        # older draft as if they were fresh, actionable findings.
        kind, issues = 'script', []
        blocker = ('Automatyczna ocena pominęła wymagania użytkownika. Potrzebna jest '
                   'nowa jawna ocena z zachowaniem tych wymagań i ograniczona akcja naprawy; '
                   'nie wolno automatycznie odnawiać wyczerpanej akcji.')
    if contract_reassessment and kind != 'integrity':
        kind, issues = 'script', []
        blocker = ('Zmienił się kontrakt oceny. Najpierw oceń aktualny scenariusz według bieżących wymagań; '
                   'dawne odrzucenia nie są wynikiem tej oceny. Nie ponawiaj wykorzystanych prób automatycznie.')
    if kind != 'script':
        blocker = blocker or 'Ta blokada wymaga naprawy zasobów lub narzędzi, a nie zmiany scenariusza.'
    elif not issues:
        blocker = blocker or 'Brak zapisanego konkretnego wyniku odrzucenia. Najpierw sprawdź błąd etapu.'
    normalized = [{
        'code': str(item['code']), 'segment_ids': list(item.get('segment_ids') or []),
        'detail': redact_secrets(str(item.get('detail') or item.get('message') or ''))[:1200],
        'message': _issue_message(item, state),
    } for item in issues]
    messages = {'script': 'Scenariusz został odrzucony. Atnos może poprawić go na podstawie konkretnych uwag.',
                'resource': 'Narzędzie lub zasoby komputera nie są gotowe do produkcji.',
                'assets': 'Produkcja czeka na poprawne grafiki lub ich ocenę.',
                'integrity': 'Nie zgadzają się dowody zapisanej produkcji.',
                'other': 'Sprawdź przyczynę zatrzymania tego etapu.'}
    if image_failed:
        messages['resource'] = 'Lokalny generator obrazu przerwał pracę. Kandydat nie powstał. Sprawdź narzędzie przed wznowieniem.'
    if user_reassessment:
        messages['script'] = 'Produkcja zatrzymana: automatyczny PASS nie uwzględnił jawnych wymagań użytkownika.'
    if contract_reassessment:
        messages['script'] = 'Aktualny scenariusz wymaga ponownej oceny po zmianie kontraktu. Historyczne błędy nie opisują jego bieżącej wersji.'
    return {'job_id': job_id, 'kind': kind, 'message': messages[kind], 'issues': normalized,
            'can_repair_script': kind == 'script' and blocker is None,
            'repair_blocker': blocker, 'worker_live': state['worker_live']}


def _archive(store, state, older_payloads):
    root = _allowed_root(store, state)
    archived = [entry for payload in older_payloads for entry in payload['archive']]
    from atnos.production.scoped_script_revision import load_feedback
    scoped = load_feedback(store, state['id'])
    snapshot = write_receipt(root / 'studio_script_recovery' / 'snapshots', {
        'checkpoints': state['checkpoints'], 'history': state['history'], 'request': state['request'],
        'profile_hash': state['profile_hash'], 'provider_calls': state['provider_calls'],
        'reserved_cost_units': state['reserved_cost_units'], 'attempts_reset': False, 'budget_reset': False,
    })
    result = [{'archive_path': snapshot.relative_to(root).as_posix(), 'sha256': file_hash(snapshot)}]
    seen = set()
    for row in state['checkpoints']:
        if not row['stage'].startswith('script'):
            continue
        for entry in row['files']:
            pair = (entry['path'], entry['sha256'])
            if pair in seen:
                continue
            source = _evidence_source(root, entry, archived, scoped)
            target = root / 'studio_script_recovery' / 'bytes' / entry['sha256']
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and file_hash(target) != entry['sha256']:
                raise OrderConflict('Existing content-addressed script archive changed')
            if not target.exists():
                shutil.copy2(source, target)
            if file_hash(target) != entry['sha256']:
                raise OrderConflict('Script archive copy does not match its original bytes')
            result.append({'original_path': entry['path'], 'archive_path': target.relative_to(root).as_posix(), 'sha256': entry['sha256']})
            seen.add(pair)
    return result


def write_receipt(folder, payload, *, short_name=False):
    from atnos.production.steps.common import write_json
    digest = content_hash(payload)
    path = folder / ((digest[:12] if short_name else digest) + '.json')
    if path.exists():
        if json.loads(path.read_text('utf-8')) != payload:
            raise OrderConflict('Immutable Studio receipt already contains different evidence')
        return path
    return write_json(path, payload).artifact_path


def _audit_hash(digest):
    """Public hashes must survive the unchanged payment-card redactor."""
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Expected a full SHA-256 digest')
    return ':'.join(digest[i:i + 8] for i in range(0, 64, 8))


def _read_audit_hash(value):
    value = str(value).replace(':', '')
    return value if re.fullmatch(r'[0-9a-f]{64}', value) else None


def has_user_editorial_constraints(store, job_id):
    """A machine assessment cannot supersede the latest explicit user criteria."""
    state = store.get(job_id)
    for row in reversed(_records(store.settings, job_id)):
        payload = _load_record(store, state, row)
        if payload.get('origin') == 'studio_user':
            return bool(payload.get('submitted_editorial_issues'))
    return False


def register_script_repair(store, job_id, action: StudioScriptRepair | dict, *, origin='studio_user'):
    if origin not in {'studio_user', 'autonomous_recovery'}:
        raise ValueError('Unknown script repair origin')
    action = StudioScriptRepair.model_validate(action)
    root = _allowed_root(store, store.get(job_id))
    with dispatch_lock(root / 'worker_logs' / 'dispatch.lock'):
        state = store.get(job_id)
        rows = _records(store.settings, job_id)
        if origin == 'autonomous_recovery' and has_user_editorial_constraints(store, job_id):
            raise OrderConflict('Autonomous repair cannot replace user editorial criteria; explicit user reassessment is required.')
        matching = next((row for row in rows if row['request_key'] == action.request_key), None)
        try:
            previous_scope = _load_record(store, state, rows[-1]).get('execution_scope', 'production') if rows else 'production'
        except ProductionBlocked as exc:
            raise OrderConflict('Dowody naprawy scenariusza są niepełne lub zmienione: ' + str(exc)) from exc
        if previous_scope == 'script_only' and action.execution_scope != 'script_only':
            raise OrderConflict('A registered script-only scope cannot be promoted by another repair request')
        if matching:
            previous = _load_record(store, state, matching)
            if (previous['note'] != redact_secrets(action.note)
                    or previous.get('submitted_editorial_issues', []) !=
                    [redact_secrets(issue.model_dump(mode='json')) for issue in action.editorial_issues]):
                raise OrderConflict('Ten klucz naprawy został już użyty z inną uwagą.')
            for key in ('execution_scope', 'operation_budget', 'acceptance_criteria',
                        'expected_canonical_sha256', 'expected_plan_sha256'):
                submitted = action.model_dump(mode='json')[key]
                default = 'production' if key == 'execution_scope' else [] if key == 'acceptance_criteria' else None
                if previous.get(key, default) != submitted:
                    raise OrderConflict('Repair key already has a different execution contract')
            # Retry a registration-to-dispatch crash window using the same
            # request, never a fresh authoring allowance. The normal dispatch
            # lock/worker identity still decides whether a process is needed.
            pending_dispatch = (rows[-1]['request_key'] == action.request_key and not state['worker_live']
                                and state['status'] == 'queued' and state['current_step'] == 'script_feedback')
            if pending_dispatch:
                _check_script_evidence(store, state, [previous])
                with connect(store.settings) as db:
                    started = db.execute("SELECT 1 FROM pipeline_job_events WHERE job_id=? AND event IN ('worker_launched','worker_acquired') AND id > COALESCE((SELECT MAX(id) FROM pipeline_job_events WHERE job_id=? AND event='studio_script_repair_requested' AND detail_json LIKE ?), 0) LIMIT 1",
                        (job_id, job_id, '%' + action.request_key + '%')).fetchone()
                pending_dispatch = not bool(started)
            return {'request': previous, 'already_registered': True, 'pending_dispatch': pending_dispatch}
        recovery = recovery_snapshot(store, job_id)
        if action.editorial_issues:
            # Explicit user feedback can follow an earlier PASS. Keep the
            # same receipt/archives/authoring/review path, never fake a failed
            # validator checkpoint to unlock it. Only typed hypothetical WB
            # orders with intact canonical scripts support this extension.
            if origin != 'studio_user':
                raise OrderConflict('Autonomous recovery cannot invent user editorial feedback')
            if (state['request'].get('intake') or {}).get('kind') != 'user_hypothetical':
                raise OrderConflict('Explicit editorial feedback requires a typed hypothetical order')
            canonical = next((row for row in state['checkpoints']
                              if row['stage'] == 'script' and row['status'] == 'done'), None)
            if not canonical or canonical['output'].get('qa', {}).get('status') != 'passed':
                raise OrderConflict('Editorial feedback requires a reviewed canonical script')
            ids = {segment['id'] for segment in canonical['output']['segments']}
            if any(not set(issue.segment_ids).issubset(ids) for issue in action.editorial_issues):
                raise ValueError('Editorial feedback refers to an unknown canonical segment')
            if action.execution_scope == 'script_only':
                if any(not set(item.segment_ids).issubset(ids) for item in action.acceptance_criteria):
                    raise ValueError('Semantic criterion refers to an unknown canonical segment')
                if (not (root / 'script.json').is_file() or not (root / 'scene_plan.json').is_file()
                        or file_hash(root / 'script.json') != action.expected_canonical_sha256
                        or file_hash(root / 'scene_plan.json') != action.expected_plan_sha256):
                    raise OrderConflict('Script-only repair base canonical or plan hash is stale')
            issues = [redact_secrets(issue.model_dump(mode='json')) for issue in action.editorial_issues]
            blocker = _scope_blocker(state, issues=issues)
            if recovery['kind'] == 'integrity':
                raise OrderConflict(recovery['repair_blocker'])
            recovery = {'can_repair_script':blocker is None, 'repair_blocker':blocker,
                        'message':'Registered user editorial feedback', 'issues':issues}
        if not recovery['can_repair_script']:
            raise OrderConflict(recovery['repair_blocker'] or recovery['message'])
        older = [_load_record(store, state, row) for row in rows]
        archive = _archive(store, state, older)
        from atnos.production.scoped_script_revision import ScriptFeedback, _round_limit, authorize_feedback_rounds, load_feedback, register_feedback
        scoped = load_feedback(store, job_id)
        canonical = next((row for row in state['checkpoints'] if row['stage'] == 'script' and row['status'] == 'done'), None)
        mode = 'normal_review'
        hypothetical_evidence = None
        if (state['request'].get('intake') or {}).get('kind') == 'user_hypothetical':
            mode = 'hypothetical_review'
            hypothetical_evidence = _hypothetical_repair_evidence(state,
                canonical_only=bool(action.editorial_issues))
        elif canonical:
            mode = 'scoped_followup'
            if not scoped:
                raise OrderConflict('Gotowy scenariusz wymaga zapisanej wcześniejszej oceny redakcyjnej.')
            maximum_rounds = _round_limit(store.settings, job_id)
            if maximum_rounds is not None and scoped['round'] >= maximum_rounds:
                authorize_feedback_rounds(store.settings, job_id, scoped['round'] + 1, approved=True,
                    reason=f'User explicitly clicked bounded Studio script repair {action.request_key}; three drafts maximum, budgets and counters unchanged.')
            feedback = ScriptFeedback(editor='Atnos Studio user',
                reason=f'Studio repair requested by the user ({action.request_key}). Correct the actual recorded defects. User note: {redact_secrets(action.note)[:850]}',
                issues=[{key: issue[key] for key in ('code', 'segment_ids', 'detail')} for issue in recovery['issues'][:12]],
                target_segments=(4, 5), max_drafts=MAX_ROUNDS)
            register_feedback(store.settings, job_id, feedback, approved=True, followup=True)
            scoped = load_feedback(store, job_id)
        payload = {'version': 1, 'job_id': job_id, 'request_key': action.request_key, 'origin': origin,
            'note': redact_secrets(action.note), 'issues': recovery['issues'], 'mode': mode,
            'original_request_hash': content_hash(state['request']), 'profile_hash': state['profile_hash'],
            'base_feedback_hash': scoped['feedback_hash'] if scoped else None,
            'base_feedback': scoped['feedback'] if scoped else None, 'max_rounds': MAX_ROUNDS,
            'archive': archive, 'created_at': utc_now_iso(), 'attempts_reset': False, 'budget_reset': False,
            'provider_calls_before': state['provider_calls'], 'reserved_cost_units_before': state['reserved_cost_units']}
        if hypothetical_evidence is not None:
            payload['hypothetical_evidence'] = hypothetical_evidence
        if action.editorial_issues:
            payload['submitted_editorial_issues'] = [redact_secrets(issue.model_dump(mode='json'))
                                                    for issue in action.editorial_issues]
            payload['issue_source'] = 'user_editorial_feedback_not_validator_verdict'
        if action.execution_scope == 'script_only':
            payload.update(version=SCRIPT_ONLY_REPAIR_VERSION,
                execution_scope=action.execution_scope,
                operation_budget=action.operation_budget.model_dump(mode='json'),
                acceptance_criteria=[item.model_dump(mode='json') for item in action.acceptance_criteria],
                expected_canonical_sha256=action.expected_canonical_sha256,
                expected_plan_sha256=action.expected_plan_sha256)
        digest = content_hash(payload)
        receipt = write_receipt(root / 'studio_script_recovery' / 'requests', payload)
        with connect(store.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            owner = db.execute('SELECT lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?', (job_id,)).fetchone()
            if owner and _same_process(owner['lease_pid'], owner['lease_process_started']):
                raise OrderConflict('Cannot register repair while a worker is active')
            db.execute(f'CREATE TABLE IF NOT EXISTS {TABLE}(job_id TEXT NOT NULL,request_key TEXT NOT NULL,request_digest TEXT NOT NULL,receipt_path TEXT NOT NULL,receipt_sha256 TEXT NOT NULL,writes_used INTEGER NOT NULL DEFAULT 0,reviews_used INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,PRIMARY KEY(job_id,request_key))')
            db.execute(f'INSERT INTO {TABLE}(job_id,request_key,request_digest,receipt_path,receipt_sha256,created_at) VALUES(?,?,?,?,?,?)',
                (job_id, action.request_key, digest, receipt.relative_to(root).as_posix(), file_hash(receipt), payload['created_at']))
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='script_feedback',error=NULL,updated_at=? WHERE id=?", (utc_now_iso(), job_id))
            _event(db, job_id, 'script_feedback', 'studio_script_repair_requested', {'request_digest': _audit_hash(digest) if action.execution_scope == 'script_only' else digest,
                'request_key': action.request_key, 'max_rounds': MAX_ROUNDS, 'attempts_reset': False, 'budget_reset': False,
                'execution_scope': action.execution_scope,
                'operation_budget': payload.get('operation_budget')})
            db.commit()
        return {'request': payload, 'already_registered': False}


def prepare_recovery_context(executor):
    payload = load_recovery_request(executor.store, executor.job_id)
    if payload:
        if payload.get('execution_scope') == 'script_only':
            _check_candidate_base(executor.context.artifact_dir, payload,
                                  load_script_candidate(executor.store, executor.job_id))
        if (payload.get('origin') == 'autonomous_recovery'
                and has_user_editorial_constraints(executor.store, executor.job_id)):
            # Existing orders may already contain a generic machine receipt
            # created by the old limit bypass. Its PASS cannot substitute for
            # review against the latest user criteria. Preserve it as evidence,
            # but block media production until an explicit reassessment.
            raise ProductionBlocked(USER_EDITORIAL_REASSESSMENT_MESSAGE)
        executor.context.artifacts['studio_script_recovery'] = {**payload, 'digest': content_hash(payload)}
        if payload.get('execution_scope') == 'script_only':
            # Evidence of executed, loaded functions, distinct from a hash of
            # source currently on disk. No inference and no budget reservation.
            import hashlib
            import marshal
            import psutil
            from atnos.production import order_execution
            from atnos.production.steps import order_script
            identity = {'request_digest': _audit_hash(content_hash(payload)), 'execution_scope': 'script_only',
                        'pid': os.getpid(), 'process_started': psutil.Process().create_time(),
                        'disk_code_identity': _audit_hash(executor.runtime_code_identity),
                        'registration_contract_version': SCRIPT_ONLY_REPAIR_VERSION,
                        'repair_prompt_version': order_script.SCRIPT_REPAIR_PROMPT_VERSION,
                        'validator_version': order_script.SCRIPT_VALIDATOR_VERSION,
                        'loaded_function_hashes': {name: _audit_hash(hashlib.sha256(marshal.dumps(fn.__code__)).hexdigest())
                            for name, fn in [('run_order', order_execution.run_order),
                                             ('produce_script', order_execution._produce_script),
                                             ('prepare_recovery_context', prepare_recovery_context),
                                             ('consume_operation', consume_operation)]}}
            with connect(executor.store.settings) as db:
                db.execute('BEGIN IMMEDIATE')
                executor.store._owner(db, executor.job_id, executor.token)
                if not any(event['event'] == 'studio_script_repair_runtime_loaded' and event['detail'] == identity
                           for event in executor.store.get(executor.job_id)['history']):
                    _event(db, executor.job_id, 'script_feedback', 'studio_script_repair_runtime_loaded', identity)
                db.commit()
    return payload


def apply_recovery_guidance(context):
    request = context.artifacts.get('studio_script_recovery')
    if request and request['mode'] == 'hypothetical_review':
        # Qualitative scoped guidance assumes no claims and a pay-raise story.
        # Keep this note in the normal writer/reviewer input instead.
        context.artifacts['hypothetical_editorial_guidance'] = {
            'note': request['note'], 'issues': request['issues'],
            'scope': 'Selective editorial changes only; original source, calculation assumptions and claim definitions are frozen.'}
        profile = copy.deepcopy(context.artifacts['project_profile'])
        profile['feedback_rules'] = [*profile.get('feedback_rules', []), {
            'source': 'studio_hypothetical_recovery', 'request_digest': request['digest'],
            'feedback': context.artifacts['hypothetical_editorial_guidance'], 'quality_thresholds': 'unchanged'}]
        context.artifacts['project_profile'] = profile
        return
    if not request or request['mode'] != 'normal_review':
        return
    base = copy.deepcopy(context.artifacts.get('scoped_script_guidance') or request.get('base_feedback') or {})
    base.setdefault('editor', 'Atnos Studio user')
    base['reason'] = (str(base.get('reason') or '') + ' Correct actual review defects. User note: ' + request['note'])[:1200]
    combined = [*base.get('issues', []), *[{key: item[key] for key in ('code', 'segment_ids', 'detail')} for item in request['issues']]]
    base['issues'] = list({content_hash(item): item for item in combined}.values())
    base['target_segments'] = base.get('target_segments', [4, 5])
    base['max_drafts'] = MAX_ROUNDS
    context.artifacts['scoped_script_guidance'] = base
    profile = copy.deepcopy(context.artifacts['project_profile'])
    profile['feedback_rules'] = [*profile.get('feedback_rules', []), {
        'source': 'studio_script_recovery', 'request_digest': request['digest'],
        'feedback': base, 'quality_thresholds': 'unchanged'}]
    context.artifacts['project_profile'] = profile
    # Copy only the prompt guidance. The frozen persisted profile and draft
    # inputs stay unchanged, so an intact old draft is a reviewable source.


def hypothetical_recovery_source(context):
    request = context.artifacts.get('studio_script_recovery')
    if not request or request['mode'] != 'hypothetical_review':
        return None
    evidence = request['hypothetical_evidence']
    if (content_hash(evidence['source_script']) != evidence['source_script_hash']
            or content_hash(evidence['calculator_receipt']) != evidence['calculator_receipt_hash']
            or context.artifacts.get('script_calculations') != evidence['calculator_receipt']):
        raise ProductionBlocked('Hypothetical Studio recovery source or calculator receipt changed')
    from atnos.production.hypothetical_claim_evidence import script_binding_issues
    issues = script_binding_issues(evidence['source_script'], context.artifacts['production_order']['intake'], evidence['calculator_receipt'])
    if issues != evidence.get('source_issues', []) or any(item['code'] != 'hypothetical_assumption_wording' for item in issues):
        raise ProductionBlocked('Hypothetical recovery source has an unsupported evidence defect')
    if not issues:
        validate_hypothetical_repair(context, evidence['source_script'])
    return copy.deepcopy(evidence['source_script'])


def validate_hypothetical_repair(context, script):
    request = context.artifacts.get('studio_script_recovery')
    if not request or request['mode'] != 'hypothetical_review':
        return
    from atnos.production.hypothetical_claim_evidence import verified_binding, bind_ledger_claims
    from atnos.integrations.claim_evidence_gate import extract_claims_from_script, evaluate_gate
    binding = verified_binding(script, context.artifacts['production_order']['intake'], context.artifacts['script_calculations'])
    verdict = evaluate_gate(bind_ledger_claims(extract_claims_from_script(script), script, binding))
    if not verdict['production_allowed']:
        raise ValueError('Hypothetical Studio repair must preserve the actual calculator assumptions, supported numbers and conditional wording')


def _candidate_constraints(request, script):
    """Edit permissions and semantic review targets are deliberately distinct."""
    from atnos.production.hypothetical_claim_evidence import editorial_script_payload
    source = editorial_script_payload(request['hypothetical_evidence']['source_script'])
    candidate = editorial_script_payload(script)
    editable = {sid for issue in request['submitted_editorial_issues'] for sid in issue['segment_ids']}
    if ({s['id'] for s in candidate['segments']} != {s['id'] for s in source['segments']}
            or [s['id'] for s in candidate['segments']] != [s['id'] for s in source['segments']]):
        raise ProductionBlocked('Candidate changed the frozen segment structure')
    if any(candidate.get(key) != source.get(key) for key in source if key != 'segments'):
        raise ProductionBlocked('Candidate changed frozen title, description or financial claims')
    for old, new in zip(source['segments'], candidate['segments']):
        if old['id'] not in editable and old != new:
            raise ProductionBlocked('Candidate changed a locked scene')
        if any(old.get(key) != new.get(key) for key in ('id', 'type', 'claim_ids', 'fact_ids')):
            raise ProductionBlocked('Candidate changed frozen scene roles or claim bindings')
    return candidate


def _check_candidate_base(root, request, candidate=None):
    if not (root / 'scene_plan.json').is_file() or file_hash(root / 'scene_plan.json') != request['expected_plan_sha256']:
        raise ProductionBlocked('Controlled repair plan changed since registration')
    path = root / 'script.json'
    if path.is_file() and file_hash(path) == request['expected_canonical_sha256']:
        return
    # A normal finalized, assessed candidate can be read on an idempotent resume.
    if candidate is not None and path.is_file():
        from atnos.production.hypothetical_claim_evidence import editorial_script_payload
        actual = json.loads(path.read_text('utf-8'))
        if (content_hash(editorial_script_payload(actual)) == candidate['candidate_hash']
                and actual.get('provenance', {}).get('studio_recovery', {}).get('request_digest') == content_hash(request)):
            return
    raise ProductionBlocked('Controlled repair canonical changed since registration')


def _load_script_candidate_contents(store, state, request):
    """Validate immutable candidate evidence, without deciding base admissibility.

    Pre-finalization callers must additionally use _check_candidate_base. Only
    the finalized-canonical verifier may inspect this content after a plan change.
    """
    if not request or request.get('execution_scope') != 'script_only':
        return None
    job_id = state['id']
    digest = content_hash(request)
    stage = 'script_candidate/' + digest[:12]
    row = next((s for s in state['checkpoints'] if s['stage'] == stage and s['status'] == 'done'), None)
    if row is None:
        return None
    root = _allowed_root(store, state)
    payload = row['output']
    if (not row['files'] or not store._valid_files(root, row['files_json'])
            or not any(json.loads((root / entry['path']).read_text('utf-8')) == payload for entry in row['files'])
            or payload.get('job_id') != job_id or payload.get('request_digest') != digest
            or payload.get('candidate_hash') != content_hash(payload.get('script'))
            or payload.get('acceptance_criteria') != request['acceptance_criteria']):
        raise ProductionBlocked('Current registered candidate evidence is missing or changed')
    if _candidate_constraints(request, payload['script']) != payload['script']:
        raise ProductionBlocked('Candidate contains non-editorial fields')
    return payload


def load_script_candidate(store, job_id):
    state = store.get(job_id)
    request = load_recovery_request(store, job_id)
    payload = _load_script_candidate_contents(store, state, request)
    if payload is not None:
        _check_candidate_base(_allowed_root(store, state), request, payload)
    return payload


def verify_finalized_script_acceptance(store, job_id, script=None):
    """Read-only proof of an already finalized, independently accepted canonical.

    A successor scene plan does not revoke an intact script acceptance. This
    verifier therefore checks the finalized checkpoint and its QA/provenance
    bindings before reading candidate content without the pre-finalization base
    check. It never authorizes finalization, a plan, media generation or dispatch.
    The optional input must be the full current canonical envelope.
    """
    try:
        state = store.get(job_id)
        root = _allowed_root(store, state)
        request = load_recovery_request(store, job_id)
        if (not request or request.get('execution_scope') != 'script_only'
                or request.get('mode') != 'hypothetical_review'
                or request.get('origin') != 'studio_user'):
            raise ProductionBlocked('Current controlled script registration is required for finalized acceptance')
        digest = content_hash(request)
        row = next((item for item in state['checkpoints'] if item['stage'] == 'script'), None)
        if (not row or row['status'] != 'done' or not store._valid_files(root, row['files_json'])
                or not any((root / entry['path']).resolve() == root / 'script.json' for entry in row['files'])):
            raise ProductionBlocked('Finalized acceptance requires the intact normal canonical checkpoint and file')
        actual = json.loads((root / 'script.json').read_text('utf-8'))
        if not isinstance(actual, dict) or actual != row['output'] or script is not None and script != actual:
            raise ProductionBlocked('Finalized acceptance input or file differs from the current canonical checkpoint')
        qa = actual.get('qa') or {}
        provenance = actual.get('provenance') or {}
        if not isinstance(qa, dict) or not isinstance(provenance, dict):
            raise ProductionBlocked('Canonical QA and provenance must be normal finalized objects')
        registered = provenance.get('studio_recovery') or {}
        if (not isinstance(registered, dict) or qa.get('status') != 'passed' or qa.get('issues') != []
                or qa.get('independent_semantic_review') != 'operator_assessed'
                or qa.get('studio_recovery_digest') != digest
                or registered.get('request_digest') != digest
                or registered.get('request_key') != request['request_key']):
            raise ProductionBlocked('Canonical lacks current independent QA and registered provenance binding')
        # The canonical gate above is deliberately not a replacement for either
        # load_script_candidate's base check or require_candidate_acceptance.
        candidate = _load_script_candidate_contents(store, state, request)
        if candidate is None:
            raise ProductionBlocked('Finalized canonical has no current registered candidate evidence')
        current = _candidate_constraints(request, actual)
        candidate_hash = candidate['candidate_hash']
        if (content_hash(current) != candidate_hash or current != candidate['script']
                or qa.get('script_hash') != candidate_hash):
            raise ProductionBlocked('Canonical is not the current constrained candidate')
        review = candidate.get('review') or {}
        identities = candidate.get('model_identities') or {}
        if (review.get('status') != 'passed' or review.get('issues') != []
                or review.get('script_hash') != candidate_hash
                or review.get('studio_recovery_digest') != digest
                or any(qa.get(key) != value for key, value in review.items())
                or any(not identities.get(key) or provenance.get(field) != identities[key]
                       for key, field in [('writer', 'generator'), ('reviewer', 'reviewer')])):
            raise ProductionBlocked('Canonical QA or model provenance differs from the current candidate review')
        receipt = load_candidate_assessment(store, job_id, candidate)
        if not receipt or receipt['assessment']['decision'] != 'accept':
            raise ProductionBlocked('Finalized canonical requires the registered independent acceptance receipt')
        binding = {'assessment_hash': receipt['assessment_hash'], 'receipt_sha256': receipt['receipt_sha256'],
                   'candidate_hash': candidate_hash, 'request_digest': digest}
        if qa.get('independent_assessment') != binding:
            raise ProductionBlocked('Canonical independent assessment binding is stale or incomplete')
        if not any(event['stage'] == 'script' and event['event'] == 'stage_completed' for event in state['history']):
            raise ProductionBlocked('Canonical has no normal finalization event')
        canonical_sha256 = file_hash(root / 'script.json')
        if not any((root / entry['path']).resolve() == root / 'script.json'
                   and entry['sha256'] == canonical_sha256 for entry in row['files']):
            raise ProductionBlocked('Canonical file changed during acceptance verification')
        latest = store.get(job_id)
        latest_row = next((item for item in latest['checkpoints'] if item['stage'] == 'script'), None)
        rows = _records(store.settings, job_id)
        if latest_row != row or not rows or rows[-1]['request_digest'] != digest:
            raise ProductionBlocked('Current script registration or canonical changed during acceptance verification')
        return {'version': 1, 'job_id': job_id, 'request_digest': digest, 'request_key': request['request_key'],
                'canonical_sha256': canonical_sha256, 'candidate_hash': candidate_hash,
                'canonical_checkpoint_input_hash': row['input_hash'],
                'candidate_stage': 'script_candidate/' + digest[:12],
                'assessment_hash': receipt['assessment_hash'],
                'assessment_receipt_sha256': receipt['receipt_sha256']}
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise ProductionBlocked('Finalized script acceptance evidence is invalid or incomplete') from exc


def _validate_independent_assessment(candidate, value):
    assessment = StudioCandidateAssessment.model_validate(value)
    if (assessment.request_digest != candidate['request_digest']
            or assessment.candidate_hash != candidate['candidate_hash']):
        raise OrderConflict('Independent assessment has a stale request digest or candidate hash')
    if assessment.reviewer.casefold() in {str(v).casefold() for v in candidate['model_identities'].values()}:
        raise OrderConflict('The writing/review model cannot approve its own candidate')
    expected = {(criterion['id'], sid, field) for criterion in candidate['acceptance_criteria']
                for sid in criterion['segment_ids'] for field in criterion.get('fields', ['text'])}
    actual = [(item.criterion_id, item.segment_id, item.field) for item in assessment.checks]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('Independent assessment must evaluate every criterion and scene exactly once')
    segments = {s['id']: s for s in candidate['script']['segments']}
    for check in assessment.checks:
        if check.quote not in str(segments[check.segment_id].get(check.field) or ''):
            raise ValueError('Independent assessment quote is absent from the current candidate field')
    if (assessment.decision == 'accept') != all(check.satisfied for check in assessment.checks):
        raise ValueError('False PASS: decision disagrees with the independent criterion results')
    return assessment.model_dump(mode='json')


def load_candidate_assessment(store, job_id, candidate=None):
    candidate = candidate or load_script_candidate(store, job_id)
    if candidate is None:
        return None
    state = store.get(job_id)
    root = _allowed_root(store, state)
    for event in reversed(state['history']):
        detail = event['detail']
        if (event['event'] != 'script_candidate_assessed'
                or _read_audit_hash(detail.get('request_digest')) != candidate['request_digest']
                or _read_audit_hash(detail.get('candidate_hash')) != candidate['candidate_hash']):
            continue
        path = (root / detail['receipt_path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != _read_audit_hash(detail['receipt_sha256']):
            raise ProductionBlocked('Independent assessment receipt is missing or changed')
        payload = json.loads(path.read_text('utf-8'))
        if (payload.get('job_id') != job_id or payload.get('candidate_stage') != 'script_candidate/' + candidate['request_digest'][:12]
                or payload.get('assessment_hash') != content_hash(payload.get('assessment'))):
            raise ProductionBlocked('Independent assessment audit binding changed')
        assessment = _validate_independent_assessment(candidate, payload['assessment'])
        return {**payload, 'receipt_sha256': _read_audit_hash(detail['receipt_sha256'])}
    return None  # A loose JSON file, offline fixture or model PASS is never a receipt.


def register_candidate_assessment(store, job_id, assessment):
    root = _allowed_root(store, store.get(job_id))
    with dispatch_lock(root / 'worker_logs' / 'dispatch.lock'):
        state = store.get(job_id)
        candidate = load_script_candidate(store, job_id)
        if candidate is None:
            raise OrderConflict('There is no registered current candidate to assess')
        value = _validate_independent_assessment(candidate, assessment)
        previous = load_candidate_assessment(store, job_id, candidate)
        if previous:
            if previous['assessment'] != value:
                raise OrderConflict('Immutable independent assessment already exists for this candidate')
            return {**previous, 'already_registered': True, 'worker_requested': False}
        if state['worker_live'] or state['status'] != 'blocked':
            raise OrderConflict('Independent assessment requires an idle, held candidate')
        payload = {'version': 1, 'job_id': job_id, 'candidate_stage': 'script_candidate/' + candidate['request_digest'][:12],
                   'assessment': value, 'assessment_hash': content_hash(value), 'created_at': utc_now_iso()}
        path = write_receipt(root / 'studio_script_recovery' / 'assessments', payload, short_name=True)
        with connect(store.settings) as db:
            db.execute('BEGIN IMMEDIATE')
            owner = db.execute('SELECT lease_pid,lease_process_started FROM pipeline_job_orders WHERE job_id=?', (job_id,)).fetchone()
            if owner and _same_process(owner['lease_pid'], owner['lease_process_started']):
                raise OrderConflict('A worker acquired the candidate before assessment registration')
            _event(db, job_id, payload['candidate_stage'], 'script_candidate_assessed', {
                'request_digest': _audit_hash(value['request_digest']), 'candidate_hash': _audit_hash(value['candidate_hash']),
                'receipt_path': path.relative_to(root).as_posix(), 'receipt_sha256': _audit_hash(file_hash(path)),
                'decision': value['decision']})
            if value['decision'] == 'accept':
                db.execute("UPDATE pipeline_jobs SET status='queued',current_step='script_feedback',error=NULL,updated_at=? WHERE id=?",
                           (utc_now_iso(), job_id))
            else:
                db.execute("UPDATE pipeline_jobs SET error=?,updated_at=? WHERE id=?", (json_dumps({
                    'category': 'independent_semantic_review_rejected', 'message': 'Independent operator rejected the current candidate'}), utc_now_iso(), job_id))
            db.commit()
        return {**payload, 'receipt_sha256': file_hash(path), 'already_registered': False, 'worker_requested': False}


def require_candidate_acceptance(store, job_id, *, script=None):
    if repair_execution_scope(store, job_id) != 'script_only':
        return None
    candidate = load_script_candidate(store, job_id)
    assessment = load_candidate_assessment(store, job_id, candidate) if candidate else None
    if not assessment or assessment['assessment']['decision'] != 'accept':
        raise ProductionBlocked('Independent semantic assessment of the current candidate is required before canonical')
    if script is None or content_hash(_candidate_constraints(load_recovery_request(store, job_id), script)) != candidate['candidate_hash']:
        raise ProductionBlocked('Finalize input is not the independently assessed candidate')
    return assessment


async def hold_script_candidate(executor, script, verdict, model_identities):
    request = executor.context.artifacts.get('studio_script_recovery')
    if not request or request.get('execution_scope') != 'script_only':
        return verdict
    root = executor.context.artifact_dir
    candidate_script = _candidate_constraints(request, script)
    candidate_hash = content_hash(candidate_script)
    if (verdict.get('status') != 'passed' or verdict.get('issues')
            or verdict.get('script_hash') != candidate_hash
            or verdict.get('studio_recovery_digest') != request['digest']):
        raise ProductionBlocked('Candidate lacks current normal review against its real hash and request digest')
    from atnos.production.steps.common import write_json
    import asyncio
    async def save():
        _check_candidate_base(root, load_recovery_request(executor.store, executor.job_id))
        payload = {'version': 1, 'job_id': executor.job_id, 'request_digest': request['digest'],
                   'candidate_hash': candidate_hash, 'script': candidate_script, 'review': verdict,
                   'acceptance_criteria': request['acceptance_criteria'], 'model_identities': model_identities}
        # Short physical names stay below Windows MAX_PATH. Full hashes are
        # retained and verified in the candidate and checkpoint contracts.
        path = root / 'studio_script_recovery' / 'candidates' / (request['digest'][:12] + '-' + candidate_hash[:12] + '.json')
        if path.exists() and json.loads(path.read_text('utf-8')) != payload:
            raise OrderConflict('Immutable candidate path already contains different evidence')
        return write_json(path, payload)
    await executor.run('script_candidate/' + request['digest'][:12],
        {'request_digest': request['digest'], 'script': candidate_script, 'review': verdict}, save)
    candidate = load_script_candidate(executor.store, executor.job_id)
    assessment = load_candidate_assessment(executor.store, executor.job_id, candidate)
    if not assessment or assessment['assessment']['decision'] != 'accept':
        category = 'independent_semantic_review_rejected' if assessment else 'independent_semantic_review_required'
        await asyncio.to_thread(executor.store.block, executor.job_id, executor.token,
            'script_candidate/' + request['digest'][:12], category=category,
            message='Candidate retained; no canonical update or media allowed before independent assessment')
        raise ProductionBlocked(category)
    return {**verdict, 'independent_semantic_review': 'operator_assessed',
            'human_review': verdict.get('human_review', 'not_performed'),
            'independent_assessment': {'assessment_hash': assessment['assessment_hash'],
                                     'receipt_sha256': assessment['receipt_sha256'],
                                     'candidate_hash': candidate_hash, 'request_digest': request['digest']}}


def consume_operation(executor, stage, inputs, *, provider_call=False, external_tool_call=False, estimated_cost_usd=0):
    request = executor.context.artifacts.get('studio_script_recovery')
    if not request:
        return
    column = ('writes_used' if stage == 'script_draft' or stage.startswith('script_repair/') or re.search(r'^script_scoped/.+/draft/\d+$', stage)
              else 'reviews_used' if stage.startswith('script_review/') or re.search(r'^script_scoped/.+/review/\d+$', stage) else None)
    if column is None:
        return
    from atnos.production.order_store import local_operation_unlimited
    unlimited = local_operation_unlimited(executor.store.settings, provider_call=provider_call,
        external_tool_call=external_tool_call, estimated_cost_usd=estimated_cost_usd)
    with connect(executor.store.settings) as db:
        db.execute('BEGIN IMMEDIATE')
        executor.store._owner(db, executor.job_id, executor.token)
        checkpoint = db.execute('SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?', (executor.job_id, stage)).fetchone()
        if (checkpoint and checkpoint['status'] == 'done' and checkpoint['input_hash'] == content_hash(inputs)
                and executor.store._valid_files(executor.context.artifact_dir, checkpoint['files_json'])):
            return  # A verified cache hit is not another actual writing/review operation.
        # A stage admission refusal is not a writing/review operation. Let the
        # normal begin() persist its existing attempt_limit verdict, without
        # spending another Studio slot before any model request can start.
        from atnos.production.order_store import _approved_attempt_limit
        maximum = _approved_attempt_limit(db, executor.job_id, stage,
            executor.context.artifacts['project_profile']['production']['max_attempts_per_element'])
        if not unlimited and checkpoint and checkpoint['attempts'] >= maximum:
            return
        row = db.execute(f'SELECT {column} FROM {TABLE} WHERE job_id=? AND request_key=?', (executor.job_id, request['request_key'])).fetchone()
        budget = request.get('operation_budget')
        maximum = budget['writes' if column == 'writes_used' else 'reviews'] if budget else None if unlimited else request['max_rounds']
        if row is None or (maximum is not None and row[column] >= maximum):
            message = ('Studio script repair exhausted its registered operation budget; counters and rejected output retained' if budget
                       else 'Studio script repair reached its three-operation limit; retain the actual rejected draft for a new explicit assessment')
            raise ProductionBlocked(message)
        db.execute(f'UPDATE {TABLE} SET {column}={column}+1 WHERE job_id=? AND request_key=?', (executor.job_id, request['request_key']))
        _event(db, executor.job_id, stage, 'studio_script_repair_operation', {'request_digest': _audit_hash(request['digest']) if budget else request['digest'],
            'kind': column, 'operation': row[column] + 1, 'maximum': maximum,
            'includes_stage_retries': True})
        db.commit()
