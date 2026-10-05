# -*- coding: utf-8 -*-
"""Gate di pubblicazione delle proposte del comitato (04/10, gruppo G6, review 04).

Cosa deve essere vero per il PM che riceve il memo:
  - una BUY/ADD su un modello in sanity BLOCK non arriva MAI come operativa, comunque il
    Capo scriva l'azione (`**BUY**`, intestazione in grassetto, seconda tabella sotto);
  - una riga che il codice non sa leggere (azione fuori elenco, importo illeggibile) non e'
    mai operativa: e' dichiarata «non verificabile»;
  - una BUY valida RESTA nella ACTION TABLE pubblicata e nel registro come operativa;
  - il sizing non blocca mai: e' un avviso nel memo, con la tolleranza del MANDATO;
  - ETF/crypto/fondi senza modello restano operativi con l'etichetta «senza valutazione»;
  - la ripresa di una run e il retry non falliscono perche' le esclusioni sono gia' chiuse;
  - una proposta non operativa ha una via d'uscita: la divergenza manuale del PM.

Ticker e importi INVENTATI (ZZ*), nessun dato del book.
"""
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from bellomberg.storage.memory_db import MemoryDB
from bellomberg.agents import action_validator as av
from bellomberg.agents import consigliere_multi as cm
from bellomberg.agents.action_table_extract import parse_action_table_rows
from bellomberg.core import mandato_pm as mp
from bellomberg.storage import classificazione

H = ("# Memo\n\n## ACTION TABLE\n"
     "| Action | Ticker | EUR | Timing | Confidence | Rationale |\n"
     "|---|---|---|---|---|---|\n")
CODA = "\n## 2. Analisi\ntesto del Capo\n"


def _sidecar(report, ticker, severity, ts="2026-09-30T10:00:00"):
    os.makedirs(report, exist_ok=True)
    base = "VAL_" + ticker.replace(".", "_")
    name = base + ("_FLAGGED.payload.json" if severity == "BLOCK" else ".payload.json")
    with open(os.path.join(report, name), "w", encoding="utf-8") as f:
        json.dump({"ticker": ticker, "_timestamp": ts,
                   "sanity": {"severity": severity, "headline": "giudizio di prova"}}, f)


@pytest.fixture
def report(tmp_path):
    rep = str(tmp_path / "report")
    _sidecar(rep, "ZZBLK", "BLOCK")
    _sidecar(rep, "ZZOK", "OK")
    _sidecar(rep, "ZZWRN", "WARN")
    return rep


@pytest.fixture
def negozio(tmp_path, monkeypatch):
    voce = {"provenienza": "dichiarato", "settore_policy": "banks", "classe_size": "single",
            "verificato_il": "2026-09-01", "note": "voce di prova"}
    dati = {"ZZETF.MI": dict(voce, tipo="etf", classe_size="veicolo"),
            "ZZCOIN": dict(voce, tipo="crypto", classe_size="veicolo"),
            "ZZOPCO": dict(voce, tipo="operating")}
    p = tmp_path / "veicoli.json"
    p.write_text(json.dumps(dati), encoding="utf-8")
    monkeypatch.setattr(classificazione, "PERCORSO_VEICOLI", str(p))
    n = classificazione.carica_veicoli(str(p))
    assert n["origine"] == str(p), n["motivo"]
    return n


def _no_chroma(self):
    self.chroma_client = None
    self.col_memos = None
    self.col_decisions = None
    self.col_feedback = None


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(MemoryDB, "_init_chroma", _no_chroma)
    return MemoryDB(db_path=str(tmp_path / "data" / "g6.db"), chroma_path=str(tmp_path / "data" / "chroma"))


def _esiti(memo, report, negozio, sizing=None):
    sanity = av.collect_sanity_exclusions(memo, report_dir=report, negozio=negozio)
    return av.assess_action_table(memo, sizing, sanity_exclusions=sanity)


def _righe_pubblicate(memo_pubblicato):
    sezione = memo_pubblicato.split("## ACTION TABLE", 1)[1].split("\n## ", 1)[0]
    return [l for l in sezione.splitlines() if l.startswith("|")]


# --------------------------------------------------------------------------- G1 / G5: un parser solo

def test_g1_buy_in_grassetto_su_ticker_in_block_non_e_operativa(report, negozio):
    memo = H + "| **BUY** | ZZBLK | 5000 | ora | ALTA | x |\n| BUY | ZZOK | 1000 | ora | ALTA | y |\n" + CODA
    esiti = _esiti(memo, report, negozio)
    assert [(e["action"], e["status"]) for e in esiti] == [("BUY", "BLOCKED"), ("BUY", "OPERATIVE")]
    pubblicato = cm.build_publication_snapshot(memo, esiti)["memo_markdown"]
    righe = _righe_pubblicate(pubblicato)
    assert not any("ZZBLK" in r for r in righe), righe
    assert any("ZZOK" in r for r in righe), righe
    # l'auto-chiusura nel registro vede la stessa riga
    _, pairs = av.detect_sanity_exclusions(memo, report_dir=report)
    assert pairs == [("BUY", "ZZBLK")]


