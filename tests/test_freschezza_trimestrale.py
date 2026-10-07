"""FRESCHEZZA (06/10/2026, Opus 5.5): cascata di fonti per i numeri dell'ultimo periodo pubblicato.

Solo risposte SINTETICHE: ticker ZZFRESH / QQSYN.MI, CIK 0009990101 inventati, anni 2029, nessuna rete.
"""
import json
import time
from datetime import date
from types import SimpleNamespace

import pytest

from bellomberg.market_data import freschezza_trimestrale as ft
from bellomberg.market_data import sec_xbrl

CIK = "0009990101"
AS_OF = "2029-10-06T12:00:00+00:00"


# ------------------------------------------------------------------ companyfacts sintetici

def _ob(val, end, form, fp, filed, start=None, accn="0009990101-29-000001", fy=2029):
    ob = {"val": val, "end": end, "form": form, "fp": fp, "filed": filed, "accn": accn, "fy": fy}
    if start:
        ob["start"] = start
    return ob


def _facts(con_q2=True, solo_annuale=False):
    rev = [_ob(400.0, "2028-12-31", "10-K", "FY", "2029-02-20", "2028-01-01", "acc-k")]
    cfo = [_ob(90.0, "2028-12-31", "10-K", "FY", "2029-02-20", "2028-01-01", "acc-k")]
    cash = [_ob(55.0, "2028-12-31", "10-K", "FY", "2029-02-20", None, "acc-k")]
    if not solo_annuale:
        rev.append(_ob(111.11, "2029-03-31", "10-Q", "Q1", "2029-04-30", "2029-01-01", "acc-q1"))
        cash.append(_ob(66.0, "2029-03-31", "10-Q", "Q1", "2029-04-30", None, "acc-q1"))
        cfo.append(_ob(20.0, "2029-03-31", "10-Q", "Q1", "2029-04-30", "2029-01-01", "acc-q1"))
        if con_q2:
            rev.append(_ob(234.56, "2029-06-30", "10-Q", "Q2", "2029-07-30", "2029-01-01", "acc-q2"))  # cumulato 6 mesi
            rev.append(_ob(123.45, "2029-06-30", "10-Q", "Q2", "2029-07-30", "2029-04-01", "acc-q2"))
            cfo.append(_ob(47.5, "2029-06-30", "10-Q", "Q2", "2029-07-30", "2029-01-01", "acc-q2"))   # solo cumulato
            cash.append(_ob(77.7, "2029-06-30", "10-Q", "Q2", "2029-07-30", None, "acc-q2"))
    form_a = "20-F" if solo_annuale else None
    if form_a:
        for lista in (rev, cfo, cash):
            for ob in lista:
                ob["form"] = form_a
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": rev}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": cfo}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": cash}},
    }}, "entityName": "ZZFRESH Synthetic Corp"}


def test_quarterly_history_ultimo_10q_con_flusso_di_tre_mesi_e_cassa_cumulata():
    q = sec_xbrl.quarterly_history(_facts()["facts"])
    lp = q["latest_period"]
    assert lp["period_end"] == "2029-06-30" and lp["filing_date"] == "2029-07-30"
    assert lp["form"] == "10-Q" and lp["fp"] == "Q2" and lp["tipo"] == "trimestre" and lp["accession"] == "acc-q2"
    assert lp["values"]["revenue"] == 123.45                     # i 3 mesi, non il cumulato
    assert lp["values"]["cfo"] == 47.5 and lp["cumulati_da_inizio_esercizio_giorni"]["cfo"] > 150
    assert lp["values"]["cash"] == 77.7 and "revenue" not in lp["cumulati_da_inizio_esercizio_giorni"]
    assert q["quarters"]["revenue"] == {"2029-03-31": 111.11, "2029-06-30": 123.45}
    assert "cfo" not in q["quarters"]          # solo voci core nei trimestri (e il cumulato non e' un trimestre)


def test_a_parita_di_fine_periodo_vince_il_flusso_di_tre_mesi():
    facts = _facts()["facts"]
    facts["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"].append(
        _ob(27.5, "2029-06-30", "10-Q", "Q2", "2029-07-30", "2029-04-01", "acc-q2"))
    lp = sec_xbrl.quarterly_history(facts)["latest_period"]
    assert lp["values"]["cfo"] == 27.5 and "cfo" not in (lp.get("cumulati_da_inizio_esercizio_giorni") or {})


def test_quarterly_history_solo_annuali_dichiara_esercizio():
    lp = sec_xbrl.quarterly_history(_facts(solo_annuale=True)["facts"])["latest_period"]
    assert lp["period_end"] == "2028-12-31" and lp["tipo"] == "esercizio" and lp["form"] == "20-F"


def test_get_financial_history_espone_trimestri_e_ultimo_periodo(monkeypatch):
    from bellomberg.market_data import sec_edgar
    monkeypatch.setattr(sec_edgar, "ticker_ambiguo_per_cik", lambda t: None)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None: CIK)
    monkeypatch.setattr(sec_xbrl, "_fetch_companyfacts", lambda cik, **kw: _facts())
    monkeypatch.setattr(sec_xbrl, "cache_superata", lambda cik: None)
    r = sec_xbrl.get_financial_history("ZZFRESH")
    assert r["items"]["revenue"] == {2028: 400.0}                   # contratto annuale invariato
    assert r["latest_period"]["period_end"] == "2029-06-30"
    assert r["quarters"]["revenue"]["2029-06-30"] == 123.45


# ------------------------------------------------------------------ periodo atteso

def test_periodo_atteso_dall_ultima_data_dei_risultati():
    a = ft.periodo_atteso(date(2029, 10, 6), date_risultati=["2029-08-12", "2029-11-12"])
    assert a["periodo"] == "2029-06-30" and "2029-08-12" in a["base"]


def test_periodo_atteso_solo_data_futura():
    a = ft.periodo_atteso(date(2029, 10, 6), date_risultati=["2029-10-29", "2019-07-23"])
    assert a["periodo"] == "2029-06-30" and "2029-10-29" in a["base"]


def test_periodo_atteso_senza_calendario_regola_dichiarata():
    a = ft.periodo_atteso(date(2029, 10, 6), date_risultati=[])
    assert a["periodo"] == "2029-06-30" and "calendario" in a["base"] and "45" in a["base"]
    a = ft.periodo_atteso(date(2029, 8, 10), date_risultati=[])
    assert a["periodo"] == "2029-03-31"


def test_periodo_atteso_esercizio_non_solare_dall_ancora():
    a = ft.periodo_atteso(date(2029, 10, 6), date_risultati=["2029-09-30"], ancora="2029-05-28")
    assert a["periodo"] == "2029-08-28" and a["griglia"] == "fiscale"


# ------------------------------------------------------------------ fonti finte

_COMUNICATO_6K = """<html><body><p>ZZFRESH Synthetic Ltd reports results for the second quarter ended
June 30, 2029. Revenue of US$ 1,234.5 million for the three months ended June 30, 2029, net income
US$ 321.9 million.</p></body></html>"""


class Fonti:
    def __init__(self, *, cik=CIK, facts=None, submissions=None, indice=None, documenti=None,
                 fornitore=None, rotte=(), lente=None):
        self._cik, self._facts, self._sub = cik, facts, submissions
        self._indice, self._doc, self._forn = indice or {}, documenti or {}, fornitore
        self.rotte, self.lente, self.chiamate = set(rotte), lente or {}, []

    def _chiama(self, nome):
        self.chiamate.append(nome)
        if nome in self.lente:
            time.sleep(self.lente[nome])
        if nome in self.rotte:
            raise ConnectionError("fonte giu' (sintetico)")

    def cik(self, ticker):
        self._chiama("cik")
        return (self._cik, None) if self._cik else (None, "nessun CIK sintetico")

    def companyfacts(self, cik):
        self._chiama("companyfacts")
        return self._facts

    def submissions(self, cik):
        self._chiama("submissions")
        return self._sub

    def indice(self, cik, accession):
        self._chiama("indice")
        return self._indice.get(accession, [])

    def documento(self, url):
        self._chiama("documento")
        return self._doc[url]

    def fornitore(self, ticker):
        self._chiama("fornitore")
        return self._forn


