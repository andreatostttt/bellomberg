import json
import os
import time

import pytest

from bellomberg.market_data import sec_edgar
from tests.filing_sec_sintetici import CIK_KORE, CIK_NOVA, indice_filing_6k, submissions


@pytest.fixture(autouse=True)
def contatto(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "test@example.com")
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda: None)


def test_elenco_da_rete_poi_da_cache(tmp_path):
    chiamate = []

    def fetch():
        chiamate.append(1)
        return {"0": {"cik_str": 9990001, "ticker": "NOVA", "title": "Nova Semiconductors Inc."}}

    cache = tmp_path / "company_tickers.json"
    r1 = sec_edgar.elenco_emittenti_sec(cache_path=cache, fetch=fetch)
    r2 = sec_edgar.elenco_emittenti_sec(cache_path=cache, fetch=fetch)
    assert r1["origine"] == "rete" and r2["origine"] == "cache" and len(chiamate) == 1
    assert r2["righe"] == [{"cik": "0009990001", "ticker": "NOVA", "nome": "Nova Semiconductors Inc."}]


def test_elenco_cache_scaduta_se_rete_giu(tmp_path):
    cache = tmp_path / "company_tickers.json"
    cache.write_text(json.dumps({"0": {"cik_str": 1, "ticker": "A", "title": "A Inc."}}))
    vecchio = time.time() - 3 * 86400
    os.utime(cache, (vecchio, vecchio))

    def giu():
        raise OSError("rete giu")

    r = sec_edgar.elenco_emittenti_sec(cache_path=cache, fetch=giu)
    assert r["origine"] == "cache_scaduta" and "rete giu" in r["motivo"]
    assert r["righe"][0]["ticker"] == "A"


def test_elenco_senza_cache_e_rete_giu(tmp_path):
    def giu():
        raise OSError("giu")

    with pytest.raises(RuntimeError):
        sec_edgar.elenco_emittenti_sec(cache_path=tmp_path / "x.json", fetch=giu)


def test_elenco_senza_contatto_solleva(tmp_path, monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL")
    with pytest.raises(sec_edgar.ContattoMancante):
        sec_edgar.elenco_emittenti_sec(cache_path=tmp_path / "x.json", fetch=lambda: {})


class _Risposta:
    def __init__(self, data):
        self.data, self.status_code = data, 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_catalogo_con_cik_non_rifiuta_suffisso(monkeypatch):
    righe = [("10-Q", "2026-10-30", "0009990001-26-000003", "nova-q3.htm", "2026-09-30")]
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda url, **k: _Risposta(submissions(CIK_NOVA, "Nova Semiconductors Inc.", righe)))
    cat = sec_edgar.get_filing_catalog("NOVA.DE", cik=CIK_NOVA)
    assert cat["stato"] == "ok", cat["motivi"]
    assert cat["documenti"][0]["ticker"] == "NOVA.DE"
    assert cat["documenti"][0]["emittente_id"] == "CIK:0009990001"


def test_catalogo_con_cik_non_numerico_rifiutato():
    cat = sec_edgar.get_filing_catalog("NOVA.DE", cik="abc")
    assert cat["stato"] == "errore" and "CIK" in cat["motivi"][0]


def test_catalogo_senza_cik_rifiuta_ancora_suffisso(monkeypatch):
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    cat = sec_edgar.get_filing_catalog("NOVA.DE")
    assert cat["stato"] == "errore" and "suffisso" in cat["motivi"][0]


def test_allegati_filing_legge_tabella_e_ixbrl():
    html = indice_filing_6k([("k6.htm", "6-K", "Form 6-K", False),
                             ("ex99-1.htm", "EX-99.1", "EX-99.1", True)])
    visti = []
    out = sec_edgar.allegati_filing(CIK_KORE, "0009990011-26-000001", fetch=lambda url: visti.append(url) or html)
    assert visti == ["https://www.sec.gov/Archives/edgar/data/9990011/000999001126000001/0009990011-26-000001-index.htm"]
    assert out == [
        {"seq": 1, "descrizione": "Form 6-K", "tipo": "6-K", "ixbrl": False, "dimensione": 1000,
         "url": "https://www.sec.gov/Archives/edgar/data/9990011/000999001126000001/k6.htm"},
        {"seq": 2, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "dimensione": 1000,
         "url": "https://www.sec.gov/Archives/edgar/data/9990011/000999001126000001/ex99-1.htm"},
    ]


def test_allegati_filing_ignora_link_fuori_archivio():
    html = (b'<table><tr><td>1</td><td>x</td><td><a href="https://evil.example/a.htm">a.htm</a></td>'
            b'<td>EX-99.1</td><td>1</td></tr></table>')
    assert sec_edgar.allegati_filing(CIK_KORE, "0009990011-26-000001", fetch=lambda url: html) == []


def test_allegati_filing_link_al_visualizzatore_ixbrl():
    # Osservato sulla SEC: i documenti iXBRL puntano a /ix?doc=/Archives/...
    cartella = "/Archives/edgar/data/9990011/000999001126000001/"
    html = (f'<table><tr><td>1</td><td>EX-99.1</td><td><a href="/ix?doc={cartella}kore-20260630.htm">kore-20260630.htm</a>'
            f' &nbsp;&nbsp;iXBRL</td><td>EX-99.1</td><td>1</td></tr></table>').encode()
    out = sec_edgar.allegati_filing(CIK_KORE, "0009990011-26-000001", fetch=lambda url: html)
    assert out == [{"seq": 1, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "dimensione": 1,
                    "url": "https://www.sec.gov" + cartella + "kore-20260630.htm"}]


def test_allegati_filing_legge_la_dimensione():
    html = indice_filing_6k([("k6.htm", "6-K", "Form 6-K", True)])
    assert sec_edgar.allegati_filing(CIK_KORE, "0009990011-26-000001", fetch=lambda url: html)[0]["dimensione"] == 1000
