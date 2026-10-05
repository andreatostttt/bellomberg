# -*- coding: utf-8 -*-
"""borsa_italiana.risolvi_isin per TUTTI gli emittenti (handoff-3, IT1b, 05/10/2026, Opus 5.5).

Nessuna rete: `_scarica` e' un finto server. Le pagine del Listino A-Z e le schede sono
COSTRUITE qui con nomi, ISIN e codici INVENTATI, ma con la STRUTTURA misurata il 05/10 su 8
pagine vere (scratchpad IT1b): ogni pagina linka SE STESSA (link assoluto con &amp;, link
'lang=en', link di ordinamento ord=), la finestra del pager mostra le pagine vicine, oltre
l'ultima pagina il sito RISERVE l'ultima (e il pager non linka la successiva), nel listino
convivono MTAA, EXGM e BGEM (azioni estere), la classe dell'azione sta nel nome della riga.
"""
import json
from html import escape

import pytest

from bellomberg.market_data import borsa_italiana as bi

SEGMENTO = {"MTAA": "", "EXGM": "euronext-growth-milan/", "BGEM": "global-equity-market/"}


def isin(base):
    """ISIN inventato (11 caratteri) + cifra di controllo valida."""
    for c in "0123456789":
        if bi.isin_valido(base + c):
            return base + c
    raise AssertionError(base)


def scheda_url(codice_isin, mic="MTAA"):
    return bi.BASE + "/borsa/azioni/%sscheda/%s-%s.html?lang=it" % (SEGMENTO[mic], codice_isin, mic)


def listino_url(iniziale, pagina=1):
    return bi.URL_LISTINO.format(iniziale=iniziale) + ("&page=%d" % pagina if pagina > 1 else "")


def pagina_listino(iniziale, righe, pagina=1, finestra=()):
    """righe: [(nome, isin, mic)]; finestra: numeri di pagina linkati dal pager (oltre al link a
    se stessa, che c'e' SEMPRE, come sul sito)."""
    corpo = []
    for nome, cod, mic in righe:
        href = "/borsa/azioni/%sscheda/%s-%s.html?lang=it" % (SEGMENTO[mic], cod, mic)
        t = escape(nome, quote=True)
        corpo.append(
            '<tr><td><article class="u-hidden -sm -md"><a href="%s" title="Accedi alla scheda strumento&nbsp;%s">'
            '<span class="t-text"><strong>%s</strong></span></a></article>\n'
            '<a class="u-hidden -xs" href="%s" title="Accedi alla scheda strumento&nbsp;%s"><span class="t-text">%s'
            '<span class="t-text--market"><br>Mercato</span></span></a></td></tr>' % (href, t, t, href, t, t))
    q = "initial=%s&lang=it" % iniziale + ("&page=%d" % pagina if pagina > 1 else "")
    qa = q.replace("&", "&amp;")
    link = ['<link rel="canonical" href="https://www.borsaitaliana.it/borsa/azioni/listino-a-z.html?%s">' % qa,
            '<a href="https://www.borsaitaliana.it/borsa/azioni/listino-a-z.html?%s">IT</a>' % qa,
            '<a href="https://www.borsaitaliana.it/borsa/azioni/listino-a-z.html?%s">EN</a>'
            % qa.replace("lang=it", "lang=en"),
            '<a href="/borsa/azioni/listino-a-z.html?%s&ord=anag&mod=down">Nome</a>' % q]
    for n in finestra:
        link.append('<li><a href="/borsa/azioni/listino-a-z.html?initial=%s&lang=it&page=%d">%d</a></li>'
                    % (iniziale, n, n))
    lettere = "".join('<a href="listino-a-z.html?initial=%s&lang=it">%s</a>' % (c, c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>%s</head><body>%s<table>%s</table>"
            "<ul class='pager'>%s</ul></body></html>" % (link[0], lettere, "\n".join(corpo), "".join(link[1:]))
            ).encode("utf-8")


def pagina_scheda(codice_isin, codice):
    return ("<html><body><table>"
            "<tr><td><span class='t-text'><strong>Codice Isin</strong></span></td>"
            "<td><span class='t-text -right'>\n\t\t%s\n</span></td></tr>"
            "<tr><td><span class='t-text'><strong>Codice Alfanumerico</strong></span></td>"
            "<td><span class='t-text -right'>\n\t\t%s\n</span></td></tr>"
            "</table></body></html>" % (codice_isin, codice)).encode("utf-8")


def menu_emarket(opzioni):
    o = "".join('<option value="%d">%s</option>' % (i, escape(t)) for i, t in opzioni)
    return ('<html><body><select id="edit-azienda" name="azienda"><option value=""></option>%s</select>'
            '</body></html>' % o).encode("utf-8")


@pytest.fixture
def srv(monkeypatch, tmp_path):
    negozio = tmp_path / "isin_it.json"
    negozio.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(negozio))
    monkeypatch.setattr(bi, "PERCORSO_ISIN_AUTO", str(tmp_path / "isin_it_auto.json"))
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "PAUSA_S", 0)
    risposte, chieste = {bi.URL_MENU_EMARKET: (200, menu_emarket([(7001, "ALTRO EMITTENTE")]))}, []

    def finto(url):
        ok, motivo = bi.url_consentito(url)
        assert ok, motivo
        chieste.append(url)
        r = risposte.get(url)
        if r is None:
            raise ConnectionError("nessuna risposta finta")
        if isinstance(r, Exception):
            raise r
        return r[0], r[1], url
    monkeypatch.setattr(bi, "_scarica", finto)
    return {"risposte": risposte, "chieste": chieste, "auto": tmp_path / "isin_it_auto.json"}


def servi_lettera(srv, iniziale, pagine):
    """pagine: [[righe pagina 1], [righe pagina 2], ...]; il pager linka tutte le altre pagine e
    ogni pagina linka se stessa; oltre l'ultima (ultima+1) il sito riserve l'ultima."""
    n = len(pagine)
    for i, righe in enumerate(pagine, start=1):
        srv["risposte"][listino_url(iniziale, i)] = (
            200, pagina_listino(iniziale, righe, i, [k for k in range(1, n + 1) if k != i]))
    srv["risposte"][listino_url(iniziale, n + 1)] = (
        200, pagina_listino(iniziale, pagine[-1], n + 1, list(range(1, n))))


def servi_schede(srv, righe_codici):
    for cod, mic, codice in righe_codici:
        srv["risposte"][scheda_url(cod, mic)] = (200, pagina_scheda(cod, codice))


# ---------------------------------------------------------------- 0. la funzione e' quella VERA
def test_risolvi_isin_e_la_funzione_vera_del_modulo():
    """I finti autouse del conftest (W1) non devono coprire la funzione che questi test provano."""
    assert bi.risolvi_isin.__module__ == bi.__name__
    assert bi.risolvi_isin.__code__.co_filename == bi.__file__
    assert bi.risolvi_isin.__name__ == "risolvi_isin"


