"""Narrow, literal primary-source proofs for a dated fund/holding NAV.

Unsupported source layouts remain acquisition gaps; this module never infers a
missing liability or a published precision from a Python float.
"""
import re

from .nav_adapter import COMPONENTS

JUDGMENTS = {'perimeter', 'calendar', 'policy', 'nav_target', 'target_basis'}
LABELS = {
    'gross_assets': ('investments excluding cash', 'gross assets excluding cash', 'non-cash assets'),
    'cash': ('cash',), 'debt': ('debt', 'borrowings'),
    'preferred': ('preferred claims', 'preferred equity', 'preferred stock'),
    'other_liabilities': ('other liabilities',), 'accrued_fees': ('accrued fees',),
    'distributions_payable': ('distributions payable', 'dividends payable'),
    'tax': ('tax liabilities', 'tax payable'), 'equity_adjustments': ('equity adjustments',),
    'shares': ('shares outstanding', 'ordinary shares outstanding'),
    'reported_nav_per_share': ('net asset value per share', 'nav per share'),
}
NET_BASES = ('common equity net asset value', 'net assets attributable to ordinary shareholders',
             'net asset value attributable to ordinary shareholders')
FACT_KEYS = {'evidence_ids', 'evidence_quote', 'quoted_value', 'quoted_unit', 'period_quote'}

POLICY = {
    'version': 2, 'horizon': 'snapshot', 'analyst_judgments': sorted(JUDGMENTS),
    'historical_proof': 'Literal source excerpts for publication and components; shares, reported NAV and precision also accept verified fund_nav_statement_v1 observations. Unsupported layouts remain incomplete; no assumed zero or silent normalization.',
    'structured_observations': 'For shares, reported_nav_per_share and reported_nav_precision only: one verified fund-nav-statement document, exact /facts/N/value, /facts/N/unit and /facts/N/end evidence_pointer, quoted_value and quoted_unit. ClassSharesOutstanding, ClassNavPerShare and ClassNavPerSharePrecision respectively; exact selected legal entity, share class, opening date and currency. Precision must use the same source row/column as reported NAV. No mixed literal proof or calculation. The preparer recompiles the normalized document from its complete primary PDF text and page hashes. This proves observations, not publication, targets or full components.',
    'numeric_proof': 'evidence_ids (one), evidence_quote (one labelled measure with units), quoted_value, quoted_unit, period_quote (unique contiguous span including the measure, legal entity, exact share class and valuation_date). Same-currency scaling only.',
    'component_labels': LABELS,
    'components': 'Historical value and facts maps have exactly the adapter component keys. Each fact proves its own component, including explicit zeros. evidence_ids is exactly the union of the component source IDs. Gross assets exclude cash; do not use total assets including cash.',
    'publication': 'Historical value follows the adapter publication contract. evidence_quote is one unique contiguous excerpt containing publisher, legal entity, share class, ISO publication_date and valuation_date, and an explicit accepted common-equity net basis. Its one primary document must also prove reported_nav_per_share, directly or through its verified fund_nav_statement_v1 normalization. A known document published_at must agree; observed_download is never a publication date.',
    'publication_net_basis_phrases': NET_BASES,
    'reported_nav_precision': 'For literal NAV copy exactly the numeric fact proof of reported_nav_per_share, in unscaled reporting-currency per-share units. For normalized NAV cite ClassNavPerSharePrecision from exactly the same source row and column. The integer value is the number of decimal digits actually printed (0..8), not the decimal count of a float or a guessed tolerance.',
    'policy': 'An explicit supported accounting judgment, citing evidence and explaining complete claims/fees/payables and absence of unmodelled dilution. It does not prove a missing historical amount.',
}

