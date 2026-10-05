# -*- coding: utf-8 -*-
"""emarket_sdir.riverifica_deposito (INTERFACCIA_UE AGGIUNTA 5, P1 di RV-D4; IT2b 05/10/2026, Opus 5.5).

La ricevuta di get_data_deposito porta `risposte_salvate` (liste dei comunicati compresse, testo dei PDF
dello stadio 2 con lo sha del PDF, pagine della sezione Documenti): la riverifica RIFA' la scelta completa
senza rete e la confronta. Manomissioni di data, titolo, categoria, protocollo, corpo di una lista, lista
tolta o aggiunta, testo del PDF tolto -> False col motivo. Liste e PDF SINTETICI («ACME SINTETICA»); la
sezione Documenti e' finta (leggi_documenti / rileggi_documenti di IT3 sostituite: qui si prova la
composizione e il ramo dei comunicati; la rilettura vera dei documenti e' provata da IT3)."""
import copy
import json
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_documenti as ed
from bellomberg.market_data import emarket_sdir as em

ID = 4242
URL_DOC = "https://www.emarketstorage.it/it/documenti?azienda=%d&finto" % ID


def _lista(righe):
    blocchi = "".join(
        '<div class="views-row"><div class="azienda-wrapper" data-protocollo="%s">'
        '<div class="news-data"><a href="/sites/default/files/comunicati/2026-08/sint_%s.pdf">'
        '<time datetime="0Z" class="datetime">%s</time></a></div></div>'
        '<div class="news-title"><a href="/sites/default/files/comunicati/2026-08/sint_%s.pdf">%s</a></div></div>\n'
        % (p, p, d, p, t) for p, d, t in righe)
    return ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
            '<select name="azienda"><option value="%d" selected="selected">ACME SINTETICA</option></select></form>'
            '<div class="view-content">%s</div></body></html>' % (ID, blocchi)).encode("utf-8")


def _vuota():
    return ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
            '<select name="azienda"><option value="%d">ACME SINTETICA</option></select></form></body></html>'
            % ID).encode("utf-8")


def _pdf(testo):
    sicuro = testo.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    flusso = ("BT /F1 10 Tf 40 800 Td (%s) Tj ET" % sicuro).encode("latin-1")
    oggetti = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(flusso) + flusso + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, pos = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(oggetti, 1):
        pos.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(oggetti) + 1)
    out += b"".join(b"%010d 00000 n \n" % p for p in pos)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(oggetti) + 1, xref)
    return bytes(out)


def _cat(c, pagina=0):
    return em.URL_CATEGORIA.format(cat=c, id=ID) + ("&page=%d" % pagina if pagina else "")


def _pdf_url(p):
    return em.BASE + "/sites/default/files/comunicati/2026-08/sint_%s.pdf" % p


def _documenti(stato="vuoto_misurato", righe=()):
    corpo = {"codifica": "gzip+base64", "corpo": em._comprimi(b"<html>documenti finti</html>")["corpo"]}
    return {"stato": stato, "errore": None, "motivo": None, "righe": list(righe), "url_liste": [URL_DOC],
            "sha256_liste": {URL_DOC: "0" * 64}, "pagine_lette": 1, "limiti": [],
            "risposte_salvate": {URL_DOC: corpo}}


