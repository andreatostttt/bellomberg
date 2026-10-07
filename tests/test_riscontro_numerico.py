"""Riscontro numerico di un documento trovato fuori dal sito dell'emittente (07/10/2026, Opus 5.5).

Fonti FINTE iniettate (mai rete), ticker e numeri inventati (ZZTEST, CIK/LEI sintetici), cartella
dati in tmp. Ogni test dice la frase vera per il PM: riscontrato solo con 2 voci coincidenti e
nessuna contraria; la capogruppo ha numeri diversi; senza fonte o senza periodo si dichiara.
"""
import time

import pytest

from bellomberg.market_data import riscontro_numerico as rn

TICKER = "ZZTEST"
CIK = "9999901"
LEI = "ZZTESTLEI00000000001"


@pytest.fixture(autouse=True)
def dati_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("BELLOMBERG_DATA_DIR", str(tmp_path))


def _ob(val, end, start=None, form="10-Q", fp="Q2"):
    o = {"end": end, "val": val, "form": form, "fp": fp, "filed": "2026-08-06", "accn": "0009999901-26-000017"}
    if start:
        o["start"] = start
    return o


def companyfacts(fine="2026-06-30", inizio="2026-04-01", ricavi=4_817_284_000, utile=612_947_000,
                 eps=1.37, oper=803_116_000, attivo=21_463_552_000, valuta="USD", ytd=True):
    """companyfacts sintetico: trimestre (3 mesi) e, se ytd, il cumulato di 6 mesi con valori diversi."""
    def flusso(v, v_ytd):
        obs = [_ob(v, fine, inizio)]
        if ytd:
            obs.append(_ob(v_ytd, fine, "2026-01-01"))
        return obs
    return {"cik": int(CIK), "facts": {"us-gaap": {
        "Revenues": {"units": {valuta: flusso(ricavi, 9_402_771_000)}},
        "NetIncomeLoss": {"units": {valuta: flusso(utile, 1_177_309_000)}},
        "EarningsPerShareDiluted": {"units": {valuta + "/shares": flusso(eps, 2.63)}},
        "OperatingIncomeLoss": {"units": {valuta: flusso(oper, 1_544_918_000)}},
        "Assets": {"units": {valuta: [_ob(attivo, fine)]}},
    }}}


class Fonti:
    def __init__(self, cf=None, esef=None, forn=None, errore=None):
        self.cf, self.es, self.fo, self.errore = cf, esef, forn, errore
        self.chiamate = []

    def companyfacts(self, cik):
        self.chiamate.append(("companyfacts", cik))
        if self.errore:
            raise self.errore
        return self.cf

    def esef(self, lei):
        self.chiamate.append(("esef", lei))
        return self.es

    def fornitore(self, ticker):
        self.chiamate.append(("fornitore", ticker))
        return self.fo


FORNITORE = {"simbolo": TICKER, "valuta": "USD", "trimestrali": {
    "income": {"2026-06-30": {"Total Revenue": 4_817_284_000.0, "Net Income": 612_947_000.0,
                              "Diluted EPS": 1.37}},
    "balance": {"2026-06-30": {"Total Assets": 21_463_552_000.0}}}}


DOC_US = """ZZTest Corp reports second quarter 2026 results
Condensed consolidated statements of operations (in millions, except per share data)
Three months ended June 30, 2026
Total revenues 4,817.3 4,402.8
Operating income 803.1 711.4
Net income 612.9 540.2
Diluted EPS $1.37 $1.21
"""


def _r(testo, **kw):
    base = dict(ticker=TICKER, cik=CIK, periodo_fine="2026-06-30", periodo_inizio="2026-04-01")
    base.update(kw)
    return rn.riscontra(testo, **base)


def test_documento_vero_riscontrato_con_etichetta():
    fonti = Fonti(cf=companyfacts())
    r = _r(DOC_US, fonti=fonti)
    assert r["esito"] == "riscontrato", r
    assert r["fonte"] == "SEC XBRL companyfacts"
    assert r["etichetta"].startswith("verificato per riscontro numerico con SEC XBRL companyfacts (")
    assert "ricavi" in r["etichetta"] and "utile netto" in r["etichetta"]
    assert ("companyfacts", "0009999901") in fonti.chiamate     # CIK a 10 cifre
    voci = {v["voce"]: v for v in r["voci"]}
    assert voci["ricavi"]["valore_documento"] == pytest.approx(4_817_300_000)
    assert voci["ricavi"]["valore_fonte"] == 4_817_284_000
    assert voci["ricavi"]["scarto_pct"] < 0.01
    assert set(voci) == {"ricavi", "utile_netto", "eps_diluito", "risultato_operativo", "totale_attivo"} - {"totale_attivo"}
    for v in r["voci"]:
        assert set(v) >= {"voce", "valore_documento", "valore_fonte", "scarto_pct"}