SYSTEM = """
For fund_nav the specific NAV contract replaces the forecast/opening-kind rules:
use periods=[] and discount_convention=snapshot, never ten forecast years.
perimeter, calendar, policy, nav_target and target_basis are analyst_estimate;
the remaining opening drivers are historical. Follow fund_nav_preparation exactly
for literal scoped proofs or verified class-specific statement observations,
publication and source-printed precision.
Components require one fact for every component, including explicit zeros. Do not
invent a missing source or convert an unsupported source layout into an estimate.
"""


def _contains(text, value):
    return isinstance(text, str) and isinstance(value, str) and bool(value.strip()) and bool(
        re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text, re.I))


def _numeric(driver, item, evidence, unit, period, perimeter, prove):
    quote, span = item.get('evidence_quote'), item.get('period_quote')
    if not any(_contains(quote, label) for label in LABELS[driver]):
        return driver + ': la citazione non identifica la misura NAV supportata'
    if not all(_contains(span, perimeter[key]) for key in ('entity', 'share_class')):
        return driver + ': misura, entita e classe devono essere nello stesso estratto datato'
    if any(key in item for key in ('facts', 'evidence_pointer', 'calculation')):
        return driver + ': questa prova NAV richiede una misura letterale, senza campi ignorati'
    return prove(driver, item, evidence, unit, period, expected_entity=perimeter['entity'])


