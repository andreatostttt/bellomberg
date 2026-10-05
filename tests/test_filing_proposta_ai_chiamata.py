"""Fase C, Task 4: una sola chiamata da pulsante, cache per sha256, ledger llm_usage (client finto)."""
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

from bellomberg.core import llm_client as lc
from bellomberg.core import llm_usage as asum   # helper neutri (G3, 04/10)
from bellomberg.market_data import filing_proposta_ai as fp
from tests.filing_pdf_sintetici import semestrale_nova

URL = "https://ir.nova.example/reports/h1-2026.pdf"
# REV_G2b A1 (04/10/2026): il modello risponde con frasi LETTERALI; i pattern li costruisce il codice.
RISPOSTA = {"tipo": "half-year", "lingua": "en",
            "emittente": "Nova AG", "prova_tipo": "Half-Year Financial Report",
            "sezioni": {"prospettive": {"inizio": "Outlook for the 2026 fiscal year",
                                        "fine": "Risks and opportunities"},
                        "rischi": {"inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"},
                        "testata": {"inizio": "Review of results of operations",
                                    "fine": "Interim Group Management Report"}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    usage = []
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: usage.extend(log)))
    from bellomberg.core import llm_pricing
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda model, tokens, ttl=None: {
        "cost": 0.0021, "status": "ok", "fx_rate": 0.9, "fx_source": "live",
        "breakdown": {"cost_usd": tokens.get("cost_usd")}})
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: "nova/flash")
    # costo massimo stimato (spesa incerta) senza rete: catalogo e cambio finti
    monkeypatch.setattr(fp, "_metadati_openrouter", lambda m: {"pricing": {"prompt": "0.0000003",
                                                                          "completion": "0.0000012"}})
    monkeypatch.setattr(fp, "_fx", lambda: (0.9, "live"))
    stato = NS(chiamate=0, prompts=[], risposta=json.dumps(RISPOSTA), errore=None, attesa=0)

    def create(**kwargs):
        stato.chiamate += 1
        stato.prompts.append(kwargs)
        time.sleep(stato.attesa)
        if stato.errore:
            raise stato.errore
        return NS(content=[lc.TextBlock(stato.risposta)],
                  usage=NS(input_tokens=8_000, output_tokens=600, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0031))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    path = tmp_path / "h1.pdf"
    path.write_bytes(semestrale_nova())
    return NS(path=path, usage=usage, stato=stato)


def _proponi(env):
    return fp.proponi("NOVA.DE", nome="Nova AG", path=env.path, url=URL)


def test_prima_chiamata_ledger_costo_reale_ed_esito(env):
    out = _proponi(env)
    assert out["stato"] == "done", out
    assert out["cached"] is False
    assert env.stato.chiamate == 1
    assert env.stato.prompts[0]["max_tokens"] == fp.MAX_TOKENS
    assert env.usage and env.usage[0]["agent"] == "filing_profile_ai"
    assert env.usage[0]["model"] == "nova/flash" and env.usage[0]["in"] == 8_000
    assert out["costo_eur"] == pytest.approx(0.0021) and out["costo_usd"] == pytest.approx(0.0031)
    assert out["salvabile"] is True
    assert {s["nome"] for s in out["verificate"]} == {"prospettive", "rischi"}
    assert {s["nome"] for s in out["scartate"]} == {"testata"}
    assert out["periodo"]["fine"] == "2026-03-31"
    assert out["tipo"] == "semestrale" and out["modello"] == "nova/flash"


def test_seconda_richiesta_stesso_pdf_nessuna_chiamata(env):
    primo = _proponi(env)
    assert env.stato.chiamate == 1
    secondo = _proponi(env)
    assert env.stato.chiamate == 1 and len(env.usage) == 1
    assert secondo["cached"] is True
    for chiave in ("verificate", "scartate", "periodo", "salvabile", "costo_eur", "sha256"):
        assert secondo[chiave] == primo[chiave]
    assert fp.leggi_proposta(primo["sha256"])["profilo"]["sezioni"].keys() == {"prospettive", "rischi"}


