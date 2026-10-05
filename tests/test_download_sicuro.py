"""Fase F: download con tetto a flusso, redirect solo verso host ammessi, pulizia della cartella ai/."""
import io
import os

import pytest
import requests
import urllib3

from bellomberg.market_data import download_sicuro as ds
from bellomberg.market_data import lettore_trimestrali


def _risposta(url, corpo=b"%PDF-1.7 sintetico", *, status=200, location=None, lunghezza=None, flusso=True):
    r = requests.Response()
    r.status_code, r.url = status, url
    r.request = requests.Request("GET", url).prepare()
    if location:
        r.headers["Location"] = location
    if lunghezza is not None:
        r.headers["Content-Length"] = str(lunghezza)
    if flusso:  # corpo letto davvero a pezzi, come una risposta di rete
        r.raw = urllib3.response.HTTPResponse(body=io.BytesIO(corpo), preload_content=False)
    else:
        r._content = corpo
    return r


@pytest.fixture
def rete(monkeypatch):
    chiamate, risposte = [], {}
    monkeypatch.setattr(lettore_trimestrali, "_richiedi_indirizzi_pubblici", lambda host, port: None)

    def get(url, **kw):
        chiamate.append((url, kw))
        return risposte[url]
    monkeypatch.setattr(requests, "get", get)
    return chiamate, risposte


def test_scarica_entro_il_tetto_con_sha_e_flusso(tmp_path, rete):
    chiamate, risposte = rete
    url = "https://ir.nova.example/relazione-2025.pdf"
    risposte[url] = _risposta(url, b"x" * 5000)
    esito = ds.scarica_limitato(url, tmp_path / "ai", 10_000, {"ir.nova.example"})
    assert esito["stato"] == "ok" and esito["bytes"] == 5000 and os.path.getsize(esito["path"]) == 5000
    assert chiamate[0][1]["stream"] is True


@pytest.mark.parametrize("flusso", [True, False])
def test_tetto_superato_nessun_file(tmp_path, rete, flusso):
    _, risposte = rete
    url = "https://ir.nova.example/enorme.pdf"
    risposte[url] = _risposta(url, b"x" * 20_000, flusso=flusso)
    esito = ds.scarica_limitato(url, tmp_path / "ai", 10_000, {"ir.nova.example"})
    assert esito["stato"] == "errore" and "limite" in esito["motivo"]
    assert not (tmp_path / "ai").exists()


def test_content_length_oltre_il_tetto_non_legge_il_corpo(tmp_path, rete):
    _, risposte = rete
    url = "https://ir.nova.example/enorme.pdf"
    r = _risposta(url, b"x" * 10, lunghezza=50 * ds.MB)
    risposte[url] = r
    esito = ds.scarica_limitato(url, tmp_path, ds.MAX_PDF, {"ir.nova.example"})
    assert esito["stato"] == "errore" and "50 MB dichiarati" in esito["motivo"]
    assert r.raw.tell() == 0


def test_redirect_fuori_dominio_rifiutato(tmp_path, rete):
    chiamate, risposte = rete
    url = "https://ir.nova.example/a.pdf"
    risposte[url] = _risposta(url, b"", status=302, location="https://altro.example/a.pdf")
    esito = ds.scarica_limitato(url, tmp_path, 10_000, {"ir.nova.example"})
    assert esito["stato"] == "errore" and "altro.example" in esito["motivo"]
    assert [c[0] for c in chiamate] == [url]


def test_solo_https_e_tetto_valido(tmp_path, rete):
    chiamate, _ = rete
    assert ds.scarica_limitato("http://ir.nova.example/a.pdf", tmp_path, 10, {"ir.nova.example"})["stato"] == "errore"
    assert chiamate == []
    with pytest.raises(ValueError):
        ds.scarica_limitato("https://ir.nova.example/a.pdf", tmp_path, 0, {"ir.nova.example"})


def test_pulisci_cartella_eta_e_dimensione(tmp_path):
    adesso = 1_000_000_000
    def f(nome, size, giorni):
        p = tmp_path / nome
        p.write_bytes(b"x" * size)
        os.utime(p, (adesso - giorni * 86400, adesso - giorni * 86400))
        return str(p)
    vecchio = f("vecchio.pdf", 10, 40)
    medio = f("medio.pdf", 600, 5)
    nuovo = f("nuovo.pdf", 600, 1)
    appena = f("appena.pdf", 600, 3)  # piu' vecchio di nuovo, ma appena scaricato: si tiene
    (tmp_path / "sotto").mkdir()
    rimossi = ds.pulisci_cartella(tmp_path, giorni=30, max_totale=1300, tieni=[appena], adesso=adesso)
    assert rimossi == [vecchio, medio]
    assert sorted(os.listdir(tmp_path)) == ["appena.pdf", "nuovo.pdf", "sotto"]
    assert ds.pulisci_cartella(tmp_path / "manca") == []

