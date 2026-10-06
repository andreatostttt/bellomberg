"""Form 4 SEC: qualunque versione del foglio di stile xslF345Xnn (voce 3 handoff-4, Opus 5.5).

Il primaryDocument di un Form 4 e' l'HTML RENDERIZZATO sotto `xslF345Xnn/`; l'XML grezzo sta
nella cartella del filing. Il codice toglieva solo `xslF345X05`: con `X06` (in uso oggi) si
scaricava l'HTML, il ripiego index.json cercava dentro `.../xslF345X06/` (cartella sbagliata)
e l'eccezione del ripiego era ingoiata. Qui: rete finta, ticker/CIK/accession inventati.
"""
import pytest

from bellomberg.core.language import language_context
from bellomberg.core.presentation import render_payload

CIK = "0000000077"
ACC = "0000000077-26-000009"
CARTELLA = "https://www.sec.gov/Archives/edgar/data/77/000000007726000009"


def _xml():
    return ('<?xml version="1.0"?><ownershipDocument><issuer><issuerTradingSymbol>ZZTEST'
            '</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerCik>0000000001'
            '</rptOwnerCik><rptOwnerName>ZZ OWNER</rptOwnerName></reportingOwnerId>'
            '<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>'
            '</reportingOwner><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>'
            '2026-09-12</value></transactionDate><transactionCoding><transactionCode>P</transactionCode>'
            '</transactionCoding><transactionAmounts><transactionShares><value>123</value>'
            '</transactionShares><transactionPricePerShare><value>4.5</value></transactionPricePerShare>'
            '</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>')


HTML_RENDERIZZATO = "<html><body>Form 4 ZZTEST pagina renderizzata</body></html>"


class _Risposta:
    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        if self._payload is None:
            raise ValueError("non e' JSON")
        return self._payload


@pytest.fixture
def rete(monkeypatch):
    """Rete SEC finta: `documenti[url]` = _Risposta o eccezione; ogni URL ignoto = 404."""
    from datetime import datetime
    from bellomberg.market_data import sec_edgar as sec
    stato = {"primary": None, "acc": [ACC], "documenti": {}, "chiamate": [], "log": []}
    oggi = datetime.now().strftime("%Y-%m-%d")

    def get(url, headers=None, timeout=None, **kw):
        stato["chiamate"].append(url)
        if url == f"https://data.sec.gov/submissions/CIK{CIK}.json":
            return _Risposta(payload={"name": "ZZ Synthetic Corp", "filings": {"recent": {
                "form": ["4"], "filingDate": [oggi], "accessionNumber": stato["acc"],
                "primaryDocument": [stato["primary"]], "primaryDocDescription": [""]}}})
        risposta = stato["documenti"].get(url, _Risposta(status=404, text="Not Found"))
        if isinstance(risposta, Exception):
            raise risposta
        return risposta

    monkeypatch.setattr(sec, "lookup_cik", lambda *a, **k: CIK)
    monkeypatch.setattr(sec.requests, "get", get)
    monkeypatch.setattr(sec, "attendi_sec", lambda *a, **k: None)
    monkeypatch.setattr(sec, "_headers", lambda: {"User-Agent": "zz-test"})
    monkeypatch.setattr(sec, "_log", lambda m: stato["log"].append(str(m)))
    monkeypatch.setattr(sec.time, "sleep", lambda *_: None)
    return sec, stato


@pytest.mark.parametrize("versione", ["xslF345X05", "xslF345X06", "xslF345X07", "xslF345X99"])
def test_ogni_versione_xsl_scarica_xml_grezzo(rete, versione):
    sec, stato = rete
    stato["primary"] = f"{versione}/form4.xml"
    stato["documenti"][f"{CARTELLA}/{versione}/form4.xml"] = _Risposta(text=HTML_RENDERIZZATO)
    stato["documenti"][f"{CARTELLA}/form4.xml"] = _Risposta(text=_xml())
    motivo = []
    trades = sec.get_insider_trades("ZZTEST", motivo=motivo)
    assert motivo == []
    assert [(t["owner"], t["code"], t["shares"]) for t in trades] == [("ZZ OWNER", "P", 123.0)]
    # l'HTML renderizzato non si scarica nemmeno: si va diretti all'XML grezzo
    assert not any("/xslF345X" in u for u in stato["chiamate"]), stato["chiamate"]


