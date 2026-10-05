"""REV_G2a R-4: cache companyfacts condivisa fra numeri chiave e DCF. Scrittura atomica, solo
risposte valide, eta' dichiarata al DCF; se la rilettura forzata fallisce il DCF sa che la cache
e' anteriore a un deposito gia' pubblicato. CIK e numeri inventati, nessuna rete."""
import json
import os
import time

import pytest

from bellomberg.market_data import sec_edgar, sec_xbrl
from bellomberg.market_data.filing_numeri import numeri_per_coppia

CIK = "0009990001"
PRIMA, DOPO = ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-12-31")


def _fy(anno, val):
    return {"start": f"{anno}-01-01", "end": f"{anno}-12-31", "val": val, "form": "10-K", "fp": "FY"}


FATTI = {"cik": 9990001, "entityName": "ACME CORP", "facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [_fy(2024, 90.0), _fy(2025, 100.0)]}}}}}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_xbrl, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sec_xbrl, "_cache_path", lambda cik: str(tmp_path / f"CIK{cik}.json"))
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda **k: None)
    monkeypatch.setattr(sec_edgar, "lookup_cik", lambda t, motivo=None: CIK)
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    return tmp_path


def _rete(monkeypatch, risposta):
    import requests

    class R:
        status_code = 200 if risposta is not None else 503

        def json(self):
            return risposta
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())


def test_scrittura_atomica_la_copia_buona_sopravvive_a_un_guasto(cache, monkeypatch):
    p = cache / f"CIK{CIK}.json"
    p.write_text(json.dumps(FATTI))
    os.utime(p, (0, 0))  # scaduta: si rilegge
    _rete(monkeypatch, {**FATTI, "entityName": "ACME NUOVA"})

    def dump_rotto(dati, fh, **k):
        fh.write('{"cik": 99')
        raise OSError("disco pieno")
    monkeypatch.setattr(sec_xbrl.json, "dump", dump_rotto)
    sec_xbrl._fetch_companyfacts(CIK)
    assert json.loads(p.read_text()) == FATTI  # mai un JSON troncato al posto della copia buona
    assert not list(cache.glob("*.tmp"))


def test_risposta_senza_facts_non_entra_in_cache(cache, monkeypatch):
    _rete(monkeypatch, {"message": "not found"})
    assert sec_xbrl._fetch_companyfacts(CIK) is None
    assert not (cache / f"CIK{CIK}.json").exists()


def test_eta_della_cache_dichiarata_al_dcf(cache, monkeypatch):
    (cache / f"CIK{CIK}.json").write_text(json.dumps(FATTI))
    _rete(monkeypatch, None)
    out = sec_xbrl.get_financial_history("ACME")
    assert out["da_cache"] is True and out["companyfacts_letto_il"][:4].isdigit()


def test_rilettura_forzata_fallita_il_dcf_sa_che_la_cache_e_anteriore(cache, monkeypatch):
    (cache / f"CIK{CIK}.json").write_text(json.dumps(FATTI))
    _rete(monkeypatch, None)  # SEC giu'
    coppia = {"prima": {"metadati": {"periodo_inizio": PRIMA[0], "periodo_fine": PRIMA[1]}},
              "dopo": {"metadati": {"periodo_inizio": DOPO[0], "periodo_fine": DOPO[1]}}}
    assert numeri_per_coppia(CIK, coppia)["stato"] == "non_aggiornato"
    storico = sec_xbrl.get_financial_history("ACME")
    assert "anteriore" in (storico.get("index_note") or "") and DOPO[1] in storico["index_note"]
    # una rilettura riuscita toglie la nota
    _rete(monkeypatch, {**FATTI, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        _fy(2025, 100.0), _fy(2026, 110.0)]}}}}})
    assert numeri_per_coppia(CIK, coppia)["stato"] == "ok"
    assert not sec_xbrl.get_financial_history("ACME").get("index_note")
