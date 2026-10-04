"""Grafici del memo Trade Idea (impianto B): solo dati sintetici.

Le frasi attese nelle note sono calcolate A MANO dai numeri sintetici qui sotto
(oracolo nel test, non dal modulo sotto test).
"""
import os

import pytest

from bellomberg.reporting.trade_idea_charts import (
    KEYS, build_charts, fmt_number, operating_margins, period_label, return_colors, window_label)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
YEARS = [2020, 2021, 2022, 2023]


def _series(values):
    return {y: v * 1e6 for y, v in zip(YEARS, values)}


def _facts():
    return {
        "currency": "EUR",
        "history": {
            "years": YEARS, "unit": "EUR", "source": "Bilanci sintetici", "as_of": "2023-12-31",
            "series": {
                "revenue": _series([1000, 1200, 1100, 1400]),
                "operating_income": _series([50, 84, 77, 112]),
                "net_income": _series([30, 50, 45, 70]),
                "cfo": _series([120, 150, 160, 210]),
                "capex": _series([-60, -70, -80, -90]),
                "fcf": _series([60, 80, 80, 120]),
                "cash": _series([100, 110, 120, 130]),
                "long_term_debt": _series([400, 420, 500, 520]),
                "goodwill": _series([200, 200, 210, 260]),
                "equity": _series([500, 520, 560, 650]),
                "dividends_paid": _series([-20, -25, -30, -35]),
                "buyback": _series([0, -10, -10, -15]),
                "eps_diluted": {2020: 1.10, 2021: 1.40, 2023: 2.00},  # 2022 assente apposta
            },
        },
        "quote": {"price": 50.0, "date": "2026-01-02", "low_52w": 40.0, "high_52w": 62.0,
                  "source": "Prezzi sintetici"},
        "consensus": {"target_mean": 58.0, "target_median": 59.0, "target_high": 70.0,
                      "target_low": 45.0, "analysts": 9, "source": "Consensus sintetico",
                      "eps": [{"period": "2024", "value": 2.50}, {"period": "2025", "value": 3.00}],
                      "revenue": [{"period": "2024", "value": 1.5e9}, {"period": "2025", "value": 1.68e9}],
                      "recommendations": {"strong_buy": 4, "buy": 9, "hold": 3, "sell": 1, "strong_sell": 0},
                      "recommendations_prev": {"strong_buy": 3, "buy": 8, "hold": 5, "sell": 1, "strong_sell": 0}},
        "returns": {"window": "1y", "source": "Rendimenti sintetici", "assets": [
            {"ticker": "SYN", "label": "SYN", "return_pct": 25.0, "role": "candidate"},
            {"ticker": "HLD1", "label": "HLD1", "return_pct": 10.0, "role": "holding"},
            {"ticker": "IDX", "label": "Indice", "return_pct": 12.5, "role": "benchmark"},
            {"ticker": "P1", "label": "Peer", "return_pct": -5.0}]},
        "risk": {"candidate": {"vol_pct": 30.0, "beta": 1.2, "var99_1d_pct": -4.0, "max_drawdown_pct": -22.0,
                               "var99_1w_pct": -9.0, "es99_1w_pct": -11.7},
                 "book": {"vol_pct": 15.0, "beta": 0.9, "var99_1d_pct": -2.0, "max_drawdown_pct": -9.0}},
    }


def _is_png(path):
    with open(path, "rb") as fh:
        return fh.read(8) == PNG_MAGIC


@pytest.fixture(scope="module")
def charts_it(tmp_path_factory):
    out = tmp_path_factory.mktemp("ti_it")
    return {c["key"]: c for c in build_charts(_facts(), str(out), ticker="SYN.MI")}


