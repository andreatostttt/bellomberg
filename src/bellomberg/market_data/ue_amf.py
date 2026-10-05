# -*- coding: utf-8 -*-
"""ue_amf.py — data di DIFFUSIONE delle relazioni finanziarie (annuale / semestrale / trimestrale)
degli emittenti che depositano all'AMF francese, dall'OAM info-financiere.gouv.fr (handoff-3,
contratto scratchpad INTERFACCIA_UE.md, 05/10/2026, Opus 5.5).

FONTE E PERMESSO D'USO. L'OAM francese (stoccaggio centralizzato dell'informazione regolamentata,
gestito dalla DILA per l'AMF) espone un'API JSON Opendatasoft:
    https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records
Il robots.txt di www.info-financiere.gouv.fr ha `Disallow: /api/` per `User-agent: *` (regola
generica della piattaforma Opendatasoft, misurata il 05/10/2026). La stessa API e' pubblicata
UFFICIALMENTE su data.gouv.fr come «API info-financière», ACCESSO LIBERO, Licence Ouverte /
Open Licence 2.0, limite 10.000 chiamate per IP al giorno:
    https://www.data.gouv.fr/dataservices/api-info-financiere
DECISIONE PM 05/10/2026: si usa l'API (e' un'API ufficiale dichiarata libera, non scraping del
sito), con 1-5 richieste per chiamata, pausa minima per host e cache di 12 ore.

MISURE (ricognizione U1, 05/10/2026, due emittenti del CAC 40):
  - campo della data: `informationdeposee_inf_dat_emt`, «date technique de transmission de
    l'unite d'information au Diffuseur par l'emetteur», ISO con fuso (+00:00), al secondo. E' la
    data di DIFFUSIONE. `uin_dat_mar` (invio al mercato) vale spesso la sentinella 8887/8888
    («non diffusa verso il mercato»): NON si usa.
  - filtro emittente: `identificationsociete_iso_cd_isi` = ISIN della linea di quotazione
    PRINCIPALE (oppure `identificationsociete_iso_cd_lei`). Un ISIN di una linea secondaria non
    trova nulla: non_trovato lo dice.
  - la CATEGORIA la sceglie l'emittente ed e' inaffidabile (un emittente misurato: i conti consolidati annuali
    sotto «Half yearly financial reports»); l'annuale arriva spesso come «Document
    d'enregistrement universel» (DEU) in categoria «Registration document». Alcuni diffusori
    mettono l'etichetta della categoria IN TESTA al titolo («Rapports financiers ... semestriels
    /examens reduits / Rapport financier semestriel»): la si toglie prima di applicare la regola.
  - titoli veri SENZA anno («Rapport financier semestriel»): regola AGGIUNTA 05/10 del contratto.
  - `url_de_recuperation` e' il file stesso (PDF, o ZIP per i DEU/ESEF).

REGOLA DI SCELTA (contratto + AGGIUNTA 05/10, pura in `scegli`):
  1. il NOME del documento nel titolo e' obbligatorio (regex di emarket_sdir.DOCUMENTI_DEPOSITO +
     regex francesi locali);
  2. titolo col periodo/anno del periodo chiesto -> candidato prova='titolo'; titolo con un ALTRO
     anno -> scartato col motivo;
  3. titolo SENZA alcun anno -> candidato prova='finestra' (la data UTC cade dopo periodo_fine ed
     entro FINESTRA_GIORNI[tipo]), limite dichiarato;
  4. il francese comanda, l'inglese e' conferma; piu' candidati nella stessa lingua = ambiguo
     (mai il primo; un 'titolo' non batte un 'finestra' in silenzio). Due righe con stessa ora e
     stesso titolo nella stessa lingua sono UN deposito (l'altra va fra le conferme).
  5. (misura dal vivo 05/10) il comunicato di MESSA A DISPOSIZIONE («Modalites de mise a
     disposition du rapport financier semestriel») e' scartato se il documento stesso e' fra i
     candidati; se manca il documento, vale l'annuncio con limite dichiarato. I documenti del
     contratto di liquidita' / azioni proprie («Half yearly report on ... liquidity contract»)
     sono scartati.
"""
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from bellomberg.core.paths import DATA_DIR
from bellomberg.market_data import borsa_italiana as _bi
from bellomberg.market_data import emarket_sdir as _em

FONTE = "AMF info-financiere (OAM Francia)"
PAESE = "FR"
FONTE_MODULO = "bellomberg.market_data.ue_amf"
NATURA_DATA = "diffusione"
URL_API = ("https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/"
           "flux-amf-new-prod/records")
URL_LICENZA = "https://www.data.gouv.fr/dataservices/api-info-financiere"
# host -> percorsi AMMESSI (prefisso esatto). Nient'altro parte da questo modulo.
AMMESSI = {"www.info-financiere.gouv.fr": ("/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records",)}
USER_AGENT = _bi.USER_AGENT
TIMEOUT_S = 25
PAUSA_S = 1.0                 # pausa minima fra due richieste allo stesso host
LIMITE_PAGINA = 100           # massimo di Opendatasoft per richiesta
MAX_PAGINE = 5                # oltre: KO 'troncato' (una lista a meta' non decide nulla)
TTL_S = 12 * 3600
CACHE_DIR = str(DATA_DIR / "cache_fonti_ue")
VERSIONE_REGOLA = 3           # entra nella chiave di cache
ERRORI_DA_STALE = frozenset({"rete", "http", "formato_cambiato"})   # guasti della fonte, non dell'identita'
FINESTRA_GIORNI = {"annuale": 200, "semestrale": 150, "trimestrale": 120}
CONFERMA_MINUTI = 60          # un'altra lingua e' lo STESSO deposito solo entro questa distanza
FUSO_LOCALE = "Europe/Paris"  # solo per dichiarare il giorno locale quando differisce da quello UTC

