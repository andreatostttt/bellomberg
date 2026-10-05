# -*- coding: utf-8 -*-
"""sdir.py — INSTRADATORE fra i due SDIR italiani: eMarket SDIR (emarket_sdir) e 1INFO-SDIR
(oneinfo_sdir) (handoff-3, 05/10/2026, Opus 5.5).

Un emittente italiano diffonde le informazioni regolamentate tramite UNO SDIR (a volte cambia).
Chi chiama (tool dell'agente, chat, API insider, trade idea) usa SOLO queste due funzioni, con
la stessa forma di ritorno dei moduli sotto, piu' due chiavi SEMPRE presenti:
    "sdir":          "eMarket SDIR" | "1INFO-SDIR" | "eMarket SDIR + 1INFO-SDIR" | None
    "instradamento": {"regola", "scelta", "perche", "emarket": {...}, "oneinfo": {...}}

INTERFACCIA DICHIARATA (chi la cambia avvisa W1 e D4):
    get_data_deposito(ticker, *, tipo, periodo_fine, nome=None) -> dict
    get_internal_dealing(ticker, *, giorni=180, nome=None) -> dict
    instrada(ticker, *, nome=None) -> dict          # la sola scelta, con le misure
  `nome` (facoltativo) = nome dell'emittente per la misura automatica su 1INFO; senza, si usano
  quelli del negozio automatico (riga del listino / nome cercato).

REGOLE DI SCELTA:
  1. Voce del negozio CONFERMATO dal PM (campi `emarket` / `oneinfo`): ha la precedenza.
       emarket int + oneinfo int -> entrambi; emarket int (oneinfo null o assente) -> eMarket;
       emarket null + oneinfo int -> 1INFO; emarket null + oneinfo null -> nessuno (dichiarato);
       emarket null + oneinfo assente -> 1INFO MISURATO (nome univoco + attivita').
  2. Voce AUTOMATICA: si misurano tutti e due. eMarket attivo = l'id del negozio automatico ha
     comunicati negli ultimi GIORNI_ATTIVITA giorni (1 lettura della lista eMarket); 1INFO attivo =
     nome univoco nella lista emittenti 1INFO + comunicati DIFFUSI da 1INFO («SDIR 1INFO», non gli
     «SDIR TERZI» solo stoccati) negli ultimi GIORNI_ATTIVITA giorni.
       attivo su uno solo -> quello; su entrambi -> letti tutti e due; su nessuno -> non_coperto.
  3. Una misura in guasto (KO) o ambigua = KO 'instradamento_*': MAI un ripiego silenzioso da
     una fonte all'altra. Nome non trovato nella lista 1INFO (e eMarket non attivo, o confermato
     con emarket null) = KO 'instradamento_nome_non_trovato': ricerca fallita, NON prova di assenza
     (1INFO non espone l'ISIN: si cerca per nome, e i nomi storici differiscono da Borsa).
  4. Letti tutti e due: deposito con date discordi = `ambiguo` (con le due letture); uno ok e
     l'altro non_trovato/non_coperto = quello ok (l'altro e' scritto); un KO = KO.
     Internal dealing: comunicazioni UNITE, ognuna con il suo `sdir`; la stessa comunicazione sui
     due SDIR (stesso protocollo/PDF, o stessa data + soggetto + operazioni) conta una volta e i
     doppioni tolti si dichiarano; ricevute (url_liste, sha256_liste, richieste_oneinfo), fonte e
     motivo di ENTRAMBE le fonti.
"""
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data import emarket_sdir as _em
from bellomberg.market_data import oneinfo_sdir as _oi

SDIR_EMARKET = "eMarket SDIR"
SDIR_1INFO = "1INFO-SDIR"
SDIR_ENTRAMBI = SDIR_EMARKET + " + " + SDIR_1INFO
GIORNI_ATTIVITA = _oi.GIORNI_ATTIVITA
TTL_ATTIVITA_S = 24 * 3600
URL_ATTIVITA_EMARKET = _em.BASE + "/it/comunicati-finanziari?azienda={id}"
_ASSENTE = object()
MIC_ESTERO = "BGEM"   # Global Equity Market di Borsa Italiana: azioni ESTERE
MOTIVO_ESTERO = ("azione estera quotata in Italia: le informazioni regolamentate non passano dagli SDIR "
                 "italiani")


