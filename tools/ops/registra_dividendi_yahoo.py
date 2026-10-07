"""BELLOMBERG — registra in trade_history i dividendi che Yahoo vede e il DB no (PNL-B, 06/10/2026, Opus 5.5).

Perche': la storia del NAV usa chiusure NON aggiustate e somma SOLO i dividendi registrati
(DIVIDEND). Uno stacco durante il possesso senza registrazione resta fuori dal rendimento:
compute_nav_history lo dichiara in `dividendi_non_registrati`. Decisione PM (06/10): registrarli.

Cosa fa, per ogni stacco Yahoo durante il possesso NON abbinato a una registrazione (stessa
regola della storia NAV: portfolio_analytics._abbina_dividendi, finestra -45/+7 giorni,
possesso alla vigilia dell'ex-date):
  - importo per azione = quello di Yahoo; quantita' = azioni possedute alla VIGILIA dello stacco;
  - conversione in EUR col cambio di riferimento BCE della DATA DI STACCO (Yahoo non da' la data
    di pagamento: dichiarato). GBX = pence: per azione / 100 in GBP. Nessun cambio BCE esatto per
    quel giorno = riga NON registrata (dichiarata), mai un cambio di un altro giorno;
  - stesso formato delle registrazioni DIVIDEND esistenti: quantita' = azioni, prezzo = EUR per
    azione, valuta = EUR, data = data di stacco (ora 12:00 per convenzione), via MemoryDB.log_trade
    (il percorso delle importazioni storiche: NON tocca la cassa, come importa_trade_csv.py);
  - nota «da Yahoo, non da estratto broker» con importo originale, cambio e data del cambio.

Uso:  python tools/ops/registra_dividendi_yahoo.py            (DRY-RUN: mostra, DB in sola lettura)
      python tools/ops/registra_dividendi_yahoo.py --importi estratto.csv   (importi NETTI dell'estratto,
            colonne ticker,data,importo_netto_eur,azioni; data = data di INCASSO; dry-run se senza --apply)
Sicurezza: una proposta Yahoo con un DIVIDEND gia' registrato per lo stesso ticker entro 120 giorni
dallo stacco si rifiuta (possibile duplicato, dichiarato); con --importi entro 7 giorni.
Gli importi Yahoo sono LORDI (il broker accredita il NETTO dopo la ritenuta) e, per emittenti che
pagano in una valuta diversa dalla quotazione, convertiti da Yahoo al cambio di oggi.
      python tools/ops/registra_dividendi_yahoo.py --apply    (scrive, dopo un backup del DB)
Con --apply: rifiuto se il backend e' attivo sulla porta 8765; backup con l'API SQLite +
quick_check; conteggio righe prima/dopo: se non torna esce in ERRORE (exit 1) e dice il backup.

Cosa NON fa, dichiarato: la CASSA (cash_state) non cambia. Se il broker ha gia' accreditato quei
dividendi la cassa li contiene gia'; se no, la riconciliazione del TWR li mostrera' come cassa
attesa ma non osservata (residuo). Importi da Yahoo, NON dall'estratto broker: da verificare.
"""
import argparse
import csv
import io
import os
import socket
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta

_RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_RADICE, "src"))
sys.path.insert(0, _RADICE)
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", _RADICE)

NOTA_FONTE = "da Yahoo, non da estratto broker"
NOTA_NETTO = "importo NETTO da estratto broker"
AVVERTENZA_LORDO = ("importo LORDO da Yahoo: il broker accredita il NETTO dopo la ritenuta; Yahoo converte "
                    "al cambio di oggi per emittenti che pagano in valuta diversa dalla quotazione")
# fix PNL-B giro 4: un dividendo gia' registrato per lo stesso ticker entro questa distanza
# dallo stacco puo' essere lo STESSO dividendo (registrato a mano, data di incasso diversa):
# la proposta si rifiuta come possibile duplicato, dichiarata, mai scritta.
DUPLICATO_GG = 120
# con --importi (estratto) due righe dello stesso ticker entro 7 giorni = duplicato
DUPLICATO_IMPORTI_GG = 7


