"""Assemble dated opening evidence through the existing free source clients.

This collector declares its limited filing selection and any explicit weekend
price-date basis. It never invents an ADR ratio or an unavailable observation.
"""
from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path


def collect_preparation_evidence(ticker, *, as_of, archive_root, filing_results=(),
                                 catalog=None, download=None, facts_fetch=None, price_fetch=None,
                                 financial_currency=None, earnings_fetch=None, method_id=None, quote_identity=None):
    from .valuation_sources import collect_documents, company_facts_documents
    from .quotation_evidence import historical_quote_document, listing_identity_document
    acquired = collect_documents(ticker, as_of=as_of, archive_root=archive_root,
        filing_results=filing_results, catalog=catalog, download=download,
        selection_policy="opening_annual_comparative", max_download_attempts=12)
    result = {key: deepcopy(value) for key, value in acquired.items() if key != "documents"}
    result.update(documents=[], preparation_ready=False, acquired_document_index=[], selection={})
    documents = acquired["documents"]
    if not documents:
        return result
    try:
        dated = []
        for doc in documents:
            report_date = (doc.get("metadata") or {}).get("report_date")
            if date.fromisoformat(report_date) > date.fromisoformat(as_of):
                raise ValueError("filing reporting period after cutoff")
            dated.append((report_date, doc["published_at"], doc))
        latest = max(dated, key=lambda row: row[:2])
        same_period = [row for row in dated if row[0] == latest[0]]
        same_filing_day = [row for row in same_period if row[1] == latest[1]]
        if len(same_filing_day) > 1:
            accepted = []
            issuer_ids = {(row[2].get('metadata') or {}).get('emittente_id') for row in same_filing_day}
            for row in same_filing_day:
                meta = row[2].get('metadata') or {}
                try:
                    stamp = datetime.fromisoformat(meta.get('accepted_at', ''))
                    if (stamp.utcoffset() is None
                            or meta.get('form') not in ('6-K', '6-K/A')
                            or meta.get('tipo') not in ('trimestrale', 'semestrale', 'nove_mesi')):
                        raise ValueError('Unverified interim acceptance')
                    accepted.append((stamp, row))
                except (ValueError, TypeError):
                    raise ValueError('latest opening filing ambiguous; explicit document selection required')
            if not all(issuer_ids) or len(issuer_ids) != 1 or len({stamp for stamp, _ in accepted}) != len(accepted):
                raise ValueError('latest opening filing ambiguous; explicit document selection required')
            latest = max(accepted, key=lambda item: item[0])[1]
        on, _, primary = latest
        annual_forms = ("10-K", "20-F")
        annuals = [row for row in dated if (row[2].get("metadata") or {}).get("form") in annual_forms
                   and row[0] <= on]
        annual = max(annuals, key=lambda row: row[:2])[2] if annuals else None
        primary_docs = [primary]
        if annual is not None and annual["id"] != primary["id"]:
            primary_docs.append(annual)
        comparative = None
        if primary is not annual:
            matches = [row for row in dated if row[0] < on and row[0][5:] == on[5:]]
            comparative = max(matches, key=lambda row: row[:2])[2] if matches else None
        if comparative and all(doc["id"] != comparative["id"] for doc in primary_docs):
                primary_docs.append(comparative)
        # Earlier same-period financial reports remain visible for reconciliation;
        # publication order selects a primary, it never proves agreement of values.
        for _, _, companion in same_period:
            if all(doc['id'] != companion['id'] for doc in primary_docs):
                primary_docs.append(companion)
        selected_ids = {doc["id"] for doc in primary_docs}
        result["selection"] = {"policy": "opening_plus_latest_annual_and_available_prior_year_comparative",
            "opening_date": on, "selected_document_id": primary["id"],
            "annual_document_id": annual["id"] if annual else None,
            "comparative_document_id": comparative["id"] if comparative else None,
            "selected_document_ids": [doc["id"] for doc in primary_docs],
            "excluded_document_ids": [doc["id"] for doc in documents if doc["id"] not in selected_ids],
            "limitation": "Source availability does not certify annual/interim reconciliation or bank legal-entity ledgers; no annualization is inferred."}
        if len(same_period) > 1:
            result['selection']['same_period_selection_basis'] = (
                'latest_verified_sec_acceptance' if len(same_filing_day) > 1 else 'latest_filing_date')
        if annual is None:
            result["issues"].append({"source": "annual coverage", "reason":
                "Annual filing unavailable in the verified catalog; do not substitute interim revenue for a full year"})
        selected = deepcopy(primary_docs)
        from .parent_inline_evidence import collect_parent_inline
        parent = collect_parent_inline(primary_docs, archive_root)
        for doc in selected:
            if doc['id'] in parent['packets']:
                doc['inline_parent_fields'] = deepcopy(parent['packets'][doc['id']])
        selected.extend(deepcopy(parent['documents']))
        result['parent_inline'] = {k: deepcopy(v) for k, v in parent.items() if k not in ('documents', 'packets')}
        from .preparation_exhibits import collect_preparation_exhibits
        exhibits = collect_preparation_exhibits(primary_docs, archive_root=archive_root,
                                               as_of=as_of, download=download)
        selected.extend(exhibits["documents"])
        result["supplemental_exhibits"] = {k: deepcopy(v) for k, v in exhibits.items() if k != "documents"}
        result["issues"].extend(deepcopy(exhibits["issues"]))
        from .preparation_earnings import collect_earnings_evidence
        earnings = collect_earnings_evidence(ticker, primary=primary, as_of=as_of,
            archive_root=archive_root, fetch=earnings_fetch, download=download)
        existing = {doc['id']: doc for doc in selected}
        reused_earnings = []
        for doc in earnings['documents']:
            if doc['id'] in existing:
                if any(existing[doc['id']].get(k) != doc.get(k) for k in
                       ('url', 'published_at', 'text', 'sha256', 'document_sha256')):
                    raise ValueError('Repeated supplemental source has conflicting primary provenance')
                reused_earnings.append(doc['id'])
            else:
                selected.append(deepcopy(doc))
                existing[doc['id']] = doc
        result["earnings_releases"] = {k: deepcopy(v) for k, v in earnings.items() if k != "documents"}
        if reused_earnings:
            result['earnings_releases']['reused_document_ids'] = reused_earnings
        result["issues"].extend(deepcopy(earnings["issues"]))
        try:
            root = Path(archive_root).resolve()
            raw_path = Path(primary['archive_path']).resolve()
            if not raw_path.is_relative_to(root):
                raise ValueError('listing source path outside verified archive')
            listing = listing_identity_document(primary, raw_path.read_bytes(), ticker=ticker, on=on)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            listing = {'status': 'incomplete', 'documents': [],
                'issues': [{'source': 'listing', 'reason': type(exc).__name__ + ': ' + str(exc)}]}
        quote_on, price_basis = on, None
        try:
            if date.fromisoformat(on).weekday() >= 5 and method_id == 'operating_fcff':
                from .quotation_evidence import sec_us_weekend_quote_basis
                if financial_currency != 'USD' or len(listing['documents']) != 1:
                    raise ValueError('Weekend opening requires a verified USD Nasdaq or NYSE listing; no implicit prior close')
                quote_on, price_basis = sec_us_weekend_quote_basis(
                    listing['documents'][0], primary, ticker=ticker, opening_date=on)
            include_identity = ticker.endswith('.MI') and primary['metadata'].get('form') in ('6-K', '20-F')
            if quote_identity is not None:
                from .quotation_evidence import cached_historical_quote_document
                exchange = quote_identity.get('fullExchangeName') or quote_identity.get('exchange')
                if quote_identity.get('symbol') != ticker or not exchange or not quote_identity.get('currency'):
                    raise ValueError('Exact observed ticker, exchange and quotation currency required for cached historical price')
                historical_quote = cached_historical_quote_document(ticker, on=quote_on, as_of=as_of,
                    fetch=price_fetch, include_identity=include_identity, archive_root=archive_root,
                    quote_currency=quote_identity['currency'], context={'exchange': exchange,
                        'primary_document_id': primary['id'], 'primary_raw_sha256': primary.get('document_sha256'),
                        'listing_policy': 'SEC_listing_unit_identity/1',
                        'listing_sha256': [doc['sha256'] for doc in listing['documents']],
                        'opening_date': on, 'date_basis': price_basis})
            else:
                historical_quote = historical_quote_document(ticker, on=quote_on, as_of=as_of,
                    fetch=price_fetch, include_identity=include_identity)
            if price_basis:
                historical_quote['price_date_basis'] = deepcopy(price_basis)
        except (ValueError, KeyError, TypeError) as exc:
            historical_quote = {'status': 'incomplete', 'documents': [], 'issues': [
                {'source': 'historical quote date', 'reason': type(exc).__name__ + ': ' + str(exc)}]}
        components = {
            'company_facts': company_facts_documents(primary_docs, as_of=as_of, fetch=facts_fetch),
            'historical_quote': historical_quote, 'listing': listing}
        parent_sources_ready = True
        if method_id == 'bank_residual_income':
            from .preparation_bank_sources import collect_bank_sources
            bank = (collect_bank_sources(primary=primary, documents=selected, on=on, as_of=as_of,
                archive_root=archive_root, download=download) if financial_currency == 'USD' else
                {'status': 'incomplete', 'documents': [], 'issues': [{'source': 'bank regulatory sources',
                    'reason': 'Supported FDIC acquisition requires explicit USD reporting currency; no foreign-bank proxy.'}]})
            components['bank_regulatory'] = bank
            result['bank_regulatory_sources'] = {k: deepcopy(v) for k, v in bank.items() if k != 'documents'}
            from .preparation_parent_sources import collect_parent_sources
            parent_report = (collect_parent_sources(primary=primary, bank_documents=bank['documents'],
                on=on, as_of=as_of, archive_root=archive_root, download=download) if bank['status'] == 'ready' else
                {'status': 'not_requested', 'documents': [], 'issues': [], 'reason': 'FDIC parent identity unresolved'})
            selected.extend(deepcopy(parent_report['documents']))
            result['issues'].extend(deepcopy(parent_report['issues']))
            result['parent_regulatory_sources'] = {k: deepcopy(v) for k, v in parent_report.items() if k != 'documents'}
            # A verified SEC parent-only source is retained when the PDF is
            # unavailable. Its presence does not certify complete debt/cash scope.
            parent_sources_ready = parent_report['status'] == 'ready' or parent['status'] == 'ready'
        tagged = components['company_facts']
        financial_ready = bool(tagged['documents']) and tagged['status'] == 'ready'
        result['statement_tables'] = {'status': 'not_requested', 'issues': []}
        if not financial_ready:
            from .statement_table_evidence import collect_statement_tables
            printed = collect_statement_tables(primary_docs, archive_root)
            for doc in selected:
                if doc['id'] in printed['packets']:
                    doc['statement_table_fields'] = deepcopy(printed['packets'][doc['id']])
            selected.extend(deepcopy(printed['documents']))
            result['statement_tables'] = {k: deepcopy(v) for k, v in printed.items() if k not in ('documents', 'packets')}
            result['issues'].extend(deepcopy(printed['issues']))
            # Keep companyfacts gaps visible. Printed tables are a distinct,
            # verified source family, never fabricated tags for the missing filing.
            tagged_accessions = {(doc.get('metadata') or {}).get('accession') for doc in tagged['documents']}
            financial_ready = all(doc['id'] in printed['packets'] or
                bool((doc.get('metadata') or {}).get('accession')) and
                doc['metadata']['accession'].replace('-', '') in tagged_accessions for doc in primary_docs)
        result['financial_sources_ready'] = financial_ready
        result['balance_details'] = {'status': 'not_requested', 'issues': []}
        result['balance_sheet'] = {'status': 'not_requested', 'issues': []}
        if method_id == 'operating_fcff' and primary['metadata'].get('form') in ('10-K', '10-Q'):
            from .balance_detail_evidence import collect_balance_details
            details = collect_balance_details(primary, archive_root)
            for doc in selected:
                if doc['id'] in details['packets']:
                    doc['balance_detail_fields'] = deepcopy(details['packets'][doc['id']])
            selected.extend(deepcopy(details['documents']))
            result['balance_details'] = {k: deepcopy(v) for k, v in details.items() if k not in ('documents', 'packets')}
            result['issues'].extend(deepcopy(details['issues']))
            # Supplemental decompositions do not certify full operating scope.
            from .balance_sheet_evidence import collect_balance_sheet
            balance = collect_balance_sheet(primary, archive_root)
            for doc in selected:
                if doc['id'] in balance['packets']:
                    doc['balance_sheet_fields'] = deepcopy(balance['packets'][doc['id']])
                    doc['balance_detail_fields'] = deepcopy(balance['detail_packets'][doc['id']])
            selected.extend(deepcopy(balance['documents']))
            result['balance_sheet'] = {k: deepcopy(v) for k, v in balance.items()
                                       if k not in ('documents', 'packets', 'detail_packets')}
            result['issues'].extend(deepcopy(balance['issues']))
        elif method_id == 'operating_fcff' and (primary.get('filing_verification') is not None
                or any(d['id'] == primary['id'] and d.get('statement_table_fields') is not None for d in selected)):
            from .balance_sheet_evidence import normalize_balance_sheet
            balance = normalize_balance_sheet(next(d for d in selected if d['id'] == primary['id']))
            selected.extend(deepcopy(balance['documents']))
            result['balance_sheet'] = {k:deepcopy(v) for k,v in balance.items() if k != 'documents'}
            result['issues'].extend(deepcopy(balance['issues']))
        from .operating_wc_evidence import collect_operating_wc
        working_capital = collect_operating_wc(selected, primary_id=primary['id'], on=on, as_of=as_of)
        selected.extend(working_capital['documents'])
        result['operating_wc_disclosures'] = {k: deepcopy(v) for k, v in working_capital.items() if k != 'documents'}
        result['issues'].extend(working_capital['issues'])
        try:
            if components['listing']['status'] != 'ready' and annual is not None and components['historical_quote']['documents']:
                from .foreign_listing_evidence import normalize_foreign_listing
                components['listing'] = normalize_foreign_listing(primary, annual,
                    components['historical_quote']['documents'][0], ticker=ticker, on=on, as_of=as_of)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            components["listing"] = {"status": "incomplete", "documents": [],
                "issues": [{"source": "listing", "reason": type(exc).__name__ + ": " + str(exc)}]}
        if financial_currency is not None and components['historical_quote']['documents']:
            quote = json.loads(components['historical_quote']['documents'][0]['text'])['observation']['currency']
            quote_currency = 'GBP' if quote in ('GBp', 'GBX') else quote
            if financial_currency != quote_currency:
                from .fx_evidence import collect_fx_evidence, collect_cross_fx_evidence
                fx_collector = collect_fx_evidence if 'EUR' in (financial_currency, quote_currency) else collect_cross_fx_evidence
                components['fx'] = fx_collector(financial_currency=financial_currency,
                    quote_currency=quote_currency, on=on, as_of=as_of, archive_root=archive_root, download=download)
        result["components"] = {}
        for name, component in components.items():
            selected.extend(deepcopy(component["documents"]))
            result["issues"].extend(deepcopy(component.get("issues", [])))
            result["components"][name] = {key: deepcopy(value) for key, value in component.items() if key != "documents"}
        from .statement_shares_evidence import normalize_statement_shares
        shares = normalize_statement_shares(primary, components['company_facts']['documents'])
        selected.extend(deepcopy(shares['documents']))
        result['statement_shares'] = {k: deepcopy(v) for k, v in shares.items() if k != 'documents'}
        # This narrow extractor is supplementary, not an implied repair of other
        # share disclosures. Unsupported layouts remain an explicit coverage gap.
        if shares.get('source_disagreement'):
            result['issues'].append({'source': 'statement_shares', 'reason':
                'Printed statement and SEC common-share tags disagree; explicit source selection required.'})
        if financial_currency is not None:
            from .market_reference_evidence import collect_market_references
            market = collect_market_references(currency=financial_currency, as_of=as_of,
                archive_root=archive_root, download=download,
                ticker=ticker if components['listing']['status'] == 'ready' else None)
            selected.extend(deepcopy(market['documents']))
            result['issues'].extend(deepcopy(market['issues']))
            result['market_references'] = {k: deepcopy(v) for k, v in market.items() if k != 'documents'}
        else:
            result['market_references'] = {'status': 'not_requested',
                'reason': 'Financial currency not supplied; no quote-currency or USD assumption.'}
        # Preserve the verified private archive locator: downstream listing
        # reproof needs the original bytes, not the normalized extracted text.
        # Public provenance below continues to expose URLs and hashes only.
        result["acquired_document_index"] = [{key: deepcopy(doc[key]) for key in
            ("id", "url", "published_at", "sha256", "document_sha256", "metadata") if key in doc} for doc in selected]
        result["preparation_ready"] = parent_sources_ready and annual is not None and financial_ready and exhibits["status"] != "incomplete" and result['balance_details']['status'] != 'incomplete' and result['balance_sheet']['status'] != 'incomplete' and all(
            component.get("documents") and component.get("status") == "ready"
            for name, component in components.items() if name != 'company_facts')
        # Known missing opening proofs cannot justify paying for a proposal.
        # Retain an index and explicit reasons, but supply no partial model input.
        if result["preparation_ready"]:
            result["documents"] = selected
            result["status"] = "partial" if result["issues"] else "ready"
        else:
            result["status"] = "incomplete"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["status"] = "incomplete"
        result["issues"].append({"source": "opening selection", "reason": type(exc).__name__ + ": " + str(exc)})
    return result