def test_capogruppo_con_ricavi_diversi_e_diverso():
    doc = DOC_US.replace("4,817.3", "6,128.9").replace("612.9", "781.4")
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "diverso", r
    assert "ricavi" in r["motivo"] and "non attribuibile" in r["etichetta"]


def test_una_sola_voce_contraria_basta_per_diverso_anche_con_altre_coincidenti():
    doc = DOC_US.replace("803.1", "861.7")      # +7% sul risultato operativo
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "diverso"
    assert "risultato operativo" in r["motivo"]


def test_scarto_fra_mezzo_e_due_per_cento_non_conta_ne_blocca():
    doc = DOC_US.replace("803.1", "811.9")      # ~1,1%: incerto
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "riscontrato"
    oper = next(v for v in r["voci"] if v["voce"] == "risultato_operativo")
    assert oper["esito"] == "incerto"
    assert "risultato operativo" not in r["etichetta"]


def test_una_sola_voce_e_senza_confronto():
    doc = "Results (in millions)\nTotal revenues 4,817.3\nOther information only.\n"
    fonti = Fonti(cf=companyfacts())
    r = _r(doc, fonti=fonti)
    assert r["esito"] == "senza_confronto"
    assert "almeno 2" in r["motivo"]
    assert fonti.chiamate == []                  # con una voce non si chiama nessuna fonte


def test_una_sola_voce_confrontabile_nella_fonte_e_senza_confronto():
    cf = companyfacts()
    del cf["facts"]["us-gaap"]["NetIncomeLoss"], cf["facts"]["us-gaap"]["EarningsPerShareDiluted"]
    del cf["facts"]["us-gaap"]["OperatingIncomeLoss"]
    r = _r(DOC_US, fonti=Fonti(cf=cf), lei=None)
    assert r["esito"] == "senza_confronto"
    assert "1 voce/i coincidente/i su 1" in r["motivo"]


def test_scala_migliaia_gestita():
    doc = ("Consolidated statement of operations (in thousands of US$, except per share amounts)\n"
           "Three months ended June 30, 2026\nTotal revenues 4,817,284\nNet income 612,947\n")
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "riscontrato", r
    assert r["estratte"]["ricavi"]["valore"] == 4_817_284_000


def test_scala_sbagliata_milioni_su_numeri_in_migliaia_non_riscontra():
    doc = ("Statement of operations (in millions)\nThree months ended\nTotal revenues 4,817,284\n"
           "Net income 612,947\n")
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "diverso"


def test_scala_inline_miliardi_e_milioni():
    doc = ("ZZTest second quarter: revenue of $4.82 billion, net income of $612.9 million, "
           "diluted EPS of $1.37.\n")
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "riscontrato", r
    assert r["estratte"]["ricavi"]["valore"] == pytest.approx(4.82e9)


def test_scala_non_dichiarata_non_si_confronta():
    doc = "Second quarter\nTotal revenues 4,817.3\nNet income 612.9\n"
    fonti = Fonti(cf=companyfacts())
    r = _r(doc, fonti=fonti)
    assert r["esito"] == "senza_confronto"
    assert "scala non dichiarata" in r["motivo"]
    assert fonti.chiamate == []


def esef_cache(fine="2025-12-31", ricavi=4_817_284_000.0, utile=612_947_000.0, attivo=21_463_552_000.0,
               unita="EUR"):
    fy = int(fine[:4])
    return {"filings": {"zz-1": {"period_end": fine, "date_added": "2026-03-20", "facts": {
        "Revenue": [[fy, ricavi, unita], [fy - 1, 4_311_927_000.0, unita]],
        "ProfitLossAttributableToOwnersOfParent": [[fy, utile, unita]],
        "Assets": [[fy, attivo, unita]],
    }}}}


DOC_IT = """Gruppo ZZTest - Bilancio consolidato al 31 dicembre 2025
(milioni di euro)
Esercizio 2025
Ricavi 4.817,3 4.311,9
Utile netto di gruppo 612,9 540,2
Totale attivo 21.463,6 20.118,4
"""


