"""06/10/2026 (run Trade Idea del PM): un riferimento del Red Team fuori dal dossier sigillato non uccide la run.

La ripresa della run del 06/10 e' morta con «Red Team cites evidence outside the sealed candidate research»
per UNA obiezione non materiale che citava un tool fallito di un desk. Regola PM: la run
arriva in fondo, ogni buco dichiarato. Le obiezioni con fonti fuori dossier diventano
obiezioni inammissibili DICHIARATE (memo, Capo, controlli); se nessuna e' ammissibile la
critica e' una lacuna (red_team_complete falso: niente proposta operativa). Provider finto,
DB tmp.
"""
import json
from types import SimpleNamespace

import pytest

from bellomberg.agents import trade_idea
from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: F401
from _smtp_cattura import smtp  # noqa: F401
from test_trade_idea_source_research import frozen_clock  # noqa: F401
from test_trade_idea_store import db_path, migrated  # noqa: F401

OUTSIDE = "get_zz_tool_mai_chiamato"


def _red_refs(monkeypatch, which):
    """Aggiunge un riferimento fuori dossier alle obiezioni del Red Team scelte da `which`."""
    from bellomberg.agents.specialists import base
    from bellomberg.core import llm_client
    inner_client = trade_idea.OpenRouterClient

    class Messages:
        def __init__(self, inner):
            self.inner = inner

        def create(self, **kwargs):
            response = self.inner.create(**kwargs)
            name = (kwargs.get("response_format") or {}).get("json_schema", {}).get("name")
            if name == "trade_idea_committee_review":
                block = response.content[0]
                payload = json.loads(block.text)
                for row in payload["objections"]:
                    if which(row):
                        row["evidence_refs"] = [*row["evidence_refs"], OUTSIDE]
                block.text = json.dumps(payload)
            return response

        def stream(self, **kwargs):
            return self.inner.stream(**kwargs)

    class Client:
        def __init__(self, **kwargs):
            real = inner_client(**kwargs)
            self._http = real._http
            self.messages = Messages(real.messages)

    for owner in (base, llm_client, trade_idea):
        monkeypatch.setattr(owner, "OpenRouterClient", Client)


def test_una_obiezione_fuori_dossier_e_dichiarata_e_la_run_arriva_al_capo(no_workbook_case, monkeypatch):
    case = no_workbook_case
    _red_refs(monkeypatch, lambda row: row["desk"] == "crypto")
    ident = case.current.create_run(case.request, idempotency_key="red-one-outside")["run"]["id"]
    detail = case.execute(ident, "red-one")
    assert case.state["capo_calls"] == 1, detail["run"]["reason"]
    assert detail["run"]["technical_status"] in ("completed", "incomplete"), detail["run"]["reason"]
    assert "outside the sealed" not in (detail["run"]["reason"] or "")
    data = detail["progress"]["checkpoint"]["data"]
    rows = data["_red_inadmissible_objections"]
    assert [row["objection"]["id"] for row in rows] == ["challenge-crypto"]
    assert rows[0]["outside_refs"] == [OUTSIDE]
    assert "challenge-crypto" not in {row["objection"]["id"] for row in data["_objections"]}
    assert len(data["_objections"]) == len(trade_idea.TRADE_IDEA_DESKS) - 1
    assert not data.get("_red_team_gap")
    gaps = detail["result"]["data_gaps"]
    assert any("obiezione inammissibile" in gap and "challenge-crypto" in gap and OUTSIDE in gap
               for gap in gaps), gaps
    checks = detail["progress"]["routing_checks"]
    assert checks["evidence_sufficient"] is False and checks["red_team_complete"] is True
    annex = trade_idea._desk_annex(data)
    assert any(row["state"] == "inammissibile" and OUTSIDE in row["response"] for row in annex["ledger"])
    assert detail["cost"]["requests"] == len(case.providers)


def test_nessuna_obiezione_ammissibile_e_lacuna_red_team_mai_raise(no_workbook_case, monkeypatch):
    case = no_workbook_case
    _red_refs(monkeypatch, lambda row: True)
    ident = case.current.create_run(case.request, idempotency_key="red-all-outside")["run"]["id"]
    detail = case.execute(ident, "red-all")
    assert case.state["capo_calls"] == 1, detail["run"]["reason"]
    data = detail["progress"]["checkpoint"]["data"]
    assert data["_objections"] == []
    assert len(data["_red_inadmissible_objections"]) == len(trade_idea.TRADE_IDEA_DESKS)
    assert "Red Team non utilizzabile" in data["_red_team_gap"]
    checks = detail["progress"]["routing_checks"]
    assert checks["red_team_complete"] is False
    assert detail["run"]["destination"]["kind"] == "research"
    assert any("Red Team non utilizzabile" in gap for gap in detail["result"]["data_gaps"])


def test_le_ammissibili_passano_intatte():
    from types import SimpleNamespace
    board = SimpleNamespace(data={})
    review = {"objections": [{"id": "a", "evidence_refs": ["ok-1"]}, {"id": "b", "evidence_refs": ["ok-1", "zz"]}]}
    original = trade_idea._review_source_ids
    try:
        trade_idea._review_source_ids = lambda _board: {"ok-1"}
        assert [row["id"] for row in trade_idea._admit_red_objections(board, review)] == ["a"]
        assert board.data["_red_inadmissible_objections"][0]["outside_refs"] == ["zz"]
        assert "_red_team_gap" not in board.data
        review["objections"] = []
        assert trade_idea._admit_red_objections(board, review) == []
        assert "_red_team_gap" not in board.data and "_red_inadmissible_objections" not in board.data
    finally:
        trade_idea._review_source_ids = original


def test_contano_solo_le_inammissibili_materiali(monkeypatch):
    """PM 06/10: una inammissibile NON materiale resta dichiarata ma non blocca la proposta operativa."""
    from test_trade_idea_mutation_guards import _checks
    assert _checks(monkeypatch, {}) is True
    non_materiale = {"_red_inadmissible_objections": [{"objection": {"id": "x", "material": False},
                                                       "outside_refs": [OUTSIDE]}]}
    assert _checks(monkeypatch, non_materiale) is True
    materiale = {"_red_inadmissible_objections": [{"objection": {"id": "y", "material": True},
                                                   "outside_refs": [OUTSIDE]}]}
    assert _checks(monkeypatch, materiale) is False
    assert any("obiezione inammissibile" in gap for gap in trade_idea._red_inadmissible_gaps(
        SimpleNamespace(data=non_materiale)))