def test_g1_azione_fuori_elenco_e_importo_illeggibile_non_sono_operativi(report, negozio):
    memo = (H + "| BUY ⚠ | ZZOK | 1000 | ora | ALTA | x |\n"
            "| ADD (tranche 1) | ZZOK | 500 | ora | ALTA | x |\n"
            "| BUY | ZZOK | tanto | ora | ALTA | x |\n"
            "| TRIM | ZZOK | 200 | ora | ALTA | x |\n" + CODA)
    esiti = _esiti(memo, report, negozio)
    assert [e["status"] for e in esiti] == ["CHECK_UNAVAILABLE"] * 3 + ["OPERATIVE"]
    assert "fuori dall'elenco" in esiti[0]["reason"] and "illeggibile" in esiti[2]["reason"]
    righe = _righe_pubblicate(cm.build_publication_snapshot(memo, esiti)["memo_markdown"])
    assert len(righe) == 3 and "TRIM" in righe[2], righe   # header, separatore, TRIM


def test_g5_intestazione_in_grassetto_la_buy_valida_resta_operativa(report, negozio):
    memo = ("# Memo\n\n## ACTION TABLE\n"
            "| **Action** | **Ticker** | **EUR** | **Timing** | **Confidence** |\n"
            "|---|---|---|---|---|\n| BUY | ZZOK | 1000 | ora | ALTA |\n| TRIM | ZZWRN | 2000 | ora | MEDIA |\n" + CODA)
    esiti = _esiti(memo, report, negozio)
    assert [(e["row_index"], e["ticker"], e["status"]) for e in esiti] == [
        (0, "ZZOK", "OPERATIVE"), (1, "ZZWRN", "OPERATIVE")]
    # validator, gate e registro contano le righe allo stesso modo
    assert ([(r["row_index"], r["ticker"]) for r in av._parse_action_rows(memo)]
            == [(r["row_index"], r["ticker"]) for r in parse_action_table_rows(memo)["rows"]])
    righe = _righe_pubblicate(cm.build_publication_snapshot(memo, esiti)["memo_markdown"])
    assert any("ZZOK" in r for r in righe)


def test_g5_seconda_tabella_nella_sezione_non_e_una_proposta(report, negozio):
    memo = (H + "| BUY | ZZOK | 1000 | ora | ALTA | x |\n\nWatchlist:\n\n"
            "| Ticker | Trigger | Note | X | Y |\n|---|---|---|---|---|\n| ZZWRN | 150 | a | b | c |\n" + CODA)
    righe = parse_action_table_rows(memo)["rows"]
    assert [(r["action"], r["ticker"]) for r in righe] == [("BUY", "ZZOK")]
    assert [e["status"] for e in _esiti(memo, report, negozio)] == ["OPERATIVE"]


def test_g5_intestazione_illeggibile_nessuna_riga_resta_operativa(report, negozio):
    memo = ("# Memo\n\n## ACTION TABLE\n| Cosa | Dove | Quanto |\n|---|---|---|\n"
            "| BUY | ZZBLK | 5000 |\n" + CODA)
    parsed = parse_action_table_rows(memo)
    assert parsed["rows"] == [] and "intestazione" in parsed["error"]
    snap = cm.build_publication_snapshot(memo, [], finalization_error=parsed["error"])
    assert "| BUY | ZZBLK | 5000 |" not in snap["memo_markdown"].split("## ACTION TABLE", 1)[1]
    assert snap["nonoperative"][0]["ticker"] == "ZZBLK"
    assert snap["nonoperative"][0]["status"] == "CHECK_UNAVAILABLE"


# --------------------------------------------------------------------------- G5 sanity canonica (review G6)

def _payload_storico_sanabile(fair_value=1100.0):
    price = 100.0
    ratio = fair_value / price
    distance = abs(ratio - 1.0)
    headline = ('VAL SOSPETTA: fair value %.0f diverge %.0f%% dal prezzo %.0f (ratio %.2f). '
                'Rivedere growth/margini/WACC/shares/net_debt PRIMA di fidarsi. '
                'ESCLUSO dalla ACTION TABLE.') % (fair_value, distance * 100, price, ratio)
    return {'ticker': 'ZZOLD', 'price': price, 'fair_value_base': fair_value,
            'valuation_flagged': True, 'sanity_headline': headline, '_timestamp': '2026-10-01T12:00:00',
            'sanity': {'status': 'ok', 'ratio': round(ratio, 2),
                       'upside_pct': round((ratio - 1) * 100, 1), 'severity': 'BLOCK',
                       'exclude_from_action_table': True, 'headline': headline}}


