"""Run-bound primary-document tools shared by both ordinary committees.

The research session verifies and journals documents. These bindings supply the
application's identity and paths, never model-provided authorization or paths.
"""
from copy import deepcopy
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
import re
from threading import RLock


TOOL_NAMES = frozenset({'open_company_source', 'search_company_sources', 'acquire_company_source'})


def company_source_tools():
    ticker = {'type': 'string', 'description': 'Exact exchange ticker of the issuer being researched.'}
    schemas = [
        ('search_company_sources', 'Open the official issuer website or an observed official IR index and find links. '
         'No paid search engine. Navigation results are not financial evidence.',
         {'ticker': ticker, 'url': {'type': 'string'}, 'query': {'type': 'string'}}, ['ticker']),
        ('open_company_source', 'Read a public official company page/document and its links, with pagination. '
         'Use it to find financial statements, earnings, publication dates and literal metadata quotes. '
         'PDFs return one physical page: continue next_offset on that page, then next_page with offset zero. '
         'Reading alone does not admit a document to the model dossier.',
         {'ticker': ticker, 'url': {'type': 'string'}, 'query': {'type': 'string'},
          'offset': {'type': 'integer', 'minimum': 0},
          'page': {'type': 'integer', 'minimum': 1}}, ['ticker', 'url']),
        ('acquire_company_source', 'Download, verify and archive an official financial document in the shared run dossier. '
         'Supply dates and literal metadata quotes only as observed in the document. Missing proof remains explicit. '
         'Use read_company_dossier to read admitted full text. Acquisition does not approve financial assumptions.',
         {'ticker': ticker, 'source': {'type': 'object', 'properties': {
             key: {'type': 'string'} for key in ('url', 'published_at', 'publication_quote',
                 'report_date', 'report_date_quote', 'issuer_quote', 'title')},
             'required': ['url'], 'additionalProperties': False}}, ['ticker', 'source']),
    ]
    return [{'name': name, 'description': description, 'input_schema': {
        'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}
        for name, description, properties, required in schemas]


def source_research_guard(board):
    """Serialize source admission and model creation, never heartbeat writes."""
    return getattr(board, '_source_research_lock', None) or nullcontext()


def model_sources_sealed(board, ticker):
    """A failed pre-model attempt is not a workbook with immutable sources."""
    from bellomberg.core.research_analysis import is_research_mode
    if is_research_mode(board):
        return board.data.get('_research_thesis') is not None
    payload = (getattr(board, 'valuation_results', {}) or {}).get(ticker)
    if getattr(board, 'run_scope', 'weekly') == 'trade_idea':
        return bool(payload)
    # A later error must not reopen a previously generated model, even when
    # its file has disappeared or the current result only contains the error.
    for attempt in getattr(board, 'valuation_attempts', []) or []:
        if (isinstance(attempt, dict) and attempt.get('ticker') == ticker
                and (attempt.get('path') or attempt.get('revision_origin') in ('generated', 'reused'))):
            return True
    if not payload:
        return False
    if not isinstance(payload, dict) or payload.get('ok') is not False:
        return True
    consumption = payload.get('input_consumption') or {}
    preparation = payload.get('preparation') or {}
    if not isinstance(consumption, dict) or not isinstance(preparation, dict):
        return True
    return bool(payload.get('path') or payload.get('workbook_sha256') or payload.get('reused')
        or consumption.get('consumed_records') or consumption.get('consumed_fields')
        or consumption.get('status') not in (None, 'incomplete')
        or preparation.get('status') == 'prepared')


def dispatch_company_source(board, name, input_, *, max_chars, consultation=False):
    # Keep the guard through download and acknowledgment: a late receipt must
    # not change the journal underneath a model compiled against an older pin.
    with source_research_guard(board) if name == 'acquire_company_source' else nullcontext():
        return _dispatch_company_source(board, name, input_, max_chars=max_chars,
                                        consultation=consultation)


def _dispatch_company_source(board, name, input_, *, max_chars, consultation=False):
    try:
        resolver = getattr(board, 'company_source_session', None)
        if name not in TOOL_NAMES or not callable(resolver):
            raise ValueError('Company source research is not bound to this run')
        ticker = input_.get('ticker')
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError('An exact ticker is required')
        if name == 'acquire_company_source' and (consultation or board.current_round not in (0, 1)):
            raise ValueError('Sources can be admitted only during initial research, before model review')
        if name == 'acquire_company_source' and model_sources_sealed(board, ticker.strip().upper()):
            raise ValueError('The common research/model source revision is sealed for committee review')
        session = resolver(ticker.strip().upper())
        if session is None:
            raise ValueError('Company source session unavailable for this ticker')
        from bellomberg.agents.company_source_research import bounded_navigation_view
        if name == 'open_company_source':
            return bounded_navigation_view(session.open_page(input_['url'], query=input_.get('query', ''),
                offset=input_.get('offset', 0), max_chars=max_chars,
                **({'page': input_['page']} if 'page' in input_ else {})), max_chars=max_chars)
        if name == 'search_company_sources':
            return bounded_navigation_view(session.search(query=input_.get('query', ''), url=input_.get('url')),
                                           max_chars=max_chars)
        result = session.acquire(input_['source'])
        if result.get('ok') is True:
            snapshot = session.snapshot()
            changed = getattr(board, 'on_company_sources_changed', None)
            if callable(changed):
                changed(session, snapshot)
        return result
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {'ok': False, 'status': 'source_research_incomplete',
                'error': type(exc).__name__ + ': ' + str(exc)}


RESEARCH_INSTRUCTIONS = (
    '\nPRIMARY COMPANY DOCUMENTS: during R0/R1, use search_company_sources and open_company_source '
    'to visit the official issuer/Investor Relations website and find the latest earnings, annual and interim '
    'financial statements. Acquire the documents with acquire_company_source so every desk and the model '
    'compiler can use the same verified bytes, dates and source IDs. An empty internal archive is a research '
    'task, not evidence that official statements do not exist. Read existing read_company_dossier first; '
    'reuse admitted documents. Fundamentals must read the statements and earnings before get_valuation, '
    'explain assumptions and scenarios, and preserve source references. A page or search snippet is not '
    'a financial proof. If publication, issuer, statement or numeric evidence cannot be verified, report '
    'the specific gap; never invent a source, opening number, forecast or completed workbook.'
)

RESEARCH_ONLY_INSTRUCTIONS = (
    '\nPRIMARY COMPANY DOCUMENTS: during R0/R1 use search_company_sources, open_company_source and '
    'acquire_company_source to find, verify and archive official annual/interim statements, earnings, '
    'presentations and guidance. Start with read_company_dossier and page the exact admitted text; '
    'reuse source IDs and already acquired bytes. The catalog is research memory, not a prerequisite. '
    'Read statements and explain observed facts, management guidance, analyst consensus, your assumptions '
    'and scenario judgement separately. Missing or stale evidence stays explicit; a search snippet is '
    'not a financial proof. After the committee seal the source dossier is immutable: discuss the same '
    'version/hash and label unresolved needs as further research. No workbook, driver schema or AI fair '
    'value is required or generated. Do not call an Excel author, preparer, compiler or repair workflow.'
)


def bind_weekly_company_research(board, store, *, identity_resolver=None, profile_provider=None,
                                 session_factory=None):
    """Bind the ordinary weekly path using its persistent, original run context."""
    from bellomberg.storage.weekly_run_store import digest
    root = Path(store.db.db_path).resolve().parent
    run_dir = root / 'company_research' / store.run_id
    as_of = store.context['research_started_at'][:10]
    admission = digest(store.context)
    sessions, lock = {}, RLock()
    board._source_research_lock = lock

    def checked(session, ticker):
        pin = ((board.data.get('_company_research') or {}).get(ticker) or {}).get('revision_id')
        if pin is not None:
            session.snapshot(expected_revision_id=pin)
        return session

    def session_for(ticker):
        if not isinstance(ticker, str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.^=\-]{0,31}', ticker):
            raise ValueError('Invalid exact company ticker')
        with lock:
            if ticker in sessions:
                return checked(sessions[ticker], ticker)
            key = 'company-source-identity:' + ticker
            accepted = store.get(key)
            if accepted is None:
                from bellomberg.agents.trade_idea import resolve_ticker_identity
                from bellomberg.valuation.sector_analysis import default_sector_providers
                identity = (identity_resolver or resolve_ticker_identity)(ticker)
                if identity.get('status') != 'confirmed' or identity.get('ticker') != ticker:
                    raise ValueError('Issuer identity not confirmed for ' + ticker)
                provider = profile_provider or default_sector_providers()['profile']
                profile = provider(ticker, as_of=as_of)
                website = ((profile.get('data') or {}).get('info') or {}).get('website')
                if profile.get('status') != 'ok':
                    raise ValueError('Issuer website profile unavailable: ' + str(profile.get('message')))
                # A missing official website is explicit; curated authority URLs
                # may still be read when the agent has observed an exact URL.
                accepted = {'identity': identity, 'issuer_website': website, 'as_of': as_of}
                store.complete(key, accepted, board)
            if accepted.get('as_of') != as_of or accepted['identity'].get('ticker') != ticker:
                raise ValueError('Persisted company identity differs from this run')
            from bellomberg.agents.company_source_research import ResearchSession
            factory = session_factory or ResearchSession
            sessions[ticker] = factory(run_id=store.run_id, ticker=ticker, identity=accepted['identity'],
                as_of=as_of, admission_fingerprint=admission, archive_root=root / 'filing_archive',
                run_dir=run_dir / sha256(ticker.encode()).hexdigest(),
                issuer_website=accepted.get('issuer_website'))
            return checked(sessions[ticker], ticker)

    board.company_source_session = session_for
    def existing_session(ticker):
        # Read-only dossier access and sealing must not fetch a new identity or
        # company profile for a holding which the desks have not researched.
        if ticker in sessions or store.get('company-source-identity:' + ticker) is not None:
            return session_for(ticker)
        return None
    board.company_source_session_if_known = existing_session
    # Before any resumed paid phase, reprove each revision already acknowledged
    # by the weekly checkpoint. A valid journal prefix is not the full dossier.
    for ticker in board.data.get('_company_research') or {}:
        session_for(ticker)

    def changed(session, snapshot):
        with lock:
            ticker = snapshot['ticker']
            if board.current_round not in (0, 1) or model_sources_sealed(board, ticker):
                raise ValueError('Research sources cannot change after model creation/review; acquired bytes remain unadopted')
            pin = ((board.data.get('_company_research') or {}).get(ticker) or {}).get('revision_id')
            # Another desk may have admitted a source after this callback's
            # argument was captured. Persist the latest verified chain under
            # the binding lock, never overwrite a newer pin with that argument.
            latest = session.snapshot(expected_revision_id=pin)
            with board._lock:
                board.data.setdefault('_company_research', {})[ticker] = {
                    key: deepcopy(latest.get(key)) for key in
                    ('revision_id', 'revision_sha256', 'parent_grant_fingerprint', 'as_of')}
                store.save_snapshot(board)

    board.on_company_sources_changed = changed
    original = board.valuation_preparer
    if original is not None:
        def prepare(bundle, **kwargs):
            ticker = bundle['case']['ticker']
            # Only already created research sessions are relevant. Creating an
            # identity/document session is never an implicit requirement to price.
            key = 'company-source-identity:' + ticker
            if ticker in sessions or store.get(key) is not None:
                snapshot = session_for(ticker).snapshot()
                if snapshot.get('documents'):
                    kwargs['research_sources'] = snapshot
            return original(bundle, **kwargs)
        board.valuation_preparer = prepare