# ============================================================
# MISURE
# ============================================================
def _leggi_attivita_emarket(id_em: int) -> Dict[str, Any]:
    out = {"stato": "KO", "id": id_em, "ultimo": None, "attivo": None, "errore": None, "motivo": None,
           "letto_il": None}
    url = URL_ATTIVITA_EMARKET.format(id=id_em)
    try:
        http, corpo, _ = _bi._scarica(url)
    except Exception as e:
        out.update(errore="rete", motivo="lista eMarket non letta: %s" % type(e).__name__)
        return out
    if http != 200:
        out.update(errore="http", motivo="lista eMarket: HTTP %s" % http)
        return out
    p = _em.parse_lista(corpo.decode("utf-8", errors="replace"), id_em)
    if p["stato"] == "KO":
        out.update(errore=p["errore"], motivo="lista eMarket: %s" % p["motivo"])
        return out
    ultimo = max((r["data"] for r in p["righe"]), default=None)
    soglia = (_bi.oggi_roma() - timedelta(days=GIORNI_ATTIVITA)).isoformat()
    attivo = bool(ultimo and ultimo >= soglia)
    out.update(stato="ok", ultimo=ultimo, attivo=attivo,
               motivo=("id %d assente dal menu eMarket" % id_em) if p["stato"] == "non_coperto" else
               "ultimo comunicato su eMarket: %s (soglia di attivita' %s)" % (ultimo or "nessuno", soglia),
               letto_il=_bi.adesso_utc().isoformat(timespec="seconds"))
    return out


def attivita_emarket(id_em: int) -> Dict[str, Any]:
    return _bi.con_cache("sdir_attivita_emarket_%d" % id_em, TTL_ATTIVITA_S, lambda: _leggi_attivita_emarket(id_em))


def _misura_oneinfo(nomi: List[Tuple[str, Optional[str]]]) -> Dict[str, Any]:
    """`nomi` = [(origine, nome)] nell'ordine in cui provarli.
    {"esito": attivo|inattivo|non_trovato|ambiguo|KO|non_misurabile, "ndg", "nome_abbinato",
    "per_nome", "motivo", ...}."""
    if not [n for _o, n in nomi if n]:
        return {"esito": "non_misurabile", "ndg": None, "ultimo": None, "nome_abbinato": None, "per_nome": [],
                "motivo": "nessun nome dell'emittente: la presenza su 1INFO non e' misurabile (passa nome=...)"}
    e = _oi.cerca_emittente([n for _o, n in nomi], [o for o, _n in nomi])
    base = {"nome_abbinato": e.get("nome_abbinato"), "per_nome": e.get("per_nome") or []}
    if e["esito"] == "ambiguo":
        return dict(base, **_ambiguo_con_attivita(e))
    if e["esito"] in ("KO", "non_trovato"):
        return dict(base, esito=e["esito"], ndg=None, ultimo=None, motivo=e["motivo"])
    a = _oi.attivita(e["ndg"])
    if a.get("stato") not in ("ok", "STALE"):
        return dict(base, esito="KO", ndg=e["ndg"], ultimo=None,
                    motivo="%s; attivita' non misurata: %s" % (e["motivo"], a.get("motivo")))
    return dict(base, esito="attivo" if a["attivo"] else "inattivo", ndg=e["ndg"], ultimo=a["ultimo"],
                motivo="%s; %s%s" % (e["motivo"], a["motivo"], " (STALE)" if a["stato"] == "STALE" else ""))


MAX_CANDIDATI_ATTIVITA = 4


