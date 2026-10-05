# -*- coding: utf-8 -*-
"""depositi_ue.py — INSTRADATORE UE della data di diffusione delle relazioni periodiche
(handoff-3, 05/10/2026, Opus 5.5, agente EU-R).

Contratto: scratchpad INTERFACCIA_UE.md, sezione «Instradatore UE» (VINCOLANTE). Base della
copertura: ricognizione U1 del 05/10/2026 (fonti, trappole, testi dei limiti per DE/UK/IE/CH/AT/PT).

INTERFACCIA DICHIARATA (chi la cambia avvisa D4):
    paese_da_ticker(ticker) -> (paese | None, motivo)
    paese_di_instradamento(ticker, isin=None, dopo_sdir_italiano=False) -> {paese, regola, paese_listino, paese_isin,
                                                                             discordanza, perche, errore}
    nomi_documento(paese, tipo) -> (lista di regex_str | None, motivo)      # AGGIUNTA 2
    COPERTURA_UE: {paese: {"modulo", "fonte", "natura_data", "canale", "motivo_limite", "motivo_limite_en"}}
    get_data_deposito(ticker, *, tipo, periodo_fine, isin=None, lei=None, nome=None) -> dict
    riverifica_ricevuta(ricevuta, *, ticker, tipo, periodo_fine) -> (ok: bool, motivo: str)

REGOLE:
  1. Il PAESE si legge dal suffisso Yahoo del ticker. Registro unico dei listini = `mercati.MERCATI`
     (lo stesso di `copertura.copertura_usa`): il paese si deriva dal FUSO del listino registrato.
     I suffissi europei che il registro non ha ancora (.OL, .ST, .CO, .MC, .LS, .IR e le borse
     regionali tedesche) stanno in `_SUPPLEMENTO_LISTINI`, dichiarato come tale; se un giorno entrano
     in `mercati.MERCATI` vince il registro. Trappole: `.BE` = Borsa di BERLINO (non Belgio, che e'
     `.BR`); `.AT` = ATENE (non Austria, che e' `.VI`).
  2. `.MI` NON passa di qui: l'Italia e' l'instradatore `sdir.py` -> KO errore 'parametro'.
     Senza suffisso (USA = catalogo SEC) o suffisso di una lettera (classe di azioni) -> 'parametro'.
  3. Paese coperto -> import PIGRO del modulo (scritti da altri agenti in parallelo). Modulo
     assente / import fallito / funzione mancante = KO 'modulo_assente' DICHIARATO, mai non_coperto.
     Il ritorno del modulo si controlla (stato ammesso, chiavi del contratto, ticker e paese
     coerenti): un ritorno fuori contratto = KO 'modulo_guasto', mai passato avanti a meta'.
  4. Paese NON coperto (DE, UK, IE, CH, AT, PT, GR, extra-UE) -> stato 'non_coperto' con il testo
     del limite e «data da fornire dal PM». Mai filings.xbrl.org `date_added` come data.
  5. Le relazioni si depositano all'OAM dello Stato membro d'ORIGINE: se il chiamante passa l'ISIN, il paese
     e' quello del prefisso ISIN (`paese_di_instradamento`, regola «paese dall'ISIN»; solo per i listini UE
     NON italiani: .MI resta 'parametro' qualunque ISIN, ISIN IT -> sdir.py); ISIN assente o prefisso non nazionale (XS, EU, ...) -> paese
     del LISTINO col limite scritto. Listino e ISIN discordi = scritto in `instradamento.discordanza`.
  6. riverifica_ricevuta instrada su ricevuta['fonte_modulo']: un modulo non in elenco, o diverso da
     quello del paese del ticker, = rifiuto. Nessuna rete qui (la fa il modulo, sulle risposte salvate).
"""
import importlib
import inspect
import re
from datetime import date
from typing import Any, Dict, Optional, Tuple

from bellomberg.market_data.emarket_sdir import _TIPI, coerenza_tipo_periodo
from bellomberg.market_data.mercati import MERCATI

PACCHETTO = "bellomberg.market_data"
STATI_MODULO = ("ok", "ambiguo", "non_trovato", "KO", "non_coperto", "STALE")
NATURE_DATA = ("diffusione", "deposito_autorita", "pubblicazione_dichiarata")