def test_formato_europeo_con_esef_riscontrato():
    r = rn.riscontra(DOC_IT, ticker=TICKER, lei=LEI, periodo_fine="2025-12-31", fonti=Fonti(esef=esef_cache()))
    assert r["esito"] == "riscontrato", r
    assert r["fonte"] == "ESEF"
    assert r["valuta_documento"] == "EUR"
    assert r["estratte"]["ricavi"]["valore"] == pytest.approx(4_817_300_000)
    assert r["estratte"]["totale_attivo"]["valore"] == pytest.approx(21_463_600_000)
    assert "ricavi, utile netto, totale attivo" in r["etichetta"]


def test_esef_comparativo_dell_anno_prima_non_e_il_periodo():
    # il deposito del 2025 porta anche il 2024: un documento del 2024 non si confronta con QUEL deposito
    doc = DOC_IT.replace("4.817,3", "4.311,9")
    r = rn.riscontra(doc, ticker=TICKER, lei=LEI, periodo_fine="2024-12-31", fonti=Fonti(esef=esef_cache()))
    assert r["esito"] == "senza_confronto"
    assert "nessun dato della fonte per il periodo" in r["motivo"]


def test_esef_i_comparativi_dentro_il_deposito_non_sono_candidati():
    # stesso deposito (fine 2025), ma ricavi del documento = colonna dell'anno prima: non e' un riscontro
    doc = DOC_IT.replace("Ricavi 4.817,3 4.311,9", "Ricavi 4.311,9 4.817,3")
    r = rn.riscontra(doc, ticker=TICKER, lei=LEI, periodo_fine="2025-12-31", fonti=Fonti(esef=esef_cache()))
    assert r["esito"] == "diverso"
    assert "ricavi" in r["motivo"]


def test_periodo_diverso_non_confrontato():
    cf = companyfacts(fine="2026-03-31", inizio="2026-01-01", ytd=False)
    r = _r(DOC_US, fonti=Fonti(cf=cf))
    assert r["esito"] == "senza_confronto"
    assert "nessun dato della fonte per il periodo con fine 2026-06-30" in r["motivo"]


def test_durata_diversa_stessa_fine_non_confusa():
    # numeri del documento = cumulato di 6 mesi; il periodo dichiarato e' il trimestre
    doc = DOC_US.replace("4,817.3", "9,402.8").replace("612.9", "1,177.3").replace("803.1", "1,544.9")
    r = _r(doc, fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "diverso"
    r6 = _r(doc, periodo_inizio="2026-01-01", fonti=Fonti(cf=companyfacts()))
    # col semestre dichiarato ricavi, utile, risultato operativo coincidono (l'EPS 1.37 no: il cumulato e' 2.63)
    assert r6["esito"] == "diverso" and "EPS diluito" in r6["motivo"]
    doc6 = doc.replace("$1.37", "$2.63")
    assert _r(doc6, periodo_inizio="2026-01-01", fonti=Fonti(cf=companyfacts()))["esito"] == "riscontrato"


def test_fonte_assente_dichiarata():
    r = _r(DOC_US, fonti=Fonti(cf=None))
    assert r["esito"] == "senza_confronto"
    assert "companyfacts assente per CIK 0009999901" in r["motivo"]
    assert "yfinance (fornitore): fornitore senza risposta" in r["motivo"]
    r2 = rn.riscontra(DOC_US, ticker=TICKER, periodo_fine="2026-06-30", fonti={})
    assert r2["esito"] == "senza_confronto" and "fonte non fornita" in r2["motivo"]


def test_eccezione_della_fonte_non_esce_e_si_passa_alla_successiva():
    fonti = Fonti(errore=ConnectionError("rete giu"), esef=esef_cache(fine="2026-06-30", unita="USD"), forn=FORNITORE)
    r = _r(DOC_US, lei=LEI, fonti=fonti)
    assert r["esito"] == "riscontrato" and r["fonte"] == "yfinance (fornitore)"
    sec, esef = r["fonti_provate"][0], r["fonti_provate"][1]
    assert sec["fonte"] == "SEC XBRL companyfacts" and "ConnectionError" in sec["motivo"]
    # un trimestre non si confronta con l'esercizio ESEF: solo i saldi, che il documento non ha
    assert esef["fonte"] == "ESEF" and esef["esito"] == "senza_confronto"
    assert [c[0] for c in fonti.chiamate] == ["companyfacts", "esef", "fornitore"]


def test_eccezione_unica_fonte_senza_confronto_col_tipo():
    r = _r(DOC_US, fonti={"companyfacts": lambda cik: 1 / 0})
    assert r["esito"] == "senza_confronto"
    assert "ZeroDivisionError" in r["motivo"]


def test_tetto_di_tempo_della_fonte(monkeypatch):
    from bellomberg.market_data import freschezza_trimestrale as ft
    monkeypatch.setattr(ft, "TEMPO_TOOL_S", 0.2)
    t0 = time.monotonic()
    r = _r(DOC_US, fonti={"companyfacts": lambda cik: time.sleep(3) or companyfacts()})
    assert time.monotonic() - t0 < 2.0
    assert r["esito"] == "senza_confronto" and "tempo massimo" in r["motivo"]


def test_ingressi_rotti_non_sollevano():
    assert rn.riscontra(None, ticker=TICKER, periodo_fine="2026-06-30")["esito"] == "senza_confronto"
    r = rn.riscontra(DOC_US, ticker=TICKER, periodo_fine="non-una-data", fonti={})
    assert r["esito"] == "senza_confronto" and "periodo_fine" in r["motivo"]
    r = rn.riscontra(DOC_US, ticker=TICKER, periodo_fine=object(), fonti={})
    assert r["esito"] == "senza_confronto"
    # una fonte che risponde spazzatura
    r = _r(DOC_US, fonti={"companyfacts": lambda cik: {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"end": "2026-06-30", "start": "2026-04-01", "val": "x"}, None]}}}}}})
    assert r["esito"] == "senza_confronto"