_CAMPI = ("uin_idt_uin", "informationdeposee_inf_dat_emt", "informationdeposee_inf_tit_inf",
          "informationdeposee_inf_lng_inf", "sous_type_d_information", "subtype_of_information",
          "url_de_recuperation", "identificationsociete_iso_cd_isi", "identificationsociete_iso_cd_lei",
          "identificationsociete_iso_nom_soc", "informationdeposee_inf_upg_inf_inf_upg_sts")
_LINGUE = {"français": "fr", "francais": "fr", "anglais": "en"}
_ORDINE_LINGUE = ("fr", "en")

LIMITI_BASE = [
    "data = informationdeposee_inf_dat_emt dell'OAM AMF (trasmissione dell'emittente al diffusore), in UTC",
    "legame col documento per NOME + PERIODO nel titolo (prova='titolo') o, per i titoli senza anno, per "
    "la finestra dopo la fine del periodo (prova='finestra'); non per hash del documento",
    "filtro per ISIN della linea principale (o LEI): depositi registrati sotto un altro ISIN non sono visti",
    "non_trovato = non trovato nell'OAM AMF nella finestra cercata, non «mai pubblicato»",
    "righe senza informationdeposee_inf_dat_emt non entrano nel filtro per data (misurato 05/10/2026: "
    "solo depositi 2013-2016)",
    "robots.txt vieta /api/ ai crawler generici: uso autorizzato dalla licenza aperta data.gouv.fr (%s), "
    "decisione PM 05/10/2026" % URL_LICENZA,
]


# ============================================================
# NOMI E PERIODI (regex)
# ============================================================
_FR_NOMI = {
    "annuale": (r"rapport\s+financier\s+annuel|document\s+d['’]\s*enregistrement\s+universel|"
                r"document\s+de\s+r[ée]f[ée]rence|\bDEU\b|universal\s+registration\s+document|\bURD\b|"
                r"comptes\s+(?:annuels|sociaux|consolid[ée]s)(?!\s+(?:semestriels|interm[ée]diaires|r[ée]sum[ée]s))|"
                r"rapport\s+annuel(?!\s+semestr)"),
    "semestrale": (r"rapport\s+(?:financier\s+)?semestriel|comptes\s+(?:consolid[ée]s\s+)?semestriels|"
                   r"(?:first[\s-]+)?half[\s-]*(?:year(?:ly)?\s+)?financial\s+reports?|"
                   r"rapport\s+financier\s+(?:du\s+)?(?:premier|1er)\s+semestre"),
    "trimestrale": (r"information\s+financi[eè]re\s+trimestrielle|rapport\s+(?:financier\s+)?trimestriel|"
                    r"information\s+financi[eè]re\s+du\s+(?:premier|1er|troisi[eè]me|3[eè]me)\s+trimestre|"
                    r"quarterly\s+financial\s+(?:information|report)"),
}
_MESI_FR = ("janvier", "f[ée]vrier", "mars", "avril", "mai", "juin", "juillet", "ao[uû]t",
            "septembre", "octobre", "novembre", "d[ée]cembre")
_ANNO = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# amendamenti/aggiornamenti del DEU: aggiornano il documento, non sono la relazione annuale
_AMENDAMENTO = re.compile(r"amendement|actualisation|amendment|supplement|suppl[ée]ment", re.I)
# comunicato di MESSA A DISPOSIZIONE (art. 221-4 RG AMF): annuncia il documento, non lo e'.
# Misura dal vivo 05/10: «Modalites de mise a disposition du rapport financier semestriel»
# 46 minuti dopo il rapporto stesso, nella stessa categoria.
_ANNUNCIO = re.compile(r"mise\s+[àa]\s+disposition|modalit[ée]s\s+de\s+mise|availability|made\s+available", re.I)
# documenti che portano il nome ma non sono relazioni finanziarie (misura dal vivo 05/10:
# «Half yearly report on ... liquidity contract»)
_ESTRANEI = re.compile(r"contrat\s+de\s+liquidit|liquidity\s+(?:contract|agreement)|actions\s+propres|"
                       r"own\s+shares|rachat\s+d['’]\s*actions|buy[\s-]?back", re.I)
_RETTIFICA = re.compile(r"rectifi|erratum|corrigendum|version\s+corrig|correction|corrected|additif|"
                        r"remplace|replaces?\b", re.I)


# Costante PUBBLICA (contratto UE, AGGIUNTA 2): {tipo: [regex_str]} con tutte le lingue usate dalla
# fonte (FR locale + IT/EN di emarket_sdir), da compilare con re.I. La legge
# depositi_ue.nomi_documento(paese, tipo); e' l'UNICA sorgente dei nomi usata da `scegli`.
NOMI_DOCUMENTO = {t: [_FR_NOMI[t], _em.DOCUMENTI_DEPOSITO[t][0], _em.DOCUMENTI_DEPOSITO[t][1]] for t in _FR_NOMI}


def regex_nome(tipo: str) -> "re.Pattern":
    """Nome del documento: le regex di NOMI_DOCUMENTO[tipo] in alternativa."""
    return re.compile("|".join(NOMI_DOCUMENTO[tipo]), re.I)