def _sub_6k():
    return {"filings": {"recent": {
        "form": ["6-K", "6-K", "20-F"], "filingDate": ["2029-08-13", "2029-05-14", "2029-04-10"],
        "reportDate": ["2029-06-30", "2029-03-31", "2028-12-31"],
        "accessionNumber": ["0009990101-29-000050", "0009990101-29-000040", "0009990101-29-000030"],
        "items": ["", "", ""], "primaryDocument": ["zz6k.htm", "zzq1.htm", "zz20f.htm"]}}}


URL_6K = "https://www.sec.gov/Archives/edgar/data/9990101/000999010129000050/zzpr2q29.htm"


def _fornitore(con_trimestrali=True):
    return {"simbolo": "ZZFRESH", "valuta": "USD",
            "date_risultati": [{"data": "2029-08-12", "eps_riportato": 0.42}, {"data": "2029-11-12", "eps_riportato": None}],
            "trimestrali": {"income": {"2029-06-30": {"Total Revenue": 1234500000.0, "Net Income": 321900000.0,
                                                      "Diluted EPS": 0.42}}} if con_trimestrali else {}}


def _cascata(fonti, contesto=None, tempo=30.0):
    return ft.cascata("ZZFRESH", as_of=AS_OF, scadenza_monotonic=time.monotonic() + tempo,
                      contesto=contesto, fonti=fonti)


def _livello(esito, n):
    return next(l for l in esito["livelli"] if l["livello"] == n)


def test_cascata_si_ferma_alla_sec_quando_ha_il_periodo():
    f = Fonti(facts=_facts(), fornitore=_fornitore())
    e = _cascata(f)
    assert e["stato"] == "aggiornato" and e["periodo_atteso"]["periodo"] == "2029-06-30"
    assert e["fonte_usata"]["livello"] == 1 and e["periodo_trovato"] == "2029-06-30"
    assert _livello(e, 1)["filing_date"] == "2029-07-30" and "SEC" in _livello(e, 1)["etichetta"]
    assert all(_livello(e, n)["stato"] == "non_eseguito" for n in (2, 3, 4))
    assert "submissions" not in f.chiamate                  # il livello 3 non e' stato toccato


def test_cik_dal_profilo_filing_per_il_titolo_con_suffisso():
    f = Fonti(cik=None, facts=_facts(), fornitore=_fornitore())
    e = ft.cascata("QQSYN.MI", as_of=AS_OF, scadenza_monotonic=time.monotonic() + 10,
                   contesto={"stato": "aggiornato", "profilo": {"cik": "9990101"}}, fonti=f)
    assert e["cik"] == CIK and e["fonte_usata"]["livello"] == 1 and "companyfacts" in f.chiamate


def test_deposito_dopo_il_cutoff_non_conta():
    facts = _facts()
    facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
        _ob(555.55, "2029-09-30", "10-Q", "Q3", "2029-10-30", "2029-07-01", "acc-q3"))
    lp = sec_xbrl.quarterly_history(facts["facts"], fino_al="2029-10-06")["latest_period"]
    assert lp["period_end"] == "2029-06-30"
    e = _cascata(Fonti(facts=facts, fornitore=_fornitore()))
    assert _livello(e, 1)["period_end"] == "2029-06-30"


def test_sec_vecchia_poi_archivio_filing_col_periodo():
    f = Fonti(facts=_facts(solo_annuale=True), fornitore=_fornitore())
    ctx = {"stato": "aggiornato", "run_filing": {"id": 7, "status": "ok", "ultimo_periodo": "2029-06-30"}}
    e = _cascata(f, contesto=ctx)
    assert _livello(e, 1)["stato"] == "vecchio" and _livello(e, 1)["period_end"] == "2028-12-31"
    assert e["fonte_usata"]["livello"] == 2 and e["stato"] == "aggiornato"
    assert _livello(e, 3)["stato"] == "non_eseguito"


def test_comunicato_dell_emittente_via_edgar():
    f = Fonti(facts=_facts(solo_annuale=True), submissions=_sub_6k(),
              indice={"0009990101-29-000050": ["zz6k.htm", "zzpr2q29.htm", "logo.jpg"]},
              documenti={URL_6K: _COMUNICATO_6K,
                         URL_6K.replace("zzpr2q29", "zz6k"): "<html>cover page only</html>"},
              fornitore=_fornitore())
    e = _cascata(f, contesto={"stato": "non_aggiornato", "motivo": "documenti non scaricati in tempo"})
    l3 = _livello(e, 3)
    assert e["fonte_usata"]["livello"] == 3 and l3["stato"] == "ok"
    assert l3["period_end"] == "2029-06-30" and l3["filing_date"] == "2029-08-13" and l3["url"] == URL_6K
    assert "comunicato ufficiale dell'emittente via SEC EDGAR" in l3["etichetta"]
    assert "1,234.5" in l3["estratto"]
    assert _livello(e, 2)["stato"] == "assente" and "non scaricati in tempo" in _livello(e, 2)["motivo"]
    assert _livello(e, 4)["stato"] == "non_eseguito"


def _sub_molti_6k():
    """Un'estera che deposita molti 6-K: i piu' recenti non sono risultati, quello dei risultati e' vicino
    alla data del calendario (2029-08-12)."""
    date_dep = ["2029-10-01", "2029-09-25", "2029-09-18", "2029-09-11", "2029-09-04", "2029-08-28", "2029-08-12"]
    return {"filings": {"recent": {
        "form": ["6-K"] * 7, "filingDate": date_dep, "reportDate": date_dep,
        "accessionNumber": ["0009990101-29-0001%02d" % i for i in range(7)], "items": [""] * 7,
        "primaryDocument": ["altro%d.htm" % i for i in range(6)] + ["zzh1.htm"]}}}


def test_comunicato_vicino_alla_data_dei_risultati_e_frase_senza_data():
    indice = {"0009990101-29-0001%02d" % i: ["altro%d.htm" % i] for i in range(6)}
    indice["0009990101-29-000106"] = ["zzh1.htm"]
    base = "https://www.sec.gov/Archives/edgar/data/9990101/0009990101290001%02d/%s"
    documenti = {base % (i, "altro%d.htm" % i): "<p>share buyback notice</p>" for i in range(6)}
    documenti[base % (6, "zzh1.htm")] = "<p>ZZFRESH: net sales in the second quarter of 2029 grew; net profit 77.7</p>"
    f = Fonti(facts=_facts(solo_annuale=True), submissions=_sub_molti_6k(), indice=indice, documenti=documenti,
              fornitore=_fornitore())
    e = _cascata(f)
    l3 = _livello(e, 3)
    assert l3["stato"] == "ok" and l3["period_end"] == "2029-06-30" and l3["filing_date"] == "2029-08-12"
    assert "dedotto" in l3["nota"]
    assert f.chiamate.count("documento") == 1            # il primo letto e' quello vicino ai risultati


def test_frase_senza_data_non_vale_con_esercizio_non_solare():
    assert ft._periodi_solari("results for the second quarter of 2029") == [date(2029, 6, 30)]
    assert ft._periodi_solari("first six months of 2029") == [date(2029, 6, 30)]
    f = Fonti(cik=CIK, facts=None, submissions=_sub_6k(),
              indice={"0009990101-29-000050": ["zzpr.htm"]},
              documenti={"https://www.sec.gov/Archives/edgar/data/9990101/000999010129000050/zzpr.htm":
                         "<p>Revenue and net income for the second quarter of 2029</p>"}, fornitore=None)
    l3 = ft._livello_comunicato(f, CIK, "2029-06-20", date(2029, 10, 6), time.monotonic() + 5, time.monotonic,
                                None, solare=False)
    assert l3["stato"] == "assente"


def test_eps_del_fornitore_piu_recente_dei_bilanci():
    forn = _fornitore()
    forn["trimestrali"] = {"income": {"2028-06-30": {"Diluted EPS": 1.11}}}
    e = _cascata(Fonti(cik=None, fornitore=forn))
    l4 = _livello(e, 4)
    assert l4["stato"] == "ok" and l4["valori"] == {"EPS riportato": 0.42} and "fermi al 2028-06-30" in l4["nota"]


