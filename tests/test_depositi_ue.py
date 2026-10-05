# -*- coding: utf-8 -*-
"""Instradatore UE delle date di diffusione (depositi_ue.py, handoff-3 05/10/2026, Opus 5.5, EU-R).

Moduli UE FINTI iniettati in sys.modules: i veri li scrivono altri agenti in parallelo e qui non si
importano mai. Nessuna rete. Ticker e valori INVENTATI (ZZTEST.*, ISIN sintetici)."""
import sys
import types
from datetime import date

import pytest

from bellomberg.market_data import depositi_ue as du

PKG = "bellomberg.market_data."


def _isin(corpo11):
    """ISIN SINTETICO valido: cifra di controllo calcolata QUI (oracolo indipendente dal modulo), algoritmo
    ISO 6166 scritto per esteso: lettere -> numeri, poi Luhn raddoppiando da destra."""
    numeri = ""
    for c in corpo11:
        numeri += str(ord(c) - 55) if c.isalpha() else c
    totale = 0
    for pos, c in enumerate(numeri[::-1]):
        v = int(c)
        if pos % 2 == 0:
            v = v * 2
            v = v // 10 + v % 10
        totale += v
    return corpo11 + str((10 - totale % 10) % 10)


ISIN_FR, ISIN_NL, ISIN_DE, ISIN_GB = _isin("FR000000000"), _isin("NL000000000"), _isin("DE000000000"), _isin("GB000000000")
ISIN_IT, ISIN_XS, ISIN_LU = _isin("IT000000000"), _isin("XS000000000"), _isin("LU000000000")


def _ritorno(ticker, paese, stato="ok", **extra):
    r = {k: None for k in du._CHIAVI_CONTRATTO}
    r.update(ticker=ticker, paese=paese, stato=stato, tipo="semestrale", periodo_fine="2026-06-30",
             candidati=[], conferme=[], url_liste=["https://fonte.example/lista"],
             sha256_liste={"https://fonte.example/lista": "a" * 64},
             risposte_salvate={"https://fonte.example/lista": "{}"}, pagine_lette=1, limiti=["limite del modulo"],
             prova="finestra", scartati=[{"titolo": "Rapport ZZ 2024", "data": "2024-07-01", "motivo": "anno diverso"}],
             natura_data="diffusione")
    r.pop("fonte_modulo")
    if stato in ("ok", "STALE"):
        r.update(data_deposito="2026-07-24", ora_deposito="13:05", fuso="UTC", titolo="Rapport financier semestriel")
    r.update(extra)
    return r


class _Spia:
    def __init__(self):
        self.chiamate = []
        self.paesi = []
        self.paesi_riverifica = []
        self.riverifiche = []


def _finto(monkeypatch, modulo, *, ritorno=None, solleva=None, riverifica=(True, "coincide"), senza=()):
    """Inietta un modulo finto `bellomberg.market_data.<modulo>`; `ritorno` = funzione(ticker) -> dict."""
    spia = _Spia()
    mod = types.ModuleType(PKG + modulo)

    def get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None, paese=None):
        spia.chiamate.append(dict(ticker=ticker, tipo=tipo, periodo_fine=periodo_fine, isin=isin, lei=lei, nome=nome))
        spia.paesi.append(paese)
        if solleva:
            raise solleva
        return ritorno(ticker)

    def riverifica_ricevuta(ricevuta, *, ticker, tipo, periodo_fine, paese=None):
        spia.riverifiche.append(dict(ticker=ticker, tipo=tipo, periodo_fine=periodo_fine))
        spia.paesi_riverifica.append(paese)
        if isinstance(riverifica, Exception):
            raise riverifica
        return riverifica

    if "get_data_deposito" not in senza:
        mod.get_data_deposito = get_data_deposito
    if "riverifica_ricevuta" not in senza:
        mod.riverifica_ricevuta = riverifica_ricevuta
    monkeypatch.setitem(sys.modules, PKG + modulo, mod)
    return spia


@pytest.fixture
def tutti_assenti(monkeypatch):
    """Ogni modulo UE 'non ancora scritto': un import tentato fallisce (ModuleNotFoundError)."""
    for m in {v["modulo"] for v in du.COPERTURA_UE.values() if v["modulo"]}:
        monkeypatch.setitem(sys.modules, PKG + m, None)


def _chiama(ticker, tipo="semestrale", fine="2026-06-30", **k):
    return du.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, **k)


# ============================================================ paese_da_ticker
@pytest.mark.parametrize("ticker,paese", [
    ("ZZTEST.PA", "FR"), ("ZZTEST.BR", "BE"), ("ZZTEST.OL", "NO"), ("ZZTEST.HE", "FI"), ("ZZTEST.ST", "SE"),
    ("ZZTEST.CO", "DK"), ("ZZTEST.AS", "NL"), ("ZZTEST.MC", "ES"), ("ZZTEST.DE", "DE"), ("ZZTEST.F", "DE"),
    ("ZZTEST.L", "UK"), ("ZZTEST.IR", "IE"), ("ZZTEST.SW", "CH"), ("ZZTEST.VI", "AT"), ("ZZTEST.LS", "PT"),
    ("zztest.mi", "IT")])