def regex_periodo(fine: date, tipo: str) -> "re.Pattern":
    """Il periodo scritto nel titolo: forme di emarket_sdir (IT/EN) + forme francesi + l'anno
    attaccato al NOME del documento (prima o dopo, entro 20 caratteri senza cifre)."""
    g, m, a = fine.day, fine.month, fine.year
    nomi = regex_nome(tipo).pattern
    alt = [_em._regex_periodo(fine, tipo).pattern,
           r"\b0?%d(?:er)?\s+%s\s+%d\b" % (g, _MESI_FR[m - 1], a)]
    if tipo != "trimestrale":
        # l'anno accanto al nome basta per annuale e semestrale (uno per esercizio); NON per la
        # trimestrale, dove l'anno non dice QUALE trimestre
        alt += [r"(?:%s)\D{0,20}(?<!\d)%d(?!\d)" % (nomi, a), r"(?<!\d)%d(?!\d)\D{0,20}(?:%s)" % (a, nomi)]
    if tipo == "annuale":
        alt += [r"exercice\s+(?:clos\s+(?:le\s+)?\D{0,20})?%d\b" % a]
        if m != 12:
            alt += [r"\b%d\s*[/-]\s*(?:%d|%02d)\b" % (a - 1, a, a % 100)]
    elif tipo == "semestrale" and m == 6:
        alt += [r"(?:premier|1er)\s+semestre\s+(?:de\s+l['’]\s*(?:ann[ée]e|exercice)\s+)?%d\b" % a,
                r"\b(?:S1|H1|1S)\s*[-]?\s*%d\b" % a]
    elif tipo == "trimestrale" and m == 3:
        alt += [r"(?:premier|1er)\s+trimestre\s+%d\b" % a, r"\b(?:T1|Q1)\s*%d\b" % a]
    elif tipo == "trimestrale" and m == 9:
        alt += [r"(?:troisi[eè]me|3[eè]?me?)\s+trimestre\s+%d\b" % a, r"neuf\s+(?:premiers\s+)?mois\s+%d\b" % a,
                r"\b(?:T3|Q3|9M)\s*%d\b" % a]
    return re.compile("|".join("(?:%s)" % x for x in alt), re.I)


def titolo_proprio(titolo: str, categorie: List[Optional[str]]) -> str:
    """Toglie l'etichetta della CATEGORIA messa in testa al titolo da alcuni diffusori
    («<sous-type> / <titolo>»): la categoria la sceglie l'emittente e non deve far da nome."""
    t = " ".join((titolo or "").split())
    for c in categorie:
        c = " ".join((c or "").split())
        if c and t.lower().startswith(c.lower() + " / "):
            return t[len(c) + 3:].strip()
    return t


