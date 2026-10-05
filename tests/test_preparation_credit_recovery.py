"""An explicit funded retry preserves a reconciled credit refusal and paid work."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import sqlite3

import pytest

from test_preparation_ai import _proposer


def reconciled(tmp_path, proposer=None):
    from bellomberg.core.llm_client import APIStatusError
    from bellomberg.valuation.preparation_ai import _json
    p = proposer or _proposer(tmp_path)
    success = p.call
    metadata = {'limit_source': 'openrouter_credits', 'provider_name': None}
    reason = 'APIStatusError: HTTP 402: This request requires more credits, or fewer max_tokens. | metadata: ' + json.dumps(metadata)
    p.call = lambda **_: (_ for _ in ()).throw(APIStatusError(402, 'Insufficient credits',
        {'error': {'code': 402, 'metadata': metadata}}))
    with pytest.raises(APIStatusError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    with p._db() as db:
        row = db.execute('SELECT * FROM requests').fetchone()
        receipt = {**json.loads(row['receipt']), 'stop_reason': 'request_rejected', 'cost_usd': 0,
            'billing_reconciliation': {
                'status': 'reconciled_nonbillable_credit_admission_rejection',
                'observed_error': reason, 'api_usage_receipt_available': False,
                'generation_id_available': False, 'funding_required': True,
                'automatic_retry_authorized': False,
                'request_auxiliary_services': 'none: text-only single request without search, tools, media or file parsing',
                'provider_billing_policy': 'https://openrouter.ai/docs/guides/features/zero-completion-insurance'}}
        text = _json(receipt)
        db.execute("UPDATE requests SET state='rejected',cost=0,receipt=?", (text,))
    p.call = success
    return p, row['key'], sha256(text.encode()).hexdigest(), text


def funding():
    return {'utc': datetime.now(timezone.utc).isoformat(), 'http_status': 200,
        'endpoint': 'GET /api/v1/credits', 'total_credits': 10, 'total_usage': 0,
        'available_usd': '10', 'inference_calls': 0}


def test_manual_reconciliation_alone_does_not_authorize_retry(tmp_path):
    p, _, _, _ = reconciled(tmp_path)
    p.call = lambda **_: pytest.fail('unapproved retry')
    with pytest.raises(ValueError, match='incomplete'):
        p({'ticker': 'SYNTH'}, {'schema': {}})


def test_explicit_funded_retry_archives_refusal_and_reuses_result(tmp_path):
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_rejections import verify_history
    p, key, digest, original = reconciled(tmp_path)
    before = p.summary()
    result = authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-operator-funding-confirmation')
    # ZR 05/10 (RV-COST P3): contabilita' identica; cambia SOLO l'etichetta, che per la riga
    # autorizzata dice il vero (nuovo tentativo finanziato autorizzato, non «mai ritentabile»).
    from bellomberg.valuation.preparation_ai import MANUAL_RECONCILIATION_V2_LABEL
    after = p.summary()
    assert result['request_key'] == key
    assert after['manual_reconciliation_status'] == MANUAL_RECONCILIATION_V2_LABEL
    assert ({k: v for k, v in after.items() if k != 'manual_reconciliation_status'}
            == {k: v for k, v in before.items() if k != 'manual_reconciliation_status'})
    with p._db() as db:
        history = verify_history(db, key)
        assert len(history) == 1
        proof = json.loads(history[0][2])['pre_provider_rejection']
        assert proof['reconciled_receipt_text'] == original
        assert proof['reconciled_receipt_sha256'] == digest
    calls = []
    success = p.call
    p.call = lambda **kw: (calls.append(deepcopy(kw)), success(**kw))[1]
    answer = p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p({'ticker': 'SYNTH'}, {'schema': {}}) == answer
    assert len(calls) == 1 and p.summary()['spent_usd'] == .2
    with p._db() as db:
        assert len(verify_history(db, key)) == 1
        assert db.execute('SELECT key FROM requests').fetchone()[0] == key


@pytest.mark.parametrize('fault', ['insufficient_balance', 'stale_balance', 'bad_hash', 'unknown_cost', 'response_present', 'provider_error'])
def test_credit_retry_rejects_unverified_or_unfunded_state_without_writing(tmp_path, fault):
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_ai import _json
    p, key, digest, _ = reconciled(tmp_path)
    evidence = funding()
    if fault == 'insufficient_balance':
        evidence.update(total_credits=.01, available_usd='.01')
    elif fault == 'stale_balance':
        evidence['utc'] = '2020-01-01T00:00:00+00:00'
    elif fault == 'bad_hash':
        digest = '0' * 64
    else:
        with p._db() as db:
            if fault == 'unknown_cost':
                db.execute("UPDATE requests SET state='unknown',cost=NULL")
            elif fault == 'response_present':
                db.execute("UPDATE requests SET response='partial output'")
            else:
                text = json.loads(db.execute('SELECT receipt FROM requests').fetchone()[0])
                text['billing_reconciliation']['observed_error'] = 'upstream provider error'
                serialized = _json(text)
                db.execute('UPDATE requests SET receipt=?', (serialized,))
                digest = sha256(serialized.encode()).hexdigest()
    with p._db() as db:
        before = list(db.iterdump())
    with pytest.raises((ValueError, RuntimeError)):
        authorize_credit_retry(p, key, expected_receipt_sha256=digest,
            funding_receipt=evidence, operator_reference='synthetic-operator-funding-confirmation')
    with p._db() as db:
        assert list(db.iterdump()) == before


def test_credit_recovery_preserves_prior_inflight_attempt_and_blocks_ambiguous_retry(tmp_path, monkeypatch):
    import time
    from test_preparation_rejections import rejection
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    from bellomberg.valuation.preparation_rejections import verify_history
    now = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    p = _proposer(tmp_path)
    success = p.call
    p.call = lambda **_: (_ for _ in ()).throw(rejection())
    with pytest.raises(OSError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    now[0] += 121
    with p._db() as db:
        original_history = [tuple(row) for row in db.execute('SELECT * FROM request_rejections')]
    p.call = success
    p, key, digest, _ = reconciled(tmp_path, p)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    with p._db() as db:
        assert len(verify_history(db, key)) == 2
        assert tuple(db.execute('SELECT * FROM request_rejections ORDER BY attempt').fetchone()) == original_history[0]
    p.call = lambda **_: (_ for _ in ()).throw(TimeoutError('uncertain delivery'))
    with pytest.raises(TimeoutError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['unknown_requests'] == 1
    with pytest.raises(RuntimeError, match='unresolved'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    with p._db() as db:
        assert len(verify_history(db, key)) == 2


@pytest.mark.parametrize('tamper', ['balance', 'reserved'])
def test_runtime_audit_checks_credit_history_before_and_after_paid_reply(tmp_path, tamper):
    from test_preparation_runtime import _policy, _runtime, _write
    from test_preparation_ai import _metadata
    from bellomberg.valuation.preparation_ai import BudgetedProposer
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    _write(tmp_path / 'policy.json', _policy())
    runtime = _runtime(tmp_path, None)
    path = runtime.data_root / 'valuation_ai_budgets' / (_policy()['authorization_id'] + '.sqlite3')
    p = BudgetedProposer(path, authorized_usd='2.50', model='synthetic/model', max_tokens=16000,
        thinking={}, metadata=lambda _: _metadata(), call=_proposer(tmp_path).call)
    p, key, digest, _ = reconciled(tmp_path, p)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    assert runtime.budget_audit()['state'] == 'reconciled'
    p({'ticker': 'SYNTH'}, {'schema': {}})
    assert runtime.budget_audit()['state'] == 'reconciled'
    with p._db() as db:
        proof = json.loads(db.execute('SELECT receipt FROM request_rejections').fetchone()[0])
        if tamper == 'balance':
            proof['pre_provider_rejection']['funding_receipt']['available_usd'] = '999'
        else:
            proof['pre_provider_rejection']['reserved_nano_usd'] = 1
        db.execute('UPDATE request_rejections SET receipt=?', (json.dumps(proof),))
    with pytest.raises(ValueError, match='credit'):
        runtime.budget_audit()


def test_credit_grant_does_not_skip_live_price_cap_or_concurrent_reservation(tmp_path):
    from test_preparation_ai import _metadata
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    p, key, digest, _ = reconciled(tmp_path)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest,
        funding_receipt=funding(), operator_reference='synthetic-funded-recovery')
    metadata = _metadata(); metadata['pricing']['prompt'] = '.01'
    p.metadata = lambda _: metadata
    with pytest.raises(RuntimeError, match='budget insufficient'):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    p.metadata = lambda _: _metadata()
    success = p.call
    def call(**request):
        with pytest.raises(RuntimeError, match='unresolved'):
            _proposer(tmp_path, call=lambda **_: pytest.fail('concurrent duplicate'))(
                {'ticker': 'SYNTH'}, {'schema': {}})
        return success(**request)
    p.call = call
    p({'ticker': 'SYNTH'}, {'schema': {}})
    assert p.summary()['spent_usd'] == .2
    with pytest.raises(ValueError):
        authorize_credit_retry(p, key, expected_receipt_sha256=digest,
            funding_receipt=funding(), operator_reference='repeated-old-authorization')


# --- ZR 05/10 (Z5): riconciliazione manuale STORICA (rifiuto 'rejected' senza
# pre_provider_rejection). Dal commit 1326312 _validate_receipt la trattava come una prova
# v1 malformata: summary() del preparatore (Trade Idea) e il registro condiviso della run
# (Consigliere, _external_rows) sollevavano «invalid pre-provider rejection receipt».
# Paletti di main: forma esatta soltanto, mai ritentabile, costo 0 DICHIARATO e contato.

def test_historical_reconciliation_is_declared_zero_and_counted(tmp_path):
    from bellomberg.valuation.preparation_ai import MANUAL_RECONCILIATION_LABEL
    p, key, _, _ = reconciled(tmp_path)
    summary = p.summary()
    assert summary['requests'] == 1 and summary['unknown_requests'] == 0
    assert summary['spent_usd'] == 0 and summary['known_cost_usd'] == 0
    assert summary['manually_reconciled_nonbillable'] == [key]
    assert summary['manual_reconciliation_status'] == MANUAL_RECONCILIATION_LABEL


def _alter(p, fault):
    from bellomberg.valuation.preparation_ai import _json
    with p._db() as db:
        row = db.execute('SELECT * FROM requests').fetchone()
        receipt = json.loads(row['receipt'])
        judgment = receipt['billing_reconciliation']
        if fault == 'response':
            db.execute("UPDATE requests SET response='unverified answer'")
            return
        if fault == 'cost_null':
            db.execute('UPDATE requests SET cost=NULL')
            return
        if fault == 'response_id':
            receipt['response_id'] = 'gen-zz-synthetic'
        elif fault == 'generation_id':
            receipt['generation_id'] = 'gen-zz-synthetic'
        elif fault == 'stop_reason':
            receipt['stop_reason'] = 'max_tokens'
        elif fault == 'status':
            judgment['status'] = 'billed_after_review'
        elif fault == 'no_judgment':
            del receipt['billing_reconciliation']
        elif fault == 'generation_available':
            judgment['generation_id_available'] = True
        elif fault == 'usage_available':
            judgment['api_usage_receipt_available'] = True
        elif fault == 'cost':
            receipt['cost_usd'] = 0.123
            db.execute('UPDATE requests SET cost=?', (123000000,))
        db.execute('UPDATE requests SET receipt=?', (_json(receipt),))


@pytest.mark.parametrize('fault', ['response', 'response_id', 'generation_id', 'stop_reason', 'status',
                                   'no_judgment', 'generation_available', 'usage_available', 'cost'])
def test_any_other_rejected_form_without_proof_stays_an_error(tmp_path, fault):
    p, _, _, _ = reconciled(tmp_path)
    _alter(p, fault)
    with pytest.raises(ValueError, match='invalid pre-provider rejection receipt'):
        p.summary()


def _linked_journal(tmp_path, p, key):
    from bellomberg.core.request_journal import RequestJournal
    journal = RequestJournal(tmp_path / 'weekly-zz-requests.sqlite', run_id='zz-run',
        authorization={'source': 'offline-test'}, authorized_usd='10',
        metadata=lambda model: {'id': model, 'context_length': 200000, 'max_completion_tokens': 128000,
                                'pricing': {'prompt': '0.000001', 'completion': '0.000002'}})
    journal.link_external(p.path, key, owned=True)
    return journal


def test_shared_run_journal_counts_historical_reconciliation_as_declared_zero(tmp_path):
    from bellomberg.storage.weekly_run_store import costs_unresolved
    from bellomberg.valuation.preparation_ai import MANUAL_RECONCILIATION_LABEL
    p, key, _, _ = reconciled(tmp_path)
    summary = _linked_journal(tmp_path, p, key).summary()
    [row] = summary['external_requests']
    assert row['state'] == 'rejected' and row['cost'] == 0
    assert row['cost_status'] == MANUAL_RECONCILIATION_LABEL
    assert summary['request_count'] == 1 and summary['cost_usd'] == 0
    assert costs_unresolved(summary) is False


@pytest.mark.parametrize('fault', ['response_id', 'status', 'cost', 'cost_null'])
def test_shared_run_journal_rejects_other_forms(tmp_path, fault):
    # cost_null: il preparatore non certifica nulla per una riga senza costo (_validate_receipt
    # esce subito), quindi la forma la controlla il registro stesso.
    p, key, _, _ = reconciled(tmp_path)
    journal = _linked_journal(tmp_path, p, key)
    _alter(p, fault)
    with pytest.raises(ValueError, match='invalid pre-provider rejection receipt'):
        journal.summary()


def test_historical_reconciliation_never_authorizes_a_retry_even_after_restart(tmp_path):
    p, _, _, _ = reconciled(tmp_path)
    restarted = _proposer(tmp_path, call=lambda **_: pytest.fail('historical reconciliation retried'))
    with pytest.raises(ValueError, match='incomplete AI response: request_rejected'):
        restarted({'ticker': 'SYNTH'}, {'schema': {}})
    assert restarted.summary() == p.summary()


# RV-COST (revisore costi, 05/10): P2-a forma esatta = le SOLE 7 chiavi della ricevuta storica;
# P2-b nessuna etichetta «costo 0 attestato» su un costo ignoto; P3 una regola sola fra
# preparatore e registro (dopo l'autorizzazione v2 nessuno dei due la dichiara piu').
BILLING_EVIDENCE = {
    'usage': {'usage': {'input_tokens': 1000, 'output_tokens': 5000}},
    'response_sha256': {'response_sha256': '0' * 64},
    'provider_model': {'provider': 'synthetic', 'model': 'synthetic/model'},
    'identity_verified': {'identity_verified': True},
    'settlement': {'settlement': {'generation_id': 'gen-zz', 'cost_nano': 0}},
    'provider_generation': {'provider_generation': {'id': 'gen-zz', 'total_cost': 0.5}},
    'id': {'id': 'gen-zz-synthetic'},
}


@pytest.mark.parametrize('name', sorted(BILLING_EVIDENCE))
def test_receipt_with_billing_evidence_is_not_the_historical_form(tmp_path, name):
    from bellomberg.valuation.preparation_ai import _json
    p, key, _, _ = reconciled(tmp_path)
    journal = _linked_journal(tmp_path, p, key)
    with p._db() as db:
        receipt = json.loads(db.execute('SELECT receipt FROM requests').fetchone()[0])
        db.execute('UPDATE requests SET receipt=?', (_json({**receipt, **BILLING_EVIDENCE[name]}),))
    for summary in (p.summary, journal.summary):
        with pytest.raises(ValueError, match='invalid pre-provider rejection receipt'):
            summary()


def test_unknown_cost_is_never_declared_as_attested_zero(tmp_path):
    p, key, _, _ = reconciled(tmp_path)
    with p._db() as db:
        db.execute('UPDATE requests SET cost=NULL')
    summary = p.summary()
    assert summary['unknown_requests'] == 1 and summary['spent_usd'] is None
    assert 'manually_reconciled_nonbillable' not in summary
    assert 'manual_reconciliation_status' not in summary


def test_authorized_v2_row_declaration_is_consistent_between_preparer_and_journal(tmp_path):
    from bellomberg.valuation.preparation_credit_recovery import authorize_credit_retry
    p, key, digest, _ = reconciled(tmp_path)
    journal = _linked_journal(tmp_path, p, key)
    authorize_credit_retry(p, key, expected_receipt_sha256=digest, funding_receipt=funding(),
                           operator_reference='synthetic-operator-funding-confirmation')
    summary = p.summary()
    [row] = journal.summary()['external_requests']
    # Regola unica: la v2 incorpora la ricevuta manuale, entrambi continuano a dichiararla, con
    # l'etichetta v2 (autorizzata a un nuovo tentativo finanziato: niente «mai ritentabile»).
    from bellomberg.valuation.preparation_ai import MANUAL_RECONCILIATION_V2_LABEL
    assert summary['manually_reconciled_nonbillable'] == [key]
    assert row['cost_status'] == summary['manual_reconciliation_status'] == MANUAL_RECONCILIATION_V2_LABEL
    assert 'mai ritentabile' not in row['cost_status']


def test_authentic_v1_pre_provider_rejection_is_not_declared_as_manual_reconciliation(tmp_path):
    """RV-COST P3: un rifiuto pre-provider v1 AUTENTICO (record_rejection) non e' una
    riconciliazione manuale: ne' l'etichetta nel riepilogo ne' cost_status nel registro."""
    from test_preparation_rejections import rejection
    p = _proposer(tmp_path)
    p.call = lambda **_: (_ for _ in ()).throw(rejection())
    with pytest.raises(OSError):
        p({'ticker': 'SYNTH'}, {'schema': {}})
    with p._db() as db:
        row = db.execute('SELECT * FROM requests').fetchone()
        assert row['state'] == 'rejected' and json.loads(row['receipt'])['pre_provider_rejection']['version'] == 1
    summary = p.summary()
    assert summary['requests'] == 1 and summary['spent_usd'] == 0
    assert 'manually_reconciled_nonbillable' not in summary and 'manual_reconciliation_status' not in summary
    [linked] = _linked_journal(tmp_path, p, row['key']).summary()['external_requests']
    assert linked['state'] == 'rejected' and 'cost_status' not in linked