def test_fornitore_dati_ultimo_livello_con_etichetta():
    f = Fonti(cik=None, fornitore=_fornitore())
    e = _cascata(f)
    l4 = _livello(e, 4)
    assert e["fonte_usata"]["livello"] == 4 and l4["period_end"] == "2029-06-30"
    assert "fornitore dati" in l4["etichetta"] and "non filing" in l4["etichetta"]
    assert l4["valori"]["Total Revenue"] == 1234500000.0
    assert "nessun CIK sintetico" in _livello(e, 1)["motivo"]


def test_fornitore_senza_trimestrali_da_solo_eps_dichiarato():
    f = Fonti(cik=None, fornitore=_fornitore(con_trimestrali=False))
    e = _cascata(f)
    l4 = _livello(e, 4)
    assert l4["stato"] == "ok" and l4["valori"] == {"EPS riportato": 0.42}
    assert l4["period_end"] == "2029-06-30" and "dedotto" in l4["nota"]


def test_tutte_le_fonti_giu_lacuna_motivata_mai_eccezione():
    f = Fonti(rotte={"cik", "companyfacts", "submissions", "indice", "documento", "fornitore"})
    e = _cascata(f)
    assert e["stato"] == "lacuna" and e["fonte_usata"] is None
    assert "ConnectionError" in e["lacuna"]
    assert all(l["stato"] in ("errore", "assente") for l in e["livelli"])
    p = ft.passo_freschezza("ZZFRESH", as_of=AS_OF, scadenza_monotonic=time.monotonic() + 5, contesto=None, fonti=f)
    assert p["stato"] == "lacuna"


def test_passo_non_solleva_con_contesto_e_fonti_assurdi():
    p = ft.passo_freschezza("ZZFRESH", as_of="non-una-data", scadenza_monotonic=time.monotonic() + 2,
                            contesto="non-un-dict", fonti=object())
    assert isinstance(p, dict) and p["stato"] in ("lacuna", "errore")


def test_tempo_massimo_duro_rispettato():
    f = Fonti(facts=_facts(solo_annuale=True), fornitore=_fornitore(), lente={"submissions": 5.0})
    t0 = time.monotonic()
    e = _cascata(f, tempo=1.5)
    assert time.monotonic() - t0 < 3.0
    assert _livello(e, 3)["stato"] == "tempo_scaduto"
    assert e["stato"] in ("lacuna", "tempo_scaduto", "aggiornato")


def test_periodo_futuro_al_cutoff_non_conta():
    forn = _fornitore()
    forn["trimestrali"]["income"]["2029-12-31"] = {"Total Revenue": 9.99}
    e = _cascata(Fonti(cik=None, fornitore=forn))
    assert _livello(e, 4)["period_end"] == "2029-06-30"


def test_strumento_non_applicabile_non_cerca_bilanci():
    f = Fonti(facts=_facts(), fornitore=_fornitore())
    e = _cascata(f, contesto={"stato": "non_applicabile", "motivo": "ETF"})
    assert e["stato"] == "non_applicabile" and f.chiamate == [] and "ETF" in e["motivo"]


# ------------------------------------------------------------------ cache, tool, dossier

