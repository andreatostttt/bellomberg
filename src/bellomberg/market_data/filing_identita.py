"""Chi e' l'emittente SEC di un titolo del portafoglio.

Deterministico. Fonti, in ordine: ticker senza suffisso, alias verificati a mano
(negozio privato), nome identico sull'elenco SEC. Collegamento automatico («univoco»)
SOLO per un alias verificato o per un ticker senza suffisso il cui nome e' compatibile
con quello SEC; tutto il resto e' una proposta da confermare (revisione G1, 04/10/2026):
- con un suffisso di listino (ZZB.L) il ticker nudo non basta mai, e nemmeno il nome:
  «ACME Holdings plc» non e' «ACME Inc.» (holding e societa' operativa sono emittenti
  diversi; misurato dal vivo: un .L collegato «per nome» a un'altra societa' SEC);
- senza suffisso il solo ticker non basta: una crypto o un ETF del portafoglio puo'
  avere lo stesso simbolo di un trust USA che deposita 10-K;
- il confronto per nome conserva holding/group (come `esef._norm_esatto`).
"""
import re

from bellomberg.market_data.esef import _norm_esatto  # stessa regola del lato ESEF
from bellomberg.valuation.peer_comps import _norm_issuer

# Forme giuridiche (e parole senza identita') che si possono ignorare confrontando due nomi:
# la stop-list di `_norm_issuer` MENO holding/holdings/group, che distinguono gli emittenti.
# Seguito revisione: anche le congiunzioni («&» sparisce, «and» no) e le forme di altri paesi.
_FORME = frozenset({"nv", "n", "v", "spa", "s", "p", "a", "plc", "inc", "sa", "ag", "se", "ltd", "corp",
                    "corporation", "incorporated", "co", "company", "limited", "adr", "the", "class",
                    "aktiengesellschaft", "na", "o", "srl", "as", "bv", "gmbh", "ab", "oyj", "asa", "kgaa",
                    "llc", "lp", "and", "und", "et"})
# Parole dei veicoli d'investimento: un trust/ETF che porta il nome di una moneta o di un indice
# non e' la posizione che ha lo stesso simbolo (crypto «Zentacoin» contro «ZENTACOIN TRUST»).
_VEICOLI = frozenset({"trust", "fund", "funds", "etf", "etfs", "etn", "etp", "shares", "index", "portfolio"})
# Tipi del registro dei veicoli che non sono emittenti operativi: mai un collegamento automatico.
TIPI_NON_OPERATIVI = frozenset({"crypto", "etf", "etn", "cef", "commodity"})


def norm_forma(nome):
    """Nome senza la sola forma giuridica: «ACME Holdings plc» -> «acme holdings».

    Stessa base di `esef._norm_esatto` (accenti, grafie puntate «P.L.C.»), poi via forme e congiunzioni:
    «Zélon» = «Zelon», «Harrow & Finch» = «Harrow and Finch»."""
    return " ".join(t for t in _norm_esatto(nome).split() if t not in _FORME)


def nomi_compatibili(nome, nome_sec):
    """Il nome del portafoglio e' l'inizio, parola per parola, del nome SEC (forma giuridica
    a parte): «Nova» ~ «Nova Semiconductors Inc.», ma «ACME Holdings» non ~ «ACME Inc.»."""
    a, b = norm_forma(nome).split(), norm_forma(nome_sec).split()
    return (bool(a) and bool(b) and a[0] == b[0] and len(a) <= len(b)
            and all(y.startswith(x) for x, y in zip(a, b)))


def _veicolo_omonimo(nome, nome_sec):
    """Parole da veicolo (trust, fund, ETF...) che il nome SEC aggiunge a quello del portafoglio."""
    a, b = norm_forma(nome).split(), norm_forma(nome_sec).split()
    return sorted(set(b[len(a):]) & _VEICOLI)


