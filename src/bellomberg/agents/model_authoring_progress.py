"""Informational author ledger; no I/O, provider, financial schema or approvals.

The caller supplies the current native input contract and consultation checks
computed by the existing gates. This module describes recorded work only.
"""
from collections.abc import Mapping
import json


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _rows(value):
    return value if isinstance(value, (list, tuple)) else ()


def _error_note(value):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return {'text': text[:1600], 'truncated': len(text) > 1600,
            'total_chars': len(text), 'full_detail': 'original checkpoint/tool receipt'}


def _observed_tools(messages):
    """Match only this author's delivered tool results to their explicit calls."""
    calls = {}
    for message in _rows(messages):
        message = _mapping(message)
        for block in _rows(message.get('content')):
            block = _mapping(block)
            if message.get('role') == 'assistant' and block.get('type') == 'tool_use':
                calls[block.get('id')] = (block.get('name'), _mapping(block.get('input')))
            elif message.get('role') == 'user' and block.get('type') == 'tool_result':
                call = calls.get(block.get('tool_use_id'))
                if call is None:
                    continue
                value = block.get('content')
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        value = None
                yield call[0], call[1], value if isinstance(value, Mapping) else None


def _read_progress(observations):
    sections, pages = {}, {}
    last_submission = last_compilation = None
    for name, inputs, output in observations:
        if name in {'submit_candidate_model_plan', 'get_valuation'}:
            error = (_error_note({'status': 'unverified_tool_output', 'tool': name})
                     if output is None else _error_note(output)
                     if output.get('ok') is False or output.get('error') else None)
            if name == 'submit_candidate_model_plan':
                last_submission = error
            else:
                last_compilation = error
        section = inputs.get('contract_section')
        if name != 'get_candidate_model_inputs' or not isinstance(section, str):
            continue
        if output is None:
            sections[section] = {'status': 'unverified_output',
                                 'current_source_revalidated': False}
            continue
        if output.get('ok') is not True:
            row = sections.setdefault(section, {'status': 'observed_error',
                                                'current_source_revalidated': False})
            row['error'] = str(output.get('error') or output.get('reason') or 'Unsuccessful read')[:1200]
            continue
        start, end, total = (output.get(key) for key in ('offset', 'next_offset', 'total_chars'))
        digest = output.get('sha256')
        valid = (output.get('section') == section and isinstance(digest, str) and bool(digest)
                 and all(type(value) is int for value in (start, end, total))
                 and 0 <= start < end <= total and isinstance(output.get('text'), str)
                 and len(output['text']) == end - start
                 and output.get('complete') is (end == total))
        if not valid:
            sections[section] = {'status': 'unverified_output',
                                 'current_source_revalidated': False}
            continue
        key = (section, digest, total)
        ranges = pages.setdefault(key, [])
        ranges.append((start, end))
        contiguous = 0
        for left, right in sorted(ranges):
            if left > contiguous:
                break
            contiguous = max(contiguous, right)
        sections[section] = {'status': 'observed_complete' if contiguous == total else 'observed_partial',
            'sha256': digest, 'next_offset': contiguous, 'total_chars': total,
            'current_source_revalidated': False}
    return sections, last_submission, last_compilation