def test_paese_da_ticker_suffissi(ticker, paese):
    assert du.paese_da_ticker(ticker)[0] == paese


def test_trappole_be_berlino_e_at_atene():
    assert du.paese_da_ticker("ZZTEST.BE")[0] == "DE"      # Borsa di Berlino, NON Belgio
    assert du.paese_da_ticker("ZZTEST.AT")[0] == "GR"      # Atene, NON Austria


@pytest.mark.parametrize("ticker,parola", [("ZZTEST", "senza suffisso"), ("ZZTEST.HK", "fuori dall'Europa"),
                                           ("ZZTEST.WA", "non censito"), ("", "assente")])
def test_paese_none_col_motivo(ticker, parola):
    paese, motivo = du.paese_da_ticker(ticker)
    assert paese is None and parola in motivo


def test_registro_unico_mercati_vince_sul_supplemento():
    """Ogni listino europeo di mercati.MERCATI ha un paese; un suffisso del supplemento entrato nel registro
    usa il registro e da' lo stesso paese."""
    for suff, dati in du.MERCATI.items():
        if dati[1].startswith("Europe/"):
            assert du.paese_da_ticker("ZZTEST" + suff)[0] is not None, suff
    for suff, (_n, fuso) in du._SUPPLEMENTO_LISTINI.items():
        if suff in du.MERCATI:
            assert du._PAESE_PER_FUSO[du.MERCATI[suff][1]] == du._PAESE_PER_FUSO[fuso], suff
    assert "mercati.MERCATI" in du.paese_da_ticker("ZZTEST.PA")[1]
    assert "supplemento" in du.paese_da_ticker("ZZTEST.OL")[1]


def test_copertura_ue_tabella():
    coperti = {p: v["modulo"] for p, v in du.COPERTURA_UE.items() if v["modulo"]}
    assert coperti == {"FR": "ue_amf", "BE": "ue_fsma", "NO": "ue_newsweb", "FI": "ue_nasdaq_nordic",
                       "SE": "ue_nasdaq_nordic", "DK": "ue_nasdaq_nordic", "NL": "ue_afm", "ES": "ue_cnmv"}
    assert du.COPERTURA_UE["NL"]["natura_data"] == "deposito_autorita"
    assert du.COPERTURA_UE["BE"]["natura_data"] == "pubblicazione_dichiarata"
    assert du.COPERTURA_UE["SE"]["canale"] == du.COPERTURA_UE["DK"]["canale"] == "borsa"
    assert du.COPERTURA_UE["FI"]["canale"] == "OAM"
    for p in ("DE", "UK", "IE", "CH", "AT", "PT", "GR"):
        v = du.COPERTURA_UE[p]
        assert v["modulo"] is None and "fornire dal PM" in v["motivo_limite"] and "PM" in v["motivo_limite_en"]
    assert "Unternehmensregister" in du.COPERTURA_UE["DE"]["motivo_limite"]
    assert "National Storage Mechanism" in du.COPERTURA_UE["UK"]["motivo_limite"]
    assert "Euronext Dublin" in du.COPERTURA_UE["IE"]["motivo_limite"]
    assert "fuori UE" in du.COPERTURA_UE["CH"]["motivo_limite"]


# ============================================================ parametri e non coperti
def test_mi_non_passa_di_qui(tutti_assenti):
    r = _chiama("QQSYN.MI")
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and "sdir.py" in r["motivo"]


@pytest.mark.parametrize("ticker", ["ZZTEST", "BRK.B"])
def test_senza_listino_ue_e_parametro(tutti_assenti, ticker):
    r = _chiama(ticker)
    assert (r["stato"], r["errore"]) == ("KO", "parametro")


@pytest.mark.parametrize("ticker,paese,parola", [
    ("ZZTEST.DE", "DE", "Germania"), ("ZZTEST.L", "UK", "Regno Unito"), ("ZZTEST.IR", "IE", "Irlanda"),
    ("ZZTEST.SW", "CH", "Svizzera"), ("ZZTEST.VI", "AT", "Austria"), ("ZZTEST.LS", "PT", "Portogallo"),
    ("ZZTEST.BE", "DE", "Germania")])
def test_paesi_non_coperti_dichiarati(tutti_assenti, ticker, paese, parola):
    r = _chiama(ticker)
    assert r["stato"] == "non_coperto" and r["errore"] == "paese_non_coperto"
    assert r["paese"] == paese and parola in r["motivo"] and "fornire dal PM" in r["motivo"]
    assert r["limiti"] == [r["motivo"]] and r["fonte_modulo"] is None
    assert set(du._CHIAVI_CONTRATTO) <= set(r)


def test_extra_ue_e_listino_non_censito(tutti_assenti):
    hk, wa = _chiama("ZZTEST.HK"), _chiama("ZZTEST.WA")
    assert (hk["stato"], hk["errore"]) == ("non_coperto", "fuori_perimetro")
    assert (wa["stato"], wa["errore"]) == ("non_coperto", "paese_non_censito")
    assert "fornire dal PM" in hk["motivo"] and "fornire dal PM" in wa["motivo"]