def test_passo_scrive_la_cache_e_il_tool_la_legge(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    f = Fonti(facts=_facts(), fornitore=_fornitore())
    ft.passo_freschezza("ZZFRESH", as_of=AS_OF, scadenza_monotonic=time.monotonic() + 10, fonti=f)
    rotte = Fonti(rotte={"cik", "companyfacts", "fornitore"})
    b = ft.blocco_per_tool("ZZFRESH", fonti=rotte, oggi=date(2029, 10, 6))
    assert rotte.chiamate == []                              # dalla cache, nessuna rete
    assert b["period_end"] == "2029-06-30" and b["filing_date"] == "2029-07-30"
    assert b["livello"] == 1 and b["valori"]["revenue"] == 123.45 and b["da_cache"] is True


def test_blocco_per_tool_senza_cache_calcola_e_dichiara(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    b = ft.blocco_per_tool("ZZFRESH", fonti=Fonti(cik=None, fornitore=_fornitore()), oggi=date(2029, 10, 6))
    assert b["livello"] == 4 and b["da_cache"] is False and "fornitore dati" in b["etichetta"]
    b2 = ft.blocco_per_tool("QQSYN.MI", fonti=Fonti(rotte={"cik", "fornitore"}), oggi=date(2029, 10, 6))
    assert b2["stato"] == "lacuna" and b2["lacuna"]


def test_lacuna_in_cache_si_riprova_dopo_un_ora(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    ft.passo_freschezza("ZZFRESH", as_of=AS_OF, scadenza_monotonic=time.monotonic() + 5,
                        fonti=Fonti(rotte={"cik", "fornitore"}))
    vero = time.time
    monkeypatch.setattr(ft.time, "time", lambda: vero() + 2 * 3600)
    b = ft.blocco_per_tool("ZZFRESH", fonti=Fonti(cik=None, fornitore=_fornitore()), oggi=date(2029, 10, 6))
    assert b["da_cache"] is False and b["livello"] == 4


def test_cache_con_cutoff_anteriore_non_viene_usata(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    ft.passo_freschezza("ZZFRESH", as_of=AS_OF, scadenza_monotonic=time.monotonic() + 10,
                        fonti=Fonti(facts=_facts(), fornitore=_fornitore()))
    b = ft.blocco_per_tool("ZZFRESH", fonti=Fonti(cik=None, fornitore=_fornitore()), oggi=date(2029, 7, 1))
    assert b["da_cache"] is False                            # la cache ha un periodo dopo il cutoff


def test_chat_tools_aggiunge_il_blocco_a_financial_history_e_fundamentals(monkeypatch):
    from bellomberg.agents import chat_tools, agent_tools
    blocco = {"stato": "aggiornato", "period_end": "2029-06-30", "filing_date": "2029-07-30", "livello": 1}
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: dict(blocco, ticker=t))
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: {"ticker": t, "items": {}})
    monkeypatch.setattr(agent_tools, "tool_get_fundamentals", lambda ticker: {"ticker": ticker, "pe_trailing": 12.3})
    from bellomberg.market_data import esef
    monkeypatch.setattr(esef, "get_esef_history", lambda *a, **k: pytest.fail("ESEF chiamato con la SEC riuscita"))
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"})
    assert r["data"]["ultimo_periodo_pubblicato"]["period_end"] == "2029-06-30" and "esef_note" not in r["data"]
    r = chat_tools.dispatch("get_fundamentals", {"ticker": "ZZFRESH"})
    assert r["data"]["ultimo_periodo_pubblicato"]["ticker"] == "ZZFRESH"


def test_numero_fresco_nella_ricevuta_e_attestabile_dal_cancello():
    """Il cancello non si allenta: lega l'Evidence solo se numero e period_end stanno nella ricevuta."""
    from bellomberg.agents import trade_idea
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    out = {"_source": "SEC XBRL companyfacts (ZZFRESH)", "data": {"ticker": "ZZFRESH", "ultimo_periodo_pubblicato": {
        "stato": "aggiornato", "period_end": "2029-06-30", "filing_date": "2029-07-30", "valori": {"revenue": 123.45}}}}
    board = SimpleNamespace(execution_policy="trade-idea-research/3", analysis_mode=RESEARCH_ANALYSIS_MODE, data={},
                            tool_receipts=[{"tool": "get_financial_history", "success": True,
                                            "input": {"ticker": "ZZFRESH"}, "output": json.dumps(out)}])
    result = {"dossier": [{"key": "executive", "evidence_ids": ["e1"]}],
              "evidence": [{"id": "e1", "source": "[src: get_financial_history] SEC", "as_of": "2029-06-30",
                            "summary": "Ricavi Q2 123.45 USD"}]}
    bound, numeric = trade_idea._bound_evidence_details(result, board, "ZZFRESH", "2029-10-06")
    assert set(bound) == {"e1"} and numeric == {"e1"}
    result["evidence"][0]["summary"] = "Ricavi Q2 876.54 USD"            # numero non nella ricevuta
    bound, numeric = trade_idea._bound_evidence_details(result, board, "ZZFRESH", "2029-10-06")
    assert numeric == set()


def test_sezione_dossier_dal_record_della_fase():
    esito = _cascata(Fonti(cik=None, fornitore=_fornitore()))
    s = ft.sezione_dossier(esito)
    assert s["periodo_atteso"] == "2029-06-30" and s["periodo_trovato"] == "2029-06-30"
    assert s["livello"] == 4 and "fornitore dati" in s["etichetta"]
    assert "get_financial_history" in s["istruzione"] and "period_end" in s["istruzione"]
    lac = ft.sezione_dossier(_cascata(Fonti(rotte={"cik", "fornitore"})))
    assert lac["lacuna"] and lac["periodo_trovato"] is None


def test_dossier_della_trade_idea_porta_la_sezione(monkeypatch):
    from bellomberg.valuation import company_dossier
    from bellomberg.agents import ponte_filing_dossier
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    esito = _cascata(Fonti(cik=None, fornitore=_fornitore()))
    snap = {"ticker": "ZZFRESH", "as_of": "2029-10-06", "documents": [], "revision_sha256": "x" * 64}
    board = SimpleNamespace(analysis_mode=RESEARCH_ANALYSIS_MODE, source_qualification=None,
                            company_source_session_if_known=lambda t: SimpleNamespace(snapshot=lambda **kw: snap),
                            data={"_filing_titolo_nuovo": {"ticker": "ZZFRESH", "passi": {"freschezza": esito}}})
    monkeypatch.setattr(ponte_filing_dossier, "documenti_ponte", lambda bb, t: None)
    d = company_dossier._research_dossier_for_board(board, "ZZFRESH")
    assert d["ultimo_periodo_pubblicato"]["periodo_trovato"] == "2029-06-30"
    board.data = {}
    d = company_dossier._research_dossier_for_board(board, "ZZFRESH")
    assert "ultimo_periodo_pubblicato" not in d
    board.data = {"_filing_titolo_nuovo": {"ticker": "ALTRO", "passi": {"freschezza": esito}}}
    assert "ultimo_periodo_pubblicato" not in company_dossier._research_dossier_for_board(board, "ZZFRESH")


# ------------------------------------------------------------------ e2e Trade Idea offline

from test_trade_idea_no_workbook_e2e import no_workbook_case  # noqa: E402,F401  (fixture)
from test_trade_idea_store import db_path, migrated, store  # noqa: E402,F401
from _smtp_cattura import smtp  # noqa: E402,F401
from test_trade_idea_economic import IDENTITY  # noqa: E402


def _dossier_e_fase(detail):
    data = detail["progress"]["checkpoint"]["data"]
    return data["_research_thesis"]["dossiers"][IDENTITY["ticker"]], data.get("_filing_titolo_nuovo") or {}


def _passo_con(monkeypatch, fonti):
    """La run isolata (DB alternativo) usa i passi isolati della fase: si inietta il passo con fonti esplicite."""
    from functools import partial
    from bellomberg.market_data import filing_titolo_nuovo
    monkeypatch.setattr(filing_titolo_nuovo, "passi_isolati",
                        lambda: [("freschezza", partial(ft.passo_freschezza, fonti=fonti))])


def test_e2e_tutte_le_fonti_giu_la_run_arriva_in_fondo_con_lacuna_dichiarata(no_workbook_case, monkeypatch):
    """Fonti finte che sollevano ConnectionError ovunque (SEC, EDGAR, yfinance)."""
    _passo_con(monkeypatch, Fonti(rotte={"cik", "companyfacts", "submissions", "indice", "documento", "fornitore"}))
    case = no_workbook_case
    run_id = case.current.create_run(case.request, idempotency_key="freschezza-giu")["run"]["id"]
    detail = case.execute(run_id, "giu")
    assert detail["run"]["technical_status"] == "completed", detail["run"]["reason"]
    dossier, fase = _dossier_e_fase(detail)
    passo = fase["passi"]["freschezza"]
    assert passo["stato"] == "lacuna" and "ConnectionError" in passo["lacuna"]
    assert dossier["ultimo_periodo_pubblicato"]["lacuna"]
    assert any(i["code"] == "ultimo_periodo_lacuna" for i in dossier["issues"])


def test_e2e_numeri_freschi_dal_fornitore_arrivano_nel_dossier(no_workbook_case, monkeypatch):
    oggi = date.today()
    fine = ft._fine_prima(oggi - __import__("datetime").timedelta(days=ft.GIORNI_TERMINE - 1), None).isoformat()

    class _Finte(Fonti):
        def __init__(self):
            super().__init__(cik=None, fornitore={"simbolo": "ZZ", "valuta": "USD", "date_risultati": [],
                                                  "trimestrali": {"income": {fine: {"Total Revenue": 4321.5}}}})
    _passo_con(monkeypatch, _Finte())
    case = no_workbook_case
    run_id = case.current.create_run(case.request, idempotency_key="freschezza-ok")["run"]["id"]
    detail = case.execute(run_id, "ok")
    assert detail["run"]["technical_status"] == "completed", detail["run"]["reason"]
    dossier, fase = _dossier_e_fase(detail)
    sez = dossier["ultimo_periodo_pubblicato"]
    assert fase["passi"]["freschezza"]["stato"] == "aggiornato", fase["passi"]["freschezza"]
    assert sez["periodo_trovato"] == fine and sez["livello"] == 4 and "non filing" in sez["etichetta"]
    assert any(i["code"] == "ultimo_periodo_da_fornitore" for i in dossier["issues"])
    # vista del prompt di Capo / R2 / Red Team (research_context usa l'indice compatto col ponte)
    from bellomberg.agents.ponte_filing_dossier import indice_compatto
    assert "filing_bridge" in dossier
    vista = indice_compatto(dossier)["ultimo_periodo_pubblicato"]
    assert vista["periodo_trovato"] == fine and vista["livello"] == 4 and "non filing" in vista["etichetta"]
    assert "get_financial_history" in vista["istruzione"]


# ------------------------------------------------------------------ R-CASCATA: tetto e cancello per gruppo

def _fh_realistica(anni=10):
    """Ricevuta di get_financial_history della taglia di un titolo vero (30 voci x 10 anni)."""
    voci = ["v%02d" % i for i in range(30)]
    items = {v: {2019 + a: 123456789012.0 + a for a in range(anni)} for v in voci}
    derived = {d: {2019 + a: 0.1234 for a in range(anni)} for d in ("gross_margin", "operating_margin",
                                                                    "net_margin", "capex_pct_revenue", "fcf")}
    trimestri = ["2027-%02d-30" % m for m in (3, 6, 9)] + ["2028-%02d-30" % m for m in (3, 6, 9, 12)] + ["2029-03-31"]
    return {"ticker": "ZZFRESH", "cik": CIK, "entity": "ZZFRESH Synthetic Corp", "years": list(range(2019, 2019 + anni)),
            "n_items": 30, "items": items, "derived": derived,
            "tags_used": {v: "us-gaap:SomeVeryLongTagNameForTheItem" + v for v in voci},
            "units": {v: "USD" for v in voci},
            "quarters": {k: {e: 98765432.1 for e in trimestri} for k in sec_xbrl.QUARTER_CORE},
            "quarter_ends": trimestri,
            "latest_period": {"period_end": "2029-06-30", "filing_date": "2029-07-30", "form": "10-Q",
                              "values": {"revenue": 123.45}},
            "_source": "SEC XBRL companyfacts", "da_cache": True, "companyfacts_letto_il": "2029-10-01T10:00:00",
            "index_note": "companyfacts in cache anteriore a un deposito (prova)"}


def _blocco_l3():
    esito = _cascata(Fonti(facts=_facts(solo_annuale=True), submissions=_sub_6k(),
                           indice={"0009990101-29-000050": ["zzpr2q29.htm"]},
                           documenti={URL_6K: _COMUNICATO_6K * 40}, fornitore=_fornitore()))
    return ft._compatto(esito, da_cache=True, scritto=time.time())


def test_ricevuta_realistica_sta_sotto_il_tetto_col_blocco_in_testa(monkeypatch):
    from bellomberg.agents import chat_tools
    blocco = _blocco_l3()
    assert len(blocco["estratto"]) == ft.MAX_ESTRATTO
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: dict(blocco))
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: _fh_realistica())
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"})
    testo = json.dumps(r, default=str, ensure_ascii=False)
    assert len(testo) <= 0.85 * chat_tools.TETTO_TOOL_RESULT       # margine: 85% del tetto
    assert list(r["data"])[:2] == ["ultimo_periodo_pubblicato", "_vista"]
    assert testo.find("ultimo_periodo_pubblicato") < 200
    uscito = dict(r["data"]["ultimo_periodo_pubblicato"])
    assert uscito.pop("calcolato_al").endswith("(oggi: nessun cutoff della run passato al tool)")
    assert uscito == blocco                                          # blocco sempre intero
    for k in ("items", "da_cache", "companyfacts_letto_il", "index_note", "latest_period"):
        assert k in r["data"], k                                   # eta' del dato mai tolta
    assert len(r["data"]["quarter_ends"]) == 8                      # i trimestri sono lo scopo: tagliati per ultimi
    assert r["data"]["years"] == [2025, 2026, 2027, 2028]
    assert "anni anteriori agli ultimi 4" in r["data"]["_vista"]


