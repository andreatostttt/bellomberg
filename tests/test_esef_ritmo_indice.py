"""Ritmo condiviso e cache dell'indice per filings.xbrl.org (fase B, task 1). Nessuna rete."""
import json
import multiprocessing as mp
import os
import time

import pytest

from bellomberg.market_data import esef, lettore_trimestrali

LEI = "999900NOVA0000000001"


def _batti(percorso, n, coda):
    # Istante SCRITTO nel file, letto dentro il lock: time.time() dopo il ritorno misurava anche
    # lo scheduler (rosso intermittente sotto carico, revisione 04/10).
    import os
    from bellomberg.market_data import sec_edgar
    sblocca = sec_edgar._sblocca

    def leggi_e_sblocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        coda.put(float(os.read(fd, sec_edgar._RITMO_LARGHEZZA).decode("ascii").strip()))
        sblocca(fd)
    sec_edgar._sblocca = leggi_e_sblocca
    for _ in range(n):
        esef.attendi_esef(percorso=percorso)


def test_ritmo_esef_condiviso_tra_due_processi(tmp_path):
    percorso = tmp_path / ".esef_ritmo"
    ctx = mp.get_context("spawn")
    coda = ctx.Queue()
    procs = [ctx.Process(target=_batti, args=(percorso, 2, coda)) for _ in range(2)]
    for p in procs:
        p.start()
    istanti = sorted(coda.get(timeout=30) for _ in range(4))
    for p in procs:
        p.join(30)
    gap = [b - a for a, b in zip(istanti, istanti[1:])]
    assert min(gap) >= esef._ESEF_INTERVALLO_S - 0.001


def test_ritmo_esef_file_distinto_da_quello_sec(tmp_path, monkeypatch):
    from bellomberg.core import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    assert esef._ritmo_path_predefinito() == tmp_path / ".esef_ritmo"


def _righe(*ids):
    return [{"id": i, "period_end": f"202{i}-12-31", "json_url": f"https://filings.xbrl.org/x/{i}.json"} for i in ids]


def test_indice_cache_fresca_senza_rete(tmp_path):
    chiamate = []

    def fetch(lei):
        chiamate.append(lei)
        return _righe(4, 5)

    a = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=fetch)
    b = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=fetch)
    assert chiamate == [LEI]
    assert a["origine"] == "rete" and b["origine"] == "cache"
    assert [r["id"] for r in b["righe"]] == [4, 5]


def test_indice_cache_scaduta_si_rilegge(tmp_path):
    esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: _righe(4))
    p = tmp_path / f"indice_{LEI}.json"
    vecchio = time.time() - 7 * 3600
    os.utime(p, (vecchio, vecchio))
    r = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: _righe(4, 5))
    assert r["origine"] == "rete" and len(r["righe"]) == 2


def test_indice_cache_corrotta_si_rilegge_subito(tmp_path):
    p = tmp_path / f"indice_{LEI}.json"
    p.write_text("{non json")
    r = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: _righe(5))
    assert r["origine"] == "rete" and [x["id"] for x in r["righe"]] == [5]
    assert json.loads(p.read_text())["righe"][0]["id"] == 5


def test_indice_rete_giu_con_cache_vecchia_dichiarata(tmp_path):
    esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: _righe(4))
    p = tmp_path / f"indice_{LEI}.json"
    vecchio = time.time() - 30 * 3600
    os.utime(p, (vecchio, vecchio))

    def giu(lei):
        raise ConnectionError("rete giu")

    r = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=giu)
    assert r["origine"] == "cache_scaduta" and "ConnectionError" in r["motivo"]
    assert [x["id"] for x in r["righe"]] == [4]


def test_indice_senza_rete_ne_cache_solleva(tmp_path):
    def giu(lei):
        raise ConnectionError("rete giu")

    with pytest.raises(RuntimeError, match="indice ESEF"):
        esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=giu)


def test_indice_lei_non_valido_rifiutato(tmp_path):
    with pytest.raises(ValueError):
        esef.indice_depositi("../x", cache_dir=tmp_path, fetch=lambda lei: [])


def test_list_filings_con_ritmo_chiama_attendi_a_ogni_pagina(monkeypatch):
    battiti = []
    pagine = {
        f"https://filings.xbrl.org/api/entities/{LEI}/filings": {
            "data": [{"id": "1", "attributes": {"period_end": "2024-12-31", "json_url": "/a.json"}}],
            "links": {"next": f"https://filings.xbrl.org/api/entities/{LEI}/filings?page[number]=2"}},
        f"https://filings.xbrl.org/api/entities/{LEI}/filings?page[number]=2": {
            "data": [{"id": "2", "attributes": {"period_end": "2025-12-31", "json_url": "/b.json"}}],
            "links": {}},
    }

    class R:
        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            pass

        def json(self):
            return pagine[self.url]

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: R(url))
    righe = esef._list_filings(LEI, ritmo=lambda: battiti.append(1))
    assert len(battiti) == 2 and [r["id"] for r in righe] == ["1", "2"]


def test_download_esef_con_contatto_e_ritmo(tmp_path, monkeypatch):
    battiti, visti = [], {}
    monkeypatch.setattr(esef, "attendi_esef", lambda: battiti.append(1))

    class R:
        status_code = 200
        content = b'{"facts": {}}'
        headers = {"Content-Type": "application/json"}
        url = "https://filings.xbrl.org/x/a.json"

        def raise_for_status(self):
            pass

    def get(url, **k):
        visti.update(k)
        return R()

    monkeypatch.setattr(lettore_trimestrali.requests, "get", get)
    monkeypatch.setattr(lettore_trimestrali, "_richiedi_indirizzi_pubblici", lambda *a: None)
    r = lettore_trimestrali.scarica_documento("https://filings.xbrl.org/x/a.json", str(tmp_path),
                                              host_consentiti={"filings.xbrl.org"}, public_only=True)
    assert r["stato"] == "ok" and battiti == [1]
    assert "contatto" in visti["headers"]["User-Agent"] and visti["timeout"] >= 300
