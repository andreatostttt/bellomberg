"""Explicit public projection, then regeneration by the common economic engine.

No original workbook part, provider dump, personal narrative or metadata is
copied. Only consumed model values and public source references can be exported.
"""
import ast
import json
from copy import deepcopy
from hashlib import sha256
from ipaddress import ip_address
from math import isfinite
from pathlib import Path
import re
from urllib.parse import urlsplit, unquote


def _public_url(value, *, verified_urls=()):
    from .document_evidence import valid_source_url
    if not valid_source_url(value):
        return False
    parts = urlsplit(value)
    host = parts.hostname.lower().rstrip('.')
    if '.' not in host or host.endswith(('.local', '.localhost', '.internal')) or host == 'localhost':
        return False
    try:
        if not ip_address(host).is_global:
            return False
    except ValueError:
        pass
    # A download URL can contain PM-entered query/fragment metadata even when
    # its bytes are a genuine public document. Only an exact bounded primary
    # URL proof may authorize those parameters; encoded forms are not a bypass.
    path = parts.path
    while '%' in path:
        decoded = unquote(path)
        if decoded == path: break
        path = decoded
    return value in verified_urls or not parts.query and not parts.fragment and '?' not in path and '#' not in path


def _contract_tokens(*, reported_property=False, commodity=False):
    """Only literal machine contracts shipped with the live family adapters."""
    folder = Path(__file__).parent
    tokens = set()
    names = ('operating_adapter.py', 'bank_adapter.py', 'rab_adapter.py', 'nav_adapter.py',
                 'real_estate_adapter.py', 'property_development_adapter.py', 'resources_adapter.py',
                 'development_adapter.py', 'sotp_adapter.py', 'managed_care_adapter.py',
                 'managed_care.py', 'distributable_equity.py', 'insurance_adapter.py',
                 'insurance_economics.py', 'life_economics.py', 'capital_inputs.py', 'finite_cashflows.py',
                 'documented_inputs.py', 'quotation_evidence.py', 'trade_idea_exposure.py')
    if reported_property:
        names += ('property_nav_requirements.py', 'property_reported_nav.py', 'property_reported_sources.py')
    if commodity:
        names += ('trade_idea_commodity_exposure.py',)
    for name in names:
        for node in ast.walk(ast.parse((folder / name).read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r'[A-Za-z0-9_ .:/-]{1,180}', node.value):
                tokens.add(node.value)
    return tokens


def _reported_property_public_proofs(payload, records, catalog):
    """Public strings come from the bounded primary compiler, never the PM plan."""
    from .property_nav_requirements import REPORTED_SCHEMA, reported_policy_selected
    if not reported_policy_selected(records):
        return None
    from .property_reported_sources import NORMALIZER, SOURCE_URL, RAW_SHA
    from .preparation_record_evidence import prove_reported_record
    normalized = {ident for ident, doc in catalog.items()
                  if (doc.get('metadata') or {}).get('normalizer') == NORMALIZER
                  and (doc.get('metadata') or {}).get('source_document_id') == RAW_SHA}
    if RAW_SHA not in catalog or not normalized:
        raise ValueError('Reported property public projection requires the complete sealed primary catalog')
    if catalog[RAW_SHA].get('url') != SOURCE_URL:
        raise ValueError('Reported property public URL differs from the recompiled primary')
    plan = payload.get('preparation', {}).get('proposal', {}).get('plan') or {}
    model = plan.get('model') or {}
    historical = set(REPORTED_SCHEMA) - {'perimeter', 'calendar', 'policy', 'asset_shocks', 'nav_target', 'target_basis'}
    strings, keys, observed = set(), set(), set()
    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(key)
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str):
            strings.add(value)
    for row in records:
        driver = row['driver']
        if driver not in REPORTED_SCHEMA:
            raise ValueError('Unconsumed reported property input in public projection')
        if driver not in historical:
            continue
        item = model.get(driver)
        ids = (item or {}).get('evidence_ids') or []
        if (row['scenario'] != 'model' or row['kind'] != 'historical' or driver in observed
                or not isinstance(item, dict) or item.get('kind') != 'historical'
                or item.get('value') != row['value'] or not item.get('record_pointer')
                or len(ids) != 1 or ids[0] not in normalized):
            raise ValueError('Reported property historical input lacks exact primary-bound public proof: '+driver)
        error = prove_reported_record(item, [catalog[ids[0]]], driver=driver, field=row['field'],
            entity=row['entity'], period=row['period'], unit=row['unit'], basis=row['accounting_basis'])
        if error:
            raise ValueError('Reported property public proof invalid: '+error)
        observed.add(driver)
        collect(row['value'])
    if observed != historical:
        raise ValueError('Incomplete reported property public historical perimeter')
    return {'strings': strings, 'keys': keys,
            'urls': {SOURCE_URL, SOURCE_URL+'#reported-property-nav-1'}}