def prove_nav(driver, item, evidence, unit, period, perimeter, model, prove):
    """Return a blocking reason or None; no record or model value is filled in."""
    if driver == 'publication' and len(evidence) == 1 and (
            evidence[0].get('metadata') or {}).get('normalizer') == 'fund_nav_publication_catalog_v1':
        if (item.get('evidence_ids') != [evidence[0]['id']]
                or item.get('evidence_pointer') != {'value': '/publication'}
                or any(key in item for key in {'facts', 'calculation', 'record_pointer', 'evidence_quote',
                                               'quoted_value', 'quoted_unit', 'period_quote'})):
            return 'publication: exact publisher catalog receipt pointer required; no ignored mixed proofs'
        try:
            import json
            from math import isclose
            from .input_preparation import _day, _finite, _source_scale
            from .fund_nav_statement import PREFIX
            document, value = evidence[0], item.get('value')
            body, metadata = json.loads(document['text']), document['metadata']
            if (value != body['publication'] or value['publisher'] != perimeter['entity']
                    or value['share_class'] != perimeter['share_class'] or value['valuation_date'] != period
                    or value['basis'] != 'common_equity_net' or metadata['entity'] != perimeter['entity']
                    or metadata['share_class'] != perimeter['share_class'] or metadata['on'] != period
                    or body['raw_primary_page_downloaded'] is not False
                    or body['publication_date_basis'] != 'publisher_catalog_calendar_date'):
                return 'publication: publisher catalog, exact legal class, date or common NAV basis differs'
            published, available, cutoff = _day(value['publication_date']), _day(document['available_at']), _day(metadata['as_of'])
            if not published or not available or not cutoff or not _day(period) <= published <= available <= cutoff:
                return 'publication: publisher date and actual tool availability are not ordered before the cutoff'
            primary = body['statement_source_document_id']
            nav = model.get('reported_nav_per_share') or {}
            if nav.get('evidence_ids') not in ([primary], [PREFIX+primary]):
                return 'publication: published NAV must cite the exact original financial PDF behind the catalog link'
            reported = [fact for fact in body['net_class_facts'] if fact.get('concept') == 'ClassNavPerShare'
                        and fact.get('entity') == perimeter['entity'] and fact.get('share_class') == perimeter['share_class']
                        and fact.get('end') == period]
            if len(reported) != 1:
                return 'publication: one exact class-specific reported NAV required'
            scale = _source_scale(reported[0]['unit'], perimeter['currency']+' per share')
            if scale is None or not _finite(nav.get('value')) or not isclose(nav['value'], reported[0]['value']*scale, rel_tol=1e-10, abs_tol=1e-9):
                return 'publication: reported NAV amount or currency differs from the linked primary statement'
            return None
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            return 'publication: '+str(exc)
    if driver == 'components' and isinstance(item.get('value'), dict) and item['value'].get('basis') == 'incremental_claim_deductions':
        from .nav_components_evidence import NORMALIZER, verify_components_contract
        if (len(evidence) != 1 or (evidence[0].get('metadata') or {}).get('normalizer') != NORMALIZER
                or item.get('evidence_ids') != [evidence[0]['id']]
                or item.get('evidence_pointer') != {'value': '/component_value'}
                or any(key in item for key in {'facts', 'calculation', 'record_pointer', 'evidence_quote',
                                               'quoted_value', 'quoted_unit', 'period_quote'})):
            return 'components: exact exhaustive normalized coverage pointer required; no ignored mixed proofs'
        try:
            import json
            if json.loads(evidence[0]['text'])['component_value'] != item['value']:
                return 'components: values or complete coverage differ from the recompiled primary observation'
            _component_values, error = verify_components_contract(item['value'], entity=perimeter['entity'],
                share_class=perimeter['share_class'], on=period, currency=perimeter['currency'],
                as_of=evidence[0]['metadata']['as_of'])
            return error
        except (ValueError, KeyError, TypeError) as exc:
            return 'components: ' + str(exc)
    if 'evidence_pointer' in item:
        from .fund_nav_statement import structured_nav_proof
        return structured_nav_proof(driver, item, evidence, unit, period, perimeter, model)
    if driver == 'components':
        values, facts = item.get('value'), item.get('facts')
        if (not isinstance(values, dict) or set(values) != COMPONENTS
                or not isinstance(facts, dict) or set(facts) != COMPONENTS):
            return 'components: importi e prove richiesti per ogni componente, compresi gli zeri'
        if any(key in item for key in FACT_KEYS - {'evidence_ids'} | {'evidence_pointer', 'calculation'}):
            return 'components: solo prove separate per componente, nessuna prova aggregata ignorata'
        catalog, used = {doc['id']: doc for doc in evidence}, set()
        for name in sorted(COMPONENTS):
            fact = facts[name]
            if not isinstance(fact, dict) or set(fact) != FACT_KEYS:
                return name + ': campi della prova letterale mancanti o sconosciuti'
            ids = fact['evidence_ids']
            if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or ids[0] not in catalog:
                return name + ': una fonte primaria fra quelle del driver richiesta'
            error = _numeric(name, {**fact, 'value': values[name]}, [catalog[ids[0]]],
                             perimeter['currency'] + ' million', period, perimeter, prove)
            if error:
                return error
            used.update(ids)
        return None if used == set(catalog) else 'components: fonti non consumate dalle prove'
    if driver == 'publication':
        value, quote = item.get('value'), item.get('evidence_quote')
        keys = {'publisher', 'publication_date', 'valuation_date', 'basis', 'share_class'}
        if len(evidence) != 1 or not isinstance(value, dict) or set(value) != keys:
            return 'publication: contratto completo e una fonte ufficiale richiesta'
        if any(key in item for key in {'facts', 'evidence_pointer', 'calculation', 'quoted_value', 'quoted_unit', 'period_quote'}):
            return 'publication: solo evidence_quote, nessun campo di prova ignorato'
        if not isinstance(quote, str) or not quote.strip() or evidence[0]['text'].count(quote) != 1:
            return 'publication: estratto letterale contiguo e univoco richiesto'
        if (value['valuation_date'] != period or value['share_class'] != perimeter['share_class']
                or value['basis'] != 'common_equity_net'
                or not all(_contains(quote, value[key]) for key in keys - {'basis'})
                or not _contains(quote, perimeter['entity'])
                or not any(_contains(quote, phrase) for phrase in NET_BASES)):
            return 'publication: publisher, date, entita, classe o base NAV netta non provati'
        from .input_preparation import _day
        published = _day(value['publication_date'])
        if published is None or not _day(period) <= published <= _day(evidence[0]['available_at']):
            return 'publication: data pubblicazione non coerente con fotografia/disponibilita'
        if evidence[0].get('published_at') not in (None, value['publication_date']):
            return 'publication: data diversa dalla pubblicazione del documento citato'
        nav = model.get('reported_nav_per_share')
        if isinstance(nav, dict) and item['evidence_ids'] != nav.get('evidence_ids'):
            from .fund_nav_statement import PREFIX
            if ('evidence_pointer' not in nav or nav.get('evidence_ids') != [PREFIX+evidence[0]['id']]):
                return 'publication: NAV pubblicato e metadati devono citare lo stesso documento primario'
        return None
    if driver == 'reported_nav_precision':
        nav = model.get('reported_nav_per_share')
        if not isinstance(nav, dict) or any(item.get(key) != nav.get(key) for key in FACT_KEYS):
            return 'reported_nav_precision: serve la stessa prova del NAV pubblicato'
        if type(item.get('value')) is not int or not 0 <= item['value'] <= 8:
            return 'reported_nav_precision: intero tra zero e otto richiesto'
        error = _numeric('reported_nav_per_share', {**item, 'value': nav.get('value')}, evidence,
                         perimeter['currency'] + ' per share', period, perimeter, prove)
        if error:
            return error
        if item['quoted_unit'] != perimeter['currency'] + ' per share':
            return 'reported_nav_precision: precisione richiesta nella valuta per azione senza scala'
        # The shared proof already accepted exactly one plain source number.
        from .literal_fact_evidence import _NUMBER_TOKEN
        token = _NUMBER_TOKEN.findall(item['evidence_quote'])[0]
        digits = len(token.split('.')[1]) if '.' in token else 0
        return None if digits == item['value'] else 'reported_nav_precision: decimali diversi da quelli stampati nella fonte'
    return _numeric(driver, item, evidence, unit, period, perimeter, prove)


