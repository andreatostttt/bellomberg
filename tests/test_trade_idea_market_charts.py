"""Figure di mercato del memo Trade Idea (Lotto 3, L3, Opus 5.5).

Dati SINTETICI: statistiche scritte a mano qui (oracolo indipendente dal codice sotto
test) e la fixture tests/fixtures/market_pack_synth.json di L2 (ticker inventati ZZ*/QQ*).
Coprono: figura non disegnata se il blocco e' insufficient/unavailable, letture con frasi
esatte da valori a mano, verifica numero-per-numero delle letture contro le statistiche,
e il controllo d'integrita' del PDF che rilegge figure e letture.
"""
import copy
import json
import os
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from bellomberg.reporting import trade_idea_market_charts as mc
from bellomberg.reporting.trade_idea_market_charts import (MARKET_KEYS, build_market_charts, market_readings,
                                                           verify_readings)
from bellomberg.reporting.trade_idea_market_stats import market_stats

FIXTURE = Path(__file__).parent / "fixtures" / "market_pack_synth.json"


def _pack():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _beta_stats():
    return {"base_currency": "EUR",
            "beta": {"status": "ok", "beta": 1.2345, "correlation": 0.5, "n_obs": 1000,
                     "start": "2022-01-03", "end": "2026-09-30", "benchmark": "QQBENCH"},
            "rolling_beta": {"status": "ok", "window": 252, "benchmark": "QQBENCH",
                             "dates": ["2025-01-02", "2025-06-02", "2026-09-30"], "beta": [1.5, 0.9, 1.1],
                             "last": 1.1, "min": 0.9, "max": 1.5, "n_obs": 1000, "n_windows": 3}}


def _tails_stats():
    win = {"status": "ok", "start": "2021-10-05", "end": "2026-09-30"}
    return {"base_currency": "EUR",
            "moments": {**win, "n_obs": 1500,
                        "daily": {"n_obs": 1500, "excess_kurtosis": 3.456, "skew": -0.5, "beyond_3sd": 20,
                                  "beyond_3sd_normal_expected": 4.05, "jb_stat": 812.34, "jb_p": 0.0},
                        "weekly": {"excess_kurtosis": 0.8}, "monthly": {"excess_kurtosis": 0.25}},
            "histogram": {**win, "edges_pct": [-1.0, 0.0, 1.0, 2.0], "counts": [1, 2, 1],
                          "normal_mean_pct": 0.1, "normal_std_pct": 1.0, "n_obs": 4},
            "qq": {**win, "theoretical_z": [-1.0, 0.0, 1.0], "sample_pct": [-1.0, 0.0, 1.2],
                   "slope_pct": 1.0, "intercept_pct": 0.0, "n_obs": 3}}


def _by_key(items):
    return {item["key"]: item for item in items}


# ------------------------------------------------------------------ letture: frasi esatte da valori a mano
def test_rolling_beta_reading_is_computed_from_the_statistics(tmp_path):
    out = _by_key(build_market_charts(_beta_stats(), None, tmp_path, language="it", ticker="ZZCAND.MI"))
    item = out["rolling_beta"]
    assert item["notes"] == [
        "Beta giornaliero dell'intero periodo 1,23 su 1.000 rendimenti giornalieri comuni, correlazione 0,50.",
        "Beta mobile a 252 sedute: ultimo 1,10, minimo 0,90, massimo 1,50.",
        "Scarto dell'ultimo beta mobile dal beta dell'intero periodo: -0,13.",
    ]
    assert item["title"] == "ZZCAND: beta mobile contro QQBENCH"
    assert "su 252 sedute di rendimenti semplici giornalieri in EUR" in item["subtitle"]
    assert "1.000 rendimenti comuni, 03/01/2022–30/09/2026" in item["subtitle"]
    assert os.path.exists(item["path"]) and item["path"].endswith("ti_rolling_beta.png")
    assert verify_readings(_beta_stats(), [item], "it", "ZZCAND.MI") == []


