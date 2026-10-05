"""Richiesta Trade Idea di RICERCA costruita come la costruisce il server (Z3b, 05/10/2026).

Dal 05/10 validate_run_authorization rifiuta una qualificazione in modalita' ricerca con status
diverso da 'research_required' (difesa in profondita' sul cancello di spesa). Il vecchio finto
dei test - request() con status 'qualified' + analysis_mode di ricerca - era uno stato che nessun
produttore reale emette. Qui la qualificazione e' quella VERA di research_admission (bundle
validato, impronta coerente), su un profilo pubblico sintetico congelato per l'identita' data;
il grant e' l'unico ammesso dalla ricerca: solo 'committee', zero revisioni.

Interfaccia:
    research_qualification(identity, *, archive_root=None, as_of=None) -> dict
    research_request(payload=None, *, archive_root=None, as_of=None) -> dict
archive_root: senza documenti PM research_admission non lo usa; se omesso si usa una
cartella temporanea nuova (mai data/).
"""
from copy import deepcopy
from datetime import datetime, timezone

from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE

RESEARCH_GRANT_ACTIVITIES = ['committee']


def _identity_of(payload):
    return {'ticker': payload['ticker'], 'name': payload['company_name'],
            'exchange': payload['exchange'], 'currency': payload['currency'], 'status': 'confirmed'}


def research_qualification(identity, *, archive_root=None, as_of=None):
    """research_admission vera (status 'research_required') per un'identita' sintetica."""
    from bellomberg.valuation import trade_idea_model as model
    from test_trade_idea_economic import providers_for
    day = as_of or datetime.now(timezone.utc).date().isoformat()
    if archive_root is None:
        import tempfile
        archive_root = tempfile.mkdtemp(prefix='research-request-archive-')
    base = providers_for()['profile']
    observed = datetime.fromisoformat(day + 'T00:00:00+00:00')

    def profile(ticker, **kwargs):
        result = base(ticker, **kwargs)
        result['as_of'] = day
        result['retrieved_at'] = observed.isoformat()
        result['data']['info'].update(symbol=identity['ticker'], longName=identity['name'],
            shortName=identity['name'], fullExchangeName=identity['exchange'],
            exchange=identity['exchange'], currency=identity['currency'],
            financialCurrency=identity['currency'], regularMarketTime=observed.timestamp())
        return result

    admitted = model.research_admission(identity['ticker'], deepcopy(identity), day,
        archive_root=archive_root, providers={'profile': profile}, analysis_mode=RESEARCH_ANALYSIS_MODE)
    if admitted['status'] != 'research_required':
        raise AssertionError('Synthetic research admission blocked: ' + '; '.join(admitted['reasons']))
    return admitted


def research_request(payload=None, *, archive_root=None, as_of=None):
    """Una richiesta di run in modalita' ricerca accettabile da store.create_run."""
    if payload is None:
        from test_trade_idea_store import request
        payload = request()
    payload = deepcopy(payload)
    qualification = research_qualification(_identity_of(payload), archive_root=archive_root, as_of=as_of)
    payload['analysis_mode'] = RESEARCH_ANALYSIS_MODE
    payload['source_qualification'] = qualification
    payload['authorization'] = {'accepted': True, 'source_fingerprint': qualification['fingerprint'],
        'activities': list(RESEARCH_GRANT_ACTIVITIES), 'max_revision_rounds': 0}
    return payload