def prove_incremental_policy(item, model, catalog, perimeter, period, cutoff):
    """Authenticate the opt-in dilution judgment against the complete primary.

    Closed balance accounts prove the reported ledger. They do not prove the
    absence of outstanding claims which could dilute the selected share class.
    """
    components = (model.get('components') or {}).get('value')
    if not isinstance(components, dict) or components.get('basis') != 'incremental_claim_deductions':
        return None
    from .nav_components_evidence import verify_components_contract, verify_dilution_proof
    policy = item.get('value')
    expected = {'liability_basis': 'all_reported_claims_in_components',
                'fees_basis': 'incremental_deductions_after_covered_aggregates',
                'distributions_basis': 'incremental_deductions_after_covered_aggregates',
                'share_basis': 'basic_no_convertibles_or_other_dilution'}
    if (not isinstance(policy, dict) or set(policy) != set(expected) | {'dilution_proof'}
            or any(policy.get(key) != value for key, value in expected.items())):
        return 'policy: explicit incremental claim coverage and off-balance dilution policy required'
    _values, error = verify_components_contract(components, entity=perimeter['entity'],
        share_class=perimeter['share_class'], on=period, currency=perimeter['currency'], as_of=cutoff)
    if error:
        return 'policy: component coverage not verified: ' + error
    coverage = components['coverage']
    primary_id = coverage['primary_document_id']
    if primary_id not in catalog or primary_id not in (item.get('evidence_ids') or []):
        return 'policy: the original complete primary dilution disclosure must be cited'
    error = verify_dilution_proof(policy['dilution_proof'], coverage=coverage,
        entity=perimeter['entity'], on=period, as_of=cutoff, source=catalog[primary_id])
    return 'policy: ' + error if error else None


def wire_values(obj, text):
    return {'calendar': obj({'valuation_date': text, 'discount_convention': {'const': 'snapshot'},
                            'periods': {'type': 'array', 'maxItems': 0}})}