def test_g5_il_gate_legge_la_sanity_con_la_policy_corrente(report, negozio):
    with open(os.path.join(report, "VAL_ZZOLD_FLAGGED.payload.json"), "w", encoding="utf-8") as f:
        json.dump(_payload_storico_sanabile(), f)
    memo = H + "| BUY | ZZOLD | 1000 | ora | ALTA | x |\n" + CODA
    sanity = av.collect_sanity_exclusions(memo, report_dir=report, negozio=negozio)
    assert sanity[0]["severity"] == "OK"                      # come _canonical_sanity
    assert av._canonical_sanity("ZZOLD", report)[0] == "OK"
    assert [e["status"] for e in _esiti(memo, report, negozio)] == ["OPERATIVE"]
    assert cm._publication_hard_pairs([{"action": "BUY", "ticker": "ZZOLD"}], sanity, []) == []


# --------------------------------------------------------------------------- decisione PM A

SIZING = {"summary": {"invested_capital_eur": 180000, "cash_buffer_eur": 20000,
                      "deployable_from_cash_eur": 3000, "params": {"single_base_pct": 2},
                      "stress_var_budget": {"binding": False}},
          "positions": [{"ticker": "ZZOK", "remaining_capacity_eur": 1000, "verdict": "spazio"}]}


def test_a_il_sizing_non_blocca_e_resta_avviso(report, negozio):
    memo = (H + "| ADD | ZZOK | 9000 | ora | ALTA | x |\n| TRIM | ZZWRN | 4000 | ora | ALTA | rotazione |\n"
            "| BUY | ZZETF.MI | 8000 | ora | ALTA | SOPRA POLICY |\n" + CODA)
    esiti = _esiti(memo, report, negozio, sizing=SIZING)
    assert [e["status"] for e in esiti] == ["OPERATIVE"] * 3     # oltre nome, oltre aggregato
    blocco = av.build_validator_block(memo, SIZING, None, sanity_exclusions=av.collect_sanity_exclusions(
        memo, report_dir=report, negozio=negozio), mandato=mp.profilo_esempio())
    assert "oltre la policy di sizing" in blocco and "ADD ZZOK" in blocco
    assert "DEROGA DICHIARATA" in blocco                         # il tag da solo basta (regola 15/07)
    assert "**SENZA VALUTAZIONE** BUY ZZETF.MI" in blocco


def test_a_etf_e_crypto_senza_modello_restano_operativi_con_etichetta(report, negozio):
    memo = (H + "| BUY | ZZETF.MI | 1000 | ora | ALTA | x |\n| ADD | ZZCOIN | 500 | ora | ALTA | x |\n"
            "| BUY | ZZOPCO | 700 | ora | ALTA | x |\n| BUY | ZZIGNOTO | 700 | ora | ALTA | x |\n" + CODA)
    esiti = _esiti(memo, report, negozio)
    assert [e["status"] for e in esiti] == ["OPERATIVE", "OPERATIVE", "CHECK_UNAVAILABLE", "CHECK_UNAVAILABLE"]
    assert esiti[0]["senza_valutazione"] and "SENZA VALUTAZIONE" in esiti[0]["reason"]
    assert "operating" in esiti[2]["reason"] and "non dichiarata" in esiti[3]["reason"]
    snap = cm.build_publication_snapshot(memo, esiti)
    sezione = snap["memo_markdown"].split("## PROPOSTE OPERATIVE SENZA VALUTAZIONE", 1)[1].split("\n## ", 1)[0]
    assert "ZZETF.MI" in sezione and "ZZCOIN" in sezione and "ZZOPCO" not in sezione


# --------------------------------------------------------------------------- decisione PM B: il mandato

def test_b_la_tolleranza_viene_dal_mandato_in_percentuale_dell_investito():
    m = mp.profilo_esempio()
    m["sizing"]["tolleranza_sforo_sizing_pct"] = 0.5
    assert mp.tolleranza_sizing_eur(m, 200000) == (1000.0, None)
    m["sizing"]["tolleranza_sforo_sizing_pct"] = None
    tol, motivo = mp.tolleranza_sizing_eur(m, 200000)
    assert tol is None and "tolleranza_sforo_sizing_pct" in motivo
    assert mp.CAMPI["tolleranza_sforo_sizing_pct"]["obbligatorio"] is False
    assert mp.CAMPI["tolleranza_sforo_sizing_pct"]["intervallo"] == [0, 5]


def test_b_sotto_tolleranza_niente_avviso_sopra_avviso(report, negozio):
    # investito = 180000; sforo ADD = 1400 oltre i 1000 di spazio
    memo = H + "| ADD | ZZOK | 2400 | ora | ALTA | x |\n" + CODA
    sanity = av.collect_sanity_exclusions(memo, report_dir=report, negozio=negozio)
    m = mp.profilo_esempio()
    m["sizing"]["tolleranza_sforo_sizing_pct"] = 1.0               # 1800 EUR
    assert "oltre la policy" not in av.build_validator_block(memo, SIZING, None, sanity_exclusions=sanity, mandato=m)
    m["sizing"]["tolleranza_sforo_sizing_pct"] = 0.5               # 900 EUR
    assert "oltre la policy" in av.build_validator_block(memo, SIZING, None, sanity_exclusions=sanity, mandato=m)
    m["sizing"]["tolleranza_sforo_sizing_pct"] = None
    blocco = av.build_validator_block(memo, SIZING, None, sanity_exclusions=sanity, mandato=m)
    assert "CONTROLLO SIZING NON ESEGUITO" in blocco and "tolleranza_sforo_sizing_pct" in blocco


