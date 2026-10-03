"""A class-axis, mezzanine US-GAAP balance must close without a fictional SEC tag."""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from test_balance_detail_evidence import source as source_document
from bellomberg.valuation.balance_sheet_evidence import (
    extract_balance_sheet_packet, normalize_balance_sheet,
)
from bellomberg.valuation.statement_table_evidence import _json


def _raw():
    header = '''<html xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:dei="http://xbrl.sec.gov/dei/2025"
 xmlns:ex="https://example.com/issuer/2025" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
 <ix:nonnumeric name="dei:EntityRegistrantName">Synthetic Industrial Issuer</ix:nonnumeric>'''
    for key, end, member in (
        ('now', '2025-12-31', None), ('prior', '2024-12-31', None),
        ('ordinary', '2025-12-31', 'ex:OrdinarySharesMember'),
        ('ordinary-prior', '2024-12-31', 'ex:OrdinarySharesMember'),
        ('class-a', '2025-12-31', 'us-gaap:CommonClassAMember'),
        ('class-a-prior', '2024-12-31', 'us-gaap:CommonClassAMember'),
        ('class-x', '2025-12-31', 'ex:CommonClassXMember'),
        ('class-x-prior', '2024-12-31', 'ex:CommonClassXMember'),
    ):
        segment = (f'<xbrli:segment><xbrldi:explicitmember '
                   f'dimension="us-gaap:StatementClassOfStockAxis">{member}'
                   '</xbrldi:explicitmember></xbrli:segment>') if member else ''
        header += (f'<xbrli:context id="{key}"><xbrli:entity>'
                   '<xbrli:identifier scheme="http://www.sec.gov/CIK">123</xbrli:identifier>'
                   f'{segment}</xbrli:entity><xbrli:period><xbrli:instant>{end}'
                   '</xbrli:instant></xbrli:period></xbrli:context>')
    header += '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'

    rows = []
    data = [
        ('Cash', 'CashAndCashEquivalentsAtCarryingValue', '20', 'now', ''),
        ('Other current assets', 'OtherAssetsCurrent', '10', 'now', ''),
        ('Total current assets', 'AssetsCurrent', '30', 'now', ''),
        ('Property, plant and equipment', 'PropertyPlantAndEquipmentNet', '50', 'now', ''),
        ('Total non-current assets', 'AssetsNoncurrent', '50', 'now', ''),
        ('Total assets', 'Assets', '80', 'now', ''),
        ('Trade payables', 'AccountsPayableCurrent', '12', 'now', ''),
        ('Total current liabilities', 'LiabilitiesCurrent', '12', 'now', ''),
        ('Long-term debt', 'LongTermDebtNoncurrent', '8', 'now', ''),
        ('Total non-current liabilities', 'LiabilitiesNoncurrent', '8', 'now', ''),
        ('Redeemable noncontrolling interests', 'RedeemableNoncontrollingInterestEquityCarryingAmount', '2', 'now', ''),
        ('Ordinary shares', 'CommonStockValue', '1', 'ordinary', ''),
        ('Class A ordinary shares', 'CommonStockValue', '2', 'class-a', ''),
        ('Class X ordinary shares', 'CommonStockValue', '—', 'class-x', 'fixed-zero'),
        ('Additional paid-in capital', 'AdditionalPaidInCapital', '60', 'now', ''),
        ('Treasury shares', 'TreasuryStockValue', '10', 'now', 'negative_parentheses'),
        ('Retained earnings', 'RetainedEarningsAccumulatedDeficit', '1', 'now', ''),
        ('Parent shareholders equity', 'StockholdersEquity', '54', 'now', ''),
        ('Minority interests', 'MinorityInterest', '4', 'now', ''),
        ('Total equity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', '58', 'now', ''),
        ('Total liabilities and equity', 'LiabilitiesAndStockholdersEquity', '80', 'now', ''),
    ]
    for i, (label, tag, value, context, presentation) in enumerate(data):
        fmt = ' format="ixt:fixed-zero"' if presentation == 'fixed-zero' else ''
        tagged = (f'<ix:nonfraction id="balance-{i}" name="us-gaap:{tag}" '
                  f'contextref="{context}" unitref="usd" scale="3"{fmt}>{value}</ix:nonfraction>')
        cell = f'({tagged})' if presentation == 'negative_parentheses' else tagged
        prior = context + '-prior' if context in ('ordinary', 'class-a', 'class-x') else 'prior'
        rows.append(f'<tr><td>{label}</td><td>{cell}</td><td><ix:nonfraction '
                    f'id="prior-{i}" name="us-gaap:{tag}" contextref="{prior}" '
                    f'unitref="usd" scale="3"{fmt}>{value}</ix:nonfraction></td></tr>')
    rows.insert(9, '<tr><td>Commitments and contingencies</td><td>'
                    '<ix:nonfraction name="us-gaap:CommitmentsAndContingencies" '
                    'contextref="now" unitref="usd" xsi:nil="true"></ix:nonfraction>'
                    '</td><td></td></tr>')
    return (header + '<table><tr><th>Balance</th><th>2025</th><th>2024</th></tr>'
            + ''.join(rows) + '</table></html>').encode()


def primary(tmp_path, raw=None):
    raw = _raw() if raw is None else raw
    path = tmp_path / 'report.htm'; path.write_bytes(raw)
    doc = source_document(raw)
    doc['archive_path'] = str(path)
    doc['balance_sheet_fields'] = extract_balance_sheet_packet(doc, raw)
    return doc