def _commodity_public_proofs(payload, records, catalog):
    """Only source-recompiled complete observations authorize public prose."""
    from .trade_idea_commodity_exposure import selected_records, SCHEMA
    if not selected_records(records):
        return None
    from .commodity_trust_evidence import NORMALIZER, ROLES
    from .preparation_record_evidence import prove_reported_record
    normalized = {ident: doc for ident, doc in catalog.items()
                  if (doc.get('metadata') or {}).get('normalizer') == NORMALIZER}
    if len(normalized) != 1:
        raise ValueError('Commodity public projection requires one complete recompiled primary inventory')
    ident, document = next(iter(normalized.items()))
    dependencies = document['metadata']['source_document_ids']
    if set(dependencies) != set(ROLES) or any(source not in catalog for source in dependencies.values()):
        raise ValueError('Commodity public projection requires all original primary dependencies')
    plan = payload.get('preparation', {}).get('proposal', {}).get('plan') or {}
    model = plan.get('model') or {}
    historical = set(SCHEMA)-{'perimeter','calendar','policy'}
    observed, strings, keys = set(), set(), set()
    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(key)
                collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)
        elif isinstance(value, str): strings.add(value)
    for row in records:
        driver = row['driver']
        if driver not in SCHEMA:
            raise ValueError('Unconsumed commodity public input')
        if driver not in historical: continue
        item = model.get(driver)
        if (row['scenario'] != 'model' or row['kind'] != 'historical' or driver in observed
                or not isinstance(item, dict) or item.get('kind') != 'historical'
                or item.get('value') != row['value'] or not item.get('record_pointer')
                or item.get('evidence_ids') != [ident]):
            raise ValueError('Commodity public observation lacks its exact recompiled primary proof: '+driver)
        error = prove_reported_record(item, [document], driver=driver, field=row['field'], entity=row['entity'],
            period=row['period'], unit=row['unit'], basis=row['accounting_basis'])
        if error: raise ValueError('Commodity public observation proof invalid: '+error)
        observed.add(driver); collect(row['value'])
    if observed != historical:
        raise ValueError('Incomplete commodity public historical perimeter')
    # The commodity compiler proves public observations, not arbitrary download
    # URL parameters. Its source URLs follow the common conservative URL gate.
    return {'strings': strings, 'keys': keys, 'urls': set()}