# ============================================================
# RISPOSTA DELL'API -> righe
# ============================================================
def _utc(testo: Any) -> Optional[datetime]:
    """ISO con fuso -> datetime UTC; senza fuso o illeggibile -> None (il fuso non si indovina)."""
    if not isinstance(testo, str):
        return None
    try:
        d = datetime.fromisoformat(testo.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        return None
    return d.astimezone(timezone.utc)


def parse_risposta(corpo: str, *, isin: Optional[str] = None, lei: Optional[str] = None) -> Dict[str, Any]:
    """{"stato": ok|KO, "errore", "motivo", "righe", "totale", "illeggibili"}. Forma attesa
    (misurata): {"total_count": int, "results": [ {campi} ]}. Ogni riga deve avere data con fuso,
    titolo e URL https; una riga di un ALTRO emittente (filtro ignorato dalla fonte) = KO."""
    try:
        d = json.loads(corpo)
    except ValueError:
        return {"stato": "KO", "errore": "formato_cambiato", "motivo": "risposta non JSON", "righe": [],
                "totale": None, "illeggibili": 0}
    if not isinstance(d, dict) or isinstance(d.get("total_count"), bool) or \
            not isinstance(d.get("total_count"), int) or not isinstance(d.get("results"), list):
        return {"stato": "KO", "errore": "formato_cambiato",
                "motivo": "JSON senza total_count intero e results lista: formato dell'API cambiato",
                "righe": [], "totale": None, "illeggibili": 0}
    righe, illeggibili, estranei, incoerenti = [], 0, 0, 0
    for r in d["results"]:
        if not isinstance(r, dict):
            illeggibili += 1
            continue
        if isin and r.get("identificationsociete_iso_cd_isi") != isin:
            estranei += 1
            continue
        if not isin and lei and r.get("identificationsociete_iso_cd_lei") != lei:
            estranei += 1
            continue
        if isin and lei and r.get("identificationsociete_iso_cd_lei") not in (None, lei):
            # ISIN e LEI forniti insieme: una riga dell'ISIN con un ALTRO LEI vuol dire che
            # l'identita' del chiamante non e' coerente con la fonte (review RV-UE1 AMF-4)
            incoerenti += 1
            continue
        quando = _utc(r.get("informationdeposee_inf_dat_emt"))
        titolo = r.get("informationdeposee_inf_tit_inf")
        url = r.get("url_de_recuperation")
        if quando is None or not isinstance(titolo, str) or not titolo.strip() or \
                not isinstance(url, str) or not url.startswith("https://"):
            illeggibili += 1
            continue
        lng = (r.get("informationdeposee_inf_lng_inf") or "").strip()
        cat = r.get("subtype_of_information") or r.get("sous_type_d_information")
        righe.append({"id": r.get("uin_idt_uin"), "quando_utc": quando.isoformat(),
                      "data": quando.date().isoformat(), "ora": quando.strftime("%H:%M"),
                      "titolo": " ".join(titolo.split()),
                      "titolo_proprio": titolo_proprio(titolo, [r.get("sous_type_d_information"),
                                                                r.get("subtype_of_information")]),
                      "url": url, "categoria": cat if isinstance(cat, str) else None,
                      "lingua": _LINGUE.get(lng.lower(), lng.lower() or None),
                      "nome_emittente": r.get("identificationsociete_iso_nom_soc"),
                      "stato_aggiornamento": r.get("informationdeposee_inf_upg_inf_inf_upg_sts")})
    if estranei:
        return {"stato": "KO", "errore": "filtro_ignorato",
                "motivo": "%d righe di un altro emittente nella risposta filtrata per %s: filtro ignorato dalla "
                          "fonte, nessuna riga usata" % (estranei, isin or lei),
                "righe": [], "totale": d["total_count"], "illeggibili": illeggibili, "ricevute": len(d["results"])}
    if incoerenti:
        return {"stato": "KO", "errore": "identita_incoerente",
                "motivo": "%d righe dell'ISIN %s portano un LEI diverso da %s: ISIN e LEI forniti non sono dello "
                          "stesso emittente secondo la fonte, nessuna riga usata" % (incoerenti, isin, lei),
                "righe": [], "totale": d["total_count"], "illeggibili": illeggibili, "ricevute": len(d["results"])}
    if d["results"] and not righe:
        return {"stato": "KO", "errore": "formato_cambiato",
                "motivo": "%d righe, nessuna leggibile (data con fuso, titolo, URL https): campi cambiati"
                          % len(d["results"]), "righe": [], "totale": d["total_count"], "illeggibili": illeggibili,
                "ricevute": len(d["results"])}
    return {"stato": "ok", "errore": None, "motivo": None, "righe": righe, "totale": d["total_count"],
            "illeggibili": illeggibili, "ricevute": len(d["results"])}


# ============================================================
# SCELTA (pura)
# ============================================================
def scegli(righe: List[Dict[str, Any]], tipo: str, fine: date) -> Dict[str, Any]:
    """Righe -> {"stato": ok|ambiguo|non_trovato, "scelto", "candidati", "conferme", "prova",
    "scartati", "motivo"} con la regola del docstring del modulo."""
    nome = regex_nome(tipo)
    per = regex_periodo(fine, tipo)
    limite = (fine + timedelta(days=FINESTRA_GIORNI[tipo])).isoformat()
    cand: List[Dict[str, Any]] = []
    scartati: List[Dict[str, Any]] = []

    def _scarta(r, motivo):
        scartati.append({"titolo": r["titolo"], "data": r["data"], "ora": r["ora"], "url": r["url"],
                         "motivo": motivo})

    for r in sorted(righe, key=lambda r: r["quando_utc"]):
        t = r["titolo_proprio"]
        if not nome.search(t):
            continue
        if r["data"] <= fine.isoformat():
            _scarta(r, "diffuso il %s, non dopo la fine del periodo %s" % (r["data"], fine.isoformat()))
            continue
        if r["data"] > limite:
            _scarta(r, "diffuso il %s, oltre la finestra di %d giorni dalla fine del periodo"
                    % (r["data"], FINESTRA_GIORNI[tipo]))
            continue
        if _ESTRANEI.search(t):
            _scarta(r, "non e' una relazione finanziaria (contratto di liquidita' / azioni proprie)")
            continue
        annuncio = bool(_ANNUNCIO.search(t))
        if tipo == "annuale" and any(regex_nome(x).search(t) for x in ("semestrale", "trimestrale")):
            # vince il nome SPECIFICO: «Semi-annual financial report» contiene «annual financial
            # report» (review RV-UE1 AMF-1)
            _scarta(r, "il titolo nomina una relazione semestrale/trimestrale: non e' l'annuale")
            continue
        if tipo == "annuale" and not annuncio and _AMENDAMENTO.search(t):
            _scarta(r, "amendamento/aggiornamento del documento di registrazione: non e' la relazione annuale")
            continue
        if per.search(t):
            prova = "titolo"
        elif _ANNO.search(t):
            _scarta(r, "il titolo cita un anno/periodo diverso da %s %s" % (tipo, fine.isoformat()))
            continue
        else:
            prova = "finestra"
        cand.append(dict(r, prova=prova, annuncio=annuncio,
                         rettifica=bool(_RETTIFICA.search(t)) or bool(r.get("stato_aggiornamento"))))
    # il DOCUMENTO comanda sul comunicato che lo annuncia; l'annuncio vale solo se il documento
    # non c'e' (e allora la data e' dichiarata come quella dell'annuncio)
    if any(not c["annuncio"] for c in cand):
        for c in [c for c in cand if c["annuncio"]]:
            _scarta(c, "comunicato di messa a disposizione: annuncia il documento, che e' fra i candidati")
        cand = [c for c in cand if not c["annuncio"]]
    gruppo: List[Dict[str, Any]] = []
    for lng in _ORDINE_LINGUE + tuple(sorted({c["lingua"] or "" for c in cand} - set(_ORDINE_LINGUE))):
        gruppo = [c for c in cand if (c["lingua"] or "") == lng]
        if gruppo:
            break
    conferme: List[Dict[str, Any]] = []
    unici: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in gruppo:
        k = (c["quando_utc"], c["titolo_proprio"].lower())
        if k in unici:
            conferme.append(c)
        else:
            unici[k] = c
    scelti = list(unici.values())
    # un candidato in un'ALTRA lingua e' conferma solo se e' lo STESSO deposito (entro
    # CONFERMA_MINUTI da un candidato scelto); altrimenti e' un altro documento e resta
    # candidato -> ambiguo (review RV-UE1 AMF-2: FR «Comptes consolides» 12/02 + EN «URD» 31/03)
    for c in cand:
        if c in gruppo:
            continue
        t = datetime.fromisoformat(c["quando_utc"])
        if any(abs((t - datetime.fromisoformat(s["quando_utc"])).total_seconds()) <= CONFERMA_MINUTI * 60
               for s in scelti) or (c["quando_utc"], c["titolo_proprio"].lower()) in unici:
            conferme.append(c)
        else:
            unici[(c["quando_utc"], c["titolo_proprio"].lower())] = c
            scelti.append(c)
    n_scartati = len(scartati)
    if not scelti:
        return {"stato": "non_trovato", "scelto": None, "candidati": [], "conferme": [], "prova": None,
                "scartati": scartati,
                "motivo": "non trovato nella fonte %s fra il %s e il %s: %d depositi letti, nessuno col nome del "
                          "documento (%s) e il periodo; scartati: %d (vedi scartati)"
                          % (FONTE, fine.isoformat(), limite, len(righe), tipo, n_scartati)}
    if len(scelti) > 1:
        prove = sorted({c["prova"] for c in scelti})
        return {"stato": "ambiguo", "scelto": None, "candidati": scelti, "conferme": conferme, "prova": None,
                "scartati": scartati,
                "motivo": "%d depositi candidati (lingua principale %s; un'altra lingua conta solo se e' lo stesso "
                          "deposito) per lo stesso documento e periodo (prove: %s): nessuno scelto (vedi candidati)"
                          % (len(scelti), scelti[0]["lingua"], ", ".join(prove))}
    return {"stato": "ok", "scelto": scelti[0], "candidati": scelti, "conferme": conferme,
            "prova": scelti[0]["prova"], "scartati": scartati, "motivo": None}


# ============================================================
# RETE E CACHE
# ============================================================
class URLVietato(ValueError):
    """URL fuori dagli host/percorsi ammessi o non https."""


_ultima_richiesta: Dict[str, float] = {}


def url_ammesso(url: str) -> Tuple[bool, str]:
    p = urlsplit(url or "")
    if p.scheme != "https":
        return False, "URL non https"
    percorsi = AMMESSI.get((p.hostname or "").lower())
    if percorsi is None:
        return False, "host %s fuori dalle fonti ammesse" % p.hostname
    if p.path not in percorsi:
        return False, "percorso %s non ammesso su %s" % (p.path, p.hostname)
    return True, ""


def _scarica(url: str) -> Tuple[int, bytes]:
    """GET senza redirect (un redirect torna come HTTP 3xx = KO), User-Agent del modulo, pausa per host."""
    ok, motivo = url_ammesso(url)
    if not ok:
        raise URLVietato(motivo)
    import requests
    host = urlsplit(url).hostname
    attesa = PAUSA_S - (time.monotonic() - _ultima_richiesta.get(host, -1e9))
    if attesa > 0:
        time.sleep(attesa)
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                         timeout=TIMEOUT_S, allow_redirects=False)
    finally:
        _ultima_richiesta[host] = time.monotonic()
    return r.status_code, r.content


