"""Fase C: problemi trovati dalla revisione finale (2026-10-03), con test che li riproducono."""
import json
import re
import time

import pytest

from bellomberg.market_data import filing_proposta_ai as fp
from tests.filing_pdf_sintetici import semestrale_nova
from tests.test_filing_proposta_ai_verifica import URL, _proposta, _verifica


@pytest.mark.parametrize("pattern", [r"(?:\w+\s?)+:", r"(a+)+b", r"(?:[A-Za-z]+\s*)+\d{4}", r"(?:x|xy)*(?:y+)*z",
                                     r"((ab)*)+c", r"(?:\s*\w+)*$"])
def test_quantificatori_annidati_rifiutati(pattern):
    """Regex del modello con quantificatori annidati: backtracking catastrofico su una riga normale."""
    assert "annidati" in (fp._regex_sicura(pattern) or "")


@pytest.mark.parametrize("pattern", [r"Outlook for the \d{4} fiscal year", r"RELATED\s+PARTIES\s+TRANSACTIONS",
                                     r"(?P<mesi>six|6)\s+months\s+ended\s+(?P<fine>\d{1,2}\s+\w+\s+\d{4})",
                                     r"HALF[\s-]*YEAR\s+FINANCIAL\s+REPORT", r"Nova(?: Semiconductors AG)?"])
def test_regex_normali_ammesse(pattern):
    assert fp._regex_sicura(pattern) is None


def test_ripieghi_e_nome_superano_il_controllo():
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    for lista in fp._PERIODI_RIPIEGO.values():
        for rx in lista:
            assert fp._regex_sicura(rx) is None, rx
    for rx in fp._PROVA_TIPO.values():
        assert fp._regex_sicura(rx) is None, rx
    assert fp._regex_sicura(_regex_nome("Nova & Kore-Tech S.p.A.")) is None


def test_sezione_con_regex_catastrofica_scartata_in_fretta(tmp_path):
    proposta = _proposta(lenta={"inizio": r"(?:\w+\s?)+:", "fine": "Risks and opportunities"},
                         rischi={"inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"})
    inizio = time.monotonic()
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert time.monotonic() - inizio < 5
    assert "annidati" in {s["nome"]: s["motivo"] for s in out["scartate"]}["lenta"]


def test_regola_che_combacia_a_vuoto_non_regge(tmp_path):
    """Una regola che trova solo la stringa vuota non e' una prova (come _cerca_prova della pipeline)."""
    proposta = _proposta()
    proposta["prova_tipo"] = r"(?:Quarterly Statement)?"
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True, out
    assert out["regole"]["tipo"]["origine"] == "ripiego"


def test_regex_nome_a_parola_intera():
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    assert not re.search(_regex_nome("Nova AG"), "innovation report", re.I)
    assert re.search(_regex_nome("Nova AG"), "NOVA AG Half-Year", re.I)


def test_pdf_quasi_senza_testo_non_cambia_l_esito_dei_profili_che_verificavano(tmp_path):
    """La regola OCR spiega un fallimento; non rifiuta un PDF che si verifica comunque."""
    from bellomberg.market_data.filing_verifica import verifica_documento
    from tests.filing_pdf_sintetici import pdf
    righe = ["Nova AG Half-Year Financial Report", "The consolidated group results for the six months ended "
             "31 March 2026.", "Risks and opportunities", "Export restrictions could limit sales.",
             "Notes to the Financial Statements"]
    path = tmp_path / "misto.pdf"
    path.write_bytes(pdf([righe, [], [], [], [], [], []]))
    profilo = fp.profilo_ir("NOVA.DE", nome="Nova AG", ir_urls=[URL], proposta=_proposta(
        rischi={"inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"}),
        sha256="0" * 64, modello="m", lingua_rilevata="en")
    assert verifica_documento(path, url=URL, profilo=profilo)["stato"] == "ok"


# ---------------------------------------------------------------- route e cache

from tests.test_filing_routes_ai import SHA, URL as URL_PDF, env  # noqa: E402,F401


def test_accept_per_un_altro_ticker_rifiutato(env):
    env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF})
    r = env.client.post("/filings/KORE.DE/ai-proposal/accept", json={"sha256": SHA})
    assert r.status_code == 409
    assert env.service.store.get_profile("KORE.DE") is None


