"""Only structural archive paths may preserve foreign IDs or hidden FV evidence."""
from copy import deepcopy

import pytest

from bellomberg.valuation import dcf_engine, dcf_quality
from test_documented_sanity_policy import stress_bundle


ARCHIVES = (
    ('preparation', 'review_basis'),
    ('preparation', 'proposal', 'plan'),
    ('preparation', 'provenance', 'trade_idea_revision', 'scoped_review', 'context', 'before'),
)
ALIAS_CASES = [(parts, location) for parts in ARCHIVES
    for location in ('root_dotted', 'nested_dotted', 'shared_root_dotted')
    if location != 'nested_dotted' or len(parts) > 2]


@pytest.fixture(scope='module')
def valid_payload(tmp_path_factory):
    payload = dcf_engine.generate_valuation('SYNTH-STRESS', prepared_bundle=stress_bundle(),
        output_dir=str(tmp_path_factory.mktemp('archive-alias')))
    assert payload['valuation_usability']['usable'], payload['valuation_usability']
    return payload


def put_path(payload, parts, value):
    parent = payload
    for part in parts[:-1]:
        parent = parent.setdefault(part, {})
    parent[parts[-1]] = value


def get_path(payload, parts):
    for part in parts:
        payload = payload[part]
    return payload


def alias(payload, parts, value, location):
    if location == 'nested_dotted':
        keys = ('preparation', '.'.join(parts[1:]))
    else:
        keys = ('.'.join(parts),)
    if location == 'shared_root_dotted':
        put_path(payload, parts, value)
    put_path(payload, keys, value)
    return keys


@pytest.mark.parametrize('parts,location', ALIAS_CASES)
@pytest.mark.parametrize('fault', ['generation_id', 'exclude_from_action_table'])
def test_dotted_archive_alias_cannot_skip_current_identity_or_exclusion(valid_payload, parts, location, fault):
    payload = deepcopy(valid_payload)
    value = {fault: 'foreign-current-generation' if fault == 'generation_id' else True}
    alias(payload, parts, value, location)
    result = dcf_quality.normalize_valuation_payload(payload)
    assert not result['valuation_usability']['usable'], result['valuation_usability']
    assert result['fair_value_base'] is None


@pytest.mark.parametrize('parts,location', [(parts, location) for parts, location in ALIAS_CASES
                                         if location != 'shared_root_dotted'])
def test_dotted_archive_alias_does_not_preserve_hidden_valuation(valid_payload, parts, location):
    payload = deepcopy(valid_payload)
    keys = alias(payload, parts, {'fair_value_base': 765.432}, location)
    payload['calculation_details']['generation_id'] = 'foreign-current-generation'
    result = dcf_quality.normalize_valuation_payload(payload)
    assert not result['valuation_usability']['usable']
    assert get_path(result, keys)['fair_value_base'] is None


@pytest.mark.parametrize('parts', ARCHIVES)
@pytest.mark.parametrize('block_current', [False, True])
def test_exact_structural_archives_remain_immutable_evidence(valid_payload, parts, block_current):
    payload = deepcopy(valid_payload)
    archived = {'generation_id': 'historical-generation', 'snapshot_id': 'historical-snapshot',
        'exclude_from_action_table': True, 'valuation_flagged': True,
        'fair_value_base': 123.45, 'nested': {'fair_value_base': 54.321}}
    put_path(payload, parts, deepcopy(archived))
    if block_current:
        payload['calculation_details']['generation_id'] = 'foreign-current-generation'
    before = deepcopy(payload)
    result = dcf_quality.normalize_valuation_payload(payload)
    assert result['valuation_usability']['usable'] is not block_current
    assert get_path(result, parts) == archived
    assert payload == before


@pytest.mark.parametrize('parts', ARCHIVES)
def test_foreign_current_ids_outside_exact_archive_still_block(valid_payload, parts):
    payload = deepcopy(valid_payload)
    put_path(payload, (*parts[:-1], parts[-1] + '_current'), {'generation_id': 'foreign-current-generation'})
    result = dcf_quality.normalize_valuation_payload(payload)
    assert not result['valuation_usability']['usable']
    assert result['fair_value_base'] is None
