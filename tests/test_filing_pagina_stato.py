"""Fase E, Task 1: stato canonico per la pagina Filing e etichetta del documento (funzioni pure)."""
import pytest

from bellomberg.market_data.filing_stato_ui import GRUPPI_UI, documento, stato_ui

# Schede sintetiche (stesse chiavi di filing_context.scheda_da_run / _scheda_vuota).
NOVITA = {"ticker": "NOVA", "gruppo": 0, "run_id": 7, "confronto_at": "2026-10-01T10:00:00+00:00"}
CAMBIATO = {"ticker": "NOVA", "gruppo": 1, "run_id": 7, "confronto_at": "2026-10-01T10:00:00+00:00"}
INVARIATO = {"ticker": "NOVA", "gruppo": 2, "run_id": 7, "confronto_at": "2026-10-01T10:00:00+00:00"}
VUOTA = {"ticker": "NOVA", "gruppo": 3, "run_id": None, "confronto_at": None}

RUN = {"id": 9, "started_at": "2026-10-03T08:00:00+00:00", "trigger": "scheduled"}
ERRORE = {"at": "2026-10-02T08:00:00+00:00", "reason": "HTTPError: 503"}
PROPOSTA = {"sha256": "a" * 64, "url": "https://ir.nova.example/h1.pdf", "at": "2026-10-02", "salvabile": True,
            "verificate": 3}


def _caso(scheda=VUOTA, profilo_attivo=True, run_attivo=None, ultimo_errore=None, esito=None, proposta_ai=None,
          escluso=False, scollegato=False, automatico=True):
    return dict(scheda=scheda, profilo_attivo=profilo_attivo, run_attivo=run_attivo, ultimo_errore=ultimo_errore,
                esito=esito, proposta_ai=proposta_ai, escluso=escluso, scollegato=scollegato, automatico=automatico)


CASI = [
    # --- gli 11 stati
    ("novita", _caso(NOVITA), "novita", "novita"),
    ("aggiornato", _caso(CAMBIATO), "aggiornato", "aggiornati"),
    ("invariato", _caso(INVARIATO), "invariato", "aggiornati"),
    ("in corso", _caso(CAMBIATO, run_attivo=RUN), "in_corso", "aggiornati"),
    ("primo confronto in attesa", _caso(VUOTA), "primo_confronto", "aggiornati"),
    ("errore dopo un confronto valido", _caso(CAMBIATO, ultimo_errore=ERRORE), "errore", "da_sistemare"),
    ("da confermare", _caso(profilo_attivo=False, esito={"esito": "da_confermare"}), "da_confermare", "da_sistemare"),
    ("proposta AI in sospeso", _caso(profilo_attivo=False, proposta_ai=PROPOSTA), "proposta_ai", "da_sistemare"),
    ("senza fonte", _caso(profilo_attivo=False, esito={"esito": "senza_fonte"}), "senza_fonte", "senza_fonte"),
    ("non attivo", _caso(profilo_attivo=False), "non_attivo", "senza_fonte"),
    ("escluso", _caso(profilo_attivo=False, escluso=True), "escluso", "senza_fonte"),
    # --- casi limite
    ("escluso con profilo e novita'", _caso(NOVITA, escluso=True, run_attivo=RUN), "escluso", "senza_fonte"),
    ("scollegato con vecchio confronto", _caso(CAMBIATO, scollegato=True, esito={"esito": "scollegato"}),
     "non_attivo", "senza_fonte"),
    ("scollegato poi da confermare", _caso(CAMBIATO, scollegato=True, esito={"esito": "da_confermare"}),
     "da_confermare", "da_sistemare"),
    ("primo confronto in errore", _caso(VUOTA, ultimo_errore=ERRORE), "errore", "da_sistemare"),
    ("in corso vince sull'errore", _caso(CAMBIATO, ultimo_errore=ERRORE, run_attivo=RUN), "in_corso", "aggiornati"),
    ("novita' vince sull'errore", _caso(NOVITA, ultimo_errore=ERRORE), "novita", "novita"),
    ("nuova proposta AI con profilo", _caso(INVARIATO, proposta_ai=PROPOSTA), "proposta_ai", "da_sistemare"),
    ("proposta AI prima dell'esito", _caso(profilo_attivo=False, proposta_ai=PROPOSTA,
                                           esito={"esito": "senza_fonte"}), "proposta_ai", "da_sistemare"),
    ("esito errore senza profilo", _caso(profilo_attivo=False, esito={"esito": "errore"}), "errore", "da_sistemare"),
    ("preferito con profilo", _caso(CAMBIATO), "aggiornato", "aggiornati"),
    ("profilo disattivato senza confronto", _caso(VUOTA, automatico=False), "non_attivo", "senza_fonte"),
    ("profilo disattivato con confronto valido", _caso(CAMBIATO, automatico=False), "aggiornato", "aggiornati"),
    ("profilo disattivato con novita'", _caso(NOVITA, automatico=False), "novita", "novita"),
    ("esito vecchio ignorato col profilo", _caso(INVARIATO, esito={"esito": "da_confermare"}), "invariato",
     "aggiornati"),
]