# ---------------------------------------------------------------- A. paginazione del listino
RIGA_A = [("Alfa Finta", isin("ITZZALFA000"), "MTAA")]


def test_pagina_che_linka_solo_se_stessa_non_ha_successiva():
    """Misura 05/10: la pagina linka SE STESSA (assoluto &amp;, en, ord=). La vecchia regola
    (qualunque page=) la prendeva per «pagina successiva»."""
    html = pagina_listino("A", RIGA_A, pagina=3, finestra=[1, 2]).decode()
    assert "page=3" in html
    assert bi.parse_listino(html, "A", 3)["pagina_successiva"] is False


def test_pagina_con_link_alla_successiva():
    html = pagina_listino("A", RIGA_A, pagina=2, finestra=[1, 3, 4]).decode()
    assert bi.parse_listino(html, "A", 2)["pagina_successiva"] is True
    html1 = pagina_listino("A", RIGA_A, pagina=1, finestra=[2, 3, 4, 5, 6]).decode()
    assert bi.parse_listino(html1, "A", 1)["pagina_successiva"] is True


def test_pagina_ventitre_non_vale_come_pagina_due():
    """page=23 non e' page=2 (cifra dopo il numero esclusa)."""
    html = pagina_listino("A", RIGA_A, pagina=1, finestra=[23]).decode()
    assert bi.parse_listino(html, "A", 1)["pagina_successiva"] is False


def test_link_di_un_altra_lettera_non_conta():
    html = pagina_listino("B", RIGA_A, pagina=1, finestra=[2]).decode()
    assert bi.parse_listino(html, "A", 1)["pagina_successiva"] is False


def test_pagina_oltre_l_ultima_non_linka_la_successiva():
    """Come sul sito: richiesta pagina 5 di 4, torna l'ultima e il pager linka 1..3 (+ se stessa)."""
    html = pagina_listino("A", RIGA_A, pagina=5, finestra=[1, 2, 3]).decode()
    assert bi.parse_listino(html, "A", 5)["pagina_successiva"] is False


def _lettera_lunga(prefisso="A"):
    """4 pagine sintetiche ordinate, 3 righe ciascuna."""
    nomi = [["Abaco", "Abete", "Acero"], ["Acme Finta", "Adige", "Agata"],
            ["Agrifoglio", "Alce Sintetica", "Aloe"], ["Alto", "Ambra", "Azalea"]]
    pagine = []
    for k, gruppo in enumerate(nomi):
        pagine.append([(n, isin("ITZZLUN%d%03d" % (k, j)), "MTAA") for j, n in enumerate(gruppo)])
    return pagine


def _isin_lunga(nome):
    for k, gruppo in enumerate(_lettera_lunga()):
        for n, cod, mic in gruppo:
            if n == nome:
                return cod
    raise KeyError(nome)


