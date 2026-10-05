"""G3 difetto 5 (04/10/2026): gli helper di chiamata/costo/ledger stanno in un modulo neutro.

I filing («Proponi con AI») non devono dipendere dal modulo della sintesi articoli: prima
filing_proposta_ai importava market_data.article_summary solo per _call_model/_cost/_record_usage.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bellomberg.core import llm_usage

RADICE = Path(__file__).resolve().parents[1] / "src" / "bellomberg"


def _moduli_importati(path):
    albero = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            out.add(nodo.module)
            out.update(nodo.module + "." + a.name for a in nodo.names)
        elif isinstance(nodo, ast.Import):
            out.update(a.name for a in nodo.names)
    return out


@pytest.mark.parametrize("modulo", ["market_data/filing_proposta_ai.py"])
def test_i_filing_non_importano_il_modulo_news(modulo):
    importati = _moduli_importati(RADICE / modulo)
    assert "bellomberg.market_data.article_summary" not in importati
    assert "bellomberg.core.llm_usage" in importati


def test_helper_neutri_senza_default_news():
    import inspect
    # il chiamante dichiara sempre tetto di token e agente del ledger: nessun default «news»
    assert inspect.signature(llm_usage._call_model).parameters["max_tokens"].default is inspect._empty
    assert inspect.signature(llm_usage._record_usage).parameters["agent"].default is inspect._empty


def test_un_solo_livello_di_retry_quello_del_client(monkeypatch):
    """REV_G3 R3: il client OpenRouter ritenta gia' (max_retries): _call_model fa UNA chiamata."""
    tentativi = []

    class Sovraccarico(Exception):
        status_code = 503

    def create(**kwargs):
        tentativi.append(kwargs)
        raise Sovraccarico("overloaded")

    with pytest.raises(Sovraccarico):
        llm_usage._call_model(NS(messages=NS(create=create)), "prova/modello", "ciao", max_tokens=77)
    assert len(tentativi) == 1


def test_client_dei_click_con_timeout_dichiarato(monkeypatch):
    from bellomberg.core import llm_client as lc
    visti = []
    monkeypatch.setattr(lc, "OpenRouterClient", lambda **k: visti.append(k) or NS())
    llm_usage.nuovo_client()
    assert visti == [{"timeout": llm_usage.TIMEOUT_CLICK_S}]
    assert 30 <= llm_usage.TIMEOUT_CLICK_S <= 120


def test_ledger_dal_modulo_neutro(monkeypatch):
    righe = []
    monkeypatch.setattr(llm_usage, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: righe.extend(log)))
    tentativi = []

    def create(**kwargs):
        tentativi.append(kwargs)
        return NS(usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=None,
                           cache_creation_input_tokens=None, cost_usd=None))

    msg = llm_usage._call_model(NS(messages=NS(create=create)), "prova/modello", "ciao", max_tokens=77)
    assert tentativi[0]["max_tokens"] == 77 and tentativi[0]["thinking"] == {"type": "disabled"}
    costo = llm_usage._cost("prova/modello-sconosciuto", msg.usage)
    assert costo["tokens"] == {"in": 10, "out": 5}
    assert costo["cost_eur"] is None and costo["cost_status"] != "ok"   # mai uno zero finto
    llm_usage._record_usage("prova/modello", costo, 2, 0.5, agent="agente_prova")
    assert righe and righe[0]["agent"] == "agente_prova" and righe[0]["api_calls"] == 2