def test_ricevuta_che_sta_gia_sotto_il_tetto_resta_intatta(monkeypatch):
    from bellomberg.agents import chat_tools
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: {"stato": "aggiornato", "period_end": "2029-06-30"})
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: {"ticker": t, "items": {}, "quarters": {}})
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"})
    assert "_vista" not in r["data"] and "quarters" in r["data"]


def test_ricevuta_enorme_taglia_dichiarando_e_tiene_l_eta(monkeypatch):
    from bellomberg.agents import chat_tools
    fh = _fh_realistica(anni=30)
    fh["items"] = {("w%03d" % i): {y: 123456789012.0 for y in fh["years"]} for i in range(120)}
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: _blocco_l3())
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: fh)
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"})["data"]
    v = r["_vista"]
    assert v.index("anni anteriori") < v.index("`derived`") < v.index("`tags_used`") < v.index("trimestri anteriori")
    assert "supera COMUNQUE" in v and len(r["quarter_ends"]) == 4
    assert r["da_cache"] is True and r["companyfacts_letto_il"] and r["index_note"]
    assert r["ultimo_periodo_pubblicato"]["period_end"] == "2029-06-30" and len(r["ultimo_periodo_pubblicato"]["estratto"]) > 1000


def _board_con(output, tool="get_financial_history"):
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    return SimpleNamespace(execution_policy="trade-idea-research/3", analysis_mode=RESEARCH_ANALYSIS_MODE, data={},
                           tool_receipts=[{"tool": tool, "success": True, "input": {"ticker": "ZZFRESH"},
                                           "output": json.dumps(output)}])


def _lega(board, summary, as_of, tool="get_financial_history"):
    from bellomberg.agents import trade_idea
    result = {"dossier": [{"key": "executive", "evidence_ids": ["e1"]}],
              "evidence": [{"id": "e1", "source": "[src: %s] x" % tool, "as_of": as_of, "summary": summary}]}
    return trade_idea._bound_evidence_details(result, board, "ZZFRESH", "2029-10-06")


def _ricevuta_fh():
    return {"_source": "SEC", "data": {
        "ultimo_periodo_pubblicato": {"stato": "aggiornato", "period_end": "2029-06-30", "filing_date": "2029-07-30",
                                      "valori": {"revenue": 123.45}, "livelli": [{"livello": 1, "stato": "ok"}]},
        "latest_period": {"period_end": "2029-06-30", "values": {"other": 555.55}},
        "items": {"revenue": {"2018": 777.77}}, "pe_trailing": 31.42}}


def test_cancello_numero_del_blocco_attestato_con_la_sua_data():
    _, numeric = _lega(_board_con(_ricevuta_fh()), "Ricavi Q2 123.45 USD", "2029-06-30")
    assert numeric == {"e1"}


def test_cancello_numero_vecchio_o_ttm_non_attestato_con_la_data_del_trimestre():
    board = _board_con(_ricevuta_fh())
    assert _lega(board, "Ricavi 2018 777.77 USD", "2029-06-30")[1] == set()
    assert _lega(board, "P/E TTM 31.42 USD", "2029-06-30")[1] == set()
    assert _lega(board, "Altro 555.55 USD", "2029-06-30")[1] == {"e1"}   # latest_period SEC: numero nel suo gruppo
    board = _board_con(_ricevuta_fh(), tool="get_fundamentals")
    assert _lega(board, "P/E TTM 31.42 USD", "2029-06-30", tool="get_fundamentals")[1] == set()
    assert _lega(board, "Ricavi Q2 123.45 USD", "2029-06-30", tool="get_fundamentals")[1] == {"e1"}


def test_cancello_data_al_livello_principale_invariato():
    """as_of di get_price_live in cima al payload: l'ambito resta il payload intero (comportamento di prima)."""
    out = {"_source": "yfinance", "data": {"as_of": "2029-10-05", "price": 45.67, "dettagli": {"prev_close": 44.44}}}
    board = _board_con(out, tool="get_price_live")
    assert _lega(board, "Prezzo 45.67 USD", "2029-10-05", tool="get_price_live")[1] == {"e1"}
    assert _lega(board, "Chiusura precedente 44.44 USD", "2029-10-05", tool="get_price_live")[1] == {"e1"}


def test_cancello_blocco_in_lacuna_non_attesta_nulla():
    out = _ricevuta_fh()
    out["data"]["ultimo_periodo_pubblicato"]["stato"] = "lacuna"
    del out["data"]["latest_period"]
    assert _lega(_board_con(out), "Ricavi Q2 123.45 USD", "2029-06-30") == ({}, set())


def test_blocco_compatto_ha_la_data_solo_sul_livello_usato():
    b = _blocco_l3()
    assert b["period_end"] == "2029-06-30" and all("period_end" not in l for l in b["livelli"])
    assert len(b["estratto"]) <= ft.MAX_ESTRATTO


def test_cancello_gruppo_datato_esef_resta_attestabile_solo_nel_suo_gruppo():
    """Ripiego ESEF (o qualsiasi gruppo annidato con la sua data): numero e data nello stesso sotto-oggetto."""
    out = {"_source": "ESEF", "data": {"items": {"revenue": {"2028": 444.44}},
                                       "filings": [{"period_end": "2028-12-31", "revenue": 333.33}],
                                       "ultimo_periodo_pubblicato": {"stato": "lacuna", "period_end": None}}}
    board = _board_con(out)
    assert _lega(board, "Ricavi FY 333.33 EUR", "2028-12-31")[1] == {"e1"}
    assert _lega(board, "Ricavi FY 444.44 EUR", "2028-12-31")[1] == set()


def test_cancello_data_al_livello_principale_data_il_payload_come_prima():
    """Contratto di prima per una data in cima (fixture sintetiche dei test v4): ambito = payload intero."""
    out = {"_source": "x", "data": {"as_of": "2029-06-30", "pe_trailing": 31.42}}
    assert _lega(_board_con(out, tool="get_fundamentals"), "P/E 31.42 USD", "2029-06-30",
                 tool="get_fundamentals")[1] == {"e1"}


_CHIAVI_DATA = {"as_of", "price_asof", "valuation_date", "fiscal_date", "period_end", "reported_at",
                "filing_date", "observed_at"}


@pytest.mark.parametrize("tool", ["get_fundamentals", "get_financial_history"])
def test_cablaggio_chat_tools_blocco_vero_in_testa_senza_date_in_cima(tool, tmp_path, monkeypatch):
    """Nessuna sostituzione di blocco_per_tool: la cascata vera gira con le fonti finte della fixture
    (ConnectionError ovunque) e la lacuna arriva dichiarata in testa alla ricevuta."""
    from bellomberg.agents import chat_tools, agent_tools
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(agent_tools, "tool_get_fundamentals", lambda ticker: {"ticker": ticker, "pe_trailing": 12.3})
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: {"ticker": t, "items": {"revenue": {2028: 1.5}}})
    r = chat_tools.dispatch(tool, {"ticker": "ZZCABLE"}, as_of="2029-10-06")
    assert list(r["data"])[0] == "ultimo_periodo_pubblicato"
    b = r["data"]["ultimo_periodo_pubblicato"]
    assert b["stato"] == "lacuna" and "ConnectionError" in b["lacuna"] and b["period_end"] is None
    assert not (_CHIAVI_DATA & set(r["data"]))                    # nessuna data economica in cima



