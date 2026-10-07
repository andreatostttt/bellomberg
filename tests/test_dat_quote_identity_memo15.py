"""DAT HYPE quote qualification; provider responses entirely synthetic."""
from types import SimpleNamespace as NS
import pytest
from bellomberg.valuation import dat_metrics as dm
from bellomberg.valuation.dcf_mnav import _spec_purr

INPUTS = dict(bookNAV=1000., cashFromOps=20., cashFromFin=30., treasuryDeploy=50.,
              reportedDigital=900., hypeHeld=30., taxRate=21., taxBasis=900.,
              reportedDTL=0., basicShares=100., warrants=[{'strike':5.,'amount':10.}])


def providers(monkeypatch, price=12., currency='USD', exchange='NMS', hype='40', fallback=False):
    import yfinance as yf
    calls=[]
    def ticker(symbol):
        calls.append(symbol)
        fast = NS(last_price=price if symbol != 'HYPE32196-USD' else hype)
        if currency is not None: fast.currency=currency
        if exchange is not None: fast.exchange=exchange
        return NS(fast_info=fast)
    monkeypatch.setattr(yf,'Ticker',ticker)
    monkeypatch.setattr(dm.requests,'get',lambda *a,**k:NS(raise_for_status=lambda:None,json=lambda:{'inputs':INPUTS}))
    def post(*a,**k):
        if fallback:raise RuntimeError('synthetic offline failure')
        return NS(json=lambda:{'HYPE':hype})
    monkeypatch.setattr(dm.requests,'post',post)
    return calls


def test_usd_math_and_requested_identity_preserved(monkeypatch):
    calls=providers(monkeypatch,exchange='SYNTHETIC_EXCHANGE')
    out=dm._fetch_dat_dashboard_inputs('EXACT.ALIAS')
    assert calls==['EXACT.ALIAS']
    assert out['derived']['adjusted_nav_musd']==1197.
    assert out['derived']['mnav']==1.061
    q=out['purr_quote']
    assert q['last']==12. and q['requested_ticker']=='EXACT.ALIAS'
    assert q['currency']=='USD' and q['exchange']=='SYNTHETIC_EXCHANGE'
    assert q['identity_status']=='UNVERIFIED'
    assert 'Nasdaq' not in out['nota_mnav'] and 'Nasdaq' not in q['fonte']


@pytest.mark.parametrize('currency',[None,'EUR','GBp','GBP',''])
def test_incomparable_currency_blocks_tool_and_real_consumer(monkeypatch,currency):
    providers(monkeypatch,currency=currency)
    out=dm._fetch_dat_dashboard_inputs('SYNTH')
    assert 'derived' not in out and out['derived_warning']
    assert out['purr_quote']['last'] is None
    assert out['purr_quote']['raw_last']==12.
    assert out['purr_quote']['currency']==currency
    assert out['purr_quote']['reason'] in ('currency_missing','currency_not_usd')
    assert 'error' in _spec_purr({'ticker':'SYNTH','_sources':{},'warnings_spec':[]},out)
    result=dm._prezzi_live_hype('SYNTH')
    assert len(result)==4 and result[0] is None


@pytest.mark.parametrize('price',[float('nan'),float('inf'),float('-inf'),True,False,0.,-1.])
def test_invalid_price_blocks_default_tuple_and_consumer(monkeypatch,price):
    providers(monkeypatch,price=price)
    out=dm._fetch_dat_dashboard_inputs('SYNTH')
    assert 'derived' not in out and out['derived_warning']
    assert out['purr_quote']['last'] is None
    assert out['purr_quote']['price_quality']=='INVALID'
    assert 'error' in _spec_purr({'ticker':'SYNTH','_sources':{},'warnings_spec':[]},out)
    assert dm._prezzi_live_hype('SYNTH')[0] is None


def test_absent_exchange_not_inferred_from_suffix(monkeypatch):
    providers(monkeypatch,exchange=None)
    out=dm._fetch_dat_dashboard_inputs('EXACT.MI')
    assert out['purr_quote']['exchange'] is None
    assert out['purr_quote']['identity_status']=='UNVERIFIED'
    assert 'Nasdaq' not in out['purr_quote']['fonte']
    assert out['derived']['mnav']==1.061


def test_declared_hype_fallback_preserved(monkeypatch):
    calls=providers(monkeypatch,fallback=True)
    out=dm._fetch_dat_dashboard_inputs('SYNTH')
    assert calls==['SYNTH','HYPE32196-USD']
    assert 'fallback' in out['hype_quote']['fonte']
    assert out['hype_quote']['unit_status']=='UNVERIFIED'
    assert out['derived']['mnav']==1.061


@pytest.mark.parametrize('hype',[float('nan'),float('inf'),True,0.])
def test_invalid_hype_price_not_used(monkeypatch,hype):
    providers(monkeypatch,hype=hype)
    out=dm._fetch_dat_dashboard_inputs('SYNTH')
    assert 'derived' not in out and out['derived_warning']
    assert out['hype_quote']['last'] is None
    assert out['hype_quote']['price_quality']=='INVALID'


def test_standard_tuple_remains_four_values_and_positive_strings_supported(monkeypatch):
    providers(monkeypatch,price='12.0',hype='40')
    result=dm._prezzi_live_hype('SYNTH')
    assert len(result)==4 and result[0]==12. and result[2]==40.


def test_metadata_exception_is_declared_without_info_lookup(monkeypatch):
    import yfinance as yf
    providers(monkeypatch)
    class Fast:
        last_price=12.
        @property
        def currency(self): raise RuntimeError('synthetic metadata failure')
        @property
        def exchange(self): raise RuntimeError('synthetic metadata failure')
    class Ticker:
        fast_info=Fast()
        @property
        def info(self): pytest.fail('no extra info fetch')
    monkeypatch.setattr(yf,'Ticker',lambda t:Ticker())
    out=dm._fetch_dat_dashboard_inputs('EXACT')
    assert out['purr_quote']['last'] is None
    assert out['purr_quote']['raw_last']==12.
    assert out['purr_quote']['metadata_errors']=={'currency':'RuntimeError','exchange':'RuntimeError'}
    assert out['purr_quote']['reason']=='currency_missing'
