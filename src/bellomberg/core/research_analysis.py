"""Versioned research identity, independent of any valuation workbook.

The seal records observed evidence and actual desk reports; it creates neither
financial assumptions nor a valuation. Archived runs without a mode stay legacy.
"""
from copy import deepcopy
from hashlib import sha256
import json

RESEARCH_ANALYSIS_MODE = 'fundamentals_research_v1'
RESEARCH_COMPLETION_CONTRACT = 'research-memo-pdf/1'


def is_research_mode(value):
    if isinstance(value, dict):
        return value.get('analysis_mode') == RESEARCH_ANALYSIS_MODE
    return getattr(value, 'analysis_mode', None) == RESEARCH_ANALYSIS_MODE


def research_digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _current_dossiers(blackboard, tickers):
    from bellomberg.valuation.company_dossier import dossier_for_board, DossierUnavailable
    dossiers = {}
    for ticker in tickers:
        try:
            dossiers[ticker] = dossier_for_board(blackboard, ticker)
        except DossierUnavailable as exc:
            # Missing issuer research is an explicit limitation, not a fabricated
            # document or an exception mask for corrupt/changed source evidence.
            dossiers[ticker] = {'ticker': ticker, 'status': 'unavailable',
                'documents': [], 'records': [], 'issues': [{'reason': str(exc)}]}
    return dossiers


def seal_research_thesis(blackboard, *, desks=None, missing=None):
    if not is_research_mode(blackboard):
        raise ValueError('Research seal requires its explicit analysis mode')
    if blackboard.data.get('_research_thesis') is not None:
        research_reference(blackboard)
        return deepcopy(blackboard.data['_research_thesis'])
    tickers = ([blackboard.target_ticker] if getattr(blackboard, 'target_ticker', None)
        else [*getattr(blackboard, 'research_tickers', ()), *(blackboard.data.get('_company_research') or {})])
    if (not tickers and getattr(blackboard, 'run_scope', None) != 'weekly'
            or any(not isinstance(t, str) or not t for t in tickers)):
        raise ValueError('Research dossier identity unavailable')
    tickers = sorted(set(tickers))
    if desks is None:
        desks = ('macro', 'eventdesk', 'crypto', 'fundamentals', 'quant', 'options')
    reports = {}
    for desk in desks:
        report = blackboard.read(desk, 1)
        if not isinstance(report, str) or not report.strip() or report.startswith('[ERROR'):
            raise ValueError('Research report incomplete or unavailable: ' + desk)
        reports[desk] = report
    dossiers = _current_dossiers(blackboard, tickers)
    evidence = deepcopy(getattr(blackboard, 'tool_receipts', []))
    payload = {'analysis_mode': RESEARCH_ANALYSIS_MODE, 'version': 1,
        'scope_status': 'companies_in_scope' if tickers else 'no_company_in_scope',
        'dossiers': dossiers, 'reports': reports, 'tool_receipts': evidence}
    # A desk the caller declares missing is sealed as an explicit gap (with its reason),
    # never as a report; seals without gaps keep their historical hash.
    if missing:
        payload['missing_reports'] = {str(desk): str(reason) for desk, reason in dict(missing).items()}
    payload['dossier_sha256'] = research_digest({'dossiers': dossiers, 'tool_receipts': evidence})
    payload['thesis_sha256'] = research_digest(_thesis_identity(payload))
    with blackboard._lock:
        blackboard.data['_research_thesis'] = deepcopy(payload)
    return deepcopy(payload)


def _thesis_identity(sealed):
    identity = {'reports': sealed.get('reports'), 'dossier_sha256': sealed.get('dossier_sha256')}
    if 'missing_reports' in sealed:
        identity['missing_reports'] = sealed['missing_reports']
    return identity


def research_reference(blackboard):
    if not is_research_mode(blackboard):
        raise ValueError('Research reference requires its explicit analysis mode')
    sealed = blackboard.data.get('_research_thesis')
    if not isinstance(sealed, dict) or not is_research_mode(sealed) or sealed.get('version') != 1:
        raise ValueError('Research seal unavailable or version differs')
    dossiers, reports = sealed.get('dossiers'), sealed.get('reports')
    if (not isinstance(dossiers, dict) or not isinstance(reports, dict) or not reports
            or not dossiers and (getattr(blackboard, 'run_scope', None) != 'weekly'
                or sealed.get('scope_status') != 'no_company_in_scope')):
        raise ValueError('Research seal integrity differs')
    if (sealed.get('dossier_sha256') != research_digest({'dossiers': dossiers,
            'tool_receipts': sealed.get('tool_receipts')})
            or sealed.get('thesis_sha256') != research_digest(_thesis_identity(sealed))):
        raise ValueError('Research seal integrity differs')
    if any(blackboard.read(desk, 1) != report for desk, report in reports.items()):
        raise ValueError('Research thesis changed after the committee seal')
    if research_digest(_current_dossiers(blackboard, sorted(dossiers))) != research_digest(dossiers):
        raise ValueError('Research dossier changed after the committee seal')
    return {key: sealed[key] for key in ('analysis_mode', 'version', 'dossier_sha256', 'thesis_sha256')}