# ------------------------------------------------------------------ R-CASCATA: difetti medi e mutanti sopravvissuti

def _sub_uno(form, deposito, acc, doc, items=""):
    return {"filings": {"recent": {"form": [form], "filingDate": [deposito], "reportDate": [deposito],
                                   "accessionNumber": [acc], "items": [items], "primaryDocument": [doc]}}}


def _url(acc, doc):
    return "https://www.sec.gov/Archives/edgar/data/9990101/%s/%s" % (acc.replace("-", ""), doc)


def test_6k_sul_buyback_non_e_un_comunicato_dei_risultati():
    acc = "0009990101-29-000077"
    testo = ("<p>ZZFRESH Ltd - Report on transactions in own shares. During the period ended September 30, 2029 "
             "the company repurchased 1,234,567 shares at an average price of 45.67.</p>")
    f = Fonti(submissions=_sub_uno("6-K", "2029-10-02", acc, "buyback.htm"), indice={acc: ["buyback.htm"]},
              documenti={_url(acc, "buyback.htm"): testo})
    l3 = ft._livello_comunicato(f, CIK, "2029-09-30", date(2029, 10, 6), time.monotonic() + 5, time.monotonic, None)
    assert l3["stato"] == "assente" and "non e' un comunicato dei risultati" in l3["motivo"]
    assert ft._e_comunicato_risultati(_COMUNICATO_6K)


def test_tolleranza_esercizio_a_52_53_settimane():
    """Un trimestre che chiude il 27/06 copre l'atteso 30/06 (tolleranza dichiarata)."""
    assert ft._copre("2029-06-27", "2029-06-30") and not ft._copre("2029-06-10", "2029-06-30")


def test_comunicato_depositato_prima_del_periodo_atteso_non_vale():
    acc = "0009990101-29-000033"
    testo = "<p>Revenue and net income outlook for the three months ended June 30, 2029.</p>"
    f = Fonti(submissions=_sub_uno("6-K", "2029-05-14", acc, "outlook.htm"), indice={acc: ["outlook.htm"]},
              documenti={_url(acc, "outlook.htm"): testo})
    l3 = ft._livello_comunicato(f, CIK, "2029-06-30", date(2029, 10, 6), time.monotonic() + 5, time.monotonic, None)
    assert l3["stato"] == "assente" and "indice" not in f.chiamate


def test_comunicato_con_comparativo_prende_il_periodo_piu_recente():
    acc = "0009990101-29-000050"
    testo = ("<p>Revenue for the three months ended June 30, 2029 was 1,234.5 million versus the three months "
             "ended June 30, 2028; net income 321.9 million.</p>")
    f = Fonti(submissions=_sub_6k(), indice={acc: ["zzpr2q29.htm"]}, documenti={URL_6K: testo})
    l3 = ft._livello_comunicato(f, CIK, "2029-06-30", date(2029, 10, 6), time.monotonic() + 5, time.monotonic, None)
    assert l3["stato"] == "ok" and l3["period_end"] == "2029-06-30"


def test_valuta_del_fornitore_mancante_dichiarata_mai_usd():
    forn = _fornitore()
    forn.pop("valuta", None)
    l4 = _livello(_cascata(Fonti(cik=None, fornitore=forn)), 4)
    assert l4["stato"] == "ok" and l4["valuta"] == "n.d."


def test_nota_dei_flussi_cumulati_nel_livello_sec():
    l1 = _livello(_cascata(Fonti(facts=_facts(), fornitore=_fornitore())), 1)
    assert "cfo: cumulato da inizio esercizio" in l1["nota"]


def _facts_con_q4():
    """Tre 10-Q e il 10-K dello stesso esercizio 2028, con i comparativi dentro i 10-Q."""
    rev, cash = [], []
    for fp, (st, en), v, acc, filed in (("Q1", ("2028-01-01", "2028-03-31"), 100.0, "a-q1", "2028-04-30"),
                                         ("Q2", ("2028-04-01", "2028-06-30"), 110.0, "a-q2", "2028-07-30"),
                                         ("Q3", ("2028-07-01", "2028-09-30"), 120.0, "a-q3", "2028-10-30")):
        rev.append(_ob(v, en, "10-Q", fp, filed, st, acc))
        rev.append(_ob(v - 50, en.replace("2028", "2027"), "10-Q", fp, filed,
                       st.replace("2028", "2027"), acc))                 # comparativo dell'anno prima
        cash.append(_ob(v / 10, en, "10-Q", fp, filed, None, acc))
        cash.append(_ob(9.99, "2027-12-31", "10-Q", fp, filed, None, acc))  # saldo comparativo di fine anno
    rev.append(_ob(460.0, "2028-12-31", "10-K", "FY", "2029-02-20", "2028-01-01", "a-k"))
    rev.append(_ob(130.5, "2028-12-31", "10-K", "FY", "2029-03-01", "2028-10-01", "a-k"))  # Q4 di 3 mesi nel 10-K
    cash.append(_ob(13.0, "2028-12-31", "10-K", "FY", "2029-02-20", None, "a-k"))
    return {"us-gaap": {"Revenues": {"units": {"USD": rev}},
                        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": cash}},
                        "EarningsPerShareDiluted": {"units": {"USD/shares": [
                            _ob(1.0, "2028-03-31", "10-Q", "Q1", "2028-04-30", "2028-01-01", "a-q1"),
                            _ob(4.2, "2028-12-31", "10-K", "FY", "2029-02-20", "2028-01-01", "a-k")]}}}}


def test_q4_derivato_dichiarato_e_comparativi_esclusi():
    q = sec_xbrl.quarterly_history(_facts_con_q4())
    assert q["quarters"]["revenue"] == {"2028-03-31": 100.0, "2028-06-30": 110.0, "2028-09-30": 120.0,
                                        "2028-12-31": 130.0}                  # 460 - 330, mai il 130.5 a 3 mesi
    assert q["q4"]["derivati"] == {"2028-12-31": ["revenue"]}
    assert q["q4"]["non_derivabili"] == {"2028-12-31": ["eps_diluted"]}      # l'EPS non si somma
    assert "2027-03-31" not in q["quarters"]["revenue"]                       # comparativi fuori
    assert q["quarters"]["cash"]["2028-12-31"] == 13.0 and "2027-12-31" not in q["quarters"]["cash"]
    lp = q["latest_period"]
    assert lp["tipo"] == "esercizio" and lp["values"]["revenue"] == 460.0 and lp["form"] == "10-K"


def test_compatto_mantiene_il_tipo_del_periodo():
    facts = {"facts": _facts_con_q4()}
    forn = {"simbolo": "ZZ", "valuta": "USD", "trimestrali": {}, "date_risultati": [{"data": "2029-02-20", "eps_riportato": 4.2}]}
    e = ft.cascata("ZZFRESH", as_of="2029-03-15", scadenza_monotonic=time.monotonic() + 10,
                   fonti=Fonti(facts=facts, fornitore=forn))
    b = ft._compatto(e, da_cache=False)
    assert b["livello"] == 1 and b["tipo"] == "esercizio" and b["period_end"] == "2028-12-31"


