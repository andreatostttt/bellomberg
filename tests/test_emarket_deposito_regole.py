# -*- coding: utf-8 -*-
"""emarket_sdir.get_data_deposito su TUTTI gli emittenti (handoff-3, IT2b, 05/10/2026, Opus 5.5).

Regole allargate: sinonimi dei nomi dei documenti, titoli solo inglesi, nomi «intermedi» decisi
dal mese, approvazione del CdA e comunicato dei risultati NON sono il deposito, rettifiche,
comunicati doppi, titoli generici con il nome solo nel PDF (costo misurato e tagli dichiarati),
coerenza tipo/periodo. Nessuna rete: liste Drupal e PDF SINTETICI costruiti qui (emittente
«ACME SINTETICA», protocolli e date inventati).
"""
import json
import re
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_sdir as em

ID = 4242


# ------------------------------------------------------------ strumenti sintetici
def _r(prot, data, titolo, cat=101):
    return {"data": data, "ora": "10:00", "protocollo": prot, "titolo": titolo,
            "url_pdf": "https://x/%s.pdf" % prot, "categoria": cat}


def _pdf_url(prot):
    return em.BASE + "/sites/default/files/comunicati/2026-08/sint_%s.pdf" % prot


def _lista(righe, successiva=False):
    """Pagina lista eMarket SINTETICA con la struttura Drupal misurata (come dep_cat101.html).
    righe: [(protocollo, 'gg/mm/aaaa - HH:MM', titolo)]."""
    blocchi = "".join(
        '<div class="views-row"><div class="azienda-wrapper" data-protocollo="%s">'
        '<div class="news-data"><a href="/sites/default/files/comunicati/2026-08/sint_%s.pdf">'
        '<time datetime="0Z" class="datetime">%s</time></a></div></div>'
        '<div class="news-title"><a href="/sites/default/files/comunicati/2026-08/sint_%s.pdf">%s</a></div></div>\n'
        % (p, p, d, p, t) for p, d, t in righe)
    pager = '<nav class="pager"><li class="pager__item--next"><a title="Go to next page">x</a></li></nav>' \
        if successiva else ""
    return ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
            '<select name="azienda"><option value="%d" selected="selected">ACME SINTETICA</option></select></form>'
            '<div class="view-content">%s</div>%s</body></html>' % (ID, blocchi, pager)).encode("utf-8")


def _vuota():
    return ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
            '<select name="azienda"><option value="%d">ACME SINTETICA</option></select></form></body></html>'
            % ID).encode("utf-8")


def _pdf(testo):
    """PDF minimo valido (una pagina, Helvetica) col testo dato: niente file binari nel repo."""
    sicuro = testo.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    flusso = ("BT /F1 10 Tf 40 800 Td (%s) Tj ET" % sicuro).encode("latin-1")
    oggetti = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
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


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": "ITZZACME0007", "emarket": ID}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 4))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    # IT2b 05/10: la sezione DOCUMENTI (emarket_documenti, IT3) e' l'ancora primaria del deposito.
    # Qui si provano le regole dei COMUNICATI: la sezione Documenti risponde «nessun documento»
    # (vuoto_misurato), quindi vale il comunicato. Il cablaggio vero e' in test_emarket_deposito_documenti.py.
    from bellomberg.market_data import emarket_documenti as ed
    monkeypatch.setattr(ed, "leggi_documenti", lambda id_em, **k: {
        "stato": "vuoto_misurato", "errore": None, "motivo": None, "righe": [], "url_liste": [],
        "sha256_liste": {}, "pagine_lette": 0, "limiti": []})
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
    return {"risposte": risposte, "chieste": chieste}


def _liste(amb, tipo, per_cat):
    """Tutte le categorie del tipo: quelle non date sono liste vuote misurate."""
    for c in em.CATEGORIE_DEPOSITO[tipo]:
        amb["risposte"][_cat(c)] = (200, _lista(per_cat[c]) if c in per_cat else _vuota())


# ------------------------------------------------------------ A. sinonimi (puri)
SEM, ANN = date(2026, 6, 30), date(2025, 12, 31)
Q1, Q3 = date(2026, 3, 31), date(2026, 9, 30)


@pytest.mark.parametrize("tipo,fine,titolo,lingua", [
    ("semestrale", SEM, "ACME SINTETICA: messa a disposizione della Relazione finanziaria semestrale consolidata al 30 giugno 2026", "it"),
    ("semestrale", SEM, "ACME: depositato il Bilancio consolidato semestrale abbreviato al 30.06.2026", "it"),
    ("semestrale", SEM, "ACME: pubblicata la Relazione finanziaria al 30 giugno 2026", "it"),
    ("semestrale", SEM, "ACME: Relazione finanziaria intermedia al 30 giugno 2026 messa a disposizione", "it"),
    ("semestrale", SEM, "ACME: pubblicata la Relazione finanziaria semestrale del primo semestre 2026", "it"),
    ("semestrale", SEM, "ACME: Interim Financial Report at 30 June 2026 published", "en"),
    ("semestrale", SEM, "ACME: Condensed consolidated interim financial statements as at June 30, 2026 available", "en"),
    ("semestrale", SEM, "ACME: H1 2026 half-year report published", "en"),
    ("annuale", ANN, "ACME: pubblicata la Relazione finanziaria annuale 2025", "it"),
    ("annuale", ANN, "ACME: messa a disposizione del Bilancio consolidato al 31 dicembre 2025", "it"),
    ("annuale", ANN, "ACME: depositato il bilancio d'esercizio 2025", "it"),
    ("annuale", ANN, "ACME: deposito del Progetto di bilancio 2025 e della documentazione assembleare", "it"),
    ("annuale", ANN, "ACME: Relazione annuale integrata 2025 pubblicata", "it"),
    ("annuale", ANN, "ACME: 2025 Integrated Annual Report published", "en"),
    ("annuale", ANN, "ACME: Annual Report 2025 now available", "en"),
    ("annuale", ANN, "ACME: Consolidated financial statements at 31 December 2025 filed", "en"),
    ("trimestrale", Q1, "ACME: pubblicato il Resoconto intermedio al 31 marzo 2026", "it"),
    ("trimestrale", Q1, "ACME: Informativa finanziaria periodica aggiuntiva al 31.03.2026 disponibile", "it"),
    ("trimestrale", Q1, "ACME: relazione trimestrale al 31 marzo 2026 pubblicata", "it"),
    ("trimestrale", Q1, "ACME: Relazione finanziaria intermedia al 31 marzo 2026 pubblicata", "it"),
    ("trimestrale", Q1, "ACME: Q1 2026 additional periodic financial information published", "en"),
    ("trimestrale", Q1, "ACME: Interim financial report at March 31, 2026 published", "en"),
    ("trimestrale", Q1, "ACME: Quarterly report as of 31 March 2026 filed", "en"),
    ("trimestrale", Q3, "ACME: Resoconto intermedio di gestione al 30 settembre 2026 pubblicato", "it"),
    ("trimestrale", Q3, "ACME: pubblicate le informazioni finanziarie periodiche aggiuntive dei primi nove mesi 2026", "it"),
    ("trimestrale", Q3, "ACME: 9M 2026 additional periodic financial information available", "en"),
])
def test_sinonimi_dei_documenti(tipo, fine, titolo, lingua):
    v = em.candidati_deposito([_r("1", "2026-11-20" if fine == Q3 else "2026-05-15" if fine == Q1
                                  else "2026-08-10" if fine == SEM else "2026-04-10", titolo)], tipo, fine)
    assert v["stato"] == "ok", (titolo, v["motivo"], v["scartati"])
    assert v["scelto"]["lingua"] == lingua