def test_class_axis_mezzanine_layout_reconciles_without_fabricated_liabilities_tag(tmp_path):
    doc = primary(tmp_path); before = deepcopy(doc)
    result = normalize_balance_sheet(doc)
    assert result['status'] == 'ready', result
    assert doc == before
    ledger = result['documents'][0]; body = json.loads(ledger['text'])
    assert body['reported_balance_reconciled'] and not body['economic_classification_approved']
    assert {f['accounting_side'] for f in body['components']} == {'asset', 'liability'}
    assert sum(Decimal(f['value_exact']) for f in body['components']
               if f['accounting_side'] == 'asset') == 80000
    assert sum(Decimal(f['value_exact']) for f in body['components']
               if f['accounting_side'] == 'liability') == 20000
    assert all(f['model_treatment'] is None for f in body['components'])
    assert not any('us-gaap:Liabilities' == g['parent']['reported_tag'] for g in body['groups'])
    closing = body['groups'][-1]
    assert [f['reported_tag'] for f in closing['components']] == [
        'us-gaap:LiabilitiesCurrent', 'us-gaap:LiabilitiesNoncurrent',
        'us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount',
        'us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest']
    assert body['liabilities_calculation']['operation'] == 'sum_reported_components'
    assert body['liabilities_calculation']['value_exact'] == '20000'
    equity = next(g for g in body['groups'] if g['parent']['reported_tag'] == 'us-gaap:StockholdersEquity')
    shares = [f for f in equity['components'] if f.get('reported_concept') == 'us-gaap:CommonStockValue']
    assert len(shares) == len({f['reported_tag'] for f in shares}) == 3
    assert {f['class_member'].split(':')[-1] for f in shares} == {
        'OrdinarySharesMember', 'CommonClassAMember', 'CommonClassXMember'}
    assert next(f for f in shares if f['class_member'].endswith('CommonClassXMember'))['explicit_zero']
    assert closing['components'][2]['value_exact'] == '2000'
    assert body['groups'][-2]['components'][-1]['value_exact'] == '4000'  # Minority interest.


@pytest.mark.parametrize('before,after', [
    ('dimension="us-gaap:StatementClassOfStockAxis"', 'dimension="us-gaap:OtherAxis"'),
    ('<tr><td>Class X ordinary shares</td>', '<tr><td>Unclassified zero class</td>'),
    ('name="us-gaap:LiabilitiesAndStockholdersEquity" contextref="now" unitref="usd" scale="3">80',
     'name="us-gaap:LiabilitiesAndStockholdersEquity" contextref="now" unitref="usd" scale="3">81'),
    ('name="us-gaap:LiabilitiesNoncurrent" contextref="now" unitref="usd" scale="3">8',
     'name="us-gaap:LiabilitiesNoncurrent" contextref="now" unitref="usd" scale="3">9'),
    ('name="us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest" contextref="now" unitref="usd" scale="3">58',
     'name="us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest" contextref="now" unitref="usd" scale="3">59'),
    ('format="ixt:fixed-zero">—', 'format="ixt:num-dot-decimal">—'),
    ('format="ixt:fixed-zero">—', 'format="ixt:fixed-zero">9'),
])
def test_class_axis_variant_fails_closed_on_changed_context_subtotal_zero_or_closing(tmp_path, before, after):
    raw = _raw()
    assert before.encode() in raw
    result = normalize_balance_sheet(primary(tmp_path, raw.replace(before.encode(), after.encode(), 1)))
    assert result['status'] == 'incomplete' and not result['documents'], result


def test_duplicate_class_or_omitted_zero_class_stays_incomplete(tmp_path):
    raw = _raw(); begin = raw.index(b'<tr><td>Class X ordinary shares</td>')
    end = raw.index(b'</tr>', begin) + len(b'</tr>')
    zero_row = raw[begin:end]
    for changed in (raw.replace(zero_row, b'', 1), raw.replace(zero_row, zero_row * 2, 1)):
        result = normalize_balance_sheet(primary(tmp_path, changed))
        assert result['status'] == 'incomplete' and not result['documents'], result


def test_rehashed_class_context_packet_cannot_overrule_original_bytes(tmp_path):
    doc = primary(tmp_path); forged = deepcopy(doc)
    packet = forged['balance_sheet_fields']
    packet['contexts']['class-a'] = packet['contexts']['ordinary']
    packet['sha256'] = sha256(_json({k:v for k,v in packet.items() if k != 'sha256'}).encode()).hexdigest()
    result = normalize_balance_sheet(forged)
    assert result['status'] == 'incomplete' and not result['documents'], result


def test_class_context_with_mixed_instant_and_duration_is_not_an_opening_fact(tmp_path):
    raw = _raw(); start = raw.index(b'<xbrli:context id="class-a">')
    end = raw.index(b'</xbrli:context>', start) + len(b'</xbrli:context>')
    original = raw[start:end]
    changed = original.replace(b'</xbrli:instant>',
        b'</xbrli:instant><xbrli:startdate>2025-01-01</xbrli:startdate>')
    assert original != changed
    result = normalize_balance_sheet(primary(tmp_path, raw.replace(original, changed, 1)))
    assert result['status'] == 'incomplete' and not result['documents'], result
