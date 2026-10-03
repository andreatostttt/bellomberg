"""Trade Idea acquisition timestamps are distinct from daily historical cutoffs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from bellomberg.valuation.trade_idea_model import qualification, prepare, source_fingerprint
from test_trade_idea_economic import providers_for, IDENTITY, _documents, _operating_plan
from test_sector_analysis import DAY


def qualify(tmp_path, *, retrieved_at=DAY+'T12:00:00+00:00', observed_at=None, price=None, cutoff=DAY):
    providers = providers_for()
    original = providers['profile']
    def profile(*args, **kwargs):
        source = original(*args, **kwargs)
        if retrieved_at is None:
            source.pop('retrieved_at')
        else:
            source['retrieved_at'] = retrieved_at
        if observed_at is not None:
            source['data']['info']['regularMarketTime'] = observed_at.timestamp()
        if price is not None:
            source['data']['info']['regularMarketPrice'] = price
        return source
    providers['profile'] = profile
    plan = _operating_plan()
    if cutoff != DAY:
        for scope in [plan['model'], *plan['scenarios'].values()]:
            for entry in scope.values():
                if isinstance(entry, dict):
                    entry.update(valid_until=cutoff, valid_until_basis={'policy':'same_day','as_of':cutoff})
    return qualification('SYNTH-EXT', IDENTITY, cutoff, archive_root=tmp_path,
        providers=providers, source_report={'documents': _documents(), 'source_plan':plan})


@pytest.mark.parametrize('fault', ['same_day_future_observation','missing_receipt','naive_receipt',
    'malformed_receipt','boolean_receipt','future_acquisition','wrong_acquisition_day'])
def test_timestamp_faults_block_before_proposer_without_relabelling_historical_cutoff(tmp_path,fault):
    retrieval = DAY+'T12:00:00+00:00'
    observation = None
    if fault == 'same_day_future_observation':
        observation = datetime.fromisoformat(DAY+'T23:59:59+00:00')
    elif fault == 'missing_receipt': retrieval=None
    elif fault == 'naive_receipt': retrieval=DAY+'T12:00:00'
    elif fault == 'malformed_receipt': retrieval='unproved timestamp'
    elif fault == 'boolean_receipt': retrieval=True
    elif fault == 'future_acquisition': retrieval=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    else: retrieval='2026-09-11T12:00:00+00:00'
    q = qualify(tmp_path,retrieved_at=retrieval,observed_at=observation)
    assert q['status']=='blocked'
    assert any('Current quotation' in reason for reason in q['reasons']),q['reasons']
    assert q['as_of']==DAY and q['source_report']['source_plan']['model']['calendar']['value']['valuation_date']=='2025-12-31'
    calls=[]
    with pytest.raises(ValueError,match='Fonti non qualificate'):
        prepare(q,lambda *_:calls.append('forbidden-paid'),tmp_path/'model')
    assert calls==[]


def test_today_future_time_is_blocked_by_full_free_qualification(tmp_path):
    now=datetime.now(timezone.utc)
    future=now+timedelta(minutes=1)
    if future.date()!=now.date():pytest.skip('The last minute of UTC day is covered by the dated observation test')
    q=qualify(tmp_path,retrieved_at=now.isoformat(),observed_at=future,cutoff=now.date().isoformat())
    assert q['status']=='blocked' and any('timestamp is future' in reason for reason in q['reasons'])
    assert q['coverage']['sources']['current_quotation']['observed_local_date']==now.date().isoformat()


def test_equivalent_refetch_changes_acquisition_receipt_but_preserves_semantic_fingerprint(tmp_path):
    first=qualify(tmp_path/'first')
    second=qualify(tmp_path/'second',retrieved_at=DAY+'T12:30:00+00:00')
    assert first['status']==second['status']=='qualified',(first['reasons'],second['reasons'])
    a=first['coverage']['sources']['current_quotation']; b=second['coverage']['sources']['current_quotation']
    assert a['retrieved_at']!=b['retrieved_at']
    assert first['bundle']['snapshot_id']!=second['bundle']['snapshot_id']
    assert first['fingerprint']==second['fingerprint']==source_fingerprint(first)==source_fingerprint(second)
    assert first['as_of']==second['as_of']==DAY


def test_changed_observation_is_a_new_semantic_source_fingerprint(tmp_path):
    first=qualify(tmp_path/'first')
    second=qualify(tmp_path/'second',retrieved_at=DAY+'T12:30:00+00:00',
        observed_at=datetime.fromisoformat(DAY+'T12:01:00+00:00'))
    assert first['status']==second['status']=='qualified'
    assert first['fingerprint']!=second['fingerprint']


def test_changed_price_is_a_new_semantic_source_fingerprint(tmp_path):
    first=qualify(tmp_path/'first')
    second=qualify(tmp_path/'second',retrieved_at=DAY+'T12:30:00+00:00',price=11.)
    assert first['status']==second['status']=='qualified'
    assert first['fingerprint']!=second['fingerprint']


def test_live_trade_idea_provider_records_the_actual_post_acquisition_timestamp(monkeypatch):
    from types import SimpleNamespace
    import sys
    from bellomberg.valuation.trade_idea_model import _free_providers
    start=datetime.now(timezone.utc)
    monkeypatch.setitem(sys.modules,'yfinance',SimpleNamespace(Ticker=lambda _:SimpleNamespace(info={'symbol':'SYNTH-EXT'})))
    providers=_free_providers('SYNTH-EXT',IDENTITY,DAY)
    end=datetime.now(timezone.utc)
    envelope=providers['profile']('SYNTH-EXT',as_of=DAY)
    retrieved=datetime.fromisoformat(envelope['retrieved_at'])
    assert start<=retrieved<=end and retrieved.utcoffset()==timedelta(0)
    assert envelope['as_of']==retrieved.date().isoformat()
    assert envelope['as_of']!=DAY  # A live acquisition cannot backdate itself to the historical cutoff.


def test_prepare_rechecks_receipt_before_dispatch_even_after_a_resealed_qualification(tmp_path):
    q=qualify(tmp_path)
    q['bundle']['case']['sources']['profile'].pop('retrieved_at')
    q['fingerprint']=source_fingerprint(q)
    calls=[]
    with pytest.raises(ValueError,match='acquisition receipt'):
        prepare(q,lambda *_:calls.append('forbidden-paid'),tmp_path/'model')
    assert calls==[]


@pytest.mark.parametrize('zone,observed,retrieved,cutoff,expected', [
    ('Pacific/Auckland','2026-09-27T23:00:00+00:00','2026-09-27T23:01:00+00:00','2026-09-27','qualified'),
    ('America/New_York','2026-09-28T00:00:00+00:00','2026-09-28T00:01:00+00:00','2026-09-28','qualified'),
    ('Pacific/Auckland','2026-09-25T21:00:00+00:00','2026-09-27T23:01:00+00:00','2026-09-27','qualified'),
    ('America/New_York','2026-09-25T20:00:00+00:00','2026-09-28T12:00:00+00:00','2026-09-28','qualified'),
    ('Pacific/Auckland','2026-09-23T20:00:00+00:00','2026-09-27T23:01:00+00:00','2026-09-27','blocked'),
    ('America/New_York','2026-09-24T20:00:00+00:00','2026-09-28T12:00:00+00:00','2026-09-28','blocked'),
    ('Invalid/Timezone','2026-09-27T23:00:00+00:00','2026-09-27T23:01:00+00:00','2026-09-27','blocked'),
])
def test_current_quote_uses_utc_cutoff_and_consistent_exchange_local_freshness(tmp_path,zone,observed,retrieved,cutoff,expected):
    providers = providers_for(); original = providers['profile']
    def profile(*args, **kwargs):
        source = original(*args, **kwargs)
        source.update(as_of=cutoff,retrieved_at=retrieved)
        source['data']['info'].update(exchangeTimezoneName=zone,
            regularMarketTime=datetime.fromisoformat(observed).timestamp())
        return source
    providers['profile'] = profile
    plan = _operating_plan()
    for scope in [plan['model'],*plan['scenarios'].values()]:
        for item in scope.values():
            item.update(valid_until=cutoff,valid_until_basis={'policy':'same_day','as_of':cutoff})
    q = qualification('SYNTH-EXT',IDENTITY,cutoff,archive_root=tmp_path,providers=providers,
        source_report={'documents':_documents(),'source_plan':plan})
    assert q['status'] == expected, q['reasons']
    if expected == 'qualified':
        evidence = q['coverage']['sources']['current_quotation']
        assert evidence['observed_at'] == observed.replace('+00:00','Z')
        assert evidence['retrieved_at'] == retrieved
        assert evidence['acquired_as_of'] == evidence['information_cutoff'] == cutoff