@pytest.mark.parametrize("tipo,fine,titolo", [
    # «interim» lo decide il MESE: al 30/06 non e' una trimestrale, al 31/03 non e' una semestrale
    ("trimestrale", date(2026, 3, 31), "ACME: Interim Financial Report at 30 June 2026 published"),
    ("semestrale", SEM, "ACME: Interim financial report at March 31, 2026 published"),
    ("semestrale", SEM, "ACME: Relazione finanziaria intermedia al 31 marzo 2026 pubblicata"),
    # la semestrale abbreviata e i prospetti «condensed / half-year» non sono il bilancio annuale
    # (esercizio non solare chiuso al 30/06, unico caso in cui le date coincidono)
    ("annuale", SEM, "ACME: pubblicato il Bilancio consolidato semestrale abbreviato al 30 giugno 2026"),
    ("annuale", SEM, "ACME: Condensed consolidated financial statements at 30 June 2026 published"),
    ("annuale", SEM, "ACME: Half-year consolidated financial statements at 30 June 2026 published"),
    # «primo semestre» senza nome del documento = i risultati
    ("semestrale", SEM, "ACME: risultati del primo semestre 2026 pubblicati"),
])
def test_sinonimi_non_sconfinano(tipo, fine, titolo):
    v = em.candidati_deposito([_r("1", "2026-08-10", titolo)], tipo, fine)
    assert v["stato"] == "non_trovato", (titolo, v)


def test_nome_catturato_senza_la_data_contratto_D4():
    """Accordo D4 05/10: il mese e' un LOOKAHEAD, group(0) e' solo il nome del documento."""
    sem_en = re.compile(em.DOCUMENTI_DEPOSITO["semestrale"][1], re.I)
    tri_en = re.compile(em.DOCUMENTI_DEPOSITO["trimestrale"][1], re.I)
    m = sem_en.search("Interim Financial Report at 30 June 2026")
    assert m and m.group(0) == "Interim Financial Report"
    assert tri_en.search("Interim Financial Report at 30 June 2026") is None
    m = tri_en.search("Interim Financial Report at 31 March 2026")
    assert m and m.group(0) == "Interim Financial Report"
    assert sem_en.search("Interim Financial Report at 31 March 2026") is None
    # un mese che non e' ne' giugno ne' marzo/settembre: nessun tipo
    assert not any(re.search(rx, "Interim financial report at 31 October 2026", re.I)
                   for coppia in em.DOCUMENTI_DEPOSITO.values() for rx in coppia)


# ------------------------------------------------------------ B. approvazione, risultati, rettifiche, doppi
def test_approvazione_del_cda_non_e_il_deposito():
    v = em.candidati_deposito([_r("1", "2026-07-29", "ACME: il CdA approva la Relazione finanziaria semestrale al 30 giugno 2026")],
                              "semestrale", SEM)
    assert v["stato"] == "non_trovato" and v["scelto"] is None
    assert [s["protocollo"] for s in v["scartati"]] == ["1"] and "approvazione" in v["scartati"][0]["motivo"]
    v = em.candidati_deposito([_r("1", "2026-03-12", "ACME: Board of Directors approves the 2025 Annual Financial Report")],
                              "annuale", ANN)
    assert v["stato"] == "non_trovato" and "approvazione" in v["scartati"][0]["motivo"]


def test_approvazione_fa_partire_lo_stadio_2():
    """Approvazione col nome + deposito dal titolo generico: vince il deposito (letto nel PDF)."""
    righe = [_r("1", "2026-07-29", "ACME: approvata la Relazione finanziaria semestrale al 30 giugno 2026"),
             _r("2", "2026-08-05", "ACME S.p.A.: deposito della documentazione")]
    v = em.candidati_deposito(righe, "semestrale", SEM)
    assert v["stato"] == "non_trovato" and [g["protocollo"] for g in v["generici"]] == ["2"]
    v = em.candidati_deposito(righe, "semestrale", SEM, testi_pdf={
        "https://x/2.pdf": "e' stata messa a disposizione la Relazione finanziaria semestrale al 30 giugno 2026"})
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2" and v["prova"] == "testo_pdf"


def test_trimestrale_informazioni_periodiche_si_diffondono_col_comunicato_di_approvazione():
    v = em.candidati_deposito([_r("1", "2026-05-13", "ACME: il CdA approva le informazioni finanziarie periodiche aggiuntive al 31 marzo 2026")],
                              "trimestrale", Q1)
    assert v["stato"] == "ok" and v["scelto"]["natura"] == "informazioni_nel_comunicato"
    # il resoconto intermedio APPROVATO invece no: si deposita a parte
    v = em.candidati_deposito([_r("1", "2026-05-13", "ACME: il CdA approva il Resoconto intermedio di gestione al 31 marzo 2026")],
                              "trimestrale", Q1)
    assert v["stato"] == "non_trovato"


def test_comunicato_dei_risultati_scartato_e_dichiarato():
    righe = [_r("1", "2026-05-13", "ACME: Risultati al 31 marzo 2026"),
             _r("2", "2026-05-13", "ACME: Q1 2026 results"),
             _r("3", "2026-02-10", "ACME: pubblicato il Resoconto intermedio di gestione al 31 marzo 2026")]
    v = em.candidati_deposito(righe, "trimestrale", Q1)
    assert v["stato"] == "non_trovato" and v["candidati"] == []
    motivi = {s["protocollo"]: s["motivo"] for s in v["scartati"]}
    assert "RISULTATI" in motivi["1"] and "RISULTATI" in motivi["2"]
    assert "non dopo la fine del periodo" in motivi["3"]
    assert "scartati: 3" in v["motivo"]


def test_rettifica_e_originale_ambiguo_dichiarato():
    righe = [_r("1", "2026-08-05", "ACME: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"),
             _r("2", "2026-08-20", "ACME: rettifica - pubblicata la Relazione finanziaria semestrale al 30 giugno 2026")]
    v = em.candidati_deposito(righe, "semestrale", SEM)
    assert v["stato"] == "ambiguo" and v["scelto"] is None
    assert sorted(c["protocollo"] for c in v["candidati"]) == ["1", "2"] and "1 rettifiche" in v["motivo"]


