"""Shared read-only view of admitted documents and the existing compiler's facts.

No acquisition, new accounting parser, model generation or approval lives here.
The two orchestrators supply their already acquired qualification/model payload.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import re


class DossierUnavailable(LookupError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def _catalog(documents, as_of):
    from .input_preparation import _catalog as compile_catalog, _day
    catalog, issues, _ = compile_catalog(documents, _day(as_of))
    if issues:
        raise ValueError("Dossier source verification failed: " + "; ".join(row['reason'] for row in issues))
    return catalog


def _dossier(bundle, documents, report, plan, *, source_fingerprint=None, generation_id=None):
    from .input_preparation import prepare_method_inputs
    from .sector_analysis import validate_bundle
    bundle = validate_bundle(bundle, bundle['case']['ticker'])
    catalog = _catalog(documents, bundle['case']['as_of'])
    issues, records = [], []
    if isinstance(plan, dict) and plan:
        prepared = prepare_method_inputs(bundle, documents=documents,
            propose=lambda *_: deepcopy(plan), source_report={**report, 'source_plan': plan})
        issues = deepcopy(prepared['issues'])
        # A compiler proposal is not a verified observation. Partial dossiers
        # retain the admitted documents and diagnostics, never promote a draft.
        records = deepcopy(prepared['bundle']['case']['records'] if prepared['status'] == 'prepared' else [])
    else:
        issues = [{'field': 'plan', 'code': 'unavailable',
                   'reason': 'Admitted documents are available; no compiled accounting observations are present.'}]
    entries = []
    for record in records:
        ids = record['source_locator'].split(',')
        if not ids or any(ident not in catalog for ident in ids):
            raise ValueError("Canonical record refers to a source outside the admitted catalog")
        entries.append({'record': record, 'sources': [
            {key: deepcopy(catalog[ident].get(key)) for key in
             ('id', 'url', 'sha256', 'document_sha256', 'published_at', 'metadata')}
            for ident in ids]})
    return {'contract': 'company_dossier/1', 'ticker': bundle['case']['ticker'],
        'research_as_of': bundle['case']['as_of'], 'method_id': bundle['decision']['method_id'],
        'source_fingerprint': source_fingerprint, 'generation_id': generation_id,
        'status': 'partial' if issues else 'compiled', 'issues': issues,
        'records': entries, 'documents': deepcopy(list(catalog.values())),
        'research_complete': False, 'approval_status': 'not_PM_approved',
        'limitation': 'Historical observations, company guidance and analyst estimates retain their own kind. '
                      'Document access is not complete research, current market validation or trading approval.'}


def dossier_from_qualification(qualification):
    from .trade_idea_model import source_fingerprint
    if (not isinstance(qualification, dict) or not qualification.get('fingerprint')
            or qualification['fingerprint'] != source_fingerprint(qualification)):
        raise ValueError('Qualified source fingerprint differs')
    report = qualification.get('source_report') or {}
    if qualification.get('status') == 'research_required' and not report.get('documents'):
        from .trade_idea_model import validate_research_admission
        validate_research_admission(qualification)
        return {'contract': 'company_dossier/1', 'ticker': qualification['ticker'],
            'research_as_of': qualification['as_of'], 'method_id': qualification.get('method_id'),
            'source_fingerprint': qualification['fingerprint'], 'generation_id': None,
            'status': 'research_required', 'documents': [], 'records': [],
            'issues': [{'field': 'documents', 'code': 'research_required',
                'reason': 'Acquire and read official company statements during R0/R1 before compiling the model.'}],
            'research_complete': False, 'approval_status': 'not_PM_approved',
            'limitation': 'Admission authorizes research; no financial observation or model is approved.'}
    plan = report.get('source_plan') or report.get('historical_preparation_plan')
    return _dossier(qualification['bundle'], report.get('documents') or [], report, plan,
                    source_fingerprint=qualification['fingerprint'])


def dossier_from_payload(payload):
    from .preparation_seed import restore_seed
    from .sector_analysis import validate_bundle
    prepared = payload.get('preparation') or {}
    basis = prepared.get('review_basis') or {}
    source = basis.get('dossier') or {}
    if not basis or prepared.get('status') != 'prepared' or not payload.get('generation_id'):
        raise ValueError('Verified preparation basis and generation are unavailable')
    plan = restore_seed(basis.get('seed'), source, basis.get('contract'))
    if _json(plan) != _json((prepared.get('proposal') or {}).get('plan')):
        raise ValueError('Preparation plan differs from its exact seed')
    bundle = validate_bundle(payload.get('acquisition_snapshot'), payload['ticker'])
    if (source.get('ticker') != payload['ticker'] or source.get('as_of') != bundle['case']['as_of']
            or source.get('method_id') != bundle['decision']['method_id']):
        raise ValueError('Dossier ticker, date or method differs from the model generation')
    result = _dossier(bundle, source.get('documents') or [], source.get('document_acquisition') or {},
                      plan, generation_id=payload['generation_id'])
    compiled = [item['record'] for item in result['records']]
    if result['issues'] or Counter(map(_json, compiled)) != Counter(map(_json, bundle['case']['records'])):
        raise ValueError('Model observations differ from the recompiled dossier or its source proofs')
    return result


def _with_acquisition_diagnostics(dossier, snapshot):
    """Expose retained decisions, never promote them to accepted documents."""
    from bellomberg.agents.company_source_research import SOURCE_RECOVERY_INSTRUCTION
    if snapshot.get('ticker') != dossier['ticker'] or snapshot.get('as_of') != dossier['research_as_of']:
        raise ValueError('Research diagnostics differ from the dossier identity or date')
    verified = {(doc['url'], doc.get('document_sha256')) for doc in snapshot.get('documents') or []}
    diagnostics = []
    for failure in snapshot.get('failures') or []:
        rows = (failure.get('receipt') or {}).get('documents') or []
        if rows:
            for row in rows:
                url = row.get('url') or (row.get('request') or {}).get('url')
                recovered = (url, row.get('sha256')) in verified and row.get('sha256') is not None
                diagnostics.append({'status': 'retained_rejection_with_verified_bytes' if recovered else 'needs_verification',
                    'url': url, 'reason': row.get('reason') or failure.get('reason'),
                    'recovery_instruction': ('The same document bytes are now verified in this dossier; '
                        'reuse the admitted document. The earlier rejection is retained unchanged.'
                        if recovered else SOURCE_RECOVERY_INSTRUCTION)})
        else:
            diagnostics.append({'status': 'acquisition_failed', 'url': failure.get('url'),
                'reason': failure.get('reason'), 'recovery_instruction':
                    'The failed acquisition is retained; no automatic repeat. Metadata claims cannot repair a failed transport or catalog receipt.'})
    diagnostics.extend({'status': 'pending_request', 'url': pending.get('url'),
        'reason': pending.get('reason'), 'recovery_instruction':
            'The request has no durable response; no automatic repeat. Report the unresolved acquisition; metadata claims cannot recover missing bytes.'}
        for pending in snapshot.get('pending_requests') or [])
    if diagnostics:
        dossier['acquisition_diagnostics'] = diagnostics
    return dossier


def _research_dossier_for_board(blackboard, ticker):
    """Verified source memory; never reconstruct a model or compile assumptions."""
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    qualification = getattr(blackboard, 'source_qualification', None)
    snapshot = bridge = None
    bridge_refs = []
    if qualification is not None:
        from .trade_idea_model import source_fingerprint
        if (qualification.get('ticker') != ticker or not qualification.get('fingerprint')
                or qualification['fingerprint'] != source_fingerprint(qualification)):
            raise ValueError('Research source identity or fingerprint differs')
        documents = (qualification.get('source_report') or {}).get('documents') or []
        as_of = qualification['as_of']
        fingerprint = qualification['fingerprint']
        resolver = getattr(blackboard, 'company_source_session', None)
        if callable(resolver):
            pin = (blackboard.data.get('_source_research') or {}).get('revision_id')
            snapshot = resolver(ticker).snapshot(**({'expected_revision_id': pin} if pin is not None else {}))
            admission = getattr(blackboard, 'source_admission', None)
            if admission and snapshot.get('parent_grant_fingerprint') != admission.get('fingerprint'):
                raise ValueError('Research source grant differs from its accepted original')
            revision = ({key: deepcopy(snapshot.get(key)) for key in (
                'run_id', 'ticker', 'as_of', 'revision_id', 'revision_sha256', 'parent_grant_fingerprint')}
                if snapshot.get('revision_id') is not None else None)
            if qualification.get('research_revision') != revision:
                raise ValueError('Research source revision differs from the admitted source dossier')
            # Admission may already contain verified PM documents. Both it and
            # the append-only session are normalized by the qualification step;
            # compare that exact union, retaining every source hash and byte.
            from .preparation_service import merge_research_sources
            expected = merge_research_sources((admission or {}).get('source_report') or {}, snapshot)
            expected_catalog = _catalog(expected['documents'], as_of) if expected['documents'] else {}
            admitted_catalog = _catalog(documents, as_of) if documents else {}
            if expected_catalog != admitted_catalog:
                raise ValueError('Research document revision differs from the admitted source dossier')
        # APERTO-TI: ponte Filing anche nella Trade Idea (ricevuta nei dati della run, pre-R0).
        # None = run senza ponte (codice precedente): dossier di prima, byte per byte.
        from bellomberg.agents.ponte_filing_dossier import documenti_ponte
        bridge = documenti_ponte(blackboard, ticker)
        if bridge is not None:
            bridge_documents, bridge_declaration = bridge
            if bridge_documents and as_of != blackboard.data['_filing_bridge']['as_of']:
                raise ValueError('Filing bridge cutoff differs from the accepted research admission')
            acquired = {doc.get('id') for doc in documents}
            bridge_declaration['gia_acquisiti_dal_desk'] = [doc['id'] for doc in bridge_documents if doc['id'] in acquired]
            bridge_refs = [doc for doc in bridge_documents if doc['id'] not in acquired]
    else:
        # V1-PONTE: documenti gia' verificati dall'archivio Filing, ammessi prima di R0 con ricevuta.
        # None = run senza ponte (codice precedente) o titolo fuori perimetro: dossier di prima.
        from bellomberg.agents.ponte_filing_dossier import documenti_ponte
        bridge = documenti_ponte(blackboard, ticker)
        resolver = getattr(blackboard, 'company_source_session_if_known', None)
        if not callable(resolver) and bridge is None:
            raise DossierUnavailable('No admitted company research is available for this ticker in the run')
        session = resolver(ticker) if callable(resolver) else None
        if session is None and bridge is None:
            raise DossierUnavailable('No company documents were acquired for this ticker; coverage is unavailable')
        if session is not None:
            pin = ((blackboard.data.get('_company_research') or {}).get(ticker) or {}).get('revision_id')
            snapshot = session.snapshot(**({'expected_revision_id': pin} if pin is not None else {}))
            if snapshot.get('ticker') != ticker:
                raise ValueError('Company research snapshot belongs to a different ticker')
            documents, as_of = snapshot.get('documents') or [], snapshot['as_of']
            fingerprint = snapshot.get('revision_sha256')
        else:
            documents, as_of, fingerprint = [], blackboard.data['_filing_bridge']['as_of'], None
        if bridge is not None:
            bridge_documents, bridge_declaration = bridge
            if bridge_documents and as_of != blackboard.data['_filing_bridge']['as_of']:
                raise ValueError('Filing bridge cutoff differs from the company research session')
            # Stessi byte gia' acquisiti dal desk: resta il documento del desk, nessun duplicato.
            acquired = {doc.get('id') for doc in documents}
            bridge_declaration['gia_acquisiti_dal_desk'] = [doc['id'] for doc in bridge_documents if doc['id'] in acquired]
            # Riferimenti SENZA testo dalla sola ricevuta: il sigillo non rilegge mai l'archivio vivo.
            bridge_refs = [doc for doc in bridge_documents if doc['id'] not in acquired]
            fingerprint = fingerprint or bridge_declaration['entry_sha256']
    catalog = _catalog(documents, as_of) if documents else {}
    if bridge is not None:
        catalog.update({doc['id']: deepcopy(doc) for doc in bridge_refs})
    result = {'contract': 'company_dossier/1', 'analysis_mode': RESEARCH_ANALYSIS_MODE,
        'ticker': ticker, 'research_as_of': as_of, 'method_id': None,
        'source_fingerprint': fingerprint, 'generation_id': None,
        'status': 'research' if catalog else 'research_required',
        'issues': [] if catalog else [{'field': 'documents', 'code': 'unavailable',
            'reason': 'No verified company document is available; disclose this research limitation.'}],
        'records': [], 'documents': deepcopy(list(catalog.values())),
        'research_complete': False, 'approval_status': 'not_PM_approved',
        'limitation': 'Verified documents are source evidence, not approval of financial facts, '
                      'independent assumptions, analytical completeness or an operational proposal.'}
    if snapshot is not None:
        result['research_revision'] = {key: deepcopy(snapshot.get(key)) for key in
            ('revision_id', 'revision_sha256', 'parent_grant_fingerprint')}
        result = _with_acquisition_diagnostics(result, snapshot)
    if bridge is not None:
        result['filing_bridge'] = bridge_declaration
        if not catalog and bridge_declaration['esito'] == 'non_applicabile':
            # Decisione PM 06/10: ETF/ETN/fondi non hanno un bilancio societario: non e' una ricerca mancante.
            result['status'] = 'non_applicabile'
            result['issues'] = [{'field': 'documents', 'code': 'non_applicabile',
                                 'reason': '; '.join(bridge_declaration['motivi'])}]
            non_usati = bridge_declaration.get('verificati_non_usati') or {}
            if non_usati.get('conteggio') != 0 and 'conteggio' in non_usati:
                # Documenti verificati di uno strumento non societario: non ammessi ma mai persi in silenzio.
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_verificati_non_usati',
                    'reason': ("%s documenti verificati dall'archivio Filing ma non usati: strumento %s (scelta PM)"
                               % ('n.d.' if non_usati['conteggio'] is None else non_usati['conteggio'],
                                  bridge_declaration.get('tipo_strumento'))
                               + ('' if non_usati['conteggio'] is not None else '; ' + non_usati['motivo']))})
        elif not catalog:
            result['issues'].append({'field': 'documents', 'code': 'filing_archive_' + str(bridge_declaration['esito']),
                'reason': 'Archivio Filing: ' + ('; '.join(bridge_declaration['motivi']) or str(bridge_declaration['esito']))})
        elif bridge_declaration['motivi']:
            # Archivio non aggiornato / run in errore / repository fermo: dichiarato accanto ai documenti.
            result['issues'].append({'field': 'documents', 'code': 'filing_archive_limitation',
                'reason': 'Archivio Filing: ' + '; '.join(bridge_declaration['motivi'])
                          + ' (periodo piu\' recente ammesso: ' + str(bridge_declaration['periodo_piu_recente']) + ')'})
        for doc in bridge_refs:
            eta = doc['metadata']['filing_bridge']
            if eta.get('verifica') == 'non_verificato':
                # Decisione PM 06/10: ammesso con etichetta, leggibile e citabile, mai fonte primaria verificata.
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_documento_non_verificato',
                    'document_id': doc['id'], 'reason': ('Documento %s del periodo %s ammesso ma %s; leggibile e '
                    "citabile come tale, NON conta come fonte primaria verificata per la proposta operativa."
                    % (doc['metadata'].get('form') or eta.get('tipo_documento') or 'archiviato',
                       eta.get('periodo') or 'n.d.' if eta.get('periodo_stato') != 'da_confermare'
                       else str(eta.get('periodo') or 'n.d.') + ' (periodo da confermare)',
                       eta.get('etichetta_verifica') or 'non verificato'))})
            if eta.get('eta_giorni_al_cutoff') is None:
                from bellomberg.agents.ponte_filing_dossier import eta_dichiarata
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_eta_non_valutabile',
                    'document_id': doc['id'], 'reason': 'Documento senza data di fine periodo: eta\' '
                    + str(eta_dichiarata(eta)) + '; soglia di eta\' PM 06/10 non applicabile.'})
                continue
            if eta['esercizio_successivo_chiuso']:
                # Fatto di calendario, nessuna soglia di giudizio: la soglia di obsolescenza la decide il PM.
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_esercizio_successivo_chiuso',
                    'document_id': doc['id'], 'reason': ('Documento del periodo %s: al cutoff %s (eta %d giorni) '
                    "si e' gia' chiuso il periodo omologo successivo (%s); NON e' il periodo piu' recente chiuso."
                    % (eta['periodo_fine'], as_of, eta['eta_giorni_al_cutoff'], eta['fine_esercizio_successivo']))})
            if eta.get('corrente') is False:
                # Soglia PM 06/10 (nella ricevuta): il documento resta ammesso ma e' dichiarato non corrente.
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_documento_non_corrente',
                    'document_id': doc['id'], 'reason': ('Documento %s del periodo %s NON corrente al cutoff %s: '
                    'corrente fino al %s (%d mesi, soglia PM 06/10); ammesso e dichiarato.'
                    % (eta['tipo_periodo'], eta['periodo_fine'], as_of, eta['corrente_fino_al'], eta['soglia_mesi']))})
            elif 'corrente' in eta and eta['corrente'] is None:
                result['issues'].append({'field': 'documents', 'code': 'filing_archive_eta_non_valutabile',
                    'document_id': doc['id'], 'reason': ('Documento del periodo %s: tipo di periodo non noto, '
                    'soglia di eta\' PM 06/10 non applicabile (eta %d giorni).'
                    % (eta['periodo_fine'], eta['eta_giorni_al_cutoff']))})
    if qualification is not None:
        # PM-supplied unreadable documents precede the ResearchSession journal.
        # Keep their source and rejection beside later successful acquisitions;
        # never promote an unverified pointer to admitted financial evidence.
        result['issues'].extend(deepcopy((qualification.get('source_report') or {}).get('issues') or []))
        for row in (qualification.get('document_receipt') or {}).get('documents') or []:
            if row.get('status') != 'verified':
                result.setdefault('acquisition_diagnostics', []).append({
                    'status': row.get('status'), 'financial_evidence': False,
                    'source': 'pm_document_receipt', 'url': row.get('url') or (row.get('request') or {}).get('url'),
                    'reason': row.get('reason'), 'recovery_instruction':
                        'This PM source remains unverified and outside the admitted catalog. '
                        'Report the gap; further research cannot infer financial facts from this pointer.'})
    # FRESCHEZZA (06/10/2026): ultimo periodo pubblicato dalla cascata della fase documenti pre-R0
    # (record nel checkpoint). Record assente o di un altro titolo: dossier di prima, byte per byte.
    fase = (getattr(blackboard, 'data', None) or {}).get('_filing_titolo_nuovo')
    if isinstance(fase, dict) and fase.get('ticker') == ticker:
        from bellomberg.market_data.freschezza_trimestrale import sezione_dossier
        sezione = sezione_dossier((fase.get('passi') or {}).get('freschezza'))
        if sezione is not None:
            result['ultimo_periodo_pubblicato'] = sezione
            if sezione.get('lacuna'):
                result['issues'].append({'field': 'latest_period', 'code': 'ultimo_periodo_lacuna',
                    'reason': 'Ultimo periodo pubblicato non trovato: ' + str(sezione['lacuna'])})
            elif sezione.get('livello') == 4:
                result['issues'].append({'field': 'latest_period', 'code': 'ultimo_periodo_da_fornitore',
                    'reason': 'Numeri del periodo %s dal fornitore dati (non filing): citarli con questa etichetta.'
                              % sezione.get('periodo_trovato')})
    return result


def dossier_for_board(blackboard, ticker):
    if not isinstance(ticker, str) or not ticker.strip():
        raise ValueError('An exact ticker is required')
    from bellomberg.core.research_analysis import is_research_mode
    if is_research_mode(blackboard):
        return _research_dossier_for_board(blackboard, ticker)
    qualification = getattr(blackboard, 'source_qualification', None)
    payload = (getattr(blackboard, 'valuation_results', {}) or {}).get(ticker)
    if qualification is not None:
        if qualification.get('ticker') != ticker:
            raise ValueError('Dossier ticker differs from the accepted candidate')
        admitted = dossier_from_qualification(qualification)
        if not isinstance(payload, dict) or not payload.get('generation_id'):
            resolver = getattr(blackboard, 'company_source_session', None)
            if callable(resolver):
                pin = ((getattr(blackboard, 'data', {}) or {}).get('_source_research') or {}).get('revision_id')
                session = resolver(ticker)
                snapshot = session.snapshot(**({'expected_revision_id': pin} if pin is not None else {}))
                original = getattr(blackboard, 'source_admission', None)
                if original and snapshot.get('parent_grant_fingerprint') != original.get('fingerprint'):
                    raise ValueError('Research diagnostics differ from the original source grant')
                return _with_acquisition_diagnostics(admitted, snapshot)
            return admitted
        result = dossier_from_payload(payload)
        def documents_pin(dossier):
            return Counter(_json({key: doc.get(key) for key in (
                'id', 'sha256', 'document_sha256', 'url', 'published_at', 'metadata')})
                for doc in dossier['documents'])
        if (result['ticker'] != admitted['ticker'] or result['research_as_of'] != admitted['research_as_of']
                or result['method_id'] != admitted['method_id'] or documents_pin(result) != documents_pin(admitted)):
            raise ValueError('Compiled dossier differs from the admitted identity, date or sources')
        result['source_fingerprint'] = admitted['source_fingerprint']
        return result
    if not isinstance(payload, dict) or (payload.get('preparation') or {}).get('status') != 'prepared':
        resolver = getattr(blackboard, 'company_source_session', None)
        if not callable(resolver):
            raise DossierUnavailable('No admitted dossier is available for this ticker in the run')
        snapshot = resolver(ticker).snapshot()
        documents = snapshot.get('documents') or []
        catalog = _catalog(documents, snapshot['as_of']) if documents else {}
        return _with_acquisition_diagnostics({'contract': 'company_dossier/1', 'ticker': ticker,
            'research_as_of': snapshot['as_of'], 'method_id': None,
            'source_fingerprint': snapshot.get('revision_sha256'), 'generation_id': None,
            'status': 'research', 'issues': [{'field': 'model', 'code': 'not_compiled',
                'reason': 'Official documents are shared; financial observations are not yet compiled.'}],
            'records': [], 'documents': deepcopy(list(catalog.values())),
            'research_complete': False, 'approval_status': 'not_PM_approved',
            'limitation': 'Document verification does not approve financial facts, assumptions or a valuation.',
            'research_revision': {key: deepcopy(snapshot.get(key)) for key in
                ('revision_id', 'revision_sha256', 'parent_grant_fingerprint')}}, snapshot)
    return dossier_from_payload(payload)


def _page(text, input_, metadata, max_chars):
    offset = input_.get('offset', 0)
    if type(offset) is not int or not 0 <= offset < len(text):
        raise ValueError('offset must address the exact admitted text')
    query = input_.get('query')
    match_offset = None
    if query is not None:
        if not isinstance(query, str) or not query.strip() or not 1 <= len(query) <= 200:
            raise ValueError('query must be a nonempty literal of at most 200 characters')
        match = re.compile(re.escape(query), re.I).search(text, offset)
        if match is None:
            return {'ok': False, 'status': 'literal_not_found', **metadata, 'offset': offset,
                    'error': 'Literal not present at or after this text offset'}
        match_offset = match.start()
        offset = max(offset, match_offset - 300)

    def page(end):
        return {'ok': True, **metadata, 'offset': offset, 'next_offset': end,
                'total_chars': len(text), 'complete': end == len(text),
                'match_offset': match_offset, 'text': text[offset:end]}

    low, high = offset, min(len(text), offset + max_chars)
    while low < high:
        midpoint = (low + high + 1) // 2
        if len(json.dumps(page(midpoint), ensure_ascii=False)) + 256 <= max_chars:
            low = midpoint
        else:
            high = midpoint - 1
    if low == offset or match_offset is not None and low <= match_offset:
        raise ValueError('Source fragment cannot fit the unchanged tool-result limit')
    return page(low)


def read_qualified_document(qualification, input_, max_chars):
    """Compatibility entry for Trade Idea's original paginated reader."""
    try:
        from .trade_idea_model import source_fingerprint
        if qualification.get('fingerprint') != source_fingerprint(qualification):
            raise ValueError('Qualified source fingerprint differs')
        catalog = _catalog((qualification.get('source_report') or {}).get('documents') or [],
                           qualification['as_of'])
        return _read_document(catalog, input_, max_chars, qualification['fingerprint'])
    except (ValueError, TypeError, KeyError) as exc:
        return {'ok': False, 'error': str(exc)}