def test_lock_per_titolo_i_desk_paralleli_non_ricalcolano(tmp_path):
    import threading
    f = Fonti(cik=None, fornitore=_fornitore(), lente={"fornitore": 0.5})
    uscite = []
    th = [threading.Thread(target=lambda: uscite.append(ft.blocco_per_tool("ZZLOCK", fonti=f, oggi=date(2029, 10, 6))))
          for _ in range(3)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert f.chiamate.count("fornitore") == 1
    assert sorted(u["da_cache"] for u in uscite) == [False, True, True]


def test_tetto_complessivo_esaurito_lacuna_dichiarata(monkeypatch):
    monkeypatch.setattr(ft, "BUDGET_FINESTRA_S", 0)
    f = Fonti(cik=None, fornitore=_fornitore())
    b = ft.blocco_per_tool("ZZBUDGET", fonti=f, oggi=date(2029, 10, 6))
    assert b["stato"] == "lacuna" and "tetto complessivo" in b["lacuna"] and f.chiamate == []


def _scaduti_finti(n):
    """n thread di rete DAVVERO oltre la loro scadenza (_entro con scadenza breve su una fonte bloccata)."""
    import threading
    libera = threading.Event()
    for _ in range(n):
        with pytest.raises(ft._TempoScaduto):
            ft._entro(lambda: libera.wait(30), time.monotonic() + 0.05)
    assert ft.scaduti_appesi() == n
    return libera


def test_troppi_thread_scaduti_la_chat_da_lacuna_con_la_frase_giusta():
    libera = _scaduti_finti(ft.MAX_THREAD_VIVI)
    try:
        f = Fonti(cik=None, fornitore=_fornitore())
        b = ft.blocco_per_tool("ZZVIVI", fonti=f, oggi=date(2029, 10, 6))
        assert b["stato"] == "lacuna" and f.chiamate == []
        assert "%d chiamate di rete precedenti ancora appese oltre il loro tempo" % ft.MAX_THREAD_VIVI in b["lacuna"]
        r = ft.blocco_per_tool("ZZVIVI2", fonti=f, oggi=date(2029, 10, 6), in_run=True, tempo_max_s=0.6)
        assert r["stato"] == "lacuna" and "oltre il loro tempo dopo 1 s di attesa" in r["lacuna"]
    finally:
        libera.set()


def test_run_aspetta_che_i_thread_scaduti_terminino_e_ottiene_il_blocco():
    import threading
    libera = _scaduti_finti(ft.MAX_THREAD_VIVI)
    threading.Timer(0.5, libera.set).start()
    b = ft.blocco_per_tool("ZZATTESA", fonti=Fonti(cik=None, fornitore=_fornitore()), oggi=date(2029, 10, 6),
                           in_run=True, tempo_max_s=10)
    assert b["stato"] == "aggiornato"
    assert ft.scaduti_appesi() == 0


def test_caso_a_preriscaldamento_piu_tre_desk_tutti_col_blocco():
    """R-CASCATA 2a verifica, caso A: 4 worker del preriscaldamento + 3 desk, tutti al lavoro nel loro
    tempo (nessuno scaduto): nessuna lacuna «appese»."""
    import threading
    lenta = lambda: Fonti(cik=None, fornitore=_fornitore(), lente={"fornitore": 1.0})
    pre = ft.preriscalda_in_background(["PA", "PB", "PC", "PD"], as_of="2029-10-06", fonti=lenta())
    time.sleep(0.2)
    esiti = {}
    ths = [threading.Thread(target=lambda t=t: esiti.__setitem__(
        t, ft.blocco_per_tool(t, fonti=lenta(), oggi=date(2029, 10, 6), in_run=True))) for t in ("DA", "DB", "DC")]
    for th in ths:
        th.start()
        time.sleep(0.2)            # come in R-CASCATA: ogni desk entra mentre gli altri sono gia' in rete
    for th in ths:
        th.join()
    pre.join(20)
    assert {t: b["stato"] for t, b in esiti.items()} == {"DA": "aggiornato", "DB": "aggiornato", "DC": "aggiornato"}


def test_cache_ricalcolata_se_e_passata_la_data_dei_risultati():
    forn = _fornitore()
    forn["date_risultati"] = [{"data": "2029-08-12", "eps_riportato": 0.42}, {"data": "2029-11-12", "eps_riportato": None}]
    ft.passo_freschezza("ZZRIS", as_of="2029-10-06", scadenza_monotonic=time.monotonic() + 10,
                        fonti=Fonti(cik=None, fornitore=forn))
    assert ft.blocco_per_tool("ZZRIS", fonti=Fonti(rotte={"cik", "fornitore"}), oggi=date(2029, 11, 11))["da_cache"] is True
    b = ft.blocco_per_tool("ZZRIS", fonti=Fonti(rotte={"cik", "fornitore"}), oggi=date(2029, 11, 12))
    assert b["da_cache"] is False


def test_fixture_cache_del_singolo_test(tmp_path):
    assert ft._cartella().startswith(str(tmp_path))


def test_as_of_della_run_solo_per_i_due_tool():
    from bellomberg.agents import chat_tools
    bb = SimpleNamespace(data={"_data_cutoff": "2029-10-01T08:00:00+00:00"})
    assert chat_tools.as_of_freschezza("get_fundamentals", bb) == {"as_of": "2029-10-01"}
    assert chat_tools.as_of_freschezza("get_financial_history", bb) == {"as_of": "2029-10-01"}
    assert chat_tools.as_of_freschezza("get_valuation", bb) == {}
    assert chat_tools.as_of_freschezza("get_fundamentals", SimpleNamespace(data={})) == {}


def test_cutoff_passato_a_get_financial_history(monkeypatch):
    from bellomberg.agents import chat_tools
    visti = []
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: visti.append(kw) or {"ticker": t})
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: visti.append(kw) or {"stato": "lacuna"})
    chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"}, as_of="2029-10-01")
    assert visti[0] == {"fino_al": "2029-10-01"} and visti[1]["oggi"] == date(2029, 10, 1)


def test_q4_non_derivato_con_trimestri_incompleti():
    facts = _facts_con_q4()
    facts["us-gaap"]["Revenues"]["units"]["USD"] = [ob for ob in facts["us-gaap"]["Revenues"]["units"]["USD"]
                                                     if ob["accn"] != "a-q2"]
    q = sec_xbrl.quarterly_history(facts)
    assert "2028-12-31" not in q["quarters"]["revenue"]
    assert "revenue" in q["q4"]["non_derivabili"]["2028-12-31"]


def test_trimestre_solo_cumulato_non_entra_nella_serie():
    facts = _facts()["facts"]
    facts["us-gaap"]["Revenues"]["units"]["USD"] = [ob for ob in facts["us-gaap"]["Revenues"]["units"]["USD"]
                                                     if ob["val"] != 123.45]          # Q2 solo cumulato 6 mesi
    q = sec_xbrl.quarterly_history(facts)
    assert "2029-06-30" not in q["quarters"]["revenue"]
    assert "revenue" not in q["latest_period"]["values"]       # un ricavo cumulato non si spaccia per trimestre


# ------------------------------------------------------------------ main 07/10: niente lacune per budget nelle run

def test_run_con_budget_chat_esaurito_ottiene_comunque_il_blocco(monkeypatch):
    monkeypatch.setattr(ft, "BUDGET_FINESTRA_S", 0)
    f = Fonti(cik=None, fornitore=_fornitore())
    chat = ft.blocco_per_tool("ZZRUN", fonti=f, oggi=date(2029, 10, 6))
    assert chat["stato"] == "lacuna" and "tetto complessivo" in chat["lacuna"]
    run = ft.blocco_per_tool("ZZRUN", fonti=f, oggi=date(2029, 10, 6), in_run=True)
    assert run["stato"] == "aggiornato" and run["period_end"] == "2029-06-30"


@pytest.mark.parametrize("caller,in_run", [("specialista-run:fundamentals", True), ("red-team", True),
                                           ("chat:fundamentals", False), (None, False)])
def test_dispatch_distingue_run_e_chat(monkeypatch, caller, in_run):
    from bellomberg.agents import chat_tools, agent_tools
    visti = []
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: visti.append(kw["in_run"]) or {"stato": "lacuna"})
    monkeypatch.setattr(agent_tools, "tool_get_fundamentals", lambda ticker: {"ticker": ticker})
    chat_tools.dispatch("get_fundamentals", {"ticker": "ZZRUN"}, caller=caller)
    assert visti == [in_run]


def test_preriscaldamento_con_fonte_giu_non_solleva_e_dichiara():
    f = Fonti(cik=None, fornitore=_fornitore(), rotte={"fornitore"})
    r = ft.preriscalda(["ZZA", "ZZB", "zza", ""], as_of="2029-10-06", fonti=f)
    assert r["titoli"] == 2 and set(r["lacune"]) == {"ZZA", "ZZB"} and not r["errori"]
    assert all(v.startswith("nessuna fonte ha il periodo atteso") for v in r["lacune"].values())


