# -*- coding: utf-8 -*-
"""get_data_deposito con la sezione DOCUMENTI di eMarket come ancora primaria (handoff-3, IT2b, 05/10/2026,
Opus 5.5; decisione main dopo la sonda S2). Documento = data di STOCCAGGIO (tipo_data
'stoccaggio_documento'), comunicato = conferma (scarto in giorni) o ripiego dichiarato
('diffusione_comunicato'); una sezione in KO = KO. Nessuna rete: emittente «ACME SINTETICA», protocolli e
date inventati; la scelta del documento e' quella VERA di emarket_documenti (IT3), solo la lettura di rete
e' finta, tranne nel test di cablaggio che passa da leggi_documenti vero sulle fixture sintetiche di IT3.
"""
import json
import os
from datetime import date

import pytest

from bellomberg.market_data import borsa_italiana as bi
from bellomberg.market_data import emarket_documenti as ed
from bellomberg.market_data import emarket_sdir as em

ID = 4242
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "fonti_it")


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


def _vuota(id_em=ID):
    return ('<html><body><form><select name="categoria"><option value="All">Categoria</option></select>'
            '<select name="azienda"><option value="%d">ACME SINTETICA</option></select></form></body></html>'
            % id_em).encode("utf-8")


def _doc(prot, data, titolo, esef=False, categorie=(), lingua="it", ora="18:00"):
    ext = "zip" if esef else "pdf"
    cartella = "xbrl" if esef else "comunicati"
    return {"data": data, "ora": ora, "titolo": titolo, "protocollo": prot, "esef": esef, "lingua": lingua,
            "url": em.BASE + "/sites/default/files/%s/%s/sint_%s.%s" % (cartella, data[:7], prot, ext),
            "categoria": categorie[0] if categorie else None, "categorie": list(categorie)}


def _docs(stato="ok", righe=(), errore=None):
    return {"stato": stato, "errore": errore, "motivo": "finto" if stato != "ok" else None, "righe": list(righe),
            "url_liste": ["https://www.emarketstorage.it/it/documenti?azienda=%d&finto" % ID],
            "sha256_liste": {"https://www.emarketstorage.it/it/documenti?azienda=%d&finto" % ID: "0" * 64},
            "pagine_lette": 1, "limiti": []}


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"ACME.MI": {"isin": "ITZZACME0007", "emarket": ID},
                                   "SINT.MI": {"isin": "ITZZSINT0003", "emarket": 900}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 4))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    monkeypatch.setattr(ed, "PAUSA_S", 0)
    risposte, chieste, chiamate_doc = {}, [], []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        assert ok, motivo
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta")
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    amb = {"risposte": risposte, "chieste": chieste, "chiamate_doc": chiamate_doc, "docs": _docs("vuoto_misurato")}

    def leggi_finto(id_em, **k):
        chiamate_doc.append((id_em, k))
        return amb["docs"]
    amb["leggi_finto"] = leggi_finto
    monkeypatch.setattr(ed, "leggi_documenti", leggi_finto)
    return amb


def _comunicati(amb, tipo, per_cat):
    for c in em.CATEGORIE_DEPOSITO[tipo]:
        url = em.URL_CATEGORIA.format(cat=c, id=ID)
        amb["risposte"][url] = (200, _lista(per_cat[c]) if c in per_cat else _vuota())


ANNUALE_DOC = [_doc("910101", "2026-03-20", "ACME SINTETICA - Relazione finanziaria annuale 2025 (ESEF)", esef=True),
               _doc("910102", "2026-03-20", "Relazione della societa' di revisione sulla Relazione finanziaria annuale 2025")]