def test_valuta_diversa_fonte_non_confrontata():
    r = _r(DOC_US, valuta="EUR", fonti=Fonti(cf=companyfacts()))
    assert r["esito"] == "senza_confronto"
    assert "valuta diversa (documento EUR, fonte USD)" in r["motivo"]
    # la valuta del testo basta: documento in euro contro una fonte in dollari
    doc_eur = DOC_US.replace("$", "€").replace("(in millions", "(EUR in millions")
    r2 = _r(doc_eur, fonti=Fonti(cf=companyfacts()))
    assert r2["esito"] == "senza_confronto" and "valuta diversa" in r2["motivo"]


def test_fornitore_etichettato_e_solo_trimestri():
    forn = FORNITORE
    r = rn.riscontra(DOC_US, ticker=TICKER, periodo_fine="2026-06-30", periodo_inizio="2026-04-01",
                     fonti=Fonti(forn=forn))
    assert r["esito"] == "riscontrato"
    assert r["etichetta"].startswith("verificato per riscontro numerico con yfinance (fornitore) (")
    # documento annuale: i trimestri del fornitore non sono il suo periodo
    r2 = rn.riscontra(DOC_US, ticker=TICKER, periodo_fine="2026-06-30", periodo_inizio="2025-07-01",
                      fonti=Fonti(forn=forn))
    assert r2["esito"] == "senza_confronto"


def test_tabella_html_e_perdita_tra_parentesi():
    cf = companyfacts(utile=-23_684_000)
    html = ("<html><body><p>Unaudited (in millions)</p><table><tr><td>Total revenues</td><td>4,817.3</td></tr>"
            "<tr><td>Net income (loss)</td><td>(23.7)</td></tr></table></body></html>")
    r = _r(html, fonti=Fonti(cf=cf))
    assert r["estratte"]["utile_netto"]["valore"] == pytest.approx(-23_700_000)
    assert r["esito"] == "riscontrato", r


def test_parse_numero_formati():
    assert rn.parse_numero("1,234.5", "inglese") == (1234.5, 1)
    assert rn.parse_numero("1.234,5", "europeo") == (1234.5, 1)
    assert rn.parse_numero("1.234", "europeo") == (1234.0, 0)
    assert rn.parse_numero("1,234", "inglese") == (1234.0, 0)
    assert rn.parse_numero("1,234", None) is None                 # ambiguo: non si indovina
    assert rn.parse_numero("1 234,56", None) == (1234.56, 2)
    assert rn.parse_numero("12,34,5", "inglese") is None


def test_date_anni_e_percentuali_non_sono_valori():
    doc = ("(in millions)\nRevenue for the quarter ended June 30, 2026 rose 7% to 4,817.3\n"
           "Net income in 2026 was 612.9\n")
    e = rn.estrai_voci(doc)["voci"]
    assert e["ricavi"]["valore"] == pytest.approx(4_817_300_000)
    assert e["utile_netto"]["valore"] == pytest.approx(612_900_000)


def test_costo_dei_ricavi_e_eps_non_sono_ricavi_e_utile():
    doc = ("(in millions, except per share)\nCost of revenues 2,904.6\nNet income per share 0.61\n"
           "Adjusted revenue 5,001.2\nTotal revenues 4,817.3\nNet income 612.9\n")
    e = rn.estrai_voci(doc)["voci"]
    assert e["ricavi"]["valore"] == pytest.approx(4_817_300_000)
    assert e["utile_netto"]["valore"] == pytest.approx(612_900_000)
