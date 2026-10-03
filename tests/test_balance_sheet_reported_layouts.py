"""Reported US-GAAP caption disclosures and separate equity totals must stay exact."""
from decimal import Decimal
import json

import pytest

from test_balance_sheet_evidence import balance_raw, primary
from bellomberg.valuation.balance_sheet_evidence import normalize_balance_sheet


def _allowance_raw():
    caption = (b'<td>Accounts receivable, net of allowance of '
        b'<ix:nonfraction name="us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent" '
        b'contextref="now" unitref="usd" scale="3">1</ix:nonfraction> and '
        b'<ix:nonfraction name="us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent" '
        b'contextref="prior" unitref="usd" scale="3">2</ix:nonfraction>, respectively</td>')
    raw = balance_raw().replace(b'<td>CashAndCashEquivalentsAtCarryingValue</td>', caption)
    raw = raw.replace(b'us-gaap:CashAndCashEquivalentsAtCarryingValue', b'us-gaap:AccountsReceivableNetCurrent')
    return raw.replace(b'scale="3">20</ix:nonfraction></td>', b'scale="3">20</ix:nonfraction></td>'
        b'<td><ix:nonfraction name="us-gaap:AccountsReceivableNetCurrent" '
        b'contextref="prior" unitref="usd" scale="3">19</ix:nonfraction></td>', 1)


def test_reported_net_receivable_allowance_is_preserved_without_double_counting():
    result = normalize_balance_sheet(primary(_allowance_raw()))
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    receivable = next(x for x in body['components'] if x['reported_tag'] == 'us-gaap:AccountsReceivableNetCurrent')
    assert receivable['value_exact'] == '20000'
    assert [(x['end'], x['value_exact']) for x in receivable['caption_disclosures']] == [
        ('2025-12-31', '1000'), ('2024-12-31', '2000')]
    assert all(x['treatment'] == 'already_in_reported_net_amount' and x['proof']['row_index'] == 0
               for x in receivable['caption_disclosures'])
    assert sum(Decimal(x['value_exact']) for x in body['components'] if x['accounting_side'] == 'asset') == 80000
    assert not any('AllowanceForDoubtful' in x['reported_tag'] for x in body['components'])


@pytest.mark.parametrize('mutation', ['concept', 'currency', 'period', 'future_period', 'dimension', 'gross_parent', 'duplicate'])
def test_monetary_caption_must_match_reported_net_receivable_scope_and_unit(mutation):
    raw = _allowance_raw()
    if mutation == 'concept':
        raw = raw.replace(b'us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent', b'ex:OtherMonetaryCaption')
    elif mutation == 'currency':
        raw = raw.replace(b'<table>', b'<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit><table>', 1)
        raw = raw.replace(b'contextref="now" unitref="usd" scale="3">1</ix:nonfraction>',
                          b'contextref="now" unitref="eur" scale="3">1</ix:nonfraction>', 1)
    elif mutation == 'period':
        raw = raw.replace(b'contextref="prior" unitref="usd" scale="3">2</ix:nonfraction>',
                          b'contextref="missing" unitref="usd" scale="3">2</ix:nonfraction>', 1)
        with pytest.raises(ValueError, match='context or unit missing'):
            primary(raw)
        return
    elif mutation == 'future_period':
        raw = raw.replace(b'>2024-12-31</xbrli:instant>', b'>2027-12-31</xbrli:instant>')
    elif mutation == 'dimension':
        raw = raw.replace(b'>123</xbrli:identifier></xbrli:entity>',
                          b'>123</xbrli:identifier><xbrli:segment>Division</xbrli:segment></xbrli:entity>')
    elif mutation == 'gross_parent':
        raw = raw.replace(b'us-gaap:AccountsReceivableNetCurrent', b'us-gaap:AccountsReceivableGrossCurrent')
    else:
        raw = raw.replace(b'contextref="prior" unitref="usd" scale="3">2</ix:nonfraction>',
                          b'contextref="now" unitref="usd" scale="3">2</ix:nonfraction>', 1)
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and not result['documents'], result