def test_fat_tails_reading_in_both_languages(tmp_path):
    it = _by_key(build_market_charts(_tails_stats(), None, tmp_path / "it", language="it", ticker="ZZCAND.MI"))
    assert it["fat_tails"]["notes"] == [
        "Curtosi in eccesso 3,46 e asimmetria -0,50 su 1.500 rendimenti giornalieri: una normale darebbe zero per entrambe.",
        "Rendimenti oltre tre deviazioni standard dalla media: 20 contro 4,1 attesi con una normale.",
        "Jarque-Bera: statistica 812,3, p-value inferiore a 0,001: normalità respinta al 5%.",
        "Curtosi in eccesso dei rendimenti settimanali 0,80 e mensili 0,25.",
    ]
    en = _by_key(build_market_charts(_tails_stats(), None, tmp_path / "en", language="en", ticker="ZZCAND.MI"))
    assert en["fat_tails"]["notes"][0].startswith("Excess kurtosis 3.46 and skewness -0.50 over 1,500 daily returns")
    assert verify_readings(_tails_stats(), [it["fat_tails"]], "it") == []
    assert verify_readings(_tails_stats(), [en["fat_tails"]], "en") == []


# ------------------------------------------------------------------ figura non disegnata
def test_insufficient_block_draws_no_figure_and_removes_the_old_png(tmp_path):
    stats = _beta_stats()
    stats["vol_cone"] = {"status": "insufficient", "n_obs": 300, "years_observed": 1.2,
                         "reason": "Candidato: storia insufficiente (1.2 anni, 300 osservazioni; servono almeno 3 anni)"}
    stale = tmp_path / "ti_vol_cone.png"
    stale.write_bytes(b"vecchio")
    out = _by_key(build_market_charts(stats, None, tmp_path, language="it", ticker="ZZCAND.MI"))
    assert out["vol_cone"] == {"key": "vol_cone", "title": "ZZCAND: cono di volatilità",
                               "missing": stats["vol_cone"]["reason"]}
    assert not stale.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ti_rolling_beta.png"]


def test_unavailable_pack_gives_eight_declared_missing_figures(tmp_path):
    stats = market_stats({"version": 1, "status": "unavailable", "reason": "rete assente (sintetico)"})
    out = build_market_charts(stats, None, tmp_path, language="it", ticker="ZZCAND.MI")
    assert [item["key"] for item in out] == list(MARKET_KEYS)
    assert all("missing" in item and "path" not in item for item in out)
    assert all("rete assente (sintetico)" in item["missing"] for item in out[:-1])
    assert list(tmp_path.iterdir()) == []


def _multiples_stats():
    row = lambda tk, role, s, e, pt, pf, status="ok", reason=None: {
        "ticker": tk, "role": role, "ev_sales": s, "ev_ebitda": e, "pe_trailing": pt, "pe_forward": pf,
        "status": status, "reason": reason, "source": "sintetico", "as_of": "2026-09-30"}
    med = lambda v, n, x=0: {"value": v, "n": n, "n_excluded_non_positive_or_invalid": x}
    return {"multiples": {"status": "ok", "rows": [
        row("ZZCAND.MI", "candidate", 2.0, 10.0, 20.0, None),
        row("QQPEER1.PA", "peer", 1.0, 8.0, -5.0, 15.0),
        row("QQPEER9.DE", "peer", None, None, None, None, "error", "download fallito (sintetico)")],
        "peer_median": {"ev_sales": med(1.0, 1), "ev_ebitda": med(8.0, 1), "pe_trailing": med(None, 0, 1),
                        "pe_forward": med(15.0, 1)},
        "history": {"status": "unavailable"}}}


def test_multiples_is_a_table_with_candidate_bold_and_peer_median_last(tmp_path):
    stale = tmp_path / "ti_multiples.png"
    stale.write_bytes(b"vecchio")
    item = _by_key(build_market_charts(_multiples_stats(), None, tmp_path, language="it", ticker="ZZCAND.MI"))["multiples"]
    assert "path" not in item and not stale.exists()
    assert item["table"]["columns"] == ["Titolo", "EV/Sales", "EV/EBITDA", "P/E trailing", "P/E forward"]
    assert item["table"]["rows"] == [
        ["ZZCAND.MI", "2,0", "10,0", "20,0", "n.d."],
        ["QQPEER1.PA", "1,0", "8,0", "-5,0", "15,0"],
        ["QQPEER9.DE", "n.d.", "n.d.", "n.d.", "n.d."],
        ["Mediana dei peer", "1,0", "8,0", "n.d.", "15,0"],
        ["Peer nella mediana (esclusi)", "1 (0)", "1 (0)", "0 (1)", "1 (0)"]]
    assert item["table"]["bold_rows"] == [0, 3]
    assert "QQPEER9.DE: n.d., download fallito (sintetico)." in item["table"]["foot"]
    assert item["notes"] == [
        "EV/Sales 2,0 contro una mediana dei peer di 1,0 (1 peer): premio del 100,0%.",
        "EV/EBITDA 10,0 contro una mediana dei peer di 8,0 (1 peer): premio del 25,0%."]
    assert verify_readings(_multiples_stats(), [item], "it", "ZZCAND.MI") == []
    forged = copy.deepcopy(item)  # una cella scritta fuori dalle statistiche
    forged["table"]["rows"][1][1] = "1,1"
    assert verify_readings(_multiples_stats(), [forged], "it", "ZZCAND.MI")


