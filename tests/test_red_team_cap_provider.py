"""Run 05/10 21:00: il Red Team chiedeva 128000 token a un modello col tetto del provider a 65536;
il controllo prezzi fermava la run prima di spendere. Ora il tetto del provider vince, dichiarato."""
from bellomberg.agents import red_team


def test_il_tetto_del_provider_abbassa_la_richiesta(capsys):
    meta = lambda model: {"id": model, "top_provider": {"max_completion_tokens": 65536}}
    assert red_team._cap_del_provider(128000, "zz/finto-flash", meta) == 65536
    assert "128000 -> 65536" in capsys.readouterr().out


def test_tetto_piu_alto_o_assente_lascia_la_richiesta():
    assert red_team._cap_del_provider(128000, "zz/a", lambda m: {"top_provider": {"max_completion_tokens": 200000}}) == 128000
    assert red_team._cap_del_provider(128000, "zz/b", lambda m: {"top_provider": {}}) == 128000


def test_catalogo_irraggiungibile_e_dichiarato(capsys):
    def guasto(model):
        raise OSError("offline")
    assert red_team._cap_del_provider(128000, "zz/c", guasto) == 128000
    assert "non letto (OSError)" in capsys.readouterr().out
