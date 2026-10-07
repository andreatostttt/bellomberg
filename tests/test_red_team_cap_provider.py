"""Run 05/10 21:00: il Red Team chiedeva 128000 token a un modello col tetto del provider a 65536;
il controllo prezzi fermava la run prima di spendere. Ora il tetto del provider vince, dichiarato.
MOD-CAP (06/10): la regola vive nel punto unico del client (llm_client.tetto_uscita), per TUTTI i
ruoli; red_team._cap_del_provider non esiste piu' (un solo meccanismo). Stesse tre prove."""
import pytest

from bellomberg.core import llm_client


@pytest.fixture(autouse=True)
def dichiarazioni_nuove(monkeypatch):
    monkeypatch.setattr(llm_client, "_TETTI_DICHIARATI", set())
    monkeypatch.setattr(llm_client, "_LISTINO_PROCESSO", {})


def test_il_tetto_del_provider_abbassa_la_richiesta(capsys, monkeypatch):
    monkeypatch.setattr(llm_client, "_listino_fuori_run",
                        lambda model: {"id": model, "top_provider": {"max_completion_tokens": 65536}})
    assert llm_client.tetto_uscita("zz/finto-flash", 128000, ruolo="red_team") == 65536
    assert "red_team: richiesti 128000 token, zz/finto-flash ne accetta 65536: uso 65536" in capsys.readouterr().out


def test_tetto_piu_alto_o_assente_lascia_la_richiesta(monkeypatch):
    listini = {"zz/a": {"top_provider": {"max_completion_tokens": 200000}}, "zz/b": {"top_provider": {}}}
    monkeypatch.setattr(llm_client, "_listino_fuori_run", lambda model: listini[model])
    assert llm_client.tetto_uscita("zz/a", 128000, ruolo="red_team") == 128000
    assert llm_client.tetto_uscita("zz/b", 128000, ruolo="red_team") == 128000


def test_catalogo_irraggiungibile_e_dichiarato(capsys, monkeypatch):
    def guasto(model):
        raise OSError("offline")
    monkeypatch.setattr(llm_client, "_listino_fuori_run", guasto)
    assert llm_client.tetto_uscita("zz/c", 128000, ruolo="red_team") == 128000
    out = capsys.readouterr().out
    assert "non verificato (listino non letto (OSError))" in out and "uso i 128000 richiesti" in out