# ------------------------------------------------------------------ verifica delle letture
def test_verify_readings_rejects_numbers_not_from_the_statistics(tmp_path):
    item = _by_key(build_market_charts(_beta_stats(), None, tmp_path, language="it", ticker="ZZCAND.MI"))["rolling_beta"]
    written = copy.deepcopy(item)
    written["notes"][1] = written["notes"][1].replace("massimo 1,50", "massimo 1,50 (picco 1,72)")
    assert any("senza controparte" in p for p in verify_readings(_beta_stats(), [written], "it", "ZZCAND.MI"))
    wrong = copy.deepcopy(item)
    wrong["notes"][0] = wrong["notes"][0].replace("1,23", "1,32")
    problems = verify_readings(_beta_stats(), [wrong], "it", "ZZCAND.MI")
    assert any("stampato 1.32 al posto di" in p for p in problems)
    finer = copy.deepcopy(item)  # piu' decimali di quelli dichiarati nel controllo: non e' la stessa cifra provata
    finer["notes"][0] = finer["notes"][0].replace("correlazione 0,50.", "correlazione 0,500.")  # ultimo numero: niente spostamenti
    assert verify_readings(_beta_stats(), [finer], "it", "ZZCAND.MI")
    moved = _beta_stats()
    moved["rolling_beta"]["last"] = 1.4  # le statistiche cambiano, la lettura no
    assert verify_readings(moved, [item], "it", "ZZCAND.MI")


def test_ticker_digits_are_not_numbers_of_the_reading(tmp_path):
    stats = _beta_stats()
    stats["beta"]["benchmark"] = stats["rolling_beta"]["benchmark"] = "QQ500X"
    item = _by_key(build_market_charts(stats, None, tmp_path, language="it", ticker="ZZ7CAND.MI"))["rolling_beta"]
    assert verify_readings(stats, [item], "it", "ZZ7CAND.MI") == []


def test_ticker_starting_with_digits_does_not_hide_a_number(tmp_path):
    """Un ticker «1000.HK» si toglie solo come occorrenza intera: un «1.000» o «1000» altrove resta da provare."""
    stats = _beta_stats()
    item = _by_key(build_market_charts(stats, None, tmp_path, language="it", ticker="1000.HK"))["rolling_beta"]
    assert verify_readings(stats, [item], "it", "1000.HK") == []
    forged = copy.deepcopy(item)
    forged["notes"].append("Volume medio 1000 azioni.")
    assert any("senza controparte" in p for p in verify_readings(stats, [forged], "it", "1000.HK"))


# ------------------------------------------------------------------ segno, posizione ed etichetta (review RV-L3)
def test_sign_flips_are_problems(tmp_path):
    pack = _pack()
    stats = market_stats(pack)
    item = _by_key(market_readings(stats, pack, ticker="ZZCAND.MI"))
    ph = copy.deepcopy(item["price_history"])
    ph["notes"] = [n.replace("+", "").replace("-", "+").replace("", "-") for n in ph["notes"]]
    problems = verify_readings(stats, [ph], "it", "ZZCAND.MI")
    assert sum("stampato" in p and "al posto di" in p for p in problems) >= 3, problems
    dd = copy.deepcopy(item["drawdown"])
    dd["notes"] = [n.replace("-", "") for n in dd["notes"]]
    assert verify_readings(stats, [dd], "it", "ZZCAND.MI")
    neg = copy.deepcopy(item["drawdown"])  # meno tipografico U+2212 letto come segno
    neg["notes"] = [n.replace("-", "−") for n in neg["notes"]]
    assert verify_readings(stats, [neg], "it", "ZZCAND.MI") == []