def public_model_payload(payload):
    """Return {status,reasons,bundle,sources}; never claim text scrubbing is enough."""
    envelope = {'status': 'blocked', 'reasons': [], 'bundle': None, 'sources': [],
                'contract': 'public_common_model/1'}
    try:
        from .sector_analysis import validate_bundle, prepare_sector_analysis
        from .input_preparation import _catalog, _day
        from .valuation_profile import BUSINESS_MODELS
        from .preparation_methods import method_schema
        original = payload.get('acquisition_snapshot') or {}
        ticker = original.get('case', {}).get('ticker')
        if not isinstance(ticker, str) or not re.fullmatch(r'[A-Z0-9^][A-Z0-9.=_^-]{0,31}', ticker):
            raise ValueError('Canonical public listing identity required')
        original = validate_bundle(original, ticker)
        from .trade_idea_model import candidate_model_usability
        if not candidate_model_usability(payload)['usable']:
            raise ValueError('Only complete usable common models have a public projection')
        basis = payload.get('preparation', {}).get('review_basis', {})
        dossier = basis.get('dossier') or {}
        documents = dossier.get('documents') or []
        catalog, issues, _ = _catalog(documents, _day(original['case']['as_of']))
        if issues or not catalog:
            raise ValueError('Verified preparation source catalog unavailable for public projection')
        records = original['case']['records']
        reported_property = (_reported_property_public_proofs(payload, records, catalog)
                             if original['decision']['method_id'] == 'property_nav' else None)
        exposure = original['decision']['method_id'] == 'exposure_analysis'
        commodity = _commodity_public_proofs(payload, records, catalog) if exposure else None
        proof = reported_property or commodity or {}
        for document in catalog.values():
            if not _public_url(document['url'], verified_urls=proof.get('urls', ())):
                raise ValueError('Source requires an explicit public URL without credentials, queries or local host')
        tokens = _contract_tokens(reported_property=reported_property is not None, commodity=commodity is not None)
        proven_public_strings = set(proof.get('strings', ()))
        proven_public_keys = set(proof.get('keys', ()))
        if exposure:
            # Exposure risk/replication text is observation, not analyst prose.
            # It may leave the private model only when the exact whole value
            # is independently re-proved against its public source observation.
            from .preparation_record_evidence import prove_reported_record
            source_plan = payload.get('preparation', {}).get('proposal', {}).get('plan') or {}
            def strings(value):
                if isinstance(value, dict):
                    for child in value.values():
                        yield from strings(child)
                elif isinstance(value, list):
                    for child in value:
                        yield from strings(child)
                elif isinstance(value, str):
                    yield value
            for row in original['case']['records']:
                if row['kind'] != 'historical':
                    continue
                item = (source_plan.get('model') or {}).get(row['driver'])
                ids = (item or {}).get('evidence_ids') or []
                if not isinstance(item, dict) or not item.get('record_pointer') or any(ident not in catalog for ident in ids):
                    raise ValueError('Observed exposure text lacks exact public source proof')
                if item.get('value') != row['value']:
                    raise ValueError('Observed exposure differs from its source-bound preparation')
                error = prove_reported_record(item, [catalog[ident] for ident in ids], driver=row['driver'], field=row['field'],
                    entity=row['entity'], period=row['period'], unit=row['unit'], basis=row['accounting_basis'])
                if error:
                    raise ValueError('Observed exposure public source proof invalid: ' + error)
                proven_public_strings.update(strings(row['value']))
        # Opaque identifiers preserve legal/asset correspondence without
        # exporting manually entered names, addresses or internal labels.
        identifiers = {}
        def identifier(value):
            if value not in identifiers:
                identifiers[value] = 'PUBLIC_ENTITY_' + str(len(identifiers)+1)
            return identifiers[value]
        for row in records:
            identifier(row['entity'])
        allowed_urls = {doc['url'] for doc in catalog.values()}
        def public_value(value, key=None):
            if isinstance(value, dict):
                projected = {}
                for name, child in value.items():
                    if not isinstance(name, str):
                        raise ValueError('Public model object keys must be strings')
                    public_name = name if name in tokens or name in proven_public_keys or name in ('start', 'end') else identifier(name)
                    projected[public_name] = public_value(child, name)
                return projected
            if isinstance(value, list):
                return [public_value(child, key) for child in value]
            if value is None or isinstance(value, bool):
                return value
            if type(value) in (int, float):
                if not isfinite(value):
                    raise ValueError('Nonfinite public model value')
                return value
            if not isinstance(value, str):
                raise ValueError('Unconsumed public model value type')
            if value in identifiers:
                return identifiers[value]
            if value in tokens or value in allowed_urls or value == ticker or re.fullmatch(r'\d{4}-\d{2}-\d{2}|[A-Z]{3}|GBp', value):
                return value
            if (exposure or reported_property is not None) and value in proven_public_strings:
                return value
            if key in ('id', 'entity', 'consolidated_entity', 'parent_entity', 'taxpayer_entity', 'name',
                       'label', 'custody', 'reserve_report', 'tax_source', 'regime', 'tax_jurisdiction', 'stage_order', 'share_class'):
                return identifier(value)
            # These disclosures are required text to explain the economic
            # conventions. Reconstruct them; never carry the PM's commentary.
            if key in ('capital.reconciliation_basis', 'capital.upstream_approval_basis', 'capital.terminal_basis'):
                return 'Public scenario convention; original public sources and consumed numeric inputs retained.'
            raise ValueError('Unproven free text in consumed public model: ' + str(key))
        public_ids = {ident: sha256((doc['url'] + '\n' + doc['sha256']).encode()).hexdigest()
                      for ident, doc in catalog.items()}
        public_records = []
        # Source locators are transformed explicitly; raw record metadata is
        # never copied. This also rejects references to private method archives.
        for row in records:
            ids = [part.strip() for part in str(row.get('source_locator') or '').split(',') if part.strip()]
            if not ids or any(ident not in catalog for ident in ids) or row['source_id'] not in allowed_urls:
                raise ValueError('Consumed input lacks a verified public preparation source')
            public_records.append({key: deepcopy(row[key]) for key in
                ('field', 'driver', 'scenario', 'period', 'unit', 'accounting_basis', 'as_of', 'valid_until', 'kind')})
            public_records[-1].update(value=public_value(row['value'], row['driver']),
                entity=identifiers[row['entity']], source_id=catalog[ids[0]]['url'],
                source_locator=','.join(public_ids[ident] for ident in ids),
                rationale='Public model ' + row['kind'] + ' input; supporting source references: '
                    + ', '.join(public_ids[ident] for ident in ids))
        profile = original['decision'].get('profile_id')
        instrument = original['decision'].get('instrument')
        if (not exposure and profile not in BUSINESS_MODELS) or instrument not in ('equity', 'cef', 'holding', 'dat', 'etf', 'etn', 'crypto', 'commodity'):
            raise ValueError('Public economic classification cannot be reconstructed')
        source = next(iter(catalog.values()))['url']
        cutoff = original['case']['as_of']
        quote = next(row['value'] for row in public_records if row['driver'] == 'quotation' and row['scenario'] == 'model')
        p = {'status': 'ok', 'source_id': source, 'as_of': cutoff, 'data': {
            'info': {'symbol': ticker, 'shortName': ticker, 'currency': quote['quote_unit'],
                     'financialCurrency': quote['financial_currency'], 'quoteType': 'EQUITY'},
            'vehicle_registry': None, 'evidence': [
                {'field': 'instrument', 'value': instrument, 'source_id': source, 'as_of': cutoff}]
                + ([] if exposure else [{'field': 'business_model', 'value': profile, 'source_id': source, 'as_of': cutoff}])}}
        context = ({'analysis_rationale': 'Public observed exposure inputs and exact published source references; intrinsic company fair value is not applicable.'}
                   if exposure else {'scenario_rationale': {
                       scenario: 'Public ' + scenario + ' scenario: consumed numerical assumptions and public source references.'
                       for scenario in ('bear', 'base', 'bull')}})
        bundle = prepare_sector_analysis(ticker, as_of=cutoff, providers={'profile': lambda *_a, **_k: deepcopy(p)},
            user_context={'method_records': public_records, 'analysis_context': context})
        if bundle['decision']['method_id'] != original['decision']['method_id']:
            raise ValueError('Public projection changes economic method')
        envelope['sources'] = [{'id': public_ids[ident], 'url': doc['url'],
            'title': urlsplit(doc['url']).hostname + ' public source', 'sha256': doc['sha256'],
            'published_at': doc['published_at'], 'available_at': doc['available_at'],
            'period': (doc.get('metadata') or {}).get('report_date') if _day((doc.get('metadata') or {}).get('report_date')) else None,
            'locator': public_ids[ident]} for ident, doc in catalog.items()]
        envelope.update(status='ready', bundle=bundle)
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        envelope['reasons'].append(str(exc))
    return envelope


