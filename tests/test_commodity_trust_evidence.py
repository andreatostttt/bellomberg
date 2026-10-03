"""Entirely fictional originals test the primary-format proof contract."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import socket
import sqlite3

import pytest

from bellomberg.valuation.commodity_trust_evidence import normalize_commodity_trust, NORMALIZER

DAY = '2026-09-28'
ON = '2026-06-30'
ENTITY = 'Example Gold Trust'
TICKER = 'EXAMPLE-GOLD'
ORIGIN = 'https://example.org/issuer/'


def _pdf(path, pages):
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfbase.pdfmetrics import stringWidth
    pdf = canvas.Canvas(str(path), pagesize=(700,850))
    for text in pages:
        y=820; pdf.setFont('Helvetica',10)
        for paragraph in text.splitlines():
            for line in simpleSplit(paragraph,'Helvetica',10,640):
                assert y>=40 and stringWidth(line,'Helvetica',10)<=640
                pdf.drawString(30,y,line); y-=14
        pdf.showPage()
    pdf.save()


def source(path, url, text):
    raw = path.read_bytes()
    return {'id': sha256(raw).hexdigest(), 'url': url, 'text': text,
            'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': sha256(raw).hexdigest(),
            'archive_path': str(path), 'published_at': DAY,
            'metadata': {'issuer': ENTITY, 'report_date': ON}}


def sources(tmp_path, *, fault=None):
    cover = f'''FORM 10-Q
For the quarterly period ended June 30, 2026
{ENTITY} (Exact name of registrant as specified in its charter)
Title of each class Trading Symbol(s) Name of each exchange on which registered
Shares {TICKER} NYSE Arca, Inc.'''
    balance = '''Statements of Assets and Liabilities (Unaudited)
At June 30, 2026 and December 31, 2025
June 30, 2026 December 31, 2025
Assets
Investment in gold bullion, at fair value(a) $ 1,020 $ 2,040
Total Assets 1,020 2,040
Liabilities
Sponsor’s fee payable 20 40
Total Liabilities 20 40
Commitments and contingent liabilities (Note 6) — —
Net Assets $ 1,000 $ 2,000
Shares issued and outstanding(b) 100 200'''
    expenses = '''Statements of Operations (Unaudited)
For the three and six months ended June 30, 2026 and 2025
Three Months Ended June 30, Six Months Ended June 30,
2026 2025 2026 2025
Expenses
Sponsor’s fee $ 1 $ 2 $ 3 $ 4
Total expenses 1 2 3 4'''
    highlights = '''8 - Financial Highlights
For the three and six months ended June 30, 2026 and 2025
Three Months Ended June 30, Six Months Ended June 30,
2026 2025 2026 2025
Expenses (e) 0.37% 0.37% 0.37% 0.37%
(e) Percentage is annualized.'''
    notes = '''1 - Organization
Example Gold Trust was organized as a New York trust.
The Trust’s sponsor is Example Sponsor LLC, a Delaware limited liability company.
Gold price is stated in U.S. dollars.
The Trust’s maximum exposure under these arrangements is unknown as this would involve future potential claims that may be made against the Trust that have not yet occurred.'''
    prospectus = f'''{ENTITY}
The Shares are listed under the ticker symbol “{TICKER}”.
The Shares are not interests in nor obligations of either the Sponsor or the Trustee.
The value of the Shares will be adversely affected if gold owned by the Trust is lost or damaged in circumstances in which the Trust is not able to recover the corresponding loss.
The lack of an active trading market for the Shares may result in losses on your investment at the time of disposition of your Shares.
The amount of gold represented by each Share will decrease to pay the Sponsor’s Fee and other Trust expenses.'''
    if fault == 'omitted_asset': balance = balance.replace('Total Assets', 'Receivable 1 2\nTotal Assets')
    if fault == 'omitted_liability': balance = balance.replace('Total Liabilities', 'Other payable 1 2\nTotal Liabilities')
    if fault == 'unclosed_ledger': balance = balance.replace('Net Assets $ 1,000', 'Net Assets $ 1,001')
    if fault == 'negative_liability': balance = balance.replace('fee payable 20', 'fee payable -20')
    if fault == 'cover_entity': cover = cover.replace(ENTITY, 'Other Gold Trust')
    if fault == 'cover_entity_suffix': cover = cover.replace(ENTITY, 'Other ' + ENTITY)
    if fault == 'cover_ticker': cover = cover.replace(TICKER, 'OTHER-GOLD')
    if fault == 'cover_date': cover = cover.replace('June 30, 2026', 'June 29, 2026')
    if fault == 'column_order': expenses = expenses.replace('2026 2025 2026 2025', '2025 2026 2025 2026')
    if fault == 'balance_scale': balance = balance.replace('(Unaudited)', '(Unaudited)\nAmounts in millions of U.S. dollars except shares')
    if fault == 'balance_currency_preamble': balance = 'Financial statement reporting currency: Canadian dollars\n' + balance
    if fault == 'balance_unknown_preamble': balance = 'An uncertified accounting convention\n' + balance
    if fault == 'balance_currency_after_table': balance += '\nReporting currency: Canadian dollars'
    if fault == 'notes_currency': notes += '\nFinancial statement reporting currency: Canadian dollars'
    if fault == 'notes_currency_units': notes += '\nAll amounts are presented in Canadian dollars'
    if fault == 'balance_column_order': balance = balance.replace('June 30, 2026 December 31, 2025', 'December 31, 2025 June 30, 2026')
    if fault == 'operation_unknown_scale': expenses = expenses.replace('(Unaudited)', '(Unaudited)\nAmounts in kUSD')
    if fault == 'operation_column_date': expenses = expenses.replace('Six Months Ended June 30,', 'Six Months Ended June 29,')
    if fault == 'highlight_missing_columns': highlights = highlights.replace('Three Months Ended June 30, Six Months Ended June 30,\n2026 2025 2026 2025\n', '')
    if fault == 'highlight_wrong_date': highlights = highlights.replace('June 30, 2026 and 2025', 'June 30, 2025 and 2024')
    if fault == 'highlight_column_order': highlights = highlights.replace('2026 2025 2026 2025', '2025 2026 2025 2026')
    if fault == 'highlight_column_date': highlights = highlights.replace('Six Months Ended June 30,', 'Six Months Ended June 29,')
    if fault == 'highlight_duplicate_date': highlights = highlights.replace('Expenses (e)', 'For the three and six months ended June 30, 2026 and 2025\nExpenses (e)')
    if fault == 'unannualized': highlights = highlights.replace('Percentage is annualized', 'Percentage is not annualized')
    statement_path, prospectus_path = tmp_path/'statement.pdf', tmp_path/'prospectus.pdf'
    _pdf(statement_path, [cover, balance, expenses, highlights, notes])
    _pdf(prospectus_path, [prospectus])
    fund = {'@type': ['InvestmentFund', 'FinancialProduct'], '@id': ORIGIN+'product#fund',
            'name': ENTITY, 'identifier': [{'propertyID': 'ticker', 'value': TICKER}]}
    properties = [{'name': 'NAV as of', 'value': '10', 'unitText': 'USD', 'valueReference': {'value': 'Sep 25, 2026'}},
                  {'name': 'Reference Benchmark', 'value': 'Observed gold benchmark'}]
    graph = {'@graph': [fund, {'about': {'@id': fund['@id']}, 'additionalProperty': properties}]}
    if fault == 'product_entity': fund['name'] = 'Other Gold Trust'
    if fault == 'product_ticker': fund['identifier'][0]['value'] = 'OTHER-GOLD'
    if fault == 'product_currency': properties[0]['unitText'] = 'CAD'
    items = [('closingPrice', 'Closing Price', '$10.25'), ('consolidatedVolume', 'Daily Volume', '1,000.00'),
             ('thirtyDayMedianBidAskSpread', '30 Day Median Bid/Ask Spread', '0.01%')]
    html = '<html><script type="application/ld+json">'+json.dumps(graph)+'</script>'
    for key, label, value in items:
        if fault == 'spread_relabel' and key == 'thirtyDayMedianBidAskSpread': label = 'Current Bid/Ask Spread'
        stamp = 'Sep 29, 2026' if fault == 'future_market' else 'Sep 25, 2026'
        html += f'<span data-id="keyFundFacts-{key}-label">{label}</span><span data-id="keyFundFacts-{key}-data">{value}</span><span data-id="keyFundFacts-{key}-asOf">as of {stamp}</span>'
    html += '<a href="statement.pdf">Quarterly report</a><a href="prospectus.pdf">Prospectus</a></html>'
    if fault == 'unlinked_prospectus': html = html.replace('href="prospectus.pdf"', 'href="other.pdf"')
    product_path = tmp_path/'product.html'; product_path.write_text(html, encoding='utf-8')
    result = [source(statement_path, ORIGIN+'statement.pdf', '\n'.join([cover,balance,expenses,highlights,notes])),
              source(product_path, ORIGIN+'product', html), source(prospectus_path, ORIGIN+'prospectus.pdf', prospectus)]
    if fault == 'normalized_original': result[0]['metadata']['normalizer'] = 'caller-constructed'
    if fault == 'raw_mismatch': result[0]['document_sha256'] = 'a'*64
    if fault == 'text_mismatch': result[0]['sha256'] = 'b'*64
    if fault == 'missing_raw': result[0].pop('archive_path')
    return result


def normalize(documents):
    return normalize_commodity_trust(*documents, ticker=TICKER, entity=ENTITY, on=ON, as_of=DAY)


def test_complete_commodity_primary_formats_preserve_accounting_market_and_cost_scopes(tmp_path, monkeypatch):
    docs = sources(tmp_path)
    calls = {'dns':0,'socket':0,'sqlite':0}
    def denied(key):
        def inner(*a,**k): calls[key]+=1; raise AssertionError('No transport/database in primary normalization')
        return inner
    monkeypatch.setattr(socket,'getaddrinfo',denied('dns'))
    monkeypatch.setattr(socket.socket,'connect',denied('socket'))
    monkeypatch.setattr(sqlite3,'connect',denied('sqlite'))
    result = normalize(docs)
    assert result['status']=='ready', result['issues']
    body=json.loads(result['documents'][0]['text'])
    rows={row['driver']:row for row in body['observations']}
    assert body['contract']==NORMALIZER and len(rows)==7
    assert rows['holdings']['value']['reported']['net_assets']==1000
    assert rows['fees']['period']=='2026-01-01/2026-06-30'
    assert rows['fees']['value']['annualized_expense_ratio']==.0037
    assert rows['fees']['value']['other_future_fees_ratio'] is None
    assert rows['fees']['value']['future_expense_ratio'] is None
    assert rows['terms']['value']['investment_leverage'] is None
    assert rows['liquidity']['period']==rows['quotation']['period']=='2026-09-25'
    assert rows['liquidity']['value']['spread_basis']=='30_day_median_bid_ask'
    assert rows['liquidity']['value']['spread_ratio']==.0001
    assert len(body['proofs']['financial'])>=10
    assert calls=={'dns':0,'socket':0,'sqlite':0}


@pytest.mark.parametrize('fault', ['omitted_asset','omitted_liability','unclosed_ledger','negative_liability',
    'cover_entity','cover_entity_suffix','cover_ticker','cover_date','column_order','unannualized','product_entity','product_ticker',
    'product_currency','spread_relabel','future_market','unlinked_prospectus','normalized_original',
    'raw_mismatch','text_mismatch','missing_raw','balance_scale','balance_column_order','operation_unknown_scale',
    'operation_column_date','highlight_missing_columns','highlight_wrong_date','highlight_column_order',
    'highlight_column_date','highlight_duplicate_date','balance_currency_preamble','balance_unknown_preamble',
    'balance_currency_after_table','notes_currency','notes_currency_units'])
def test_incomplete_or_forged_originals_never_create_typed_observations(tmp_path, fault):
    result=normalize(sources(tmp_path,fault=fault))
    assert result['status']=='incomplete' and result['issues'] and result['documents']==[], (fault,result)


def test_resealed_typed_json_is_rebuilt_from_originals_before_acceptance(tmp_path):
    docs=sources(tmp_path)
    first=normalize(docs)
    assert first['status']=='ready',first['issues']
    document=deepcopy(first['documents'][0]); body=json.loads(document['text'])
    body['observations'][1]['value']['liabilities']['sponsor_fee_payable']=0
    document['text']=json.dumps(body,sort_keys=True,separators=(',',':'))
    document['sha256']=sha256(document['text'].encode()).hexdigest()
    rebuilt=normalize(docs)['documents'][0]
    assert rebuilt['text']!=document['text'] and rebuilt['sha256']!=document['sha256']
