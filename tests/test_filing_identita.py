import pytest

from bellomberg.market_data import filing_identita, sec_edgar
from bellomberg.market_data.filing_identita import proponi, proponi_sec

RIGHE = [{"cik": "0009990001", "ticker": "NOVA", "nome": "Nova Semiconductors Inc."},
         {"cik": "0009990002", "ticker": "ALFA", "nome": "Alfa Holdings Inc."},
         {"cik": "0009990002", "ticker": "ALFB", "nome": "Alfa Holdings Inc."},
         {"cik": "0009990011", "ticker": "KORE", "nome": "Kore Mining plc"},
         {"cik": "0009990012", "ticker": "KOREY", "nome": "Kore Mining Ltd"},
         {"cik": "0009990020", "ticker": "ZZB", "nome": "Zeta Bravo Aircraft Co"}]


def test_usa_senza_suffisso():
    r = proponi_sec("NOVA", "Nova Semiconductors Inc.", righe=RIGHE, alias={})
    assert r["stato"] == "univoco" and r["candidati"][0]["origine"] == "ticker"


def test_usa_senza_suffisso_senza_nome_va_confermato():
    # Revisione G1: il solo ticker non basta (crypto/ETF omonimo di un trust USA).
    r = proponi_sec("NOVA", None, righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and r["candidati"][0]["origine"] == "ticker"


def test_usa_senza_suffisso_nome_diverso_resta_univoco():
    assert proponi_sec("NOVA", "Nova", righe=RIGHE, alias={})["stato"] == "univoco"


def test_europeo_per_nome_identico_va_confermato():
    # Revisione G1: con un suffisso di listino il nome identico e' una proposta, mai un collegamento.
    r = proponi_sec("NOVA.DE", "NOVA SEMICONDUCTORS INC", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and [c["cik"] for c in r["candidati"]] == ["0009990001"]


def test_alias_verificato_basta():
    r = proponi_sec("NSX.DE", None, righe=RIGHE, alias={"NSX": "NOVA"})
    assert r["stato"] == "univoco" and r["candidati"][0]["origine"] == "alias"


def test_omonimo_americano_mai_collegato():
    assert proponi_sec("ZZB.L", "Zeta Bravo Defence plc", righe=RIGHE, alias={})["stato"] == "nessuno"


def test_ticker_nudo_con_suffisso_senza_nome_non_basta():
    assert proponi_sec("ZZB.L", None, righe=RIGHE, alias={})["stato"] == "nessuno"


def test_stesso_cik_piu_ticker_e_un_solo_candidato():
    r = proponi_sec("ALF.DE", "Alfa Holdings Inc", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and len(r["candidati"]) == 1  # suffisso: da confermare (G1)


def test_omonimi_per_nome_ambigui():
    r = proponi_sec("KORE.DE", "Kore Mining", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and {c["cik"] for c in r["candidati"]} == {"0009990011", "0009990012"}


def test_nome_abbreviato_del_listino_va_confermato():
    r = proponi_sec("NSX.DE", "NOVA SEMICOND.", righe=RIGHE, alias={})
    assert r["stato"] == "ambiguo" and r["candidati"][0]["origine"] == "nome_simile"


def test_rifiutato_escluso():
    r = proponi_sec("KORE.DE", "Kore Mining", righe=RIGHE, alias={}, rifiutati={"0009990012"})
    assert r["stato"] == "ambiguo" and [c["cik"] for c in r["candidati"]] == ["0009990011"]  # suffisso (G1)


def test_proponi_errore_di_configurazione_non_e_nessuna_fonte(monkeypatch):
    def manca(**k):
        raise sec_edgar.ContattoMancante("SEC_CONTACT_EMAIL assente nel .env")

    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec", manca)
    # RUN-ANDREA (Opus 5.5, 05/10): contatto assente = SEC NON CONFIGURATA, dichiarata (mai «nessuna
    # fonte»); il listino estero prova lo stesso l'ESEF (qui finto, nessuna rete).
    monkeypatch.setattr(filing_identita, "proponi_esef",
                        lambda t, n, rifiutati=frozenset(), rifiutati_lei=frozenset():
                        {"stato": "nessuno", "candidati": [], "motivo": "finto"})
    r = proponi("NOVA.DE", "Nova Semiconductors Inc.")
    assert r["sec"]["stato"] == "non_configurata" and "SEC_CONTACT_EMAIL" in r["sec"]["motivo"]


def test_proponi_usa_elenco_alias_e_nome(monkeypatch):
    monkeypatch.setattr(sec_edgar, "elenco_emittenti_sec",
                        lambda **k: {"righe": RIGHE, "origine": "cache", "motivo": None})
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    monkeypatch.setattr(filing_identita, "nome_emittente", lambda t: "Nova Semiconductors Inc.")
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",  # fase B: forme del CIK univoco di un listino estero
                        lambda t, cik=None, **k: {"stato": "ok", "documenti": [{"form": "10-Q"}]})
    r = proponi("NOVA.DE")
    assert r["nome"] == "Nova Semiconductors Inc." and r["sec"]["stato"] == "ambiguo"  # suffisso (G1)
    assert r["origine_elenco"] == "cache"
