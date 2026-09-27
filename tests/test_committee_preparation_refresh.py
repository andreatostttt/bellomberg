"""Two-generation parity with real storage, compiler and a metered fake provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from test_valuation_automation import automation, TICKER
from test_sector_analysis import DAY, providers_for


@pytest.mark.parametrize('route', ['queue', 'committee'])
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
    if route == 'queue':
        manager.enqueue_refresh(TICKER, 'filing_diff', 'next-information-cutoff')
        result = manager.run_one(owner='refresh')
        assert result['status'] == 'succeeded', result
        updated = manager.versions.current(TICKER)['current']
    else:
        monkeypatch.setattr(memory_db, 'MemoryDB', lambda: manager.versions.db)
        monkeypatch.setattr(valuation_versions, 'ValuationVersions', lambda *a, **k: manager.versions)
        monkeypatch.setattr(chat_tools, 'REPORT_DIR', manager.runtime.output_dir)
        monkeypatch.setattr(preparation_sources, 'collect_preparation_evidence', collect)
        updated = chat_tools.dispatch('get_valuation', {'ticker': TICKER},
            prepared_bundle=acquire(TICKER, as_of=tomorrow.date().isoformat()),
            valuation_preparer=manager.runtime.preparer_for('committee'))['data']
        assert updated['valuation_usability']['usable'], updated.get('error')
        assert updated['model_publication']['status'] == 'published'

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
