"""The Capo continuation consumes the exact request, before any mutable context."""
from copy import deepcopy

from bellomberg.agents import capo
from bellomberg.core import current_facts
from test_capo_collasso import _prepara, _msg, _bb, MEMO_VERO


def test_capo_reuses_frozen_request_when_context_changes(monkeypatch):
    calls = _prepara(monkeypatch, lambda *_: _msg(MEMO_VERO))
    bb = _bb()
    saved = []
    bb.persist_run_checkpoint = lambda event, payload: saved.append((event, deepcopy(payload)))
    first, _ = capo.run_capo(bb)
    assert saved and saved[0][0] == 'capo_request'
    original = deepcopy(saved[0][1])
    def forbidden():
        raise AssertionError('A frozen paid request must not reacquire current context')
    monkeypatch.setattr(current_facts, 'current_facts_block', forbidden)
    monkeypatch.setattr(current_facts, 'pm_theses_block', forbidden)
    bb.data['macro'][2] = 'later report which must not replace the original'
    second, _ = capo.run_capo(bb)
    assert second == first and calls[1] == calls[0]
    assert bb.data['_capo_request'] == original and len(saved) == 1
