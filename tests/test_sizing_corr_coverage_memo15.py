"""Copertura delle controparti: book e matrice sintetici, nessun provider."""
import copy

import pytest

from bellomberg.portfolio import sizing_engine as se


def case(n=9, matrix_n=8):
    names = [f"SYN{i}" for i in range(n)]
    book = {"positions": [{"ticker": t, "valore_mercato_eur": 1000} for t in names],
            "cash_disponibile_eur": 2000}
    ticks = names[:matrix_n]
    risk = {"correlation": {"tickers": ticks,
            "matrix": [[1 if i == j else .2 for j in range(len(ticks))] for i in range(len(ticks))]}}
    return book, risk


def run(book, risk, candidates=None):
    return se.compute_sizing(book, risk, candidates=candidates,
                            negozio=se.cl.carica_veicoli(se.cl.ESEMPIO_VEICOLI),
                            parametri=se._params_di_esempio())


def test_nine_names_top_eight_exposes_partial_without_changing_coefficients():
    out = run(*case())
    p, absent = out["positions"][0], out["positions"][8]
    assert p["avg_corr_book"] == .2 and p["corr_estimated"] is False
    assert p["corr_multiplier"] == 1.05
    assert absent["avg_corr_book"] == .5 and absent["corr_estimated"] is True
    c = p["corr_coverage"]
    assert (c["observed_counterparties"], c["expected_counterparties"], c["status"]) == (7, 8, "PARTIAL")
    assert c["missing_tickers"] == ["SYN8"]
    assert absent["corr_coverage"]["status"] == "UNAVAILABLE"
    text = se.format_for_capo(out)
    assert "corr copertura 7/8 PARZIALE" in text
    assert "corr copertura 0/8 n.d." in text


def test_full_matrix_and_foreign_matrix_names_do_not_expand_denominator():
    book, risk = case(3, 3)
    risk["correlation"]["tickers"].append("NOT_IN_BOOK")
    for row in risk["correlation"]["matrix"]:
        row.append(.99)
    risk["correlation"]["matrix"].append([.99, .99, .99, 1])
    c = run(book, risk)["positions"][0]["corr_coverage"]
    assert (c["observed_counterparties"], c["expected_counterparties"], c["status"]) == (2, 2, "COMPLETE")


@pytest.mark.parametrize("risk", [None, {}, {"correlation": {"tickers": ["SYN0"], "matrix": []}}, {"correlation": ["invalid"]}])
def test_missing_matrix_is_declared(risk):
    book, _ = case(2)
    c = run(book, risk)["positions"][0]["corr_coverage"]
    assert c["status"] == "UNAVAILABLE"
    assert c["missing_tickers"] == ["SYN1"]
    assert c["basis_status"] == "UNVERIFIED"


def test_single_position_has_no_counterparties_not_zero_percent():
    out = run(*case(1))
    c = out["positions"][0]["corr_coverage"]
    assert c["status"] == "NOT_APPLICABLE"
    assert c["expected_counterparties"] == 0
    assert "0/0" not in se.format_for_capo(out)


def test_missing_ticker_is_unmapped_counterparty():
    _, risk = case(2)
    # Il book canonico ha ticker; questo esercita il conteggio difensivo senza
    # attribuire a compute_sizing un nuovo contratto per righe non canoniche.
    c = se._build_corr_lookup(risk).coverage("SYN0", [None])
    assert c["expected_counterparties"] == 1
    assert c["unmapped_counterparties"] == 1
    assert c["status"] == "UNAVAILABLE"


def test_scalar_candidate_cannot_claim_verified_book_coverage():
    out = run(*case(), candidates=[{"ticker": "IDEA", "avg_corr": .2}])
    c = out["candidates"][0]
    assert c["avg_corr_assumed"] == .2
    assert c["corr_coverage"]["status"] == "UNVERIFIED"
    assert c["corr_coverage"]["observed_counterparties"] is None
    assert "corr copertura NON VERIFICATA" in se.format_for_capo(out)


def test_candidate_already_in_book_does_not_count_itself():
    out = run(*case(), candidates=[{"ticker": "SYN0", "avg_corr": .2}])
    assert out["candidates"][0]["corr_coverage"]["expected_counterparties"] == 8


def test_observation_metadata_is_preserved_without_inventing_currency_basis():
    book, risk = case(2)
    risk["correlation"]["meta"] = {"obs": 42, "estimator": "synthetic"}
    before = copy.deepcopy(risk)
    c = run(book, risk)["positions"][0]["corr_coverage"]
    assert c["matrix_meta"] == before["correlation"]["meta"]
    assert c["basis_status"] == "UNVERIFIED"
    assert "currency" not in c
    assert risk == before


@pytest.mark.parametrize("value", [float('nan'), float('inf'), float('-inf'), True, False])
def test_invalid_cells_are_visible_without_changing_coverage(value):
    book, risk = case(2, 2)
    risk['correlation']['matrix'][0][1] = value
    out = run(book, risk)
    c = out['positions'][0]['corr_coverage']
    assert c['status'] == 'COMPLETE'
    assert c['observed_counterparties'] == 1
    assert c['value_quality'] == 'INVALID'
    assert c['invalid_tickers'] == ['SYN1']
    assert 'valori correlazione NON VALIDI: SYN1' in se.format_for_capo(out)


@pytest.mark.parametrize("value", [0., -.2, .5])
def test_finite_cells_are_valid(value):
    book, risk = case(2, 2)
    risk['correlation']['matrix'][0][1] = value
    c = run(book, risk)['positions'][0]['corr_coverage']
    assert c['value_quality'] == 'VALID'
    assert c['invalid_tickers'] == []


@pytest.mark.parametrize("value,quality", [(float('nan'),'INVALID'), (float('inf'),'INVALID'), (True,'INVALID'), (False,'INVALID'), (0.,'UNVERIFIED'), (-.2,'UNVERIFIED')])
def test_candidate_scalar_quality_is_not_observed_quality(value, quality):
    out = run(*case(), candidates=[{'ticker':'IDEA','avg_corr':value}])
    c = out['candidates'][0]['corr_coverage']
    assert c['status'] == 'UNVERIFIED'
    assert c['value_quality'] == quality
    if quality == 'INVALID':
        assert 'valori correlazione NON VALIDI: IDEA' in se.format_for_capo(out)