def _porta_8765_occupata():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 8765)) == 0


def _backup(db_path):
    bk = db_path + ".pre_dividendi_yahoo_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    src, dst = sqlite3.connect(db_path, timeout=15), sqlite3.connect(bk)
    try:
        src.backup(dst)
        qc = dst.execute("PRAGMA quick_check").fetchone()
    finally:
        dst.close()
        src.close()
    if not qc or str(qc[0]).lower() != "ok":
        raise SystemExit(f"[STOP] backup non sano ({qc}): nessuna scrittura")
    return bk


def _conta(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM trade_history").fetchone()[0]
    finally:
        con.close()


def _leggi_trade(db_path):
    """Lettura in SOLA LETTURA (mode=ro): il dry-run non apre MemoryDB (che crea tabelle)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT ticker, action, quantita, prezzo, valuta, data "
                           "FROM trade_history ORDER BY data ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def cambio_bce(valuta, giorno, scarica=None):
    """Unita' di `valuta` per 1 EUR al fixing BCE di `giorno` esatto; None se BCE non ha
    l'osservazione di quel giorno (festivo TARGET) o la rete fallisce (dichiarato dal chiamante)."""
    from bellomberg.valuation.fx_evidence import reference_url
    url = reference_url("EUR", valuta, giorno)
    try:
        testo = (scarica or _scarica_testo)(url)
    except Exception as e:
        return None, f"BCE non raggiungibile ({type(e).__name__})"
    righe = [r for r in csv.DictReader(io.StringIO(testo)) if r.get("TIME_PERIOD") == giorno]
    if len(righe) != 1:
        return None, f"nessuna osservazione BCE il {giorno}"
    try:
        v = float(righe[0]["OBS_VALUE"])
    except (KeyError, TypeError, ValueError):
        return None, "valore BCE non numerico"
    return (v, None) if v > 0 else (None, "valore BCE non positivo")


def _scarica_testo(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read().decode("utf-8")


def stacchi_non_registrati(trades, scarica_prezzi=None):
    """[(ticker, ex_date, per_azione, valuta, quantita)] con la STESSA regola della storia NAV.
    Ritorna (lista, motivo) con lista None se la verifica non e' eseguibile."""
    import bellomberg.portfolio.portfolio_analytics as pa
    non_div = [t for t in trades if (t.get("action") or "").upper() != "DIVIDEND"]
    timeline = pa._build_position_timeline(non_div)
    tickers = sorted(timeline.keys())
    labels = pa._currency_labels_for_tickers(tickers, trades)
    ccy_of = {k: v.valore for k, v in labels.items() if v.valore is not None}
    salta = pa.prezzi_speciali()["prezzi"]["senza_yfinance"]
    inizio = min((t.get("data") or "")[:10] for t in trades)
    fine = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    prezzi = (scarica_prezzi or pa._download_prices_for_history)(tickers, inizio, fine, salta, auto_adjust=False)
    div = getattr(prezzi, "attrs", {}).get("dividendi") if prezzi is not None else None
    if div is None:
        return None, "dividendi Yahoo assenti dal download: verifica NON eseguita"
    esito = pa._abbina_dividendi(trades, div, timeline, ccy_of)
    return esito["non_registrati"], None


def _registrati_vicini(trades, ticker, giorno, distanza):
    d = datetime.strptime(giorno, "%Y-%m-%d")
    return [t["data"][:10] for t in trades
            if (t.get("action") or "").upper() == "DIVIDEND" and t.get("ticker") == ticker
            and abs((datetime.strptime(t["data"][:10], "%Y-%m-%d") - d).days) <= distanza]


def prepara(trades, scarica_bce=None, scarica_prezzi=None):
    """Righe da scrivere + righe rifiutate (con motivo). Nessuna scrittura."""
    lista, motivo = stacchi_non_registrati(trades, scarica_prezzi)
    if lista is None:
        return None, [], motivo
    pronte, rifiutate = [], []
    for x in lista:
        tk, ex, ps, ccy, q = x["ticker"], x["ex_date"], float(x["per_azione"]), x["valuta"], float(x["quantita"])
        vicini = _registrati_vicini(trades, tk, ex, DUPLICATO_GG)
        if vicini:
            rifiutate.append({**x, "data": ex, "motivo": f"possibile duplicato: DIVIDEND gia' registrato il "
                              f"{', '.join(vicini)} (entro {DUPLICATO_GG} giorni dallo stacco)"})
            continue
        if ccy == "EUR":
            eur_per_azione, cambio, nota_fx, fonte = ps, 1.0, "EUR", "identity"
        elif ccy in ("GBX", "USD", "GBP", "CHF", "JPY", "DKK", "SEK", "NOK", "HKD", "CAD"):
            base = "GBP" if ccy == "GBX" else ccy
            per_azione_base = ps / 100.0 if ccy == "GBX" else ps
            cambio, perche = cambio_bce(base, ex, scarica_bce)
            if cambio is None:
                rifiutate.append({**x, "data": ex, "motivo": perche})
                continue
            eur_per_azione = per_azione_base / cambio
            nota_fx = f"cambio BCE del {ex} (data di stacco): {cambio} {base}/EUR"
            fonte = "storico"
        else:
            rifiutate.append({**x, "data": ex, "motivo": f"valuta {ccy} non gestita"})
            continue
        eur = eur_per_azione * q
        pronte.append({
            "ticker": tk, "action": "DIVIDEND", "quantita": q, "prezzo": round(eur_per_azione, 6),
            "valuta": "EUR", "data": ex, "fx_fonte": fonte, "importo_eur": round(eur, 2),
            "note": (f"Dividend {tk} {NOTA_FONTE}: {ps} {ccy}/azione x {q:g} azioni (possedute alla "
                     f"vigilia dello stacco {ex}); {nota_fx}; = {eur:.2f} EUR LORDI. Data = data di stacco "
                     f"(pagamento non noto da Yahoo)."),
        })
    return pronte, rifiutate, None


def leggi_importi(path, trades):
    """--importi: CSV `ticker,data,importo_netto_eur,azioni` (importi NETTI dall'estratto, data =
    data di INCASSO). Righe da scrivere + rifiutate. Nessuna scrittura, nessuna rete."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from importa_trade_csv import _numero
    pronte, rifiutate, viste = [], [], []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            tk = (r.get("ticker") or "").strip().upper()
            giorno = (r.get("data") or "").strip()[:10]
            try:
                datetime.strptime(giorno, "%Y-%m-%d")
                netto = _numero(r.get("importo_netto_eur"))
                azioni = _numero(r.get("azioni"))
            except (ValueError, TypeError) as e:
                rifiutate.append({"ticker": tk or "?", "data": giorno or "?", "motivo": f"riga {n}: {e}"})
                continue
            if not tk or netto <= 0 or azioni <= 0:
                rifiutate.append({"ticker": tk or "?", "data": giorno, "motivo": f"riga {n}: ticker vuoto o importo/azioni non positivi"})
                continue
            vicini = _registrati_vicini(trades, tk, giorno, DUPLICATO_IMPORTI_GG) + [
                g for t, g in viste if t == tk and abs((datetime.strptime(g, "%Y-%m-%d")
                                                        - datetime.strptime(giorno, "%Y-%m-%d")).days) <= DUPLICATO_IMPORTI_GG]
            if vicini:
                rifiutate.append({"ticker": tk, "data": giorno, "motivo": f"riga {n}: possibile duplicato "
                                  f"({', '.join(vicini)}, entro {DUPLICATO_IMPORTI_GG} giorni)"})
                continue
            viste.append((tk, giorno))
            pronte.append({"ticker": tk, "action": "DIVIDEND", "quantita": azioni,
                           "prezzo": round(netto / azioni, 6), "valuta": "EUR", "data": giorno,
                           "fx_fonte": "identity", "importo_eur": round(netto, 2),
                           "note": f"Dividend {tk}: {NOTA_NETTO}, {netto:.2f} EUR netti su {azioni:g} azioni, "
                                   f"data di incasso {giorno}."})
    return pronte, rifiutate


def scrivi(db_path, righe):
    """Scrive via MemoryDB.log_trade; ritorna (prima, dopo, scritte, errore)."""
    from bellomberg.storage.memory_db import MemoryDB
    prima = _conta(db_path)
    db = MemoryDB(db_path=db_path)
    scritte, errore = 0, None
    for r in righe:
        try:
            db.log_trade(ticker=r["ticker"], action="DIVIDEND", quantita=r["quantita"], prezzo=r["prezzo"],
                         valuta="EUR", data=r["data"], note=r["note"], fx_fonte=r["fx_fonte"])
            scritte += 1
        except ValueError as e:
            errore = f"{r['ticker']} {r['data']}: rifiutata dal DB: {e}"
            break
    return prima, _conta(db_path), scritte, errore


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="scrive davvero (default: dry-run)")
    ap.add_argument("--db", default=None, help="path del DB (default: memory_db.SQLITE_PATH)")
    ap.add_argument("--importi", default=None,
                    help="CSV ticker,data,importo_netto_eur,azioni: importi NETTI dell'estratto alla data di incasso")
    a = ap.parse_args(argv)
    from bellomberg.storage.memory_db import SQLITE_PATH
    db_path = a.db or SQLITE_PATH
    if not os.path.exists(db_path):
        print(f"[STOP] DB non trovato: {db_path}")
        return 2
    if a.apply and _porta_8765_occupata():
        print("[STOP] backend attivo sulla porta 8765: chiudi l'app prima di scrivere sul DB")
        return 2
    if a.importi:
        righe, rifiutate = leggi_importi(a.importi, _leggi_trade(db_path))
        avvertenza = f"importi {NOTA_NETTO} (dal file {a.importi}), alla data di incasso"
    else:
        righe, rifiutate, motivo = prepara(_leggi_trade(db_path))
        if righe is None:
            print(f"[STOP] {motivo}")
            return 1
        avvertenza = f"{AVVERTENZA_LORDO}; importi {NOTA_FONTE}"
    print(f"{'ticker':<10} {'stacco':<10} {'azioni':>8} {'EUR/azione':>11} {'EUR':>9}  fonte cambio")
    for r in righe:
        print(f"{r['ticker']:<10} {r['data']:<10} {r['quantita']:>8g} {r['prezzo']:>11.6f} {r['importo_eur']:>9.2f}  {r['fx_fonte']}")
    for x in rifiutate:
        print(f"  [NON REGISTRABILE] {x['ticker']} {x['data']}: {x['motivo']}")
    print(f"\ntotale {sum(r['importo_eur'] for r in righe):.2f} EUR su {len(righe)} righe; {len(rifiutate)} non registrabili")
    print(f"[NOTA] {avvertenza}; la cassa (cash_state) NON cambia")
    if not a.apply:
        print(f"[DRY-RUN] nessuna scrittura; trade_history = {_conta(db_path)} righe")
        return 0
    if _porta_8765_occupata():
        print("[STOP] backend attivo sulla porta 8765: chiudi l'app prima di scrivere sul DB")
        return 2
    bk = _backup(db_path)
    print(f"[BACKUP] {bk}")
    prima, dopo, scritte, errore = scrivi(db_path, righe)
    print(f"[APPLY] scritte {scritte}/{len(righe)} - trade_history prima {prima} -> dopo {dopo}")
    if errore or dopo - prima != len(righe) or scritte != len(righe):
        print(f"[ERRORE] conteggio non torna o riga rifiutata ({errore}); via di ritorno = il backup {bk}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