def research_context(blackboard):
    reference = research_reference(blackboard)
    sealed = blackboard.data['_research_thesis']
    dossiers = deepcopy(sealed['dossiers'])
    # Exact primary text stays in the shared paginated source reader. The
    # committee receives an identical inventory and immutable thesis reference.
    for dossier in dossiers.values():
        for document in dossier.get('documents') or []:
            document.pop('text', None)
    # V1-PONTE: un dossier col ponte Filing entra nel prompt come INDICE compatto (niente testo,
    # percorsi o liste di esclusi); il dettaglio resta in read_company_dossier section=catalog.
    if any(isinstance(dossier, dict) and 'filing_bridge' in dossier for dossier in dossiers.values()):
        from bellomberg.agents.ponte_filing_dossier import indice_compatto
        dossiers = {ticker: indice_compatto(dossier) if 'filing_bridge' in dossier else dossier
                    for ticker, dossier in dossiers.items()}
    return {'research_ref': reference, 'dossiers': dossiers,
        'reports': deepcopy(sealed['reports']),
        'instruction': 'Discuss this exact research dossier and thesis. Observations, management guidance, '
            'analyst consensus and independent assumptions remain distinct. Missing sources or consensus '
            'must be declared. No workbook or AI fair value is required. Source text remains available '
            'through read_company_dossier; this seal is not evidence of economic completeness or approval.'}


def build_research_action_bindings(sealed, *, run_id, memo_markdown, cutoff):
    """Bind proposals to the sealed source inventory, not to semantic truth."""
    from bellomberg.agents.action_table_extract import parse_action_table_rows
    reference = _binding_seal_reference(sealed)
    cutoff_day = _binding_day(cutoff)
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError('Research binding run identity unavailable')
    envelope = {'version': 1, 'run_id': run_id, 'cutoff': cutoff,
                'memo_sha256': sha256(memo_markdown.encode('utf-8')).hexdigest(),
                'research_ref': reference, 'rows': []}
    reports = {desk: research_digest(report) for desk, report in sealed['reports'].items()}
    for row in parse_action_table_rows(memo_markdown).get('rows') or []:
        if row['action'] not in ('BUY', 'ADD'):
            continue
        binding = {**_binding_row_identity(row), 'status': 'CHECK_UNAVAILABLE',
                   'reason': '', 'documents': [], 'receipts': [], 'reports': reports}
        envelope['rows'].append(binding)
        ticker = binding['ticker']
        dossier = sealed['dossiers'].get(ticker)
        if not isinstance(dossier, dict) or dossier.get('ticker') != ticker:
            binding['reason'] = 'dossier assente o ticker del dossier diverso'
            continue
        binding['dossier_sha256'] = research_digest(dossier)
        binding['limitations'] = deepcopy(dossier.get('issues') or [])
        binding['missing_reports'] = deepcopy(sealed.get('missing_reports') or {})
        rejected = []
        for doc in dossier.get('documents') or []:
            try:
                availability = _binding_document(doc, cutoff_day)
                binding['documents'].append({'id': doc['id'], 'sha256': doc['sha256'],
                    'url': doc['url'], **availability})
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append(str(exc))
        if dossier.get('status') not in ('research', 'compiled', 'partial') or not binding['documents']:
            binding['reason'] = 'fonti dossier non disponibili: ' + ('; '.join(rejected) or str(dossier.get('status')))
            continue
        for index, receipt in enumerate(sealed['tool_receipts']):
            try:
                source, dates = _binding_receipt(receipt, ticker, dossier, binding['documents'], cutoff_day)
                binding['receipts'].append({'index': index, 'sha256': research_digest(receipt),
                    'tool': receipt['tool'], 'source': source, 'dating': dates})
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append(str(exc))
        binding['excluded_sources'] = sorted(set(rejected))
        if not binding['receipts']:
            binding['reason'] = 'ricevute sigillate non disponibili: ' + ('; '.join(sorted(set(rejected))) or 'nessuna ricevuta')
            continue
        binding.update(status='AVAILABLE', reason='provenienza della proposta legata alla ricerca sigillata; '
                       'non verifica della verita semantica o approvazione PM')
    envelope['sha256'] = research_digest(envelope)
    return envelope


