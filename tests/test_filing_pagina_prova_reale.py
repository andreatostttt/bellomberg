"""Difetti trovati nella prova reale della fase E (pagina Filing, 04/10/2026). Dati sintetici."""
from bellomberg.agents.filing_context import scheda_da_run
from bellomberg.market_data.filing_stato_ui import stato_ui

PROFILO = {"ticker": "NOVA.DE", "tipo": "annuale", "cik": "0009990001", "forme_sec": ["20-F"],
           "sezioni": {"rischi": {}, "gestione": {}}}


def _run(diff):
    return {"id": 4, "finished_at": "2026-10-03T21:58:00+00:00", "status": "ok", "profile": PROFILO,
            "result": {"stato": "ok", "variante": "annuale", "confronto_corrente": diff,
                       "coppia": {"prima": {"sha256": "a", "metadati": {"periodo_fine": "2024-12-31"}},
                                  "dopo": {"sha256": "b", "metadati": {"periodo_fine": "2025-12-31"}}}}}


def test_confronto_senza_sezioni_e_un_errore_da_sistemare_non_un_invariato():
    diff = {"stato": "parziale", "sezioni_confrontate": [], "cambiamenti": [],
            "motivi": ["rischi: prima: intestazione iniziale assente o ambigua; dopo: intestazione iniziale assente o ambigua"]}
    s = scheda_da_run("NOVA.DE", profilo=PROFILO, run=_run(diff), ultimo=None, freschezza={"stato": "aggiornato"},
                      novita_dopo=None)
    assert s["ultimo_errore"]["reason"].startswith("nessuna sezione confrontata")
    assert "intestazione iniziale assente" in s["ultimo_errore"]["reason"]
    stato = stato_ui(scheda=s, profilo_attivo=True, run_attivo=None, ultimo_errore=s["ultimo_errore"], esito=None,
                     proposta_ai=None, escluso=False, scollegato=False)
    assert stato == ("errore", "da_sistemare")


def test_confronto_parziale_con_sezioni_resta_un_confronto():
    diff = {"stato": "parziale", "sezioni_confrontate": ["gestione"], "cambiamenti": [],
            "motivi": ["rischi: intestazione iniziale assente"]}
    s = scheda_da_run("NOVA.DE", profilo=PROFILO, run=_run(diff), ultimo=None, freschezza={"stato": "aggiornato"},
                      novita_dopo=None)
    assert s["ultimo_errore"] is None