def test_stesso_comunicato_in_due_categorie_con_protocolli_diversi_e_un_deposito():
    t = "ACME: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"
    a, b = _r("1", "2026-08-05", t, 101), dict(_r("2", "2026-08-05", t, 150), ora="10:03")
    v = em.candidati_deposito([b, a], "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "1"
    assert [c["protocollo"] for c in v["conferme"]] == ["2"]
    # stesso titolo ma GIORNI diversi: due depositi -> ambiguo
    v = em.candidati_deposito([a, dict(b, data="2026-08-07")], "semestrale", SEM)
    assert v["stato"] == "ambiguo"


def test_scartati_hanno_un_tetto_e_il_totale_nel_motivo():
    righe = [_r(str(i), "2026-08-%02d" % (i + 1), "ACME: risultati al 30 giugno 2026 (%d)" % i) for i in range(20)]
    v = em.candidati_deposito(righe, "semestrale", SEM)
    assert len(v["scartati"]) == em.MAX_SCARTATI and "scartati: 20" in v["motivo"]


# ------------------------------------------------------------ C. titoli generici
@pytest.mark.parametrize("titolo", [
    "ACME S.p.A.: deposito della documentazione", "ACME: avviso di deposito",
    "ACME: documentazione messa a disposizione del pubblico", "ACME: Notice of filing",
    "ACME: availability of documentation", "ACME: messa a disposizione del pubblico di documenti",
    # nome del documento + verbo del deposito ma SENZA periodo: il periodo si cerca nel PDF
    "ACME: messa a disposizione della Relazione finanziaria semestrale",
])
def test_generici_nuove_forme(titolo):
    v = em.candidati_deposito([_r("1", "2026-08-05", titolo)], "semestrale", SEM)
    assert v["stato"] == "non_trovato" and [g["protocollo"] for g in v["generici"]] == ["1"]


def test_stadio_2_costo_misurato_e_tagli_dichiarati(ambiente, monkeypatch):
    """5 generici: 1 oltre la finestra (non letto), 4 nella finestra ma tetto 2 -> letti 2 PDF.
    Le richieste sono contate e i due tagli dichiarati in `limiti`."""
    monkeypatch.setattr(em, "MAX_PDF_DEPOSITO", 2)
    righe = [("810101", "05/08/2026 - 10:00", "ACME S.p.A.: deposito documenti"),
             ("810102", "06/08/2026 - 10:00", "ACME S.p.A.: deposito documenti"),
             ("810103", "07/08/2026 - 10:00", "ACME S.p.A.: deposito documenti"),
             ("810104", "08/08/2026 - 10:00", "ACME S.p.A.: deposito documenti"),
             ("810105", "20/03/2027 - 10:00", "ACME S.p.A.: deposito documenti")]
    _liste(ambiente, "semestrale", {101: righe})
    altro = _pdf("Avviso: messa a disposizione dello statuto aggiornato")
    for p, _, _ in righe:
        ambiente["risposte"][_pdf_url(p)] = (200, altro)
    ambiente["risposte"][_pdf_url("810102")] = (200, _pdf(
        "ACME SINTETICA: e' stata messa a disposizione del pubblico la Relazione finanziaria semestrale al 30 giugno 2026"))
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["protocollo"] == "810102" and r["prova"] == "testo_pdf", r["motivo"]
    assert r["richieste"] == {"liste": len(em.CATEGORIE_DEPOSITO["semestrale"]), "pdf": 2, "documenti": 0}
    assert _pdf_url("810103") not in ambiente["chieste"] and _pdf_url("810105") not in ambiente["chieste"]
    assert any("oltre 200 giorni" in l for l in r["limiti"])
    assert any("letti i PDF dei primi 2 (prima i titoli italiani" in l for l in r["limiti"])
    assert any("letti 2 PDF su 5" in l for l in r["limiti"])
    assert r["natura"] == "messa_a_disposizione"


def test_stadio_2_non_trovato_elenca_i_pdf_letti_negli_scartati(ambiente):
    _liste(ambiente, "semestrale", {101: [("810201", "05/08/2026 - 10:00", "ACME S.p.A.: documents filing")]})
    ambiente["risposte"][_pdf_url("810201")] = (200, _pdf("ACME SINTETICA: documents relating to the shareholders meeting"))
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_trovato" and r["richieste"]["pdf"] == 1
    assert [s["protocollo"] for s in r["scartati"]] == ["810201"] and "testo del PDF" in r["scartati"][0]["motivo"]


def test_integrazione_approvazione_scartata_e_deposito_trovato(ambiente):
    _liste(ambiente, "annuale", {150: [
        ("820102", "15/04/2026 - 18:00", "ACME: Relazione Finanziaria Annuale 2025 messa a disposizione del pubblico"),
        ("820101", "12/03/2026 - 18:00", "ACME: il CdA approva il progetto di bilancio 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["protocollo"] == "820102" and r["data_deposito"] == "2026-04-15"
    assert [s["protocollo"] for s in r["scartati"]] == ["820101"]
    # (review RV-IT2: «approvazione» nei limiti era vacuo, c'e' gia' nel limite fisso) il MOTIVO dello scarto
    assert "approvazione" in r["scartati"][0]["motivo"]


# ------------------------------------------------------------ D. coerenza tipo/periodo
@pytest.mark.parametrize("tipo,fine,frase", [
    ("trimestrale", "2026-06-30", "al 30/06 c'e' la semestrale"),
    ("trimestrale", "2026-12-31", "al 31/12 l'annuale"),
    ("semestrale", "2026-03-31", "semestrale al 31/03 incoerente"),
    ("semestrale", "2026-09-30", "semestrale al 30/09 incoerente"),
    ("annuale", "2025-12-30", "ultimo giorno di un mese"),
    ("trimestrale", "2026-03-30", "ultimo giorno di un mese"),
])
def test_tipo_e_periodo_incoerenti_errore_parametro(ambiente, tipo, fine, frase):
    r = em.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine)
    assert r["stato"] == "KO" and r["errore"] == "parametro" and frase in r["motivo"]
    assert ambiente["chieste"] == []


@pytest.mark.parametrize("tipo,fine", [("trimestrale", date(2026, 3, 31)), ("trimestrale", date(2026, 9, 30)),
                                       ("semestrale", date(2026, 6, 30)), ("semestrale", date(2026, 12, 31)),
                                       ("annuale", date(2025, 12, 31)), ("annuale", date(2026, 6, 30)),
                                       ("annuale", date(2024, 2, 29))])
def test_tipo_e_periodo_coerenti(tipo, fine):
    assert em.coerenza_tipo_periodo(tipo, fine) is None


def test_annuale_esercizio_non_solare():
    v = em.candidati_deposito([_r("1", "2026-10-01", "ACME: pubblicata la Relazione finanziaria annuale dell'esercizio 2025/2026")],
                              "annuale", date(2026, 6, 30))
    assert v["stato"] == "ok"
    v = em.candidati_deposito([_r("1", "2026-10-01", "ACME: pubblicata la Relazione finanziaria annuale dell'esercizio 2024/2025")],
                              "annuale", date(2026, 6, 30))
    assert v["stato"] == "non_trovato"


# ------------------------------------------------------------ E. forme misurate da M1 (05/10) riprodotte su titoli SINTETICI
@pytest.mark.parametrize("tipo,fine,titolo,natura", [
    # il solo anno dopo / prima del nome
    ("semestrale", SEM, "ACME: Avviso di pubblicazione della Relazione finanziaria semestrale 2026", "messa_a_disposizione"),
    ("semestrale", SEM, "ACME: Filing of 2026 Half-Year Financial Report", "messa_a_disposizione"),
    # data con l'anno a 2 cifre attaccata a un codice
    ("annuale", ANN, "ACME: Avviso deposito della Relazione finanziaria annuale al 31/12/25_ZZ999", "messa_a_disposizione"),
    ("annuale", ANN, "ACME: Avviso deposito documentazione assembleare: Resoconti dell'esercizio 2025", "messa_a_disposizione"),
    ("annuale", ANN, "ACME: Pubblication of annual report 2025", "messa_a_disposizione"),
    # trimestrale: informativa periodica aggiuntiva approvata = diffusa col comunicato
    ("trimestrale", Q1, "ACME: Approvata dal CdA l'informativa periodica aggiuntiva al 31 marzo 2026", "informazioni_nel_comunicato"),
    ("trimestrale", Q1, "ACME: Additional periodic financial report at March 31, 2026 approved by the Board", "informazioni_nel_comunicato"),
    # prospetti intermedi col periodo lontano dal nome (45 caratteri senza cifre)
    ("trimestrale", Q1, "ACME: CONSOLIDATED CONDENSED INTERIM FINANCIAL STATEMENTS For the three-month period ended March 31, 2026 filed",
     "messa_a_disposizione"),
    # avviso sui quotidiani col verbo del deposito: unico candidato
    ("semestrale", SEM, "ACME: Avviso Stampa. Pubblicata la Relazione semestrale al 30 giugno 2026", "avviso_stampa"),
])
def test_forme_misurate(tipo, fine, titolo, natura):
    v = em.candidati_deposito([_r("1", "2026-08-10" if fine == SEM else "2026-05-14" if fine == Q1 else "2026-03-30",
                                  titolo)], tipo, fine)
    assert v["stato"] == "ok", (titolo, v["motivo"], v["scartati"])
    assert v["scelto"]["natura"] == natura


def test_comunicato_stampa_col_nome_non_e_il_deposito():
    v = em.candidati_deposito([_r("1", "2026-08-01", "ACME: Comunicato stampa Relazione Finanziaria Semestrale Consolidata al 30-06-2026"),
                               _r("2", "2026-08-01", "ACME: Press release on Consolidated Half-year Financial Report at 30-06-2026")],
                              "semestrale", SEM)
    assert v["stato"] == "non_trovato"
    assert all("comunicato stampa" in s["motivo"] for s in v["scartati"]) and len(v["scartati"]) == 2


def test_avviso_sui_quotidiani_conta_solo_senza_il_comunicato_del_deposito():
    deposito = _r("1", "2026-07-31", "ACME: Avviso di deposito della Relazione finanziaria semestrale al 30 giugno 2026")
    avviso = _r("2", "2026-08-01", "ACME: Avviso pubblicazione Relazione semestrale al 30 giugno 2026 (quotidiano ZZ in data 01/08)")
    v = em.candidati_deposito([avviso, deposito], "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "1"
    assert [c["protocollo"] for c in v["conferme"]] == ["2"]
    v = em.candidati_deposito([avviso], "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["natura"] == "avviso_stampa"


def test_stesso_giorno_comunicato_e_avviso_di_deposito_data_univoca():
    a = dict(_r("1", "2026-07-31", "ACME: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"), ora="18:00")
    b = dict(_r("2", "2026-07-31", "ACME: Avviso di deposito della Relazione finanziaria semestrale al 30 giugno 2026"), ora="17:40")
    v = em.candidati_deposito([a, b], "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2"
    assert [c["protocollo"] for c in v["conferme"]] == ["1"] and "stesso giorno 2026-07-31" in v["note"][0]
    # stesso giorno con la rettifica «annulla e sostituisce»: la data non cambia, la nota lo dice
    v = em.candidati_deposito([a, dict(b, titolo="Annulla e sostituisce invio precedente: " + b["titolo"])],
                              "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2" and "rettifica" in v["note"][0]


@pytest.mark.parametrize("titolo", ["ACME: Notice of Filing of updated company bylaws",
                                    "ACME: Avviso di deposito verbale Assemblea ordinaria",
                                    "ACME: Avviso di deposito Documento Informativo operazione con parti correlate",
                                    "ACME: Pubblicazione della documentazione relativa alla sollecitazione di deleghe"])
def test_generici_di_altri_documenti_non_si_leggono(titolo):
    v = em.candidati_deposito([_r("1", "2026-08-05", titolo)], "semestrale", SEM)
    assert v["stato"] == "non_trovato" and v["generici"] == []


def test_generici_italiani_prima_degli_inglesi():
    righe = [_r("1", "2026-08-05", "ACME: documents filing"), _r("2", "2026-08-06", "ACME: deposito documenti"),
             _r("3", "2026-08-07", "ACME: deposito documentazione / filing notice")]
    assert [g["protocollo"] for g in em.generici_deposito(righe, SEM, "semestrale")] == ["2", "3", "1"]


def test_semestrale_cercata_anche_in_109(ambiente):
    """Misura M1: alcuni emittenti depositano la semestrale in 109 («3.1 altre informazioni»)."""
    _liste(ambiente, "semestrale", {109: [("830101", "05/08/2026 - 10:00",
                                           "ACME: Avviso di deposito della Relazione finanziaria semestrale al 30 giugno 2026")]})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["categoria"] == 109 and r["protocollo"] == "830101"
    assert 109 in r["categorie_cercate"] and 100 in r["categorie_cercate"]


def test_integrazione_avviso_stampa_dichiarato_nei_limiti(ambiente):
    _liste(ambiente, "semestrale", {101: [("830201", "12/08/2026 - 09:00", "ACME: Avviso Stampa. Pubblicata la Relazione semestrale al 30 giugno 2026")]})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["natura"] == "avviso_stampa"
    assert any("AVVISO" in l and "anteriore" in l for l in r["limiti"])


# ------------------------------------------------------------ F. classi di mancati.jsonl (M1a 05/10) su titoli/PDF SINTETICI
@pytest.mark.parametrize("tipo,fine,titolo", [
    ("annuale", ANN, "ACME: Filing of the Integrated Financial Report 2025 and the documentation for the Shareholders' meeting"),
    ("annuale", ANN, "ACME: Publication of 2025 Financial Report and documents 2026 Shareholders' Meeting"),
    ("annuale", ANN, "ACME: Annual Integrated Report 2025 published"),
    ("annuale", ANN, "ACME: pubblicato il bilancio al 31 dicembre 2025"),
    ("semestrale", SEM, "ACME notice: Interim financial report 30 06 2026 published"),
    ("semestrale", SEM, "ACME: Publication first half financial report 2026"),
])
def test_classi_misurate_nel_titolo(tipo, fine, titolo):
    v = em.candidati_deposito([_r("1", "2026-08-07" if fine == SEM else "2026-03-31", titolo)], tipo, fine)
    assert v["stato"] == "ok", (titolo, v["motivo"], v["scartati"])


def test_gemella_inglese_col_titolo_italiano_e_una_conferma():
    t = "ACME: pubblicato il Resoconto Intermedio di Gestione Consolidato al 31 marzo 2026"
    v = em.candidati_deposito([_r("1", "2026-05-07", t + " - (Versione Italiano)"),
                               dict(_r("2", "2026-05-08", t + " - (Versione Inglese)"))], "trimestrale", Q1)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "1"
    assert [(c["protocollo"], c["lingua"]) for c in v["conferme"]] == [("2", "en")]


@pytest.mark.parametrize("titolo", ["ACME: Avviso di pubblicazione di documenti", "ACME: Notice publication of documents"])
def test_generici_pubblicazione_di_documenti(titolo):
    v = em.candidati_deposito([_r("1", "2026-08-13", titolo)], "semestrale", SEM)
    assert [g["protocollo"] for g in v["generici"]] == ["1"]


@pytest.mark.parametrize("tipo,fine,testo", [
    # pypdf spezza l'anno
    ("annuale", ANN, "messa a disposizione la relazione finanziaria annuale al 31 dicembre 202 5, comprensiva"),
    # nome seguito dal SOLO anno nel testo del PDF
    ("annuale", ANN, "is available: Annual Report 2025 of ACME SINTETICA S.p.A. - comprising the draft"),
    ("annuale", ANN, "e' a disposizione la Relazione finanziaria annuale integrata 2025, che comprende il progetto"),
    ("semestrale", SEM, "e' a disposizione la Relazione finanziaria semestrale 2026 di ACME SINTETICA"),
])
def test_stadio_2_forme_del_testo_pdf(tipo, fine, testo):
    righe = [_r("1", "2026-08-13" if fine == SEM else "2026-03-30", "ACME: deposito documentazione")]
    v = em.candidati_deposito(righe, tipo, fine, testi_pdf={"https://x/1.pdf": testo})
    assert v["stato"] == "ok" and v["prova"] == "testo_pdf", (testo, v["motivo"])


def test_stadio_2_anno_da_solo_vale_solo_nel_pdf_e_vicino_al_nome():
    righe = [_r("1", "2026-03-30", "ACME: deposito documentazione")]
    lontano = "la relazione finanziaria annuale " + "x" * 80 + " assemblea 2025"
    v = em.candidati_deposito(righe, "annuale", ANN, testi_pdf={"https://x/1.pdf": lontano})
    assert v["stato"] == "non_trovato"
    # nel TITOLO l'anno da solo lontano dal nome non basta
    v = em.candidati_deposito([_r("1", "2026-03-30", "ACME: pubblicata la relazione finanziaria annuale; assemblea del 2025")],
                              "annuale", ANN)
    assert v["stato"] == "non_trovato"


def test_liste_vuote_in_tutte_le_categorie_dichiarate(ambiente):
    _liste(ambiente, "semestrale", {})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_coperto" and r["errore"] == "id_senza_comunicati"
    assert "VUOTE in tutte le categorie" in r["motivo"] and "altro id" in r["motivo"]


def test_stadio_2_punto_all_ordine_del_giorno_non_e_il_documento():
    """Misura offline sui PDF di M1a: due avvisi di deposito per l'assemblea, il primo cita il bilancio
    solo come PUNTO ALL'ORDINE DEL GIORNO, il secondo elenca la relazione messa a disposizione."""
    righe = [_r("1", "2026-03-06", "ACME: deposito documenti Assemblea"),
             _r("2", "2026-03-13", "ACME: deposito documenti Assemblea")]
    testi = {"https://x/1.pdf": "la relazione illustrativa sul punto 1 all'ordine del giorno, relativa al bilancio "
                                "d'esercizio 202 5 e alla destinazione degli utili",
             "https://x/2.pdf": "sono a disposizione del pubblico: Relazione finanziaria annuale 2025, comprendente il "
                                "progetto di bilancio"}
    v = em.candidati_deposito(righe, "annuale", ANN, testi_pdf=testi)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2", v["motivo"]
    assert [s["protocollo"] for s in v["scartati"]] == ["1"]


def test_non_trovato_dice_dove_ha_guardato(ambiente):
    _liste(ambiente, "trimestrale", {150: [("840101", "13/05/2026 - 18:00", "ACME: Risultati al 31 marzo 2026")]})
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31")
    assert r["stato"] == "non_trovato" and r["errore"] is None
    assert "letto: categorie 150, 109, 101, 3 pagine, 1 comunicati dopo il 2026-03-31" in r["motivo"]
    assert [s["protocollo"] for s in r["scartati"]] == ["840101"] and "RISULTATI" in r["scartati"][0]["motivo"]


# ------------------------------------------------------------ G. misure M1b (05/10)
@pytest.mark.parametrize("titolo", ["ACME SINTETICA: PROGETTO DI BILANCIO 2025",
                                    "ACME: Draft separate and consolidated financial statements at 31 December 2025"])
def test_progetto_di_bilancio_senza_deposito_e_l_approvazione(titolo):
    """M1b: un «ok» del codice di partenza era il PROGETTO di bilancio (approvazione del CdA)."""
    v = em.candidati_deposito([_r("1", "2026-03-05", titolo)], "annuale", ANN)
    assert v["stato"] == "non_trovato" and "approvazione" in v["scartati"][0]["motivo"]
    # col verbo del deposito invece e' la messa a disposizione
    v = em.candidati_deposito([_r("1", "2026-03-30", "ACME: deposito del Progetto di bilancio 2025")], "annuale", ANN)
    assert v["stato"] == "ok"


@pytest.mark.parametrize("testo", [
    "messa a disposizione la Relazione finanziaria semestrale al 3 0 giugno 20 26",
    "messa a disposizione la Relazione finanziaria semestrale al 30 giugno 2 026",
])
def test_stadio_2_cifre_spezzate_nel_giorno_e_nell_anno(testo):
    v = em.candidati_deposito([_r("1", "2026-08-05", "ACME: deposito documenti")], "semestrale", SEM,
                              testi_pdf={"https://x/1.pdf": testo})
    assert v["stato"] == "ok" and v["prova"] == "testo_pdf"


@pytest.mark.parametrize("titolo,natura", [
    ("ACME: Avviso di deposito delle Informazioni Finanziarie Periodiche al 31 marzo 2026", "messa_a_disposizione"),
    ("ACME S.p.A. approva le informazioni finanziarie periodiche consolidate non revisionate al 31 marzo 2026",
     "informazioni_nel_comunicato"),
])
def test_trimestrale_informazioni_finanziarie_periodiche_senza_aggiuntive(titolo, natura):
    v = em.candidati_deposito([_r("1", "2026-05-13", titolo)], "trimestrale", Q1)
    assert v["stato"] == "ok" and v["scelto"]["natura"] == natura
    # al 30 giugno quel nome NON e' una trimestrale (lookahead del mese)
    assert not re.search(em.DOCUMENTI_DEPOSITO["trimestrale"][0],
                         "Informazioni Finanziarie Periodiche al 30 giugno 2026", re.I)


@pytest.mark.parametrize("titolo", ["ACME: Avviso/Notice", "Avviso/notice", "ACME_Filing and Storage"])
def test_generici_misurati_m1b(titolo):
    assert [g["protocollo"] for g in em.candidati_deposito([_r("1", "2026-08-01", titolo)], "semestrale", SEM)["generici"]] == ["1"]


def test_deposit_inglese_e_un_verbo_del_deposito():
    v = em.candidati_deposito([_r("1", "2026-03-31", "ACME: Shareholders' Meeting: deposit of the Annual Financial "
                                                      "Report and additional documentation")], "annuale", ANN)
    assert v["stato"] == "non_trovato" and [g["protocollo"] for g in v["generici"]] == ["1"]


def test_avviso_senza_verbo_di_deposito_scartato():
    """Sonda S2: «Avviso RFA e pagamento dividendo» usci' 24 giorni DOPO il documento. Senza verbo di deposito
    (regola invertita, main 05/10) non e' il deposito: scartato col motivo."""
    v = em.candidati_deposito([_r("1", "2026-04-24", "ACME: Avviso Relazione Finanziaria Annuale 2025 e pagamento dividendo")],
                              "annuale", ANN)
    assert v["stato"] == "non_trovato" and "senza verbo di deposito" in v["scartati"][0]["motivo"]



def test_integrazione_informazioni_nel_comunicato_dichiarate_nei_limiti(ambiente):
    _liste(ambiente, "trimestrale", {150: [("850101", "13/05/2026 - 18:00",
                                            "ACME: il CdA approva le informazioni finanziarie periodiche aggiuntive al 31 marzo 2026")]})
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31")
    assert r["stato"] == "ok" and r["natura"] == "informazioni_nel_comunicato"
    assert any("si diffondono col comunicato che le approva" in l for l in r["limiti"])



def test_stadio_2_giorno_spezzato_trimestrale():
    """Per la trimestrale l'anno da solo non basta: il giorno spezzato («3 1 marzo») va ricucito."""
    v = em.candidati_deposito([_r("1", "2026-05-14", "ACME: deposito documenti")], "trimestrale", Q1,
                              testi_pdf={"https://x/1.pdf": "messo a disposizione il Resoconto intermedio di gestione "
                                                            "al 3 1 marzo 20 26"})
    assert v["stato"] == "ok" and v["prova"] == "testo_pdf"



# ------------------------------------------------------------ H. regola INVERTITA (main 05/10 dopo RV-IT2)
@pytest.mark.parametrize("tipo,fine,titolo,motivo", [
    # R1: risultati senza la parola «risultati» (data ANTICIPATA di 19 giorni nel caso vero)
    ("annuale", ANN, "ACME: Bilancio consolidato esercizio 2025: Ricavi in crescita, Ebitda record. Proposta di dividendo",
     "risultati"),
    # R2/R3: sigle CS / PR
    ("trimestrale", Q1, "ACME: CS_ Resoconto Intermedio di Gestione al 31 Marzo 2026", "CS, PR"),
    ("trimestrale", Q1, "PR ACME_RESOCONTO INTERMEDIO DI GESTIONE AL 31 MARZO 2026", "CS, PR"),
    # R4: la gemella EN «releases» dell'approvazione italiana
    ("trimestrale", Q1, "The Board of Directors of ACME releases the interim financial report as of March 31, 2026",
     "approvazione"),
    # S1 convocazione / esame
    ("semestrale", SEM, "ACME: convocazione del Consiglio di Amministrazione per l'esame della Relazione finanziaria "
                        "semestrale al 30 giugno 2026", "approvazione"),
    # S2 futuro
    ("semestrale", SEM, "ACME: la Relazione finanziaria semestrale al 30 giugno 2026 sara' messa a disposizione del "
                        "pubblico il 5 agosto 2026", "FUTURO"),
    ("semestrale", SEM, "ACME: the Half-year Financial Report at 30 June 2026 will be published on 5 August", "FUTURO"),
    # S3 approvazione + «disponibile» riferito ad altro
    ("semestrale", SEM, "ACME: il CdA approva la Relazione finanziaria semestrale al 30 giugno 2026; disponibile la "
                        "presentazione agli analisti", "disponibile"),
    # S4 risultati EN senza «results»
    ("semestrale", SEM, "ACME: Half-year Financial Report at 30 June 2026: revenues up, EBITDA margin stable", "risultati"),
    # S6 calendario
    ("annuale", ANN, "ACME: Calendario finanziario 2026 - Relazione finanziaria annuale 2025 il 30 marzo", "calendario"),
    # S10 nome seguito da un ALTRO anno
    ("annuale", ANN, "ACME: deposito Relazione finanziaria annuale 2024 - rif. 31/12/25", "senza il nome"),
    # nome e periodo senza alcun verbo
    ("semestrale", SEM, "ACME: Relazione finanziaria semestrale al 30 giugno 2026", "senza verbo POSITIVO"),
])
def test_senza_verbo_positivo_non_e_il_deposito(tipo, fine, titolo, motivo):
    data = {"annuale": "2026-01-20", "semestrale": "2026-07-29", "trimestrale": "2026-05-14"}[tipo]
    v = em.candidati_deposito([_r("1", data, titolo)], tipo, fine)
    assert v["stato"] == "non_trovato", (titolo, v["scelto"])
    assert motivo in v["scartati"][0]["motivo"], v["scartati"]


def test_s5_l_inglese_col_verbo_batte_l_italiano_dei_risultati():
    v = em.candidati_deposito([_r("1", "2026-07-29", "ACME: Relazione finanziaria semestrale al 30 giugno 2026 - ricavi in crescita"),
                               _r("2", "2026-08-05", "ACME: Half-year Financial Report at 30 June 2026 published")],
                              "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2"


def test_futuro_verso_il_quotidiano_non_annulla_il_deposito():
    """«Avviso di deposito ... che sara' pubblicato su Il Sole 24 Ore»: il deposito c'e', il futuro e' l'avviso."""
    v = em.candidati_deposito([_r("1", "2026-03-31", "ACME: Avviso di deposito della Relazione Finanziaria Annuale 2025 "
                                                      "che sara' pubblicato su Il Sole 24 Ore")], "annuale", ANN)
    assert v["stato"] == "ok" and v["scelto"]["natura"] == "avviso_stampa"


def test_relazione_di_revisione_sul_documento_scartata_anche_lo_stesso_giorno():
    v = em.candidati_deposito([
        dict(_r("1", "2026-08-05", "ACME: deposito relazione della societa' di revisione sul bilancio consolidato "
                                   "semestrale al 30 giugno 2026"), ora="09:00"),
        dict(_r("2", "2026-08-05", "ACME: deposito Relazione finanziaria semestrale al 30 giugno 2026"), ora="09:05")],
        "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2"
    assert "revisione" in v["scartati"][0]["motivo"]
    # «... e della Relazione della Societa' di Revisione» in coda NON esclude il deposito
    v = em.candidati_deposito([_r("1", "2026-08-04", "ACME: Comunicato di deposito della Relazione Finanziaria Semestrale "
                                                      "al 30 giugno 2026 e della Relazione della Societa' di Revisione")],
                              "semestrale", SEM)
    assert v["stato"] == "ok"


def test_stesso_giorno_vince_la_rettifica_piu_recente():
    v = em.candidati_deposito([
        dict(_r("1", "2026-08-05", "ACME: pubblicata la Relazione finanziaria semestrale al 30 giugno 2026"), ora="09:00"),
        dict(_r("2", "2026-08-05", "ACME: annulla e sostituisce - pubblicata la Relazione finanziaria semestrale al 30 "
                                   "giugno 2026"), ora="11:00")], "semestrale", SEM)
    assert v["stato"] == "ok" and v["scelto"]["protocollo"] == "2" and "rettifica" in v["note"][0]


@pytest.mark.parametrize("testo,motivo", [
    # S7 futuro nel PDF
    ("La relazione finanziaria annuale 2025 sara' messa a disposizione del pubblico entro il 30 marzo", "verbo POSITIVO"),
    # S8 verbale dell'assemblea che ha approvato il bilancio
    ("verbale dell'assemblea che ha approvato il bilancio d'esercizio 2025 e lo statuto", "verbo POSITIVO"),
    # nome + periodo SENZA verbo nel testo
    ("Relazione finanziaria annuale 2025: indice dei contenuti", "verbo POSITIVO"),
    # S13 PDF senza testo (scansione)
    ("   ", "senza testo estraibile"),
])
def test_stadio_2_regola_invertita_nel_pdf(testo, motivo):
    v = em.candidati_deposito([_r("1", "2026-03-30", "ACME: deposito documentazione assemblea")], "annuale", ANN,
                              testi_pdf={"https://x/1.pdf": testo})
    assert v["stato"] == "non_trovato" and motivo in v["scartati"][0]["motivo"]


def test_stadio_2_ricucitura_solo_dopo_mese_o_esercizio():
    """S9: «pagine 20 26» non diventa l'anno 2026."""
    righe = [_r("1", "2026-08-05", "ACME: deposito documenti")]
    v = em.candidati_deposito(righe, "semestrale", SEM, testi_pdf={
        "https://x/1.pdf": "messa a disposizione la Relazione finanziaria semestrale al 30 giugno 2025, pagine 20 26"})
    assert v["stato"] == "non_trovato"


@pytest.mark.parametrize("tipo,fine,fe,ok", [
    ("semestrale", date(2026, 10, 31), "04-30", True), ("trimestrale", date(2026, 7, 31), "04-30", True),
    ("trimestrale", date(2027, 1, 31), "04-30", True), ("annuale", date(2026, 4, 30), "04-30", True),
    ("semestrale", date(2026, 6, 30), "04-30", False), ("trimestrale", date(2026, 3, 31), "04-30", False),
    ("annuale", date(2026, 12, 31), "04-30", False), ("semestrale", date(2026, 10, 31), "4-30", False),
    ("semestrale", date(2026, 12, 31), "06-30", True), ("trimestrale", date(2026, 9, 30), "06-30", True),
])
def test_coerenza_esercizio_non_solare(tipo, fine, fe, ok):
    assert (em.coerenza_tipo_periodo(tipo, fine, fe) is None) is ok


def test_esercizio_non_solare_passa_da_get_data_deposito(ambiente):
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-07-31")
    assert r["errore"] == "parametro"
    _liste(ambiente, "trimestrale", {150: [("860101", "14/09/2026 - 10:00",
                                            "ACME: pubblicato il Resoconto intermedio di gestione al 31 luglio 2026")]})
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-07-31", fine_esercizio="04-30")
    # main 05/10 (opzione b): fine_esercizio arriva anche alla sezione Documenti; qui i documenti sono vuoti
    # (finto) e vale il comunicato, dichiarato
    assert r["errore"] != "parametro" and r["stato"] == "ok", r["motivo"]
    assert r["data_deposito"] == "2026-09-14" and r["tipo_data"] == "diffusione_comunicato"


def test_stadio_2_verbo_dopo_un_elenco_fino_a_fine_frase():
    """Misure M1b (05/10), testo sintetico: «si rende noto che la seguente documentazione: - la Relazione ... ; - ... ;
    ... e' a disposizione del pubblico». Il verbo sta oltre 400 caratteri ma nella STESSA frase."""
    elenco = "; ".join("- documento accessorio numero %d della societa' ACME SINTETICA" % i for i in range(12))
    testo = ("si rende noto che la seguente documentazione: - la Relazione finanziaria annuale al 31 dicembre 2025; "
             + elenco + " e' a disposizione del pubblico presso la sede sociale.")
    righe = [_r("1", "2026-03-30", "ACME: deposito documentazione assemblea")]
    v = em.candidati_deposito(righe, "annuale", ANN, testi_pdf={"https://x/1.pdf": testo})
    assert v["stato"] == "ok" and v["prova"] == "testo_pdf"
    # il verbo in una frase SUCCESSIVA non vale
    testo2 = ("la Relazione finanziaria annuale al 31 dicembre 2025 e' stata approvata dal CdA. Sono a disposizione "
              "del pubblico le relazioni illustrative.")
    v = em.candidati_deposito(righe, "annuale", ANN, testi_pdf={"https://x/1.pdf": testo2})
    assert v["stato"] == "non_trovato"


def test_futuro_solo_verso_il_quotidiano_non_e_un_annuncio_di_deposito():
    """Banco N04: il futuro rivolto al quotidiano non e' il deposito annunciato; senza altro verbo il motivo
    e' «senza verbo», non «futuro»."""
    v = em.candidati_deposito([_r("1", "2026-03-31", "ACME: Relazione finanziaria annuale 2025, avviso che sara' "
                                                      "pubblicato su Il Sole 24 Ore")], "annuale", ANN)
    assert v["stato"] == "non_trovato" and "FUTURO" not in v["scartati"][0]["motivo"]


def test_informazioni_periodiche_annunciate_al_futuro_scartate():
    """Banco N07: l'eccezione delle informazioni periodiche aggiuntive non copre l'annuncio al futuro."""
    v = em.candidati_deposito([_r("1", "2026-04-20", "ACME: le informazioni finanziarie periodiche aggiuntive al 31 marzo "
                                                      "2026 saranno pubblicate il 14 maggio")], "trimestrale", Q1)
    assert v["stato"] == "non_trovato" and "FUTURO" in v["scartati"][0]["motivo"]


def test_stadio_2_nome_seguito_da_un_altro_anno_nel_pdf():
    """Banco N11: nel PDF «Relazione finanziaria annuale 2024 ... al 31 dicembre 2025» e' il documento del 2024."""
    v = em.candidati_deposito([_r("1", "2026-03-30", "ACME: deposito documentazione assemblea")], "annuale", ANN,
                              testi_pdf={"https://x/1.pdf": "e' stata messa a disposizione la Relazione finanziaria "
                                                            "annuale 2024 e il prospetto al 31 dicembre 2025"})
    assert v["stato"] == "non_trovato"


def test_titolo_nome_e_periodo_lontani_non_valgono():
    """Banco N12: nel titolo nome e periodo devono stare entro 80 caratteri."""
    lungo = ("ACME: pubblicata la Relazione finanziaria semestrale; si ricorda inoltre agli azionisti l'assemblea "
             "straordinaria e le modalita' di partecipazione previste, convocata per il 30 giugno 2026")
    v = em.candidati_deposito([_r("1", "2026-08-05", lungo)], "semestrale", SEM)
    assert v["stato"] == "non_trovato"


def test_integrazione_pdf_senza_testo_dichiarato_nei_limiti(ambiente):
    """Banco N17: un PDF da cui non si estrae testo (scansione) e' dichiarato in `limiti`."""
    _liste(ambiente, "semestrale", {101: [("870101", "05/08/2026 - 10:00", "ACME S.p.A.: deposito documenti")]})
    ambiente["risposte"][_pdf_url("870101")] = (200, _pdf(""))
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "non_trovato"
    assert any("senza testo estraibile" in l for l in r["limiti"])
    assert "senza testo estraibile" in r["scartati"][0]["motivo"]



# ------------------------------------------------------------ I. nomi trimestrali EN (casi MF 05/10, fonte unica dei nomi)
@pytest.mark.parametrize("fine,titolo", [
    (Q1, "ACME: Interim statement as at 31 March 2026 published"),
    (Q1, "ACME: First Quarter 2026 report published"),
    (Q1, "ACME: Q1 2026 report filed"),
    (Q3, "ACME: Third Quarter 2026 report published"),
    (Q3, "ACME: 3Q 2026 report made available"),
])
def test_nomi_trimestrali_en_col_verbo(fine, titolo):
    data = "2026-05-14" if fine == Q1 else "2026-11-12"
    v = em.candidati_deposito([_r("1", data, titolo)], "trimestrale", fine)
    assert v["stato"] == "ok" and v["scelto"]["lingua"] == "en", (titolo, v["scartati"])


@pytest.mark.parametrize("tipo,fine,titolo", [
    # senza verbo di deposito: regola invertita invariata
    ("trimestrale", Q1, "ACME: First Quarter 2026 report"),
    # «interim statement» legato al mese: al 30 giugno non e' una trimestrale
    ("trimestrale", Q1, "ACME: Interim statement at 30 June 2026 published"),
    # il trimestre nel nome e' di un altro anno
    ("trimestrale", Q1, "ACME: First Quarter 2025 report published"),
])
def test_nomi_trimestrali_en_non_sconfinano(tipo, fine, titolo):
    v = em.candidati_deposito([_r("1", "2026-05-14", titolo)], tipo, fine)
    assert v["stato"] == "non_trovato", (titolo, v["scelto"])


def test_interim_statement_al_30_giugno_non_e_un_tipo():
    """Contratto D4: «interim statement» decide il tipo solo col mese (marzo/settembre)."""
    assert not any(re.search(rx, "Interim statement at 30 June 2026", re.I)
                   for coppia in em.DOCUMENTI_DEPOSITO.values() for rx in coppia)
    m = re.search(em.DOCUMENTI_DEPOSITO["trimestrale"][1], "Interim statement as at 31 March 2026", re.I)
    assert m and m.group(0) == "Interim statement"



# ------------------------------------------------------------ L. nomi italiani misurati da IT3 su S2 (fonte unica dei nomi)
@pytest.mark.parametrize("tipo,fine,data,titolo", [
    ("annuale", ANN, "2026-04-02", "ACME - Assemblea soci 2026 - depositata la Bozza bilancio 2025"),
    ("annuale", ANN, "2026-04-02", "ACME: pubblicato il Bilancio al 31.12.2025"),
    ("trimestrale", Q1, "2026-05-07", "ACME: pubblicata la Trimestrale al 31 marzo 2026"),
    ("trimestrale", Q1, "2026-05-14", "ACME_Relazione Intermedio di gestione al 31 marzo 2026 pubblicata"),
    ("trimestrale", date(2025, 9, 30), "2025-11-12", "ACME: pubblicata la Relazione finanziaria III Trimestre 2025"),
    ("trimestrale", Q1, "2026-05-14", "ACME: pubblicata la Relazione finanziaria I trimestre 2026"),
])
def test_nomi_italiani_misurati_col_verbo(tipo, fine, data, titolo):
    v = em.candidati_deposito([_r("1", data, titolo)], tipo, fine)
    assert v["stato"] == "ok" and v["scelto"]["lingua"] == "it", (titolo, v["scartati"])


@pytest.mark.parametrize("tipo,fine,titolo,motivo", [
    # la BOZZA di bilancio senza verbo di deposito e' l'approvazione, come il progetto
    ("annuale", ANN, "ACME: Bozza bilancio 2025", "approvazione"),
    # «Trimestrale» con risultati e senza verbo: non e' il deposito
    ("trimestrale", Q1, "ACME: Trimestrale al 31 marzo 2026: ricavi in crescita", "risultati"),
    # la relazione intermedia DI GESTIONE e' trimestrale solo a marzo/settembre
    ("trimestrale", Q1, "ACME: pubblicata la Relazione intermedia di gestione al 30 giugno 2026", None),
])
def test_nomi_italiani_misurati_non_sconfinano(tipo, fine, titolo, motivo):
    v = em.candidati_deposito([_r("1", "2026-07-20", titolo)], tipo, fine)
    assert v["stato"] == "non_trovato", (titolo, v["scelto"])
    if motivo:
        assert motivo in v["scartati"][0]["motivo"]


def test_relazione_intermedia_di_gestione_e_trimestrale_solo_con_marzo_o_settembre():
    """Banco K04: il nome trimestrale «relazione intermedi[oa] di gestione» e' legato al mese (contratto D4)."""
    tri = em.DOCUMENTI_DEPOSITO["trimestrale"][0]
    assert not re.search(tri, "Relazione intermedia di gestione al 30 giugno 2026", re.I)
    m = re.search(tri, "Relazione Intermedio di gestione al 31 marzo 2026", re.I)
    assert m and m.group(0) == "Relazione Intermedio di gestione"

def test_url_vietato_dalla_lista_motivo_controllato_senza_il_testo_dell_eccezione(monkeypatch):
    """VF 05/10 (regola 16): il messaggio di URLVietato puo' contenere l'URL; nel motivo va un testo
    controllato (tipo dell'eccezione + motivo fisso), lo stato resta KO/url_vietato."""
    monkeypatch.setattr(em, "PAUSA_S", 0)

    def vietato(url):
        raise bi.URLVietato("redirect verso https://zz-host-finto.example/x?token=QQSEGRETO: host non previsto")
    monkeypatch.setattr(bi, "_scarica", vietato)
    out = em._leggi_deposito("ACME.MI", "ITZZACME0007", ID, "semestrale", SEM)
    assert (out["stato"], out["errore"]) == ("KO", "url_vietato")
    assert "QQSEGRETO" not in out["motivo"] and "zz-host-finto" not in out["motivo"]
    assert "URLVietato" in out["motivo"] and "categoria" in out["motivo"] and "pagina 1" in out["motivo"]