def _binding_seal_reference(sealed):
    """Pure verification of the inventory. The caller verifies board identity too."""
    if (not isinstance(sealed, dict) or not is_research_mode(sealed) or sealed.get('version') != 1
            or not isinstance(sealed.get('dossiers'), dict) or not isinstance(sealed.get('tool_receipts'), list)
            or not isinstance(sealed.get('reports'), dict) or not sealed['reports']
            or any(not isinstance(report, str) or not report.strip() or report.startswith('[ERROR')
                   for report in sealed['reports'].values())
            or sealed.get('dossier_sha256') != research_digest({'dossiers': sealed.get('dossiers'),
                'tool_receipts': sealed.get('tool_receipts')})
            or sealed.get('thesis_sha256') != research_digest(_thesis_identity(sealed))):
        raise ValueError('Research binding seal integrity differs')
    return {key: sealed[key] for key in ('analysis_mode', 'version', 'dossier_sha256', 'thesis_sha256')}


def _binding_day(value):
    from datetime import date
    if not isinstance(value, str):
        raise ValueError('data fonte/cutoff assente')
    return date.fromisoformat(value[:10])


def _binding_row_identity(row):
    return {'row_index': row['row_index'], 'action': row['action'].upper(),
            'ticker': row['ticker'].upper(), 'row_sha256': research_digest(row['raw'])}


def _binding_document(doc, cutoff):
    import re
    from bellomberg.valuation.document_evidence import source_dates
    if not isinstance(doc, dict) or not doc.get('id') or not doc.get('url'):
        raise ValueError('identita fonte documento assente')
    bridge = (doc.get('metadata') or {}).get('filing_bridge') or {}
    if bridge.get('verifica') == 'non_verificato' or doc.get('source_verified') is False:
        raise ValueError('fonte documento non verificata')
    if bridge.get('corrente') is False or str(doc.get('status') or '').upper() == 'STALE':
        raise ValueError('fonte documento STALE secondo controllo esistente')
    dated_doc = doc
    if doc.get('availability_basis') == 'observed_download' and doc.get('retrieval') is None:
        from bellomberg.agents.ponte_filing_dossier import CONTRATTO
        if bridge.get('contract') != CONTRATTO or not bridge.get('run_concluso_il'):
            raise ValueError('ricevuta download sigillata assente')
        # Same explicit receipt as ponte._disponibilita, using ONLY sealed pins.
        dated_doc = dict(doc, retrieval={'url': doc['url'], 'document_sha256': doc.get('document_sha256'),
                                        'retrieved_at': bridge['run_concluso_il']})
    availability = source_dates(dated_doc, cutoff)
    if not re.fullmatch('[0-9a-fA-F]{64}', str(doc.get('sha256') or '')):
        raise ValueError('hash fonte documento assente')
    if 'text' in doc and sha256(doc['text'].encode('utf-8')).hexdigest() != doc['sha256']:
        raise ValueError('hash testo documento diverso')
    return availability


