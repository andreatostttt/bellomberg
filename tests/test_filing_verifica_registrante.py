"""Seguito revisione G1 (rilievo 2): nei documenti inline-XBRL il CIK del markup lega il documento al
CIK del profilo, non il CIK al titolo del portafoglio. Per i collegamenti AUTOMATICI (ticker, nome) il
registrante (dei:EntityRegistrantName) deve avere un nome compatibile col titolo. Nomi inventati."""
import pytest

from bellomberg.market_data.filing_profili_auto import profilo_sec
from bellomberg.market_data.filing_verifica import verifica_documento
from tests.filing_sec_sintetici import CIK_NOVA, documento_10q

CAT = {"form": "10-Q", "report_date": "2026-09-30", "emittente_id": "CIK:" + CIK_NOVA}


def _doc(tmp_path, registrante=None):
    raw = documento_10q(2026, 3, rischio="Export rules may tighten. " * 80,
                        gestione="Revenue grew on data center demand. " * 60)
    if registrante:
        tag = f'<ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c1">{registrante}</ix:nonNumeric>'
        raw = raw.replace(b"</ix:hidden>", tag.encode() + b"</ix:hidden>", 1)
    path = tmp_path / "q.htm"
    path.write_bytes(raw)
    return path


def _profilo(nome, origine):
    return profilo_sec("ZZQ.L", cik=CIK_NOVA, sec_ticker="NOVA", nome=nome, origine=origine, forme={"10-Q"})


def test_registrante_di_un_altra_societa_non_passa(tmp_path):
    r = verifica_documento(_doc(tmp_path, "ACME INC."), url="https://www.sec.gov/x",
                           profilo=_profilo("ACME Holdings plc", "nome"), catalogo=CAT)
    assert r["stato"] == "non_verificato" and "registrante" in r["motivi"][0]


@pytest.mark.parametrize("origine", ["ticker", "nome"])
def test_registrante_compatibile_passa(tmp_path, origine):
    r = verifica_documento(_doc(tmp_path, "Nova Semiconductors, Inc."), url="https://www.sec.gov/x",
                           profilo=_profilo("Nova Semiconductors Inc.", origine), catalogo=CAT)
    assert r["stato"] == "ok", r["motivi"]


def test_collegamento_confermato_dall_utente_non_e_ricontrollato(tmp_path):
    r = verifica_documento(_doc(tmp_path, "ACME INC."), url="https://www.sec.gov/x",
                           profilo=_profilo("ACME Holdings plc", "confermato_utente"), catalogo=CAT)
    assert r["stato"] == "ok", r["motivi"]


def test_senza_registrante_vale_la_prova_testuale_del_nome(tmp_path):
    # Il documento sintetico stampa «Nova Semiconductors Inc.»: il titolo «ACME Holdings plc» non c'e'.
    r = verifica_documento(_doc(tmp_path), url="https://www.sec.gov/x",
                           profilo=_profilo("ACME Holdings plc", "nome"), catalogo=CAT)
    assert r["stato"] == "non_verificato" and "emittente" in r["motivi"][0]
    r = verifica_documento(_doc(tmp_path), url="https://www.sec.gov/x",
                           profilo=_profilo("Nova Semiconductors Inc.", "nome"), catalogo=CAT)
    assert r["stato"] == "ok", r["motivi"]