def url_query(isin: Optional[str], lei: Optional[str], tipo: str, fine: date, pagina: int) -> str:
    """URL della pagina `pagina` (da 0). Finestra [fine, fine + FINESTRA_GIORNI] in UTC: il
    giorno stesso della fine si legge per poterlo SCARTARE dichiarato."""
    filtro = ('identificationsociete_iso_cd_isi="%s"' % isin) if isin else \
             ('identificationsociete_iso_cd_lei="%s"' % lei)
    fino = fine + timedelta(days=FINESTRA_GIORNI[tipo] + 1)
    q = {"where": '%s and informationdeposee_inf_dat_emt >= "%s" and informationdeposee_inf_dat_emt < "%s"'
                  % (filtro, fine.isoformat(), fino.isoformat()),
         "select": ",".join(_CAMPI), "order_by": "informationdeposee_inf_dat_emt asc",
         "limit": LIMITE_PAGINA, "offset": pagina * LIMITE_PAGINA}
    return URL_API + "?" + urlencode(q)


def _cache_path(chiave: str) -> str:
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", chiave) + ".json")


def _cache_leggi(chiave: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_cache_path(chiave), encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(c, dict) and isinstance(c.get("risultato"), dict) and \
            isinstance(c.get("salvato_ts"), (int, float)) and not isinstance(c.get("salvato_ts"), bool):
        return c
    return None


def _cache_scrivi(chiave: str, risultato: Dict[str, Any]) -> None:
    """Temporaneo + os.replace: un file a meta' non si serve mai."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"salvato_ts": time.time(), "risultato": risultato}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(chiave))
    except OSError:
        pass  # la cache e' un'ottimizzazione: senza, la prossima chiamata rilegge la fonte


# ============================================================
# FUNZIONE PUBBLICA
# ============================================================
def _base(ticker: Any, tipo: Any, periodo_fine: Any, isin: Any, lei: Any, nome: Any) -> Dict[str, Any]:
    return {"ticker": str(ticker or "").strip().upper(), "isin": isin, "lei": lei, "nome": nome, "tipo": tipo,
            "periodo_fine": periodo_fine.isoformat() if isinstance(periodo_fine, date) else periodo_fine,
            "stato": "KO", "errore": None, "motivo": None,
            "data_deposito": None, "ora_deposito": None, "fuso": None, "natura_data": NATURA_DATA,
            "data_deposito_locale": None, "ora_deposito_locale": None, "fuso_locale": None,
            "titolo": None, "url": None, "url_documento": None, "sha256_documento": None,
            "categoria": None, "lingua": None, "candidati": [], "conferme": [], "prova": None, "scartati": [],
            "fonte": FONTE, "paese": PAESE, "url_liste": [], "sha256_liste": {}, "risposte_salvate": {},
            "fonte_modulo": FONTE_MODULO, "pagine_lette": 0, "letto_il": None, "limiti": list(LIMITI_BASE),
            "cache": None}


def lei_valido(lei: Any) -> bool:
    """ISO 17442: 18 alfanumerici + 2 cifre di controllo, ISO 7064 MOD 97-10 (resto 1)."""
    if not isinstance(lei, str) or not re.fullmatch(r"[A-Z0-9]{18}\d{2}", lei):
        return False
    return int("".join(str(int(c, 36)) for c in lei)) % 97 == 1


def _riassunto(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: c.get(k) for k in ("titolo", "data", "ora", "url", "categoria", "lingua", "prova", "annuncio",
                                  "rettifica", "id")}


def _applica_scelta(out: Dict[str, Any], v: Dict[str, Any]) -> None:
    out.update(stato=v["stato"], motivo=v["motivo"], prova=v["prova"], scartati=v["scartati"],
               candidati=[_riassunto(c) for c in v["candidati"]], conferme=[_riassunto(c) for c in v["conferme"]])
    s = v["scelto"]
    if s is not None:
        out.update(data_deposito=s["data"], ora_deposito=s["ora"], fuso="UTC", titolo=s["titolo"], url=s["url"],
                   url_documento=s["url"] if s["url"].lower().endswith(".pdf") else None,
                   categoria=s["categoria"], lingua=s["lingua"])
        if s["prova"] == "finestra":
            out["limiti"].append("titolo senza anno: il periodo e' dedotto dalla data (dopo il %s, entro %d "
                                 "giorni), non letto nel titolo" % (out["periodo_fine"], FINESTRA_GIORNI[out["tipo"]]))
        if s.get("annuncio"):
            out["limiti"].append("documento non trovato: la data e' quella del comunicato di MESSA A DISPOSIZIONE "
                                 "che lo annuncia, non del documento")
        if s.get("rettifica"):
            out["limiti"].append("il deposito scelto e' marcato come rettifica/aggiornamento")
        # mezzanotte (review RV-UE1 AMF-5): la data e' UTC; se a Parigi e' gia' il giorno dopo lo si
        # DICHIARA, perche' chi confronta solo la data col cutoff ammetterebbe un deposito tardivo
        try:
            from zoneinfo import ZoneInfo
            locale = datetime.fromisoformat(s["quando_utc"]).astimezone(ZoneInfo(FUSO_LOCALE))
            out["data_deposito_locale"] = locale.strftime("%Y-%m-%d")
            out["ora_deposito_locale"] = locale.strftime("%H:%M")
            out["fuso_locale"] = FUSO_LOCALE
            if out["data_deposito_locale"] != s["data"]:
                out["limiti"].append("MEZZANOTTE: diffuso il %s %s UTC = %s %s ora di %s, gia' il giorno dopo: "
                                     "un confronto col cutoff deve usare l'ora, non la sola data"
                                     % (s["data"], s["ora"], out["data_deposito_locale"], out["ora_deposito_locale"],
                                        FUSO_LOCALE))
        except Exception as e:
            out["limiti"].append("giorno locale %s non calcolato (%s): la data e' solo UTC" % (FUSO_LOCALE, type(e).__name__))


def _leggi(out: Dict[str, Any], isin: Optional[str], lei: Optional[str], tipo: str, fine: date) -> Dict[str, Any]:
    righe: List[Dict[str, Any]] = []
    ricevute = 0
    for pagina in range(MAX_PAGINE):
        url = url_query(isin, lei, tipo, fine, pagina)
        try:
            http, corpo = _scarica(url)
        except URLVietato as e:
            out.update(errore="url_vietato", motivo="URL rifiutato prima della rete: %s" % e.args[0])
            return out
        except Exception as e:
            out.update(errore="rete", motivo="richiesta all'API AMF fallita (pagina %d): %s" % (pagina + 1, type(e).__name__))
            return out
        if http != 200:
            out.update(errore="http", motivo="HTTP %s dall'API AMF (pagina %d)" % (http, pagina + 1))
            return out
        try:
            testo = corpo.decode("utf-8")
        except UnicodeDecodeError:
            out.update(errore="formato_cambiato", motivo="risposta non UTF-8 (pagina %d)" % (pagina + 1))
            return out
        out["pagine_lette"] += 1
        out["url_liste"].append(url)
        out["sha256_liste"][url] = hashlib.sha256(corpo).hexdigest()
        out["risposte_salvate"][url] = testo
        p = parse_risposta(testo, isin=isin, lei=lei)
        if p["stato"] != "ok":
            out.update(errore=p["errore"], motivo="pagina %d: %s" % (pagina + 1, p["motivo"]))
            return out
        if p["illeggibili"]:
            out["limiti"].append("pagina %d: %d righe illeggibili saltate" % (pagina + 1, p["illeggibili"]))
        righe.extend(p["righe"])
        ricevute += p["ricevute"]
        if (pagina + 1) * LIMITE_PAGINA >= p["totale"]:
            if ricevute != p["totale"]:
                # la fonte dichiara N depositi ma ne ha mandati di meno (o di piu'): lista
                # incompleta, nessun verdetto (review RV-UE1 AMF-3)
                out.update(errore="troncato", motivo="la fonte dichiara %d depositi nella finestra ma le %d pagine "
                                                     "ne contengono %d: lista incompleta, nessun verdetto"
                                                     % (p["totale"], pagina + 1, ricevute))
                return out
            break
    else:
        out.update(errore="troncato", motivo="piu' di %d depositi nella finestra (%d pagine lette): lista "
                                             "incompleta, nessun verdetto" % (MAX_PAGINE * LIMITE_PAGINA, MAX_PAGINE))
        return out
    out["letto_il"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _applica_scelta(out, scegli(righe, tipo, fine))
    return out


def get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str] = None,
                      lei: Optional[str] = None, nome: Optional[str] = None,
                      paese: Optional[str] = None) -> Dict[str, Any]:
    """Data di diffusione (UTC) sull'OAM AMF della relazione `tipo` al `periodo_fine`. Identita' dal
    chiamante: ISIN (preferito) o LEI, mai indovinata dal simbolo. Stati: ok | ambiguo |
    non_trovato | KO | non_coperto | STALE. Cache 12 h per ok/ambiguo; STALE solo per guasti della fonte.
    `paese` (AGGIUNTA 4): il paese su cui instrada il router; l'AMF non dipende dal suffisso, quindi
    il valore si riporta nel ritorno ('paese', default 'FR') e, se diverso da 'FR', lo dice un limite."""
    r = _get_data_deposito(ticker, tipo=tipo, periodo_fine=periodo_fine, isin=isin, lei=lei, nome=nome)
    if paese is not None:
        r["paese"] = paese
        if paese != PAESE:
            r["limiti"] = list(r.get("limiti") or []) + [
                "instradato dal router come paese %s: interrogato l'OAM francese (AMF), che copre gli emittenti "
                "con deposito in Francia" % paese]
    return r


def _get_data_deposito(ticker: str, *, tipo: str, periodo_fine: Any, isin: Optional[str],
                       lei: Optional[str], nome: Optional[str]) -> Dict[str, Any]:
    out = _base(ticker, tipo, periodo_fine, isin, lei, nome)
    if tipo not in FINESTRA_GIORNI:
        out.update(errore="parametro", motivo="tipo %r non ammesso: %s" % (tipo, ", ".join(FINESTRA_GIORNI)))
        return out
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        out.update(errore="parametro", motivo="periodo_fine %r non e' una data AAAA-MM-GG" % (periodo_fine,))
        return out
    out["periodo_fine"] = fine.isoformat()
    incoerente = _em.coerenza_tipo_periodo(tipo, fine)
    if incoerente:
        out.update(errore="parametro", motivo=incoerente)
        return out
    if not isin and not lei:
        out.update(errore="identita_mancante", motivo="l'OAM AMF si interroga per ISIN (linea principale) o LEI: "
                                                      "servono isin= o lei= dall'identita' confermata")
        return out
    if isin and not _bi.isin_valido(isin):
        out.update(errore="identita_non_valida", motivo="ISIN %r non valido (forma o cifra di controllo)" % (isin,))
        return out
    if lei and not lei_valido(lei):
        out.update(errore="identita_non_valida", motivo="LEI %r non valido (forma o cifre di controllo)" % (lei,))
        return out
    if not isin:
        out["limiti"].append("filtro per LEI (ISIN non fornito)")
    # ISIN E LEI nella chiave, e la lettura in cache deve portare la stessa identita': una chiamata
    # con un altro LEI non riceve l'ok di quella precedente (review RV-UE1 AMF-4c)
    chiave = "amf_v%d_%s_%s_%s_%s" % (VERSIONE_REGOLA, isin or "-", lei or "-", tipo, fine.isoformat())
    c = _cache_leggi(chiave)
    if c is not None and (c["risultato"].get("isin") != isin or c["risultato"].get("lei") != lei):
        c = None
    eta = (time.time() - float(c["salvato_ts"])) if c is not None else None
    if c is not None and eta is not None and 0 <= eta < TTL_S:
        r = dict(c["risultato"])
        r.update(ticker=out["ticker"], nome=nome, cache="fresca")
        return r
    nuovo = _leggi(out, isin, lei, tipo, fine)
    if nuovo["stato"] in ("ok", "ambiguo"):
        _cache_scrivi(chiave, {k: v for k, v in nuovo.items() if k != "cache"})
        return nuovo
    # STALE solo per i GUASTI DELLA FONTE (rete, HTTP, formato): un KO d'identita' o di filtro
    # (identita_incoerente, filtro_ignorato) o una lista troncata NON si copre con la lettura vecchia
    if nuovo["stato"] == "KO" and c is not None and nuovo["errore"] in ERRORI_DA_STALE:
        vecchio = dict(c["risultato"])
        vecchio.update(ticker=out["ticker"], nome=nome, stato_originale=vecchio.get("stato"), stato="STALE",
                       errore=nuovo["errore"], cache="scaduta",
                       motivo="fonte AMF in guasto (%s): servita l'ultima lettura buona del %s, eta' %s"
                              % (nuovo["motivo"], vecchio.get("letto_il"),
                                 "%.0f s" % eta if eta is not None and eta >= 0 else "non misurabile"))
        vecchio["limiti"] = list(vecchio.get("limiti") or []) + ["STALE: lettura vecchia servita per guasto della fonte"]
        return vecchio
    return nuovo


# ============================================================
# RIVERIFICA SENZA RETE
# ============================================================
def riverifica_ricevuta(ricevuta: Dict[str, Any], *, ticker: str, tipo: str, periodo_fine: Any,
                        paese: Optional[str] = None) -> Tuple[bool, str]:
    """Ricalcola la scelta dalle `risposte_salvate`, senza rete: gli URL devono essere quelli che
    il modulo costruisce per questa identita'/tipo/periodo, gli sha256 devono combaciare coi corpi,
    e stato/data/ora/titolo/url ricalcolati devono coincidere con quelli della ricevuta."""
    if not isinstance(ricevuta, dict):
        return False, "ricevuta non e' un dict"
    if ricevuta.get("fonte_modulo") != FONTE_MODULO:
        return False, "fonte_modulo %r non e' %s" % (ricevuta.get("fonte_modulo"), FONTE_MODULO)
    # AGGIUNTA 4: il paese su cui instrada il router deve essere quello sigillato nella ricevuta
    if paese is not None and ricevuta.get("paese") != paese:
        return False, "paese %r diverso da quello sigillato nella ricevuta %r" % (paese, ricevuta.get("paese"))
    if str(ticker or "").strip().upper() != ricevuta.get("ticker"):
        return False, "ticker %r diverso da quello della ricevuta %r" % (ticker, ricevuta.get("ticker"))
    try:
        fine = periodo_fine if isinstance(periodo_fine, date) else date.fromisoformat(str(periodo_fine))
    except ValueError:
        return False, "periodo_fine %r non e' una data" % (periodo_fine,)
    if tipo != ricevuta.get("tipo") or fine.isoformat() != ricevuta.get("periodo_fine") or tipo not in FINESTRA_GIORNI:
        return False, "tipo/periodo chiesti (%s %s) diversi da quelli della ricevuta (%s %s)" % (
            tipo, fine.isoformat(), ricevuta.get("tipo"), ricevuta.get("periodo_fine"))
    stato = ricevuta.get("stato_originale") if ricevuta.get("stato") == "STALE" else ricevuta.get("stato")
    if stato not in ("ok", "ambiguo", "non_trovato"):
        return False, "ricevuta in stato %r: nessuna lettura della fonte da riverificare" % (stato,)
    isin, lei = ricevuta.get("isin"), ricevuta.get("lei")
    urls = ricevuta.get("url_liste") or []
    salvate = ricevuta.get("risposte_salvate") or {}
    impronte = ricevuta.get("sha256_liste") or {}
    if not urls:
        return False, "ricevuta senza url_liste"
    righe: List[Dict[str, Any]] = []
    ricevute = 0
    for i, u in enumerate(urls):
        atteso = url_query(isin, None if isin else lei, tipo, fine, i)
        if u != atteso:
            return False, "URL %d della ricevuta non e' la query di questo modulo per %s %s %s" % (
                i + 1, isin or lei, tipo, fine.isoformat())
        corpo = salvate.get(u)
        if not isinstance(corpo, str):
            return False, "risposta salvata mancante per l'URL %d" % (i + 1)
        if hashlib.sha256(corpo.encode("utf-8")).hexdigest() != impronte.get(u):
            return False, "sha256 della risposta %d diverso da quello della ricevuta: contenuto alterato" % (i + 1)
        p = parse_risposta(corpo, isin=isin, lei=lei)
        if p["stato"] != "ok":
            return False, "risposta %d illeggibile alla riverifica: %s" % (i + 1, p["motivo"])
        righe.extend(p["righe"])
        ricevute += p["ricevute"]
        if i == len(urls) - 1 and ((i + 1) * LIMITE_PAGINA < p["totale"] or ricevute != p["totale"]):
            return False, "le pagine salvate (%d righe) non coprono i %d depositi dichiarati dalla fonte" % (
                ricevute, p["totale"])
    v = scegli(righe, tipo, fine)
    s = v["scelto"] or {}
    ricalcolo = {"stato": v["stato"], "data_deposito": s.get("data"), "ora_deposito": s.get("ora"),
                 "titolo": s.get("titolo"), "url": s.get("url")}
    for k, val in ricalcolo.items():
        atteso = stato if k == "stato" else ricevuta.get(k)
        if val != atteso:
            return False, "%s ricalcolato %r diverso dalla ricevuta %r" % (k, val, atteso)
    # AGGIUNTA 2: la LISTA dei candidati ricalcolata deve coincidere (stesso insieme per titolo,
    # data, ora, url): per un ambiguo e' l'unica cosa che la ricevuta afferma
    def _insieme(cc):
        return sorted((c.get("titolo"), c.get("data"), c.get("ora"), c.get("url")) for c in cc or []
                      if isinstance(c, dict))
    if _insieme(v["candidati"]) != _insieme(ricevuta.get("candidati")):
        return False, "candidati ricalcolati (%d) diversi da quelli della ricevuta (%d)" % (
            len(v["candidati"]), len(ricevuta.get("candidati") or []))
    return True, "ricevuta riverificata senza rete: %s, %d risposte con sha256 coerente" % (v["stato"], len(urls))


if __name__ == "__main__":  # python -m bellomberg.market_data.ue_amf TICKER TIPO AAAA-MM-GG ISIN
    import sys
    a = sys.argv[1:]
    print(json.dumps({k: v for k, v in get_data_deposito(a[0], tipo=a[1], periodo_fine=a[2], isin=a[3]).items()
                      if k != "risposte_salvate"}, ensure_ascii=False, indent=2))
