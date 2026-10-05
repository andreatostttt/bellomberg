"""Revisione 04/10 (R8): errori ESEF trattati come assenza di dato. Nessuna rete, dati sintetici.

1. Un 404 di filings.xbrl.org diventava un elenco vuoto: il DCF usava uno storico vecchio
   senza nota e la pipeline leggeva «0 depositi». Ora il 404 e' dichiarato.
2. Un pacchetto ESEF invalido si riscaricava (fino a 80 MB) a ogni giro. Ora si ricorda
   (URL + sha256 + data) e non si riscarica prima della scadenza, dichiarandolo.
"""
import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from bellomberg.market_data import esef, esef_sito, ixbrl_oim

LEI = "999900NOVA0000000001"


class _R404:
    status_code = 404

    def raise_for_status(self):
        import requests
        raise requests.HTTPError("404")


def test_list_filings_404_e_dichiarato_non_elenco_vuoto(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: _R404())
    with pytest.raises(esef.EntitaEsefAssente) as exc:
        esef._list_filings(LEI)
    assert "404" in str(exc.value) and LEI in str(exc.value)


def test_indice_404_senza_cache_righe_vuote_con_motivo(tmp_path):
    def fetch(lei):
        raise esef.EntitaEsefAssente("filings.xbrl.org: HTTP 404 per l'entita' " + lei)

    a = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=fetch)
    assert a["righe"] == [] and "404" in a["motivo"]
    b = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: pytest.fail("cache fresca: niente rete"))
    assert b["righe"] == [] and b["origine"] == "cache" and "404" in b["motivo"]


def test_indice_404_con_cache_piena_usa_la_copia_e_lo_dice(tmp_path):
    righe = [{"id": 1, "period_end": "2024-12-31", "json_url": "https://filings.xbrl.org/x/1.json"}]
    esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=lambda lei: righe, ttl_s=0)

    def fetch(lei):
        raise esef.EntitaEsefAssente("filings.xbrl.org: HTTP 404 per l'entita' " + lei)

    out = esef.indice_depositi(LEI, cache_dir=tmp_path, fetch=fetch, ttl_s=0)
    assert out["origine"] == "cache_scaduta" and out["righe"] == righe and "404" in out["motivo"]


def test_storico_dcf_404_porta_la_nota_di_staleness(monkeypatch):
    vecchia = {"index_fetched_at": time.time() - 30 * 86400,
               "filings": {"f1": {"period_end": "2023-12-31", "facts": {"Revenue": [[2023, 100.0, "EUR"]]}}}}
    salvata = {}
    monkeypatch.setattr(esef, "_load_cache", lambda lei: json.loads(json.dumps(vecchia)))
    monkeypatch.setattr(esef, "_save_cache", lambda lei, c: salvata.update(c))
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: None)
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **k: _R404())
    cache = esef._refresh_entity_cache(LEI)
    assert "404" in cache["index_error"]
    assert cache["index_fetched_at"] == vecchia["index_fetched_at"]  # mai «aggiornato adesso»


# ---------------------------------------------------------------- pacchetti invalidi


def _zip_deflate_rotto():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("nova/reports/nova-2025.xhtml", b"<html>" + "".join(f"{i * 7919 % 100003:06d}" for i in range(800)).encode() + b"</html>")
    dati = bytearray(buf.getvalue())
    inizio = 30 + len("nova/reports/nova-2025.xhtml")  # intestazione locale + nome, poi i dati compressi
    dati[inizio] = 0xFF  # primo blocco deflate di tipo riservato (11): zlib.error, non BadZipFile
    return bytes(dati)


def test_deflate_corrotto_e_pacchetto_non_valido(tmp_path):
    p = tmp_path / "rotto.xbri"
    p.write_bytes(_zip_deflate_rotto())
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        ixbrl_oim.converti_pacchetto(p)


def test_annidamento_profondo_e_pacchetto_non_valido(monkeypatch):
    def esplode(*a, **k):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(ixbrl_oim, "_markup", esplode)
    xhtml = (b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">'
             b'<body><ix:nonNumeric name="ix:x" contextRef="c">x</ix:nonNumeric></body></html>')
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        ixbrl_oim.converti_xhtml(io.BytesIO(xhtml))


URL = "https://group.nova.example/f/nova-2025-12-31-en.xbri"


def _scarica_finto(contenuto, chiamate):
    import hashlib

    def scarica(url, dest, max_bytes, hosts):
        chiamate.append(url)
        Path(dest).mkdir(parents=True, exist_ok=True)
        p = Path(dest) / "pkg.xbri"
        p.write_bytes(contenuto)
        return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(contenuto).hexdigest()}
    return scarica