# ------------------------------------------------------------------ struttura
def test_dodici_png_con_facts_completi(charts_it):
    assert list(charts_it) == list(KEYS) and len(KEYS) == 12
    for key, c in charts_it.items():
        assert "missing" not in c, (key, c.get("missing"))
        assert os.path.basename(c["path"]) == f"ti_{key}.png"
        assert _is_png(c["path"])
        assert c["title"] and c["subtitle"] and c["source"]
        assert 0.15 < c["aspect"] < 0.7  # impianto M: figure larghe; la fascia prezzo e' bassa
        assert 2 <= len(c["notes"]) <= 4, (key, c["notes"])
    assert charts_it["revenue_margin"]["title"] == "Ricavi e margine operativo, 2020-2023"
    assert charts_it["risk"]["title"].startswith("SYN ")
    assert charts_it["revenue_margin"]["source"] == "Bilanci sintetici, 31/12/2023"
    assert charts_it["price_targets"]["source"] == "Consensus sintetico · Prezzo Prezzi sintetici, 02/01/2026"
    assert charts_it["risk"]["source"] == "Calcoli quantitativi Bellomberg e rischio del portafoglio"
    assert charts_it["tail_risk"]["source"] == charts_it["risk"]["source"]


@pytest.mark.parametrize("key, mutate, reason_word", [
    ("revenue_margin", lambda f: f.update(history=None), "Storico di bilancio non disponibile"),
    ("cash", lambda f: f["history"]["series"].pop("fcf"), "flusso libero"),
    ("price_targets", lambda f: f["consensus"].update(target_median=None), "Manca il target mediano."),
    ("risk", lambda f: f["risk"]["book"].pop("beta"), "il beta del book"),
    ("balance", lambda f: f["history"]["series"].pop("goodwill"), "Nello storico manca l'avviamento."),
    ("margins", lambda f: f["history"]["series"].pop("net_income"), "l'utile netto"),
    ("eps_path", lambda f: f["consensus"].pop("eps"), "EPS attesi"),
    ("revenue_path", lambda f: f["consensus"].update(revenue=[{"period": "2024", "value": None}]), "ricavi attesi"),
    ("capital_allocation", lambda f: f["history"]["series"].pop("buyback"), "i buyback"),
    ("recommendations", lambda f: f["consensus"]["recommendations"].pop("hold"), "mancano Hold"),
    ("returns", lambda f: f["returns"]["assets"][0].update(role=None), "candidato"),
    ("tail_risk", lambda f: f["risk"]["candidate"].pop("es99_1w_pct"), "l'ES 99% a 1 settimana del candidato"),
])
def test_ogni_grafico_senza_dati_e_missing_e_nessun_file(tmp_path, key, mutate, reason_word):
    build_charts(_facts(), str(tmp_path))  # un PNG vecchio resta su disco...
    assert (tmp_path / f"ti_{key}.png").exists()
    facts = _facts()
    mutate(facts)
    item = {c["key"]: c for c in build_charts(facts, str(tmp_path))}[key]
    assert set(item) == {"key", "title", "missing"}
    assert reason_word in item["missing"]
    assert not (tmp_path / f"ti_{key}.png").exists()  # ...e viene tolto


def test_unita_diverse_fra_storico_e_consensus_e_missing(tmp_path):
    facts = _facts()
    facts["history"]["unit"] = "USD"
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert "USD" in out["eps_path"]["missing"] and "EUR" in out["eps_path"]["missing"]
    assert "missing" in out["revenue_path"]


def test_ricavi_un_solo_anno_e_missing(tmp_path):
    facts = _facts()
    facts["history"]["years"] = [2023]
    out = build_charts(facts, str(tmp_path))
    assert "missing" in out[0] and "missing" in out[1]


def test_frasi_missing_senza_nomi_di_campo(tmp_path):
    facts = _facts()
    facts["consensus"].update(target_low=None, target_high=None)
    facts["risk"] = None
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert out["price_targets"]["missing"] == "Mancano il target minimo e il target massimo."
    assert out["risk"]["missing"] == "Metriche di rischio non disponibili."
    for c in out.values():
        if "missing" in c:
            assert "_" not in c["missing"] and "facts" not in c["missing"], c["missing"]


def test_fonte_breve_con_data_italiana_e_niente_nomi_tecnici(tmp_path):
    facts = _facts()
    facts["history"]["source"] = "ESEF filings.xbrl.org"
    facts["consensus"]["source"] = "consensus_estimates.get_consensus"
    facts["quote"]["source"] = "yfinance"
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert out["cash"]["source"] == "Bilanci ESEF, 31/12/2023"
    assert out["price_targets"]["source"] == "Consensus · Prezzo Yahoo Finance, 02/01/2026"
    assert "_" not in out["recommendations"]["source"]


