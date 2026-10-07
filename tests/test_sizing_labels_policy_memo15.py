"""Text policy only: native temporary stores, never live providers."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from test_trade_idea_store import db_path, migrated, request, store, _save_resume_checkpoint
from bellomberg.core import mandato_pm as mp
from bellomberg.core.language import language_context

KEY = "mandate_text_policy"
POLICY = "mandate-sizing-labels/1"
_REAL_BLOCCO_PROMPT = mp.blocco_prompt
from test_trade_idea_capo_finalization_dispatch import research_board

@pytest.mark.parametrize("lang,expected", [("it", "soglia minima d'azione"), ("en", "minimum action amount")])
def test_opt_in_label(lang, expected):
    with language_context(lang):
        text = mp.blocco_prompt(mp.profilo_esempio(), text_policy=POLICY)
    assert expected in text

@pytest.mark.parametrize("bad", [None, "", "mandate-sizing-labels/2", False, 1, []])
def test_present_bad_marker_refuses(bad):
    with pytest.raises(ValueError, match="mandate text policy"):
        mp.text_policy_from_context({KEY: bad})


def test_absence_is_legacy():
    assert mp.text_policy_from_context({}) is None
    m = mp.profilo_esempio()
    assert mp.blocco_prompt(m) == mp.blocco_prompt(m, text_policy=None)
    assert "posizione minima" in mp.blocco_prompt(m)


def test_new_acceptance_server_owned_idempotent_continuation(migrated):
    s = store(migrated)
    payload = request()
    payload[KEY] = "client-must-not-control-policy"
    first = s.create_run(payload, idempotency_key="policy-parent")
    rid = first["run"]["id"]
    assert first["run"].get(KEY) == POLICY
    with sqlite3.connect(migrated) as conn:
        before = conn.execute("SELECT request_json,request_sha256,context_json FROM trade_idea_runs WHERE id=?", (rid,)).fetchone()
    assert json.loads(before[2])[KEY] == POLICY
    again = s.create_run(payload, idempotency_key="policy-parent")
    assert not again["created"] and again["run"] == first["run"]
    token = s.claim_run(rid)
    _save_resume_checkpoint(s, rid, token)
    s.interrupt_run(rid, reason="synthetic checkpoint")
    child = s.create_continuation(rid, idempotency_key="policy-child", authorize_new_requests=True)
    assert child["run"][KEY] == POLICY
    with s._connect(read_only=True) as conn:
        row = s._row(conn, child["run"]["id"])
        assert row["context_json"] == before[2]
        assert s._current_run_context(conn, row) == json.loads(before[2])
        parent = conn.execute("SELECT request_json,request_sha256,context_json FROM trade_idea_runs WHERE id=?", (rid,)).fetchone()
        assert tuple(parent) == before


def test_weekly_resume_does_not_upgrade():
    from bellomberg.agents.consigliere_multi import _resume_publication_contract
    assert KEY not in _resume_publication_contract({KEY: POLICY}, {})
    assert _resume_publication_contract({}, {KEY: POLICY})[KEY] == POLICY


def test_board_scopes_and_invalid_marker():
    assert mp.text_policy_for_board(SimpleNamespace(run_scope="weekly", weekly_store=None)) is None
    weekly = SimpleNamespace(run_scope="weekly", weekly_store=SimpleNamespace(context={"contract": {KEY: POLICY}}))
    assert mp.text_policy_for_board(weekly) == POLICY
    ti = SimpleNamespace(run_scope="trade_idea", **{KEY: POLICY})
    assert mp.text_policy_for_board(ti) == POLICY
    setattr(ti, KEY, None)
    with pytest.raises(ValueError):
        mp.text_policy_for_board(ti)


def _historical_row(s, path, *, marker_absent=True, marker=None):
    """Insert a historical fixture; immutable rows/triggers are never updated/disabled."""
    first = s.create_run(request(), idempotency_key="template")["run"]["id"]
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM trade_idea_runs WHERE id=?", (first,)).fetchone())
    s.claim_run(first)
    s.interrupt_run(first, reason="historical fixture template closed")
    with sqlite3.connect(path) as conn:
        context = json.loads(row["context_json"])
        context.pop(KEY, None)
        if not marker_absent:
            context[KEY] = marker
        row.update(id="historical", idempotency_key="historical", context_json=json.dumps(context))
        columns = list(row)
        conn.execute("INSERT INTO trade_idea_runs (" + ",".join(columns) + ") VALUES (" +
                     ",".join("?" for _ in columns) + ")", tuple(row.values()))
    return "historical"


def test_legacy_native_idempotence_and_continuation_never_upgraded(migrated):
    s = store(migrated)
    parent = _historical_row(s, migrated)
    assert KEY not in s.get_run(parent)["run"]
    same = s.create_run(request(), idempotency_key="historical")
    assert same["run"]["id"] == parent and not same["created"] and KEY not in same["run"]
    token = s.claim_run(parent)
    _save_resume_checkpoint(s, parent, token)
    s.interrupt_run(parent, reason="synthetic historic crash")
    child = s.create_continuation(parent, idempotency_key="historical-child", authorize_new_requests=True)
    assert KEY not in child["run"]
    with s._connect(read_only=True) as conn:
        row = s._row(conn, child["run"]["id"])
        assert s._current_run_context(conn, row) == json.loads(row["context_json"])
        assert KEY not in json.loads(row["context_json"])


@pytest.mark.parametrize("bad", [None, "", [], "mandate-sizing-labels/2"])
def test_native_unknown_policy_refuses_public_projection(migrated, bad):
    s = store(migrated)
    rid = _historical_row(s, migrated, marker_absent=False, marker=bad)
    with pytest.raises(ValueError, match="Unsupported mandate text policy"):
        s.get_run(rid)


@pytest.mark.parametrize("legacy", [False, True])
def test_native_budget_gate_replays_exact_old_request_only(migrated, legacy):
    from bellomberg.agents.trade_idea import TradeIdeaBudgetGate, costruisci_corpo
    from bellomberg.storage.trade_idea_store import _digest
    s = store(migrated)
    parent = (_historical_row(s, migrated) if legacy else
              s.create_run(request(), idempotency_key="paid-parent")["run"]["id"])
    accepted = s.get_run(parent)["run"]
    policy = mp.text_policy_from_context(accepted)
    kwargs = dict(model=accepted["models"]["specialist"]["model"], max_tokens=1000,
                  system=mp.blocco_prompt(mp.profilo_esempio(), text_policy=policy),
                  messages=[{"role": "user", "content": "Synthetic original question"}])
    pricing = {"prompt": "0.0000001", "completion": "0.0000004"}
    body = costruisci_corpo(**kwargs, provider_max_price={"prompt": .1, "completion": .4, "request": 0.0})
    token = s.claim_run(parent)
    _save_resume_checkpoint(s, parent, token)
    s.reserve_cost(parent, "paid", "specialist:macro", kwargs["model"], "0.10", request_sha256=_digest(body))
    response = {"id": "synthetic-paid", "model": kwargs["model"], "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Paid exact answer"}],
                "usage": {"input_tokens": 10, "output_tokens": 10, "cost_usd": "0.01"}}
    s.reconcile_cost(parent, "paid", charged_usd="0.01", usage=response["usage"],
        receipt={"request_sha256": _digest(body), "response": response, "response_sha256": _digest(response),
                 "response_id": response["id"], "model": response["model"], "stop_reason": "end_turn"})
    s.interrupt_run(parent, reason="paid before crash")
    child = s.create_continuation(parent, idempotency_key="paid-child", authorize_new_requests=True)["run"]["id"]
    gate = TradeIdeaBudgetGate(s, child, s.claim_run(child), {"models": {"specialist": {"pricing": pricing}}})
    with sqlite3.connect(migrated) as conn:
        before = conn.execute("SELECT * FROM trade_idea_costs").fetchall()
    reused = gate._reuse(kwargs, "specialist:macro")
    assert reused.content[0].text == "Paid exact answer"
    changed = {**kwargs, "system": kwargs["system"] + " changed"}
    assert gate._reuse(changed, "specialist:macro") is None
    with sqlite3.connect(migrated) as conn:
        assert conn.execute("SELECT * FROM trade_idea_costs").fetchall() == before


@pytest.mark.parametrize("policy", [None, POLICY])
def test_native_request_journal_preserves_paid_body(tmp_path, policy):
    from test_desk_stream_journal import _journal, _righe
    j = _journal(tmp_path)
    body = {"model": "synthetic/model", "max_tokens": 1000,
            "messages": [{"role": "system", "content": mp.blocco_prompt(mp.profilo_esempio(), text_policy=policy)},
                         {"role": "user", "content": "Synthetic original question"}]}
    scope = {"phase": "committee", "agent": "macro", "round_n": 1}
    request_id, wire, replay = j.prepare(body, scope)
    assert replay is None
    response = {"id": "synthetic-response", "model": body["model"], "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": "Paid answer"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "cost": .00001}}
    assert j.receive(request_id, response) == "received"
    before = _righe(j)
    repeated_id, repeated_body, saved = j.prepare(body, scope)
    assert (repeated_id, repeated_body, saved) == (request_id, body, response)
    assert wire["messages"] == body["messages"]
    assert json.loads(before[0]["request"]) == body
    assert _righe(j) == before
    assert len(before) == 1


@pytest.mark.parametrize("held", [False, True])
def test_sizing_numbers_and_inputs_unchanged_by_labels(held):
    from copy import deepcopy
    from bellomberg.agents.trade_idea import _sizing_band_block
    from test_trade_idea_v4_pipeline import measured_sizing, book, TICKER, MANDATE
    sizing, portfolio, mandate = measured_sizing(held=held), book(held_value=4000 if held else None), deepcopy(MANDATE)
    before = deepcopy((sizing, portfolio, mandate))
    old = _sizing_band_block(sizing, portfolio, TICKER, mandate)
    new = _sizing_band_block(sizing, portfolio, TICKER, mandate, text_policy=POLICY)
    bands = json.loads(new.split(":\n", 1)[1])
    assert bands == json.loads(old.split(":\n", 1)[1])
    assert (sizing, portfolio, mandate) == before
    if held:
        assert "soglia minima d'azione del mandato (su NAV)" in new
        assert {key: (row["band_min_eur"], row["band_max_eur"]) for key, row in bands.items()} == {
            "ADD": (600.0, 2954.0), "TRIM": (600.0, 1000.0), "SELL": (600.0, 4000.0)}
    else:
        assert bands["BUY"]["band_min_eur"] == 1800.0


def test_small_holding_remains_optional_action_band():
    from bellomberg.agents.trade_idea import _sizing_band_block
    from test_trade_idea_v4_pipeline import measured_sizing, book, TICKER, MANDATE
    sizing = measured_sizing(held=True, current_eur=100.0)
    new = _sizing_band_block(sizing, book(held_value=100), TICKER, MANDATE, text_policy=POLICY)
    assert "non un peso minimo obbligatorio" in mp.blocco_prompt(mp.profilo_esempio(), text_policy=POLICY)
    bands = json.loads(new.split(":\n", 1)[1])
    assert bands["ADD"]["band_min_eur"] == 600
    assert bands["SELL"]["band_max_eur"] == 100
    assert "proposal=null" not in new  # Available action bands do not impose a trade.


@pytest.mark.parametrize("bad", [None, "", "mandate-sizing-labels/2"])
def test_real_capo_unknown_marker_stops_before_paid_reuse(research_board, bad):
    from bellomberg.agents.trade_idea import run_trade_idea_capo
    calls = []
    research_board.budget_gate.reuse_capo_response = lambda: calls.append("reuse")
    setattr(research_board, KEY, bad)
    with pytest.raises(ValueError, match="mandate text policy"):
        run_trade_idea_capo(research_board, portfolio={}, mandate={})
    assert calls == []


def test_real_capo_body_new_policy_and_legacy_unchanged(research_board, monkeypatch):
    from test_trade_idea_v4_pipeline import _capture, measured_sizing, HELD_BOOK, TICKER
    from bellomberg.core.trade_idea_policy import EXECUTION_POLICY_V4
    monkeypatch.setattr(mp, "blocco_prompt", _REAL_BLOCCO_PROMPT)
    m = mp.profilo_esempio()
    old = _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(held=True), portfolio=HELD_BOOK, mandate=m)
    setattr(research_board, KEY, POLICY)
    new = _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(held=True), portfolio=HELD_BOOK, mandate=m)
    assert "posizione minima" in old["system"]
    assert "soglia minima d'azione" in new["system"]
    assert "posizione minima" not in new["system"]
    for key in old.keys() - {"system", "messages"}:
        assert old[key] == new[key]
    delattr(research_board, KEY)
    assert _capture(research_board, EXECUTION_POLICY_V4, sizing=measured_sizing(held=True), portfolio=HELD_BOOK, mandate=m) == old


def test_checkpoint_hash_never_accepts_changed_label():
    from bellomberg.agents.specialists.base import _checkpoint_output_contract, _checkpoint_digest
    fields = {"max_tokens": 64000, "system": mp.blocco_prompt(mp.profilo_esempio())}
    saved = {"contract": _checkpoint_digest(fields), "max_tokens": 64000}
    assert _checkpoint_output_contract(fields, saved) == (64000, saved["contract"])
    changed = {**fields, "system": mp.blocco_prompt(mp.profilo_esempio(), text_policy=POLICY)}
    with pytest.raises(ValueError):
        _checkpoint_output_contract(changed, saved)


def test_weekly_factory_freezes_policy(monkeypatch):
    from bellomberg.agents.consigliere_multi import _weekly_contract
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "modello_o_buco", lambda *args: "synthetic-selected-model")
    assert _weekly_contract()[KEY] == POLICY


def test_pm_snapshot_preserved_verbatim_with_valid_policy(tmp_path):
    from bellomberg.agents.trade_idea import _bind_pm_constraints, PM_CONSTRAINTS_KEY
    saved = {"version": 1, "mandate": {"text": "Original paid mandate"}}
    board = SimpleNamespace(run_scope="trade_idea", data={PM_CONSTRAINTS_KEY: saved}, **{KEY: POLICY})
    assert _bind_pm_constraints(board, tmp_path / "must-not-open.db", {}, resumed=True) is saved
    assert not (tmp_path / "must-not-open.db").exists()


from test_desk_stream_journal import bb as desk_board

@pytest.mark.parametrize("bad", [None, "", "mandate-sizing-labels/2"])
def test_real_desk_invalid_marker_not_swallowed(desk_board, bad, monkeypatch):
    from test_desk_stream_journal import _Desk
    calls = []
    def forbidden(*a, **k):
        calls.append("provider")
        raise AssertionError("provider must not be reached")
    desk_board.weekly_store = SimpleNamespace(context={"contract": {KEY: bad}})
    desk = _Desk(desk_board, client=SimpleNamespace(messages=SimpleNamespace(create=forbidden, stream=forbidden)))
    with pytest.raises(ValueError, match="mandate text policy"):
        desk._run_loop(1)
    assert calls == []


@pytest.mark.parametrize("bad", [None, "", "mandate-sizing-labels/2"])
def test_weekly_capo_invalid_marker_zero_client(monkeypatch, bad):
    from bellomberg.agents import capo
    calls = []
    monkeypatch.setattr(capo, "_modello_llm", lambda *_: "synthetic-capo")
    monkeypatch.setattr(mp, "carica", mp.profilo_esempio)
    def forbidden(*a, **k):
        calls.append("client")
        raise AssertionError("client must not be reached")
    monkeypatch.setattr(capo, "OpenRouterClient", forbidden)
    bb = SimpleNamespace(data={}, weekly_store=SimpleNamespace(context={"contract": {KEY: bad}}))
    with pytest.raises(ValueError, match="mandate text policy"):
        capo.run_capo(bb)
    assert calls == []


@pytest.mark.parametrize("policy", [None, POLICY])
def test_native_snapshot_uses_policy_without_chroma(migrated, policy):
    from bellomberg.agents.trade_idea import _pm_constraints_snapshot
    snap = _pm_constraints_snapshot(migrated, mp.profilo_esempio(), origin="synthetic", text_policy=policy)
    assert snap["mandate"]["status"] == "available"
    assert snap["mandate"]["text"] == mp.blocco_prompt(mp.profilo_esempio(), text_policy=policy)


@pytest.mark.parametrize("bad", [None, "", "mandate-sizing-labels/2"])
def test_weekly_resume_refuses_unknown_before_pipeline(bad):
    from bellomberg.agents.consigliere_multi import _resume_publication_contract
    with pytest.raises(ValueError, match="mandate text policy"):
        _resume_publication_contract({KEY: POLICY}, {KEY: bad})


def test_new_english_sizing_note_names_action_amount():
    from bellomberg.agents.trade_idea import _sizing_band_block
    from test_trade_idea_v4_pipeline import measured_sizing, HELD_BOOK, TICKER, MANDATE
    with language_context("en"):
        block = _sizing_band_block(measured_sizing(held=True), HELD_BOOK, TICKER, MANDATE, text_policy=POLICY)
    assert "minimum action amount under the mandate (on NAV)" in block