def _ambiguo_con_attivita(e: Dict[str, Any]) -> Dict[str, Any]:
    """(MF 05/10, caso di un emittente in elenco ma storico) Nome ambiguo: si misura l'attivita' dei
    candidati (al massimo MAX_CANDIDATI_ATTIVITA, una richiesta ciascuno, cache 24 h). Tutti storici =
    esito 'inattivo' (l'emittente non usa 1INFO), mai 'ambiguo'; altrimenti resta ambiguo e il
    motivo conta SOLO i candidati attivi. Un solo candidato attivo NON viene scelto: l'abbinamento
    per nome non lo prova."""
    ndg = sorted({n for p in e.get("per_nome") or [] if p.get("esito") in ("ok", "ambiguo") for n in p.get("ndg") or []})
    if not ndg or len(ndg) > MAX_CANDIDATI_ATTIVITA:
        return {"esito": "ambiguo", "ndg": None, "ultimo": None,
                "motivo": e["motivo"] + ("; attivita' dei %d candidati non misurata (oltre %d)"
                                         % (len(ndg), MAX_CANDIDATI_ATTIVITA) if ndg else "")}
    nomi = {n: nm for p in e.get("per_nome") or [] for n, nm in zip(p.get("ndg") or [], p.get("nomi_1info") or [])}
    attivi, storici = [], []
    for n in ndg:
        a = _oi.attivita(n)
        if a.get("stato") not in ("ok", "STALE"):
            return {"esito": "KO", "ndg": None, "ultimo": None,
                    "motivo": "%s; attivita' del candidato %d non misurata: %s" % (e["motivo"], n, a.get("motivo"))}
        (attivi if a["attivo"] else storici).append((n, a.get("ultimo")))
    descr = lambda xs: ", ".join("%s (ndg %d, ultimo %s)" % (nomi.get(n, "?"), n, u or "nessuno") for n, u in xs)  # noqa: E731
    if not attivi:
        return {"esito": "inattivo", "ndg": None, "ultimo": None,
                "motivo": "emittente STORICO su 1INFO: i candidati per nome sono tutti inattivi da piu' di %d giorni: %s"
                          % (GIORNI_ATTIVITA, descr(storici))}
    return {"esito": "ambiguo", "ndg": None, "ultimo": None,
            "motivo": "%s; candidati ATTIVI su 1INFO: %d (%s)%s" % (
                e["motivo"], len(attivi), descr(attivi), ("; storici esclusi: %s" % descr(storici)) if storici else "")}


def _estero(ticker: str, voce: Dict[str, Any]) -> bool:
    """Riga del Global Equity Market del Listino A-Z: campo strutturato 'mic' scritto da
    risolvi_isin (borsa_italiana.mercato_listino, senza rete; vale anche per un ticker CONFERMATO
    con voce automatica dello stesso ISIN). Voce senza 'mic' = False: si procede come prima."""
    return (_bi.mercato_listino(ticker) or voce.get("mic")) == MIC_ESTERO


def _fermo_estero(out: Dict[str, Any]) -> Dict[str, Any]:
    """Nessuna ricerca per nome, che troverebbe omonimi italiani (misura 05/10 su una banca francese:
    il nome trovava la SGR immobiliare italiana del suo gruppo); stessa regola di risolvi_isin per eMarket."""
    out.update(regola="mercato_listino", stato="ok", scelta="nessuno",
               perche=MOTIVO_ESTERO + " (mercato %s del Listino A-Z): nessuna ricerca su eMarket ne' su 1INFO"
                      % MIC_ESTERO)
    return out


def _nomi_da_provare(ticker: str, nome: Optional[str], voce: Dict[str, Any]) -> List[Tuple[str, Optional[str]]]:
    """Ordine (richiesta main 05/10): il nome dato dal chiamante, poi il nome della riga del
    Listino A-Z di Borsa (borsa_italiana.nome_listino, senza rete: vale anche per un ticker del
    negozio CONFERMATO se c'e' una voce automatica con lo stesso ISIN), poi il nome cercato a suo
    tempo. Ognuno e' provato da solo; vedi oneinfo_sdir.cerca_emittente."""
    return [("nome dato", nome), ("nome listino", _bi.nome_listino(ticker) or voce.get("riga_listino")),
            ("nome cercato", voce.get("nome_cercato"))]


def _misura_emarket(id_em: Optional[int], motivo_assente: Optional[str]) -> Dict[str, Any]:
    if id_em is None:
        return {"esito": "non_trovato", "id": None, "ultimo": None,
                "motivo": motivo_assente or "nessun id eMarket nel negozio automatico"}
    a = attivita_emarket(id_em)
    if a.get("stato") not in ("ok", "STALE"):
        return {"esito": "KO", "id": id_em, "ultimo": None, "motivo": a.get("motivo")}
    return {"esito": "attivo" if a["attivo"] else "inattivo", "id": id_em, "ultimo": a["ultimo"],
            "motivo": "%s%s" % (a["motivo"], " (STALE)" if a["stato"] == "STALE" else "")}


