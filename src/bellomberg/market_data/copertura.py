# -*- coding: utf-8 -*-
"""Copertura delle fonti SOLO-USA per un ticker (04/10/2026, Opus 5.5, P1 voce 5).

PERCHE' ESISTE. Quiver (congress / lobbying / contratti governativi), gli insider
(Form 4 SEC, Finnhub) e le opzioni (Polygon/OPRA, IBKR con contratto SMART/USD)
coprono solo emittenti quotati negli USA. Chiesti con un ticker europeo
rispondevano `n: 0` o «no data» — indistinguibile da «nessun dato» — oppure,
PEGGIO, con i dati di un OMONIMO americano quando qualcuno buttava il suffisso
(`BA.L` -> `BA` -> Boeing: v. `sec_edgar.ticker_ambiguo_per_cik`). Qui si
risponde PRIMA della rete a una domanda sola: «questa fonte USA copre questo
ticker?».

REGOLE (dottrina della casa, scritte qui perche' sono la specifica):
  · Suffisso di un listino del registro `mercati.MERCATI` (.MI, .DE, .L, ...)
    -> `non_coperto`, col nome del mercato. Il suffisso NON si toglie mai e non
    si cerca un «gemello» dal simbolo: nessun alias automatico.
  · Crypto (registro `mercati.CRYPTO_24_7`, coppie `XXX-USD`/`-EUR`/`-USDT`/
    `-USDC`) -> `non_coperto`: nessun emittente USA dietro il simbolo.
  · Suffisso fuori registro di 2+ LETTERE (`.MC`, `.CO`, `.ST`, `.OL`, `.AX`:
    listini esteri non censiti in `mercati.py`) -> `non_coperto`, «listino estero
    fuori registro: copertura USA esclusa» (ok PM/main 04/10, review RV-C P3a).
  · Suffisso fuori registro di UNA lettera (`.B` di una classe di azioni USA come
    BRK.B) o non alfabetico -> `indeterminato`.
  · Simbolo con caratteri da indice/futures/cambio (`^GSPC`, `GC=F`, `EURUSD=X`)
    -> `indeterminato`.
  · Senza suffisso -> `coperto` (listino USA PRESUNTO, e il motivo lo dice), ma
    se la `valuta` passata e' diversa da USD -> `indeterminato`: «il ticker
    senza suffisso non identifica un mercato e non implica USD»
    (`classificazione.valuta`).
  · I proxy ADR/ETF passati ESPLICITAMENTE (ticker USA veri, senza suffisso)
    restano `coperto`: la guardia giudica il simbolo chiesto, non lo sostituisce.

`non_coperto` e `indeterminato` sono astensioni DECISE e dichiarate (come il
`not_applicable` di signal_engine), non fonti mute: chi chiama restituisce
`risposta_non_coperta(...)` — un `error` con il motivo — al posto di `n: 0`.

DOVE SI FERMA COSA. I provider (quiver_data, polygon_data, vol_surface) fermano
SOLO `non_coperto`: non conoscono la valuta, e un `indeterminato` da suffisso
fuori registro (es. una classe di azioni USA col punto) va all'URL col simbolo
INTATTO — nessun omonimo possibile — come gia' chiedono i test dei simboli col
punto delle opzioni. Chi conosce la valuta della posizione (i wrapper dei tool)
chiama `copertura_usa(ticker, fonte, valuta)` e decide anche sull'`indeterminato`.

Questa guardia NON sostituisce `sec_edgar.ticker_ambiguo_per_cik`: quella
risponde a «e' sicuro risolvere il CIK?» (e conosce gli alias SEC verificati),
questa a «la fonte copre questo mercato?».

Nessun DB, nessuna rete, nessun negozio: solo stdlib + il registro dei mercati.
"""
import re
from typing import Any, Dict, Optional

from bellomberg.core.presentation import message
from bellomberg.market_data.mercati import CRYPTO_24_7, MERCATI

STATI = ("coperto", "non_coperto", "indeterminato")
MERCATI_USA = "NYSE/Nasdaq"   # come mercati.USA[0]
MERCATO_FUORI_REGISTRO = "listino estero fuori registro"

# Coppie crypto nella forma dei fornitori di prezzi (BTC-USD). `BRK-B` (classe
# di azioni in forma yfinance) NON combacia: il trattino da solo non e' crypto.
_COPPIA_CRYPTO = re.compile(r"^[A-Z0-9]+-(USD|USDT|USDC|EUR)$")
# Simbolo «da azione»: lettere, cifre, punto, trattino. Tutto il resto (^ = / spazi)
# e' indice, futures o cambio: non e' un emittente.
_SIMBOLO_AZIONE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")

# Mercati con opzioni listate fuori dagli USA: solo per il motivo leggibile.
_BORSA_OPZIONI = {".MI": "IDEM"}


def _esito(stato: str, motivo: str, fonte: str, ticker: str,
           mercato: Optional[str] = None) -> Dict[str, Any]:
    return {"stato": stato, "motivo": motivo, "fonte": fonte,
            "ticker": ticker, "mercato": mercato}


