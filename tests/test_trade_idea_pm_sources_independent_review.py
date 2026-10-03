"""Independent read-only E1 probes; fake public transport and temporary archives."""
from copy import deepcopy
import pytest
from bellomberg.agents import trade_idea_sources as sources
from test_trade_idea_pm_sources import (IDENTITY, URL, WEBSITE, transport, qualifier,
    _profile_providers, method_collector, measured_no_real_transports)


@pytest.mark.parametrize('url', [
    URL + '?%2561pi_key=PRIVATE_CANARY',
    URL + '?%2574oken=PRIVATE_CANARY',
])
def test_credential_keys_remain_forbidden_after_supported_url_decoding(url):
    with pytest.raises(ValueError, match='Credential|Noncanonical'):
        sources.allowed_publisher_host(url, WEBSITE)


def test_worker_pin_does_not_alias_a_different_query_resource(tmp_path, monkeypatch):
    download, calls = transport()
    initial = qualifier(download)(IDENTITY['ticker'], IDENTITY, '2026-09-28', archive_root=tmp_path,
                                  document_sources=[{'url': URL}])
    assert initial['status'] == 'qualified', initial['reasons']
    alternate = URL + '?financial-year=2024'
    seen = []
    def fresh_download(url, *args, **kwargs):
        seen.append(('fresh', url))
        raise ValueError('Deliberate different-source offline block')
    monkeypatch.setattr('bellomberg.market_data.lettore_trimestrali.scarica_documento', fresh_download)
    def reconfirm(ticker, *, download, **kwargs):
        try:
            fetched = download(alternate, str(tmp_path / 'unused-destination'))
        except ValueError:
            seen.append(('blocked', alternate))
        else:
            seen.append(('reused', fetched['url']))
            assert fetched['url'] == alternate, 'query-distinct document was aliased to the accepted primary bytes'
        return method_collector(ticker, **kwargs)
    sources.qualify_with_document_sources(IDENTITY['ticker'], IDENTITY, '2026-09-28',
        archive_root=tmp_path, accepted_receipt=initial['document_receipt'],
        providers=_profile_providers('2026-09-28'), collector=reconfirm)
    assert seen and seen[0][0] != 'reused'
    assert len([row for row in calls if row[0] == 'request']) == 1