# Paese dal fuso del listino (un fuso europeo = un paese). Solo Europa: un fuso extra-europeo
# (.HK, .T, .TO del registro) -> nessun paese UE, stato non_coperto «fuori perimetro».
_PAESE_PER_FUSO = {
    "Europe/Rome": "IT", "Europe/Berlin": "DE", "Europe/Vienna": "AT", "Europe/London": "UK",
    "Europe/Amsterdam": "NL", "Europe/Paris": "FR", "Europe/Brussels": "BE", "Europe/Helsinki": "FI",
    "Europe/Athens": "GR", "Europe/Zurich": "CH", "Europe/Oslo": "NO", "Europe/Stockholm": "SE",
    "Europe/Copenhagen": "DK", "Europe/Madrid": "ES", "Europe/Lisbon": "PT", "Europe/Dublin": "IE",
}

# Suffissi Yahoo europei ASSENTI da mercati.MERCATI (05/10/2026): (nome del listino, fuso).
# Supplemento dichiarato, non un secondo registro: per i suffissi gia' nel registro vince il registro.
_SUPPLEMENTO_LISTINI = {
    ".OL": ("Oslo Bors", "Europe/Oslo"),
    ".ST": ("Nasdaq Stockholm", "Europe/Stockholm"),
    ".CO": ("Nasdaq Copenhagen", "Europe/Copenhagen"),
    ".MC": ("Bolsa de Madrid", "Europe/Madrid"),
    ".LS": ("Euronext Lisbona", "Europe/Lisbon"),
    ".IR": ("Euronext Dublin", "Europe/Dublin"),
    ".BE": ("Borsa di Berlino", "Europe/Berlin"),       # NON Belgio (Bruxelles e' .BR)
    ".DU": ("Borsa di Duesseldorf", "Europe/Berlin"),
    ".HM": ("Borsa di Amburgo", "Europe/Berlin"),
    ".MU": ("Borsa di Monaco", "Europe/Berlin"),
    ".SG": ("Borsa di Stoccarda", "Europe/Berlin"),
}

_PM = "Data di pubblicazione da fornire dal PM."
_PM_EN = "Publication date to be supplied by the PM."
_LIMITE_AT_PT = ("OAM nazionale senza interfaccia interrogabile individuata (ricognizione 05/10/2026). "
                 "Data da fornire dal PM.")
_LIMITE_AT_PT_EN = ("national OAM without a queryable interface found (survey of 05/10/2026). "
                    "Date to be supplied by the PM.")


def _coperto(modulo: str, fonte: str, natura: str, canale: str = "OAM") -> Dict[str, Any]:
    return {"modulo": modulo, "fonte": fonte, "natura_data": natura, "canale": canale,
            "motivo_limite": None, "motivo_limite_en": None}


def _scoperto(it: str, en: str) -> Dict[str, Any]:
    return {"modulo": None, "fonte": None, "natura_data": None, "canale": None,
            "motivo_limite": it, "motivo_limite_en": en}


COPERTURA_UE: Dict[str, Dict[str, Any]] = {
    "FR": _coperto("ue_amf", "AMF info-financiere (OAM Francia)", "diffusione"),
    "BE": _coperto("ue_fsma", "FSMA STORI (OAM Belgio)", "pubblicazione_dichiarata"),
    "NO": _coperto("ue_newsweb", "Oslo Bors NewsWeb (OAM Norvegia)", "diffusione"),
    "FI": _coperto("ue_nasdaq_nordic", "Nasdaq Helsinki (OAM Finlandia)", "diffusione"),
    "SE": _coperto("ue_nasdaq_nordic", "Nasdaq Stockholm (canale di borsa, non l'OAM svedese)",
                   "diffusione", canale="borsa"),
    "DK": _coperto("ue_nasdaq_nordic", "Nasdaq Copenhagen (canale di borsa, non l'OAM danese)",
                   "diffusione", canale="borsa"),
    "NL": _coperto("ue_afm", "AFM registro Financiele verslaggeving (deposito presso l'autorita')",
                   "deposito_autorita"),
    "ES": _coperto("ue_cnmv", "CNMV (registro ufficiale Spagna)", "diffusione"),   # annuale: v. sotto
    "DE": _scoperto(
        "Germania: nessuna fonte ufficiale gratuita interrogabile. Il Unternehmensregister vieta la ricerca "
        "automatica (robots.txt) e filings.xbrl.org non ha depositi tedeschi (0 misurati il 05/10/2026). " + _PM,
        "Germany: no free official source that can be queried. The Unternehmensregister forbids automated "
        "search (robots.txt) and filings.xbrl.org has no German filings (0 measured on 05/10/2026). " + _PM_EN),
    "UK": _scoperto(
        "Regno Unito: l'FCA non consente l'accesso automatico al National Storage Mechanism (FAQ FCA). " + _PM,
        "United Kingdom: the FCA does not allow automated access to the National Storage Mechanism "
        "(FCA FAQ). " + _PM_EN),
    "IE": _scoperto(
        "Irlanda: l'OAM e' Euronext Dublin, le cui pagine notizie emittenti sono escluse dal robots.txt; "
        "filings.xbrl.org 0 depositi IE. Data da fornire dal PM.",
        "Ireland: the OAM is Euronext Dublin, whose issuer-news pages are excluded by robots.txt; "
        "filings.xbrl.org has 0 IE filings. Date to be supplied by the PM."),
    "CH": _scoperto(
        "Svizzera: nessun meccanismo ufficiale centrale di deposito (fuori UE). Data da fornire dal PM.",
        "Switzerland: no central official storage mechanism (outside the EU). Date to be supplied by the PM."),
    "AT": _scoperto("Austria: " + _LIMITE_AT_PT, "Austria: " + _LIMITE_AT_PT_EN),
    "PT": _scoperto("Portogallo: " + _LIMITE_AT_PT, "Portugal: " + _LIMITE_AT_PT_EN),
    "GR": _scoperto(
        "Grecia: paese non compreso nella ricognizione UE del 05/10/2026, nessuna fonte ufficiale "
        "agganciata. Data da fornire dal PM.",
        "Greece: country not included in the EU survey of 05/10/2026, no official source connected. "
        "Date to be supplied by the PM."),
}