def test_b_senza_tolleranza_il_comitato_non_parte_trade_idea_si(tmp_path, monkeypatch, capsys):
    m = mp.profilo_esempio()
    m["sizing"].pop("tolleranza_sforo_sizing_pct")
    p = tmp_path / "mandato_senza_tolleranza.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(mp, "PERCORSO_MANDATO", str(p))
    assert mp.carica()["sizing"]["base_single_pct"] == m["sizing"]["base_single_pct"]   # Trade Idea/chat
    with pytest.raises(SystemExit) as ei:
        cm.mandato_o_esci()
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert "MANDATO NON DICHIARATO" in out and "tolleranza_sforo_sizing_pct" in out


# --------------------------------------------------------------------------- end-to-end sul registro

def _gate(db, memo_id, memo, report, monkeypatch, sanity_pairs=()):
    monkeypatch.setattr(cm, "REPORT_DIR", report)
    bb = SimpleNamespace(data={"_sizing": SIZING}, valuation_results={})
    store = SimpleNamespace(request_journal=None)
    return cm._publication_gate(bb, db, store, memo_id, memo, memo, None, list(sanity_pairs))


def _decisioni(db, memo_id):
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {r["ticker"]: dict(r) for r in conn.execute(
            "SELECT * FROM decisions WHERE memo_id=? AND status != 'EXPIRED'", (memo_id,))}


def test_e2e_la_buy_valida_resta_operativa_e_la_ripresa_non_fallisce(db, report, negozio, monkeypatch):
    memo = (H + "| BUY | ZZOK | 1000 | ora | ALTA | x |\n| **BUY** | ZZBLK | 5000 | ora | ALTA | x |\n"
            "| BUY | ZZETF.MI | 800 | ora | ALTA | x |\n| BUY | ZZOPCO | 700 | ora | ALTA | x |\n"
            "| BUY ⚠ | ZZWRN | 600 | ora | ALTA | x |\n" + CODA)
    memo_id = db.save_memo(memo)
    gate = _gate(db, memo_id, memo, report, monkeypatch)
    assert gate["decisions_error"] is None, gate["decisions_error"]
    righe = _righe_pubblicate(gate["publication"]["memo_markdown"])
    assert any("ZZOK" in r for r in righe) and any("ZZETF.MI" in r for r in righe)
    assert not any(t in r for r in righe for t in ("ZZBLK", "ZZOPCO", "ZZWRN")), righe
    dec = _decisioni(db, memo_id)
    assert dec["ZZOK"]["assessment_status"] == "OPERATIVE" and dec["ZZOK"]["status"] == "PENDING"
    assert dec["ZZETF.MI"]["assessment_status"] == "OPERATIVE"
    assert dec["ZZBLK"]["assessment_status"] == "BLOCKED" and dec["ZZBLK"]["status"] == "SKIPPED"
    assert "AUTO-ESCLUSA" in dec["ZZBLK"]["outcome_notes"]
    assert dec["ZZOPCO"]["assessment_status"] == "CHECK_UNAVAILABLE"
    assert dec["ZZWRN"]["assessment_status"] == "CHECK_UNAVAILABLE"
    # G2: ripresa / retry dello stesso gate: niente «apply count mismatch», stessi id
    again = _gate(db, memo_id, memo, report, monkeypatch)
    assert again["decisions_error"] is None, again["decisions_error"]
    assert again["decision_ids"] == gate["decision_ids"]
    assert again["publication"]["memo_markdown"] == gate["publication"]["memo_markdown"]


def test_g2_persistenza_con_esclusione_ripetuta_e_idempotente(db, report, negozio, monkeypatch):
    memo = H + "| BUY | ZZBLK | 5000 | ora | ALTA | x |\n" + CODA
    memo_id = db.save_memo(memo)
    rows = parse_action_table_rows(memo)["rows"]
    esiti = [dict(a, ticker="ZZBLK") for a in _esiti(memo, report, negozio)]
    bb = SimpleNamespace(data={}, valuation_results={})
    store = SimpleNamespace(request_journal=None)
    ids = cm._persist_publication_decisions(bb, db, store, memo_id, memo, rows, esiti, [("BUY", "ZZBLK")])
    assert cm._persist_publication_decisions(bb, db, store, memo_id, memo, rows, esiti,
                                             [("BUY", "ZZBLK")]) == ids


# --------------------------------------------------------------------------- G4 / G8 sul registro

