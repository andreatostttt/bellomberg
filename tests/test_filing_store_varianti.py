import pytest

from bellomberg.storage.filing_store import _validate_profile, unisci_variante

BASE = {"ticker": "KORE.DE", "emittente_id": "CIK:9990011", "lingua": "en", "tipo": "annuale",
        "perimetro": "consolidato", "forme_sec": ["20-F"], "sezioni_salta_indice": True,
        "verifica": {"lingua": "the", "tipo": "annual report", "perimetro": "consolidated"},
        "sezioni": {"rischi": {"inizio": "d\\. risk factors", "fine": "item 4\\b.*"}}}
V6K = {"tipo": "semestrale", "forme_sec": ["6-K"], "sezioni": {}, "sezioni_intero": True,
       "periodo_regola": "piu_recente", "stesso_periodo": "piu_lungo",
       "verifica": {"tipo": "six months ended", "emittente": "Kore",
                    "periodo": "(?P<mesi>six) months ended (?P<fine>.+)"}}


def test_varianti_valide():
    _validate_profile("KORE.DE", {**BASE, "varianti": [{"tipo": "annuale"}, V6K]}, 24)


def test_unisci_variante_fonde_verifica_e_toglie_varianti():
    p = unisci_variante({**BASE, "varianti": [V6K]}, V6K)
    assert "varianti" not in p and p["tipo"] == "semestrale" and p["sezioni_intero"] is True
    assert p["verifica"]["lingua"] == "the" and p["verifica"]["tipo"] == "six months ended"
    assert p["forme_sec"] == ["6-K"]


@pytest.mark.parametrize("cattiva", [
    {"varianti": []},
    {"varianti": "annuale"},
    {"varianti": [{"tipo": "annuale"}, {"tipo": "annuale"}]},
    {"varianti": [{"tipo": "annuale"}, {"tipo": "semestrale"}, {"tipo": "trimestrale"}, {"tipo": "nove_mesi"}]},
    {"varianti": [{"tipo": "annuale", "ir_urls": ["https://x.example"]}]},
    {"varianti": [{"forme_sec": ["10-K"]}]},
    {"varianti": [{"tipo": "semestrale", "sezioni": {}}]},
    {"varianti": [{"tipo": "semestrale", "verifica": "x"}]},
    {"forme_sec": ["8-K"]},
    {"forme_sec": []},
    {"periodo_regola": "qualsiasi"},
    {"stesso_periodo": "primo"},
    {"sezioni_intero": "si"},
    {"sezioni_salta_indice": 1},
    {"sezioni": {}},
])
def test_varianti_e_chiavi_non_valide(cattiva):
    with pytest.raises(ValueError):
        _validate_profile("KORE.DE", {**BASE, **cattiva}, 24)


def test_sezioni_vuote_ammesse_con_testo_intero():
    _validate_profile("KORE.DE", {**BASE, "sezioni": {}, "sezioni_intero": True}, 24)


def test_profilo_storico_invariato():
    p = {k: v for k, v in BASE.items() if k not in ("forme_sec", "sezioni_salta_indice")}
    _validate_profile("KORE.DE", p, 168)