# CNMV (EU-NLES 05/10): per l'annuale la data e' l'invio IFA all'autorita', non la diffusione.
COPERTURA_UE["ES"]["natura_data_per_tipo"] = {"annuale": "deposito_autorita"}

MODULI_AMMESSI = frozenset(PACCHETTO + "." + v["modulo"] for v in COPERTURA_UE.values() if v["modulo"])

LIMITE_PAESE_LISTINO = ("il paese e' quello del LISTINO (suffisso del ticker), non lo Stato membro d'origine "
                        "dell'emittente: un emittente con OAM d'origine diverso dal paese del listino puo' "
                        "risultare non_trovato in questa fonte")
LIMITE_PAESE_ISIN = ("paese d'origine dal prefisso ISIN (paese dell'agenzia che l'ha assegnato, di norma quello di "
                     "costituzione): se lo Stato membro d'origine dell'emittente e' un altro, puo' risultare "
                     "non_trovato in questa fonte")
LIMITE_CANALE_BORSA = ("canale di BORSA Nasdaq, non l'OAM nazionale: la data e' quella della diffusione sul "
                       "canale di borsa")

_CHIAVI_CONTRATTO = (
    "ticker", "isin", "lei", "nome", "tipo", "periodo_fine", "stato", "errore", "motivo",
    "data_deposito", "ora_deposito", "fuso", "natura_data", "titolo", "url", "url_documento",
    "sha256_documento", "categoria", "lingua", "candidati", "conferme", "fonte", "paese",
    "url_liste", "sha256_liste", "risposte_salvate", "fonte_modulo", "pagine_lette", "letto_il",
    "limiti", "cache",
    "prova", "scartati")    # AGGIUNTA 05/10 del contratto: passano INTATTE dal modulo al chiamante


# ============================================================
# PAESE DAL TICKER
# ============================================================
def _listino(ticker: Any) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(ticker MAIUSCOLO, suffisso, nome del listino, fuso) — listino None se il suffisso non e' censito."""
    t = str(ticker or "").strip().upper()
    if "." not in t:
        return t, None, None, None
    suff = "." + t.rsplit(".", 1)[1]
    dati = MERCATI.get(suff)
    if dati is not None:
        return t, suff, dati[0], dati[1]
    dati = _SUPPLEMENTO_LISTINI.get(suff)
    if dati is not None:
        return t, suff, dati[0], dati[1]
    return t, suff, None, None


def paese_da_ticker(ticker: Any) -> Tuple[Optional[str], str]:
    """(paese, motivo). Paese = codice di COPERTURA_UE ('FR', 'UK', ...) o 'IT' per .MI; None con il motivo
    quando il simbolo non identifica un listino europeo (senza suffisso, suffisso non censito, extra-UE)."""
    t, suff, nome, fuso = _listino(ticker)
    if not t:
        return None, "ticker assente"
    if suff is None:
        return None, "%s senza suffisso di borsa: non identifica un listino europeo" % t
    if nome is None:
        return None, "%s: suffisso %s non censito (ne' in mercati.MERCATI ne' nel supplemento UE)" % (t, suff)
    paese = _PAESE_PER_FUSO.get(fuso)
    if paese is None:
        return None, "%s: listino %s (%s) fuori dall'Europa" % (t, nome, fuso)
    da = "mercati.MERCATI" if suff in MERCATI else "supplemento depositi_ue"
    return paese, "%s: listino %s (suffisso %s, %s) -> %s" % (t, nome, suff, da, paese)