def test_listino_lungo_si_ferma_quando_le_righe_superano_il_nome(srv):
    servi_lettera(srv, "A", _lettera_lunga())
    cod = _isin_lunga("Alce Sintetica")
    servi_schede(srv, [(cod, "MTAA", "ALCS")])
    r = bi.risolvi_isin("ALCS.MI", nome="Alce Sintetica S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == cod, r["motivo"]
    # pagina 3 finisce con ALOE, oltre ALCE: la 4 non serve
    assert r["verifica"]["pagine_listino"] == [listino_url("A", 1), listino_url("A", 2), listino_url("A", 3)]


def test_listino_lungo_nome_sull_ultima_pagina(srv):
    servi_lettera(srv, "A", _lettera_lunga())
    cod = _isin_lunga("Azalea")
    servi_schede(srv, [(cod, "MTAA", "AZL")])
    r = bi.risolvi_isin("AZL.MI", nome="Azalea")
    assert r["stato"] == "ok" and r["isin"] == cod
    assert len(r["verifica"]["pagine_listino"]) == 4      # la 4 non linka la 5: fine, nessuna richiesta in piu'
    assert listino_url("A", 5) not in srv["chieste"]


def test_sito_che_riserve_l_ultima_pagina_e_la_linka_comunque(srv):
    """Caso peggiore: oltre l'ultima il sito riserve l'ultima E il pager linka ancora la
    successiva. Nessuna riga nuova = fine del listino, dichiarata; mai un giro di 10 pagine."""
    pagine = _lettera_lunga()
    servi_lettera(srv, "A", pagine)
    for k in (4, 5):   # le pagine 4 e 5 linkano la successiva
        srv["risposte"][listino_url("A", k)] = (200, pagina_listino("A", pagine[-1], k, [1, 2, 3, k + 1]))
    r = bi.risolvi_isin("ZZQ.MI", nome="Azzurro")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_nel_listino"
    assert srv["chieste"][-1] == listino_url("A", 5) and listino_url("A", 6) not in srv["chieste"]
    assert any("senza righe nuove" in x for x in r["verifica"]["limiti"])


def test_listino_troncato_e_verdetto_sospeso_non_nome_assente(srv, monkeypatch):
    monkeypatch.setattr(bi, "MAX_PAGINE_LISTINO", 2)
    servi_lettera(srv, "A", _lettera_lunga())
    r = bi.risolvi_isin("AZL.MI", nome="Azalea")
    assert r["stato"] == "KO" and r["errore"] == "listino_troncato" and r["isin"] is None
    assert "verdetto sospeso" in r["motivo"] and not srv["auto"].exists()


def test_listino_troncato_con_righe_ma_nessuna_scheda_valida_resta_sospeso(srv, monkeypatch):
    """Una riga simile nelle pagine lette, scheda col codice sbagliato, e il listino non finito:
    la riga giusta poteva essere oltre -> KO, non «non_trovato»."""
    monkeypatch.setattr(bi, "MAX_PAGINE_LISTINO", 1)
    pagine = [[("Azzurra", isin("ITZZAZZU000"), "MTAA")], [("Azzurra Due", isin("ITZZAZZD000"), "MTAA")]]
    servi_lettera(srv, "A", pagine)
    servi_schede(srv, [(pagine[0][0][1], "MTAA", "AZR")])
    r = bi.risolvi_isin("AZRD.MI", nome="Azzurra")
    assert r["stato"] == "KO" and r["errore"] == "listino_troncato"


def test_stesso_isin_su_due_mercati_sono_due_righe(srv):
    cod = isin("ITZZDUPL000")
    # le due righe su due pagine: il controllo dei doppioni fra pagine non deve fonderle
    servi_lettera(srv, "D", [[("Duplo Finto", cod, "BGEM")], [("Duplo Finto", cod, "MTAA"), ("Dura", isin("ITZZDURA000"), "MTAA")]])
    srv["risposte"][scheda_url(cod, "MTAA")] = (200, pagina_scheda(cod, "DUP"))
    srv["risposte"][scheda_url(cod, "BGEM")] = (200, pagina_scheda(cod, "1DUP"))
    r = bi.risolvi_isin("DUP.MI", nome="Duplo Finto")
    assert r["stato"] == "ok" and r["isin"] == cod
    assert len(r["verifica"]["righe_corrispondenti"]) == 2
    assert r["verifica"]["riga_usata"]["mic"] == "MTAA"


# ---------------------------------------------------------------- B. nomi
@pytest.mark.parametrize("nome,atteso", [
    ("S&P Finta Inc", "S E P FINTA"),                       # la regola S.E. non mangia S&P
    ("Beta & Gamma S.p.A.", "BETA E GAMMA"),
    ("Sant’Elia Finta S.p.A.", "SANT ELIA FINTA"),     # apostrofo tipografico
    ("L'Oca Sintetica SpA", "L OCA SINTETICA"),
    ("Città Fittizia Società per Azioni", "CITTA FITTIZIA"),
    ("Banca Popolare dell'Inventato S.p.A.", "BANCA POPOLARE DELL INVENTATO"),
    ("Omega N.V.", "OMEGA"), ("Omega S.A.", "OMEGA"), ("Omega SE", "OMEGA"),
    ("Zeta Fittizia Rsp", "ZETA FITTIZIA RSP"),             # la classe resta nel nome normalizzato
])
def test_normalizza_nome_casi_difficili(nome, atteso):
    assert bi.normalizza_nome(nome) == atteso


@pytest.mark.parametrize("cercato,riga,atteso", [
    ("ZETAFIN FINTA BANK", "ZETAFINFINTA BANK", "compatto"),
    ("SYS DAT FINTO", "SYSDAT FINTO", "compatto"),
    ("ZETA FITTIZIA INDUSTRIA CHIMICA", "ZETA FITTIZIA ORD", "parole"),   # classe azione ignorata
    ("ZETA FITTIZIA", "ZETA FITTIZIA RSP", "compatto"),   # classe e parole vuote fuori
    ("GRUPPO OMEGA", "OMEGA", "compatto"),
    ("OMEGA", "OMEGA FINTA HOLDING", "parole"),
    ("BANCA ALFA", "BANCA BETA", None),
    ("GRUPPO", "GRUPPO OMEGA", None),                       # nessuna parola significativa
    ("ALFA", "ALFA", "esatto"),
])
def test_combacia(cercato, riga, atteso):
    assert bi._combacia(cercato, riga) == atteso


@pytest.mark.parametrize("nome,atteso", [
    ("ASSICURAZIONI OMEGA", [("A", "ASSICURAZIONI"), ("O", "OMEGA")]),
    ("GRUPPO MUTUI FINTI", [("G", "GRUPPO"), ("M", "MUTUI"), ("F", "FINTI")]),
    ("24 ORE FINTA", [("O", "ORE"), ("F", "FINTA")]),
    ("BANCA BETA", [("B", "BETA")]),                        # stessa lettera: si arresta alla parola piu' alta
    ("ZETA FITTIZIA RSP", [("Z", "ZETA"), ("F", "FITTIZIA")]),
    ("ALFA BETA GAMMA DELTA", [("A", "ALFA"), ("B", "BETA"), ("G", "GAMMA")]),   # al massimo MAX_LETTERE
])
def test_lettere_da_provare(nome, atteso):
    assert bi._lettere_da_provare(nome) == atteso


@pytest.mark.parametrize("riga,parola,atteso", [
    ("ZETAFINBANK", "ZETAFIN", False), ("ZETAFO", "ZETAFIN", True), ("Z O R", "ZORBAX", False),
    ("ZORTUM", "ZORBAX", True), ("ZORBAX RSP", "ZORBAX", False),
])
def test_oltre(riga, parola, atteso):
    assert bi._oltre(riga, parola) is atteso


# ---------------------------------------------------------------- C. scelte di risolvi_isin
ZF_ORD, ZF_RSP = isin("ITZZZFOR000"), isin("ITZZZFRS000")


def _zeta(srv, nome_ord="Zeta Fittizia"):
    servi_lettera(srv, "Z", [[(nome_ord, ZF_ORD, "MTAA"), ("Zeta Fittizia Rsp", ZF_RSP, "MTAA")]])
    servi_lettera(srv, "F", [[("Fico Altro", isin("ITZZFICO000"), "MTAA")]])   # lettera di ripiego
    servi_schede(srv, [(ZF_ORD, "MTAA", "ZZF"), (ZF_RSP, "MTAA", "ZZFR")])


def test_ordinaria_scelta_quando_il_ticker_e_l_ordinaria(srv):
    _zeta(srv)
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == ZF_ORD
    esiti = {s["isin"]: s["esito"] for s in r["verifica"]["schede_controllate"]}
    assert esiti == {ZF_ORD: "accettata", ZF_RSP: "scartata"}
    assert r["verifica"]["righe_corrispondenti"][0]["combacia"] == "esatto"   # l'esatta controllata per prima


def test_risparmio_scelta_quando_il_ticker_e_la_risparmio(srv):
    _zeta(srv)
    r = bi.risolvi_isin("ZZFR.MI", nome="Zeta Fittizia S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == ZF_RSP


def test_riga_ord_trovata_col_nome_lungo_del_fornitore(srv):
    """Riga «Zeta Fittizia Ord», nome del fornitore lungo con altre parole: combacia per parole
    grazie alla classe dell'azione ignorata (prima: nessuna corrispondenza)."""
    _zeta(srv, nome_ord="Zeta Fittizia Ord")
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia Industria Chimica S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == ZF_ORD, r["motivo"]


def test_classe_non_decisa_dal_nome_due_schede_valide_e_ambiguo(srv):
    """Se le due schede portano lo stesso codice (pagina anomala) non si sceglie: ambiguo."""
    _zeta(srv)
    servi_schede(srv, [(ZF_RSP, "MTAA", "ZZF")])
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia")
    assert r["stato"] == "ambiguo" and r["isin"] is None and not srv["auto"].exists()


def test_omonimi_su_due_mercati_con_lo_stesso_codice_ambiguo(srv):
    a, b = isin("ITZZSIGA000"), isin("ITZZSIGB000")
    servi_lettera(srv, "S", [[("Sigma Finta", a, "MTAA"), ("Sigma Finta", b, "EXGM")]])
    servi_schede(srv, [(a, "MTAA", "SIGF"), (b, "EXGM", "SIGF")])
    r = bi.risolvi_isin("SIGF.MI", nome="Sigma Finta")
    assert r["stato"] == "ambiguo" and r["errore"] == "piu_schede_valide" and r["isin"] is None
    assert a in r["motivo"] and b in r["motivo"]


def test_omonimo_estero_scartato_dalla_scheda(srv):
    a, b = isin("ITZZSIGA000"), isin("USZZSIGB000")
    servi_lettera(srv, "S", [[("Sigma Finta", b, "BGEM"), ("Sigma Finta", a, "MTAA")]])
    servi_schede(srv, [(a, "MTAA", "SIGF"), (b, "BGEM", "1SIGF")])
    r = bi.risolvi_isin("SIGF.MI", nome="Sigma Finta")
    assert r["stato"] == "ok" and r["isin"] == a


def test_nome_che_inizia_con_altra_parola_prova_la_lettera_successiva(srv):
    """«Assicurazioni Omega» -> nessuna riga sotto la A che combaci -> la O ha «Omega»."""
    om = isin("ITZZOMEG000")
    servi_lettera(srv, "A", [[("Abaco", isin("ITZZABAC000"), "MTAA"), ("Azalea", isin("ITZZAZAL000"), "MTAA")]])
    servi_lettera(srv, "O", [[("Oca", isin("ITZZOCAA000"), "MTAA"), ("Omega", om, "MTAA"), ("Orso", isin("ITZZORSO000"), "MTAA")]])
    servi_schede(srv, [(om, "MTAA", "OMG")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(7101, "ASSICURAZIONI OMEGA"), (7102, "BANCA OMEGA")]))
    r = bi.risolvi_isin("OMG.MI", nome="Assicurazioni Omega S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == om, r["motivo"]
    assert r["verifica"]["iniziali_lette"] == ["A", "O"] and r["verifica"]["iniziale"] == "A"
    # eMarket: la riga dice solo «Omega» (2 voci per parole), il nome cercato e' esatto su una
    assert r["emarket"] == 7101 and r["emarket_motivo"] is None


def test_nome_che_inizia_con_un_numero(srv):
    o = isin("ITZZOREF000")
    servi_lettera(srv, "O", [[("Ore Finta", o, "MTAA")]])
    servi_schede(srv, [(o, "MTAA", "ORF")])
    r = bi.risolvi_isin("ORF.MI", nome="24 Ore Finta")
    assert r["stato"] == "ok" and r["isin"] == o and r["verifica"]["iniziali_lette"] == ["O"]


def test_nome_solo_numeri_non_trovato_dichiarato(srv):
    r = bi.risolvi_isin("ZZN.MI", nome="2468")
    assert r["stato"] == "non_trovato" and r["errore"] == "iniziale_non_alfabetica" and srv["chieste"] == []


def test_non_trovato_dopo_tutte_le_lettere_le_dichiara(srv):
    servi_lettera(srv, "A", [[("Abaco", isin("ITZZABAC000"), "MTAA")]])
    servi_lettera(srv, "O", [[("Oca", isin("ITZZOCAA000"), "MTAA")]])
    r = bi.risolvi_isin("OMG.MI", nome="Assicurazioni Omega")
    assert r["stato"] == "non_trovato" and r["errore"] == "nome_non_nel_listino"
    assert r["verifica"]["iniziali_lette"] == ["A", "O"] and "A/O" in r["motivo"]


def test_lettera_di_ripiego_in_guasto_e_KO(srv):
    servi_lettera(srv, "A", [[("Abaco", isin("ITZZABAC000"), "MTAA")]])
    r = bi.risolvi_isin("OMG.MI", nome="Assicurazioni Omega")
    assert r["stato"] == "KO" and r["errore"] == "rete" and "listino O" in r["motivo"]


def test_accenti_apostrofi_e_commerciale(srv):
    se, bg = isin("ITZZSELI000"), isin("ITZZBGFI000")
    servi_lettera(srv, "S", [[("Sant'Elia Finta", se, "MTAA")]])
    servi_lettera(srv, "B", [[("B & G Finta", bg, "EXGM")]])
    servi_schede(srv, [(se, "MTAA", "SEF"), (bg, "EXGM", "BGF")])
    r = bi.risolvi_isin("SEF.MI", nome="Sant’Elia Finta S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == se, r["motivo"]
    r = bi.risolvi_isin("BGF.MI", nome="B&G Finta SpA")
    assert r["stato"] == "ok" and r["isin"] == bg, r["motivo"]


def test_nome_da_etf_dichiarato_senza_rete(srv):
    r = bi.risolvi_isin("ZZE.MI", nome="Finto Semiconduttori UCITS ETF")
    assert r["stato"] == "non_trovato" and r["errore"] == "non_azione" and srv["chieste"] == []
    assert "solo le azioni" in r["motivo"]


def test_schede_troncate_verdetto_sospeso(srv, monkeypatch):
    monkeypatch.setattr(bi, "MAX_SCHEDE", 2)
    righe = [("Banca Kappa Uno", isin("ITZZBKU1000"), "MTAA"), ("Banca Kappa Due", isin("ITZZBKU2000"), "MTAA"),
             ("Banca Kappa Tre", isin("ITZZBKU3000"), "MTAA")]
    servi_lettera(srv, "B", [righe])
    servi_lettera(srv, "K", [[("Kilo Altro", isin("ITZZKILO000"), "MTAA")]])   # ripiego: niente
    servi_schede(srv, [(c, m, "XX%d" % k) for k, (_n, c, m) in enumerate(righe)])
    r = bi.risolvi_isin("BKT.MI", nome="Banca Kappa")
    assert r["stato"] == "KO" and r["errore"] == "schede_troncate" and r["isin"] is None
    assert len(r["verifica"]["schede_controllate"]) == 2


def test_con_molte_righe_si_controllano_le_piu_vicine(srv, monkeypatch):
    # nessuna riga esatta: per parole, prima quella con meno parole di scarto
    monkeypatch.setattr(bi, "MAX_SCHEDE", 1)
    lunga, giusta = isin("ITZZKPLU000"), isin("ITZZKPGI000")
    servi_lettera(srv, "K", [[("Kappa Finta Holding Lunga", lunga, "MTAA"), ("Kappa Finta Italia", giusta, "MTAA")]])
    servi_schede(srv, [(lunga, "MTAA", "KPL"), (giusta, "MTAA", "KPF")])
    r = bi.risolvi_isin("KPF.MI", nome="Kappa Finta S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == giusta


def test_scheda_in_guasto_5xx_sospende_404_scarta(srv):
    _zeta(srv)
    srv["risposte"][scheda_url(ZF_ORD)] = (503, b"")
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia")
    assert r["stato"] == "KO" and r["errore"] == "http" and "verdetto sospeso" in r["motivo"]
    srv["risposte"][scheda_url(ZF_ORD)] = (404, b"")
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia")
    assert r["stato"] == "non_trovato" and r["errore"] == "nessuna_scheda_valida"


# ---------------------------------------------------------------- D. id eMarket
@pytest.mark.parametrize("opzioni,riga,cercato,atteso", [
    ([(1, "OMEGA"), (2, "OMEGA RISPARMIO")], "OMEGA", None, (1, "ok")),          # l'esatta vince
    ([(1, "ASSICURAZIONI OMEGA"), (2, "BANCA OMEGA")], "OMEGA", "Assicurazioni Omega S.p.A.", (1, "ok")),
    ([(1, "ASSICURAZIONI OMEGA"), (2, "BANCA OMEGA")], "OMEGA", "Omega", (None, "ambiguo")),
    ([(1, "ASSICURAZIONI OMEGA"), (2, "BANCA OMEGA")], "OMEGA", None, (None, "ambiguo")),
    ([(1, "ZETAFINFINTA BANK")], "ZETAFIN FINTA BANK", None, (1, "ok")),            # compatto
    ([(1, "ALTRO")], "OMEGA", None, (None, "non_trovato")),
    ([(1, "OMEGA"), (2, "OMEGA S.P.A.")], "OMEGA", None, (None, "ambiguo")),       # due voci esatte: mai la prima
])
def test_cerca_emarket(srv, opzioni, riga, cercato, atteso):
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket(opzioni))
    i, esito, motivo = bi._cerca_emarket(riga, cercato)
    assert (i, esito) == atteso, motivo


def test_cerca_emarket_menu_senza_voci_e_KO(srv):
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([]))
    i, esito, motivo = bi._cerca_emarket("OMEGA", None)
    assert i is None and esito == "KO" and "senza voci" in motivo


def test_cerca_emarket_menu_assente_e_rete(srv):
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, b"<html><body>altra pagina</body></html>")
    assert bi._cerca_emarket("OMEGA")[1] == "KO"
    srv["risposte"][bi.URL_MENU_EMARKET] = TimeoutError("x")
    i, esito, motivo = bi._cerca_emarket("OMEGA")
    assert (i, esito) == (None, "KO") and "TimeoutError" in motivo


# ---------------------------------------------------------------- E. campo facoltativo 'oneinfo'
@pytest.mark.parametrize("voce,atteso", [
    ({"isin": "ITZZACME0007", "emarket": 4242}, "ASSENTE"),              # voci di oggi: restano valide
    ({"isin": "ITZZACME0007", "emarket": None, "oneinfo": 99901}, 99901),
    ({"isin": "ITZZACME0007", "emarket": 4242, "oneinfo": None}, None),   # null = dichiarato non su 1INFO
])
def test_oneinfo_facoltativo_assente_diverso_da_null(srv, tmp_path, monkeypatch, voce, atteso):
    p = tmp_path / "isin_it.json"
    p.write_text(json.dumps({"ACME.MI": voce}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(p))
    v, err, _ = bi.voce_ticker_o_auto("ACME.MI")
    assert err is None and v["negozio"] == "confermato"
    assert v.get("oneinfo", "ASSENTE") == atteso


@pytest.mark.parametrize("valore", [0, -3, True, "99901", 1.5])
def test_oneinfo_malformato_rende_illeggibile(tmp_path, valore):
    p = tmp_path / "isin_it.json"
    p.write_text(json.dumps({"ACME.MI": {"isin": "ITZZACME0007", "emarket": None, "oneinfo": valore}}), encoding="utf-8")
    neg = bi.carica_isin_it(str(p))
    assert neg["origine"] == "illeggibile" and "oneinfo" in neg["motivo"]


def test_oneinfo_nel_negozio_automatico(tmp_path):
    p = tmp_path / "auto.json"
    base = {"isin": "ITZZACME0007", "emarket": None, "origine": bi.ORIGINE_AUTO % "x"}
    p.write_text(json.dumps({"ACME.MI": dict(base, oneinfo=99901), "ZZTEST.MI": dict(base, isin="ITZZTEST0001")}),
                 encoding="utf-8")
    voci = bi.carica_isin_auto(str(p))["voci"]
    assert voci["ACME.MI"]["oneinfo"] == 99901 and "oneinfo" not in voci["ZZTEST.MI"]
    p.write_text(json.dumps({"ACME.MI": dict(base, oneinfo="x")}), encoding="utf-8")
    assert bi.carica_isin_auto(str(p))["origine"] == "illeggibile"


# ---------------------------------------------------------------- F. trattini e classi A/B (misura M1b 05/10)
@pytest.mark.parametrize("riga,voce,atteso", [
    ("Zu Ve Finto", "ZU-VE FINTO", "esatto"),            # trattino del menu = spazio
    ("Zuve Finto", "ZU-VE FINTO", "compatto"),           # riga attaccata, menu col trattino
    ("Mfx A", "MFX-MEDIAFINTA", None),                   # la lettera A/B conta qui: la toglie solo
    ("B & G Finta", "B E G FINTA", "esatto"),            # _nomi_di_confronto, con la riga sorella
    ("Zindel Finto B", "ZINDEL FINTO", "parole"),
])
def test_combacia_trattini_e_classi(riga, voce, atteso):
    assert bi._combacia(bi.normalizza_nome(riga), bi.normalizza_nome(voce)) == atteso


def _righe(*nomi):
    r = [{"nome_normalizzato": bi.normalizza_nome(x)} for x in nomi]
    bi._nomi_di_confronto(r)
    return [x["nome_confronto"] for x in r]


def test_lettera_di_classe_tolta_solo_con_la_riga_sorella():
    assert _righe("Mfx A", "Mfx B", "Mfy A") == ["MFX", "MFX", "MFY A"]
    assert _righe("Zindel Finto B") == ["ZINDEL FINTO B"]          # nessuna sorella: parte del nome
    assert _righe("A", "B") == ["A", "B"]                        # una lettera sola non e' una classe


def test_classe_b_senza_sorella_non_perde_la_lettera(srv):
    giusta, altra = isin("ITZZINDB000"), isin("ITZZINDL000")
    servi_lettera(srv, "Z", [[("Zindel Finto", altra, "MTAA"), ("Zindel Finto B", giusta, "MTAA")]])
    servi_lettera(srv, "F", [[("Fico Altro", isin("ITZZFICO000"), "MTAA")]])
    servi_schede(srv, [(giusta, "MTAA", "ZZINB"), (altra, "MTAA", "ZZINF")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(31, "ZINDEL FINTO"), (32, "ZINDEL FINTO B")]))
    r = bi.risolvi_isin("ZZINB.MI", nome="Zindel Finto B S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == giusta and r["emarket"] == 32, r["motivo"]
    assert r["verifica"]["righe_corrispondenti"][0]["combacia"] == "esatto"


def test_classe_a_trovata_col_nome_lungo_del_fornitore(srv):
    a, b = isin("NLZZMFXA000"), isin("NLZZMFXB000")
    servi_lettera(srv, "M", [[("Mfx A", a, "MTAA"), ("Mfx B", b, "MTAA")]])
    servi_schede(srv, [(a, "MTAA", "MFXA"), (b, "MTAA", "MFXB")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(8801, "MFX-MEDIAFINTA")]))
    r = bi.risolvi_isin("MFXB.MI", nome="MFX-MediaFinta N.V.")
    assert (r["stato"], r["isin"], r["emarket"]) == ("ok", b, 8801), r["motivo"]


def test_due_classi_condividono_l_id_emarket_e_la_scheda_sceglie(srv):
    """Righe «Mfx A»/«Mfx B» (due ISIN, codici MFXA/MFXB), un solo emittente sul menu col trattino."""
    a, b = isin("NLZZMFXA000"), isin("NLZZMFXB000")
    servi_lettera(srv, "M", [[("Mfx A", a, "MTAA"), ("Mfx B", b, "MTAA")]])
    servi_schede(srv, [(a, "MTAA", "MFXA"), (b, "MTAA", "MFXB")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(8801, "MFX-MEDIAFINTA"), (8802, "MEDIA ALTRA")]))
    ra = bi.risolvi_isin("MFXA.MI", nome="Mfx A")
    rb = bi.risolvi_isin("MFXB.MI", nome="Mfx B")
    assert (ra["stato"], ra["isin"], ra["emarket"]) == ("ok", a, 8801), ra["motivo"]
    assert (rb["stato"], rb["isin"], rb["emarket"]) == ("ok", b, 8801), rb["motivo"]