def test_proposta_in_cache_per_un_altro_ticker_riverificata_senza_chiamata(env):
    env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF})
    out = env.client.post("/filings/KORE.DE/ai-proposal", json={"url": URL_PDF}).json()
    assert out["cached"] is True and out["ticker"] == "KORE.DE" and len(env.chiamate) == 1
    assert env.client.post("/filings/KORE.DE/ai-proposal/accept", json={"sha256": SHA}).status_code == 200
    assert env.service.store.get_profile("KORE.DE")["profile"]["ticker"] == "KORE.DE"


def test_accept_richiede_l_url_verificato_tra_gli_ir_urls(env):
    env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF})
    r = env.client.post("/filings/NOVA.DE/ai-proposal/accept",
                        json={"sha256": SHA, "ir_urls": ["https://ir.nova.example/altro.pdf"]})
    assert r.status_code == 422 and env.service.store.get_profile("NOVA.DE") is None


def test_accept_di_una_verifica_vecchia_chiede_di_riaprire(env):
    env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF})
    dati = fp.leggi_proposta(SHA)
    dati["versione_verifica"] = 0
    fp._salva(dati)
    assert env.client.post("/filings/NOVA.DE/ai-proposal/accept", json={"sha256": SHA}).status_code == 409


def test_stima_senza_modello_non_scarica(env, monkeypatch):
    from bellomberg.core import llm_client as lc

    def mancante(*a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", mancante)
    out = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL_PDF}).json()
    assert out["stato"] == "not_configured" and env.scaricati == []


def test_stima_e_proposta_senza_chiave_api_not_configured(env, monkeypatch):
    from bellomberg.core import llm_client as lc

    def senza_chiave():
        raise lc.ConfigurazioneLLMMancante("OPENROUTER_API_KEY")
    monkeypatch.setattr(lc, "chiave_api", senza_chiave)
    out = env.client.get("/filings/NOVA.DE/ai-estimate", params={"url": URL_PDF}).json()
    assert out == {"stato": "not_configured", "variabile": "OPENROUTER_API_KEY"}
    assert env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF}).json()["stato"] == "not_configured"
    assert env.scaricati == [] and env.chiamate == []


def test_risposta_illeggibile_non_si_ripaga_senza_riprova_esplicita(env, monkeypatch):
    from types import SimpleNamespace as NS
    from bellomberg.core import llm_client as lc
    risposte = ["non so", json.dumps({"tipo": "semestrale", "sezioni": {"rischi": {
        "inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"}}})]

    def create(**kwargs):
        env.chiamate.append(kwargs)
        return NS(content=[lc.TextBlock(risposte[len(env.chiamate) - 1])],
                  usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.001))
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    primo = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF}).json()
    assert primo["stato"] == "error" and primo["costo_eur"] is not None and primo["riprovabile"] is True
    secondo = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF}).json()
    assert secondo["stato"] == "error" and secondo["cached"] is True and len(env.chiamate) == 1
    # Revisione 04/10/2026 (R10 c, decisione PM): «Riprova» sulla stessa richiesta (PDF, prompt,
    # modello) rilegge la risposta gia' pagata; una nuova chiamata solo con un altro modello.
    stessa = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF, "riprova": True}).json()
    assert stessa["stato"] == "error" and stessa["riprovabile"] is False and len(env.chiamate) == 1
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "nova/pro")
    monkeypatch.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    terzo = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF, "riprova": True}).json()
    assert terzo["stato"] == "done" and len(env.chiamate) == 2
    # una proposta riuscita non si rifa' nemmeno con riprova
    quarto = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF, "riprova": True}).json()
    assert quarto["cached"] is True and len(env.chiamate) == 2


def test_errore_della_verifica_dopo_la_chiamata_non_perde_la_proposta(env, monkeypatch):
    def guasta(*a, **k):
        raise RuntimeError("verifica rotta")
    monkeypatch.setattr(fp, "verifica_proposta", guasta)
    out = env.client.post("/filings/NOVA.DE/ai-proposal", json={"url": URL_PDF}).json()
    assert out["stato"] == "done" and out["salvabile"] is False
    assert any("verifica rotta" in m for m in out["motivi"])
    assert fp.leggi_proposta(SHA) is not None and len(env.chiamate) == 1