def _equity_raw(*, redeemable=True, noncontrolling=True, minority_tag='NonredeemableNoncontrollingInterest'):
    def row(tag, value):
        return (f'<tr><td>{tag}</td><td><ix:nonfraction name="us-gaap:{tag}" '
                f'contextref="now" unitref="usd" scale="3">{value}</ix:nonfraction></td></tr>').encode()
    raw = balance_raw()
    parent = 60 - (2 if redeemable else 0) - (3 if noncontrolling else 0)
    raw = raw.replace(b'scale="3">55</ix:nonfraction>', f'scale="3">{parent-5}</ix:nonfraction>'.encode(), 1)
    raw = raw.replace(b'scale="3">60</ix:nonfraction>', f'scale="3">{parent}</ix:nonfraction>'.encode(), 1)
    if redeemable:
        raw = raw.replace(b'<tr><td>CommonStockValue</td>', row('RedeemableNoncontrollingInterestEquityCarryingAmount', 2)
                          + b'<tr><td>CommonStockValue</td>', 1)
    if noncontrolling:
        raw = raw.replace(b'<tr><td>LiabilitiesAndStockholdersEquity</td>', row(minority_tag, 3)
            + row('StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', parent+3)
            + b'<tr><td>LiabilitiesAndStockholdersEquity</td>', 1)
    return raw


@pytest.mark.parametrize('redeemable,noncontrolling,minority_tag', [
    (True, True, 'NonredeemableNoncontrollingInterest'),
    (True, True, 'MinorityInterest'),
    (False, True, 'NonredeemableNoncontrollingInterest'),
    (True, False, 'NonredeemableNoncontrollingInterest'),
])
def test_reported_mezzanine_and_noncontrolling_equity_close_separately(redeemable, noncontrolling, minority_tag):
    result = normalize_balance_sheet(primary(_equity_raw(
        redeemable=redeemable, noncontrolling=noncontrolling, minority_tag=minority_tag)))
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    assert body['reported_balance_reconciled'] and body['economic_classification_approved'] is False
    closing = body['groups'][-1]
    assert closing['parent']['reported_tag'] == 'us-gaap:LiabilitiesAndStockholdersEquity'
    assert sum(Decimal(x['value_exact']) for x in closing['components']) == Decimal(closing['parent']['value_exact']) == 80000
    assert sum(Decimal(x['value_exact']) for x in body['components'] if x['accounting_side'] == 'liability') == 20000
    if redeemable:
        assert closing['components'][1]['reported_tag'] == 'us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount'
    if noncontrolling:
        assert body['groups'][-2]['components'][1]['reported_tag'] == 'us-gaap:'+minority_tag


@pytest.mark.parametrize('mutation', ['redeemable', 'minority', 'equity', 'closing', 'order', 'unknown', 'missing_parent'])
def test_separate_equity_layout_cannot_hide_unreconciled_or_uncovered_rows(mutation):
    raw = _equity_raw()
    if mutation in ('redeemable', 'minority', 'equity', 'closing'):
        tag, before, after = {
            'redeemable': ('RedeemableNoncontrollingInterestEquityCarryingAmount', 2, 3),
            'minority': ('NonredeemableNoncontrollingInterest', 3, 4),
            'equity': ('StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 58, 59),
            'closing': ('LiabilitiesAndStockholdersEquity', 80, 81),
        }[mutation]
        raw = raw.replace(f'name="us-gaap:{tag}" contextref="now" unitref="usd" scale="3">{before}'.encode(),
                          f'name="us-gaap:{tag}" contextref="now" unitref="usd" scale="3">{after}'.encode(), 1)
    elif mutation == 'order':
        raw = raw.replace(b'us-gaap:NonredeemableNoncontrollingInterest', b'us-gaap:TemporarySwap')
        raw = raw.replace(b'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest',
                          b'us-gaap:NonredeemableNoncontrollingInterest')
        raw = raw.replace(b'us-gaap:TemporarySwap', b'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest')
    elif mutation == 'unknown':
        raw = raw.replace(b'us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount', b'ex:UnclassifiedCapital')
    else:
        raw = raw.replace(b'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', b'ex:UnknownTotal')
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and not result['documents'], result
