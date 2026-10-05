"""K1 (04/10/2026, Opus 5.5): una richiesta del weekly riconciliata dalla bolletta del
provider ('settled') non deve piu' bloccare ripresa, email e completamento.

Prima della cura `costs_unresolved` accettava solo received/rejected: il journal
diceva "0 richieste incerte" ma lo store continuava a dire "costi incerti".
Dati interamente sintetici (modello, id, importi inventati)."""
from contextlib import closing, contextmanager
import json
import sqlite3

import pytest

from bellomberg.core.request_journal import RequestJournal
from bellomberg.storage.weekly_run_store import CONTRACT_VERSION, WeeklyRunStore, costs_unresolved

MODEL = "synthetic/zz-model"
GEN = "gen-zztest-0001"


def _journal(path, run_id):
    return RequestJournal(path, run_id=run_id, authorization={"source": "offline-test"},
        authorized_usd="10",
        metadata=lambda model: {"id": model, "context_length": 200000, "max_completion_tokens": 128000,
                                "pricing": {"prompt": "0.000001", "completion": "0.000002"}})


def _unknown_request(journal):
    """Una POST partita e mai ricevuta: stato 'unknown', costo ignoto."""
    request_id, _, _ = journal.prepare({"model": MODEL, "max_tokens": 100,
        "messages": [{"role": "user", "content": "desk work"}]},
        {"phase": "weekly", "agent": "macro", "round_n": 1})
    journal.fail(request_id, RuntimeError("stream interrotto"))
    return request_id


def _settle(path, request_id, charged="0.000123"):
    assert RequestJournal.settle_unknown(path, request_id, charged_usd=charged, generation_id=GEN,
                                         provider_generation={"id": GEN}, lookup_sha256="0" * 64) is True


def test_settled_request_from_the_real_journal_no_longer_blocks(tmp_path):
    journal = _journal(tmp_path / "weekly-run-requests.sqlite", "synthetic-weekly")
    request_id = _unknown_request(journal)
    assert costs_unresolved(journal.summary()) is True
    _settle(journal.path, request_id)
    summary = RequestJournal.read_summary(journal.path)
    assert [row["state"] for row in summary["requests"]] == ["settled"]
    assert summary["unknown_requests"] == 0 and summary["cost_usd"] == pytest.approx(0.000123)
    assert costs_unresolved(summary) is False


@pytest.mark.parametrize("state", ["unknown", "incomplete", "overrun", "reserved", None])
def test_other_states_still_block_even_with_a_cost(state):
    # 'incomplete' e 'overrun' hanno un costo misurato ma il journal li blocca in prepare():
    # restano incerti anche qui. 'released' NON e' piu' fra questi (KA 04/10): dalla
    # decisione PM «TI-RITENTATIVO-NON-FATTURATO» e' uno stato del journal weekly, chiuso
    # con costo 0 e la prova del mancato addebito (test_released_* sotto).
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": state, "cost": 5}], "external_requests": []}) is True


def test_settled_without_a_cost_still_blocks():
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": "settled", "cost": None}], "external_requests": []}) is True


def test_settled_mixed_with_an_open_unknown_still_blocks():
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": "settled", "cost": 5}, {"state": "unknown", "cost": None}],
                             "external_requests": []}) is True


class _Db:
    def __init__(self, path):
        self.db_path = str(path)
        with self._conn() as conn:
            conn.execute("CREATE TABLE memos(id INTEGER PRIMARY KEY, notes TEXT)")
            conn.execute("CREATE TABLE positions(ticker TEXT PRIMARY KEY, qty REAL)")
            conn.execute("CREATE TABLE cash_state(singleton_id INTEGER PRIMARY KEY, eur REAL)")
            conn.execute("INSERT INTO memos VALUES(7,'weekly sintetico')")
            conn.execute("INSERT INTO positions VALUES('ZZTEST',3)")

    @contextmanager
    def _conn(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn


def test_store_status_unblocks_resume_after_settlement(tmp_path):
    """Il cablaggio vero: status() rilegge il journal dal disco e decide la ripresa."""
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    from bellomberg.storage.weekly_run_store import current_book_identity
    db = _Db(tmp_path / "consigliere.db")
    context = {"contract_version": CONTRACT_VERSION, "book_identity": current_book_identity(db),
               "contract": {"analysis_mode": RESEARCH_ANALYSIS_MODE, "roster": ["macro"], "r2_specialists": []}}
    store = WeeklyRunStore(db, 7, context=context)
    path = tmp_path / ("weekly-" + store.run_id + "-requests.sqlite")
    request_id = _unknown_request(_journal(path, store.run_id))
    store.update(request_journal_path=str(path), status="incomplete")
    before = store.status()
    assert before["blocked_reason"] == "Richieste o costi incerti: riconciliazione necessaria"
    assert before["resume_available"] is False
    _settle(path, request_id)
    after = store.status()
    assert after["request_costs"]["requests"][0]["state"] == "settled"
    assert after["blocked_reason"] is None and after["resume_available"] is True


def test_released_with_its_zero_cost_does_not_block():
    """KA (04/10): richiesta CERTAMENTE non fatturata (connessione mai stabilita o 402 di
    ammissione) chiusa dal journal con costo 0: non e' un costo incerto."""
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": "released", "cost": 0}], "external_requests": []}) is False


def test_released_without_a_cost_still_blocks():
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": "released", "cost": None}], "external_requests": []}) is True


def test_released_mixed_with_an_open_unknown_still_blocks():
    assert costs_unresolved({"unknown_requests": 0, "external_unresolved_requests": 0,
                             "requests": [{"state": "released", "cost": 0}, {"state": "unknown", "cost": None}],
                             "external_requests": []}) is True


def test_released_request_from_the_real_journal_does_not_block(tmp_path):
    """Il cablaggio vero: RequestJournal.release_unbilled scrive lo stato, summary lo dichiara."""
    from bellomberg.core.llm_client import APIConnectionError
    journal = _journal(tmp_path / "weekly-run-requests.sqlite", "synthetic-weekly")
    request_id, _, _ = journal.prepare({"model": MODEL, "max_tokens": 100,
        "messages": [{"role": "user", "content": "desk work"}]},
        {"phase": "weekly", "agent": "macro", "round_n": 1})
    error = APIConnectionError("ConnectError: synthetic refused")
    error.transport_phase = "connect"
    assert journal.release_unbilled(request_id, error, reason="connessione mai stabilita") is True
    summary = RequestJournal.read_summary(journal.path)
    assert [(row["state"], row["cost"]) for row in summary["requests"]] == [("released", 0)]
    assert summary["unknown_requests"] == 0 and summary["cost_usd"] == 0
    assert summary["released_requests"] == [{"request_id": request_id, "agent": "macro", "phase": "weekly",
                                              "round_n": 1, "reason": "connessione mai stabilita"}]
    assert costs_unresolved(summary) is False
