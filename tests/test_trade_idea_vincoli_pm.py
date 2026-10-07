"""E7 (04/10/2026, Opus 5.5): vincoli e mandato del PM nella Trade Idea.

La Trade Idea consegna `memory_db=None` al Red Team e alla Blackboard (commit 1326312):
nessuna scrittura sul DB, ripresa solo dal checkpoint. Effetto collaterale misurato sulla
run PRY: il blocco delle parole vincolanti del PM (feedback e veti) era VUOTO e lo diceva
solo il log; il Red Team non riceveva nemmeno il mandato; i desk R1/R2 perdevano i vincoli
senza nemmeno la riga di log.

La cura (provata qui): una lettura in SOLA LETTURA all'avvio, fotografata nella blackboard
(quindi nel checkpoint), passata come TESTO; ogni buco e' dichiarato nel prompt e in data_gaps.
Ticker e testi inventati (ZZTEST, QQSYN): nessun dato del book.
"""
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from bellomberg.agents.specialists.base import Blackboard, Specialist
from bellomberg.core import mandato_pm
from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
from bellomberg.storage.memory_db import MemoryDB
from test_trade_idea_store import db_path, migrated, store  # noqa: F401 (fixture della run vera)
from _smtp_cattura import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401 (autouse: orologio della replica)
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401


VETO = "mai covered call su ZZTEST, nemmeno in variante"
FEEDBACK = "basta proporre QQSYN.MI sotto 123,45: non mi va"