def test_menu_col_trattino_per_nome_attaccato(srv):
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(9901, "ZU-VE FINTO"), (9902, "ZUCE FINTA")]))
    assert bi._cerca_emarket("ZUVE FINTO", "Zuve Finto S.p.A.")[:2] == (9901, "ok")


# ---------------------------------------------------------------- G. rilievi RV-IT1 (P1, P2, P3)
@pytest.mark.parametrize("opzioni,riga,cercato,atteso", [
    # P1: un solo candidato «per parole» non e' piu' una prova
    ([(1, "GRUPPO VERDE POTENZA")], "QQAL VERDE POTENZA", None, (None, "da_confermare")),
    ([(1, "ZZCOR RES")], "RES", None, (None, "da_confermare")),               # prima parola diversa
    # P1-bis: il nome cercato corto non scavalca la riga abbreviata
    ([(1, "CALTA"), (2, "CALTA EDITORE")], "CALTA EDIT", "Calta", (2, "ok")),
    ([(1, "CALTA"), (2, "CALTA EDITORE")], "CALTA EDIT", "Calta Editore S.p.A.", (2, "ok")),
    ([(1, "CALTA")], "CALTA EDIT", "Calta", (None, "da_confermare")),
    # il nome cercato lungo resta uno spareggio valido quando contiene la riga
    ([(1, "ASSICURAZIONI OMEGA"), (2, "BANCA OMEGA")], "OMEGA", "Assicurazioni Omega", (1, "ok")),
    ([(1, "ASSICURAZIONI OMEGA")], "OMEGA", None, (None, "da_confermare")),
])
def test_cerca_emarket_rilievi_rv_it1(srv, opzioni, riga, cercato, atteso):
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket(opzioni))
    i, esito, motivo = bi._cerca_emarket(riga, cercato)
    assert (i, esito) == atteso, motivo