def test_min_max_swapped_values_are_problems(tmp_path):
    item = _by_key(build_market_charts(_beta_stats(), None, tmp_path, language="it", ticker="ZZCAND.MI"))["rolling_beta"]
    swapped = copy.deepcopy(item)
    swapped["notes"][1] = swapped["notes"][1].replace("minimo 0,90, massimo 1,50", "minimo 1,50, massimo 0,90")
    problems = verify_readings(_beta_stats(), [swapped], "it", "ZZCAND.MI")
    assert any("al posto di get(('rolling_beta', 'min')" in p for p in problems)
    assert any("al posto di get(('rolling_beta', 'max')" in p for p in problems)


def test_field_label_is_an_oracle_independent_of_the_sentence(tmp_path):
    """Il campo «max» stampato dopo «minimo» e' un problema anche se valore e segno tornano."""
    # un errore nel CODICE della frase: valore giusto per il campo, etichetta di un altro campo
    r = mc._Reading(_beta_stats(), "it")
    text = f"Beta mobile: massimo {r.num(('rolling_beta', 'min'), 2)}, minimo {r.num(('rolling_beta', 'max'), 2)}."
    item = {"key": "rolling_beta", "notes": [r.place(text, {"field": "notes", "index": 0})], "checks": r.checks}
    problems = verify_readings(_beta_stats(), [item], "it", "ZZCAND.MI")
    assert any("etichetta «minimo»" in p for p in problems) and any("etichetta «massimo»" in p for p in problems)
    assert not any("al posto di" in p for p in problems)  # valori e segni tornano: lo prende solo l'etichetta


def test_multiples_in_line_with_the_median_and_premium_word_checked(tmp_path):
    stats = _multiples_stats()
    stats["multiples"]["peer_median"]["ev_sales"]["value"] = 2.0
    item = _by_key(build_market_charts(stats, None, tmp_path, language="it", ticker="ZZCAND.MI"))["multiples"]
    assert item["notes"][0] == "EV/Sales 2,0 contro una mediana dei peer di 2,0 (1 peer): in linea con la mediana."
    assert verify_readings(stats, [item], "it", "ZZCAND.MI") == []
    word = copy.deepcopy(item)
    word["notes"][1] = word["notes"][1].replace("premio", "sconto")
    assert any("etichetta «premio»" in p for p in verify_readings(stats, [word], "it", "ZZCAND.MI"))


def test_historical_approximations_reach_the_memo_from_the_history_level(tmp_path):
    """Forma del pacchetto vero di L1: approssimazioni a livello storico, non nelle righe anno."""
    stats = _multiples_stats()
    stats["multiples"]["peer_median"]["pe_trailing"] = {"value": 30.0, "n": 1, "n_excluded_non_positive_or_invalid": 0}
    stats["multiples"]["rows"][1]["pe_trailing"] = 30.0
    stats["multiples"]["history"] = {"status": "ok", "approximations": ["EV senza debito a breve (sintetico)"],
                                     "years": [{"fy": 2024, "ev_sales": 1.1}]}
    item = _by_key(build_market_charts(stats, None, tmp_path, language="it", ticker="ZZCAND.MI"))["multiples"]
    assert "approssimazioni: EV senza debito a breve (sintetico)" in item["subtitle"]
    assert item["notes"][-1] == ("A fine esercizio 2024 il candidato quotava EV/Sales 1,1 (multipli storici con le "
                                 "approssimazioni dichiarate sopra la tabella).")
    assert verify_readings(stats, [item], "it", "ZZCAND.MI") == []


def test_weekly_beta_and_price_index_are_declared(tmp_path):
    pack = _pack()
    stats = market_stats(pack)
    item = _by_key(market_readings(stats, pack, ticker="ZZCAND.MI"))
    rb, ph = item["rolling_beta"], item["price_history"]
    if stats["beta"].get("frequency") == "weekly":
        assert rb["notes"][0].startswith("Beta settimanale dell'intero periodo")
        assert "settimane di rendimenti semplici settimanali" in rb["subtitle"]
    assert stats["series"]["benchmark"].get("index_type") == "price"
    assert "lo scarto favorisce il candidato, che include i dividendi" in ph["notes"][1]
    assert "benchmark: indice di prezzo, senza dividendi" in ph["subtitle"]


