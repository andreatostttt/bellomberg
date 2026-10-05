"""Tool di riserva get_filing_changes (agent_tools): stessi parametri e validazione del tool di chat."""
from bellomberg.agents import agent_tools, chat_tools, filing_context


def _chiamate(monkeypatch):
    viste = []

    def finto(ticker, **kw):
        viste.append((ticker, kw))
        return {"ticker": ticker}
    monkeypatch.setattr(filing_context, "get_filing_changes", finto)
    return viste


def test_schema_di_riserva_espone_gli_stessi_parametri_del_tool_principale():
    principale = next(t for t in chat_tools.TOOL_DEFINITIONS if t["name"] == "get_filing_changes")
    riserva = next(t for t in agent_tools.TOOLS_SCHEMA if t["name"] == "get_filing_changes")
    assert riserva["input_schema"]["properties"] == principale["input_schema"]["properties"]
    assert riserva["input_schema"]["required"] == ["ticker"]


def test_riserva_inoltra_i_parametri_validi(monkeypatch):
    viste = _chiamate(monkeypatch)
    agent_tools.TOOL_DISPATCHER["get_filing_changes"](
        ticker="NOVA.DE", da="3", max_changes=10, variante="annuale", ordine="punteggio", run_id="7")
    assert viste == [("NOVA.DE", {"da": 3, "max_changes": 10, "variante": "annuale",
                                  "ordine": "punteggio", "run_id": 7})]


def test_riserva_senza_parametri_usa_i_default_del_tool_principale(monkeypatch):
    viste = _chiamate(monkeypatch)
    agent_tools.TOOL_DISPATCHER["get_filing_changes"](ticker="NOVA.DE")
    assert viste == [("NOVA.DE", {"da": 1, "max_changes": 5, "variante": None, "ordine": None, "run_id": None})]


def test_riserva_con_parametri_non_validi_da_errore_senza_eccezioni():
    for kw in ({"da": "abc"}, {"max_changes": 99}, {"variante": "mensile"}, {"ordine": "x"}, {"run_id": -1}):
        out = agent_tools.TOOL_DISPATCHER["get_filing_changes"](ticker="NOVA.DE", **kw)
        principale = chat_tools.dispatch("get_filing_changes", {"ticker": "NOVA.DE", **kw})
        assert isinstance(out, dict) and out.get("status") == "errore", (kw, out)
        assert principale["data"]["status"] == "errore" and principale["data"]["reason"] == out["reason"]