@pytest.mark.parametrize("nome,kw,stato,gruppo", CASI, ids=[c[0] for c in CASI])
def test_stato_ui(nome, kw, stato, gruppo):
    assert stato_ui(**kw) == (stato, gruppo)


def test_tutti_gli_stati_coperti():
    assert {c[2] for c in CASI} == set(GRUPPI_UI)


def _sec(*forme, ticker="NOVA"):
    from bellomberg.market_data.filing_profili_auto import profilo_sec
    return profilo_sec(ticker, cik="9990001", sec_ticker=ticker, nome="Nova Inc.", origine="ticker",
                       forme=set(forme), tipo_6k="semestrale")


def _esef():
    from bellomberg.market_data.filing_profili_auto import profilo_esef
    return profilo_esef("NOVA.MI", lei="999900NOVA0000000001", nome="Nova SpA", origine="nome", lingua="it")


def _ir(tipo="semestrale"):
    return {"ticker": "NOVA.MI", "fonti": ["ir"], "tipo": tipo, "ir_urls": ["https://ir.nova.example/h1.pdf"],
            "sezioni": {}, "lingua": "it", "verifica": {}}


def test_documento_it(monkeypatch):
    monkeypatch.setattr("bellomberg.market_data.filing_stato_ui.text", lambda it, en: it)
    from bellomberg.market_data.filing_proposta_ai import variante_ir
    assert documento(_sec("10-Q")) == "SEC 10-Q"
    assert documento(_sec("10-K")) == "SEC 10-K"
    assert documento(_sec("10-K", "10-Q")) == "SEC 10-K + 10-Q"
    assert documento(_sec("20-F", "6-K")) == "SEC 20-F + 6-K"
    assert documento(_esef()) == "ESEF annuale"
    assert documento(_ir()) == "IR semestrale"
    assert documento(_ir("trimestrale")) == "IR trimestrale"
    assert documento(variante_ir(_esef(), _ir())) == "ESEF annuale + IR semestrale"
    assert documento(None) is None
    assert documento({"ticker": "NOVA", "sezioni": {}}) is None


def test_documento_en(monkeypatch):
    monkeypatch.setattr("bellomberg.market_data.filing_stato_ui.text", lambda it, en: en)
    from bellomberg.market_data.filing_proposta_ai import variante_ir
    assert documento(_esef()) == "ESEF annual"
    assert documento(_ir()) == "IR half-year"
    assert documento(variante_ir(_esef(), _ir("trimestrale"))) == "ESEF annual + IR quarterly"
    assert documento(_sec("20-F", "6-K")) == "SEC 20-F + 6-K"