def test_lingua_non_supportata(tmp_path):
    with pytest.raises(ValueError):
        build_charts(_facts(), str(tmp_path), language="de")


# ------------------------------------------------------------------ calcoli
def test_margine_solo_dove_ci_sono_entrambi():
    facts = _facts()
    facts["history"]["series"]["operating_income"].pop(2021)
    facts["history"]["series"]["revenue"].pop(2023)
    m = operating_margins(facts["history"])
    assert set(m) == {2020, 2022}
    assert m[2020] == pytest.approx(5.0)
    assert m[2022] == pytest.approx(7.0)


def test_margine_con_chiavi_stringa_da_json():
    history = {"years": [2022, 2023], "series": {
        "revenue": {"2022": 200.0, "2023": 0.0}, "operating_income": {"2022": 20.0, "2023": 5.0}}}
    assert operating_margins(history) == {2022: pytest.approx(10.0)}  # ricavi 0: niente margine


def test_formattazione_it_en():
    assert fmt_number(12345.678, 2, "it") == "12.345,68"
    assert fmt_number(12345.678, 2, "en") == "12,345.68"
    assert fmt_number(-1234, 0, "it") == "-1.234"
    assert fmt_number(-0.04, 1, "it") == "0,0"
    with pytest.raises(ValueError):
        fmt_number(1, 0, "fr")


def test_fcf_negativo_non_rompe(tmp_path):
    facts = _facts()
    facts["history"]["series"]["fcf"] = _series([-40, -15, 20, 35])
    facts["history"]["series"]["operating_income"] = _series([-30, 10, 20, 40])
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path), language="en")}
    for key in ("revenue_margin", "cash", "margins"):
        assert "missing" not in out[key] and _is_png(out[key]["path"])
    assert "absolute" in out["cash"]["subtitle"]  # capex negativo nei facts: dichiarato
    assert "Free cash flow is negative in 2020 and 2021." in out["cash"]["notes"]


# ------------------------------------------------------------------ note: numeri giusti
def test_note_ricavi(charts_it):
    n = charts_it["revenue_margin"]["notes"]
    assert n[0] == ("I ricavi passano da 1.000 a 1.400 milioni di EUR tra il 2020 e il 2023 "
                    "(+40,0%, media annua composta +11,9%).")
    assert n[1] == "Il 2022 è l'unico esercizio in calo (-8,3%)."
    assert n[2] == "Il margine operativo passa da 5,0% a 8,0% tra il 2020 e il 2023 (+3,0 punti percentuali)."


def test_note_cassa(charts_it):
    n = charts_it["cash"]["notes"]
    assert n[0] == "Il flusso libero passa da 60 a 120 milioni di EUR tra il 2020 e il 2023 (+100,0%)."
    assert n[1] == "Il flusso libero è positivo in tutti i 4 esercizi."
    assert "47,4%" in n[2]


def test_note_prezzo(charts_it):
    n = charts_it["price_targets"]["notes"]
    assert "implica +16,0%" in n[0] and "(59) +18,0%" in n[0]
    assert n[1] == "Il prezzo è 11,1% sopra il target minimo (45) e 28,6% sotto il massimo (70)."
    assert "al 45%" in n[2] and "19,4% sotto il massimo" in n[2]
    assert n[3].startswith("Target di 9 analisti") and "il 43%" in n[3]


def test_note_rischio(charts_it):
    n = charts_it["risk"]["notes"]
    assert n[0] == "Volatilità annua: 30,0% per SYN contro 15,0% del book (2,0 volte)."
    assert n[1] == "Beta: 1,20 contro 0,90 del book."
    assert "4,0% contro 2,0%" in n[2]
    assert "22,0% contro 9,0% del book (2,4 volte)" in n[3]


def test_note_stato_patrimoniale(charts_it):
    n = charts_it["balance"]["notes"]
    assert n[0] == ("Nel 2023: cassa 130, debito a lungo termine 520, avviamento 260, "
                    "patrimonio netto 650 milioni di EUR.")
    assert "da 400 a 520" in n[1] and "+30,0%" in n[1]
    assert "il 25,0% del debito" in n[2]
    assert "il 40,0% del patrimonio" in n[3]