def prepare_shareable_model(payload, output_dir):
    """Generate a separate common workbook exclusively from the public projection."""
    from .dcf_engine import generate_valuation, _write_payload_sidecar
    projected = public_model_payload(payload)
    if projected['status'] != 'ready':
        raise ValueError('Public model blocked: ' + '; '.join(projected['reasons']))
    bundle = projected['bundle']
    result = generate_valuation(bundle['case']['ticker'], prepared_bundle=bundle, output_dir=str(output_dir))
    from .trade_idea_model import candidate_model_usability
    if not candidate_model_usability(result)['usable']:
        raise ValueError('Public projection did not pass the common economic engine: ' + str(result.get('error')))
    # Numeric parity is part of public projection, not a best-effort promise.
    for scenario in ('bear', 'base', 'bull'):
        if result.get('fair_value_' + scenario) != payload.get('fair_value_' + scenario):
            raise ValueError('Public projection changes a decisive scenario value: ' + scenario)
    if result.get('method') == 'exposure_analysis':
        for key in ('gross_exposure', 'net_exposure', 'largest_absolute_weight', 'squared_weight_sum', 'annual_declared_cost_ratio', 'buckets'):
            if result['exposure_analysis'].get(key) != payload['exposure_analysis'].get(key):
                raise ValueError('Public projection changes an observed exposure result: ' + key)
        from .trade_idea_commodity_exposure import selected_records
        if selected_records(payload['acquisition_snapshot']['case']['records']):
            for key in ('accounting', 'fees', 'liquidity', 'quotation', 'accounting_nav_per_share',
                        'gold_nav_ratio', 'liability_nav_ratio', 'investment_leverage', 'historical_annualized_expense_ratio'):
                if result['exposure_analysis'].get(key) != payload['exposure_analysis'].get(key):
                    raise ValueError('Public projection changes a decisive commodity observation: '+key)
    result['preparation'] = {'status': 'public_projection', 'issues': [], 'provenance': {
        'origin': 'explicit_public_projection', 'public_sources': projected['sources'],
        'contract': projected['contract']}}
    if result.get('path'):
        result = _write_payload_sidecar(result)
    return result