def _read_document(catalog, input_, max_chars, fingerprint):
    document = catalog.get(input_.get('document_id'))
    if not document:
        raise ValueError('One exact qualified document ID is required')
    text = document.get('text')
    if not isinstance(text, str) or not text.strip() or sha256(text.encode()).hexdigest() != document.get('sha256'):
        raise ValueError('Qualified document text is absent or its SHA-256 differs')
    metadata = {key: document.get(key) for key in ('sha256', 'url', 'published_at')}
    metadata.update(document_id=document['id'], source_fingerprint=fingerprint,
                    provenance='Exact admitted primary text; no new acquisition or economic approval')
    ponte = (document.get('metadata') or {}).get('filing_bridge') or {}
    if ponte.get('verifica') == 'non_verificato':
        # APERTO-TI: etichetta onesta in ogni pagina letta; la ricevuta del tool non attesta numeri operativi.
        metadata.update(fonte_primaria_verificata=False, etichetta=ponte.get('etichetta_verifica'),
                        periodo_stato=ponte.get('periodo_stato'),
                        provenance='Archived issuer document NOT verified (' + str(ponte.get('etichetta_verifica'))
                                   + '); readable and citable as such, not verified primary evidence')
    return _page(text, input_, metadata, max_chars)


def read_company_dossier(blackboard, input_, max_chars):
    try:
        ticker = input_.get('ticker') or getattr(blackboard, 'target_ticker', None)
        dossier = dossier_for_board(blackboard, ticker)
        section = input_.get('section', 'catalog')
        if section == 'document':
            documents = {doc['id']: doc for doc in dossier['documents']}
            chosen = documents.get(input_.get('document_id'))
            if chosen is not None and 'text' not in chosen and (chosen.get('metadata') or {}).get('filing_bridge'):
                # V1-PONTE: il sigillo tiene il documento per riferimento; byte e testo si riverificano ORA.
                from bellomberg.agents.ponte_filing_dossier import testo_documento, DocumentoPonteNonDisponibile
                try:
                    documents[chosen['id']] = {**chosen, 'text': testo_documento(blackboard, chosen)}
                except DocumentoPonteNonDisponibile as exc:
                    return {'ok': False, 'status': 'unavailable', 'declared_gap': True, 'requires_acquisition': False,
                            'document_id': chosen['id'], 'expected_sha256': chosen['document_sha256'],
                            'reason': str(exc) + '. Dichiara la lacuna di questo documento; la run prosegue.'}
            return _read_document(documents, input_, max_chars, dossier['source_fingerprint'])
        if section not in ('catalog', 'records'):
            raise ValueError('section must be catalog, records or document')
        body = dossier['records'] if section == 'records' else {
            key: value for key, value in dossier.items() if key not in ('documents', 'records')}
        if section == 'catalog':
            body['documents'] = [{key: deepcopy(doc.get(key)) for key in
                ('id', 'url', 'sha256', 'published_at', 'metadata')} for doc in dossier['documents']]
            body['records_count'] = len(dossier['records'])
        return _page(_json(body), input_, {'ticker': ticker, 'section': section,
                     'contract': dossier['contract'], 'source_fingerprint': dossier['source_fingerprint'],
                     'dossier_status': dossier['status'], 'generation_id': dossier['generation_id'],
                     'research_as_of': dossier['research_as_of'], 'issues_count': len(dossier['issues'])}, max_chars)
    except DossierUnavailable as exc:
        return {'ok': False, 'status': 'unavailable', 'reason': str(exc), 'requires_acquisition': True}
    except (ValueError, TypeError, KeyError) as exc:
        return {'ok': False, 'status': 'invalid', 'reason': str(exc), 'requires_acquisition': False}


def company_dossier_tool_schema():
    return {'name': 'read_company_dossier',
        'description': 'Read the shared admitted company dossier without new acquisition or AI. '
                       'Start with catalog, then page records or one exact document. Historical facts, '
                       'guidance and estimates remain distinct. Missing dossiers are explicit; model writing is separate.',
        'input_schema': {'type': 'object', 'properties': {
            'ticker': {'type': 'string'}, 'section': {'type': 'string', 'enum': ['catalog', 'records', 'document']},
            'document_id': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0},
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
            'required': ['ticker'], 'additionalProperties': False}}