# ============================================================
# SCELTA
# ============================================================
def instrada(ticker: str, *, nome: Optional[str] = None) -> Dict[str, Any]:
    """{"ticker", "isin", "stato": ok|KO, "scelta": emarket|oneinfo|entrambi|nessuno|None,
    "emarket_id", "oneinfo_ndg", "regola": negozio_confermato|misura_automatica|misto,
    "perche", "emarket": {esito,...}, "oneinfo": {esito,...}, "voce_da", "errore", "motivo"}."""
    out: Dict[str, Any] = {"ticker": (ticker or "").strip().upper(), "isin": None, "stato": "KO", "scelta": None,
                           "emarket_id": None, "oneinfo_ndg": None, "regola": None, "perche": None,
                           "emarket": None, "oneinfo": None, "voce_da": None, "errore": None, "motivo": None}
    voce, err, mot = _bi.voce_ticker_o_auto(ticker)
    if voce is None:
        out.update(errore=err, motivo=mot, perche="nessuna voce nel negozio: %s" % mot)
        return out
    out.update(isin=voce["isin"], voce_da=voce["negozio"])
    em, oi = voce.get("emarket"), voce.get("oneinfo", _ASSENTE)
    nomi = _nomi_da_provare(ticker, nome, voce)
    if voce["negozio"] == "confermato":
        out["regola"] = "negozio_confermato"
        dich_em = {"esito": "dichiarato" if em is not None else "dichiarato_assente", "id": em}
        dich_oi = {"esito": "non_dichiarato" if oi is _ASSENTE else
                   ("dichiarato" if oi is not None else "dichiarato_assente"),
                   "ndg": None if oi is _ASSENTE else oi}
        out.update(emarket=dich_em, oneinfo=dich_oi)
        if em is not None and isinstance(oi, int):
            out.update(stato="ok", scelta="entrambi", emarket_id=em, oneinfo_ndg=oi,
                       perche="negozio confermato: emarket %d e oneinfo %d dichiarati" % (em, oi))
        elif em is not None:
            out.update(stato="ok", scelta="emarket", emarket_id=em,
                       perche="negozio confermato: emarket %d dichiarato (oneinfo %s)" % (
                           em, "assente" if oi is _ASSENTE else "null"))
        elif isinstance(oi, int):
            out.update(stato="ok", scelta="oneinfo", oneinfo_ndg=oi,
                       perche="negozio confermato: emarket null, oneinfo %d dichiarato" % oi)
        elif oi is None:
            out.update(stato="ok", scelta="nessuno",
                       perche="negozio confermato: emarket null e oneinfo null (non su nessuno dei due SDIR)")
        else:
            out["regola"] = "misto"
            if _estero(ticker, voce):
                return _fermo_estero(out)
            m = _misura_oneinfo(nomi)
            out["oneinfo"] = m
            if m["esito"] in ("KO", "ambiguo", "non_misurabile", "non_trovato"):
                out.update(errore="instradamento_%s" % ("nome_non_trovato" if m["esito"] == "non_trovato"
                                                        else m["esito"]), motivo=m["motivo"],
                           perche="negozio confermato: emarket null; 1INFO non dichiarato e non misurato: %s" % m["motivo"])
            elif m["esito"] == "attivo":
                out.update(stato="ok", scelta="oneinfo", oneinfo_ndg=m["ndg"],
                           perche="negozio confermato: emarket null; 1INFO misurato: %s" % m["motivo"])
            else:
                out.update(stato="ok", scelta="nessuno",
                           perche="negozio confermato: emarket null; 1INFO misurato %s: %s" % (m["esito"], m["motivo"]))
        return out
    # negozio automatico: si misurano tutti e due
    out["regola"] = "misura_automatica"
    if _estero(ticker, voce):
        return _fermo_estero(out)
    me = _misura_emarket(em, voce.get("emarket_motivo"))
    mo = _misura_oneinfo(nomi)
    out.update(emarket=me, oneinfo=mo)
    for nome_fonte, m in (("emarket", me), ("oneinfo", mo)):
        if m["esito"] in ("KO", "ambiguo", "non_misurabile"):
            out.update(errore="instradamento_%s" % m["esito"],
                       motivo="misura %s: %s" % (nome_fonte, m["motivo"]),
                       perche="misura automatica incompleta (%s %s): nessuna scelta, nessun ripiego"
                              % (nome_fonte, m["esito"]))
            return out
    a_em, a_oi = me["esito"] == "attivo", mo["esito"] == "attivo"
    perche = "eMarket: %s; 1INFO: %s" % (me["motivo"], mo["motivo"])
    if not a_em and mo["esito"] == "non_trovato":
        # nome non trovato nella lista 1INFO = misura FALLITA (nomi storici diversi da Borsa),
        # non la prova che l'emittente non ci sia (review RV-ON P2-3)
        out.update(errore="instradamento_nome_non_trovato", motivo=mo["motivo"],
                   perche="non attivo su eMarket e nome non trovato su 1INFO: presenza su 1INFO non misurata "
                          "(passa il nome usato da 1INFO o dichiara 'oneinfo' nel negozio). " + perche)
        return out
    if a_em and a_oi:
        out.update(stato="ok", scelta="entrambi", emarket_id=me["id"], oneinfo_ndg=mo["ndg"],
                   perche="attivo su entrambi gli SDIR, letti tutti e due. " + perche)
    elif a_em:
        out.update(stato="ok", scelta="emarket", emarket_id=me["id"], perche="attivo solo su eMarket. " + perche)
    elif a_oi:
        out.update(stato="ok", scelta="oneinfo", oneinfo_ndg=mo["ndg"], perche="attivo solo su 1INFO. " + perche)
    else:
        out.update(stato="ok", scelta="nessuno", perche="non attivo ne' su eMarket ne' su 1INFO. " + perche)
    return out