def _classifica(candidati, *, ticker, nome, tipo=None):
    """Stato della proposta SEC dai candidati (gia' filtrati dai rifiuti). `tipo`: natura della
    posizione dal registro dei veicoli, se nota."""
    t = ticker.upper().strip()
    if len(candidati) > 1:
        return {"stato": "ambiguo", "candidati": candidati, "motivo": f"{len(candidati)} emittenti SEC con lo stesso nome"}
    if not candidati:
        return {"stato": "nessuno", "candidati": [], "motivo": "nessun emittente SEC con nome o alias corrispondente"}
    c = candidati[0]
    if c["origine"] == "alias":
        return {"stato": "univoco", "candidati": candidati, "motivo": "collegato tramite alias verificato"}
    compatibili = c["origine"] == "ticker" and "." not in t and nome and nomi_compatibili(nome, c["nome"])
    veicolo = _veicolo_omonimo(nome, c["nome"]) if compatibili else []
    if compatibili and not veicolo and tipo not in TIPI_NON_OPERATIVI:
        return {"stato": "univoco", "candidati": candidati, "motivo": "collegato tramite ticker e nome"}
    if compatibili and tipo in TIPI_NON_OPERATIVI:
        motivo = (f"ticker e nome coincidono con {c['nome']}, ma la posizione e' di tipo {tipo} (registro dei "
                  "veicoli): un emittente SEC omonimo non e' la posizione, conferma necessaria")
    elif compatibili:
        motivo = (f"ticker e nome coincidono solo a meno di «{' '.join(veicolo)}» ({c['nome']}): veicolo omonimo "
                  "(crypto, ETF o fondo?), conferma necessaria")
    elif "." in t:
        motivo = (f"nome identico a {c['nome']} ma {t} ha un suffisso di listino: un emittente SEC "
                  "omonimo puo' essere un'altra societa', conferma necessaria")
    elif c["origine"] == "ticker" and not nome:
        motivo = (f"solo il ticker coincide con {c['nome']} (nome del titolo non disponibile): "
                  "conferma necessaria (crypto, ETF o fondo omonimo?)")
    elif c["origine"] == "ticker":
        motivo = (f"solo il ticker coincide: nome SEC {c['nome']} diverso da {nome}, conferma necessaria "
                  "(crypto, ETF o fondo omonimo?)")
    else:
        motivo = f"solo il nome coincide con {c['nome']} (ticker SEC {c['ticker']}): conferma necessaria"
    return {"stato": "ambiguo", "candidati": candidati, "motivo": motivo}


def proponi_sec(ticker, nome, *, righe, alias, rifiutati=frozenset(), tipo=None):
    """Candidati SEC per `ticker` (stato univoco / ambiguo / nessuno); `tipo` v. `_classifica`."""
    t = ticker.upper().strip()
    base, suffisso = t.split(".")[0], "." in t
    nome_n = _norm_issuer(nome) if nome else ""  # solo per i nomi simili (sempre da confermare)
    nome_e = _norm_esatto(nome) if nome else ""  # forma giuridica, holding e group compresi
    trovati = {}

    def aggiungi(r, origine):
        if r["cik"] not in rifiutati and r["cik"] not in trovati:
            trovati[r["cik"]] = {"cik": r["cik"], "ticker": r["ticker"], "nome": r["nome"], "origine": origine}

    for r in righe:
        # Con un suffisso di listino il ticker nudo vale solo col nome identico (e resta da confermare).
        if r["ticker"] == base and (not suffisso or (nome_e and _norm_esatto(r["nome"]) == nome_e)):
            aggiungi(r, "ticker")
    if base in alias:
        for r in righe:
            if r["ticker"] == str(alias[base]).upper():
                aggiungi(r, "alias")
    if nome_e:
        for r in righe:
            if _norm_esatto(r["nome"]) == nome_e:
                aggiungi(r, "nome")
    candidati = list(trovati.values())
    if not candidati and nome_n:
        # Nomi abbreviati dei listini («NOVA SEMICOND.»): simili per prefisso di
        # parola, SEMPRE da confermare.
        parole = nome_n.split()

        def simile(r):
            altre = _norm_issuer(r["nome"]).split()
            return (bool(altre) and altre[0] == parole[0] and len(parole) <= len(altre)
                    and all(a.startswith(b) for a, b in zip(altre, parole)))

        simili = {r["cik"]: r for r in righe if r["cik"] not in rifiutati and simile(r)}
        if 0 < len(simili) <= 5:
            return {"stato": "ambiguo", "motivo": "nome simile: conferma necessaria",
                    "candidati": [{"cik": r["cik"], "ticker": r["ticker"], "nome": r["nome"],
                                   "origine": "nome_simile"} for r in simili.values()]}
    return _classifica(candidati, ticker=t, nome=nome, tipo=tipo)