def test_pacchetto_invalido_ricordato_e_non_riscaricato(tmp_path):
    chiamate = []
    scarica = _scarica_finto(b"non e' uno zip", chiamate)
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    with pytest.raises(esef_sito.PacchettoInvalidoNoto) as exc:
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    assert chiamate == [URL]  # nessun secondo download
    testo = str(exc.value)
    assert "non riscaricato" in testo and "zip" in testo.lower()
    memoria = json.loads((tmp_path / esef_sito.INVALIDI_SITO).read_text(encoding="utf-8"))[URL]
    assert len(memoria["pacchetto_sha256"]) == 64 and memoria["motivo"] and memoria["quando"]


def test_pacchetto_invalido_si_ritenta_dopo_la_scadenza(tmp_path):
    chiamate = []
    scarica = _scarica_finto(b"non e' uno zip", chiamate)
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    p = tmp_path / esef_sito.INVALIDI_SITO
    dati = json.loads(p.read_text(encoding="utf-8"))
    dati[URL]["quando"] = time.time() - (esef_sito.GIORNI_PACCHETTO_INVALIDO + 1) * 86400
    p.write_text(json.dumps(dati), encoding="utf-8")
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    assert chiamate == [URL, URL]


def test_download_fallito_non_marca_il_pacchetto_invalido(tmp_path):
    chiamate = []

    def scarica(url, dest, max_bytes, hosts):
        chiamate.append(url)
        return {"stato": "errore", "motivo": "ConnectionError: rete giu'"}

    for _ in range(2):
        with pytest.raises(ValueError):
            esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    assert chiamate == [URL, URL] and not (tmp_path / esef_sito.INVALIDI_SITO).exists()


# ---------------------------------------------------------------- REV_G2a R-5


def _scarica_con_etag(contenuto, chiamate, etag):
    base = _scarica_finto(contenuto, chiamate)

    def scarica(url, dest, max_bytes, hosts):
        return {**base(url, dest, max_bytes, hosts), "etag": etag[0], "last_modified": None, "content_length": None}
    return scarica


def test_ripubblicato_con_etag_diverso_si_riscarica_uguale_no(tmp_path, monkeypatch):
    chiamate, etag, remote = [], ['"v1"'], {"etag": '"v1"', "last_modified": None, "content_length": None}
    scarica = _scarica_con_etag(b"non e' uno zip", chiamate, etag)
    teste = []
    monkeypatch.setattr(esef_sito, "_intestazioni_remote", lambda url, hosts: teste.append(url) or dict(remote),
                        raising=False)
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    with pytest.raises(esef_sito.PacchettoInvalidoNoto):  # stesso ETag: non si riscarica
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    assert chiamate == [URL] and teste == [URL]
    remote["etag"] = '"v2"'  # l'emittente ha ripubblicato allo stesso URL
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    assert chiamate == [URL, URL]


def test_forza_riscarica_un_pacchetto_invalido_noto(tmp_path):
    chiamate = []
    scarica = _scarica_finto(b"non e' uno zip", chiamate)
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica)
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"}, scarica_fn=scarica, forza=True)
    assert chiamate == [URL, URL]


def test_memoria_non_scritta_e_dichiarata(tmp_path, monkeypatch):
    def rotto(path, dati):
        raise OSError("disco pieno")
    monkeypatch.setattr(esef_sito, "_scrivi_json", rotto)
    with pytest.raises(ixbrl_oim.PacchettoNonValido, match="NON scritta"):
        esef_sito.scarica_pacchetto_json(URL, tmp_path, {"group.nova.example"},
                                         scarica_fn=_scarica_finto(b"non e' uno zip", []))


def test_head_di_controllo_nel_ritmo_senza_redirect_e_solo_host_ammessi(monkeypatch):
    import requests
    eventi = []
    monkeypatch.setattr(esef, "attendi_esef", lambda **k: eventi.append("ritmo"))
    monkeypatch.setattr("bellomberg.market_data.lettore_trimestrali._richiedi_indirizzi_pubblici",
                        lambda host, porta: eventi.append(("ip", host)))

    class R:
        status_code = 200
        headers = {"ETag": '"v9"', "Last-Modified": "Sat, 03 Oct 2026 10:00:00 GMT", "Content-Length": "123"}

    def head(url, **k):
        eventi.append(("head", k.get("allow_redirects")))
        return R()
    monkeypatch.setattr(requests, "head", head)
    out = esef_sito._intestazioni_remote(URL, {"group.nova.example"})
    assert out == {"etag": '"v9"', "last_modified": "Sat, 03 Oct 2026 10:00:00 GMT", "content_length": "123"}
    assert eventi == [("ip", "group.nova.example"), "ritmo", ("head", False)]
    with pytest.raises(ValueError):
        esef_sito._intestazioni_remote("https://altro.example/x.xbri", {"group.nova.example"})