def test_ripiego_index_json_cerca_nella_cartella_del_filing(rete):
    # sottocartella di rendering sconosciuta: il nome grezzo non esiste, l'XML ha un altro nome
    sec, stato = rete
    stato["primary"] = "zzrender/form4.htm"
    stato["documenti"][f"{CARTELLA}/zzrender/form4.htm"] = _Risposta(text=HTML_RENDERIZZATO)
    stato["documenti"][f"{CARTELLA}/index.json"] = _Risposta(payload={"directory": {"item": [
        {"name": "zz-primary.htm"}, {"name": "zz-ownership.xml"}]}})
    stato["documenti"][f"{CARTELLA}/zz-ownership.xml"] = _Risposta(text=_xml())
    motivo = []
    trades = sec.get_insider_trades("ZZTEST", motivo=motivo)
    assert motivo == []
    assert [t["shares"] for t in trades] == [123.0]
    assert f"{CARTELLA}/index.json" in stato["chiamate"]
    assert f"{CARTELLA}/zzrender/index.json" not in stato["chiamate"]


def test_ripiego_x06_con_xml_dal_nome_diverso(rete):
    sec, stato = rete
    stato["primary"] = "xslF345X06/zz-doc.xml"
    stato["documenti"][f"{CARTELLA}/index.json"] = _Risposta(payload={"directory": {"item": [
        {"name": "zz-ownership.xml"}]}})
    stato["documenti"][f"{CARTELLA}/zz-ownership.xml"] = _Risposta(text=_xml())
    motivo = []
    assert len(sec.get_insider_trades("ZZTEST", motivo=motivo)) == 1
    assert motivo == []


@pytest.mark.parametrize("guasto,tipo", [
    (ConnectionError(f"boom {CARTELLA}/index.json?zzchiave=ZZSEGRETO"), "ConnectionError"),
    (_Risposta(text="<html>non json</html>"), "ValueError"),
])
def test_ripiego_fallito_si_dichiara_col_solo_tipo(rete, guasto, tipo):
    sec, stato = rete
    stato["primary"] = "xslF345X06/form4.xml"
    stato["documenti"][f"{CARTELLA}/index.json"] = guasto
    motivo = []
    with language_context("it"):
        trades = sec.get_insider_trades("ZZTEST", motivo=motivo)
    assert trades == []
    assert len(motivo) == 1, motivo
    it = str(motivo[0])
    en = str(render_payload(motivo, language="en")[0])
    for testo in (it, en):
        assert tipo in testo, testo
        assert ACC in testo, testo            # quale Form 4: l'accession, non l'URL
        assert "https://" not in testo and "sec.gov" not in testo, testo
        assert "ZZSEGRETO" not in testo and "boom" not in testo, testo
    assert "ripiego" in it and "fallback" in en
    # anche il log porta il motivo, senza URL
    assert any(tipo in r for r in stato["log"]), stato["log"]
    assert not any("ZZSEGRETO" in r or "https://" in r for r in stato["log"]), stato["log"]


def test_ripiego_riuscito_ma_senza_xml_resta_dichiarato(rete):
    # index.json risponde ma non ha l'XML: il buco resta dichiarato come prima
    sec, stato = rete
    stato["primary"] = "xslF345X06/form4.xml"
    stato["documenti"][f"{CARTELLA}/index.json"] = _Risposta(payload={"directory": {"item": []}})
    motivo = []
    assert sec.get_insider_trades("ZZTEST", motivo=motivo) == []
    assert len(motivo) == 1 and "non trovato" in str(motivo[0]), motivo
    # il ripiego e' RIUSCITO (non ha trovato XML): nessuna frase di ripiego «fallito (None)»
    assert "ripiego" not in str(motivo[0]) and "None" not in str(motivo[0]), motivo


def test_cartella_del_filing_non_riconosciuta_si_dichiara_senza_get(rete):
    # senza accessionNumber get_recent_filings costruisce .../data/77//<doc>: niente cartella
    sec, stato = rete
    stato["primary"] = "xslF345X06/zzdoc.xml"
    stato["acc"] = []
    motivo = []
    with language_context("it"):
        assert sec.get_insider_trades("ZZTEST", motivo=motivo) == []
    assert len(motivo) == 1, motivo
    assert "cartella del filing non riconosciuta" in str(motivo[0]), motivo
    assert "not recognised" in str(render_payload(motivo, language="en")[0]), motivo
    assert "AttributeError" not in str(motivo[0]) and "https://" not in str(motivo[0]), motivo
    assert not any(u.endswith("/index.json") for u in stato["chiamate"]), stato["chiamate"]