@pytest.mark.parametrize("tipo,fine", [("mensile", "2026-06-30"), ("semestrale", "30/06/2026"),
                                       ("semestrale", "2026-03-31"), ("trimestrale", "2026-06-30"),
                                       ("annuale", "2026-06-15")])
def test_parametri_sbagliati_prima_della_fonte(monkeypatch, tipo, fine):
    spia = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    r = _chiama("ZZTEST.PA", tipo=tipo, fine=fine)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and spia.chiamate == []


# ============================================================ instradamento
def test_francia_instradata_al_modulo(monkeypatch):
    spia = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    r = _chiama("zztest.pa", isin=ISIN_FR, lei="LEI0SINTETICO", nome="ACME SINTETICA")
    assert spia.chiamate == [dict(ticker="ZZTEST.PA", tipo="semestrale", periodo_fine=date(2026, 6, 30),
                                  isin=ISIN_FR, lei="LEI0SINTETICO", nome="ACME SINTETICA")]
    assert r["stato"] == "ok" and r["data_deposito"] == "2026-07-24"
    assert r["fonte_modulo"] == "bellomberg.market_data.ue_amf"
    assert r["limiti"][0] == "limite del modulo" and du.LIMITE_PAESE_ISIN in r["limiti"]
    assert du.LIMITE_PAESE_LISTINO not in r["limiti"]
    assert r["instradamento"]["regola"] == du.REGOLA_ISIN and r["instradamento"]["discordanza"] is None
    assert du.LIMITE_CANALE_BORSA not in r["limiti"]
    assert r["instradamento"]["paese"] == "FR" and r["instradamento"]["modulo"] == PKG + "ue_amf"


def test_prova_e_scartati_passano_intatti(monkeypatch):
    _finto(monkeypatch, "ue_fsma", ritorno=lambda t: _ritorno(t, "BE", natura_data="pubblicazione_dichiarata"))
    r = _chiama("ZZTEST.BR")
    atteso = _ritorno("ZZTEST.BR", "BE")
    assert r["prova"] == atteso["prova"] and r["scartati"] == atteso["scartati"]
    assert r["risposte_salvate"] == atteso["risposte_salvate"] and r["sha256_liste"] == atteso["sha256_liste"]


@pytest.mark.parametrize("ticker,paese,canale_borsa", [("ZZTEST.ST", "SE", True), ("ZZTEST.CO", "DK", True),
                                                       ("ZZTEST.HE", "FI", False)])
def test_nasdaq_nordic_canale_di_borsa(monkeypatch, ticker, paese, canale_borsa):
    _finto(monkeypatch, "ue_nasdaq_nordic", ritorno=lambda t: _ritorno(t, paese))
    r = _chiama(ticker)
    assert r["fonte_modulo"] == PKG + "ue_nasdaq_nordic"
    assert (du.LIMITE_CANALE_BORSA in r["limiti"]) is canale_borsa


@pytest.mark.parametrize("ticker,modulo,paese", [("ZZTEST.OL", "ue_newsweb", "NO"), ("ZZTEST.AS", "ue_afm", "NL"),
                                                 ("ZZTEST.MC", "ue_cnmv", "ES")])
def test_altri_paesi_instradati(monkeypatch, ticker, modulo, paese):
    natura = du.COPERTURA_UE[paese]["natura_data"]
    _finto(monkeypatch, modulo, ritorno=lambda t: _ritorno(t, paese, natura_data=natura))
    r = _chiama(ticker)
    assert r["stato"] == "ok" and r["fonte_modulo"] == PKG + modulo
    assert not [x for x in r["limiti"] if "natura_data" in x]


def test_natura_diversa_dal_registro_dichiarata(monkeypatch):
    _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL", natura_data="diffusione"))
    r = _chiama("ZZTEST.AS")
    assert any("natura_data dichiarata dal modulo (diffusione)" in x for x in r["limiti"])


@pytest.mark.parametrize("stato", ["non_trovato", "ambiguo", "STALE", "KO", "non_coperto"])
def test_stati_del_modulo_passano(monkeypatch, stato):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR", stato=stato, motivo="dal modulo"))
    r = _chiama("ZZTEST.PA")
    assert r["stato"] == stato and r["motivo"] == "dal modulo" and r["fonte_modulo"] == PKG + "ue_amf"


# ============================================================ modulo assente / guasto
def test_modulo_assente_e_ko_dichiarato_mai_non_coperto(tutti_assenti):
    for t, mod in (("ZZTEST.PA", "ue_amf"), ("ZZTEST.ST", "ue_nasdaq_nordic"), ("ZZTEST.MC", "ue_cnmv")):
        r = _chiama(t)
        assert (r["stato"], r["errore"]) == ("KO", "modulo_assente"), t
        assert PKG + mod in r["motivo"] and "ModuleNotFoundError" in r["motivo"]
        assert set(du._CHIAVI_CONTRATTO) <= set(r)