def test_preriscaldamento_riempie_la_cache_letta_dai_desk():
    f = Fonti(cik=None, fornitore=_fornitore())
    r = ft.preriscalda(["ZZC"], as_of="2029-10-06", fonti=f)
    assert r["aggiornati"] == ["ZZC"]
    b = ft.blocco_per_tool("ZZC", fonti=Fonti(rotte={"cik", "fornitore"}), oggi=date(2029, 10, 6))
    assert b["da_cache"] is True and b["period_end"] == "2029-06-30"


def test_preriscaldamento_con_input_assurdo_non_solleva():
    r = ft.preriscalda(object(), as_of="non-una-data")
    assert "_preriscaldamento" in r["errori"]
    t = ft.preriscalda_in_background(None, as_of=None)
    t.join(5)
    assert not t.is_alive()


def test_weekly_avvia_il_preriscaldamento_prima_di_r0():
    """Cablaggio: la run settimanale chiama preriscalda_in_background (lettura del sorgente: la weekly
    vera non si esegue senza DB e AI; il comportamento e' provato dai test sopra)."""
    import inspect
    from bellomberg.agents import consigliere_multi
    src = inspect.getsource(consigliere_multi)
    i = src.index("preriscalda_in_background(_filing_tickers(portfolio)")
    assert i < src.index("_ponte.ammetti_archivio_filing(bb, store")


def test_preriscaldamento_ignora_il_tetto_della_chat(monkeypatch):
    monkeypatch.setattr(ft, "BUDGET_FINESTRA_S", 0)
    r = ft.preriscalda(["ZZD"], as_of="2029-10-06", fonti=Fonti(cik=None, fornitore=_fornitore()))
    assert r["aggiornati"] == ["ZZD"]


def test_preriscaldamento_un_titolo_che_esplode_non_ferma_gli_altri(monkeypatch):
    vero = ft.blocco_per_tool

    def a_volte(t, **kw):
        if t == "ZZBOOM":
            raise RuntimeError("guasto sintetico")
        return vero(t, **kw)
    monkeypatch.setattr(ft, "blocco_per_tool", a_volte)
    r = ft.preriscalda(["ZZBOOM", "ZZE"], as_of="2029-10-06", fonti=Fonti(cik=None, fornitore=_fornitore()))
    assert r["errori"] == {"ZZBOOM": "RuntimeError"} and r["aggiornati"] == ["ZZE"]


def test_cancello_un_anno_non_e_una_misura_e_i_numeri_sono_interi():
    """R-CASCATA 2a verifica: «2029» sta in ogni period_end; «23.45» e' dentro «123.45» come sottostringa."""
    board = _board_con(_ricevuta_fh())
    assert _lega(board, "Ricavi Q2 2029 876.54 USD", "2029-06-30")[1] == set()
    assert _lega(board, "Depositati il 30/07/2029, margine 5% USD", "2029-06-30")[1] == set()
    assert _lega(board, "Ricavi 23.45 USD", "2029-06-30")[1] == set()
    assert _lega(board, "Ricavi 123.4 USD", "2029-06-30")[1] == set()
    assert _lega(board, "Ricavi Q2 2029 123.45 USD", "2029-06-30")[1] == {"e1"}
    assert _lega(board, "Ricavi 123 USD", "2029-06-30")[1] == set()


def test_cancello_il_segno_fa_parte_del_numero():
    """R-CASCATA 3a verifica: una perdita vera -123.45 non attesta «Utile 123.45»."""
    out = _ricevuta_fh()
    out["data"]["ultimo_periodo_pubblicato"]["valori"] = {"net_income": -123.45}
    board = _board_con(out)
    assert _lega(board, "Utile Q2 123.45 USD", "2029-06-30")[1] == set()
    assert _lega(board, "Perdita Q2 -123.45 USD", "2029-06-30")[1] == {"e1"}


# ------------------------------------------------------------------ R-CASCATA 2a verifica: N2, N3, N4, medi 4 e 5

def test_vista_non_suggerisce_un_recupero_inesistente_e_tiene_le_unita(monkeypatch):
    from bellomberg.agents import chat_tools
    fh = _fh_realistica(anni=30)
    fh["items"] = {("w%03d" % i): {y: 123456789012.0 for y in fh["years"]} for i in range(120)}
    fh["units"] = {k: "JPY" for k in fh["items"]}
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: _blocco_l3())
    monkeypatch.setattr(sec_xbrl, "get_financial_history", lambda t, years=10, **kw: fh)
    r = chat_tools.dispatch("get_financial_history", {"ticker": "ZZFRESH"})["data"]
    assert "years=" not in r["_vista"] and "non presenti in questa ricevuta" in r["_vista"]
    assert r["units"] == fh["units"] and "`units`" not in r["_vista"]


def test_blocco_dichiara_la_data_di_calcolo(monkeypatch):
    from bellomberg.agents import chat_tools, agent_tools
    monkeypatch.setattr(ft, "blocco_per_tool", lambda t, **kw: {"stato": "lacuna"})
    monkeypatch.setattr(agent_tools, "tool_get_fundamentals", lambda ticker: {"ticker": ticker})
    r = chat_tools.dispatch("get_fundamentals", {"ticker": "ZZFRESH"}, as_of="2029-10-01")
    assert r["data"]["ultimo_periodo_pubblicato"]["calcolato_al"] == "2029-10-01 (cutoff della run)"
    r = chat_tools.dispatch("get_fundamentals", {"ticker": "ZZFRESH"})
    assert "(oggi: nessun cutoff della run" in r["data"]["ultimo_periodo_pubblicato"]["calcolato_al"]


def test_cache_invalidata_dopo_la_data_stimata_senza_calendario_futuro():
    forn = _fornitore()
    forn["date_risultati"] = [{"data": "2029-08-12", "eps_riportato": 0.42}]       # nessuna data futura
    e = ft.passo_freschezza("ZZSTIMA", as_of="2029-10-06", scadenza_monotonic=time.monotonic() + 10,
                            fonti=Fonti(cik=None, fornitore=forn))
    assert e["periodo_atteso"]["prossimi_risultati"] is None
    assert e["periodo_atteso"]["prossimi_risultati_stimati"] == "2029-11-14"       # 30/09 + 45 giorni
    giu = Fonti(rotte={"cik", "fornitore"})
    assert ft.blocco_per_tool("ZZSTIMA", fonti=giu, oggi=date(2029, 11, 13))["da_cache"] is True
    assert ft.blocco_per_tool("ZZSTIMA", fonti=giu, oggi=date(2029, 11, 14))["da_cache"] is False


def test_preriscaldamento_fermato_non_chiama_e_non_scrive():
    import threading
    stop = threading.Event()
    stop.set()
    f = Fonti(cik=None, fornitore=_fornitore())
    r = ft.preriscalda(["ZZF1", "ZZF2"], as_of="2029-10-06", fonti=f, stop=stop)
    assert r["non_eseguiti"] == ["ZZF1", "ZZF2"] and f.chiamate == []


def test_preriscaldamento_tempo_massimo_complessivo():
    f = Fonti(cik=None, fornitore=_fornitore())
    r = ft.preriscalda(["ZZT1"], as_of="2029-10-06", fonti=f, tempo_max_s=0)
    assert r["non_eseguiti"] == ["ZZT1"] and f.chiamate == []


def test_fine_run_ferma_il_preriscaldamento_senza_scritture_dopo():
    f = Fonti(cik=None, fornitore=_fornitore(), lente={"fornitore": 1.0})
    righe = []
    t = ft.preriscalda_in_background(["ZZS1", "ZZS2", "ZZS3", "ZZS4", "ZZS5", "ZZS6"], as_of="2029-10-06",
                                     fonti=f, log=righe.append)
    time.sleep(0.3)
    assert ft.ferma_preriscaldamenti(attesa_s=10) == 1
    assert not t.is_alive()
    assert f.chiamate.count("fornitore") == 4                 # i 4 gia' partiti; i 2 in coda non partono
    import os
    assert not os.path.exists(ft._cartella()) or os.listdir(ft._cartella()) == []
    assert righe == []                                        # nessuna riga di log dopo la fine


def test_weekly_ferma_il_preriscaldamento_a_fine_run():
    import inspect
    from bellomberg.agents import consigliere_multi
    src = inspect.getsource(consigliere_multi.run_multi_agent)
    assert src.count("_ferma_preriscaldamento()") == 2      # fine normale e run interrotta