# ============================================================
# PAESE D'ORIGINE DALL'ISIN (seguito main 05/10)
# ============================================================
# Le relazioni periodiche si depositano all'OAM dello Stato membro d'ORIGINE, non del listino (un emittente
# olandese quotato a Parigi deposita all'AFM). Il prefisso ISIN e' il paese dell'agenzia che l'ha assegnato,
# di norma quello di costituzione dell'emittente: si usa come paese d'origine, DICHIARATO come tale.
# Solo prefissi nazionali dei paesi censiti; XS, EU e ogni altro prefisso -> paese del listino + limite.
_PAESE_PER_PREFISSO_ISIN = {
    "FR": "FR", "BE": "BE", "NO": "NO", "FI": "FI", "SE": "SE", "DK": "DK", "NL": "NL", "ES": "ES",
    "DE": "DE", "GB": "UK", "IE": "IE", "CH": "CH", "AT": "AT", "PT": "PT", "GR": "GR", "IT": "IT",
}
REGOLA_ISIN = "paese dall'ISIN"
MOTIVO_MI = "i .MI passano da sdir.py (diffusione SDIR italiana obbligatoria per i quotati in Italia)"
REGOLA_LISTINO = "paese del listino"
# main 05/10 (misura MF: emittenti NL quotati a Milano assenti da eMarket e 1INFO): per un .MI la fonte dello
# Stato d'origine si chiede SOLO dopo che sdir.py ha risposto non_coperto / KO instradamento_nome_non_trovato.
REGOLA_ISIN_DOPO_SDIR = "paese dall'ISIN dopo gli SDIR italiani"
_FORMA_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def isin_valido(isin: Any) -> bool:
    """Forma ISO 6166 + cifra di controllo (Luhn sulle cifre, lettere A=10 ... Z=35)."""
    i = str(isin or "").strip().upper()
    if not _FORMA_ISIN.match(i):
        return False
    cifre = "".join(str(int(c, 36)) for c in i[:-1])
    somma = 0
    for k, c in enumerate(reversed(cifre)):
        n = int(c) * (2 if k % 2 == 0 else 1)
        somma += n - 9 if n > 9 else n
    return (10 - somma % 10) % 10 == int(i[-1])


def paese_di_instradamento(ticker: Any, isin: Any = None, dopo_sdir_italiano: bool = False) -> Dict[str, Any]:
    """{"paese", "regola" (REGOLA_ISIN | REGOLA_LISTINO), "paese_listino", "paese_isin", "discordanza",
    "perche", "errore" ('parametro' se l'ISIN passato non e' valido, altrimenti None)}.
    ISIN nazionale censito + listino europeo -> paese dall'ISIN; ISIN assente o con prefisso non nazionale ->
    paese del listino (col limite dichiarato da chi chiama). Senza listino europeo l'ISIN non instrada."""
    pl, ml = paese_da_ticker(ticker)
    out = {"paese": pl, "regola": REGOLA_LISTINO, "paese_listino": pl, "paese_isin": None, "discordanza": None,
           "perche": ml, "errore": None}
    if isin is None or not str(isin).strip():
        out["perche"] = "%s; ISIN non passato: paese del listino" % ml
        return out
    i = str(isin).strip().upper()
    if not isin_valido(i):
        out.update(errore="parametro", perche="ISIN %r non valido (forma o cifra di controllo)" % (isin,))
        return out
    pi = _PAESE_PER_PREFISSO_ISIN.get(i[:2])
    out["paese_isin"] = pi
    if pl is None:
        out["perche"] = "%s; ISIN %s non usato: nessun listino europeo da instradare" % (ml, i)
        return out
    if pl == "IT" and not (dopo_sdir_italiano is True and pi not in (None, "IT")):
        # main 05/10: chi e' quotato a Milano diffonde via SDIR italiano qualunque sia l'ISIN; l'eccezione e' solo
        # dopo_sdir_italiano=True con un ISIN nazionale censito non italiano
        out["perche"] = "%s; ISIN %s non usato: %s" % (ml, i, MOTIVO_MI)
        return out
    if pl == "IT":
        out.update(paese=pi, regola=REGOLA_ISIN_DOPO_SDIR,
                   discordanza="listino IT, ISIN %s: vale l'ISIN (Stato d'origine) dopo gli SDIR italiani" % pi,
                   perche="paese dall'ISIN dopo gli SDIR italiani: prefisso %s -> %s (%s)" % (i[:2], pi, ml))
        return out
    if pi is None:
        out["perche"] = "%s; prefisso ISIN %s non nazionale o non censito: paese del listino" % (ml, i[:2])
        return out
    out.update(paese=pi, regola=REGOLA_ISIN,
               discordanza=("listino %s, ISIN %s: vale l'ISIN (Stato d'origine)" % (pl, pi)) if pi != pl else None,
               perche="paese dall'ISIN: prefisso %s -> %s (%s)" % (i[:2], pi, ml))
    return out