def test_documento_ancora_primaria_comunicato_conferma(ambiente):
    ambiente["docs"] = _docs("ok", ANNUALE_DOC)
    _comunicati(ambiente, "annuale", {150: [("920101", "23/03/2026 - 10:00",
                                             "ACME: pubblicata la Relazione finanziaria annuale 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok", r["motivo"]
    assert (r["data_deposito"], r["protocollo"], r["tipo_data"], r["prova"], r["prova_documento"]) == \
        ("2026-03-20", "910101", "stoccaggio_documento", "documento", "esef")
    assert r["documento"]["esef"] is True and r["natura"] == "stoccaggio_documento"
    assert r["fuso"] == "Europe/Rome"
    c = r["comunicato_conferma"]
    assert (c["stato"], c["data"], c["protocollo"], c["scarto_giorni"]) == ("ok", "2026-03-23", "920101", 3)
    assert any("scarto +3 giorni" in l for l in r["limiti"]), r["limiti"]
    # ricevuta: liste dei documenti E dei comunicati
    assert r["url_liste_documenti"] and set(r["sha256_liste_documenti"]) == set(r["url_liste_documenti"])
    assert r["url_liste"] and set(r["sha256_liste"]) == set(r["url_liste"])
    assert any("revisione" in s["motivo"] for s in r["scartati"])
    # finestra e categorie passate a leggi_documenti
    (id_em, k), = ambiente["chiamate_doc"]
    assert id_em == ID and (k["data_da"], k["data_a"]) == ("2026-01-01", "2026-05-30")
    assert tuple(k["categorie"]) == ()


def test_semestrale_chiede_la_categoria_101(ambiente):
    _comunicati(ambiente, "semestrale", {})
    em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-06-30")
    (_, k), = ambiente["chiamate_doc"]
    assert tuple(k["categorie"]) == (ed.CATEGORIA_SEMESTRALE,)


def test_documento_trovato_i_pdf_dei_comunicati_generici_non_si_leggono(ambiente):
    ambiente["docs"] = _docs("ok", ANNUALE_DOC)
    _comunicati(ambiente, "annuale", {150: [("920201", "23/03/2026 - 10:00", "ACME S.p.A.: deposito documenti")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["richieste"]["pdf"] == 0
    assert not any(u.endswith(".pdf") for u in ambiente["chieste"])
    assert r["comunicato_conferma"]["stato"] == "non_trovato" and r["comunicato_conferma"]["scarto_giorni"] is None
    assert any("NON letti nel PDF" in l for l in r["limiti"])
    assert any("nessun comunicato di conferma" in l for l in r["limiti"])


def test_documento_assente_vale_il_comunicato_dichiarato(ambiente):
    _comunicati(ambiente, "annuale", {150: [("920301", "23/03/2026 - 10:00",
                                             "ACME: pubblicata la Relazione finanziaria annuale 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-03-23" and r["prova"] == "titolo"
    assert r["tipo_data"] == "diffusione_comunicato" and r["stato_documenti"] == "non_trovato"
    assert any("documento non stoccato nella sezione Documenti" in l for l in r["limiti"])


def test_mancano_entrambi_non_trovato_cita_le_due_sezioni(ambiente):
    _comunicati(ambiente, "annuale", {150: [("920401", "13/03/2026 - 10:00", "ACME: risultati dell'esercizio 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "non_trovato" and r["data_deposito"] is None and r["tipo_data"] is None
    assert "ne' nella sezione Documenti" in r["motivo"] and "fra i comunicati" in r["motivo"]


@pytest.mark.parametrize("stato,errore", [("KO", "rete"), ("KO", "filtro_ignorato"), ("STALE", None),
                                          ("non_coperto", "id_non_nel_menu")])
def test_sezione_documenti_non_letta_KO_senza_verdetto(ambiente, stato, errore):
    ambiente["docs"] = _docs(stato, ANNUALE_DOC if stato == "STALE" else (), errore)
    _comunicati(ambiente, "annuale", {150: [("920501", "23/03/2026 - 10:00",
                                             "ACME: pubblicata la Relazione finanziaria annuale 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "KO" and r["data_deposito"] is None and r["errore"].startswith("documenti_")
    assert "sezione Documenti" in r["motivo"]
    assert ambiente["chieste"] == []          # i comunicati non si leggono nemmeno


def test_documento_ambiguo_resta_ambiguo(ambiente):
    ambiente["docs"] = _docs("ok", [
        _doc("910601", "2026-04-10", "ACME SINTETICA: Relazione finanziaria annuale 2025"),
        _doc("910602", "2026-05-18", "ACME SINTETICA: Relazione finanziaria annuale 2025")])
    _comunicati(ambiente, "annuale", {150: [("920601", "10/04/2026 - 10:00",
                                             "ACME: pubblicata la Relazione finanziaria annuale 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ambiguo" and r["data_deposito"] is None and r["prova"] == "documento"
    assert sorted(c["protocollo"] for c in r["candidati"]) == ["910601", "910602"]
    assert r["comunicato_conferma"]["data"] == "2026-04-10" and "sezione Documenti" in r["motivo"]


def test_comunicati_in_KO_restano_KO_anche_col_documento(ambiente):
    ambiente["docs"] = _docs("ok", ANNUALE_DOC)
    _comunicati(ambiente, "annuale", {})
    ambiente["risposte"][em.URL_CATEGORIA.format(cat=100, id=ID)] = (500, b"")
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "http" and r["url_liste_documenti"]


def test_periodo_non_concluso_documenti_non_letti(ambiente):
    _comunicati(ambiente, "semestrale", {101: [("920701", "05/08/2026 - 10:00", "ACME: altro comunicato")]})
    r = em.get_data_deposito("ACME.MI", tipo="semestrale", periodo_fine="2026-12-31")
    assert r["stato"] == "non_trovato" and ambiente["chiamate_doc"] == []
    assert "sezione Documenti non letta" in r["motivo"]


def test_ok_dal_documento_in_cache(ambiente):
    ambiente["docs"] = _docs("ok", ANNUALE_DOC)
    _comunicati(ambiente, "annuale", {})
    a = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    n = len(ambiente["chieste"])
    b = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert a["stato"] == "ok" and b["cache"]["stato"] == "fresca" and len(ambiente["chieste"]) == n
    assert b["tipo_data"] == "stoccaggio_documento" and b["data_deposito"] == "2026-03-20"


@pytest.fixture
def reale(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({"SINT.MI": {"isin": "ITZZSINT0003", "emarket": 900}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 11, 5))
    monkeypatch.setattr(em, "PAUSA_S", 0)
    monkeypatch.setattr(ed, "PAUSA_S", 0)
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


# URL attesi SCRITTI A MANO (review RV-IT3: niente oracolo dalle funzioni sotto test). data_to del sito e'
# ESCLUSIVO: per leggere fino al 28/10 incluso l'URL porta 2026-10-29.
URL_DOC = ("https://www.emarketstorage.it/it/documenti?azienda=900&data_from=2026-07-01&data_to=2026-10-29")


def _pagine_reali(reale, sposta_al=None):
    with open(os.path.join(FIX, "emd_semestrale.html"), "rb") as fh:
        tutte = fh.read()
    with open(os.path.join(FIX, "emd_semestrale_cat101.html"), "rb") as fh:
        c101 = fh.read()
    if sposta_al:      # il documento stoccato nell'ULTIMO giorno della finestra (data_a)
        # tutte le righe della fixture dopo il periodo (06/08 e 09/09) vanno all'ultimo giorno della finestra
        tutte, c101 = (x.replace(b"06/08/2026", sposta_al.encode()).replace(b"09/09/2026", sposta_al.encode())
                       for x in (tutte, c101))
    reale["risposte"][URL_DOC] = (200, tutte)
    reale["risposte"][URL_DOC + "&categoria=101"] = (200, c101)
    for c in em.CATEGORIE_DEPOSITO["semestrale"]:
        reale["risposte"][em.URL_CATEGORIA.format(cat=c, id=900)] = (200, _vuota(900))


def test_cablaggio_con_leggi_documenti_vero(reale):
    """Il leggi_documenti VERO (IT3) dietro get_data_deposito, sulle fixture sintetiche di IT3 (id 900):
    gli URL chiesti sono quelli della sezione Documenti, con la categoria 101 per la semestrale. Atteso a mano:
    la relazione semestrale della fixture, protocollo 900208, stoccata il 06/08/2026 alle 06:35."""
    _pagine_reali(reale)
    r = em.get_data_deposito("SINT.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["tipo_data"] == "stoccaggio_documento", r["motivo"]
    assert (r["data_deposito"], r["ora_deposito"], r["protocollo"]) == ("2026-08-06", "06:35", "900208")
    assert r["fuso"] == "Europe/Rome"
    assert URL_DOC in reale["chieste"] and URL_DOC + "&categoria=101" in reale["chieste"]
    assert set(r["url_liste_documenti"]) == {URL_DOC, URL_DOC + "&categoria=101"}
    # le liste dei comunicati sono vuote ovunque: il documento vale lo stesso, il limite lo dice
    assert r["comunicato_conferma"]["stato"] == "non_coperto"


def test_documento_stoccato_nell_ultimo_giorno_della_finestra(reale, monkeypatch):
    """RV-IT3 P1: data_to del sito esclusivo. Oggi = 28/10 = data_a: il documento stoccato OGGI si vede."""
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 28))
    _pagine_reali(reale, sposta_al="28/10/2026")
    r = em.get_data_deposito("SINT.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert r["stato"] == "ok" and r["tipo_data"] == "stoccaggio_documento", r["motivo"]
    assert (r["data_deposito"], r["protocollo"]) == ("2026-10-28", "900208")


def test_ora_esef_piu_tarda_del_pdf_dichiarata(ambiente):
    """RV-IT3 P3: con l'ESEF l'ora e' quella dell'ESEF (istante piu' tardo, prudente); se un PDF dello stesso
    giorno e' piu' antico lo si dichiara."""
    ambiente["docs"] = _docs("ok", [
        _doc("910701", "2026-03-20", "ACME SINTETICA - Relazione finanziaria annuale 2025 (ESEF)", esef=True, ora="17:51"),
        _doc("910702", "2026-03-20", "ACME SINTETICA - Relazione finanziaria annuale 2025", ora="17:46")])
    _comunicati(ambiente, "annuale", {})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["ora_deposito"] == "17:51"
    assert any("17:46" in l and "piu' antica" in l for l in r["limiti"]), r["limiti"]


def test_ok_dal_comunicato_porta_il_fuso(ambiente):
    _comunicati(ambiente, "annuale", {150: [("920801", "23/03/2026 - 10:00",
                                             "ACME: pubblicata la Relazione finanziaria annuale 2025")]})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["tipo_data"] == "diffusione_comunicato" and r["fuso"] == "Europe/Rome"


def test_chiave_di_cache_nuova_non_riusa_i_verdetti_della_regola_vecchia(ambiente, monkeypatch):
    """Banco I10: gli ok in cache della regola vecchia (solo comunicati, senza documento) non si riusano."""
    chiavi = []
    vera = bi.con_cache

    def spia(chiave, ttl, f):
        chiavi.append(chiave)
        return vera(chiave, ttl, f)
    monkeypatch.setattr(bi, "con_cache", spia)
    _comunicati(ambiente, "annuale", {})
    em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert chiavi == ["emarket_deposito_v2_%d_annuale_2025-12-31" % ID]


@pytest.mark.parametrize("tipo,fine,da,a,titolo", [
    ("trimestrale", "2026-07-31", "2026-08-01", "2026-10-04",
     "ACME SINTETICA - Resoconto intermedio di gestione al 31 luglio 2026"),
    ("semestrale", "2025-10-31", "2025-11-01", "2026-02-28",
     "ACME SINTETICA - Relazione finanziaria semestrale al 31 ottobre 2025"),
])
def test_esercizio_non_solare_arriva_ai_documenti(ambiente, tipo, fine, da, a, titolo):
    """main 05/10 (opzione b): esercizio che chiude il 30/04. fine_esercizio passa a emarket_documenti
    (finestra e scelta): trimestrale al 31/07 e semestrale al 31/10 arrivano alla sezione Documenti."""
    data_doc = "2026-09-14" if tipo == "trimestrale" else "2025-12-15"
    ambiente["docs"] = _docs("ok", [_doc("911001", data_doc, titolo)])
    _comunicati(ambiente, tipo, {})
    r = em.get_data_deposito("ACME.MI", tipo=tipo, periodo_fine=fine, fine_esercizio="04-30")
    assert r["stato"] == "ok" and r["tipo_data"] == "stoccaggio_documento", r["motivo"]
    assert (r["data_deposito"], r["protocollo"]) == (data_doc, "911001")
    (_, k), = ambiente["chiamate_doc"]
    assert (k["data_da"], k["data_a"]) == (da, a)   # finestra scritta a mano (oggi = 04/10/2026)


def test_esercizio_non_solare_periodo_incoerente_KO_parametro(ambiente):
    """Con l'esercizio al 30/04 la trimestrale al 31/03 e' incoerente: KO 'parametro' prima di ogni rete."""
    r = em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-03-31", fine_esercizio="04-30")
    assert r["stato"] == "KO" and r["errore"] == "parametro"
    assert ambiente["chieste"] == [] and ambiente["chiamate_doc"] == []


def test_fine_esercizio_nella_chiave_di_cache(ambiente, monkeypatch):
    chiavi = []
    vera = bi.con_cache
    monkeypatch.setattr(bi, "con_cache", lambda c, t, f: chiavi.append(c) or vera(c, t, f))
    _comunicati(ambiente, "trimestrale", {})
    em.get_data_deposito("ACME.MI", tipo="trimestrale", periodo_fine="2026-07-31", fine_esercizio="04-30")
    assert chiavi == ["emarket_deposito_v2_%d_trimestrale_2026-07-31_fe04-30" % ID]


def test_candidati_documento_che_solleva_diventa_KO_parametro(ambiente, monkeypatch):
    ambiente["docs"] = _docs("ok", ANNUALE_DOC)

    def solleva(*a, **k):
        raise ed.ParametroDocumenti("finto")
    monkeypatch.setattr(ed, "candidati_documento", solleva)
    _comunicati(ambiente, "annuale", {})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and "ParametroDocumenti" in r["motivo"]
    assert "finto" not in r["motivo"]          # mai il testo dell'eccezione nel payload


def test_fuso_preso_dalla_riga_del_documento(ambiente):
    riga = dict(ANNUALE_DOC[0], fuso="Europe/Rome")
    ambiente["docs"] = _docs("ok", [riga])
    _comunicati(ambiente, "annuale", {})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "ok" and r["fuso"] == "Europe/Rome" and r["documento"]["fuso"] == "Europe/Rome"


def test_finestra_che_solleva_diventa_KO_parametro(ambiente, monkeypatch):
    """Guardia difensiva: se la sezione Documenti rifiuta un tipo/periodo che emarket_sdir accetta, KO 'parametro'
    dichiarato (mai un crash del tool, mai il testo dell'eccezione)."""
    def solleva(*a, **k):
        raise ed.ParametroDocumenti("finto")
    monkeypatch.setattr(ed, "finestra_documenti", solleva)
    _comunicati(ambiente, "annuale", {})
    r = em.get_data_deposito("ACME.MI", tipo="annuale", periodo_fine="2025-12-31")
    assert r["stato"] == "KO" and r["errore"] == "parametro" and "ParametroDocumenti" in r["motivo"]
    assert "finto" not in r["motivo"] and ambiente["chieste"] == []