def _suffisso(t: str) -> Optional[str]:
    return "." + t.rsplit(".", 1)[1] if "." in t else None


def copertura_usa(ticker: str, fonte: str, valuta: Optional[str] = None) -> Dict[str, Any]:
    """La fonte SOLO-USA `fonte` copre `ticker`? Ritorna SEMPRE un dict, mai eccezioni.

    {"stato": "coperto"|"non_coperto"|"indeterminato", "motivo": str, "fonte": str,
     "ticker": str (maiuscolo, suffisso intatto), "mercato": str|None}
    """
    fonte = str(fonte or "").strip() or "fonte USA"
    t = str(ticker or "").strip().upper()
    if not t:
        return _esito("indeterminato", message(
            "ticker assente: copertura di {f} non determinabile, nessuna chiamata",
            "Missing ticker: {f} coverage cannot be determined, no call made", f=fonte),
            fonte, t)
    if t in CRYPTO_24_7 or _COPPIA_CRYPTO.match(t):
        return _esito("non_coperto", message(
            "{t} e' una crypto (registro mercati.CRYPTO_24_7 o coppia -USD): {f} copre "
            "solo emittenti quotati negli USA; nessuna chiamata",
            "{t} is a crypto asset (mercati.CRYPTO_24_7 registry or -USD pair): {f} covers "
            "only US-listed issuers; no call made", t=t, f=fonte), fonte, t, "crypto 24/7")
    if not _SIMBOLO_AZIONE.match(t):
        return _esito("indeterminato", message(
            "{t} non ha la forma di un'azione (indice, futures o cambio?): copertura "
            "di {f} non determinabile dal simbolo",
            "{t} is not shaped like a share (index, future or FX?): {f} coverage "
            "cannot be determined from the symbol", t=t, f=fonte), fonte, t)
    suff = _suffisso(t)
    if suff is not None:
        dati = MERCATI.get(suff)
        if dati is not None:
            return _esito("non_coperto", message(
                "{t} e' quotato su {m} (suffisso {s}): {f} copre solo emittenti quotati "
                "negli USA. Nessuna chiamata, suffisso NON rimosso (togliendolo si "
                "aggancerebbe un omonimo americano)",
                "{t} is listed on {m} (suffix {s}): {f} covers only US-listed issuers. "
                "No call made, suffix NOT stripped (stripping it would hit a US namesake)",
                t=t, m=dati[0], s=suff, f=fonte), fonte, t, dati[0])
        if len(suff) >= 3 and suff[1:].isalpha():
            return _esito("non_coperto", message(
                "{t}: listino estero fuori registro (suffisso {s}): copertura USA esclusa "
                "per {f}. Nessuna chiamata, suffisso NON rimosso",
                "{t}: foreign listing outside the registry (suffix {s}): US coverage "
                "excluded for {f}. No call made, suffix NOT stripped",
                t=t, s=suff, f=fonte), fonte, t, MERCATO_FUORI_REGISTRO)
        return _esito("indeterminato", message(
            "{t}: suffisso {s} fuori dal registro dei mercati (classe di azioni USA o "
            "listino non censito?): copertura di {f} non determinabile dal simbolo",
            "{t}: suffix {s} not in the market registry (US share class or uncatalogued "
            "listing?): {f} coverage cannot be determined from the symbol",
            t=t, s=suff, f=fonte), fonte, t)
    v = str(valuta).strip().upper() if valuta is not None else ""
    if v and v != "USD":
        return _esito("indeterminato", message(
            "{t} senza suffisso ma con valuta {v}: un ticker senza suffisso non "
            "identifica un listino USA (dottrina classificazione.valuta); copertura "
            "di {f} non determinabile",
            "{t} has no suffix but currency {v}: a ticker without suffix does not "
            "identify a US listing (classificazione.valuta doctrine); {f} coverage "
            "cannot be determined",
            t=t, v=v, f=fonte), fonte, t)
    return _esito("coperto", message(
        "{t} senza suffisso di borsa estera{vv}: listino USA PRESUNTO, {f} interrogabile",
        "{t} has no foreign exchange suffix{vv}: US listing PRESUMED, {f} can be queried",
        t=t, vv=(", valuta USD" if v == "USD" else ""), f=fonte), fonte, t, MERCATI_USA)