def build_model_authoring_progress(blackboard, *, contract, remaining_turns=None,
        messages=None, required_consultation_desks=(), consultation_checks=None):
    """Describe missing work without changing the board or authorizing dispatch.

    ``contract`` is the existing native input contract. ``consultation_checks``
    maps each ID to current/received/decided booleans from the native validators;
    absent checks remain unknown. ``messages`` can be the current author loop;
    otherwise only its saved fundamentals:R1 conversation is observed.
    """
    data = _mapping(getattr(blackboard, 'data', None))
    qualification = _mapping(getattr(blackboard, 'source_qualification', None))
    contract = _mapping(contract)
    schema = _mapping(contract.get('schema'))
    scenarios = [scope for scope in _rows(contract.get('scenarios')) if isinstance(scope, str)]
    raw_draft = data.get('_model_input_draft')
    draft = _mapping(raw_draft)
    scopes, schema_valid = {}, bool(schema)
    for scope in ['model', *scenarios]:
        expected = []
        for driver, descriptor in schema.items():
            if not isinstance(driver, str) or not _rows(descriptor) or descriptor[-1] not in {'model', 'scenario'}:
                schema_valid = False
                continue
            if descriptor[-1] == ('model' if scope == 'model' else 'scenario'):
                expected.append(driver)
        values = _mapping(draft.get('model') if scope == 'model'
                          else _mapping(draft.get('scenarios')).get(scope))
        stored = sorted(set(expected) & set(values))
        scopes[scope] = {'stored': stored, 'missing': sorted(set(expected) - set(values)),
            'unfilled': [key for key in stored if not isinstance(values[key], Mapping)
                         or values[key].get('value') is None],
            'unexpected': sorted(str(key) for key in set(values) - set(expected))}
    if messages is None:
        checkpoints = _mapping(getattr(blackboard, 'specialist_checkpoints', None))
        messages = _mapping(checkpoints.get('fundamentals:R1')).get('messages', [])
    reads, submit_error, compile_tool_error = _read_progress(_observed_tools(messages))
    document_ids = sorted({doc['id'] for doc in
        (_mapping(row) for row in _rows(_mapping(qualification.get('source_report')).get('documents')))
        if isinstance(doc.get('id'), str) and doc['id']})

    checks = _mapping(consultation_checks)
    consultations = []
    for raw in _rows(data.get('_model_consultations')):
        row = _mapping(raw)
        if not isinstance(row.get('id'), str) or not isinstance(row.get('desk'), str):
            continue
        checked = _mapping(checks.get(row['id']))
        current = checked.get('current') if type(checked.get('current')) is bool else None
        received = (False if current is False else checked['received']
                    if current is True and type(checked.get('received')) is bool else None)
        decided = (False if received is False else checked['decided']
                   if received is True and type(checked.get('decided')) is bool else None)
        item = {'id': row['id'], 'desk': row['desk'], 'status': row.get('status'),
                'current': current, 'received': received, 'decided': decided}
        if row.get('author_view_complete') is not True:
            offset = row.get('answer_read_offset', 0)
            item['next_read_offset'] = offset if type(offset) is int and offset >= 0 else None
        if decided:
            item['decision'] = _mapping(row.get('fundamentals_decision')).get('decision')
        consultations.append(item)
    required = sorted({desk for desk in required_consultation_desks if isinstance(desk, str)})
    current_desks = {row['desk'] for row in consultations if row['current'] is True}
    unverified_desks = {row['desk'] for row in consultations if row['current'] is None}
    missing_desks = sorted(set(required) - current_desks - unverified_desks)
    undecided = sorted(row['id'] for row in consultations
                       if row['received'] is True and row['decided'] is not True)
    unreceived = sorted(row['id'] for row in consultations
                        if row['current'] is True and row['received'] is not True)
    attempts = _rows(data.get('_model_compilation_attempts'))
    last_attempt = _mapping(attempts[-1]) if attempts else {}
    remaining_valid = remaining_turns is None or type(remaining_turns) is int and remaining_turns >= 0
    remaining = remaining_turns if remaining_valid else None
    actions = []
    if not document_ids:
        actions.append('Acquire verifiable primary documents; use their exact admitted IDs before model consultations.')
    for section, row in sorted(reads.items()):
        if row['status'] == 'observed_partial':
            actions.append(f'Read get_candidate_model_inputs contract_section={section}, offset={row["next_offset"]}; preserve already received pages.')
    if any(row['missing'] or row['unfilled'] for row in scopes.values()):
        actions.append('Author supported missing drivers from admitted evidence and save each ready group with submit_candidate_model_plan; no archived base value is available unless explicitly supplied.')
    if missing_desks:
        actions.append('Ask the missing desks using explicit draft assumptions and exact admitted evidence IDs; batch independent questions.')
    if unreceived:
        actions.append('Read pending consultation pages to the end; batch pages of independent answers. Preserve previously received answers.')
    if undecided:
        actions.append('Submit explicit consultation_decisions for the received undecided IDs, with your own rationale; do not buy those answers again.')
    actions.append('Use contract_section=draft_validation on the saved draft, repair the reported gaps, then compile the complete authored plan. Stored inputs are not validated inputs or a usable Excel.')
    if remaining == 0:
        actions.append('Tool-turn allowance exhausted: preserve an incomplete checkpoint; continue missing work only through explicit native recovery. This ledger authorizes no new request.')
    elif remaining == 1:
        actions.append('One turn remains: persist only supported draft work and explicit outcomes; declare unresolved inputs instead of substituting a prose report for the model.')
    return {'contract': 'model_authoring_progress/1', 'kind': 'informational_not_approval',
        'contract_status': 'available' if schema_valid else 'unavailable',
        'method_id': contract.get('method_id'), 'source_fingerprint': qualification.get('fingerprint'),
        'remaining_turns': remaining,
        'remaining_turns_error': None if remaining_valid else 'Remaining turns must be a nonnegative integer or unknown.',
        'admitted_document_ids': document_ids, 'contract_reads': dict(sorted(reads.items())),
        'draft': {'present': isinstance(raw_draft, Mapping), 'scopes': scopes,
            'proofs_validated': False,
            'scenario_rationale_missing': [scope for scope in scenarios if not isinstance(
                _mapping(draft.get('scenario_rationale')).get(scope), str)
                or not draft['scenario_rationale'][scope].strip()],
            'analysis_rationale_missing': not scenarios and not bool(
                isinstance(draft.get('analysis_rationale'), str) and draft['analysis_rationale'].strip())},
        'consultations': {'checks': 'native' if consultation_checks is not None else 'not_supplied',
            'required_desks': required, 'missing_desks': missing_desks,
            'items': consultations, 'undecided_ids': undecided, 'unreceived_ids': unreceived,
            'unverified_ids': sorted(row['id'] for row in consultations
                if any(row[key] is None for key in ('current', 'received', 'decided')))},
        'errors': {'last_submission': submit_error,
            'recorded_plan_error': _error_note(data.get('_model_plan_last_error')),
            'last_compilation': compile_tool_error or (_error_note(last_attempt) if last_attempt.get('error') else None),
            'recorded_completion_error': _error_note(data.get('_model_completion_error'))},
        'instructions': ('Progress is informational, never proof or approval. No archived value is adopted. '
            'Use exact admitted_document_ids in evidence_refs, never tool labels, descriptions or appended values. '
            'Save supported driver groups incrementally; a missing archived base must be authored from evidence, not reread indefinitely. '
            'Do not invent missing facts, zero values or consultation decisions. '
            'Contract reads describe observed pages only; reread source-dependent sections after new documents or a changed opening basis. '
            'All financial, provenance, budget and compiler checks remain required.'),
        'next_actions': actions}