def test_modello_non_configurato_nessun_client(env, monkeypatch):
    def mancante(*a):
        raise lc.ConfigurazioneLLMMancante("NEWS_SUMMARY_MODEL")
    monkeypatch.setattr(lc, "modello", mancante)
    monkeypatch.setattr(lc, "OpenRouterClient", lambda: pytest.fail("client creato senza modello"))
    out = _proponi(env)
    assert out == {"stato": "not_configured", "variabile": "NEWS_SUMMARY_MODEL"}
    assert env.usage == []


def test_errore_del_modello_nessuna_cache(env):
    env.stato.errore = RuntimeError("provider down")
    out = _proponi(env)
    assert out["stato"] == "error" and "provider down" in out["dettaglio"]
    assert env.usage == []
    env.stato.errore = None
    # REV_G2b A3: un errore che puo' arrivare dopo la generazione e' spesa INCERTA, contata al costo
    # massimo stimato; il tentativo resta nel registro (throttle per lo stesso titolo).
    secondo = _proponi(env)
    assert out["spesa_incerta"] is True and out["costo_incerto_max_eur"] > 0
    assert secondo["stato"] == "refused" and secondo["variabile"] == "FILING_AI_INTERVALLO_TICKER_S"
    assert env.stato.chiamate == 1


def test_risposta_illeggibile_ledger_scritto_e_riprovabile(env):
    env.stato.risposta = "non so"
    out = _proponi(env)
    assert out["stato"] == "error" and "illeggibile" in out["dettaglio"]
    assert len(env.usage) == 1  # il costo pagato resta nel ledger
    assert fp.leggi_proposta(out["sha256"])["fallita"] is True  # nessuna seconda spesa senza «Riprova»
    assert _proponi(env)["cached"] is True and env.stato.chiamate == 1
    env.stato.risposta = json.dumps(RISPOSTA)
    # Revisione 04/10/2026 (R10 c, decisione PM): la STESSA richiesta (PDF, prompt, modello) non si
    # ripaga nemmeno con «Riprova»: si rilegge la risposta gia' pagata (ancora illeggibile).
    rifatta = fp.proponi("NOVA.DE", nome="Nova AG", path=env.path, url=URL, riprova=True)
    assert rifatta["stato"] == "error" and rifatta["riprovabile"] is False and env.stato.chiamate == 1


def test_due_clic_contemporanei_una_sola_chiamata(env):
    env.stato.attesa = 0.2
    esiti = []
    fili = [threading.Thread(target=lambda: esiti.append(_proponi(env))) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert env.stato.chiamate == 1
    assert sorted(e["cached"] for e in esiti) == [False, True]


def test_cache_corrotta_ignorata(env, monkeypatch):
    monkeypatch.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")  # seconda chiamata pagata subito: niente throttle
    primo = _proponi(env)
    fp._cache_path(primo["sha256"]).write_text("{rotto", encoding="utf-8")
    assert fp.leggi_proposta(primo["sha256"]) is None
    assert _proponi(env)["cached"] is False
    assert env.stato.chiamate == 2


def test_verifica_di_una_versione_precedente_rifatta_in_locale_senza_chiamata(env, monkeypatch):
    primo = _proponi(env)
    dati = fp.leggi_proposta(primo["sha256"])
    dati.update(versione_verifica=0, salvabile=False, verificate=[], profilo=None)
    fp._salva(dati)
    secondo = _proponi(env)
    assert env.stato.chiamate == 1 and len(env.usage) == 1
    assert secondo["cached"] is True and secondo["salvabile"] is True
    assert {s["nome"] for s in secondo["verificate"]} == {"prospettive", "rischi"}
    assert fp.leggi_proposta(primo["sha256"])["versione_verifica"] == fp.VERSIONE_VERIFICA