# ============================================================
# RITORNO
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    out = {k: None for k in _CHIAVI_CONTRATTO}
    out.update(ticker=str(ticker or "").strip().upper(), isin=isin, lei=lei, nome=nome, tipo=tipo,
               periodo_fine=periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
               stato="KO", sha256_documento=None, candidati=[], conferme=[], url_liste=[], sha256_liste={},
               risposte_salvate={}, pagine_lette=0, limiti=[], scartati=[])
    out["instradamento"] = {"paese": None, "regola": None, "paese_listino": None, "paese_isin": None,
                            "discordanza": None, "paese_modulo": None, "suffisso": None, "listino": None, "modulo": None,
                            "canale": None, "perche": None}
    return out


def _fermo(out: Dict[str, Any], stato: str, errore: Optional[str], motivo: str) -> Dict[str, Any]:
    out.update(stato=stato, errore=errore, motivo=motivo)
    out["instradamento"]["perche"] = out["instradamento"]["perche"] or motivo
    return out


def _accetta_paese(funzione) -> bool:
    """La firma ha il parametro 'paese' (o **kwargs)? AGGIUNTA 4: il router passa il paese instradato."""
    try:
        parametri = inspect.signature(funzione).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(x.name == "paese" or x.kind is inspect.Parameter.VAR_KEYWORD for x in parametri)