def test_riga_estera_bgem_non_cerca_emarket(srv):
    cod = isin("USZZORAN000")
    servi_lettera(srv, "O", [[("Oranzo Finto", cod, "BGEM")]])
    servi_schede(srv, [(cod, "BGEM", "ORZ")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(5, "ORANZO FINTO MEDIA")]))
    r = bi.risolvi_isin("ORZ.MI", nome="Oranzo Finto")
    assert r["stato"] == "ok" and r["emarket"] is None
    assert "non_cercato" in r["emarket_motivo"] and "BGEM" in r["emarket_motivo"]
    assert bi.URL_MENU_EMARKET not in srv["chieste"]


@pytest.mark.parametrize("nome,atteso", [("PLC S.p.A.", "PLC"), ("S.p.A.", ""), ("Plc", "PLC"), ("SE", "SE")])
def test_nome_che_e_una_sigla_di_forma_giuridica(nome, atteso):
    assert bi.normalizza_nome(nome) == atteso


def test_ripiego_anche_quando_la_prima_lettera_ha_solo_schede_sbagliate(srv):
    """P2: sotto la G c'e' «Gufo», un altro emittente (scheda con un altro codice); la riga
    giusta sta sotto la C. Prima: non_trovato gia' alla lettera G, senza provare la C."""
    sbagliata, giusta = isin("ITZZGUFO000"), isin("ITZZCGUF000")
    servi_lettera(srv, "I", [[("Ibis", isin("ITZZIBIS000"), "MTAA")]])
    servi_lettera(srv, "G", [[("Gufo", sbagliata, "MTAA")]])
    servi_lettera(srv, "C", [[("Cantieri Gufo", giusta, "MTAA")]])
    servi_schede(srv, [(sbagliata, "MTAA", "GUF"), (giusta, "MTAA", "CGF")])
    r = bi.risolvi_isin("CGF.MI", nome="Industria Gufo Cantieri")
    assert r["stato"] == "ok" and r["isin"] == giusta, r["motivo"]
    assert r["verifica"]["iniziali_lette"] == ["I", "G", "C"]


