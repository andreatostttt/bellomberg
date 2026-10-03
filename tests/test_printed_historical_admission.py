"""The ordinary catalog rechecks a printed ledger and its opening share count."""
from copy import deepcopy
from datetime import date
import json
from hashlib import sha256

import pytest

from test_printed_balance_evidence import primary


@pytest.mark.parametrize('tamper', [False, True])
def test_common_balance_dispatch_catalog_and_count_use_same_primary(tmp_path, tamper):
    from bellomberg.valuation.balance_sheet_evidence import normalize_balance_sheet
    from bellomberg.valuation.statement_table_evidence import normalize_statement_tables
    from bellomberg.valuation.preparation_fresh_historical import _has_opening_share_observation
    from bellomberg.valuation.input_preparation import _catalog
    source = primary(tmp_path)
    printed = normalize_statement_tables(source)
    balance = normalize_balance_sheet(source)
    assert balance['status'] == 'ready', balance['issues']
    documents = [source, *printed['documents'], *balance['documents']]
    if tamper:
        doc = deepcopy(documents[-1])
        body = json.loads(doc['text'])
        body['components'][0]['value_exact'] = '999999'
        doc['text'] = json.dumps(body)
        doc['sha256'] = sha256(doc['text'].encode()).hexdigest()
        documents[-1] = doc
    catalog, issues, _ = _catalog(documents, date(2026, 9, 9))
    if tamper:
        assert issues
    else:
        assert issues == []
        assert _has_opening_share_observation(catalog, source['metadata']['issuer'],
            '2026-06-30', '2026-09-09', (source,))
