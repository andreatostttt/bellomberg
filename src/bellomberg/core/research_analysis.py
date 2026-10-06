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