def test_modulo_senza_funzione_e_assente(monkeypatch):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"), senza=("get_data_deposito",))
    r = _chiama("ZZTEST.PA")
    assert (r["stato"], r["errore"]) == ("KO", "modulo_assente") and "interfaccia" in r["motivo"]


def test_eccezione_del_modulo_solo_il_tipo(monkeypatch):
    _finto(monkeypatch, "ue_amf", solleva=RuntimeError("https://x.example/?apikey=SEGRETO"))
    r = _chiama("ZZTEST.PA")
    assert (r["stato"], r["errore"]) == ("KO", "modulo_guasto")
    assert "RuntimeError" in r["motivo"] and "SEGRETO" not in repr(r)


@pytest.mark.parametrize("guasta,parola", [
    (lambda r: "non un dict", "invece di un dict"),
    (lambda r: dict(r, stato="forse"), "fuori contratto"),
    (lambda r: {k: v for k, v in r.items() if k != "prova"}, "mancanti: prova"),
    (lambda r: {k: v for k, v in r.items() if k != "sha256_liste"}, "mancanti: sha256_liste"),
    (lambda r: dict(r, ticker="ALTRO.PA"), "ticker restituito"),
    (lambda r: dict(r, fonte_modulo=PKG + "ue_fsma"), "fonte_modulo dichiarata"),
    (lambda r: dict(r, data_deposito=None), "ok senza data_deposito")])
def test_ritorno_fuori_contratto_e_modulo_guasto(monkeypatch, guasta, parola):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: guasta(_ritorno(t, "FR")))
    r = _chiama("ZZTEST.PA")
    assert (r["stato"], r["errore"]) == ("KO", "modulo_guasto") and parola in r["motivo"]
    assert r["data_deposito"] is None


# ============================================================ riverifica
def _ricevuta(paese="FR", modulo="ue_amf", isin_instradamento=None):
    r = _ritorno("ZZTEST.PA", paese)
    r["fonte_modulo"] = PKG + modulo
    r["isin_instradamento"] = isin_instradamento
    r["dopo_sdir_italiano"] = False
    return r


