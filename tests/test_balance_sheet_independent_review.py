"""Independent bounded review of new reported balance layouts, frozen bytes only."""
from decimal import Decimal
import json

import pytest

from bellomberg.valuation.balance_sheet_evidence import normalize_balance_sheet
from test_balance_sheet_evidence import balance_raw, balance_with_note_root, primary, _with_dimensional_asset_note
from test_balance_sheet_reported_layouts import _equity_raw


def receivable_with_note_and_allowance():
    raw = balance_with_note_root(b'us-gaap:ReceivablesNetCurrent')
    caption = (b'<td>Receivables, net of allowance '
        b'<ix:nonfraction name="us-gaap:AllowanceForDoubtfulAccountsReceivableCurrent" '
        b'contextref="now" unitref="usd" scale="3">1</ix:nonfraction></td>')
    return raw.replace(b'<td>OtherAssetsCurrent</td>', caption, 1)


def test_net_receivable_detail_keeps_allowance_without_another_asset_deduction():
    result = normalize_balance_sheet(primary(receivable_with_note_and_allowance()))
    assert result['status'] == 'ready', result
    body = json.loads(result['documents'][0]['text'])
    parent = next(row for row in body['groups'][0]['components']
                  if row['reported_tag'] == 'us-gaap:ReceivablesNetCurrent')
    assert parent['value_exact'] == '10000'
    assert [(row['value_exact'], row['treatment']) for row in parent['caption_disclosures']] == [
        ('1000', 'already_in_reported_net_amount')]
    components = body['components']
    assert sum(Decimal(row['value_exact']) for row in components if row['accounting_side'] == 'asset') == 80000
    assert sum(Decimal(row['value_exact']) for row in components
               if 'us-gaap:ReceivablesNetCurrent' in row['reported_ancestors']) == 10000
    assert all(row['reported_tag'] != 'us-gaap:ReceivablesNetCurrent' and
               'AllowanceForDoubtfulAccounts' not in row['reported_tag'] for row in components)
    assert body['economic_classification_approved'] is False


@pytest.mark.parametrize('fault', ['entity', 'period', 'unit'])
def test_caption_cannot_import_another_entity_date_or_currency_into_net_receivables(fault):
    raw = receivable_with_note_and_allowance()
    if fault == 'unit':
        raw = raw.replace(b'<table>', b'<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure>'
                          b'</xbrli:unit><table>', 1)
        raw = raw.replace(b'contextref="now" unitref="usd" scale="3">1</ix:nonfraction>',
                          b'contextref="now" unitref="eur" scale="3">1</ix:nonfraction>', 1)
    else:
        issuer, on = ('456', '2025-12-31') if fault == 'entity' else ('123', '2025-12-30')
        context = (f'<xbrli:context id="foreign-caption"><xbrli:entity><xbrli:identifier '
            f'scheme="http://www.sec.gov/CIK">{issuer}</xbrli:identifier></xbrli:entity>'
            f'<xbrli:period><xbrli:instant>{on}</xbrli:instant></xbrli:period></xbrli:context>').encode()
        raw = raw.replace(b'<table>', context + b'<table>', 1)
        raw = raw.replace(b'contextref="now" unitref="usd" scale="3">1</ix:nonfraction>',
                          b'contextref="foreign-caption" unitref="usd" scale="3">1</ix:nonfraction>', 1)
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and result['issues'] and not result['documents'], result


def test_two_noncontrolling_totals_cannot_be_silently_chosen_even_when_extra_row_is_zero():
    raw = _equity_raw()
    duplicate = (b'<tr><td>Additional noncontrolling total</td><td><ix:nonfraction '
        b'name="us-gaap:MinorityInterest" contextref="now" unitref="usd" scale="3">0'
        b'</ix:nonfraction></td></tr>')
    raw = raw.replace(b'<tr><td>StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest</td>',
        duplicate + b'<tr><td>StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest</td>', 1)
    result = normalize_balance_sheet(primary(raw))
    assert result['status'] == 'incomplete' and not result['documents'], result
    assert 'noncontrolling' in str(result['issues'])


def test_dimensional_table_with_reported_balance_subtotal_remains_an_ambiguity():
    raw = _with_dimensional_asset_note(balance_raw())
    raw = raw.replace(b'name="ex:MaximumExposureToLoss"', b'name="us-gaap:AssetsCurrent"')
    doc = primary(raw)
    assert len(doc['balance_sheet_fields']['tables']) == 2
    assert not doc['balance_sheet_fields'].get('excluded_tables')
    result = normalize_balance_sheet(doc)
    assert result['status'] == 'incomplete' and not result['documents'], result
    assert 'unique complete balance table' in str(result['issues'])