def test_riga_abbreviata_del_listino(srv):
    cod, altro = isin("ITZZCALT000"), isin("ITZZCALA000")
    servi_lettera(srv, "C", [[("Calta", altro, "MTAA"), ("Calta Edit", cod, "MTAA")]])
    servi_lettera(srv, "E", [[("Ebano", isin("ITZZEBAN000"), "MTAA")]])
    servi_schede(srv, [(cod, "MTAA", "QQCED"), (altro, "MTAA", "QQCALT")])
    srv["risposte"][bi.URL_MENU_EMARKET] = (200, menu_emarket([(11, "CALTA"), (12, "CALTA EDITORE")]))
    r = bi.risolvi_isin("QQCED.MI", nome="Calta Editore S.p.A.")
    assert r["stato"] == "ok" and r["isin"] == cod and r["emarket"] == 12, r["motivo"]
    assert "abbreviato" in {x["combacia"] for x in r["verifica"]["righe_corrispondenti"]}


@pytest.mark.parametrize("cercato,riga,atteso", [
    ("CALTA EDITORE", "CALTA EDIT", "abbreviato"), ("CALTA EDITORE", "CALTA ED", None),   # < 3 lettere
    ("CALTA EDITORE", "ALTRA EDIT", None), ("CALTA", "CALTA EDIT", None),
])
def test_abbreviato(cercato, riga, atteso):
    assert bi._abbreviato(cercato, riga) == atteso


# ---------------------------------------------------------------- H. scrittura concorrente (rilievo R3 di RV-W1)
import os
import subprocess
import sys
import threading
import time