def nome_emittente(ticker):
    """Nome dell'emittente: posizione in portafoglio, poi Yahoo; None se ignoto."""
    try:
        from bellomberg.storage.memory_db import MemoryDB
        for p in MemoryDB().get_portfolio():
            if p.get("ticker") == ticker and (p.get("nome") or "").strip():
                return p["nome"].strip()
    except Exception:
        pass
    try:
        from bellomberg.market_data.news_aggregator import _nome_emittente_yahoo
        return _nome_emittente_yahoo(ticker) or None
    except Exception:
        return None


def preferita(proposta):
    """Fonte da attivare: "sec", "esef" o None (nessuna o da decidere dopo un errore SEC).

    La SEC vince quando collega davvero (univoca, o ambigua tra nomi identici). Una SEC
    ambigua solo per nomi simili (un'omonima estera con altro nome) non e' un
    collegamento: con candidati LEI si propone l'ESEF (univoco: attivato; ambiguo: da confermare).
    """
    sec, esef_p = proposta.get("sec") or {}, proposta.get("esef") or {}
    if sec.get("stato") == "errore":
        return None
    if sec.get("stato") == "univoco":
        return "sec"
    if sec.get("stato") == "ambiguo":
        solo_simili = all(c.get("origine") == "nome_simile" for c in sec.get("candidati") or [])
        return "esef" if solo_simili and esef_p.get("stato") in ("univoco", "ambiguo") else "sec"
    return "esef" if esef_p.get("stato") in ("univoco", "ambiguo") else None


MAX_SONDATI = 3  # candidati SEC di un listino estero di cui si legge il catalogo (rete)
# Frase unica per la SEC non configurata: proposta, attivazione e card di Agents Live (Opus 5.5, 05/10).
SEC_NON_CONFIGURATA = "SEC non configurata: imposta SEC_CONTACT_EMAIL nel .env"
# filings.xbrl.org vuole lo stesso contatto nello User-Agent (esef._headers): stessa causa, detta per ESEF.
ESEF_NON_CONFIGURATO = "ESEF non configurato: imposta SEC_CONTACT_EMAIL nel .env (filings.xbrl.org vuole un contatto)"


def _forme_utili(ticker, cik, catalogo_fn):
    """Forme SEC supportate dal catalogo del CIK; None se il catalogo non si legge."""
    from bellomberg.market_data.filing_profili_auto import MODELLI_SEC, forme_presenti
    try:
        catalogo = catalogo_fn(ticker, cik=cik)
    except Exception:
        return None
    if not isinstance(catalogo, dict) or catalogo.get("stato") not in ("ok", "parziale"):
        return None
    return forme_presenti(catalogo) & set(MODELLI_SEC)


def tipo_registro(ticker):
    """Natura della posizione dal registro dei veicoli (crypto, etf...), None se non dichiarata.

    Solo una cintura in piu': senza registro decide comunque la regola sui nomi di `_classifica`."""
    try:
        from bellomberg.storage.classificazione import voce
        v = voce(ticker)
    except Exception:
        return None
    return v.get("tipo") if v else None


