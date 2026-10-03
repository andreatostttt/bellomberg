"""Local, durable statement views shared by native author reads and recovery."""
from copy import deepcopy


def statement_receipt(board):
    from bellomberg.valuation.author_statement_evidence import _digest
    state = board.data.get('_author_statements') or {}
    if not state.get('active_sha256'):
        return None
    rows = state.get('receipts')
    if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get('sha256') !=
            _digest({key: value for key, value in row.items() if key != 'sha256'}) for row in rows):
        raise ValueError('Saved statement evidence journal differs')
    matches = [row for row in rows if row['sha256'] == state['active_sha256']]
    if len(matches) != 1:
        raise ValueError('Saved statement evidence receipt is missing or ambiguous')
    return matches[0]


def acquire_board_statements(board):
    """Expose verified facts without adopting them as the author's assumptions."""
    from bellomberg.valuation.author_statement_evidence import statement_basis
    qualification = board.source_qualification
    if qualification.get('method_id') != 'operating_fcff':
        return {'status': 'not_applicable', 'economic_decisions_applied': False}
    old = statement_receipt(board)  # Corrupt saved evidence is never overwritten.
    research = board.data.get('_source_research') or {}
    if research.get('qualified_fingerprint') == qualification.get('fingerprint') and old:
        return deepcopy(old['view'])
    root = research.get('archive_root')
    if not root:
        return {'status': 'incomplete', 'economic_decisions_applied': False,
                'issues': [{'reason': 'No run-bound source archive for statement evidence'}]}
    plan = board.data.get('_model_input_draft')
    existing = None
    if old:
        from bellomberg.valuation.author_quotation import _context
        context = {**_context(qualification, plan, root)[0], 'policy': 'author_statement_evidence/1'}
        matches = [row for row in board.data['_author_statements']['receipts'] if row['context'] == context]
        if len(matches) > 1:
            raise ValueError('Saved statement context is ambiguous')
        existing = matches[0] if matches else None
    result = statement_basis(qualification, plan, archive_root=root, existing=existing)
    receipt = result.get('receipt')
    if receipt is None:
        return deepcopy(result['view'])
    previous = deepcopy(board.data.get('_author_statements'))
    state = deepcopy(previous or {'receipts': []})
    matches = [row for row in state['receipts'] if row.get('context') == receipt['context']]
    if matches and matches != [receipt]:
        raise ValueError('Statement facts changed for the same accepted source and author calendar')
    if old == receipt and state.get('active_sha256') == receipt['sha256']:
        return deepcopy(result['view'])
    if not matches:
        state['receipts'].append(deepcopy(receipt))
    state['active_sha256'] = receipt['sha256']
    board.data['_author_statements'] = state
    try:
        persist = getattr(board, 'persist_run_checkpoint', None)
        if callable(persist):
            persist('author_statement_evidence_observed')
    except BaseException:
        if previous is None:
            board.data.pop('_author_statements', None)
        else:
            board.data['_author_statements'] = previous
        raise
    return deepcopy(result['view'])


def board_author_evidence_view(board):
    from bellomberg.valuation.author_quotation import board_quotation_view
    from bellomberg.valuation.author_statement_evidence import merge_statement_evidence
    receipt = statement_receipt(board)
    overlay = board_quotation_view(board)
    if receipt is None:
        return overlay
    research = board.data.get('_source_research') or {}
    if research.get('qualified_fingerprint') == board.source_qualification.get('fingerprint'):
        return overlay
    return merge_statement_evidence(board.source_qualification, board.data['_model_input_draft'], receipt,
        archive_root=research['archive_root'], overlay=overlay)