def _decisione_dal_parser(db, memo_id, ticker="ZZOK", action="BUY"):
    with sqlite3.connect(db.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decisions (memo_id, timestamp, action, ticker, status, proposal_row_index, "
            "proposal_action, proposal_ticker, proposal_ticker_cell) "
            "VALUES (?, '2026-10-01T10:00:00', ?, ?, 'PENDING', 0, ?, ?, ?)",
            (memo_id, action, ticker, action, ticker, ticker))
        return cur.lastrowid


def test_g4_riga_del_parser_senza_esito_non_e_operativa(db):
    memo_id = db.save_memo("memo di prova")
    did = _decisione_dal_parser(db, memo_id)
    with sqlite3.connect(db.db_path) as conn:
        storica = conn.execute("INSERT INTO decisions (memo_id, timestamp, action, ticker, status) "
                               "VALUES (?, '2026-10-01T10:00:00', 'BUY', 'ZZOLD', 'PENDING')",
                               (memo_id,)).lastrowid
    letti = {d["id"]: d for d in db.get_recent_decisions(10)}
    assert letti[did]["assessment_status"] == "CHECK_UNAVAILABLE"
    assert letti[storica]["assessment_status"] is None             # storia e Trade Idea: invariati
    with pytest.raises(ValueError):
        db.update_decision(did, status="EXECUTED")
    assert db.update_decision(storica, status="EXECUTED") is True


def test_g8_la_divergenza_manuale_chiude_una_proposta_non_verificabile(db):
    memo_id = db.save_memo("memo di prova")
    did = _decisione_dal_parser(db, memo_id)
    with sqlite3.connect(db.db_path) as conn:
        tid = conn.execute(
            "INSERT INTO trade_history (ticker, action, quantita, prezzo, valuta, data, link_origin) "
            "VALUES ('ZZOK', 'BUY', 3, 12.5, 'EUR', '2026-10-02T10:00:00', 'none')").lastrowid
    event = db.record_manual_trade_divergence(did, tid, "eseguita a mano: dato di valutazione assente")
    dettagli = json.loads(event["details_json"])
    assert dettagli["assessment_status"] == "CHECK_UNAVAILABLE" and dettagli["trade_id"] == tid
    assert db.get_manual_trade_divergences(did)[0]["details"]["trade_id"] == tid


# --------------------------------------------------------------------------- G7 ripresa legacy

def test_g7_ripresa_di_un_memo_validato_prima_del_gate_esegue_il_gate():
    chiamate = []

    def gate(memo, pairs):
        chiamate.append((memo, pairs))
        return {"publication": {"memo_markdown": "PROIEZIONE", "assessments": [{"row_index": 0}]},
                "decision_ids": [7], "decisions_error": None, "hard_pairs": [("BUY", "ZZBLK")]}

    legacy = {"memo": "MEMO VECCHIO", "capo_usage": {}, "sanity_pairs": [["BUY", "ZZBLK"]]}
    out = cm._restore_validated(legacy, "SORGENTE", gate)
    assert chiamate == [("MEMO VECCHIO", [["BUY", "ZZBLK"]])]
    assert out["memo"] == "PROIEZIONE" and out["decision_ids"] == [7]
    nuovo = {"memo": "PUBBLICATO", "capo_usage": {}, "sanity_pairs": [],
             "publication": {"memo_markdown": "PUBBLICATO", "assessments": []},
             "decision_ids": [3], "decisions_error": None, "hard_pairs": []}
    out = cm._restore_validated(nuovo, "SORGENTE", gate)
    assert len(chiamate) == 1 and out["decision_ids"] == [3] and out["memo"] == "PUBBLICATO"


# --------------------------------------------------------------------------- G3: misura della revisione

def test_g3_il_recupero_legacy_di_regenerate_memo_e_spento_prima_di_ogni_lavoro():
    """La revisione citava cli/regenerate_memo.py:180-270: quel codice sta in `_regenerate`,
    che solleva alla PRIMA riga; `main` passa solo da run_multi_agent (ripresa nativa, che
    attraversa il gate). Qui si prova che il ramo citato non puo' girare."""
    from bellomberg.cli import regenerate_memo
    with pytest.raises(ValueError, match="Helper legacy disabilitato"):
        regenerate_memo._regenerate(None, 1, [], None)


# --------------------------------------------------------------------------- seguito revisione REV_G6

def _registro(db, memo_id):
    with sqlite3.connect(db.db_path) as conn:
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT ticker, status, assessment_status FROM decisions WHERE memo_id=? AND status != 'EXPIRED'",
            (memo_id,))}


def _pubblicati(gate):
    return {a["ticker"]: a["status"] for a in gate["publication"]["assessments"] if a.get("row_index") is not None}


def _concordi(db, memo_id, gate):
    """Il registro non e' mai piu' permissivo del memo: OPERATIVE+PENDING solo se il memo dice OPERATIVE."""
    pub = _pubblicati(gate)
    for ticker, (status, esito) in _registro(db, memo_id).items():
        if status == "PENDING" and esito == "OPERATIVE":
            assert pub.get(ticker) == "OPERATIVE", (ticker, pub, _registro(db, memo_id))


SRC_R1 = (H + "| BUY | ZZOK | 5000 | ora | ALTA | x |\n| BUY | ZZBLK | 3000 | ora | ALTA | x |\n" + CODA)


