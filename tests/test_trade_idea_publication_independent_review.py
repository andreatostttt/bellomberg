"""Independent primary-publication scope probes; synthetic source text only."""
import pytest
from bellomberg.agents.trade_idea_sources import _primary_publication_context, _resolve_metadata_claims

IDENTITY = {'ticker': 'SYNTH-PUB', 'name': 'Synthetic issuer', 'exchange': 'TEST',
            'currency': 'EUR', 'status': 'confirmed'}
URL = 'https://example.org/declared-synthetic/report.html'

def test_actual_complete_publication_header_has_a_positive_control():
    text = 'Synthetic issuer\nPublished on 2026-07-31\nYear ended 2026-06-30\nReport body\n'
    result, _ = _resolve_metadata_claims({'url': URL}, text, IDENTITY)
    assert result['published_at'] == '2026-07-31'
    assert result['report_date'] == '2026-06-30'

def test_a_2000_character_cut_must_not_create_a_complete_publication_header():
    prefix = 'Synthetic issuer\nYear ended 2026-06-30\n'
    token = 'Published on 2026-07-23'
    padding = 'P' * (2000-len(prefix)-len(token)-1)
    text = prefix + padding + '\n' + token + ' for the separate merger press release; this report was released later.\n'
    assert text[:2000].endswith(token)
    assert not text.endswith(token)
    assert _primary_publication_context(text) == [], 'The bounded slice fabricated a line ending inside a continuing narrative reference'
    with pytest.raises(ValueError, match='Publication'):
        _resolve_metadata_claims({'url': URL}, text, IDENTITY)

@pytest.mark.parametrize('manual_quote', [False, True])
def test_a_manual_locator_cannot_choose_one_of_two_conflicting_primary_headers(manual_quote):
    text = ('Synthetic issuer\nPublished on 2026-07-31\nYear ended 2026-06-30\n'
            'Published on 2026-07-23\nConflicting primary metadata requires an external verified publication receipt.\n')
    assert {row[0] for row in _primary_publication_context(text)} == {'2026-07-23', '2026-07-31'}
    source = {'url': URL, 'published_at': '2026-07-23'}
    if manual_quote:
        source['publication_quote'] = 'Published on 2026-07-23'
    with pytest.raises(ValueError, match='Publication'):
        _resolve_metadata_claims(source, text, IDENTITY)

def test_even_exact_narrative_quote_beyond_cover_cannot_relabel_publication():
    text = ('Synthetic issuer\nYear ended 2026-06-30\n' + ('Cover and report contents.\n'*100)
            + 'The other issuer press release was published on 2026-07-23.\n')
    with pytest.raises(ValueError, match='Publication'):
        _resolve_metadata_claims({'url': URL, 'published_at': '2026-07-23',
            'publication_quote': 'published on 2026-07-23'}, text, IDENTITY)