def _carica(modulo: str):
    """Import pigro. Ritorna (modulo|None, motivo_se_assente)."""
    nome = PACCHETTO + "." + modulo
    try:
        mod = importlib.import_module(nome)
    except Exception as e:      # ModuleNotFoundError (non ancora scritto) o import rotto
        return None, "modulo %s non disponibile (%s)" % (nome, type(e).__name__)
    return mod, None


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None, dopo_sdir_italiano: bool = False,
                      motivo_sdir: Optional[str] = None) -> Dict[str, Any]:
    """Data di diffusione della relazione `tipo` al `periodo_fine` dalla fonte ufficiale del paese del listino.
    Stati: ok | ambiguo | non_trovato | KO | non_coperto | STALE; chiavi del contratto in OGNI stato, piu'
    `instradamento` {paese, suffisso, listino, modulo, canale, perche}."""
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    if tipo not in _TIPI:
        return _fermo(out, "KO", "parametro", "tipo %r non ammesso: %s" % (tipo, ", ".join(_TIPI)))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return _fermo(out, "KO", "parametro", "periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
    out["periodo_fine"] = fine.isoformat()
    incoerente = coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        return _fermo(out, "KO", "parametro", incoerente)
    t, suff, listino, _fuso = _listino(ticker)
    if dopo_sdir_italiano is not False:
        if dopo_sdir_italiano is not True:
            return _fermo(out, "KO", "parametro", "dopo_sdir_italiano %r non e' un booleano" % (dopo_sdir_italiano,))
        if suff != ".MI":
            return _fermo(out, "KO", "parametro", "dopo_sdir_italiano=True vale solo per i .MI (%s non lo e')" % t)
        if not (isinstance(motivo_sdir, str) and motivo_sdir.strip()):
            return _fermo(out, "KO", "parametro", "dopo_sdir_italiano=True richiede motivo_sdir (l'esito di "
                          "sdir.py: non_coperto o KO instradamento_nome_non_trovato)")
    scelta = paese_di_instradamento(ticker, isin, dopo_sdir_italiano)
    paese, perche = scelta["paese"], scelta["perche"]
    if scelta["regola"] == REGOLA_ISIN_DOPO_SDIR:
        perche = ("emittente non trovato sugli SDIR italiani (motivo sdir: %s): fonte dello Stato d'origine %s. %s"
                  % (motivo_sdir.strip(), paese, perche))
    out["instradamento"].update({k: scelta[k] for k in ("paese", "regola", "paese_listino", "paese_isin",
                                                        "discordanza")}, suffisso=suff, listino=listino,
                                perche=perche, dopo_sdir_italiano=dopo_sdir_italiano is True,
                                motivo_sdir=motivo_sdir.strip() if dopo_sdir_italiano is True else None)
    out["paese"] = paese
    if scelta["errore"]:
        return _fermo(out, "KO", "parametro", perche)
    if scelta["paese_listino"] == "IT" and scelta["regola"] != REGOLA_ISIN_DOPO_SDIR:
        if dopo_sdir_italiano is True:
            return _fermo(out, "KO", "parametro", "%s: dopo gli SDIR italiani serve un ISIN valido con prefisso "
                          "nazionale censito non italiano (%s)" % (t, perche))
        return _fermo(out, "KO", "parametro", "%s: %s" % (t, MOTIVO_MI))
    if paese == "IT":   # ISIN italiano su un listino UE non italiano
        return _fermo(out, "KO", "parametro", "%s: emittente italiano (%s): la data di deposito si chiede "
                      "all'instradatore sdir.py (eMarket SDIR / 1INFO-SDIR), non a depositi_ue" % (t, perche))
    if paese is None:
        if listino is not None:     # listino del registro, ma extra-europeo (.HK, .T, .TO)
            return _fermo(out, "non_coperto", "fuori_perimetro",
                          "%s: fuori dal perimetro UE, nessuna fonte agganciata. %s" % (perche, _PM))
        if suff is not None and len(suff) >= 3 and suff[1:].isalpha():     # listino estero non censito
            return _fermo(out, "non_coperto", "paese_non_censito",
                          "%s: nessuna fonte ufficiale UE agganciata. %s" % (perche, _PM))
        # senza suffisso (USA = catalogo SEC) o suffisso di una lettera / non alfabetico (classe di azioni?)
        return _fermo(out, "KO", "parametro", "%s; nessun listino UE da instradare" % perche)
    voce = COPERTURA_UE.get(paese)
    if voce is None or voce["modulo"] is None:
        motivo = voce["motivo_limite"] if voce else ("%s: nessuna fonte ufficiale agganciata. %s" % (paese, _PM))
        if scelta["regola"] != REGOLA_LISTINO:
            motivo = "%s (%s)" % (motivo, perche)
        out["limiti"] = [motivo]
        return _fermo(out, "non_coperto", "paese_non_coperto", motivo)
    nome_mod = PACCHETTO + "." + voce["modulo"]
    out["instradamento"].update(modulo=nome_mod, canale=voce["canale"])
    mod, assente = _carica(voce["modulo"])
    funzione = getattr(mod, "get_data_deposito", None) if mod is not None else None
    if not callable(funzione):
        out.update(fonte=voce["fonte"], natura_data=voce["natura_data"])
        return _fermo(out, "KO", "modulo_assente", assente or (
            "modulo %s senza get_data_deposito: interfaccia non rispettata" % nome_mod))
    if not _accetta_paese(funzione):
        out.update(fonte=voce["fonte"], natura_data=voce["natura_data"])
        return _fermo(out, "KO", "modulo_guasto", "%s.get_data_deposito non accetta il kwarg 'paese' "
                      "(AGGIUNTA 4 del contratto): nessuna chiamata" % nome_mod)
    try:
        r = funzione(t, tipo=tipo, periodo_fine=fine, isin=isin, lei=lei, nome=nome, paese=paese)
    except Exception as e:
        out.update(fonte=voce["fonte"], natura_data=voce["natura_data"])
        return _fermo(out, "KO", "modulo_guasto", "%s ha sollevato %s" % (nome_mod, type(e).__name__))
    guasto = _fuori_contratto(r, t, paese, nome_mod, isin)
    if guasto:
        out.update(fonte=voce["fonte"], natura_data=voce["natura_data"])
        return _fermo(out, "KO", "modulo_guasto", guasto)
    r = dict(r)
    r["fonte_modulo"] = nome_mod
    # main 05/10: il modulo non riceve il paese e non deve indovinarlo; lo scrive il router. Il valore del
    # modulo si conserva in 'paese_modulo' e, se diverso, si scrive in instradamento (non e' un KO).
    r["paese_modulo"] = r.get("paese")
    r["paese"] = paese
    if r["paese_modulo"] is not None and r["paese_modulo"] != paese:
        out["instradamento"]["paese_modulo"] = ("il modulo ha scritto paese %r, il router ha instradato su %r "
                                                "(%s): vale il router" % (r["paese_modulo"], paese, scelta["regola"]))
    limiti = list(r.get("limiti") or [])
    natura_attesa = (voce.get("natura_data_per_tipo") or {}).get(tipo, voce["natura_data"])
    if r.get("natura_data") is not None and r.get("natura_data") != natura_attesa:
        limiti.append("natura_data dichiarata dal modulo (%s) diversa da quella del registro COPERTURA_UE (%s)"
                      % (r.get("natura_data"), natura_attesa))
    if voce["canale"] == "borsa" and LIMITE_CANALE_BORSA not in limiti:
        limiti.append(LIMITE_CANALE_BORSA)
    limite_paese = LIMITE_PAESE_LISTINO if scelta["regola"] == REGOLA_LISTINO else LIMITE_PAESE_ISIN
    if limite_paese not in limiti:
        limiti.append(limite_paese)
    r["limiti"] = limiti
    # isin con cui il ROUTER ha instradato (None = paese del listino): la riverifica ricalcola il paese da qui
    r["isin_instradamento"] = str(isin).strip().upper() if isin is not None and str(isin).strip() else None
    r["dopo_sdir_italiano"] = dopo_sdir_italiano is True      # sigillato: la riverifica lo confronta
    r["instradamento"] = out["instradamento"]
    return r


def _fuori_contratto(r: Any, ticker: str, paese: str, nome_mod: str, isin: Any = None) -> Optional[str]:
    """None se il ritorno del modulo rispetta il contratto, altrimenti il motivo del KO 'modulo_guasto'."""
    if not isinstance(r, dict):
        return "%s ha restituito %s invece di un dict" % (nome_mod, type(r).__name__)
    if r.get("stato") not in STATI_MODULO:
        return "%s: stato %r fuori contratto (%s)" % (nome_mod, r.get("stato"), ", ".join(STATI_MODULO))
    mancanti = [k for k in _CHIAVI_CONTRATTO if k not in r and k != "fonte_modulo"]
    if mancanti:
        return "%s: chiavi del contratto mancanti: %s" % (nome_mod, ", ".join(mancanti))
    if str(r.get("ticker") or "").strip().upper() != ticker:
        return "%s: ticker restituito %r diverso da quello chiesto %r" % (nome_mod, r.get("ticker"), ticker)
    if r.get("fonte_modulo") not in (None, nome_mod):
        return "%s: fonte_modulo dichiarata %r diversa dal modulo chiamato" % (nome_mod, r.get("fonte_modulo"))
    if r.get("stato") == "ok" and not r.get("data_deposito"):
        return "%s: stato ok senza data_deposito" % nome_mod
    chiesto = str(isin or "").strip().upper()
    if chiesto and r.get("isin") is not None and str(r.get("isin")).strip().upper() != chiesto:
        # RV-UE2 P2-3: un ISIN diverso da quello chiesto (es. dalla cache di un altro emittente) e' un'altra identita'
        return "%s: isin restituito %r diverso da quello chiesto %r" % (nome_mod, r.get("isin"), chiesto)
    con_data = r.get("stato") == "ok" or (r.get("stato") == "STALE" and r.get("data_deposito"))
    if (con_data or r.get("natura_data") is not None) and r.get("natura_data") not in NATURE_DATA:
        return "%s: natura_data %r fuori contratto (%s)" % (nome_mod, r.get("natura_data"), ", ".join(NATURE_DATA))
    if con_data and not r.get("fuso"):
        # AGGIUNTA 4.2 (RV-UE2 R-2): senza fuso il chiamante non costruisce l'istante del deposito
        return "%s: %s senza fuso (AGGIUNTA 4: fuso obbligatorio)" % (nome_mod, r.get("stato"))
    return None


# ============================================================
# NOMI LOCALI DEI DOCUMENTI (AGGIUNTA 2 del contratto, main 05/10)
# ============================================================
def nomi_documento(paese: Any, tipo: Any) -> Tuple[Optional[list], str]:
    """(lista di regex_str | None, motivo). Legge con import pigro NOMI_DOCUMENTO = {tipo: [regex_str, ...]}
    del modulo del `paese` (codice di COPERTURA_UE). None DICHIARATO se: tipo non ammesso, paese non coperto,
    modulo assente, costante assente o malformata (non dict, non lista di stringhe, regex che non compila con
    re.I), nessun nome per il tipo. Ritorna una COPIA: il chiamante non tocca la costante del modulo."""
    if tipo not in _TIPI:
        return None, "tipo %r non ammesso: %s" % (tipo, ", ".join(_TIPI))
    voce = COPERTURA_UE.get(str(paese or "").strip().upper())
    if voce is None:
        return None, "paese %r non censito in COPERTURA_UE" % (paese,)
    if voce["modulo"] is None:
        return None, "paese %s non coperto: %s" % (paese, voce["motivo_limite"])
    nome_mod = PACCHETTO + "." + voce["modulo"]
    mod, assente = _carica(voce["modulo"])
    if mod is None:
        return None, "modulo_assente: %s" % assente
    nomi = getattr(mod, "NOMI_DOCUMENTO", None)
    if not isinstance(nomi, dict):
        return None, "%s senza NOMI_DOCUMENTO (dict) pubblico: AGGIUNTA 2 non rispettata" % nome_mod
    lista = nomi.get(tipo)
    if lista is None or (isinstance(lista, list) and not lista):
        return None, "%s: nessun nome di documento per il tipo %s" % (nome_mod, tipo)
    if not isinstance(lista, list) or not all(isinstance(x, str) and x for x in lista):
        return None, "%s: NOMI_DOCUMENTO[%r] non e' una lista di stringhe" % (nome_mod, tipo)
    for x in lista:
        try:
            re.compile(x, re.I)
        except re.error as e:
            return None, "%s: NOMI_DOCUMENTO[%r] contiene una regex che non compila (%s)" % (
                nome_mod, tipo, type(e).__name__)
    return list(lista), "%s: %d nomi per %s (%s)" % (nome_mod, len(lista), tipo, paese)


# ============================================================
# RIVERIFICA SENZA RETE
# ============================================================
def riverifica_ricevuta(ricevuta: Any, *, ticker: str, tipo: str, periodo_fine: Any,
                        dopo_sdir_italiano: bool = False) -> Tuple[bool, str]:
    """Instrada la riverifica al modulo di ricevuta['fonte_modulo'] (solo moduli in elenco e solo quello
    del paese di instradamento: ricalcolato dall'ISIN con cui il router ha instradato, sigillato in
    ricevuta['isin_instradamento'], altrimenti dal listino del ticker).
    Ritorna (True, motivo) solo se il modulo conferma con True."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dict (%s)" % type(ricevuta).__name__
    fm = ricevuta.get("fonte_modulo")
    if fm not in MODULI_AMMESSI:
        return False, "fonte_modulo %r non in elenco dei moduli UE ammessi: rifiutata" % (fm,)
    if "isin_instradamento" not in ricevuta:
        return False, "ricevuta senza 'isin_instradamento': non prodotta da depositi_ue, rifiutata"
    if ricevuta.get("dopo_sdir_italiano") is not (dopo_sdir_italiano is True):
        return False, ("dopo_sdir_italiano chiesto %r diverso da quello sigillato nella ricevuta %r: rifiutata"
                       % (dopo_sdir_italiano, ricevuta.get("dopo_sdir_italiano")))
    scelta = paese_di_instradamento(ticker, ricevuta["isin_instradamento"], dopo_sdir_italiano is True)
    if scelta["errore"]:
        return False, "ricevuta: %s: rifiutata" % scelta["perche"]
    paese, perche = scelta["paese"], scelta["perche"]
    voce = COPERTURA_UE.get(paese or "")
    atteso = PACCHETTO + "." + voce["modulo"] if voce and voce["modulo"] else None
    if fm != atteso:
        return False, "fonte_modulo %s non e' la fonte del paese di instradamento (%s; atteso %s): rifiutata" % (
            fm, perche, atteso)
    if ricevuta.get("paese") != paese:
        return False, "paese della ricevuta %r diverso da quello di instradamento %r: rifiutata" % (
            ricevuta.get("paese"), paese)
    mod, assente = _carica(fm.rsplit(".", 1)[1])
    funzione = getattr(mod, "riverifica_ricevuta", None) if mod is not None else None
    if not callable(funzione):
        return False, "modulo_assente: %s" % (assente or "%s senza riverifica_ricevuta" % fm)
    if not _accetta_paese(funzione):
        return False, ("modulo_guasto: %s.riverifica_ricevuta non accetta il kwarg 'paese' (obbligatorio, main "
                       "05/10): riverifica non eseguita" % fm)
    try:
        # main 05/10: il paese ricalcolato si passa SEMPRE (stesso mercato della lettura); il controllo della firma
        # sta PRIMA, fuori dal try
        esito = funzione(ricevuta, ticker=str(ticker or "").strip().upper(), tipo=tipo, periodo_fine=periodo_fine,
                         paese=paese)
    except Exception as e:
        return False, "%s: riverifica fallita (%s)" % (fm, type(e).__name__)
    if not (isinstance(esito, tuple) and len(esito) == 2 and isinstance(esito[0], bool)):
        return False, "%s: riverifica fuori contratto (atteso (bool, str))" % fm
    return esito[0] is True, "%s: %s" % (fm, esito[1])