def _apply_bloccato(monkeypatch, quante):
    """apply_sanity_exclusions che trova il registro occupato le prime `quante` volte."""
    vero = av.apply_sanity_exclusions
    giri = {"n": 0}

    def apply_bloccato(dbx, mid, pairs, **kw):
        giri["n"] += 1
        if giri["n"] <= quante:
            if kw.get("strict"):
                raise sqlite3.OperationalError("database is locked")
            return 0
        return vero(dbx, mid, pairs, **kw)
    monkeypatch.setattr(av, "apply_sanity_exclusions", apply_bloccato)
    monkeypatch.setattr(cm.time, "sleep", lambda *_: None)
    return giri


def _finalizza(db, memo_id, src, gate):
    return cm._finalize_publication_decisions(
        SimpleNamespace(data={}, valuation_results={}), db, SimpleNamespace(request_journal=None),
        memo_id, src, gate["publication"], gate["decision_ids"])


def _eventi(db, memo_id):
    with sqlite3.connect(db.db_path) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT d.ticker, e.event_type, e.to_status, e.actor FROM decision_events e "
            "JOIN decisions d ON d.id=e.decision_id WHERE d.memo_id=? AND e.event_type != 'ASSESSMENT_RECORDED'",
            (memo_id,))]


def test_r1_registro_occupato_una_volta_si_ritenta_e_chiude(db, report, negozio, monkeypatch):
    memo_id = db.save_memo(SRC_R1)
    giri = _apply_bloccato(monkeypatch, 1)
    g1 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert g1["decisions_error"] is None and giri["n"] == 2          # ritentato, non dichiarato guasto
    _concordi(db, memo_id, g1)
    assert _pubblicati(g1)["ZZOK"] == "OPERATIVE"
    assert _registro(db, memo_id)["ZZBLK"] == ("SKIPPED", "BLOCKED")


def test_r1_n5_chiusura_che_fallisce_davvero_e_dichiarata_nel_memo_poi_la_finalizzazione_chiude(
        db, report, negozio, monkeypatch):
    from bellomberg.reporting.email_sender import corpo_azioni
    memo_id = db.save_memo(SRC_R1)
    _apply_bloccato(monkeypatch, cm._RITENTATIVI_TRANSITORI)        # il gate esaurisce i tentativi
    g1 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert g1["decisions_error"] and "database is locked" in g1["decisions_error"]
    assert _pubblicati(g1)["ZZOK"] == "OPERATIVE"                    # la BUY valida non e' persa
    _concordi(db, memo_id, g1)
    nonop = {n["ticker"]: n["reason"] for n in g1["publication"]["nonoperative"]}
    assert "auto-chiusura nel registro non riuscita" in nonop["ZZBLK"]     # nel memo (N5)
    assert "auto-chiusura nel registro non riuscita" in corpo_azioni(g1["publication"]["assessments"])
    ids, errore = _finalizza(db, memo_id, SRC_R1, g1)                # la finalizzazione ritenta e chiude
    assert errore is None and ids == g1["decision_ids"]
    assert _registro(db, memo_id)["ZZBLK"] == ("SKIPPED", "BLOCKED")


def test_r1_n5_chiusura_mai_riuscita_arriva_nell_email(db, report, negozio, monkeypatch):
    from bellomberg.reporting.email_sender import corpo_azioni
    memo_id = db.save_memo(SRC_R1)
    _apply_bloccato(monkeypatch, 99)
    g1 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    pubblicazione = {"assessments": [dict(a, reason=a["reason"].split("; auto-chiusura")[0])
                                     for a in g1["publication"]["assessments"]]}
    _, errore = cm._finalize_publication_decisions(
        SimpleNamespace(data={}, valuation_results={}), db, SimpleNamespace(request_journal=None),
        memo_id, SRC_R1, pubblicazione, g1["decision_ids"])
    assert errore and "database is locked" in errore                 # dichiarato, la consegna procede
    assert "auto-chiusura nel registro non riuscita" in corpo_azioni(pubblicazione["assessments"])
    assert _registro(db, memo_id)["ZZBLK"] == ("PENDING", "BLOCKED")    # non eseguibile


def test_r1_rilettura_occupata_una_volta_non_ritira_la_buy_valida(db, report, negozio, monkeypatch):
    memo_id = db.save_memo(SRC_R1)
    vero = cm._recorded_assessments
    giri = {"n": 0}

    def rilettura_occupata(dbx, ids):
        giri["n"] += 1
        if giri["n"] == 2:          # 1a = «gia' registrati?», 2a = rilettura dopo la scrittura
            raise sqlite3.OperationalError("database is locked")
        return vero(dbx, ids)
    monkeypatch.setattr(cm, "_recorded_assessments", rilettura_occupata)
    monkeypatch.setattr(cm.time, "sleep", lambda *_: None)
    g = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert g["decisions_error"] is None
    assert _pubblicati(g)["ZZOK"] == "OPERATIVE"
    assert _registro(db, memo_id)["ZZOK"] == ("PENDING", "OPERATIVE")


