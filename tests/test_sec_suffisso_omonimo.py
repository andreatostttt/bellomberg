"""Revisione G1 (04/10/2026), difetto PREESISTENTE: un ticker con suffisso di listino non USA
(ZZFUND.MI, inventato) si agganciava al fondo USA con le stesse lettere (ZZFUND) nello storico
companyfacts (DCF e chat) e nell'elenco dei filing recenti; il messaggio diceva «tassonomia
atipica» e la lista tornava vuota senza motivo. La guardia storica `ticker_ambiguo_per_cik`
ora vale anche qui: nessuna richiesta SEC senza alias verificato, e il motivo e' dichiarato."""
from datetime import date

import pytest

from bellomberg.market_data import sec_edgar, sec_xbrl

FONDO = {"0": {"cik_str": 999777, "ticker": "ZZFUND", "title": "ZZFund Senior Income Fund"}}


class _Risposta:
    def __init__(self, dati):
        self.status_code, self._dati = 200, dati

    def json(self):
        return self._dati

    def raise_for_status(self):
        pass


@pytest.fixture
def rete(monkeypatch, tmp_path):
    chiamate = []

    def get(url, **k):
        chiamate.append(url)
        if url.endswith("company_tickers.json"):
            return _Risposta(FONDO)
        if "companyfacts" in url:
            return _Risposta({"entityName": "ZZFund Senior Income Fund", "facts": {}})
        if "submissions" in url:
            oggi = date.today().isoformat()
            return _Risposta({"cik": "999777", "name": "ZZFund Senior Income Fund", "filings": {"recent": {
                "form": ["10-K"], "filingDate": [oggi], "accessionNumber": ["0000999777-26-000001"],
                "primaryDocument": ["d.htm"], "primaryDocDescription": ["10-K"]}}})
        raise AssertionError(url)

    monkeypatch.setenv("SEC_CONTACT_EMAIL", "test@example.invalid")
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {})
    monkeypatch.setattr(sec_edgar, "_CIK_CACHE", {})
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path / "xbrl"))
    return chiamate


def test_storico_companyfacts_non_aggancia_il_fondo_omonimo(rete):
    r = sec_xbrl.get_financial_history("ZZFUND.MI")
    assert "error" in r and "suffisso di listino" in r["error"]
    assert "tassonomia atipica" not in r["error"]
    assert rete == []  # nessuna richiesta SEC: niente CIK di un'altra societa'


def test_filing_recenti_dichiarano_perche_sono_vuoti(rete):
    motivo = []
    assert sec_edgar.get_recent_filings("ZZFUND.MI", form_types=["10-K"], days=30, motivo=motivo) == []
    assert motivo and "suffisso di listino" in motivo[0]
    assert rete == []


def test_ticker_usa_senza_suffisso_resta_risolto(rete):
    righe = sec_edgar.get_recent_filings("ZZFUND", form_types=["10-K"], days=30, motivo=[])
    assert [r["form"] for r in righe] == ["10-K"]


def test_alias_verificato_resta_ammesso(rete, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {"ZZFUND": "ZZFUND"})
    righe = sec_edgar.get_recent_filings("ZZFUND.MI", form_types=["10-K"], days=30, motivo=[])
    assert [r["form"] for r in righe] == ["10-K"]


def test_lookup_cik_da_solo_non_aggancia_l_omonimo(rete):
    # Seguito revisione: la guardia sta anche dentro lookup_cik, per i chiamanti futuri.
    motivo = []
    assert sec_edgar.lookup_cik("ZZFUND.MI", motivo=motivo) is None
    assert motivo and "suffisso di listino" in motivo[0] and rete == []
    assert sec_edgar.lookup_cik("ZZFUND", motivo=[]) == "0000999777"


def test_lookup_cik_con_alias_verificato_risolve(rete, monkeypatch):
    monkeypatch.setattr(sec_edgar, "_alias_sec", lambda: {"ZZFUND": "ZZFUND"})
    assert sec_edgar.lookup_cik("ZZFUND.MI", motivo=[]) == "0000999777"
