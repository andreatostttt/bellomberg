"""Worker ordinario su una catena col modello candidato (R1 autentico gia' pagato).

Il completamento del modello mancante e' in quarantena in
archive/private/attic/tests_excel_archiviato_20261005/test_trade_idea_model_authoring_pipeline_legacy.py (Excel archiviato
03/10); qui resta il contratto attuale: la catena non prosegue col worker.
"""
from copy import deepcopy

import pytest

from bellomberg.agents import trade_idea
from bellomberg.core import mandato_pm
from test_trade_idea_model_authoring_recovery import native_case, bb, db_path, migrated, blocks, rows


@pytest.fixture(autouse=True)
def frozen_native_mandate(monkeypatch):
    # The origin and worker must see the same complete frozen mandate; changing
    # it only at continuation would correctly invalidate the saved request.
    monkeypatch.setattr(mandato_pm, "carica", mandato_pm.profilo_esempio)
    native_configure = trade_idea._configure_native_recovery
    def configure(board, store, token, **kwargs):
        # The reusable helper fixture begins at the specialist, whereas the
        # ordinary worker already loads this exact DB context before binding.
        # Seed it before the origin's first sealed checkpoint, never on replay.
        if kwargs.get("inherited") is None:
            board.data.setdefault("_decision_context", store.decision_context(board.run_id))
        return native_configure(board, store, token, **kwargs)
    monkeypatch.setattr(trade_idea, "_configure_native_recovery", configure)


def test_ordinary_worker_refuses_to_finish_an_archived_excel_model_run(native_case, tmp_path):
    # Il completamento del modello mancante col worker ordinario e' in archive/private/attic/tests_excel_archiviato_20261005/
    # test_trade_idea_model_authoring_pipeline_legacy.py. Contratto attuale: la stessa catena
    # (R1 autentico gia' pagato) non prosegue col worker; parent e figlio restano invariati.
    from test_trade_idea_pipeline import _assert_excel_run_refused_before_work
    case = native_case
    case.store.interrupt_run(case.child, reason="Offline handoff to the ordinary worker")
    run_id = case.store.create_continuation(case.child, idempotency_key="ordinary-model-completion",
        authorize_new_requests=True)["run"]["id"]
    parent, child = deepcopy(case.store.get_run(case.parent)), deepcopy(case.store.get_run(case.child))
    _assert_excel_run_refused_before_work(case.store, run_id, tmp_path)
    assert case.store.get_run(case.parent) == parent and case.store.get_run(case.child) == child