def copertura_opzioni(ticker: str, valuta: Optional[str] = None) -> Dict[str, Any]:
    """Copertura delle opzioni USA (Polygon/OPRA e IBKR col contratto SMART/USD).

    Stessa forma di `copertura_usa`, fonte `opzioni_usa`. Ogni suffisso di borsa
    non USA e' `non_coperto` (non solo .MI): Polygon interroga `/v3/snapshot/options/
    <T>` e `underlying_ticker=<T>` sul solo mercato OPRA, e `_get_ibkr_options`
    costruisce `Stock(ticker, "SMART", "USD")` — sbagliato per costruzione su un
    titolo europeo. Il motivo lo dice: nessun tentativo IBKR.
    """
    esito = copertura_usa(ticker, "opzioni_usa", valuta)
    if esito["stato"] == "non_coperto" and esito.get("mercato") != "crypto 24/7":
        suff = _suffisso(esito["ticker"])
        borsa = _BORSA_OPZIONI.get(suff)
        if borsa:
            esito["motivo"] = message(
                "opzioni {b} non coperte: nessun tentativo IBKR. {t} e' quotato su {m}; "
                "Polygon/OPRA copre solo opzioni USA e il contratto IBKR della casa e' "
                "SMART/USD",
                "{b} options not covered: no IBKR attempt. {t} is listed on {m}; "
                "Polygon/OPRA covers US options only and the house IBKR contract is SMART/USD",
                b=borsa, t=esito["ticker"], m=esito["mercato"])
        else:
            esito["motivo"] = message(
                "opzioni di {m} non coperte: nessun tentativo IBKR. {t} ha il suffisso {s}; "
                "Polygon/OPRA copre solo opzioni USA e il contratto IBKR della casa e' "
                "SMART/USD",
                "{m} options not covered: no IBKR attempt. {t} has suffix {s}; "
                "Polygon/OPRA covers US options only and the house IBKR contract is SMART/USD",
                m=esito["mercato"], t=esito["ticker"], s=suff)
    return esito


def risposta_non_coperta(esito: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Payload da restituire AL POSTO di `n: 0` / «no data» quando la fonte non e' stata
    interrogata. E' un `error` (cosi' `_stamp`/`_error` lo propagano), col motivo.

    {"error": "non coperto: <motivo>" | "copertura indeterminata: <motivo>",
     "copertura": "non_coperto"|"indeterminato", "motivo", "fonte", "ticker",
     "mercato", "_source": source}

    Chiamarla con un esito `coperto` e' un errore di programma: dichiarare «non
    coperto» un ticker coperto sarebbe un buco inventato -> ValueError.
    """
    stato = (esito or {}).get("stato")
    if stato not in ("non_coperto", "indeterminato"):
        raise ValueError("risposta_non_coperta: esito %r non e' un'astensione" % (stato,))
    motivo = esito.get("motivo") or ""
    if stato == "non_coperto":
        errore = message("non coperto: {m}", "not covered: {m}", m=motivo)
    else:
        errore = message("copertura indeterminata: {m}", "coverage undetermined: {m}", m=motivo)
    return {"error": errore, "copertura": stato, "motivo": motivo,
            "fonte": esito.get("fonte"), "ticker": esito.get("ticker"),
            "mercato": esito.get("mercato"), "_source": source}


def valuta_dal_book(ticker: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """La valuta di QUOTAZIONE della posizione attiva nel book, per `copertura_usa(..., valuta)`
    (W1, 04/10, Opus 5.5; stessa query dell'endpoint insider di W2: un solo posto).

    {"valuta": str|None, "origine": "posizione"|"fuori_book"|"db_assente"|"illeggibile",
     "nota": str} — mai eccezioni. `nota` va nel payload: fuori dal book (o DB non
    leggibile) la presunzione «listino USA dal simbolo» e' DICHIARATA, non taciuta.

    Lettura: `memory_db.connect_sqlite` (la via sorvegliata dal tripwire dei test sul DB di
    produzione: decisione main 04/10) + `PRAGMA query_only=ON` subito dopo, cosi' la
    connessione non puo' scrivere. Nei test una fixture autouse di conftest sostituisce
    questa funzione con un finto («book non letto»). DB assente = non lo si apre (sqlite lo
    CREEREBBE vuoto). Il percorso e' `memory_db.SQLITE_PATH` letto a ogni chiamata.
    """
    import os
    t = str(ticker or "").strip().upper()
    presunto = message("listino USA presunto dal simbolo", "US listing presumed from the symbol")
    try:
        from bellomberg.storage import memory_db
        path = db_path or memory_db.SQLITE_PATH
        if not os.path.exists(str(path)):
            return {"valuta": None, "origine": "db_assente", "nota": message(
                "valuta della posizione non leggibile (DB del book assente): {p}",
                "Position currency unreadable (book DB missing): {p}", p=presunto)}
        conn = memory_db.connect_sqlite(str(path))
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute("SELECT valuta FROM positions WHERE UPPER(TRIM(ticker))=? "
                               "AND is_active=1", (t,)).fetchone()
        finally:
            conn.close()
    except Exception as e:
        return {"valuta": None, "origine": "illeggibile", "nota": message(
            "valuta della posizione non leggibile ({e}): {p}",
            "Position currency unreadable ({e}): {p}", e=type(e).__name__, p=presunto)}
    v = (str(row[0]).strip().upper() or None) if row and row[0] is not None else None
    if v:
        return {"valuta": v, "origine": "posizione", "nota": message(
            "valuta dalla posizione nel book: {v}", "Currency from the book position: {v}", v=v)}
    return {"valuta": None, "origine": "fuori_book", "nota": message(
        "valuta non nota ({t} non e' una posizione attiva del book o non ha valuta): {p}",
        "Currency unknown ({t} is not an active book position or has no currency): {p}",
        t=t or "<vuoto>", p=presunto)}
