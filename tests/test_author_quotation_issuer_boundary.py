"""Existing SEC legal-name policy at the author quotation boundary."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from test_author_quotation import case


@pytest.mark.parametrize('fault', ['terminal_full_stop', 'different_issuer', 'raw_ticker', 'raw_issuer'])
def test_author_filter_reuses_native_legal_name_policy_without_weakening_raw_binding(case, fault):
    from bellomberg.valuation.author_quotation import quotation_basis
    from bellomberg.valuation.trade_idea_model import source_fingerprint

    if fault == 'terminal_full_stop':
        case.plan['model']['perimeter']['value']['entity'] += '.'
    elif fault == 'different_issuer':
        case.plan['model']['perimeter']['value']['entity'] += ' Holdings.'
    else:
        # Even a self-consistent new raw receipt cannot substitute another
        # ticker or registrant for the accepted instrument and issuer.
        path = Path(case.primary['archive_path'])
        raw = path.read_bytes().replace(
            b'SYNTH-EXT' if fault == 'raw_ticker' else b'SYNTH-GROUP', b'UNRELATED')
        path.write_bytes(raw)
        digest = sha256(raw).hexdigest()
        case.primary.update(text=raw.decode(), sha256=digest, document_sha256=digest)
        case.qualification['fingerprint'] = source_fingerprint(case.qualification)
    before = deepcopy((case.qualification, case.plan))
    result = quotation_basis(case.qualification, case.plan, archive_root=case.root, price_fetch=case.fetch)
    if fault == 'terminal_full_stop':
        assert result['status'] == 'ready', result.get('issues')
        assert len(case.calls) == 1
        assert result['driver']['value']['share_class'] == case.plan['model']['perimeter']['value']['share_class']
    else:
        assert result['status'] == 'incomplete' and result['issues']
        assert not result.get('driver') and not result.get('receipt')
        assert case.calls == []
    assert (case.qualification, case.plan) == before