def test_thread_paralleli_non_perdono_voci(tmp_path):
    p = str(tmp_path / "negozio.json")
    errori = []

    def scrivi(k):
        ok, mot = bi.aggiorna_json_bloccato(p, lambda d: dict(d, **{"T%d" % k: k}))
        if not ok:
            errori.append(mot)
    th = [threading.Thread(target=scrivi, args=(k,)) for k in range(16)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert errori == []
    with open(p, encoding="utf-8") as fh:
        assert json.load(fh) == {"T%d" % k: k for k in range(16)}


_FIGLIO = "\n".join([
    "import sys, time",
    "from bellomberg.market_data import borsa_italiana as bi",
    "percorso, prefisso, n, pausa = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])",
    "ko = 0",
    "for k in range(n):",
    "    def modifica(d, k=k):",
    "        time.sleep(pausa)",
    "        d[prefisso + str(k)] = k",
    "        return d",
    "    ok, mot = bi.aggiorna_json_bloccato(percorso, modifica, attesa_s=30)",
    "    ko += (not ok)",
    "print('KO=%d' % ko)",
])


def _figlio(*argomenti):
    return subprocess.Popen([sys.executable, "-B", "-c", _FIGLIO, *map(str, argomenti)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                            errors="replace", env=dict(os.environ))


def test_due_processi_veri_non_perdono_voci(tmp_path):
    p = str(tmp_path / "negozio.json")
    a, b = _figlio(p, "A", 15, 0.005), _figlio(p, "B", 15, 0.005)
    oa, ea = a.communicate(timeout=180)
    ob, eb = b.communicate(timeout=180)
    assert "KO=0" in oa and "KO=0" in ob, (ea[-500:], eb[-500:])
    with open(p, encoding="utf-8") as fh:
        dati = json.load(fh)
    assert len(dati) == 30 and all(("A%d" % k) in dati and ("B%d" % k) in dati for k in range(15))


def test_lock_tenuto_da_un_altro_processo_timeout_dichiarato(tmp_path):
    p = str(tmp_path / "negozio.json")
    figlio = _figlio(p, "A", 1, 6.0)     # tiene il lock ~6 s
    fine = time.monotonic() + 90
    while not os.path.exists(p + ".lock") and time.monotonic() < fine:
        time.sleep(0.05)
    time.sleep(1.5)                      # il figlio e' dentro la modifica
    ok, mot = bi.aggiorna_json_bloccato(p, lambda d: dict(d, X=1), attesa_s=0.3)
    out, err = figlio.communicate(timeout=180)
    assert ok is False and "non ottenuto" in mot and "non scritto" in mot, mot
    assert "KO=0" in out, err[-500:]
    with open(p, encoding="utf-8") as fh:
        assert json.load(fh) == {"A0": 0}


def test_json_illeggibile_non_sovrascritto_salvo_cache(tmp_path):
    p = tmp_path / "n.json"
    p.write_text("{rotto", encoding="utf-8")
    ok, mot = bi.aggiorna_json_bloccato(str(p), lambda d: dict(d, X=1))
    assert ok is False and "illeggibile" in mot and p.read_text(encoding="utf-8") == "{rotto"
    ok, _ = bi.aggiorna_json_bloccato(str(p), lambda d: dict(d, X=1), illeggibile_si_riscrive=True)
    assert ok and json.loads(p.read_text(encoding="utf-8")) == {"X": 1}


def test_rifiuto_della_modifica_non_scrive(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"A": 1}), encoding="utf-8")

    def rifiuta(d):
        raise bi.RifiutoModifica("motivo nostro")
    assert bi.aggiorna_json_bloccato(str(p), rifiuta) == (False, "motivo nostro")
    assert json.loads(p.read_text(encoding="utf-8")) == {"A": 1}