def test_r1_n2_ripresa_con_sanity_passata_a_block_pubblica_il_piu_severo(db, report, negozio, monkeypatch):
    memo_id = db.save_memo(SRC_R1)
    g1 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert _pubblicati(g1)["ZZOK"] == "OPERATIVE"
    os.remove(os.path.join(report, "VAL_ZZOK.payload.json"))
    _sidecar(report, "ZZOK", "BLOCK")                         # cambia fra crash e ripresa
    g2 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert _pubblicati(g2)["ZZOK"] == "BLOCKED"                # «BUY su BLOCK mai operativa» vince
    motivo = next(n["reason"] for n in g2["publication"]["nonoperative"] if n["ticker"] == "ZZOK")
    assert "piu' severo di quello registrato (OPERATIVE)" in motivo and "chiusa SKIPPED" in motivo
    assert not any("ZZOK" in r for r in _righe_pubblicate(g2["publication"]["memo_markdown"]))
    _concordi(db, memo_id, g2)
    assert _registro(db, memo_id)["ZZOK"][0] == "SKIPPED"
    # una ripresa ancora successiva non torna indietro (N7)
    g3 = _gate(db, memo_id, SRC_R1, report, monkeypatch)
    assert _pubblicati(g3)["ZZOK"] != "OPERATIVE"


def test_r1_n1_il_ritiro_e_del_gate_non_del_pm(db, report, negozio, monkeypatch):
    memo_id = db.save_memo(SRC_R1)
    _gate(db, memo_id, SRC_R1, report, monkeypatch)            # registra ZZOK OPERATIVE
    monkeypatch.setattr(cm, "REPORT_DIR", report)
    g = cm._publication_gate(SimpleNamespace(data={"_sizing": SIZING}, valuation_results={}), db,
                             SimpleNamespace(request_journal=None), memo_id, SRC_R1, SRC_R1,
                             "testo sorgente Capo non archiviato: prova", [])
    assert _pubblicati(g)["ZZOK"] == "CHECK_UNAVAILABLE"
    _concordi(db, memo_id, g)
    assert _registro(db, memo_id)["ZZOK"][0] == "SKIPPED"
    ritiri = [e for e in _eventi(db, memo_id) if e[0] == "ZZOK"]
    assert ritiri == [("ZZOK", "PUBLICATION_WITHDRAWN", "SKIPPED", "publication_gate")], ritiri
    zzok = next(d for d in db.get_recent_decisions(10) if d["ticker"] == "ZZOK")
    assert zzok["assessment_status"] == "CHECK_UNAVAILABLE"    # coerente col memo
    with pytest.raises(ValueError):
        db.update_decision(zzok["id"], status="EXECUTED")       # niente scorciatoia: divergenza manuale
    motivo = next(n["reason"] for n in g["publication"]["nonoperative"] if n["ticker"] == "ZZOK")
    assert "chiusa SKIPPED nel registro dal gate (non dal PM)" in motivo     # memo ed email lo dicono
    from bellomberg.agents import scorekeeper
    monkeypatch.setattr(scorekeeper, "get_track_record_for_capo", lambda *a, **k: "")
    contesto = db.build_capo_memory_context(max_chars=50000)
    assert "CHIUSE DAL GATE DI PUBBLICAZIONE" in contesto
    sk = [l for l in contesto.splitlines() if l.startswith("NON eseguite questa volta")]
    assert not any("ZZOK" in l for l in sk), sk                # non conta come «no» del PM


def test_n3_seconda_intestazione_action_table_non_resta_nel_memo(report, negozio):
    memo = (H + "| BUY | ZZOK | 1000 | ora | ALTA | x |\n" + CODA
            + "\n## ACTION TABLE (aggiornata)\n| Action | Ticker | EUR | Timing | Confidence |\n"
              "|---|---|---|---|---|\n| BUY | ZZBLK | 9000 | ora | ALTA |\n")
    snap = cm.build_publication_snapshot(memo, _esiti(memo, report, negozio))
    assert "| BUY | ZZBLK | 9000 | ora | ALTA |" not in snap["memo_markdown"]
    assert [n["ticker"] for n in snap["nonoperative"]] == ["ZZBLK"]
    assert "non valutata" in snap["nonoperative"][0]["reason"]


def test_n4_pannello_pdf_non_pesca_una_tabella_dell_analisi(report, negozio, tmp_path, monkeypatch):
    import pypdf
    import bellomberg.reporting.charts_institutional as ci
    from bellomberg.reporting.pdf_institutional import build_institutional_memo
    monkeypatch.setattr(ci, "DIR", str(tmp_path / "ch"))
    memo = ("# Memo\n\n## ACTION TABLE\n| Action | Asset | EUR | Timing | Confidence |\n|---|---|---|---|---|\n"
            "| BUY | ZZOK | 5000 | ora | ALTA |\n\n## 2. Analisi\nScenari:\n\n"
            "| Scenario | Prob | Target | Ticker | Nota |\n|---|---|---|---|---|\n| Bull | 30% | 120 | ZZOK | x |\n")
    pubblicato = cm.build_publication_snapshot(memo, _esiti(memo, report, negozio))["memo_markdown"]
    p = build_institutional_memo(pubblicato, output_path=str(tmp_path / "n4.pdf"), title_date="2026-10-04")
    testo = "\n".join(pg.extract_text() for pg in pypdf.PdfReader(p).pages)
    for titolo in ("Tabella operativa", "Action table - weekly"):
        i = testo.find(titolo)
        pannello = testo[i:i + 200] if i >= 0 else ""
        assert "Scenario" not in pannello and "Bull" not in pannello, pannello
    assert "Bull" in testo                                     # la tabella dell'analisi resta dov'era