def test_fixture_pack_draws_eight_figures_with_proven_readings(tmp_path):
    pack = _pack()
    for language in ("it", "en"):
        stats = market_stats(pack, language=language)
        drawn = build_market_charts(stats, pack, tmp_path / language, language=language, ticker="ZZCAND.MI")
        assert [item["key"] for item in drawn] == list(MARKET_KEYS)
        assert not [item for item in drawn if "missing" in item]
        assert verify_readings(stats, drawn, language, "ZZCAND.MI") == []
        assert all(1 <= len(item["notes"]) <= 4 and item["checks"] for item in drawn)
        light = market_readings(stats, pack, language=language, ticker="ZZCAND.MI")
        assert [(x["key"], x["notes"]) for x in light] == [(x["key"], x["notes"]) for x in drawn]
    # dichiarazioni sui dati: finestra, osservazioni, valuta e conversione nelle etichette
    ph = _by_key(market_readings(market_stats(pack), pack, ticker="ZZCAND.MI"))["price_history"]
    assert "in EUR" in ph["subtitle"] and "osservazioni" in ph["subtitle"]
    assert "ZZSPX convertito da USD al cambio dello stesso giorno (EURUSD=X" in ph["subtitle"]
    notes = " ".join(ph["notes"])
    assert "QQPEER2.CO (storia insufficiente)" in notes and "QQPEER3.US (non disponibile)" in notes


# ------------------------------------------------------------------ renderer + controllo d'integrita'
def _v4():
    from test_trade_idea_report_v4 import v4_result, v4_run
    return v4_run(), v4_result()


def _pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def _flat(text):
    return "".join(text.split())


def test_memo_places_market_figures_and_rereads_them(tmp_path):
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    run, result = _v4()
    run["market_pack"] = _pack()
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "market.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    assert artifact["quality"]["content_integrity"] == "complete"
    flat = _flat(_pdf_text(artifact["path"]))
    risk = flat.find(_flat("Rischio di portafoglio"))
    assert risk > 0
    for title in ("ZZTEST: code dei rendimenti giornalieri", "ZZTEST: cono di volatilità", "ZZTEST: beta mobile contro ZZSPX"):
        assert flat.find(_flat(title)) > risk, title
    assert flat.find(_flat("Prezzo in base 100")) > flat.find(_flat("Posizionamento"))
    assert flat.find(_flat("ZZTEST: multipli contro i peer")) > flat.find(_flat("Valutazione"))
    assert _flat("Peer QQPEER2.CO: storia insufficiente") in flat