def test_note_margini(charts_it):
    n = charts_it["margins"]["notes"]
    assert "da 5,0% a 8,0%" in n[0] and "+3,0 punti" in n[0]
    assert "da 3,0% a 5,0%" in n[1] and "+2,0 punti" in n[1]
    assert "in media 7,1%, minimo 6,0% nel 2020" in n[2]


def test_note_eps(charts_it):
    n = charts_it["eps_path"]["notes"]
    assert n[0] == "L'EPS diluito passa da 1,10 a 2,00 EUR tra il 2020 e il 2023 (+81,8%, media annua composta +22,1%)."
    assert "2022" in n[1]  # buco nello storico dichiarato
    assert n[2] == "2024 (stima): 2,50 EUR, +25,0% rispetto all'ultimo esercizio storico (2023)."
    assert n[3] == "2025 (stima): 3,00 EUR, +20,0% rispetto alla stima precedente."


def test_note_ricavi_storico_e_stime(charts_it):
    n = charts_it["revenue_path"]["notes"]
    assert n[0].startswith("I ricavi passano da 1,0 a 1,4 miliardi di EUR")
    assert n[1] == "2024 (stima): 1,5 miliardi di EUR, +7,1% rispetto all'ultimo esercizio storico (2023)."
    assert n[2] == "2025 (stima): 1,7 miliardi di EUR, +12,0% rispetto alla stima precedente."


def test_note_allocazione_capitale(charts_it):
    n = charts_it["capital_allocation"]["notes"]
    assert "flusso operativo cumulato è 640 milioni di EUR" in n[0]
    assert "300, il 46,9%" in n[1]
    assert "insieme 145, il 42,6%" in n[2] and "(340)" in n[2]
    assert n[3] == "Buyback pari a zero nel 2020."


def test_note_raccomandazioni(charts_it):
    n = charts_it["recommendations"]["notes"]
    assert n[0] == ("17 raccomandazioni in tutto: positive 13 (76,5%), neutrali 3 (17,6%), "
                    "negative 1 (5,9%).")
    assert n[1] == "Classe più frequente: Buy (9)."
    assert "11 su 17 (64,7%)" in n[2]


def test_note_rendimenti(charts_it):
    n = charts_it["returns"]["notes"]
    assert charts_it["returns"]["title"] == "Rendimento su 1 anno"
    assert n[0] == "Su 1 anno SYN rende +25,0%, 1° su 4."
    assert n[1] == "Migliore: SYN (+25,0%); peggiore: Peer (-5,0%)."
    assert n[2] == "Scarto contro Indice, benchmark: +12,5 punti percentuali."
    assert n[3] == "Scarto contro HLD1, posizione del book: +15,0 punti percentuali."


def test_ruolo_book_vecchio_nome_ancora_accettato(tmp_path):
    facts = _facts()
    facts["returns"]["assets"][1]["role"] = "book"
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert "Scarto contro HLD1, posizione del book: +15,0 punti percentuali." in out["returns"]["notes"]


def test_colori_rendimenti_funzione_pura():
    assets = [{"role": "holding"}, {"role": "candidate"}, {"role": "benchmark"}, {"role": None}, {"role": "book"}]
    assert return_colors(assets) == ["#7F7F7F", "#1F3864", "#7F7F7F", "#D0D0D0", "#7F7F7F"]


def test_colore_candidato_nel_png(charts_it):
    """Cablaggio: nel PNG la barra navy e' la piu' alta (il candidato ha il rendimento migliore)."""
    import matplotlib.image as mpimg
    import numpy as np
    img = mpimg.imread(charts_it["returns"]["path"])[:, :, :3]

    def top_row(rgb):
        mask = np.all(np.abs(img - np.array(rgb) / 255.0) < 0.03, axis=2)
        rows = np.where(mask.any(axis=1))[0]
        return rows.min() if len(rows) else None

    navy, dark, light = top_row((0x1F, 0x38, 0x64)), top_row((0x7F, 0x7F, 0x7F)), top_row((0xD0, 0xD0, 0xD0))
    assert navy is not None and dark is not None and light is not None
    assert navy < dark and navy < light
    assert top_row((0xED, 0x7D, 0x31)) is None  # impianto M: niente arancio