def test_r2_intestazione_con_alias_sconosciuto_finalizza_e_l_email_elenca(db, report, negozio, monkeypatch):
    from bellomberg.reporting.email_sender import corpo_azioni
    src = ("# Memo\n\n## ACTION TABLE\n| Action | Asset | EUR | Timing | Confidence |\n|---|---|---|---|---|\n"
           "| BUY | ZZOK | 5000 | ora | ALTA |\n| TRIM | ZZWRN | 3000 | ora | ALTA |\n" + CODA)
    memo_id = db.save_memo(src)
    g = _gate(db, memo_id, src, report, monkeypatch)
    assert g["decisions_error"] is None and g["decision_ids"] == []
    assert not _righe_pubblicate(g["publication"]["memo_markdown"])
    email = corpo_azioni(g["publication"]["assessments"])
    assert "ZZOK" in email and "ZZWRN" in email and "CHECK_UNAVAILABLE" in email
    ids, errore = cm._finalize_publication_decisions(
        SimpleNamespace(data={}, valuation_results={}), db, SimpleNamespace(request_journal=None),
        memo_id, src, g["publication"], [])
    assert errore is None and ids == []


@pytest.mark.parametrize("separatore", [
    "\nNuove idee:\n\n| Action | Ticker | EUR | Timing | Confidence |\n|---|---|---|---|---|\n",
    "\n",                                                  # tabella spezzata da una riga vuota
])
def test_r3_righe_fuori_dalla_tabella_valutata_non_restano_nel_memo(db, report, negozio, monkeypatch, separatore):
    _sidecar(report, "ZZBLK2", "BLOCK")
    src = H + "| BUY | ZZOK | 1000 | ora | ALTA | x |\n" + separatore + "| BUY | ZZBLK2 | 7000 | ora | ALTA | y |\n" + CODA
    memo_id = db.save_memo(src)
    g = _gate(db, memo_id, src, report, monkeypatch)
    sezione = g["publication"]["memo_markdown"].split("## ACTION TABLE", 1)[1].split("\n## ", 1)[0]
    assert "ZZBLK2" not in sezione and "ZZOK" in sezione
    dichiarata = [n for n in g["publication"]["nonoperative"] if n["ticker"] == "ZZBLK2"]
    assert dichiarata and "fuori dalla tabella" in dichiarata[0]["reason"]
    assert "ZZBLK2" not in _registro(db, memo_id)


def test_r4_senza_valutazione_sta_nella_riga_del_pannello(report, negozio):
    memo = H + "| BUY | ZZETF.MI | 1000 | entro venerdi | ALTA | x |\n| BUY | ZZOK | 1000 | ora | ALTA | y |\n" + CODA
    righe = _righe_pubblicate(cm.build_publication_snapshot(memo, _esiti(memo, report, negozio))["memo_markdown"])
    etf = next(r for r in righe if "ZZETF.MI" in r)
    assert "**SENZA VALUTAZIONE** — entro venerdi" in etf
    assert "SENZA VALUTAZIONE" not in next(r for r in righe if "ZZOK" in r)
    assert parse_action_table_rows("## ACTION TABLE\n" + "\n".join(righe))["rows"][0]["ticker"] == "ZZETF.MI"


def test_r5_ticker_future_col_uguale(tmp_path, report):
    voce = {"provenienza": "dichiarato", "settore_policy": "banks", "classe_size": "veicolo",
            "verificato_il": "2026-09-01", "note": "voce di prova"}
    p = tmp_path / "veicoli_f.json"
    p.write_text(json.dumps({"ZZG=F": dict(voce, tipo="commodity")}), encoding="utf-8")
    n = classificazione.carica_veicoli(str(p))
    memo = H + "| BUY | ZZG=F | 1000 | ora | ALTA | x |\n" + CODA
    esito = _esiti(memo, report, n)[0]
    assert esito["status"] == "OPERATIVE" and esito["senza_valutazione"], esito


def test_r1_l_auto_chiusura_del_gate_non_inghiotte_il_guasto():
    class DBBloccato:
        def _conn(self):
            raise sqlite3.OperationalError("database is locked")
    with pytest.raises(sqlite3.OperationalError):
        av.apply_sanity_exclusions(DBBloccato(), 1, [("BUY", "ZZBLK")], strict=True)
    assert av.apply_sanity_exclusions(DBBloccato(), 1, [("BUY", "ZZBLK")]) == 0      # chiamanti storici
    errore = cm._close_sanity_blocks(DBBloccato(), 1, [("BUY", "ZZBLK")])
    assert errore and "database is locked" in errore