def test_scrivi_voce_auto_concorrente_tiene_tutte_le_voci(srv):
    base = {"emarket": None, "origine": bi.ORIGINE_AUTO % "x"}
    codici = [isin("ITZZCON%d000" % k) for k in range(8)]
    th = [threading.Thread(target=bi._scrivi_voce_auto, args=("ZC%d.MI" % k, dict(base, isin=c)))
          for k, c in enumerate(codici)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    voci = bi.carica_isin_auto(str(srv["auto"]))["voci"]
    assert sorted(voci) == sorted("ZC%d.MI" % k for k in range(8))


def test_replace_riprova_se_un_lettore_tiene_il_file(tmp_path, monkeypatch):
    """Windows: os.replace fallisce con PermissionError se un lettore ha il file aperto."""
    p = str(tmp_path / "n.json")
    vero, conta = os.replace, {"n": 0}

    def replace_occupato(a, b):
        conta["n"] += 1
        if conta["n"] <= 3:
            raise PermissionError("occupato")
        return vero(a, b)
    monkeypatch.setattr(bi.os, "replace", replace_occupato)
    assert bi.aggiorna_json_bloccato(p, lambda d: {"X": 1}) == (True, None) and conta["n"] == 4


# ---------------------------------------------------------------- I. lettore concorrente (negozi_privati.carica)
from bellomberg.storage import negozi_privati as _np


def _open_occupato(volte, conta):
    vero = open

    def finto(*a, **k):
        conta["n"] += 1
        if conta["n"] <= volte:
            raise PermissionError("file in sostituzione")
        return vero(*a, **k)
    return finto


def test_carica_riprova_un_permissionerror_transitorio(tmp_path, monkeypatch):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({"ACME.MI": {"isin": "ITZZACME0007", "emarket": None,
                                         "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    conta = {"n": 0}
    monkeypatch.setattr(_np, "PAUSA_LETTURA_S", 0)
    monkeypatch.setattr(_np, "open", _open_occupato(_np.TENTATIVI_LETTURA - 1, conta), raising=False)
    neg = bi.carica_isin_auto(str(p))
    assert neg["origine"] == str(p) and "ACME.MI" in neg["voci"] and conta["n"] == _np.TENTATIVI_LETTURA


def test_carica_permissionerror_persistente_resta_illeggibile_dichiarato(tmp_path, monkeypatch):
    p = tmp_path / "auto.json"
    p.write_text("{}", encoding="utf-8")
    conta = {"n": 0}
    monkeypatch.setattr(_np, "PAUSA_LETTURA_S", 0)
    monkeypatch.setattr(_np, "open", _open_occupato(10 ** 6, conta), raising=False)
    neg = bi.carica_isin_auto(str(p))
    assert neg["origine"] == "illeggibile" and "PermissionError" in neg["motivo"]
    assert conta["n"] == _np.TENTATIVI_LETTURA


def test_carica_json_rotto_non_si_ritenta(tmp_path, monkeypatch):
    p = tmp_path / "auto.json"
    p.write_text("{rotto", encoding="utf-8")
    conta = {"n": 0}
    monkeypatch.setattr(_np, "open", _open_occupato(0, conta), raising=False)
    assert bi.carica_isin_auto(str(p))["origine"] == "illeggibile" and conta["n"] == 1


def test_costanti_dei_tentativi_dichiarate():
    assert _np.TENTATIVI_LETTURA >= 3 and _np.TENTATIVI_LETTURA * _np.PAUSA_LETTURA_S <= 1.0


def test_lettore_concorrente_con_scrittore_reale_mai_illeggibile(srv):
    """Scrittore vero (aggiorna_json_bloccato, os.replace) in un thread, lettore vero
    (voce_ticker_o_auto -> carica) nel thread del test: nessun 'illeggibile' transitorio."""
    base = {"emarket": None, "origine": bi.ORIGINE_AUTO % "x", "isin": "ITZZACME0007"}
    bi._scrivi_voce_auto("ACME.MI", base)
    fermo, errori = threading.Event(), []

    def scrittore():
        k = 0
        while not fermo.is_set():
            ok, mot = bi._scrivi_voce_auto("ZW%d.MI" % (k % 5), base)
            if not ok:
                errori.append(mot)
            k += 1
    t = threading.Thread(target=scrittore)
    t.start()
    letture, guasti = 0, []
    try:
        fine = time.monotonic() + 3.0
        while time.monotonic() < fine:
            voce, err, mot = bi.voce_ticker_o_auto("ACME.MI")
            letture += 1
            if voce is None:
                guasti.append((err, mot))
    finally:
        fermo.set()
        t.join()
    assert letture > 50 and guasti == [] and errori == [], (guasti[:3], errori[:3])


# ---------------------------------------------------------------- J. nome della riga del listino (per sdir/1INFO)
def test_nome_listino_salvato_e_letto_senza_rete(srv):
    _zeta(srv)
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia Industria S.p.A.")
    assert r["stato"] == "ok" and r["salvato"], r["motivo"]
    n = len(srv["chieste"])
    assert bi.nome_listino("ZZF.MI") == "Zeta Fittizia" and bi.nome_listino("zzf.mi ") == "Zeta Fittizia"
    assert len(srv["chieste"]) == n                       # nessuna richiesta
    assert bi.nome_listino("NONCE.MI") is None


def test_nome_listino_voce_vecchia_senza_campo_e_none(srv):
    srv["auto"].write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None,
                                                  "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    assert bi.voce_ticker_o_auto("ZZV.MI")[0] is not None and bi.nome_listino("ZZV.MI") is None


def test_nome_listino_vuoto_rende_illeggibile(tmp_path):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None, "riga_listino": "  ",
                                        "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    assert bi.carica_isin_auto(str(p))["origine"] == "illeggibile"


def test_nome_listino_confermato_solo_se_stesso_isin(srv, tmp_path, monkeypatch):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(conf))
    base = {"emarket": None, "origine": bi.ORIGINE_AUTO % "x", "riga_listino": "Riga Finta"}
    srv["auto"].write_text(json.dumps({"ZZV.MI": dict(base, isin="ITZZACME0007")}), encoding="utf-8")
    assert bi.nome_listino("ZZV.MI") == "Riga Finta"
    srv["auto"].write_text(json.dumps({"ZZV.MI": dict(base, isin="ITZZTEST0001")}), encoding="utf-8")
    assert bi.nome_listino("ZZV.MI") is None             # il confermato vince, il nome era di un altro ISIN


# ---------------------------------------------------------------- K. sopravvissuti del banco di RV-IT1 (E, J, L)
def test_scheda_429_e_verdetto_sospeso(srv):
    _zeta(srv)
    srv["risposte"][scheda_url(ZF_ORD)] = (429, b"")
    r = bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia")
    assert r["stato"] == "KO" and r["errore"] == "http" and "429" in r["motivo"] and not srv["auto"].exists()


def test_negozio_automatico_con_voce_malformata_non_si_tocca(srv):
    """JSON valido ma con una voce malformata: i lettori lo vedono illeggibile; lo scrittore non
    deve scriverci sopra ne' dire salvato=True. File identico byte per byte."""
    grezzo = json.dumps({"ALTRO.MI": {"isin": "ITZZACME0008", "emarket": None,
                                      "origine": bi.ORIGINE_AUTO % "x"}}).encode("utf-8")   # cifra sbagliata
    srv["auto"].write_bytes(grezzo)
    ok, motivo = bi._scrivi_voce_auto("ZZF.MI", {"isin": ZF_ORD, "emarket": None, "origine": bi.ORIGINE_AUTO % "x"})
    assert ok is False and "non sovrascritto" in motivo and srv["auto"].read_bytes() == grezzo


@pytest.mark.parametrize("nome", ["Netfinta S.p.A.", "Betcom Finto", "Etcetera Finta", "Ucitsa Finta"])
def test_etf_solo_come_parola_intera(srv, nome):
    r = bi.risolvi_isin("ZZQ.MI", nome=nome)
    assert r["errore"] != "non_azione"


# ---------------------------------------------------------------- L. mercato della riga del listino (per sdir/1INFO)
def test_mercato_listino_salvato_e_letto_senza_rete(srv):
    _zeta(srv)
    cod = isin("USZZORAN000")
    servi_lettera(srv, "O", [[("Oranzo Finto", cod, "BGEM")]])
    servi_schede(srv, [(cod, "BGEM", "ORZ")])
    assert bi.risolvi_isin("ZZF.MI", nome="Zeta Fittizia")["salvato"]
    assert bi.risolvi_isin("ORZ.MI", nome="Oranzo Finto")["salvato"]
    n = len(srv["chieste"])
    assert bi.mercato_listino("ZZF.MI") == "MTAA" and bi.mercato_listino("orz.mi") == "BGEM"
    assert len(srv["chieste"]) == n and bi.mercato_listino("NONCE.MI") is None


def test_mercato_listino_voce_vecchia_senza_campo_e_none(srv):
    srv["auto"].write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None,
                                                  "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    assert bi.voce_ticker_o_auto("ZZV.MI")[0] is not None and bi.mercato_listino("ZZV.MI") is None


@pytest.mark.parametrize("valore", ["", "mtaa", "M TAA", 7, None, "MTAA-X"])
def test_mic_malformato_rende_illeggibile(tmp_path, valore):
    p = tmp_path / "auto.json"
    p.write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None, "mic": valore,
                                        "origine": bi.ORIGINE_AUTO % "x"}}), encoding="utf-8")
    neg = bi.carica_isin_auto(str(p))
    assert neg["origine"] == "illeggibile" and "mic" in neg["motivo"]


def test_mic_mai_nel_negozio_confermato(tmp_path):
    p = tmp_path / "conf.json"
    p.write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None, "mic": "MTAA"}}), encoding="utf-8")
    assert bi.carica_isin_it(str(p))["origine"] == "illeggibile"


def test_mercato_listino_confermato_solo_se_stesso_isin(srv, tmp_path, monkeypatch):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({"ZZV.MI": {"isin": "ITZZACME0007", "emarket": None}}), encoding="utf-8")
    monkeypatch.setattr(bi, "PERCORSO_ISIN", str(conf))
    base = {"emarket": None, "origine": bi.ORIGINE_AUTO % "x", "mic": "EXGM"}
    srv["auto"].write_text(json.dumps({"ZZV.MI": dict(base, isin="ITZZACME0007")}), encoding="utf-8")
    assert bi.mercato_listino("ZZV.MI") == "EXGM"
    srv["auto"].write_text(json.dumps({"ZZV.MI": dict(base, isin="ITZZTEST0001")}), encoding="utf-8")
    assert bi.mercato_listino("ZZV.MI") is None