def _sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def registro_path(tmp_path):
    db = MemoryDB(db_path=str(tmp_path / "data" / "consigliere.db"),
                  chroma_path=str(tmp_path / "chroma"))
    with sqlite3.connect(db.db_path) as con:
        con.execute("INSERT INTO decisions (timestamp, action, ticker, status, pm_feedback) "
                    "VALUES (?,?,?,?,?)", ("2026-08-10", "SELL", "QQSYN.MI", "SKIPPED", FEEDBACK))
        con.execute("INSERT INTO decisions (timestamp, action, ticker, status, veto, veto_reason, veto_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("2026-08-12", "SELL_CALL", "ZZTEST", "SKIPPED", 1, VETO, "2026-08-12"))
        con.commit()
    return db.db_path


@pytest.fixture
def db_vuoto(tmp_path):
    return MemoryDB(db_path=str(tmp_path / "vuoto" / "consigliere.db"),
                    chroma_path=str(tmp_path / "chroma-vuoto")).db_path


@pytest.fixture
def mandato():
    return mandato_pm.profilo_esempio()


# ============================================================
# 1. LA FOTOGRAFIA: sola lettura vera, tre stati dichiarati
# ============================================================

def test_fotografia_legge_i_vincoli_senza_scrivere_ne_costruire_memorydb(registro_path, mandato, monkeypatch):
    def vietato(*_a, **_k):
        raise AssertionError("la fotografia ha costruito un MemoryDB (DDL/chroma)")
    monkeypatch.setattr(MemoryDB, "_init_sqlite", vietato)
    monkeypatch.setattr(MemoryDB, "_init_chroma", vietato)
    prima = _sha(registro_path)
    snap = trade_idea._pm_constraints_snapshot(registro_path, mandato, origin="run_start")
    assert _sha(registro_path) == prima
    binding = snap["binding"]
    assert binding["status"] == "available" and binding["reason"] is None
    assert binding["state"] == "VINCOLANTI"
    assert VETO in binding["text"] and FEEDBACK in binding["text"]
    assert snap["mandate"]["status"] == "available"
    assert snap["mandate"]["text"] == mandato_pm.blocco_prompt(mandato)
    assert snap["mandate"]["fingerprint"] == mandato_pm.impronta(mandato)


def test_il_lettore_rifiuta_ogni_scrittura(registro_path):
    reader = trade_idea._pm_binding_reader(registro_path)
    prima = _sha(registro_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with reader._conn() as conn:
            conn.execute("DELETE FROM decisions")
    assert _sha(registro_path) == prima


def test_registro_vuoto_e_uno_zero_misurato(db_vuoto, mandato):
    snap = trade_idea._pm_constraints_snapshot(db_vuoto, mandato, origin="run_start")
    assert snap["binding"]["status"] == "available"
    assert "Nessun feedback" in snap["binding"]["text"]
    assert trade_idea.pm_constraints_gaps(SimpleNamespace(data={trade_idea.PM_CONSTRAINTS_KEY: snap})) == []


def test_db_assente_dichiarato_e_non_creato(tmp_path, mandato):
    path = tmp_path / "manca" / "consigliere.db"
    snap = trade_idea._pm_constraints_snapshot(str(path), mandato, origin="run_start")
    assert not path.exists() and not path.parent.exists()
    assert snap["binding"]["status"] == "unavailable"
    assert "vincoli del PM non disponibili: " in snap["binding"]["text"]
    assert "NON DISPONIBILI" in snap["binding"]["text"].splitlines()[0]
    assert snap["binding"]["state"].startswith("NON DISPONIBILI")
    gaps = trade_idea.pm_constraints_gaps(SimpleNamespace(data={trade_idea.PM_CONSTRAINTS_KEY: snap}))
    assert gaps == ["vincoli del PM non disponibili: " + snap["binding"]["reason"]]
    assert "database assente" in gaps[0]


def test_registro_guasto_dichiarato_col_motivo(tmp_path, mandato):
    path = tmp_path / "guasto.db"
    sqlite3.connect(path).close()  # file vero ma senza tabelle
    snap = trade_idea._pm_constraints_snapshot(str(path), mandato, origin="run_start")
    assert snap["binding"]["status"] == "unavailable"
    assert "no such table" in snap["binding"]["reason"]
    assert "vincoli del PM non disponibili" in snap["binding"]["text"]


def test_mandato_assente_dichiarato(registro_path):
    snap = trade_idea._pm_constraints_snapshot(registro_path, None, origin="run_start")
    assert snap["mandate"]["status"] == "unavailable"
    assert mandato_pm.riga_senza_mandato() in snap["mandate"]["text"]
    assert "[MANDATO n.d.]" in snap["mandate"]["text"]
    gaps = trade_idea.pm_constraints_gaps(SimpleNamespace(data={trade_idea.PM_CONSTRAINTS_KEY: snap}))
    assert gaps == ["mandato del PM non disponibile al Red Team: " + snap["mandate"]["reason"]]


def test_fotografia_assente_dichiarata_ovunque():
    board = SimpleNamespace(data={})
    vincoli, mandato_txt = trade_idea.pm_constraints_text(board)
    assert "NON DISPONIBILI" in vincoli and "vincoli del PM non disponibili" in vincoli
    assert "[MANDATO n.d.]" in mandato_txt
    assert len(trade_idea.pm_constraints_gaps(board)) == 2


def test_bind_alla_ripresa_tiene_la_fotografia_del_checkpoint(tmp_path, registro_path, mandato):
    board = SimpleNamespace(data={})
    first = trade_idea._bind_pm_constraints(board, registro_path, mandato)
    assert first["origin"] == "run_start"
    # ripresa: la fotografia salvata vale anche se il DB ora non c'e' piu'
    again = trade_idea._bind_pm_constraints(board, str(tmp_path / "sparito.db"), None, resumed=True)
    assert again is first and board.data[trade_idea.PM_CONSTRAINTS_KEY] is first
    # checkpoint precedente alla cura: lettura nuova, DICHIARATA come tale
    old = SimpleNamespace(data={})
    fresh = trade_idea._bind_pm_constraints(old, registro_path, mandato, resumed=True)
    assert "ripresa" in fresh["origin"] and fresh["binding"]["status"] == "available"


# ============================================================
# 2. IL RED TEAM: vincoli nel messaggio, mandato nel system
# ============================================================

class _Stop(BaseException):
    pass


def _red_board(snapshot=None, *, research=True, checkpoints=None):
    data = {"quant": {1: "Report di quant su ZZTEST. " * 10}}
    if snapshot is not None:
        data[trade_idea.PM_CONSTRAINTS_KEY] = snapshot
    written, failures = [], []
    board = SimpleNamespace(run_scope="trade_idea", target_ticker="ZZTEST", pm_view="view inventata",
        data=data, valuation_results={}, analysis_mode=RESEARCH_ANALYSIS_MODE if research else None,
        write=lambda name, rnd, text: written.append((name, rnd, text)),
        record_usage=lambda *a, **k: None,
        record_run_failure=lambda error, **k: failures.append(error))
    if checkpoints is not None:
        board.specialist_checkpoints = checkpoints
        board.persist_run_checkpoint = lambda *a, **k: None
    return board, written, failures


def _capture_red(monkeypatch, board):
    from bellomberg.agents import red_team
    from bellomberg.core import llm_client
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise _Stop()
    monkeypatch.setattr(trade_idea, "model_for_role", lambda role, contract=None: "synthetic/red-model")
    monkeypatch.setattr(trade_idea, "candidate_model_context", lambda *a, **k: {"status": "synthetic"})
    monkeypatch.setattr(red_team, "role_thinking", lambda *a, **k: {"type": "disabled"})
    monkeypatch.setattr(llm_client, "OpenRouterClient", lambda **k: SimpleNamespace())
    board.budget_gate = SimpleNamespace(wrap_client=lambda client, role: SimpleNamespace(messages=Messages()))
    return calls


def test_red_team_riceve_vincoli_e_mandato_come_testo(registro_path, mandato, monkeypatch):
    from bellomberg.agents import red_team
    snap = trade_idea._pm_constraints_snapshot(registro_path, mandato, origin="run_start")
    board, _w, _f = _red_board(snap)
    calls = _capture_red(monkeypatch, board)
    with pytest.raises(_Stop):
        red_team.run_red_team(board, portfolio_data=None, memory_db=None)
    user = calls[0]["messages"][0]["content"]
    assert VETO in user and FEEDBACK in user
    assert user.index(VETO) < user.index("Report di quant")
    assert mandato_pm.blocco_prompt(mandato) in calls[0]["system"]


def test_red_team_dichiara_mandato_e_vincoli_mancanti(tmp_path, monkeypatch):
    from bellomberg.agents import red_team
    snap = trade_idea._pm_constraints_snapshot(str(tmp_path / "manca.db"), None, origin="run_start")
    board, _w, _f = _red_board(snap, research=False)
    calls = _capture_red(monkeypatch, board)
    with pytest.raises(_Stop):
        red_team.run_red_team(board, portfolio_data=None, memory_db=None)
    assert "vincoli del PM non disponibili" in calls[0]["messages"][0]["content"]
    assert "[MANDATO n.d.]" in calls[0]["system"]
    assert mandato_pm.riga_senza_mandato() in calls[0]["system"]


def test_red_team_senza_fotografia_dichiara_non_tace(monkeypatch):
    from bellomberg.agents import red_team
    board, _w, _f = _red_board(None)
    calls = _capture_red(monkeypatch, board)
    with pytest.raises(_Stop):
        red_team.run_red_team(board, portfolio_data=None, memory_db=None)
    assert "fotografia dei vincoli assente" in calls[0]["messages"][0]["content"]
    assert "[MANDATO n.d.]" in calls[0]["system"]


def test_checkpoint_red_team_precedente_non_si_riprende_e_lo_dichiara(registro_path, mandato, monkeypatch):
    from bellomberg.agents import red_team
    from bellomberg.agents.specialists.base import _checkpoint_digest
    snap = trade_idea._pm_constraints_snapshot(registro_path, mandato, origin="run_start")
    reference = {key: None for key in ("snapshot_id", "generation_id", "workbook_sha256")}
    key = "red_team:R1:model:" + _checkpoint_digest(reference)
    saved = {"contract": "contract-precedente-alla-cura", "max_tokens": 65536, "status": "running",
             "user_msg": "messaggio salvato", "tools_schema": [], "messages": [], "iteration": 0,
             "usage": {}, "usage_unknown": False, "calls": 0}
    saved["sha256"] = _checkpoint_digest(saved)
    board, written, failures = _red_board(snap, checkpoints={key: saved})
    calls = _capture_red(monkeypatch, board)
    with pytest.raises(ValueError, match="non riprendibile"):
        red_team.run_red_team(board, portfolio_data=None, memory_db=None)
    assert calls == []  # nessuna chiamata pagata
    assert failures and "contract changed" in str(failures[0])
    assert written and written[0][0] == "_red_team"
    assert written[0][2].startswith(red_team.SEGNAPOSTO_NON_DISPONIBILE)
    assert "non riprendibile" in written[0][2]


def test_red_team_settimanale_resta_sul_memory_db(registro_path, monkeypatch):
    """Il Consigliere non passa dalla fotografia: legge ancora il suo memory_db."""
    from bellomberg.agents import red_team
    seen = []
    monkeypatch.setattr(trade_idea, "pm_constraints_text",
                        lambda board: seen.append(board) or ("", ""))
    board = SimpleNamespace(run_scope="weekly", data={"quant": {1: "report"}}, memory_db=None)
    monkeypatch.setattr(red_team, "_run_red_team_loop", lambda bb, ti, model, user_msg, *a: user_msg)
    monkeypatch.setattr(red_team, "_modello_llm", lambda role: "synthetic", raising=False)
    from bellomberg.core import llm_client
    monkeypatch.setattr(llm_client, "modello", lambda role: "synthetic")
    reader = trade_idea._pm_binding_reader(registro_path)
    user = red_team.run_red_team(board, portfolio_data=None, memory_db=reader)
    assert VETO in user and seen == []


# ============================================================
# 3. I DESK DELLA TRADE IDEA: il blocco entra nei due rami del prompt
# ============================================================

class _Desk(Specialist):
    name = "quant"
    system_prompt = "test"
    tools_used = []

    def compute_score(self):
        return None


@pytest.mark.parametrize("research", [True, False])
def test_desk_trade_idea_ricevono_i_vincoli(tmp_path, registro_path, mandato, research, capsys):
    board = Blackboard(memory_db=None, heartbeat_path=tmp_path / "heartbeat.json",
                       run_scope="trade_idea", run_id="e7-desk", target_ticker="ZZTEST",
                       budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    board.analysis_mode = RESEARCH_ANALYSIS_MODE if research else None
    board.pm_view = "view inventata"
    trade_idea._bind_pm_constraints(board, registro_path, mandato)
    prompt = _Desk(board, client=object())._build_round_context(0)
    assert VETO in prompt and FEEDBACK in prompt
    assert "vincoli PM R0 (trade idea): VINCOLANTI" in capsys.readouterr().out


def test_desk_trade_idea_senza_fotografia_dichiarano(tmp_path):
    board = Blackboard(memory_db=None, heartbeat_path=tmp_path / "heartbeat.json",
                       run_scope="trade_idea", run_id="e7-desk", target_ticker="ZZTEST",
                       budget_gate=SimpleNamespace(wrap_client=lambda client, role: client))
    board.analysis_mode = RESEARCH_ANALYSIS_MODE
    prompt = _Desk(board, client=object())._build_round_context(1)
    assert "vincoli del PM non disponibili" in prompt


# ============================================================
# 4. IL CABLAGGIO: una run vera (provider sintetici) porta i vincoli al Red Team
# ============================================================

def test_cablaggio_run_completa_vincoli_e_mandato_al_red_team(no_workbook_case):
    """Run research vera (classi vere, provider sintetico, DB alternativo) con un veto
    sintetico a registro: se la riga che chiama _bind_pm_constraints sparisce, Red Team e
    desk ricevono la frase «fotografia assente» e questo test cade."""
    case = no_workbook_case
    with sqlite3.connect(case.database) as conn:
        conn.execute("INSERT INTO decisions(timestamp,action,ticker,rationale,status,veto,veto_reason) "
                     "VALUES('2026-09-01','SELL_CALL','ZZTEST','tesi inventata','SKIPPED',1,?)", (VETO,))
    with sqlite3.connect(case.database) as conn:
        prima = conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    case.state.update(judgment='favorable', source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key='e7-vincoli')['run']['id']
    detail = case.execute(ident, 'e7')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    snap = detail['progress']['checkpoint']['data'][trade_idea.PM_CONSTRAINTS_KEY]
    assert snap['origin'] == 'run_start' and snap['binding']['state'] == 'VINCOLANTI', snap['binding']
    assert snap['mandate']['status'] == 'available'
    reds = [call for call in case.providers if (call.get('response_format') or {}).get(
        'json_schema', {}).get('name') == 'trade_idea_committee_review']
    assert reds, "il Red Team non e' stato chiamato"
    for call in reds:
        assert VETO in str(call['messages'][0]['content'])
        assert snap['mandate']['text'] in str(call['system'])
        assert 'fotografia dei vincoli assente' not in str(call['messages'][0]['content'])
    desks = [call for call in case.providers if call not in reds
             and 'TRADE IDEA: research on the exact' in str(call['messages'][0].get('content'))]  # i desk; il Capo ha un altro canale
    senza = [str(call['messages'][0]['content'])[:200] + ' || ' + str(call.get('system'))[:120]
             for call in desks if VETO not in str(call['messages'][0]['content'])]
    assert desks and not senza, senza
    assert not any('vincoli del PM' in gap for gap in detail['result']['data_gaps'])
    with sqlite3.connect(case.database) as conn:
        assert conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()[:len(prima)] == prima


def test_cablaggio_registro_guasto_finisce_in_data_gaps_e_nel_prompt(no_workbook_case, monkeypatch):
    """Il buco arriva al PM: frase in data_gaps del risultato e riga NON DISPONIBILI nel prompt."""
    case = no_workbook_case

    def guasto():
        raise sqlite3.OperationalError("registro sintetico guasto")
    monkeypatch.setattr(trade_idea, "_pm_binding_reader",
                        lambda path: SimpleNamespace(build_pm_binding_block=guasto))
    case.state.update(judgment='favorable', source_gaps=False)
    ident = case.current.create_run(case.request, idempotency_key='e7-guasto')['run']['id']
    detail = case.execute(ident, 'e7-guasto')
    assert detail['run']['technical_status'] in ('completed', 'incomplete'), detail['run']['reason']
    gap = [g for g in detail['result']['data_gaps'] if g.startswith('vincoli del PM non disponibili')]
    assert gap == ["vincoli del PM non disponibili: registro non ha risposto "
                   "(OperationalError: registro sintetico guasto)"]
    reds = [call for call in case.providers if (call.get('response_format') or {}).get(
        'json_schema', {}).get('name') == 'trade_idea_committee_review']
    assert reds and all("vincoli del PM non disponibili" in str(call['messages'][0]['content'])
                        for call in reds)


def test_cablaggio_ripresa_da_checkpoint_senza_fotografia_dichiarata(no_workbook_case, monkeypatch):
    """Review RV-E7 (P2): la prima corsa gira col codice di PRIMA (nessuna fotografia nel
    checkpoint) e cade dopo R0; il PM aggiunge un veto; la ripresa legge i vincoli di ADESSO.
    Il veto nuovo entra nel prompt del Red Team, e il memo DICE che i round chiusi prima della
    ripresa non lo avevano. Cade se la lettura va prima del ripristino del checkpoint (la
    ripresa la cancella) o se la ripresa non si dichiara (resumed=False)."""
    case = no_workbook_case
    vero = trade_idea._bind_pm_constraints
    monkeypatch.setattr(trade_idea, "_bind_pm_constraints", lambda *a, **k: None)  # codice pre-cura
    parent = case.current.create_run(case.request, idempotency_key='e7-ripresa')['run']['id']
    case.state['stop_after'] = 0
    first = case.execute(parent, 'e7-parent')
    assert 'native R0' in first['run']['reason'], first['run']['reason']
    assert trade_idea.PM_CONSTRAINTS_KEY not in first['progress']['checkpoint']['data']
    monkeypatch.setattr(trade_idea, "_bind_pm_constraints", vero)
    with sqlite3.connect(case.database) as conn:
        conn.execute("INSERT INTO decisions(timestamp,action,ticker,rationale,status,veto,veto_reason) "
                     "VALUES('2026-09-02','SELL_CALL','ZZTEST','tesi inventata','SKIPPED',1,?)", (VETO,))
    child = case.current.create_continuation(parent, idempotency_key='e7-ripresa-figlia',
                                             authorize_new_requests=True)['run']['id']
    case.state['stop_after'] = None
    already = len(case.providers)
    detail = case.execute(child, 'e7-child')
    assert detail['run']['technical_status'] == 'completed', detail['run']['reason']
    snap = detail['progress']['checkpoint']['data'][trade_idea.PM_CONSTRAINTS_KEY]
    assert snap['origin'] == "letti alla ripresa: il checkpoint precedente non li conteneva"
    assert snap['binding']['state'] == 'VINCOLANTI'
    gap = [g for g in detail['result']['data_gaps'] if g.startswith('vincoli del PM letti alla ripresa')]
    assert gap == ["vincoli del PM letti alla ripresa (" + snap['read_at'][:16]
                   + " UTC): i round completati prima della ripresa non li avevano ricevuti"]
    reds = [call for call in case.providers[already:] if (call.get('response_format') or {}).get(
        'json_schema', {}).get('name') == 'trade_idea_committee_review']
    assert reds and all(VETO in str(call['messages'][0]['content']) for call in reds)


@pytest.mark.parametrize("cap_name", ["TRADE_IDEA_RED_MAX_TOKENS", "legacy"])
def test_critica_pagata_pre_cura_si_riusa_col_system_originale_e_si_dichiara(registro_path, mandato,
                                                                             monkeypatch, cap_name):
    """Review RV-E7 (P1): un checkpoint Red Team COMPLETO scritto prima che il mandato entrasse
    nel system si riusa esatto (0 chiamate, critica pagata restituita) e lo si DICHIARA."""
    from bellomberg.agents import red_team, chat_tools
    from bellomberg.agents.specialists.base import _checkpoint_digest
    from bellomberg.core.language import prompt_for_language
    from bellomberg.core.trade_idea_contract import RESEARCH_RED_TEAM_INSTRUCTIONS
    snap = trade_idea._pm_constraints_snapshot(registro_path, mandato, origin="run_start")
    reference = {key: None for key in ("snapshot_id", "generation_id", "workbook_sha256")}
    key = "red_team:R1:model:" + _checkpoint_digest(reference)
    tools = [t for t in chat_tools.TOOL_DEFINITIONS
             if t["name"] in ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")]
    cap = red_team.TRADE_IDEA_RED_MAX_TOKENS if cap_name != "legacy" else 65536
    contract = _checkpoint_digest({"version": 1, "model": "synthetic/red-model",
        "system": prompt_for_language(RESEARCH_RED_TEAM_INSTRUCTIONS), "tools": tools,
        "iterations": 4, "max_tokens": cap, "thinking": {"type": "disabled"}})
    saved = {"contract": contract, "max_tokens": cap, "status": "complete",
             "critique": "critica pagata su ZZTEST", "user_msg": "messaggio salvato",
             "tools_schema": tools, "messages": [], "iteration": 1, "usage": {},
             "usage_unknown": False, "calls": 1}
    saved["sha256"] = _checkpoint_digest(saved)
    board, _written, failures = _red_board(snap, checkpoints={key: saved})
    calls = _capture_red(monkeypatch, board)
    assert red_team.run_red_team(board, portfolio_data=None, memory_db=None) == "critica pagata su ZZTEST"
    assert calls == [] and failures == []
    assert board.data["_red_team_system_pre_vincoli"] is True
    assert ("critica del Red Team ripresa da checkpoint precedente: non conteneva il mandato del PM"
            in trade_idea.pm_constraints_gaps(board))


def test_loop_pre_cura_ripreso_manda_il_body_pagato_identico(registro_path, mandato, monkeypatch):
    """Un loop Red Team pre-cura ancora aperto riparte col system ORIGINALE (senza mandato):
    il body ripreso e' quello gia' pagato, non uno nuovo."""
    from bellomberg.agents import red_team, chat_tools
    from bellomberg.agents.specialists.base import _checkpoint_digest
    from bellomberg.core.language import prompt_for_language
    from bellomberg.core.trade_idea_contract import RESEARCH_RED_TEAM_INSTRUCTIONS
    snap = trade_idea._pm_constraints_snapshot(registro_path, mandato, origin="run_start")
    reference = {key: None for key in ("snapshot_id", "generation_id", "workbook_sha256")}
    key = "red_team:R1:model:" + _checkpoint_digest(reference)
    tools = [t for t in chat_tools.TOOL_DEFINITIONS
             if t["name"] in ("get_portfolio_live", "get_portfolio_risk", "get_advanced_metrics")]
    original = prompt_for_language(RESEARCH_RED_TEAM_INSTRUCTIONS)
    cap = red_team.TRADE_IDEA_RED_MAX_TOKENS
    saved = {"contract": _checkpoint_digest({"version": 1, "model": "synthetic/red-model",
             "system": original, "tools": tools, "iterations": 4, "max_tokens": cap,
             "thinking": {"type": "disabled"}}), "max_tokens": cap, "status": "running",
             "user_msg": "messaggio salvato", "tools_schema": tools,
             "messages": [{"role": "user", "content": "messaggio salvato"}], "iteration": 0,
             "usage": {}, "usage_unknown": False, "calls": 0}
    saved["sha256"] = _checkpoint_digest(saved)
    board, _written, _failures = _red_board(snap, checkpoints={key: saved})
    import threading
    board._lock = threading.RLock()
    calls = _capture_red(monkeypatch, board)
    with pytest.raises(_Stop):
        red_team.run_red_team(board, portfolio_data=None, memory_db=None)
    assert calls and calls[0]["system"] == original
    assert calls[0]["messages"][0]["content"] == "messaggio salvato"