def _binding_receipt(receipt, ticker, dossier, documents, cutoff):
    """TI provenance filters, without TI numeric/tool-count policy or invented EvidenceIDs."""
    if not isinstance(receipt, dict) or receipt.get('success') is not True:
        raise ValueError('receipt success non confermato')
    if receipt.get('truncated') is not False:
        raise ValueError('receipt truncated o stato troncamento assente')
    if not isinstance(receipt.get('input'), dict) or receipt['input'].get('ticker') != ticker:
        raise ValueError('receipt ticker diverso')
    if not receipt.get('tool') or receipt['tool'] in ('get_valuation', 'build_dcf_model'):
        raise ValueError('receipt tool non applicabile alla ricerca')
    try:
        output = json.loads(receipt.get('output') or '')
    except (TypeError, ValueError):
        raise ValueError('receipt JSON illeggibile') from None
    if not isinstance(output, dict):
        raise ValueError('receipt JSON senza fonte strutturata')
    payload = output.get('data', output)
    dates = set()
    date_keys = {'as_of', 'price_asof', 'valuation_date', 'fiscal_date', 'period_end',
                 'reported_at', 'filing_date', 'observed_at', 'published_at', 'research_as_of'}

    def visit(node):
        if isinstance(node, dict):
            if (node.get('fonte_primaria_verificata') is False or node.get('source_verified') is False
                    or node.get('verifica') == 'non_verificato'):
                raise ValueError('receipt fonte non verificata')
            if (node.get('ok') is False or node.get('success') is False or node.get('error')
                    or node.get('stale') is True or node.get('is_stale') is True
                    or str(node.get('status') or node.get('stato') or '').upper() in ('STALE', 'ERROR', 'FAILED', 'UNAVAILABLE')):
                raise ValueError('receipt fonte STALE/KO dichiarata')
            for key, value in node.items():
                if key in date_keys and value is not None:
                    day = _binding_day(value)
                    if day > cutoff:
                        raise ValueError('receipt data economica oltre cutoff')
                    dates.add(day.isoformat())
                elif isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    eligible_scopes, excluded_scopes = [], []
    if receipt['tool'] == 'get_guidance' and isinstance(payload, dict) and isinstance(payload.get('active'), list):
        # get_guidance returns independent current/stale rows together. A stale EPS
        # is not a veto on current revenue; equally, the parent as_of cannot revive EPS.
        root = {key: value for key, value in payload.items() if key not in ('active', 'history')}
        visit(dict(output, data=root) if 'data' in output else root)
        base_path = '$.data' if 'data' in output else '$'
        for index, scope in enumerate(payload['active']):
            path = base_path + '.active[' + str(index) + ']'
            parent_dates = set(dates)
            try:
                if not isinstance(scope, dict) or scope.get('status') != 'active':
                    raise ValueError('guidance non attiva')
                visit(scope)
                eligible_scopes.append({'path': path, 'sha256': research_digest(scope)})
            except (ValueError, TypeError) as exc:
                dates.clear()
                dates.update(parent_dates)
                excluded_scopes.append({'path': path, 'reason': str(exc)})
        if 'history' in payload:
            excluded_scopes.append({'path': base_path + '.history', 'reason': 'storico, non guidance corrente'})
        if not eligible_scopes:
            raise ValueError('guidance corrente non disponibile: ' + '; '.join(scope['reason'] for scope in excluded_scopes))
    else:
        visit(output)
    source = receipt.get('source') or output.get('_source') or output.get('source')
    availability = None
    if receipt['tool'] == 'read_company_dossier':
        if not isinstance(payload, dict) or payload.get('source_fingerprint') != dossier.get('source_fingerprint') or not dossier.get('source_fingerprint'):
            raise ValueError('receipt identita fonte dossier diversa')
        document_id = receipt['input'].get('document_id')
        if receipt['input'].get('section') == 'document':
            doc = next((doc for doc in documents if doc['id'] == document_id), None)
            if not doc or payload.get('document_id') != document_id or payload.get('sha256') != doc['sha256'] or payload.get('url') != doc['url']:
                raise ValueError('receipt riferimento documento diverso')
            source = doc['url']
            availability = {key: doc[key] for key in ('available_at', 'availability_basis')}
        else:
            source = 'sealed company dossier ' + dossier['source_fingerprint']
    if not source:
        raise ValueError('receipt fonte assente')
    if not dates and availability is None:
        raise ValueError('receipt data economica assente')
    return source, {'economic_dates': sorted(dates), 'document_availability': availability,
                    'eligible_scopes': eligible_scopes, 'excluded_scopes': excluded_scopes}


def research_binding_for_row(envelope, *, memo_markdown, row, run_identity, reference):
    """Validate a persisted binding against the current sealed run and exact source row."""
    if not isinstance(envelope, dict) or envelope.get('sha256') != research_digest(
            {key: value for key, value in envelope.items() if key != 'sha256'}):
        raise ValueError('hash binding ricerca assente o diverso')
    if (envelope.get('version') != 1 or envelope.get('run_id') != run_identity.get('run_id')
            or not run_identity.get('run_id') or envelope.get('cutoff') != run_identity.get('cutoff')
            or envelope.get('research_ref') != reference):
        raise ValueError('binding ricerca di run/cutoff/sigillo diverso')
    if envelope.get('memo_sha256') != sha256(memo_markdown.encode('utf-8')).hexdigest():
        raise ValueError('binding ricerca di memo diverso')
    identity = _binding_row_identity(row)
    matches = [bound for bound in envelope.get('rows') or []
               if all(bound.get(key) == value for key, value in identity.items())]
    if len(matches) != 1:
        raise ValueError('binding ricerca della riga/indice/azione/ticker assente o ambiguo')
    return deepcopy(matches[0])