@pytest.fixture
def amb(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": "ITZZACME0007", "emarket": ID}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 4))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    risposte, chieste = {}, []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        assert ok, motivo
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta")
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    a = {"risposte": risposte, "chieste": chieste, "docs": _documenti(), "riletture": []}
    monkeypatch.setattr(ed, "leggi_documenti", lambda id_em, **k: copy.deepcopy(a["docs"]))

    def rileggi(salvate, sha, **k):
        a["riletture"].append(k)
        if URL_DOC not in salvate:
            return {"stato": "KO", "errore": "ricevuta", "motivo": "pagina dei documenti assente", "righe": [],
                    "url_liste": [], "sha256_liste": {}, "pagine_lette": 0, "limiti": [], "risposte_salvate": {}}
        return copy.deepcopy(a["docs"])
    monkeypatch.setattr(ed, "rileggi_documenti", rileggi, raising=False)
    return a


def _comunicati(amb, tipo, per_cat):
    for c in em.CATEGORIE_DEPOSITO[tipo]:
        amb["risposte"][_cat(c)] = (200, _lista(per_cat[c]) if c in per_cat else _vuota())


def _riverifica(r, **k):
    return em.riverifica_deposito(r, ticker="ACME.MI", tipo=k.get("tipo", "semestrale"),
                                  periodo_fine=k.get("fine", "2026-06-30"))


SEMESTRALE = {101: [("930102", "06/08/2026 - 10:49", "ACME: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"),
                    ("930101", "29/07/2026 - 18:00", "ACME: risultati del primo semestre 2026")]}


@pytest.fixture
def ricevuta(amb):
    _comunicati(amb, "semestrale", SEMESTRALE)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-08-06" and r["tipo_data"] == "diffusione_comunicato"
    return r


def test_ricevuta_porta_le_risposte_salvate_con_sha_sul_corpo_non_compresso(ricevuta):
    import hashlib
    assert set(ricevuta["url_liste"]) <= set(ricevuta["risposte_salvate"]) and URL_DOC in ricevuta["risposte_salvate"]
    for u in ricevuta["url_liste"]:
        v = ricevuta["risposte_salvate"][u]
        assert v["codifica"] == "gzip+base64"
        assert hashlib.sha256(em._decomprimi(v)).hexdigest() == ricevuta["sha256_liste"][u]


def test_riverifica_ok_senza_rete(ricevuta, amb):
    n = len(amb["chieste"])
    ok, motivo = _riverifica(ricevuta)
    assert ok, motivo
    assert len(amb["chieste"]) == n       # nessuna richiesta
    assert amb["riletture"] and amb["riletture"][0]["id_emarket"] == ID


@pytest.mark.parametrize("campo,valore", [("data_deposito", "2026-07-02"), ("titolo", "ACME: altro"),
                                          ("categoria", 150), ("protocollo", "999999"), ("ora_deposito", "08:00"),
                                          ("tipo_data", "stoccaggio_documento")])
def test_manomissione_di_un_campo(ricevuta, campo, valore):
    r = copy.deepcopy(ricevuta)
    r[campo] = valore
    ok, motivo = _riverifica(r)
    assert not ok and campo in motivo


def test_data_falsificata_anche_nel_corpo_della_lista_ma_sha_vecchio(ricevuta):
    """P1 di RV-D4: la data si sposta anche nel corpo salvato della lista; lo sha della ricevuta non torna."""
    r = copy.deepcopy(ricevuta)
    u = _cat(101)
    corpo = em._decomprimi(r["risposte_salvate"][u]).replace(b"06/08/2026", b"02/07/2026")
    r["risposte_salvate"][u] = em._comprimi(corpo)
    r["data_deposito"] = "2026-07-02"
    ok, motivo = _riverifica(r)
    assert not ok and "sha diverso" in motivo


def test_lista_tolta(ricevuta):
    r = copy.deepcopy(ricevuta)
    u = _cat(150)
    r["url_liste"].remove(u)
    del r["sha256_liste"][u], r["risposte_salvate"][u]
    ok, motivo = _riverifica(r)
    assert not ok and "assente dalla ricevuta" in motivo


def test_lista_aggiunta(ricevuta):
    import hashlib
    r = copy.deepcopy(ricevuta)
    u = _cat(101, 1)
    r["url_liste"].append(u)
    r["sha256_liste"][u] = hashlib.sha256(_vuota()).hexdigest()
    r["risposte_salvate"][u] = em._comprimi(_vuota())
    ok, motivo = _riverifica(r)
    assert not ok and "url_liste" in motivo


def test_ricevuta_vecchia_senza_risposte_salvate(ricevuta):
    r = copy.deepcopy(ricevuta)
    del r["risposte_salvate"]
    ok, motivo = _riverifica(r)
    assert not ok and "risposte_salvate" in motivo


def test_documenti_non_rileggibili(ricevuta):
    r = copy.deepcopy(ricevuta)
    del r["risposte_salvate"][URL_DOC]
    ok, motivo = _riverifica(r)
    assert not ok and "Documenti" in motivo


def test_ricevuta_KO_non_si_riverifica(amb):
    _comunicati(amb, "semestrale", SEMESTRALE)
    amb["risposte"][_cat(150)] = (500, b"")
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "KO"
    ok, motivo = _riverifica(r)
    assert not ok and "KO" in motivo


def test_ticker_o_periodo_diversi(ricevuta):
    ok, motivo = em.riverifica_deposito(ricevuta, ticker="ACME.MI", tipo="semestrale", periodo_fine="2025-06-30")
    assert not ok and "periodo" in motivo


# ------------------------------------------------------------ stadio 2: testo del PDF salvato
@pytest.fixture
def ricevuta_pdf(amb):
    _comunicati(amb, "semestrale", {101: [("930201", "13/08/2026 - 12:16", "ACME S.p.A.: deposito documenti")]})
    amb["risposte"][_pdf_url("930201")] = (200, _pdf(
        "ACME SINTETICA: e' stata messa a disposizione del pubblico la Relazione finanziaria semestrale al 30 giugno 2026"))
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["prova"] == "testo_pdf"
    return r


def test_stadio_2_testo_salvato_e_riverificato(ricevuta_pdf):
    v = ricevuta_pdf["risposte_salvate"][_pdf_url("930201")]
    assert v["tipo"] == "testo_pdf" and v["sha256_pdf"] == ricevuta_pdf["sha256_pdf"][_pdf_url("930201")]
    assert "Relazione finanziaria semestrale" in em._decomprimi(v).decode("utf-8")
    ok, motivo = _riverifica(ricevuta_pdf)
    assert ok, motivo


def test_stadio_2_testo_tolto_o_cambiato(ricevuta_pdf):
    r = copy.deepcopy(ricevuta_pdf)
    del r["risposte_salvate"][_pdf_url("930201")]
    ok, motivo = _riverifica(r)
    assert not ok and "testo del PDF" in motivo
    r = copy.deepcopy(ricevuta_pdf)
    v = r["risposte_salvate"][_pdf_url("930201")]
    nuovo = em._comprimi(b"testo qualunque senza la relazione")
    v["corpo"] = nuovo["corpo"]
    ok, motivo = _riverifica(r)
    assert not ok


# ------------------------------------------------------------ ramo documenti (composizione)
def _doc(prot, data, titolo):
    return {"data": data, "ora": "18:00", "titolo": titolo, "protocollo": prot, "esef": True, "lingua": "it",
            "url": em.BASE + "/sites/default/files/xbrl/%s/sint_%s.zip" % (data[:7], prot), "categoria": None,
            "categorie": [], "fuso": "Europe/Rome"}


def test_documento_primario_riverificato_e_manomesso(amb):
    amb["docs"] = _documenti("ok", [_doc("940101", "2026-08-05", "ACME SINTETICA - Relazione finanziaria semestrale al 30 giugno 2026")])
    _comunicati(amb, "semestrale", SEMESTRALE)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["tipo_data"] == "stoccaggio_documento" and r["data_deposito"] == "2026-08-05"
    assert r["comunicato_conferma"]["data"] == "2026-08-06"
    ok, motivo = _riverifica(r)
    assert ok, motivo
    falsa = copy.deepcopy(r)
    falsa["data_deposito"] = "2026-07-02"
    assert not _riverifica(falsa)[0]
    falsa = copy.deepcopy(r)
    falsa["comunicato_conferma"]["data"] = "2026-07-02"
    ok, motivo = _riverifica(falsa)
    assert not ok and "conferma" in motivo


def test_ambiguo_documenti_candidati_identici(amb):
    """RV-D4: sull'ambiguo della sezione Documenti la riverifica confronta candidati e prova_documento."""
    amb["docs"] = _documenti("ok", [
        _doc("940201", "2026-08-05", "ACME SINTETICA: Relazione finanziaria semestrale al 30 giugno 2026"),
        _doc("940202", "2026-09-10", "ACME SINTETICA: Relazione finanziaria semestrale al 30 giugno 2026")])
    amb["docs"]["righe"] = [dict(x, esef=False) for x in amb["docs"]["righe"]]
    _comunicati(amb, "semestrale", SEMESTRALE)
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ambiguo" and r["prova"] == "documento"
    assert _riverifica(r)[0]
    falsa = copy.deepcopy(r)
    falsa["candidati"] = falsa["candidati"][:1]
    ok, motivo = _riverifica(falsa)
    assert not ok and "candidati" in motivo


def test_risposte_salvate_vuote(ricevuta):
    r = copy.deepcopy(ricevuta)
    r["risposte_salvate"] = {}
    ok, motivo = _riverifica(r)
    assert not ok and "risposte_salvate" in motivo


def test_ticker_diverso(ricevuta):
    ok, motivo = em.riverifica_deposito(ricevuta, ticker="ZZTEST.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "ticker" in motivo


def test_esito_documenti_mancante(ricevuta):
    r = copy.deepcopy(ricevuta)
    del r["stato_documenti"]
    ok, motivo = _riverifica(r)
    assert not ok and "esito della sezione Documenti" in motivo


def test_giorno_di_lettura_nella_ricevuta(ricevuta, amb):
    """La finestra dei documenti dipende dal giorno di lettura: la ricevuta lo porta e la riverifica lo usa."""
    assert ricevuta["oggi_lettura"] == "2026-10-04"
    ok, _ = _riverifica(ricevuta)
    assert ok
    r = copy.deepcopy(ricevuta)
    del r["oggi_lettura"]
    ok, motivo = _riverifica(r)
    assert not ok and "oggi_lettura" in motivo


# ------------------------------------------------------------ cablaggio VERO con IT3 (leggi_documenti e rileggi_documenti veri)
def test_riverifica_con_la_rilettura_vera_dei_documenti(monkeypatch, tmp_path):
    import os
    fix = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"SINT.MI": {"isin": "ITZZSINT0003", "emarket": 900}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 11, 5))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    monkeypatch.setattr(ed, "PAUSA_S", 0)
    url_doc = "https://www.emarketstorage.it/it/documenti?azienda=900&data_from=2026-07-01&data_to=2026-10-29"
    risposte = {url_doc: open(os.path.join(fix, "emd_semestrale.html"), "rb").read(),
                url_doc + "&categoria=101": open(os.path.join(fix, "emd_semestrale_cat101.html"), "rb").read()}
    vuota = ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
             '<select name="azienda"><option value="900">ACME SINTETICA</option></select></form></body></html>').encode()
    for c in em.CATEGORIE_DEPOSITO["semestrale"]:
        risposte[em.URL_CATEGORIA.format(cat=c, id=900)] = vuota
    chieste = []

    def finto(url):
        chieste.append(url)
        if url not in risposte:
            raise ConnectionError("nessuna risposta finta")
        return 200, risposte[url], url
    monkeypatch.setattr(bi, "_scarica", finto)
    r = em.get_data_deposito("SINT.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["tipo_data"] == "stoccaggio_documento" and r["protocollo"] == "900208"
    assert {url_doc, url_doc + "&categoria=101"} <= set(r["risposte_salvate"])
    n = len(chieste)
    ok, motivo = em.riverifica_deposito(r, ticker="SINT.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok, motivo
    assert len(chieste) == n                       # nessuna rete
    falsa = copy.deepcopy(r)
    corpo = em._decomprimi(falsa["risposte_salvate"][url_doc]).replace(b"06/08/2026", b"02/07/2026")
    falsa["risposte_salvate"][url_doc] = em._comprimi(corpo)
    falsa["data_deposito"] = "2026-07-02"
    ok, motivo = em.riverifica_deposito(falsa, ticker="SINT.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert not ok and "Documenti" in motivo