def _errore_nessuno(r: Dict[str, Any]) -> str:
    return "azione_estera" if r.get("regola") == "mercato_listino" else "nessuno_sdir"


def _motivo_nessuno(r: Dict[str, Any]) -> str:
    if r.get("regola") == "mercato_listino":
        return r["perche"]
    return "emittente non su eMarket SDIR ne' su 1INFO-SDIR: %s" % r["perche"]


def _istr(r: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: r.get(k) for k in ("regola", "scelta", "perche", "emarket", "oneinfo", "emarket_id", "oneinfo_ndg",
                                 "errore", "motivo")}
    # quale nome ha abbinato l'emittente su 1INFO (None se 1INFO non e' stato misurato per nome)
    out["nome_abbinato"] = (r.get("oneinfo") or {}).get("nome_abbinato")
    return out


def _sdir_di(scelta: Optional[str]) -> Optional[str]:
    return {"emarket": SDIR_EMARKET, "oneinfo": SDIR_1INFO, "entrambi": SDIR_ENTRAMBI}.get(scelta or "")


# ============================================================
# DEPOSITO
# ============================================================
def _fermo_deposito(ticker: str, tipo: Any, periodo_fine: Any, stato: str, errore: Optional[str],
                    motivo: Optional[str], r: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = _em._base_deposito(ticker, tipo, periodo_fine)
    out.update(stato=stato, errore=errore, motivo=motivo, sdir=None, fonte=None,
               instradamento=_istr(r) if r else {"regola": None, "scelta": None,
                                                  "perche": "parametro non valido: nessun instradamento"})
    if r:
        out.update(isin=r.get("isin"), voce_da=r.get("voce_da"))
    return out


def _unisci_deposito(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """a = eMarket, b = 1INFO, letti tutti e due."""
    letture = {"emarket": {k: a.get(k) for k in ("stato", "errore", "motivo", "data_deposito", "ora_deposito",
                                                   "titolo", "protocollo", "url")},
               "oneinfo": {k: b.get(k) for k in ("stato", "errore", "motivo", "data_deposito", "ora_deposito",
                                                   "titolo", "protocollo", "url")}}
    sa, sb = a.get("stato"), b.get("stato")
    if sb == "KO" and b.get("parametro_non_supportato"):
        sb = "non_supportato"     # parametro che 1INFO non gestisce: non e' un guasto, si legge eMarket
    if "KO" in (sa, sb):
        out = dict(a if sa == "KO" else b)
        out.update(stato="KO", errore="fonte_ko",
                   motivo="letti tutti e due gli SDIR, uno in guasto (eMarket %s, 1INFO %s): nessun verdetto" % (sa, sb))
        sdir = SDIR_ENTRAMBI
    elif sa in ("ok", "STALE") and sb in ("ok", "STALE"):
        if a.get("data_deposito") == b.get("data_deposito"):
            out = dict(a)
            sdir = SDIR_EMARKET
            out["limiti"] = list(out.get("limiti") or []) + [
                "1INFO conferma la stessa data (%s, stoccaggio del documento)" % b.get("data_deposito")]
        else:
            out = dict(a)
            out.update(stato="ambiguo", data_deposito=None, ora_deposito=None, titolo=None, url=None, protocollo=None,
                       candidati=[dict(letture["emarket"], sdir=SDIR_EMARKET), dict(letture["oneinfo"], sdir=SDIR_1INFO)],
                       motivo="date discordi fra gli SDIR: eMarket %s, 1INFO %s: nessuna scelta"
                              % (a.get("data_deposito"), b.get("data_deposito")))
            sdir = SDIR_ENTRAMBI
    elif "ambiguo" in (sa, sb):
        out = dict(a if sa == "ambiguo" else b)
        out["motivo"] = "%s (letto anche l'altro SDIR: %s)" % (out.get("motivo"), sb if sa == "ambiguo" else sa)
        sdir = SDIR_EMARKET if sa == "ambiguo" else SDIR_1INFO
    elif sa in ("ok", "STALE") or sb in ("ok", "STALE"):
        primo = sa in ("ok", "STALE")
        out = dict(a if primo else b)
        altro = b if primo else a
        out["limiti"] = list(out.get("limiti") or []) + [
            "letto anche %s: %s (%s)" % (SDIR_1INFO if primo else SDIR_EMARKET, altro.get("stato"), altro.get("motivo"))]
        sdir = SDIR_EMARKET if primo else SDIR_1INFO
    else:
        out = dict(a if sa == "non_trovato" or sb != "non_trovato" else b)
        out["motivo"] = "eMarket: %s (%s); 1INFO: %s (%s)" % (sa, a.get("motivo"), sb, b.get("motivo"))
        sdir = SDIR_ENTRAMBI
    out["letture"] = letture
    # le DUE ricevute intere (risposte salvate comprese): la riverifica senza rete rifa' ciascuna
    # scelta col suo modulo e poi questa stessa fusione (P1 di RV-D4)
    out["ricevute"] = {"emarket": {k: v for k, v in a.items() if k not in ("cache", "ricevute")},
                       "oneinfo": {k: v for k, v in b.items() if k not in ("cache", "ricevute")}}
    return out, sdir


_CONFRONTO_FUSIONE = ("stato", "data_deposito", "ora_deposito", "titolo", "protocollo", "url")


def riverifica_deposito(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        fine_esercizio: Optional[str] = None) -> Tuple[bool, str]:
    """Riverifica SENZA rete di una ricevuta di sdir.get_data_deposito (INTERFACCIA_UE «AGGIUNTA 5»).
    Instrada sul modulo che ha deciso: `sdir` "eMarket SDIR" (o assente: ricevute vecchie) ->
    emarket_sdir.riverifica_deposito; "1INFO-SDIR" -> oneinfo_sdir.riverifica_deposito; scelta
    'entrambi' -> riverifica le DUE ricevute in `ricevute` e rifa' la fusione, che deve dare lo
    stesso stato/data/ora/titolo/protocollo/url e lo stesso `sdir`. (ok, motivo)."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un oggetto"
    kw = dict(ticker=ticker, tipo=tipo, periodo_fine=periodo_fine, fine_esercizio=fine_esercizio)
    scelta = (ricevuta.get("instradamento") or {}).get("scelta")
    sd = ricevuta.get("sdir", SDIR_EMARKET)
    if scelta == "entrambi":
        ric = ricevuta.get("ricevute")
        if not isinstance(ric, dict) or not isinstance(ric.get("emarket"), dict) or not isinstance(ric.get("oneinfo"), dict):
            return False, "scelta 'entrambi' senza le due ricevute (ricevute.emarket / ricevute.oneinfo)"
        ok_e, mot_e = _riverifica_emarket(ric["emarket"], kw)
        if not ok_e:
            return False, "eMarket: %s" % mot_e
        ok_o, mot_o = _oi.riverifica_deposito(ric["oneinfo"], **kw)
        if not ok_o:
            return False, "1INFO: %s" % mot_o
        rifatto, sdir_r = _unisci_deposito(ric["emarket"], ric["oneinfo"])
        diversi = [c for c in _CONFRONTO_FUSIONE if rifatto.get(c) != ricevuta.get(c)]
        if sdir_r != sd:
            diversi.append("sdir")
        if diversi:
            return False, "la fusione rifatta dalle due ricevute differisce in: %s" % ", ".join(diversi)
        return True, "eMarket: %s; 1INFO: %s; fusione identica" % (mot_e, mot_o)
    if sd == SDIR_1INFO:
        if scelta not in ("oneinfo", None):
            return False, "sdir 1INFO-SDIR con scelta %r incoerente" % (scelta,)
        return _oi.riverifica_deposito(ricevuta, **kw)
    if sd == SDIR_EMARKET:
        if scelta not in ("emarket", None):
            return False, "sdir eMarket SDIR con scelta %r incoerente" % (scelta,)
        return _riverifica_emarket(ricevuta, kw)
    return False, "sdir %r non riverificabile" % (sd,)


def _riverifica_emarket(ricevuta: Dict[str, Any], kw: Dict[str, Any]) -> Tuple[bool, str]:
    f = getattr(_em, "riverifica_deposito", None)
    if f is None:
        return False, "emarket_sdir.riverifica_deposito non disponibile: riverifica eMarket impossibile"
    return f(ricevuta, **kw)


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, nome: Optional[str] = None,
                      fine_esercizio: Optional[str] = None) -> Dict[str, Any]:
    """Data di deposito della relazione `tipo` del periodo `periodo_fine` dallo SDIR giusto.
    Stati: ok | non_trovato | ambiguo | KO | non_coperto | STALE (come emarket_sdir).
    `fine_esercizio` 'MM-GG' (None = esercizio solare: nulla cambia, il kwarg non si inoltra):
    coerenza controllata qui, poi inoltrato a eMarket e a 1INFO. 1INFO supporta l'esercizio non
    solare solo per l'annuale: altrimenti KO 'parametro' dichiarato (`parametro_non_supportato`);
    con tutti e due gli SDIR quel KO non blocca la lettura di eMarket (scritto nei limiti)."""
    fe = {} if fine_esercizio is None else {"fine_esercizio": fine_esercizio}
    if tipo not in _em._TIPI:
        return _fermo_deposito(ticker, tipo, periodo_fine, "KO", "parametro",
                               "tipo %r non ammesso: %s" % (tipo, ", ".join(_em._TIPI)), None)
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return _fermo_deposito(ticker, tipo, periodo_fine, "KO", "parametro",
                               "periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,), None)
    incoerente = _em.coerenza_tipo_periodo(tipo, fine, **fe)
    if incoerente:
        return _fermo_deposito(ticker, tipo, fine, "KO", "parametro", incoerente, None)
    r = instrada(ticker, nome=nome)
    if r["stato"] != "ok":
        return _fermo_deposito(ticker, tipo, fine, "KO", r["errore"], r["motivo"], r)
    if r["scelta"] == "nessuno":
        return _fermo_deposito(ticker, tipo, fine, "non_coperto", _errore_nessuno(r), _motivo_nessuno(r), r)
    if r["scelta"] == "emarket":
        out = _em.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, **fe)
        sdir = SDIR_EMARKET
    elif r["scelta"] == "oneinfo":
        out = _oi.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, ndg=r["oneinfo_ndg"], **fe)
        sdir = SDIR_1INFO
    else:
        out, sdir = _unisci_deposito(_em.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, **fe),
                                     _oi.get_data_deposito(ticker, tipo=tipo, periodo_fine=fine, ndg=r["oneinfo_ndg"], **fe))
    out["sdir"] = sdir
    out["instradamento"] = _istr(r)
    return out


# ============================================================
# INTERNAL DEALING
# ============================================================
def _unisci_id(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    sa, sb = a.get("stato"), b.get("stato")
    buoni = ("ok", "STALE")
    if "KO" in (sa, sb):
        out = dict(a if sa == "KO" else b)
        out.update(stato="KO", errore="fonte_ko", comunicazioni=[],
                   motivo="letti tutti e due gli SDIR, uno in guasto (eMarket %s: %s; 1INFO %s: %s)"
                          % (sa, a.get("motivo"), sb, b.get("motivo")))
        return out, SDIR_ENTRAMBI
    if sa in buoni and sb in buoni:
        out = dict(a)
        viste, unite, doppie = set(), [], 0
        for c in list(a.get("comunicazioni") or []) + list(b.get("comunicazioni") or []):
            chiavi = {x for x in (_impronta(c), ("pdf", c.get("url_pdf")) if c.get("url_pdf") else None,
                                  ("prot", c.get("protocollo")) if c.get("protocollo") else None) if x is not None}
            if chiavi & viste:
                doppie += 1
                continue
            viste |= chiavi
            unite.append(c)
        out["comunicazioni"] = sorted(unite, key=lambda c: (c.get("data") or "", c.get("ora") or ""), reverse=True)
        out["fonte"] = "%s + %s" % (a.get("fonte"), b.get("fonte"))
        out["motivo"] = "; ".join(x for x in (a.get("motivo"), b.get("motivo")) if x) or None
        out["url_liste"] = list(a.get("url_liste") or ([a["url"]] if a.get("url") else [])) + list(b.get("url_liste") or [])
        out["sha256_liste"] = dict(a.get("sha256_liste") or {}, **(b.get("sha256_liste") or {}))
        out["richieste_oneinfo"] = b.get("richieste")
        for k in ("pdf_letti", "pdf_non_letti", "pdf_falliti", "parse_falliti", "pagine_lette"):
            out[k] = (a.get(k) or 0) + (b.get(k) or 0)
        out["troncato"] = bool(a.get("troncato") or b.get("troncato"))
        out["limiti"] = list(a.get("limiti") or []) + [x for x in (b.get("limiti") or []) if x not in (a.get("limiti") or [])]
        out["oneinfo_ndg"] = b.get("oneinfo_ndg")
        if doppie:
            out["limiti"].append("%d comunicazioni presenti su tutti e due gli SDIR (stessa data, soggetto e "
                                 "operazioni): contate una volta" % doppie)
        if "STALE" in (sa, sb):
            out["stato"] = "STALE"
        return out, SDIR_ENTRAMBI
    if sa in buoni or sb in buoni:
        primo = sa in buoni
        out = dict(a if primo else b)
        altro = b if primo else a
        out["limiti"] = list(out.get("limiti") or []) + [
            "letto anche %s: %s (%s)" % (SDIR_1INFO if primo else SDIR_EMARKET, altro.get("stato"), altro.get("motivo"))]
        return out, (SDIR_EMARKET if primo else SDIR_1INFO)
    if "vuoto_misurato" in (sa, sb):
        out = dict(a if sa == "vuoto_misurato" else b)
        out["stato"] = "vuoto_misurato"
        out["motivo"] = "eMarket: %s (%s); 1INFO: %s (%s)" % (sa, a.get("motivo"), sb, b.get("motivo"))
        return out, SDIR_ENTRAMBI
    out = dict(a)
    out["motivo"] = "eMarket: %s (%s); 1INFO: %s (%s)" % (sa, a.get("motivo"), sb, b.get("motivo"))
    return out, SDIR_ENTRAMBI


def _impronta(c: Dict[str, Any]) -> Optional[Tuple]:
    """Stessa comunicazione su due SDIR: data + soggetto + operazioni (ISIN, quantita', data).
    Senza soggetto o senza operazioni lette non si deduplica (None)."""
    ops = tuple(sorted((o.get("isin") or "", o.get("quantita") or -1, o.get("data_operazione") or "")
                       for o in c.get("operazioni") or []))
    if not c.get("soggetto") or not ops:
        return None
    return (c.get("data"), " ".join(str(c["soggetto"]).upper().split()), ops)


def _segna(out: Dict[str, Any], sdir: str) -> None:
    out["comunicazioni"] = [dict(c, sdir=c.get("sdir") or sdir) for c in out.get("comunicazioni") or []]


def get_internal_dealing(ticker: str, *, giorni: int = 180, nome: Optional[str] = None) -> Dict[str, Any]:
    """Internal dealing negli ultimi `giorni` dallo SDIR giusto (contratto di emarket_sdir);
    ogni comunicazione porta il suo `sdir`."""
    if isinstance(giorni, bool) or not isinstance(giorni, int) or not 1 <= giorni <= _em.GIORNI_MAX:
        out = _em._base(ticker, giorni)
        out.update(errore="parametro", motivo="giorni dev'essere un intero fra 1 e %d, non %r" % (_em.GIORNI_MAX, giorni),
                   cache={"stato": "nessuna", "eta_s": None}, sdir=None, fonte=None, instradamento={"regola": None, "scelta": None,
                                                         "perche": "parametro non valido: nessun instradamento"})
        return out
    r = instrada(ticker, nome=nome)
    if r["stato"] != "ok" or r["scelta"] == "nessuno":
        out = _em._base(ticker, giorni)
        out.update(isin=r.get("isin"), voce_da=r.get("voce_da"), sdir=None, fonte=None,
                   cache={"stato": "nessuna", "eta_s": None})
        if r["stato"] != "ok":
            out.update(stato="KO", errore=r["errore"], motivo=r["motivo"])
        else:
            out.update(stato="non_coperto", errore=_errore_nessuno(r), motivo=_motivo_nessuno(r))
        out["instradamento"] = _istr(r)
        return out
    if r["scelta"] == "emarket":
        out, sdir = _em.get_internal_dealing(ticker, giorni=giorni), SDIR_EMARKET
        _segna(out, SDIR_EMARKET)
    elif r["scelta"] == "oneinfo":
        out, sdir = _oi.get_internal_dealing(ticker, giorni=giorni, ndg=r["oneinfo_ndg"]), SDIR_1INFO
        _segna(out, SDIR_1INFO)
    else:
        a = _em.get_internal_dealing(ticker, giorni=giorni)
        b = _oi.get_internal_dealing(ticker, giorni=giorni, ndg=r["oneinfo_ndg"])
        _segna(a, SDIR_EMARKET)
        _segna(b, SDIR_1INFO)
        out, sdir = _unisci_id(a, b)
    out["sdir"] = sdir
    out["instradamento"] = _istr(r)
    return out
