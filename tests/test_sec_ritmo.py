"""Ritmo SEC condiviso tra processi e download documenti regolati (fase D, task 1)."""
import multiprocessing as mp
import time

from bellomberg.market_data import lettore_trimestrali, sec_edgar


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
        sec_edgar.attendi_sec(percorso=percorso)


def test_ritmo_condiviso_tra_due_processi(tmp_path):
    percorso = tmp_path / ".sec_ritmo"
    ctx = mp.get_context("spawn")
    coda = ctx.Queue()
    procs = [ctx.Process(target=_batti, args=(percorso, 8, coda)) for _ in range(2)]
    for p in procs:
        p.start()
    istanti = sorted(coda.get(timeout=30) for _ in range(16))
    for p in procs:
        p.join(30)
    gap = [b - a for a, b in zip(istanti, istanti[1:])]
    # 16 richieste da due processi: mai due a meno di ~intervallo (tolleranza scheduler 20 ms)
    assert min(gap) >= sec_edgar._SEC_INTERVALLO_S - 0.001


def test_file_ritmo_corrotto_non_blocca(tmp_path):
    percorso = tmp_path / ".sec_ritmo"
    percorso.write_text("non-un-numero")
    t0 = time.monotonic()
    sec_edgar.attendi_sec(percorso=percorso)
    assert time.monotonic() - t0 < 0.5


def test_percorso_non_scrivibile_ripiega_sul_ritmo_locale(tmp_path):
    bloccante = tmp_path / "file"
    bloccante.write_text("x")
    # il genitore e' un file: mkdir/open falliscono con OSError, nessuna eccezione verso il chiamante
    sec_edgar.attendi_sec(percorso=bloccante / ".sec_ritmo")


def test_download_sec_passa_da_attendi_sec(tmp_path, monkeypatch):
    chiamate = []
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda: chiamate.append("sec"))

    class R:
        status_code = 200
        content = b"<html>x</html>"
        headers = {"Content-Type": "text/html"}
        url = "https://www.sec.gov/Archives/x.htm"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(lettore_trimestrali.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr(lettore_trimestrali, "_richiedi_indirizzi_pubblici", lambda *a: None)
    r = lettore_trimestrali.scarica_documento(
        "https://www.sec.gov/Archives/x.htm", str(tmp_path),
        host_consentiti={"www.sec.gov"}, public_only=True)
    assert r["stato"] == "ok"
    assert chiamate == ["sec"]
    # host non SEC: nessun ritmo SEC
    chiamate.clear()
    lettore_trimestrali.scarica_documento(
        "https://ir.example/x.htm", str(tmp_path), host_consentiti={"ir.example"})
    assert chiamate == []


def test_senza_fcntl_ripiego_locale_rispetta_intervallo(tmp_path, monkeypatch):
    monkeypatch.setattr(sec_edgar, "fcntl", None)
    sec_edgar.attendi_sec(percorso=tmp_path / ".sec_ritmo")
    t0 = time.monotonic()
    sec_edgar.attendi_sec(percorso=tmp_path / ".sec_ritmo")
    assert time.monotonic() - t0 >= sec_edgar._SEC_INTERVALLO_S - 0.02