def test_riverifica_instradata(monkeypatch):
    spia = _finto(monkeypatch, "ue_amf", ritorno=None)
    ok, motivo = du.riverifica_ricevuta(_ricevuta(), ticker="zztest.pa", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is True and "coincide" in motivo
    assert spia.riverifiche == [dict(ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")]


@pytest.mark.parametrize("fm", [None, "bellomberg.market_data.emarket_sdir", "os", PKG + "ue_inventato"])
def test_riverifica_modulo_non_in_elenco(monkeypatch, fm):
    spia = _finto(monkeypatch, "ue_amf", ritorno=None)
    ric = _ricevuta()
    ric["fonte_modulo"] = fm
    ok, motivo = du.riverifica_ricevuta(ric, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "non in elenco" in motivo and spia.riverifiche == []


def test_riverifica_modulo_di_altro_paese_rifiutata(monkeypatch):
    spia = _finto(monkeypatch, "ue_fsma", ritorno=None)
    ok, motivo = du.riverifica_ricevuta(_ricevuta("BE", "ue_fsma"), ticker="ZZTEST.PA", tipo="semestrale",
                                        periodo_fine="2026-06-30")
    assert ok is False and "non e' la fonte del paese di instradamento" in motivo and spia.riverifiche == []


def test_riverifica_paese_della_ricevuta_diverso(monkeypatch):
    spia = _finto(monkeypatch, "ue_nasdaq_nordic", ritorno=None)
    ok, motivo = du.riverifica_ricevuta(_ricevuta("FI", "ue_nasdaq_nordic"), ticker="ZZTEST.ST",
                                        tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "paese della ricevuta" in motivo and spia.riverifiche == []


def test_riverifica_modulo_assente(tutti_assenti):
    ok, motivo = du.riverifica_ricevuta(_ricevuta(), ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "modulo_assente" in motivo


@pytest.mark.parametrize("risposta,parola", [(("si", "x"), "fuori contratto"), ([True, "x"], "fuori contratto"),
                                             (ValueError("dettaglio SEGRETO"), "ValueError"),
                                             ((False, "sha256 diverso"), "sha256 diverso")])
def test_riverifica_esiti_negativi(monkeypatch, risposta, parola):
    _finto(monkeypatch, "ue_amf", ritorno=None, riverifica=risposta)
    ok, motivo = du.riverifica_ricevuta(_ricevuta(), ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and parola in motivo and "SEGRETO" not in motivo


def test_riverifica_non_dict():
    assert du.riverifica_ricevuta("ricevuta", ticker="ZZTEST.PA", tipo="annuale", periodo_fine="2025-12-31")[0] is False


def test_es_annuale_deposito_autorita_non_e_divergenza(monkeypatch):
    """CNMV: annuale = data di invio IFA (deposito_autorita), semestrale = diffusione; risposte_salvate
    compresse (dict) passano intatte."""
    compresso = {"https://fonte.example/lista": {"codifica": "gzip+base64", "corpo": "QUJD"}}
    _finto(monkeypatch, "ue_cnmv", ritorno=lambda t: _ritorno(t, "ES", natura_data="deposito_autorita",
                                                              risposte_salvate=compresso, nif="Z-00000000"))
    r = _chiama("ZZTEST.MC", tipo="annuale", fine="2025-12-31")
    assert r["stato"] == "ok" and not [x for x in r["limiti"] if "natura_data" in x]
    assert r["risposte_salvate"] == compresso and r["nif"] == "Z-00000000"
    r = _chiama("ZZTEST.MC", tipo="semestrale", fine="2026-06-30")
    assert any("natura_data dichiarata dal modulo (deposito_autorita)" in x for x in r["limiti"])


# ============================================================ paese d'origine dall'ISIN (seguito main 05/10)
def test_oracolo_isin_coincide_col_modulo():
    assert all(du.isin_valido(i) for i in (ISIN_FR, ISIN_NL, ISIN_DE, ISIN_GB, ISIN_IT, ISIN_XS, ISIN_LU))
    assert not du.isin_valido(ISIN_FR[:-1] + str((int(ISIN_FR[-1]) + 1) % 10))
    assert not du.isin_valido("FR00000000") and not du.isin_valido(None)


def test_emittente_nl_quotato_a_parigi_va_all_afm(monkeypatch):
    spia_amf = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    spia_afm = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL", natura_data="deposito_autorita"))
    r = _chiama("ZZTEST.PA", isin=ISIN_NL)
    assert spia_amf.chiamate == [] and len(spia_afm.chiamate) == 1 and spia_afm.chiamate[0]["isin"] == ISIN_NL
    assert r["stato"] == "ok" and r["paese"] == "NL" and r["fonte_modulo"] == PKG + "ue_afm"
    i = r["instradamento"]
    assert (i["regola"], i["paese_listino"], i["paese_isin"]) == (du.REGOLA_ISIN, "FR", "NL")
    assert "listino FR, ISIN NL" in i["discordanza"]
    assert du.LIMITE_PAESE_ISIN in r["limiti"] and du.LIMITE_PAESE_LISTINO not in r["limiti"]


def test_isin_di_paese_non_coperto_e_non_coperto_col_suo_testo(monkeypatch):
    spia = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    r = _chiama("ZZTEST.PA", isin=ISIN_DE)
    assert (r["stato"], r["errore"], r["paese"]) == ("non_coperto", "paese_non_coperto", "DE")
    assert "Germania" in r["motivo"] and "paese dall'ISIN" in r["motivo"] and spia.chiamate == []
    r = _chiama("ZZTEST.AS", isin=ISIN_GB)
    assert r["paese"] == "UK" and "Regno Unito" in r["motivo"]


@pytest.mark.parametrize("isin", [None, "", ISIN_XS, ISIN_LU])
def test_isin_assente_o_non_nazionale_resta_il_listino(monkeypatch, isin):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    r = _chiama("ZZTEST.PA", isin=isin)
    assert r["paese"] == "FR" and r["instradamento"]["regola"] == du.REGOLA_LISTINO
    assert du.LIMITE_PAESE_LISTINO in r["limiti"] and r["instradamento"]["discordanza"] is None


def test_isin_non_valido_e_parametro(monkeypatch):
    spia = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    r = _chiama("ZZTEST.PA", isin=ISIN_FR[:-1] + str((int(ISIN_FR[-1]) + 1) % 10))
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and "non valido" in r["motivo"] and spia.chiamate == []


def test_isin_italiano_va_a_sdir_anche_fuori_da_mi(tutti_assenti):
    r = _chiama("ZZTEST.DE", isin=ISIN_IT)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and "sdir.py" in r["motivo"]


@pytest.mark.parametrize("isin", [ISIN_NL, ISIN_LU, ISIN_IT, None])
def test_mi_resta_parametro_qualunque_isin(monkeypatch, isin):
    """main 05/10: chi e' quotato a Milano diffonde via SDIR italiano (misurato: emittenti LU e NL su eMarket/1INFO)."""
    spia = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL"))
    r = _chiama("QQSYN.MI", isin=isin)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and du.MOTIVO_MI in r["motivo"]
    assert spia.chiamate == [] and r["instradamento"]["paese_listino"] == "IT"


def test_paese_lo_scrive_il_router_non_il_modulo(monkeypatch):
    """main 05/10: ue_afm instradato dall'ISIN NL per un ticker .PA, che deduce il paese dal suffisso (FR):
    non e' un KO; il router scrive NL e conserva FR in paese_modulo."""
    _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "FR", natura_data="deposito_autorita"))
    r = _chiama("ZZTEST.PA", isin=ISIN_NL)
    assert r["stato"] == "ok" and r["paese"] == "NL" and r["paese_modulo"] == "FR"
    assert "il modulo ha scritto paese 'FR'" in r["instradamento"]["paese_modulo"]


@pytest.mark.parametrize("dal_modulo", ["NL", None])
def test_paese_modulo_uguale_o_assente_nessuna_nota(monkeypatch, dal_modulo):
    _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, dal_modulo, natura_data="deposito_autorita"))
    r = _chiama("ZZTEST.AS")
    assert r["stato"] == "ok" and r["paese"] == "NL" and r["paese_modulo"] == dal_modulo
    assert r["instradamento"]["paese_modulo"] is None


def test_senza_listino_europeo_l_isin_non_instrada(tutti_assenti):
    r = _chiama("ZZTEST", isin=ISIN_FR)
    assert (r["stato"], r["errore"]) == ("KO", "parametro")


def test_riverifica_usa_l_isin_sigillato(monkeypatch):
    spia = _finto(monkeypatch, "ue_afm", ritorno=None)
    ric = _ricevuta("NL", "ue_afm", isin_instradamento=ISIN_NL)
    ok, motivo = du.riverifica_ricevuta(ric, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is True and len(spia.riverifiche) == 1
    ric["isin_instradamento"] = None          # senza ISIN sigillato il paese torna quello del listino (FR): AFM rifiutato
    ok, motivo = du.riverifica_ricevuta(ric, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "atteso bellomberg.market_data.ue_amf" in motivo


def test_riverifica_isin_sigillato_non_valido(monkeypatch):
    _finto(monkeypatch, "ue_amf", ritorno=None)
    ric = _ricevuta()
    ric["isin_instradamento"] = "FR00000000ZZ"
    ok, motivo = du.riverifica_ricevuta(ric, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "non valido" in motivo


# ============================================================ nomi_documento (AGGIUNTA 2)
def _con_nomi(monkeypatch, modulo, nomi):
    _finto(monkeypatch, modulo, ritorno=None)
    if nomi is not _SENZA:
        sys.modules[PKG + modulo].NOMI_DOCUMENTO = nomi


_SENZA = object()


def test_nomi_documento_dal_modulo(monkeypatch):
    nomi = {"annuale": [r"rapport\s+financier\s+annuel", r"document\s+d.enregistrement\s+universel"],
            "semestrale": [r"rapport\s+financier\s+semestriel"]}
    _con_nomi(monkeypatch, "ue_amf", nomi)
    lista, motivo = du.nomi_documento("FR", "annuale")
    assert lista == nomi["annuale"] and "2 nomi" in motivo
    lista.append("x")
    assert len(nomi["annuale"]) == 2            # copia: la costante del modulo non si tocca
    assert du.nomi_documento("fr", "semestrale")[0] == nomi["semestrale"]


def test_nomi_documento_nordic_condiviso(monkeypatch):
    _con_nomi(monkeypatch, "ue_nasdaq_nordic", {"semestrale": [r"half[- ]year"]})
    assert du.nomi_documento("SE", "semestrale")[0] == du.nomi_documento("DK", "semestrale")[0] == [r"half[- ]year"]


@pytest.mark.parametrize("nomi,parola", [
    (_SENZA, "senza NOMI_DOCUMENTO"), (["non un dict"], "senza NOMI_DOCUMENTO"),
    ({"semestrale": [r"x"]}, "nessun nome"), ({"annuale": []}, "nessun nome"),
    ({"annuale": r"una stringa sola"}, "non e' una lista"), ({"annuale": [r"ok", 3]}, "non e' una lista"),
    ({"annuale": [r"(aperta"]}, "non compila")])
def test_nomi_documento_malformati_dichiarati(monkeypatch, nomi, parola):
    _con_nomi(monkeypatch, "ue_amf", nomi)
    lista, motivo = du.nomi_documento("FR", "annuale")
    assert lista is None and parola in motivo


def test_nomi_documento_non_coperto_assente_tipo(tutti_assenti):
    assert du.nomi_documento("FR", "annuale")[0] is None
    assert "modulo_assente" in du.nomi_documento("FR", "annuale")[1]
    lista, motivo = du.nomi_documento("DE", "annuale")
    assert lista is None and "Germania" in motivo
    assert du.nomi_documento("XX", "annuale")[0] is None and "non censito" in du.nomi_documento("XX", "annuale")[1]
    assert du.nomi_documento("FR", "mensile")[0] is None


def test_paese_di_instradamento_mi_resta_it_con_isin_estero():
    """Anche la funzione pubblica (usata da D4 e dalla riverifica) non instrada .MI dall'ISIN."""
    s = du.paese_di_instradamento("QQSYN.MI", ISIN_NL)
    assert (s["paese"], s["regola"], s["paese_isin"]) == ("IT", du.REGOLA_LISTINO, "NL")
    assert du.MOTIVO_MI in s["perche"]


# ============================================================ AGGIUNTA 4 / rilievi RV-UE2
def test_router_passa_il_paese_instradato(monkeypatch):
    """AGGIUNTA 4.1 (R-1): .DE con ISIN SE va a nasdaq col paese SE passato, il modulo non lo indovina dal suffisso."""
    spia = _finto(monkeypatch, "ue_nasdaq_nordic", ritorno=lambda t: _ritorno(t, "SE"))
    r = _chiama("ZZTEST.DE", isin=_isin("SE000000000"))
    assert spia.paesi == ["SE"] and r["stato"] == "ok" and r["paese"] == "SE"
    spia2 = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR"))
    _chiama("ZZTEST.PA")
    assert spia2.paesi == ["FR"]


def test_modulo_senza_kwarg_paese_e_guasto_senza_chiamata(monkeypatch):
    mod = types.ModuleType(PKG + "ue_amf")
    chiamate = []

    def get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None):
        chiamate.append(ticker)
        return _ritorno(ticker, "FR")
    mod.get_data_deposito = get_data_deposito
    monkeypatch.setitem(sys.modules, PKG + "ue_amf", mod)
    r = _chiama("ZZTEST.PA")
    assert (r["stato"], r["errore"]) == ("KO", "modulo_guasto") and "kwarg 'paese'" in r["motivo"]
    assert chiamate == []


def test_modulo_con_kwargs_generici_accettato(monkeypatch):
    mod = types.ModuleType(PKG + "ue_amf")
    mod.get_data_deposito = lambda ticker, **k: _ritorno(ticker, k["paese"])
    monkeypatch.setitem(sys.modules, PKG + "ue_amf", mod)
    assert _chiama("ZZTEST.PA")["stato"] == "ok"


@pytest.mark.parametrize("guasta,parola", [
    (lambda r: dict(r, fuso=None), "senza fuso"),
    (lambda r: dict(r, stato="STALE", fuso=None), "senza fuso"),
    (lambda r: dict(r, natura_data="inventata"), "natura_data 'inventata' fuori contratto"),
    (lambda r: dict(r, natura_data=None), "natura_data None fuori contratto"),
    (lambda r: dict(r, isin=_isin("FR111111111")), "isin restituito")])
def test_aggiunta_4_ok_senza_fuso_natura_isin(monkeypatch, guasta, parola):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: guasta(_ritorno(t, "FR", isin=ISIN_FR)))
    r = _chiama("ZZTEST.PA", isin=ISIN_FR)
    assert (r["stato"], r["errore"]) == ("KO", "modulo_guasto") and parola in r["motivo"]


def test_non_trovato_senza_fuso_e_natura_va_bene(monkeypatch):
    _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR", stato="non_trovato", natura_data=None))
    assert _chiama("ZZTEST.PA")["stato"] == "non_trovato"


def test_isin_restituito_dal_modulo_senza_isin_chiesto_ammesso(monkeypatch):
    """Il modulo puo' trovare l'ISIN (es. CNMV dal nome): non e' un'altra identita' se il chiamante non l'ha dato.
    La riverifica pero' instrada con isin_instradamento (None = listino), non con l'isin trovato (NQ-1)."""
    spia = _finto(monkeypatch, "ue_amf", ritorno=lambda t: _ritorno(t, "FR", isin=_isin("NL000000000")))
    r = _chiama("ZZTEST.PA")
    assert r["stato"] == "ok" and r["isin_instradamento"] is None
    ok, motivo = du.riverifica_ricevuta(r, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is True and len(spia.riverifiche) == 1


def test_isin_instradamento_sigillato_e_normalizzato(monkeypatch):
    _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL", natura_data="deposito_autorita"))
    r = _chiama("ZZTEST.PA", isin=" " + ISIN_NL.lower() + " ")
    assert r["isin_instradamento"] == ISIN_NL and r["paese"] == "NL"
    assert du.riverifica_ricevuta(r, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")[0] is True


def test_riverifica_senza_isin_instradamento_rifiutata(monkeypatch):
    spia = _finto(monkeypatch, "ue_amf", ritorno=None)
    ric = _ricevuta()
    del ric["isin_instradamento"]
    ok, motivo = du.riverifica_ricevuta(ric, ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "isin_instradamento" in motivo and spia.riverifiche == []


def test_riverifica_passa_il_paese_ricalcolato(monkeypatch):
    """EU-NQ 05/10: .DE instradato per ISIN SE -> la riverifica di nasdaq riceve paese SE (stesso mercato)."""
    spia = _finto(monkeypatch, "ue_nasdaq_nordic", ritorno=lambda t: _ritorno(t, "SE"))
    r = _chiama("ZZTEST.DE", isin=_isin("SE000000000"))
    ok, motivo = du.riverifica_ricevuta(r, ticker="ZZTEST.DE", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is True and spia.paesi_riverifica == ["SE"] and "senza paese" not in motivo


def test_riverifica_modulo_senza_kwarg_paese_rifiutata(monkeypatch):
    """main 05/10: paese obbligatorio anche nella riverifica; il modulo senza kwarg NON viene chiamato."""
    chiamate = []
    mod = types.ModuleType(PKG + "ue_amf")
    mod.riverifica_ricevuta = lambda ricevuta, *, ticker, tipo, periodo_fine: chiamate.append(1) or (True, "coincide")
    monkeypatch.setitem(sys.modules, PKG + "ue_amf", mod)
    ok, motivo = du.riverifica_ricevuta(_ricevuta(), ticker="ZZTEST.PA", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "modulo_guasto" in motivo and "kwarg 'paese'" in motivo and chiamate == []


# ============================================================ .MI dopo gli SDIR italiani (main 05/10, misura MF)
_MOTIVO_SDIR = "sdir: non_coperto (emittente non su eMarket SDIR ne' su 1INFO-SDIR)"


def test_mi_dopo_sdir_italiano_instrada_per_isin(monkeypatch):
    spia = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL", natura_data="deposito_autorita"))
    r = _chiama("QQSYN.MI", isin=ISIN_NL, dopo_sdir_italiano=True, motivo_sdir=_MOTIVO_SDIR)
    assert r["stato"] == "ok" and r["paese"] == "NL" and spia.paesi == ["NL"]
    i = r["instradamento"]
    assert i["regola"] == du.REGOLA_ISIN_DOPO_SDIR and i["dopo_sdir_italiano"] is True and i["paese_listino"] == "IT"
    assert i["perche"].startswith("emittente non trovato sugli SDIR italiani (motivo sdir: " + _MOTIVO_SDIR)
    assert "fonte dello Stato d'origine NL" in i["perche"] and i["motivo_sdir"] == _MOTIVO_SDIR
    assert r["dopo_sdir_italiano"] is True and r["isin_instradamento"] == ISIN_NL
    assert du.LIMITE_PAESE_ISIN in r["limiti"]


def test_mi_senza_kwarg_resta_parametro(monkeypatch):
    spia = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL"))
    r = _chiama("QQSYN.MI", isin=ISIN_NL)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and du.MOTIVO_MI in r["motivo"] and spia.chiamate == []
    assert du.paese_di_instradamento("QQSYN.MI", ISIN_NL)["paese"] == "IT"


@pytest.mark.parametrize("isin,parola", [(ISIN_IT, "non italiano"), (ISIN_LU, "non italiano"), (None, "non italiano")])
def test_mi_dopo_sdir_isin_inadatto_parametro(tutti_assenti, isin, parola):
    r = _chiama("QQSYN.MI", isin=isin, dopo_sdir_italiano=True, motivo_sdir=_MOTIVO_SDIR)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and parola in r["motivo"]


def test_mi_dopo_sdir_paese_non_coperto(tutti_assenti):
    r = _chiama("QQSYN.MI", isin=ISIN_DE, dopo_sdir_italiano=True, motivo_sdir=_MOTIVO_SDIR)
    assert (r["stato"], r["errore"], r["paese"]) == ("non_coperto", "paese_non_coperto", "DE")
    assert "Germania" in r["motivo"] and "SDIR italiani" in r["motivo"]


@pytest.mark.parametrize("ticker,motivo,flag,parola", [
    ("ZZTEST.PA", _MOTIVO_SDIR, True, "solo per i .MI"),
    ("QQSYN.MI", None, True, "richiede motivo_sdir"),
    ("QQSYN.MI", "  ", True, "richiede motivo_sdir"),
    ("QQSYN.MI", _MOTIVO_SDIR, "si", "non e' un booleano")])
def test_dopo_sdir_parametri_sbagliati(monkeypatch, ticker, motivo, flag, parola):
    spia = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL"))
    r = _chiama(ticker, isin=ISIN_NL, dopo_sdir_italiano=flag, motivo_sdir=motivo)
    assert (r["stato"], r["errore"]) == ("KO", "parametro") and parola in r["motivo"] and spia.chiamate == []


def test_riverifica_mi_dopo_sdir(monkeypatch):
    spia = _finto(monkeypatch, "ue_afm", ritorno=lambda t: _ritorno(t, "NL", natura_data="deposito_autorita"))
    r = _chiama("QQSYN.MI", isin=ISIN_NL, dopo_sdir_italiano=True, motivo_sdir=_MOTIVO_SDIR)
    ok, motivo = du.riverifica_ricevuta(r, ticker="QQSYN.MI", tipo="semestrale", periodo_fine="2026-06-30",
                                        dopo_sdir_italiano=True)
    assert ok is True and spia.paesi_riverifica == ["NL"]
    ok, motivo = du.riverifica_ricevuta(r, ticker="QQSYN.MI", tipo="semestrale", periodo_fine="2026-06-30")
    assert ok is False and "dopo_sdir_italiano chiesto False" in motivo
    r2 = dict(r, dopo_sdir_italiano=False)
    ok, motivo = du.riverifica_ricevuta(r2, ticker="QQSYN.MI", tipo="semestrale", periodo_fine="2026-06-30",
                                        dopo_sdir_italiano=True)
    assert ok is False and "sigillato" in motivo
    r3 = {k: v for k, v in r.items() if k != "dopo_sdir_italiano"}
    assert du.riverifica_ricevuta(r3, ticker="QQSYN.MI", tipo="semestrale", periodo_fine="2026-06-30",
                                  dopo_sdir_italiano=True)[0] is False
    assert len(spia.riverifiche) == 1