def proponi(ticker, nome=None, *, rifiutati=frozenset(), rifiutati_lei=frozenset(), catalogo_fn=None):
    """Proposta completa per la UI e l'attivazione; mai «nessuna fonte» per un errore.

    `esef` (candidati LEI) solo se la SEC non e' univoca: i titoli USA non interrogano filings.xbrl.org.
    `rifiutati`: CIK (e, per compatibilita', LEI) rifiutati; `rifiutati_lei`: LEI rifiutati.
    """
    from bellomberg.market_data import sec_edgar
    nome = nome or nome_emittente(ticker)
    try:
        elenco = sec_edgar.elenco_emittenti_sec()
    except sec_edgar.ContattoMancante:
        # Installazione senza SEC_CONTACT_EMAIL (Opus 5.5, 05/10): la SEC non e' in errore, non e'
        # CONFIGURATA. Si dichiara e, per i listini esteri, si prova lo stesso l'ESEF; i titoli
        # USA non interrogano mai filings.xbrl.org (stessa regola sotto).
        out = {"ticker": ticker, "nome": nome, "origine_elenco": None, "motivo_elenco": None,
               "sec": {"stato": "non_configurata", "candidati": [], "motivo": SEC_NON_CONFIGURATA}}
        if "." in ticker:
            out["esef"] = proponi_esef(ticker, nome, rifiutati=rifiutati, rifiutati_lei=rifiutati_lei)
        out["preferita"] = preferita(out)
        return out
    except Exception as exc:  # rete e cache assenti
        return {"ticker": ticker, "nome": nome, "origine_elenco": None, "motivo_elenco": None,
                "sec": {"stato": "errore", "candidati": [], "motivo": f"{type(exc).__name__}: {exc}"}}
    tipo = tipo_registro(ticker)
    out = {"ticker": ticker, "nome": nome, "origine_elenco": elenco["origine"], "motivo_elenco": elenco["motivo"],
           "sec": proponi_sec(ticker, nome, righe=elenco["righe"], alias=sec_edgar._alias_sec(), rifiutati=rifiutati,
                              tipo=tipo)}
    sec = out["sec"]
    candidati = sec["candidati"]
    if "." in ticker and sec["stato"] in ("univoco", "ambiguo") and len(candidati) <= MAX_SONDATI:
        # Prova reale 2026-10-03: i listini esteri hanno spesso un emittente SEC omonimo che non
        # deposita bilanci (ADR OTC con soli F-6, esenzione 12g3-2(b)): non e' una fonte, e da
        # quando il nome identico e' solo «da confermare» non deve nemmeno fermare l'ESEF.
        catalogo_fn = catalogo_fn or sec_edgar.get_filing_catalog
        senza = [c for c in candidati if _forme_utili(ticker, c["cik"], catalogo_fn) == set()]
        restanti = [c for c in candidati if c not in senza]
        if senza and not restanti:
            out["sec"] = {"stato": "nessuno", "candidati": [], "scartati": senza,
                          "motivo": f"emittente SEC {senza[0]['nome']} senza 10-K, 10-Q, 20-F o 6-K "
                                    "(es. ADR non quotato): nessun bilancio da confrontare"}
        elif senza and all(c.get("origine") == "nome_simile" for c in restanti):
            out["sec"] = {**sec, "candidati": restanti, "scartati": senza}
        elif senza:
            out["sec"] = {**_classifica(restanti, ticker=ticker, nome=nome, tipo=tipo), "scartati": senza}
    if out["sec"]["stato"] != "univoco":
        out["esef"] = proponi_esef(ticker, nome, rifiutati=rifiutati, rifiutati_lei=rifiutati_lei)
    out["preferita"] = preferita(out)
    return out


def proponi_esef(ticker, nome, *, rifiutati=frozenset(), rifiutati_lei=frozenset()):
    """Candidati LEI (filings.xbrl.org) senza i collegamenti rifiutati dall'utente."""
    rifiutati = frozenset(rifiutati) | frozenset(str(x).upper() for x in rifiutati_lei)
    from bellomberg.market_data import esef
    try:
        esito = esef.candidati_lei(ticker, nome)
    except esef.ContattoMancante:
        return {"stato": "errore", "candidati": [], "motivo": ESEF_NON_CONFIGURATO}
    except Exception as exc:
        return {"stato": "errore", "candidati": [], "motivo": f"{type(exc).__name__}: {exc}"}
    candidati = [c for c in esito["candidati"] if c["lei"] not in rifiutati]
    if len(candidati) == len(esito["candidati"]):
        return esito
    if not candidati:
        return {"stato": "nessuno", "candidati": [], "motivo": "collegamento ESEF rifiutato dall'utente"}
    return {**esito, "candidati": candidati}