def test_memo_without_pack_declares_it_and_draws_nothing(tmp_path):
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    run, result = _v4()
    artifact = build_trade_idea_report(run, result, output_path=tmp_path / "nopack.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    flat = _flat(_pdf_text(artifact["path"]))
    assert _flat("Pacchetto dati di mercato non disponibile: la run non lo contiene.") in flat
    assert _flat("cono di volatilità") not in flat and _flat("Prezzo in base 100") not in flat


def test_legacy_memo_stays_frozen_even_with_a_pack(tmp_path):
    """Le figure di mercato appartengono al memo /4: l'uscita /2-/3 resta identica (test sha in _v4)."""
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from test_trade_idea_report_v2 import editorial_fixture
    run, result = editorial_fixture("favorable")
    run["execution_policy"] = "trade-idea-research/3"
    plain = build_trade_idea_report(run, result, output_path=tmp_path / "plain.pdf")
    run["market_pack"] = _pack()
    packed = build_trade_idea_report(run, result, output_path=tmp_path / "packed.pdf")
    assert _pdf_text(plain["path"]) == _pdf_text(packed["path"])
    assert "Pacchetto dati di mercato" not in _pdf_text(plain["path"])


def test_integrity_check_catches_a_reading_changed_after_the_statistics(tmp_path, monkeypatch):
    from bellomberg.reporting import trade_idea_report as report
    run, result = _v4()
    run["market_pack"] = _pack()
    real = report._market_exhibits

    def tampered(pack, chart_dir, language, ticker):
        drawn, missing, gaps = real(pack, chart_dir, language, ticker)
        for chart in drawn:
            if chart["key"] == "rolling_beta":
                first = re.search(r"\d+,\d+", chart["notes"][0]).group(0)  # primo numero della lettura, alterato
                chart["notes"] = [chart["notes"][0].replace(first, first[::-1].replace(",", "") + ",9", 1),
                                  *chart["notes"][1:]]
        return drawn, missing, gaps

    monkeypatch.setattr(report, "_market_exhibits", tampered)
    artifact = report.build_trade_idea_report(run, result, output_path=tmp_path / "tampered.pdf")
    assert artifact["status"] == "partial"
    assert "market.rolling_beta.reading" in artifact["quality"]["integrity_missing"]


def test_integrity_check_catches_a_dropped_multiples_row(tmp_path, monkeypatch):
    from bellomberg.reporting import trade_idea_report as report
    run, result = _v4()
    run["market_pack"] = _pack()
    real = report._market_exhibits

    def dropped(pack, chart_dir, language, ticker):
        drawn, missing, gaps = real(pack, chart_dir, language, ticker)
        for chart in drawn:
            if chart["key"] == "multiples":
                chart["table"]["rows"] = chart["table"]["rows"][:-2] + chart["table"]["rows"][-1:]
        return drawn, missing, gaps

    monkeypatch.setattr(report, "_market_exhibits", dropped)
    artifact = report.build_trade_idea_report(run, result, output_path=tmp_path / "dropped.pdf")
    assert artifact["status"] == "partial"
    assert "market.multiples.row.4" in artifact["quality"]["integrity_missing"]


def test_integrity_check_catches_a_number_not_from_the_statistics(tmp_path, monkeypatch):
    from bellomberg.reporting import trade_idea_report as report
    run, result = _v4()
    run["market_pack"] = _pack()
    real_op = mc._Reading.op

    def off_by_one(self, op, args, d=0, signed=False, pct=False):  # il codice stampa un numero diverso dal campo
        marker = real_op(self, op, args, d, signed, pct)
        if args == (("rolling_beta", "window"),):
            self.checks[-1]["text"] = str(int(self.checks[-1]["text"]) + 1)
        return marker

    monkeypatch.setattr(mc._Reading, "op", off_by_one)
    artifact = report.build_trade_idea_report(run, result, output_path=tmp_path / "offbyone.pdf")
    assert artifact["status"] == "partial"
    assert any(r.startswith("Numeri delle letture di mercato non riconciliati") and "rolling_beta" in r
               for r in artifact["quality"]["reasons"]), artifact["quality"]["reasons"]


# ------------------------------------------------------------------ titoli fissi delle sezioni /4 (PM 04/10)
def test_v4_section_titles_are_fixed_from_the_key_in_both_languages(tmp_path):
    from bellomberg.core.trade_idea_contract import DOSSIER_KEYS
    from bellomberg.reporting import trade_idea_report as report
    run, result = _v4()
    assert set(report._M_SECTION_TITLES) == set(DOSSIER_KEYS)
    for language in ("it", "en"):
        artifact = report.build_trade_idea_report(run, result, output_path=tmp_path / f"t{language}.pdf",
                                                  language=language)
        assert artifact["status"] == "ready", artifact["reason"]
        flat = _flat(_pdf_text(artifact["path"]))
        expected = ([("4.", "Sintesi e giudizio"), ("9.", "Scenari"), ("11.", "Catalizzatori"),
                     ("12.", "Posizionamento"), ("14.", "Decisione")] if language == "it" else
                    [("4.", "Executive summary and judgment"), ("11.", "Catalysts"), ("12.", "Positioning")])
        for number, title in expected:
            assert _flat(f"{number} {title}") in flat, (language, title)
    # il titolo del Capo (fixture: «Catalysts», «Positioning») non e' piu' l'intestazione in italiano
    flat_it = _flat(_pdf_text(tmp_path / "tit.pdf"))
    assert _flat("11. Catalysts") not in flat_it and _flat("12. Positioning") not in flat_it


def test_v4_key_without_fixed_title_keeps_the_capo_title_declared(tmp_path, monkeypatch):
    from bellomberg.reporting import trade_idea_report as report
    titles = dict(report._M_SECTION_TITLES)
    titles.pop("catalysts")
    monkeypatch.setattr(report, "_M_SECTION_TITLES", titles)
    run, result = _v4()
    next(s for s in result["dossier"] if s["key"] == "catalysts")["title"] = "Eventi in calendario"
    artifact = report.build_trade_idea_report(run, result, output_path=tmp_path / "fallback.pdf")
    assert artifact["status"] == "ready", artifact["reason"]
    flat = _flat(_pdf_text(artifact["path"]))
    assert _flat("11. Eventi in calendario") in flat
    assert _flat("Sezione «Eventi in calendario»: titolo scritto dal Capo, nessun titolo fisso per questa sezione") in flat
