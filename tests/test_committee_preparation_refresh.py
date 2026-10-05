"""Two-generation parity with real storage, compiler and a metered fake provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from test_valuation_automation import automation, TICKER
from test_sector_analysis import DAY, providers_for


# Rotta 'committee' (dispatch get_valuation col preparatore): archiviata dal 1326312, corpo in
# archive/private/attic/tests_excel_archiviato_20261005/test_committee_preparation_refresh_legacy.py; contratto qui sotto.
@pytest.mark.parametrize('route', ['queue'])
def test_next_generation_reviews_published_basis_on_both_routes(automation, monkeypatch, route):
    from bellomberg.agents import chat_tools
    from bellomberg.storage import memory_db, valuation_versions
    from bellomberg.valuation import preparation_sources, sector_analysis
    manager, observed, policy_path, directory = automation
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    policy['triggers'].append('committee')
    policy_path.write_text(json.dumps(policy), encoding='utf-8')
    manager.enqueue_initial(TICKER, 'portfolio')
    initial = manager.run_one(owner='initial')
    assert initial['status'] == 'succeeded', initial
    before = manager.versions.current(TICKER)['current']
    original_bytes = Path(before['path']).read_bytes()
    first_count = len(observed['paid'])
    assert first_count > 0

    tomorrow = datetime.fromisoformat(DAY).replace(tzinfo=timezone.utc) + timedelta(days=1)
    manager.jobs.clock = manager.versions.clock = lambda: tomorrow
    def acquire(ticker, *, as_of):
        return sector_analysis.prepare_sector_analysis(ticker, as_of=as_of, providers=providers_for('software'))
    manager.acquire = acquire
    from test_input_preparation import _documents
    def collect(*args, **kwargs):
        return {'status': 'ready', 'documents': _documents(), 'issues': []}
    manager.collect = collect
    manager.enqueue_refresh(TICKER, 'filing_diff', 'next-information-cutoff')
    result = manager.run_one(owner='refresh')
    assert result['status'] == 'succeeded', result
    updated = manager.versions.current(TICKER)['current']
    requests = [json.loads(item['messages'][0]['content'])['contract']
                for item in observed['paid'][first_count:]]
    assert len(requests) == 4
    assert [item.get('preparation_refresh', {}).get('scope') for item in requests] == ['model', 'bear', 'base', 'bull']
    provenance = updated['preparation']['provenance']
    assert provenance['preparation_selection']['mode'] == 'review'
    assert provenance['preparation_selection']['source_generation_id'] == before['generation_id']
    assert provenance['refresh_review']['human_approved'] is False
    assert updated['generation_id'] != before['generation_id']
    assert Path(before['path']).read_bytes() == original_bytes
    assert manager.runtime.budget_audit()['request_count'] == len(observed['paid'])
    with manager.versions.db._conn() as conn:
        assert conn.execute('SELECT count(*) FROM valuation_theses').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM valuation_publications').fetchone()[0] == 2


def test_committee_route_meets_the_archived_contract(tmp_path, monkeypatch):
    """Contratto ATTUALE (ZR 05/10, Z1) della rotta 'committee' archiviata dal 1326312: con un bundle
    acquisito e un preparatore autorizzato, dispatch('get_valuation') risponde excel_archived e il
    preparatore (che spenderebbe richieste a pagamento) non viene mai invocato."""
    from bellomberg.agents import chat_tools
    from bellomberg.valuation import sector_analysis
    from _contratto_excel_archiviato import blinda_ramo_archiviato, file_in, spia_chiamante, verifica_archiviato
    monkeypatch.setattr(chat_tools, 'REPORT_DIR', tmp_path)
    bundle = sector_analysis.prepare_sector_analysis(TICKER, as_of=DAY, providers=providers_for('software'))
    usati = []
    prima = file_in(tmp_path)
    chiamate = blinda_ramo_archiviato(monkeypatch)
    risposta = chat_tools.dispatch('get_valuation', {'ticker': TICKER}, prepared_bundle=bundle,
                                   valuation_preparer=spia_chiamante(usati, 'preparatore committee'))
    verifica_archiviato(risposta, chiamate + usati, cartella=tmp_path, prima=prima)