def test_stime_trimestrali_mai_mescolate_con_gli_anni(tmp_path):
    facts = _facts()
    facts["consensus"]["revenue"] = [{"period": "0q", "value": 0.4e9}, {"period": "+1q", "value": 0.42e9},
                                     {"period": "0y", "value": 1.5e9}, {"period": "+1y", "value": 1.68e9}]
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    n = out["revenue_path"]["notes"]
    assert n[1] == "Anno in corso (stima): 1,5 miliardi di EUR, +7,1% rispetto all'ultimo esercizio storico (2023)."
    assert n[2] == "Anno prossimo (stima): 1,7 miliardi di EUR, +12,0% rispetto alla stima precedente."
    assert n[-1] == "Le stime trimestrali sono nella tabella del consensus."
    assert not any("0,4" in x or "trimestre in corso" in x for x in n)


def test_solo_stime_trimestrali_e_missing(tmp_path):
    facts = _facts()
    facts["consensus"]["eps"] = [{"period": "0q", "value": 0.6}, {"period": "+1q", "value": 0.7}]
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert "trimestrali" in out["eps_path"]["missing"]
    assert not (tmp_path / "ti_eps_path.png").exists()


def test_period_label_e_window_label():
    assert period_label("0y", "it") == "anno in corso" and period_label("0y", "en") == "current year"
    assert period_label("+1y", "it") == "anno prossimo" and period_label("+2y", "it") == "tra due anni"
    assert period_label("0q", "it") == "trimestre in corso" and period_label("+1q", "en") == "next quarter"
    assert period_label("2027", "it") == "2027"
    assert window_label("1y", "it") == "1 anno" and window_label("1y", "en") == "1 year"
    assert window_label("6mo", "it") == "6 mesi" and window_label("3mo", "en") == "3 months"
    assert window_label("12 mesi", "it") == "12 mesi"


def test_range_52_settimane_opzionale(tmp_path):
    facts = _facts()
    facts["quote"].update(low_52w=None, high_52w=None)
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    pt = out["price_targets"]
    assert "missing" not in pt and _is_png(pt["path"])
    assert pt["subtitle"].endswith("range 52 settimane non disponibile")
    assert not any("52 settimane" in x for x in pt["notes"])
    facts["quote"]["price"] = None
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path))}
    assert out["price_targets"]["missing"] == "Manca il prezzo."


def test_beta_su_basi_diverse_non_confrontato(tmp_path):
    facts = _facts()
    facts["risk"]["candidate"]["beta_basis"] = "vs indice locale"
    facts["risk"]["book"]["beta_basis"] = "vs indice USA"
    out = {c["key"]: c for c in build_charts(facts, str(tmp_path), ticker="SYN")}
    beta = out["risk"]["notes"][1]
    assert "basi diverse, non confrontabili direttamente" in beta and "contro" not in beta


def test_arrotondamento_commerciale():
    assert fmt_number(2.675, 2, "it") == "2,68"
    assert fmt_number(2.675, 2, "en") == "2.68"
    assert fmt_number(-2.675, 2, "it") == "-2,68"
    assert fmt_number(0.5, 0, "it") == "1"
    assert fmt_number(-0.004, 2, "it") == "0,00"


def test_note_rischio_di_coda(charts_it):
    n = charts_it["tail_risk"]["notes"]
    assert "4,0% in un giorno e 9,0% in una settimana" in n[0]
    assert "2,25 volte" in n[1]
    assert n[2] == ("L'ES 99% a una settimana (perdita media oltre il VaR) vale 11,7%, "
                    "1,30 volte il VaR 99% a una settimana.")


def test_note_in_inglese_con_formato_inglese(tmp_path):
    out = {c["key"]: c for c in build_charts(_facts(), str(tmp_path), language="en", ticker="SYN")}
    assert out["revenue_margin"]["notes"][0] == (
        "Revenue moves from 1,000 to 1,400 EUR mn between 2020 and 2023 (+40.0%, compound annual +11.9%).")
    assert out["price_targets"]["notes"][1] == (
        "The price is 11.1% above the lowest target (45) and 28.6% below the highest (70).")
