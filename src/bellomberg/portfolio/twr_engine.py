"""
BELLOMBERG - TWR Engine (motore contabile da fondo, audit 02 par.3 / fix #30)

Problema risolto (denuncia del PM): "se levo o aggiungo soldi non puo' essere
considerato drawdown o profit". Questo modulo separa FLUSSI ESTERNI di capitale
dal RENDIMENTO, come farebbe l'amministratore di un fondo.

Componenti:
  record_nav_snapshot()  - persiste il NAV ufficiale di OGGI in nav_snapshots
                           (stessa fonte del Dashboard: posizioni*prezzi correnti + cash).
                           Chiamato da price_updater a fine giro prezzi. No-op se la
                           tabella non esiste (la crea il PM con tools/migrations/setup_twr_tables.py).
  get_official_series()  - serie giornaliera del valore: nav_snapshots dove esistono
                           (regime 'official'), ricostruzione da compute_nav_history
                           per il passato pre-snapshot (regime 'reconstructed').
  compute_twr(series, flows) - TWR GIPS: r_t = (V_t - V_{t-1} - F_t) / V_{t-1},
                           convenzione flussi a FINE giornata (w=0, dichiarata).
  compute_irr(flows, terminal_value, ...) - XIRR money-weighted (Newton + bisezione).
  compute_twr_payload()  - payload completo per GET /portfolio/analytics/twr (cache 10 min).

REGIMI (documentati nel payload, campo 'regimes' per-data + 'regime_summary'):
  'official'      dal primo snapshot in poi. V = NAV totale (investito + cash).
                  F = SOLO flussi esterni dal ledger cash_movements (DEPOSIT +, WITHDRAWAL -).
                  Acquisti/vendite sono interni (cash <-> titoli dentro il NAV) e si elidono.
  'reconstructed' per il passato senza ledger ne' snapshot. V = solo capitale investito
                  mark-to-market (chiusure yfinance, da compute_nav_history). Senza storia
                  del cash, ogni BUY e' trattato come flusso IN al costo e ogni SELL come
                  flusso OUT al controvalore pieno: F_t = dCB_t - dRealized_t.
                  Cosi' gli acquisti NON sono profit e le vendite NON sono drawdown; il
                  realized resta rendimento (era nel prezzo durante l'holding period).
                  Dividendi (fix PNL-B 06/10): chiusure NON aggiustate per dividendi e
                  dividendi registrati (DIVIDEND) sommati al valore come CASSA dalla
                  data di incasso, come nel tratto ufficiale. (Prima: chiusure
                  auto_adjust e dividendi fuori -> mancavano dal rendimento in EUR.)
                  Punto BASE in testa = costo netto investito alla prima chiusura:
                  il primo r e' chiusura/costo - 1 (campo `base` del payload).
                  CB storico a FX STORICO del giorno del trade: usare il cambio
                  corrente altererebbe retroattivamente il costo storico.
  Transizione: se la ricostruzione copre il giorno del primo snapshot (d0) la catena e'
  continua (r ricostruiti fino a d0, poi r ufficiali da snapshot a snapshot con base =
  posizioni RICOSTRUITE a d0 + cassa dello snapshot d0, decisione PM 06/10; lo scarto
  snapshot - ricostruzione e' dichiarato in `cucitura`); altrimenti il giorno di salto
  vale r=0 (dichiarato). `riconciliazione_pnl`: P&L di performance contro P&L contabile
  con le voci misurate e il residuo (mai forzato).

IRR money-weighted (modello ibrido per ere, coerente con i regimi TWR):
  - pre-d0: flussi impliciti nei trade (BUY = esce dalla tasca del PM, SELL/TRIM/
    DIVIDEND = rientra) - il cash non tracciato dell'epoca resta fuori perimetro;
  - a d0: deposito sintetico = cash del primo snapshot (la cassa ENTRA nel perimetro
    misurato del fondo);
  - post-d0: SOLO ledger cash_movements (i trade sono interni al NAV);
  - terminale: NAV totale live (o solo investito se non esistono snapshot).
  Robusto a ledger parziale: i movimenti backfillati con data <= d0 NON si
  double-contano (quei depositi finanziavano buy gia' contati come flusso).
"""
import time
import sqlite3
import math
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional, List, Tuple

from bellomberg.storage.memory_db import MemoryDB, SQLITE_PATH, connect_sqlite
from bellomberg.core.presentation import message as _message, render_payload

_CACHE: Dict[str, Any] = {}
CACHE_TTL_SEC = 600  # 10 min, come portfolio_analytics


def _log(msg: str):
    # audit/11 §4: stesso guard di portfolio_analytics (fix 13/07, pipe morta Electron)
    try:
        print(f"[TWR] {msg}", flush=True)
    except OSError:
        pass


# F5 (riallineamento 23/07, audit/20): tolleranza di riconciliazione NAV —
# oltre questa soglia il payload alza `breach` e il health-check pre-run del
# consigliere la dichiara al Capo. Parametro PM, tarabile qui.
RECON_TOLERANCE_PCT = 1.0

# fix PNL-B (R-PNL F3, 06/10): tolleranza del RESIDUO della riconciliazione performance /
# contabile (cassa non spiegata: cambi di registrazione, commissioni, arrotondamenti).
# Oltre: «non_riconciliato». Parametro PM, tarabile qui.
RESIDUO_TOLLERANZA_EUR = 100.0
# voce «ultimo punto» oltre questa quota delle posizioni = «da_verificare» (prezzo sospetto).
# Scelta PM DICHIARATA nel payload (soglia_ultimo_punto_pct): sotto, uno scarto di prezzo
# dell'ultima foto passa come «misurata» e il titolo lo conta.
ULTIMO_PUNTO_SOGLIA_PCT = 1.0
# Variante PREPARATA, NON attiva (decisione PM in sospeso): l'ultimo punto della catena
# valuta le posizioni con gli STESSI prezzi della tabella contabile (ultima chiusura di
# nav_history, stessa data) + cassa dell'ultimo snapshot; la voce «ultimo punto» va a 0.
ULTIMO_PUNTO_PREZZI_CONTABILI = False


def build_recon_note(nav_live: float, snaps: List[Dict[str, Any]],
                     tolerance_pct: float = RECON_TOLERANCE_PCT) -> Optional[Dict[str, Any]]:
    """F5: riconciliazione |NAV live − ultimo snapshot| con soglia DICHIARATA.
    Helper PURO (niente DB/rete) per testabilita' offline. Ritorna None senza
    snapshot (buco gestito dal chiamante, mai un breach inventato)."""
    if not snaps:
        return None
    last_snap = snaps[-1]
    snap_nav = float(last_snap["nav_total_eur"] or 0)
    delta_pct = ((nav_live - snap_nav) / snap_nav * 100.0) if snap_nav > 0 else None
    return {
        "nav_live_eur": round(nav_live, 2),
        "last_snapshot_date": last_snap["date"],
        "last_snapshot_nav_eur": round(snap_nav, 2),
        "last_snapshot_created_at": last_snap.get("created_at"),
        "delta_pct": round(delta_pct, 3) if delta_pct is not None else None,
        "tolerance_pct": tolerance_pct,
        "breach": (delta_pct is not None and abs(delta_pct) > tolerance_pct),
        "note": _message('delta = NAV live (prezzi correnti) vs ultimo snapshot ufficiale persistito; breach = |delta| oltre {v0}% dichiarato', 'delta = live NAV (current prices) versus the latest persisted official snapshot; breach = |delta| above {v0}% as disclosed', v0=tolerance_pct),
    }


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ============================================================
# SNAPSHOT NAV UFFICIALE (scritto dal giro prezzi)
# ============================================================

def record_nav_snapshot(db: Optional[MemoryDB] = None) -> Dict[str, Any]:
    """Scrive/aggiorna lo snapshot NAV di OGGI in nav_snapshots.
    Fonte ufficiale = memory_db.get_portfolio_summary() (posizioni * prezzi
    correnti convertiti EUR + cash scalare): la STESSA catena del Dashboard.
    Tollerante: se la tabella non esiste -> no-op con log (mai eccezioni)."""
    try:
        if db is None:
            db = MemoryDB()
        db_path = getattr(db, "db_path", SQLITE_PATH)
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        try:
            if not _table_exists(conn, "nav_snapshots"):
                _log("nav_snapshots assente: snapshot saltato (lancia tools/migrations/setup_twr_tables.py)")
                return {"ok": False, "reason": _message('tabella nav_snapshots assente', 'nav_snapshots table missing')}
            # Mark the start of valuation. A balance inserted during the read
            # must not certify this older NAV as covering that new balance.
            now_iso = datetime.now().isoformat(timespec="microseconds")
            snap = db.get_portfolio_summary()
            has_opening = (_table_exists(conn, "position_openings") and
                           conn.execute("SELECT 1 FROM position_openings LIMIT 1").fetchone() is not None)
            if has_opening and snap.get("stale_positions"):
                return {"ok": False, "reason": _message(
                    "Saldi iniziali: prezzi mancanti o vecchi per {tickers}; nessuno snapshot ufficiale al costo.",
                    "Opening balances: missing or old prices for {tickers}; no official snapshot at cost.",
                    tickers=", ".join(snap["stale_positions"]))}
            if snap.get("fx_incomplete"):
                _log("FX incompleto, snapshot non scritto: " + str(snap["fx_incomplete"]))
                return {"ok": False, "reason": _message("FX incompleto: {missing}", "Incomplete FX: {missing}", missing=str(snap["fx_incomplete"]))}
            _fx_non_misurati = {
                cur: source for cur, source in (snap.get("fx_sources") or {}).items()
                if source != "live"
            }
            if _fx_non_misurati:
                _log("FX non live, snapshot non scritto: " + str(_fx_non_misurati))
                return {"ok": False, "reason": _message("FX non live: {sources}", "Non-live FX: {sources}", sources=str(_fx_non_misurati))}
            nav_total = float(snap.get("nav_total_eur") or 0)
            invested = float(snap.get("totale_valore_mercato_eur") or 0)
            cash = float(snap.get("cash_disponibile_eur") or 0)
            # F43(1) 27/08 (review): uno zero NON misurato (portfolio.json
            # assente/illeggibile) non diventa storia ufficiale del NAV —
            # fail-closed come il POST della cassa; il giro prezzi successivo
            # riprova. Chiave presente e nulla = dichiarazione del lettore.
            if "cash_source" in snap and snap["cash_source"] is None:
                _log("cassa NON misurata, snapshot non scritto: " + str(snap.get("cash_source_note")))
                return {"ok": False, "reason": _message("cassa NON misurata: {reason}", "Cash NOT measured: {reason}", reason=snap.get("cash_source_note"))}
            if nav_total <= 0:
                _log("NAV <= 0: snapshot non scritto")
                return {"ok": False, "reason": "nav <= 0"}
            today_iso = date.today().isoformat()
            conn.execute(
                "INSERT INTO nav_snapshots (date, nav_total_eur, invested_eur, cash_eur, source, created_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(date) DO UPDATE SET nav_total_eur=excluded.nav_total_eur, "
                "invested_eur=excluded.invested_eur, cash_eur=excluded.cash_eur, "
                "source=excluded.source, created_at=excluded.created_at",
                (today_iso, round(nav_total, 2), round(invested, 2), round(cash, 2),
                 "price_updater", now_iso))
            conn.commit()
            _log(f"snapshot {today_iso}: NAV EUR {nav_total:,.0f} (inv {invested:,.0f} + cash {cash:,.0f})")
            return {"ok": True, "date": today_iso, "nav_total_eur": round(nav_total, 2)}
        finally:
            conn.close()
    except Exception as e:
        _log(f"record_nav_snapshot failed (non bloccante): {e}")
        return {"ok": False, "reason": str(e)[:200]}


# ============================================================
# LETTURE TOLLERANTI (tabelle possono non esistere ancora)
# ============================================================

def _load_snapshots(db_path: str = SQLITE_PATH) -> List[Dict[str, Any]]:
    try:
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "nav_snapshots"):
                return []
            rows = conn.execute(
                "SELECT date, nav_total_eur, invested_eur, cash_eur, source, created_at "
                "FROM nav_snapshots ORDER BY date ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _log(f"load snapshots failed: {e}")
        return []


def get_cash_movements(db_path: str = SQLITE_PATH) -> List[Dict[str, Any]]:
    """Ledger flussi esterni, ordinato per data. [] se tabella assente."""
    try:
        conn = connect_sqlite(db_path)  # hardening #32: WAL + busy_timeout
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "cash_movements"):
                return []
            rows = conn.execute(
                "SELECT id, date, type, amount_eur, note FROM cash_movements "
                "ORDER BY date ASC, id ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _log(f"load cash_movements failed: {e}")
        return []


# ============================================================
# SERIE UFFICIALE (snapshot dove esistono + ricostruzione passato)
# ============================================================

def get_official_series() -> Dict[str, Any]:
    """Serie giornaliera del valore con regime per-data e flussi esterni per-data.
    Returns {dates, values_eur, flows_eur, regimes, official_since, notes, snapshots, ledger}."""
    snaps = _load_snapshots()
    ledger = get_cash_movements()
    notes: List[str] = []

    openings = MemoryDB().get_opening_positions()
    if openings:
        # Registration time, not the balance's as_of, determines which observed
        # NAV includes every holding. No synthetic trade or historical cash.
        def instant(value):
            parsed = datetime.fromisoformat(value)
            return parsed.astimezone()  # naive snapshot timestamps are local by contract
        latest = max(openings, key=lambda row: instant(row["created_at"]))
        added = latest["created_at"]
        covered = []
        latest_day = max(row["as_of"][:10] for row in openings)
        for snap in snaps:
            try:
                if (snap.get("source") == "price_updater"
                        and instant(snap["created_at"]) >= instant(added)
                        and snap["date"] >= latest_day):
                    covered.append(snap)
            except (ValueError, TypeError, KeyError):
                continue  # cannot certify which balances this observation includes
        excluded = len(snaps) - len(covered)
        d0 = covered[0]["date"] if covered else None
        notes.append(_message('Saldi iniziali registrati fino a {v0}; primo snapshot successivo: {v1}. Snapshot precedenti o non verificabili esclusi: {v2}. Storia degli acquisti ignota: nessuna ricostruzione del periodo precedente.', 'Opening balances registered through {v0}; first subsequent snapshot: {v1}. Earlier or unverifiable snapshots excluded: {v2}. Purchase history unknown: no reconstruction of the earlier period.', v0=added, v1=d0 or _message('n.d.', 'n/a'), v2=excluded))
        flows, previous = [], d0
        for snap in covered:
            flows.append(round(sum((1 if row["type"] == "DEPOSIT" else -1) * float(row["amount_eur"])
                                   for row in ledger if previous < row["date"] <= snap["date"]), 2))
            previous = snap["date"]
        return {"dates": [s["date"] for s in covered],
                "values_eur": [float(s["nav_total_eur"]) for s in covered],
                "flows_eur": flows, "regimes": ["official"] * len(covered),
                "official_since": d0, "seamless_transition": False,
                "notes": notes, "snapshots": covered, "ledger": ledger,
                "position_openings": openings, "baseline_added_at": added,
                "snapshot_prima_del_baseline": excluded,
                "recon_error": _message('Saldi iniziali: periodo precedente al primo snapshot coperto non ricostruibile.', 'Opening balances: the period before the first covered snapshot cannot be reconstructed.')}

    recon = {}
    try:
        from bellomberg.portfolio.portfolio_analytics import compute_nav_history
        recon = compute_nav_history() or {}
    except Exception as e:
        recon = {"error": str(e)}
        try:
            import traceback as _tb
            _log("ricostruzione NAV FALLITA (stack completo):\n" + _tb.format_exc())
        except Exception:
            pass
    if recon.get("error"):
        notes.append(_message('ricostruzione storica non disponibile: {v0}', 'Historical reconstruction unavailable: {v0}', v0=recon['error']))

    d0 = snaps[0]["date"] if snaps else None

    dates: List[str] = []
    values: List[float] = []
    flows: List[float] = []
    regimes: List[str] = []

    # --- tratto RICOSTRUITO: chiusure daily fino a d0 incluso (o tutto se niente snapshot)
    base: Optional[Dict[str, Any]] = None
    dividendi: Optional[Dict[str, Any]] = None
    if not recon.get("error") and recon.get("dates"):
        rd = recon["dates"]
        rv = recon["nav_eur"]
        rcb = recon.get("cost_basis_eur") or [0.0] * len(rd)
        rre = recon.get("realized_sales_eur") or [0.0] * len(rd)
        # fix PNL-B (06/10, Opus 5.5): i dividendi del tratto ricostruito restano nel
        # valore come CASSA/CREDITO (come nel tratto ufficiale, dove stanno nel NAV dello
        # snapshot). Prima sparivano: V = solo mercato e F = dCB - dRealized non li vede.
        # Le chiusure di compute_nav_history sono NON aggiustate: il dividendo entra UNA
        # volta, MATURATO all'ex-date (F1, revisione R-PNL), cosi' il calo del prezzo allo
        # stacco e il credito si compensano lo stesso giorno (niente buco fino all'incasso).
        rdiv = recon.get("dividendi_maturati_eur")
        if rdiv is None or len(rdiv) != len(rd):
            rdiv = None
            dividendi = {"stato": "n.d.", "nota": _message(
                'dividendi maturati del tratto ricostruito n.d. (serie assente o disallineata): NON inclusi nel valore.',
                'Accrued dividends in the reconstructed segment unavailable (series missing or misaligned): NOT included in value.')}
            notes.append(dividendi["nota"])
        # fix PNL-B (C): il rendimento dall'inizio parte dal COSTO, non dalla prima
        # chiusura. Punto base = costo netto investito alla prima chiusura
        # (CB - realizzato), datato al primo trade se precede la prima chiusura,
        # altrimenti alla vigilia (convenzione dichiarata: flusso a inizio giornata).
        # Il primo r e' chiusura/costo - 1; la prima chiusura non ha piu' flusso.
        costo0 = round(float(rcb[0]) - float(rre[0]), 2)
        primo_trade = str(recon.get("first_trade_date") or "")[:10]
        try:
            datetime.strptime(primo_trade, "%Y-%m-%d")
            valida = True
        except ValueError:
            valida = False
        if valida and primo_trade < rd[0]:
            data_base, vigilia = primo_trade, False
        else:
            data_base = (date.fromisoformat(rd[0]) - timedelta(days=1)).isoformat()
            vigilia = True
        if costo0 > 0 and (d0 is None or data_base <= d0):
            dates.append(data_base)
            values.append(costo0)
            flows.append(costo0)  # il capitale iniziale e' un flusso, non un return
            regimes.append("reconstructed")
            base = {"tipo": "costo", "data": data_base, "valore_eur": costo0,
                    "data_convenzionale": vigilia,
                    "nota": (_message('base = costo netto investito alla prima chiusura, alla vigilia della prima chiusura (data convenzionale: il primo trade cade nel giorno della chiusura); primo rendimento = chiusura/costo - 1.',
                                      'Base = net invested cost at the first close, on the eve of the first close (conventional date: the first trade falls on the closing day); first return = close/cost - 1.')
                             if vigilia else
                             _message('base = costo netto investito alla prima chiusura, alla data del primo trade; primo rendimento = chiusura/costo - 1.',
                                      'Base = net invested cost at the first close, on the first trade date; first return = close/cost - 1.'))}
        else:
            base = {"tipo": "chiusura", "data": rd[0], "valore_eur": None,
                    "nota": (_message('base = prima chiusura: costo netto iniziale non positivo, il primo giorno (costo -> chiusura) NON viene misurato.',
                                      'Base = first close: initial net cost not positive; the first day (cost -> close) is NOT measured.')
                             if costo0 <= 0 else
                             _message('base = prima chiusura: la data base cadrebbe dopo il primo snapshot, il primo giorno (costo -> chiusura) NON viene misurato.',
                                      'Base = first close: the base date would fall after the first snapshot; the first day (cost -> close) is NOT measured.'))}
        for i, dstr in enumerate(rd):
            if d0 is not None and dstr > d0:
                break
            dates.append(dstr)
            values.append(float(rv[i]) + (float(rdiv[i]) if rdiv is not None else 0.0))
            regimes.append("reconstructed")
            if i == 0:
                # con il punto base il capitale iniziale e' gia' entrato li'
                flows.append(0.0 if base["tipo"] == "costo" else float(rcb[0]))
            else:
                f = (float(rcb[i]) - float(rcb[i - 1])) - (float(rre[i]) - float(rre[i - 1]))
                flows.append(round(f, 2))
        if rdiv is not None:
            idx_d0 = len([x for x in rd if d0 is None or x <= d0]) - 1
            fine = rd[idx_d0] if idx_d0 >= 0 else ""
            # F8 (R-PNL): stacchi Yahoo durante il possesso senza registrazione, fino alla
            # fine del tratto: restano FUORI dal valore e lo stato lo dice col conteggio.
            nr = recon.get("dividendi_non_registrati")
            fuori = None if nr is None else [x for x in nr if str(x.get("ex_date") or "") <= fine]
            if fuori is None:
                stato = "inclusi_registrati_verifica_nd"
                nota = _message('dividendi registrati del tratto ricostruito inclusi nel valore, maturati all\'ex-date; verifica degli stacchi non registrati NON eseguita.',
                                'Recorded dividends in the reconstructed segment included in value, accrued on the ex-date; check for unrecorded ex-dates NOT performed.')
            elif fuori:
                stato = "parziale"
                nota = _message('dividendi del tratto ricostruito PARZIALI: inclusi quelli registrati (maturati all\'ex-date); {v0} stacchi durante il possesso senza registrazione restano fuori dal rendimento.',
                                'Dividends in the reconstructed segment PARTIAL: recorded ones included (accrued on the ex-date); {v0} ex-dates during holding without a record are left out of the return.', v0=len(fuori))
                notes.append(nota)
            else:
                stato = "inclusi"
                nota = _message('dividendi del tratto ricostruito inclusi nel valore, maturati all\'ex-date.',
                                'Dividends in the reconstructed segment included in value, accrued on the ex-date.')
            dividendi = {"stato": stato, "inclusi_eur": round(float(rdiv[idx_d0]), 2) if idx_d0 >= 0 else 0.0,
                         "stacchi_non_registrati": (len(fuori) if fuori is not None else None),
                         "nota": nota}

    # --- tratto UFFICIALE: snapshot NAV totale + flussi esterni dal ledger
    seamless = bool(dates) and d0 is not None and dates[-1] == d0
    crediti_uff: List[Dict[str, Any]] = []
    if snaps:
        flow_by_date: Dict[str, float] = {}
        for m in ledger:
            sgn = 1.0 if (m.get("type") == "DEPOSIT") else -1.0
            flow_by_date[m["date"]] = flow_by_date.get(m["date"], 0.0) + sgn * float(m.get("amount_eur") or 0)
        prev_date = d0
        last_recon_date = dates[-1] if dates else ""
        # R-PNL G3: fra stacco e incasso lo snapshot ha gia' il prezzo ex-dividendo ma non
        # ancora la cassa: il dividendo maturato e non incassato (dalla storia NAV) si somma
        # al valore come CREDITO, cosi' ne' lo stacco ne' l'incasso creano un salto.
        rd_c = recon.get("dates") if not recon.get("error") else None
        mat_c, inc_c = recon.get("dividendi_maturati_eur"), recon.get("dividend_income_eur")
        crediti_ok = bool(rd_c) and mat_c is not None and inc_c is not None and len(mat_c) == len(rd_c) == len(inc_c)

        def credito_al(giorno):
            if not crediti_ok:
                return 0.0
            noti = [i for i, x in enumerate(rd_c) if x <= giorno]
            return (float(mat_c[noti[-1]]) - float(inc_c[noti[-1]])) if noti else 0.0
        if snaps and not crediti_ok:
            notes.append(_message('crediti per dividendi maturati non incassati n.d. nel tratto ufficiale (storia NAV senza serie dei maturati): fra stacco e incasso il rendimento puo\' avere un salto.',
                                  'Receivables for accrued unpaid dividends unavailable in the official segment (NAV history without accrued series): returns may jump between ex-date and payment.'))
        for s in snaps:
            dstr = s["date"]
            if dstr <= last_recon_date:
                # data gia' coperta dal tratto ricostruito (il punto base d0): non duplicare
                prev_date = dstr
                continue
            # flussi esterni nell'intervallo (prev_date, dstr]
            f = sum(v for k, v in flow_by_date.items()
                    if (prev_date is None or k > prev_date) and k <= dstr)
            dates.append(dstr)
            cr = credito_al(dstr)
            if abs(cr) > 0.005:
                crediti_uff.append({"date": dstr, "credito_eur": round(cr, 2)})
            values.append(float(s["nav_total_eur"]) + cr)
            flows.append(round(f, 2))
            regimes.append("official")
            prev_date = dstr
        pre_ledger = [m for m in ledger if m["date"] <= (d0 or "9999-12-31")]
        if pre_ledger:
            notes.append(_message(
                "{v0} movimento del ledger precede il primo snapshot: nel tratto ricostruito i flussi sono gia' impliciti nei trade (non doppio-contati)." if len(pre_ledger) == 1 else
                "{v0} movimenti del ledger precedono il primo snapshot: nel tratto ricostruito i flussi sono gia' impliciti nei trade (non doppio-contati).",
                '{v0} ledger movement precedes the first snapshot: flows in the reconstructed segment are already implicit in trades (not counted twice).' if len(pre_ledger) == 1 else
                '{v0} ledger movements precede the first snapshot: flows in the reconstructed segment are already implicit in trades (not counted twice).', v0=len(pre_ledger)))
        if not seamless and dates:
            notes.append(_message('transizione {v0}: ricostruzione e snapshot non si sovrappongono, r del giorno di salto = 0 (perimetro non confrontabile).', 'Transition {v0}: reconstruction and snapshots do not overlap; return on the transition day = 0 (non-comparable scope).', v0=d0))
        if not ledger:
            notes.append(_message("ledger cash_movements vuoto: nel regime official i flussi esterni valgono 0 finche' non registri depositi/prelievi con tools/migrations/setup_twr_tables.py.", 'Empty cash_movements ledger: external flows are zero in the official regime until deposits/withdrawals are recorded with tools/migrations/setup_twr_tables.py.'))

    return {
        "dates": dates, "values_eur": values, "flows_eur": flows, "regimes": regimes,
        "official_since": d0, "seamless_transition": seamless,
        "notes": notes, "snapshots": snaps, "ledger": ledger,
        "recon_error": recon.get("error"),
        "base": base, "dividendi_ricostruiti": dividendi, "crediti_dividendi_ufficiali": crediti_uff,
        # serie contabile completa (fino all'ultima chiusura) per la riconciliazione
        "recon": recon if (not recon.get("error") and recon.get("dates")) else None,
    }


# ============================================================
# TWR GIPS + metriche sulla serie TWR
# ============================================================

def compute_twr(series: List[float], flows: List[float]) -> List[float]:
    """TWR GIPS: r_t = (V_t - V_{t-1} - F_t) / V_{t-1}.
    F_t = SOLO flussi esterni attribuiti all'intervallo (t-1, t], convenzione
    fine-giornata (w=0). Ritorna la lista dei rendimenti (len = len(series)-1).
    Guardia: V_{t-1} <= 0 -> r=0 (intervallo non misurabile)."""
    rets: List[float] = []
    for t in range(1, len(series)):
        v_prev = float(series[t - 1])
        v_now = float(series[t])
        f = float(flows[t]) if t < len(flows) else 0.0
        if v_prev > 0:
            rets.append((v_now - v_prev - f) / v_prev)
        else:
            rets.append(0.0)
    return rets


def _twr_metrics(dates: List[str], rets: List[float], rf_annual: float, salta: int = 0) -> Dict[str, Any]:
    """Indice base 100 + drawdown/max_dd/vol/Sharpe calcolati SULLA SERIE TWR.
    `salta` (R-PNL G2) = punti iniziali esclusi dalle STATISTICHE (punto base al costo:
    il suo primo r contiene guadagni maturati prima, non e' un rendimento giornaliero);
    l'indice e il rendimento totale/annualizzato restano dal primo punto."""
    index = [100.0]
    for r in rets:
        index.append(index[-1] * (1.0 + r))
    index = [round(x, 4) for x in index]
    idx_stat = index[salta:]
    rets_stat = rets[salta:]
    dates_stat = dates[salta:]

    peak = idx_stat[0]
    max_dd = 0.0
    for x in idx_stat:
        if x > peak:
            peak = x
        dd = (x - peak) / peak * 100.0
        if dd < max_dd:
            max_dd = dd
    overall_peak = max(idx_stat)
    current_dd = (idx_stat[-1] - overall_peak) / overall_peak * 100.0

    twr_total_pct = (index[-1] / 100.0 - 1.0) * 100.0
    ann_return = None
    vol_annual = None
    sharpe = None
    if len(dates) >= 2:
        try:
            span_days = (datetime.strptime(dates[-1], "%Y-%m-%d")
                         - datetime.strptime(dates[0], "%Y-%m-%d")).days
        except Exception:
            span_days = len(rets)
        if span_days >= 20:
            ann_return = ((index[-1] / 100.0) ** (365.25 / span_days) - 1.0) * 100.0
    ann_stat = None
    if len(dates_stat) >= 2:
        try:
            span_stat = (datetime.strptime(dates_stat[-1], "%Y-%m-%d")
                         - datetime.strptime(dates_stat[0], "%Y-%m-%d")).days
        except Exception:
            span_stat = len(rets_stat)
        if span_stat >= 20:
            ann_stat = ((idx_stat[-1] / idx_stat[0]) ** (365.25 / span_stat) - 1.0) * 100.0
    if len(rets_stat) >= 20:
        mean = sum(rets_stat) / len(rets_stat)
        var = sum((r - mean) ** 2 for r in rets_stat) / (len(rets_stat) - 1)
        vol_annual = (var ** 0.5) * (252 ** 0.5) * 100.0
        if vol_annual and vol_annual > 0 and ann_stat is not None:
            sharpe = (ann_stat / 100.0 - rf_annual) / (vol_annual / 100.0)

    return {
        "index": index,
        "twr_total_pct": round(twr_total_pct, 2),
        "twr_annualized_pct": round(ann_return, 2) if ann_return is not None else None,
        "max_drawdown_pct": round(max_dd, 2),
        "current_drawdown_pct": round(current_dd, 2),
        "vol_annual_pct": round(vol_annual, 2) if vol_annual is not None else None,
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "risk_free_used": rf_annual,
    }


# ============================================================
# IRR MONEY-WEIGHTED (XIRR: Newton con fallback bisezione)
# ============================================================

def compute_irr(flows: List[Tuple[str, float]], terminal_value: float,
                terminal_date: Optional[str] = None) -> Optional[float]:
    """XIRR annualizzato. flows = [(iso_date, eur)] in convenzione INVESTITORE:
    deposito/acquisto = NEGATIVO (soldi che escono dalla tasca del PM),
    prelievo/vendita/dividendo = POSITIVO. terminal_value = valore liquidabile
    oggi (positivo). Newton-Raphson, fallback bisezione su [-0.95, 10].

    Formulazione a VALORE FUTURO (equivalente a NPV=0, numericamente piu'
    stabile vicino a r=-0.95): FV(r) = terminal + sum cf_i * (1+r)^yrs_i = 0,
    con yrs_i = anni dal flusso alla data terminale."""
    if not flows or terminal_value is None:
        return None
    t_end = terminal_date or date.today().isoformat()
    try:
        d_end = datetime.strptime(t_end[:10], "%Y-%m-%d")
    except Exception:
        return None
    cfs: List[Tuple[float, float]] = []  # (anni dal flusso alla data terminale, importo)
    for dstr, amt in flows:
        try:
            d = datetime.strptime((dstr or "")[:10], "%Y-%m-%d")
        except Exception:
            continue
        yrs = max((d_end - d).days / 365.25, 0.0)
        cfs.append((yrs, float(amt)))
    if not cfs:
        return None

    def fv(rate: float) -> float:
        total = float(terminal_value)
        for yrs, cf in cfs:
            total += cf * (1.0 + rate) ** yrs
        return total

    def fv_prime(rate: float) -> float:
        tot = 0.0
        for yrs, cf in cfs:
            if yrs > 0:
                tot += cf * yrs * (1.0 + rate) ** (yrs - 1.0)
        return tot

    tol = 1e-6 * max(1.0, abs(float(terminal_value)))

    # Newton-Raphson
    rate = 0.10
    for _ in range(60):
        f = fv(rate)
        if abs(f) < tol and -0.95 < rate < 10.0:
            return float(rate)
        fp = fv_prime(rate)
        if abs(fp) < 1e-12:
            break
        new_rate = rate - f / fp
        if new_rate <= -0.95:
            new_rate = (rate - 0.95) / 2.0
        if new_rate > 10.0:
            new_rate = (rate + 10.0) / 2.0
        if abs(new_rate - rate) < 1e-10:
            rate = new_rate
            break
        rate = new_rate
    if abs(fv(rate)) < tol and -0.95 < rate < 10.0:
        return float(rate)

    # Fallback: bisezione su [-0.95, 10] (richiede cambio di segno)
    lo, hi = -0.95, 10.0
    f_lo, f_hi = fv(lo), fv(hi)
    if f_lo * f_hi > 0:
        return None  # nessuna radice nel range ragionevole
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = fv(mid)
        if abs(f_mid) < tol or (hi - lo) < 1e-9:
            return float(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return float((lo + hi) / 2.0)


def _build_irr_flows(ctx: Dict[str, Any], live_summary: Dict[str, Any]) -> Tuple[List[Tuple[str, float]], float, str]:
    """Flussi per l'IRR money-weighted, modello ibrido per ere (vedi docstring modulo).
    Returns (flows, terminal_value, basis)."""
    snaps = ctx.get("snapshots") or []
    ledger = ctx.get("ledger") or []
    d0 = snaps[0]["date"] if snaps else None
    flows: List[Tuple[str, float]] = []
    foreign = set()
    if ctx.get("position_openings"):
        if len(snaps) < 2:
            return [], 0.0, _message('IRR n.d.: servono due snapshot successivi alla registrazione di tutti i saldi iniziali.', 'IRR unavailable: two snapshots after registration of all opening balances are required.')
        terminal_day = snaps[-1]["date"]
        flows.append((d0, -float(snaps[0]["nav_total_eur"])))
        for movement in ledger:
            if d0 < movement["date"] <= terminal_day:
                amount = float(movement["amount_eur"])
                flows.append((movement["date"], -amount if movement["type"] == "DEPOSIT" else amount))
        return flows, float(snaps[-1]["nav_total_eur"]), (
            _message('Snapshot osservati dal {v0} al {v1}; NAV iniziale e flussi esterni documentati. Ultima registrazione saldo: {v2}; acquisti precedenti ignoti.', 'Observed snapshots from {v0} to {v1}; initial NAV and external flows documented. Latest balance registration: {v2}; earlier purchases unknown.', v0=d0, v1=terminal_day, v2=ctx['baseline_added_at']))

    # Era A: flussi impliciti nei trade fino a d0 incluso (o tutta la storia se no snapshot)
    try:
        db = MemoryDB()
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT ticker, action, quantita, prezzo, valuta, data "
                "FROM trade_history ORDER BY data ASC").fetchall()
        from bellomberg.cli.price_updater import get_fx_to_eur_con_fonte
        for r in rows:
            dstr = (r["data"] or "")[:10]
            if not dstr:
                continue
            if d0 is not None and dstr > d0:
                continue  # post-d0: i trade sono interni al NAV, non flussi esterni
            action = (r["action"] or "").upper()
            qty = float(r["quantita"] or 0)
            px = float(r["prezzo"] or 0)
            ccy = (r["valuta"] or "").upper()
            if ccy == "EUR":
                fx, source = 1.0, "identity"
            else:
                fx, source = get_fx_to_eur_con_fonte(ccy)
                foreign.add(ccy)
            if (not ccy or fx is None or not math.isfinite(fx) or fx <= 0
                    or source not in ("live", "identity")):
                return [], 0.0, _message('IRR non calcolabile: FX {v0}->EUR n.d. o fonte {v1} non utilizzabile', 'IRR cannot be calculated: FX {v0}->EUR unavailable or source {v1} unusable', v0=ccy or _message('valuta n.d.', 'currency unavailable'), v1=source)
            amt = qty * px * fx
            if action in ("BUY", "ADD"):
                flows.append((dstr, -amt))
            elif action in ("SELL", "TRIM", "DIVIDEND"):
                flows.append((dstr, amt))
    except Exception as e:
        _log(f"irr trade flows failed: {e}")
        return [], 0.0, _message("IRR non calcolabile: {error}", "IRR cannot be calculated: {error}", error=str(e))

    fx_note = (_message("; FX corrente (non storico) per {currencies}",
                        "; current (not historical) FX for {currencies}",
                        currencies=", ".join(sorted(foreign))) if foreign else "")

    if not snaps:
        terminal = float(live_summary.get("totale_valore_mercato_eur") or 0)
        return flows, terminal, _message("trades{fx_note}", "trades{fx_note}", fx_note=fx_note)

    # Transizione: la cassa del primo snapshot entra nel perimetro misurato
    cash_d0 = float(snaps[0].get("cash_eur") or 0)
    if cash_d0 > 0:
        flows.append((d0, -cash_d0))

    # Era B: solo ledger post-d0
    for m in ledger:
        if m["date"] <= d0:
            continue
        amt = float(m.get("amount_eur") or 0)
        flows.append((m["date"], -amt if m.get("type") == "DEPOSIT" else amt))

    terminal = float(live_summary.get("nav_total_eur") or 0)
    return flows, terminal, _message("hybrid: trades fino al primo snapshot + cash iniziale + ledger dopo{fx_note}",
                                     "hybrid: trades through the first snapshot + initial cash + subsequent ledger{fx_note}", fx_note=fx_note)


def _performance_coverage(db, ctx):
    """Calendar coverage, independent of return calculations or invented cash history."""
    snaps = sorted({s["date"] for s in ctx.get("snapshots", [])})
    first, last = (snaps[0], snaps[-1]) if snaps else (None, None)
    coverage = {"primo_trade": None, "primo_snapshot": first, "ultimo_snapshot": last,
                "official_since": ctx.get("official_since"), "n_trade_prima_del_primo_snapshot": None,
                "giorni_senza_snapshot": None, "nota": None}
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT data FROM trade_history ORDER BY data,id").fetchall()
        days = [datetime.fromisoformat(r["data"]).date().isoformat() for r in rows]
        coverage["primo_trade"] = min(days) if days else None
        if first:
            coverage["n_trade_prima_del_primo_snapshot"] = sum(d < first for d in days)
            coverage["giorni_senza_snapshot"] = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1 - len(snaps)
        coverage["nota"] = (_message("Primo trade: {v0}; primo snapshot: {v1}. Prima degli snapshot la serie e ricostruita senza storia completa della cassa. Giorni di calendario senza snapshot nell'intervallo: {v2}. Le registrazioni retrodatate non riscrivono gli snapshot NAV passati.", 'First trade: {v0}; first snapshot: {v1}. Before snapshots, the series is reconstructed without complete cash history. Calendar days without a snapshot in the interval: {v2}. Backdated entries do not rewrite past NAV snapshots.', v0=coverage['primo_trade'] or _message('n.d.', 'n/a'), v1=first or _message('n.d.', 'n/a'), v2=coverage['giorni_senza_snapshot'] if first else _message('n.d.', 'n/a')))
    except Exception as exc:
        coverage["nota"] = _message('Copertura non verificabile: {v0}: {v1}', 'Coverage cannot be verified: {v0}: {v1}', v0=type(exc).__name__, v1=exc)
    if ctx.get("position_openings"):
        coverage.update({"baseline_added_at": ctx["baseline_added_at"],
                         "snapshot_prima_del_baseline": ctx["snapshot_prima_del_baseline"],
                         "position_openings": ctx["position_openings"]})
        coverage["nota"] = (_message('Ultimo saldo iniziale registrato: {v0}; periodo coperto: {v1} - {v2}. Snapshot precedenti o non verificabili esclusi: {v3}. Data di acquisto e storia precedente ignote. Giorni senza snapshot nel periodo: {v4}.', 'Latest opening balance registered: {v0}; covered period: {v1} - {v2}. Earlier or unverifiable snapshots excluded: {v3}. Purchase date and earlier history unknown. Days without a snapshot in the period: {v4}.', v0=ctx['baseline_added_at'], v1=first or _message('n.d.', 'n/a'), v2=last or _message('n.d.', 'n/a'), v3=ctx['snapshot_prima_del_baseline'], v4=coverage['giorni_senza_snapshot'] if first else _message('n.d.', 'n/a')))
    return coverage


# ============================================================
# RICONCILIAZIONE P&L: performance (catena TWR) vs contabile (nav_history)
# ============================================================

def _seam_base(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """fix PNL-B (A): base del primo rendimento ufficiale = posizioni ricostruite a d0 +
    cassa dello snapshot d0. Dichiara lo scarto snapshot - ricostruzione (lo snapshot
    storico NON si riscrive). base_eur None = non costruibile (motivo in nota): la catena
    usa allora il NAV dello snapshot, come prima, e lo dice."""
    snaps = ctx.get("snapshots") or []
    recon = ctx.get("recon") or {}
    if not snaps:
        return None
    s0 = snaps[0]
    d0 = s0["date"]
    rd = recon.get("dates") or []
    cash0 = s0.get("cash_eur")
    if d0 not in rd or cash0 is None:
        return {"base_eur": None, "data": d0,
                "nota": _message('cucitura {v0}: base = NAV dello snapshot (posizioni ricostruite o cassa dello snapshot n.d.).',
                                 'Seam {v0}: base = snapshot NAV (reconstructed positions or snapshot cash unavailable).', v0=d0)}
    k = rd.index(d0)
    pos = float(recon["nav_eur"][k])
    # credito = dividendi maturati (ex-date passato) e non ancora incassati a d0: il tratto
    # ricostruito li ha gia' contati, la cassa dello snapshot no. Entrano nella base cosi'
    # che all'incasso (cassa +) il dividendo non si conti due volte.
    mat, inc = recon.get("dividendi_maturati_eur"), recon.get("dividend_income_eur")
    credito = (float(mat[k]) - float(inc[k])) if (mat is not None and inc is not None
                                                 and len(mat) == len(rd) == len(inc)) else 0.0
    inv0 = s0.get("invested_eur")
    scarto = round(float(inv0) - pos, 2) if inv0 is not None else None
    return {"base_eur": round(pos + float(cash0) + credito, 2), "data": d0,
            "credito_dividendi_eur": round(credito, 2),
            "posizioni_ricostruite_eur": round(pos, 2), "cassa_snapshot_eur": round(float(cash0), 2),
            "investito_snapshot_eur": (round(float(inv0), 2) if inv0 is not None else None),
            "scarto_snapshot_meno_ricostruzione_eur": scarto,
            "nota": _message('cucitura {v0}: base del primo rendimento ufficiale = posizioni ricostruite dalle chiusure + cassa dello snapshot; scarto snapshot - ricostruzione sulle posizioni NON contato nel rendimento: {v1} EUR (lo snapshot storico resta invariato).',
                             'Seam {v0}: base of the first official return = positions reconstructed from closes + snapshot cash; snapshot - reconstruction gap on positions NOT counted in the return: {v1} EUR (the historical snapshot is left unchanged).',
                             v0=d0, v1=(scarto if scarto is not None else _message('n.d.', 'n/a')))}


def _ultimo_punto_prezzi_contabili(ctx: Dict[str, Any], values: List[float],
                                   regimes: List[str]) -> Dict[str, Any]:
    """Variante (non attiva) dell'ultimo punto: posizioni all'ultima chiusura di nav_history
    + cassa dell'ultimo snapshot, se le date coincidono. Modifica values[-1] in posto e
    ritorna la dichiarazione (+ `_snaps` con l'ultimo snapshot rivalutato, per le voci)."""
    snaps = ctx.get("snapshots") or []
    recon = ctx.get("recon") or {}
    rd = recon.get("dates") or []
    if not (snaps and regimes and regimes[-1] == "official" and rd and rd[-1] == snaps[-1]["date"]
            and snaps[-1].get("invested_eur") is not None):
        return {"tipo": "snapshot", "nota": _message(
            'variante prezzi contabili attiva ma non applicabile (date diverse o investito n.d.): ultimo punto = snapshot.',
            'Accounting-price variant active but not applicable (different dates or invested unavailable): last point = snapshot.')}
    sl = dict(snaps[-1])
    cassa = float(sl["nav_total_eur"]) - float(sl["invested_eur"])
    credito = float(values[-1]) - float(sl["nav_total_eur"])  # dividendi maturati non incassati (G3)
    pos = float(recon["nav_eur"][-1])
    sl["invested_eur"], sl["nav_total_eur"] = pos, pos + cassa
    values[-1] = pos + cassa + credito
    return {"tipo": "prezzi_contabili", "data": sl["date"], "_snaps": snaps[:-1] + [sl],
            "nota": _message('ultimo punto = posizioni alle chiusure di nav_history ({v0}) + cassa dell\'ultimo snapshot.',
                             'Last point = positions at nav_history closes ({v0}) + cash from the latest snapshot.', v0=sl["date"])}


def _pnl_da_indice(values: List[float], flows: List[float], index: List[float]) -> float:
    """P&L in euro come lo calcola la pagina: (V_t - F_t) * r_t / (1 + r_t),
    con r_t dall'indice ARROTONDATO del payload (= base_{t-1} * r_t)."""
    tot = 0.0
    for t in range(1, min(len(values), len(flows), len(index))):
        if not (index[t - 1] > 0):
            continue
        r = index[t] / index[t - 1] - 1.0
        if 1.0 + r != 0:
            tot += (float(values[t]) - float(flows[t])) * r / (1.0 + r)
    return tot


_ORDINE_STATI = ["riconciliato_entro_tolleranza", "da_verificare", "incoerente", "non_riconciliato", "n.d."]


def _stato_complessivo(out: Dict[str, Any]) -> str:
    """R-PNL G4: stato della riconciliazione = il PEGGIORE fra residuo, voci e controllo
    cassa (una voce «da_verificare» non lascia verde lo stato complessivo)."""
    stati = [out.get("residuo_stato") or "n.d."]
    stati += ["da_verificare" for v in out.get("voci") or [] if v.get("stato") == "da_verificare"]
    if (out.get("controllo_cassa") or {}).get("stato") == "incoerente":
        stati.append("incoerente")
    return max(stati, key=lambda x: _ORDINE_STATI.index(x) if x in _ORDINE_STATI else len(_ORDINE_STATI))


def build_pnl_reconciliation(dates: List[str], values: List[float], flows: List[float],
                             regimes: List[str], bases: List[Optional[float]],
                             rets: List[float], index: List[float],
                             snaps: List[Dict[str, Any]], recon: Optional[Dict[str, Any]],
                             seamless: bool, cucitura: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Fix PNL-B (D): P&L di PERFORMANCE dall'inizio (somma dei P&L giornalieri della
    catena TWR, come in pagina) contro P&L CONTABILE (non realizzato + realizzato +
    dividendi incassati all'ultima chiusura di compute_nav_history), con le voci che li
    separano MISURATE una per una da fonti proprie.
    Revisione R-PNL (F3): NESSUNA voce fa da tappo. La cassa si misura da sola: variazione
    osservata negli snapshot contro variazione attesa da ledger, trade al costo e dividendi
    incassati; quello che non torna (cambi di registrazione, commissioni, un versamento non
    registrato, un errore) resta nel RESIDUO, che ha uno stato: entro la tolleranza
    dichiarata RESIDUO_TOLLERANZA_EUR o «non_riconciliato». n.d. con il motivo quando una
    voce non e' misurabile. Helper PURO (niente DB/rete)."""
    exact = sum(b * r for b, r in zip(bases, rets) if b is not None)
    perf_idx = _pnl_da_indice(values, flows, index)
    out: Dict[str, Any] = {
        "pnl_performance_eur": round(perf_idx, 2),
        "pnl_performance_esatto_eur": round(exact, 2),
        "pnl_contabile_eur": None, "contabile": None, "voci": [], "cassa": None,
        "residuo_eur": None, "residuo_stato": "n.d.", "motivo_nd": None, "stato": "n.d.",
        "tolleranza_residuo_eur": RESIDUO_TOLLERANZA_EUR,
        "soglia_ultimo_punto_pct": ULTIMO_PUNTO_SOGLIA_PCT,
        "nota": _message('performance = somma dei P&L giornalieri della catena TWR (titolo della pagina); contabile = non realizzato + realizzato + dividendi incassati dell\'ultima chiusura di nav_history. Residuo = performance - contabile - voci misurate: contiene la cassa non spiegata (cambi di registrazione, commissioni, flussi non registrati) e ogni errore; mai forzato.',
                         'Performance = sum of daily P&L along the TWR chain (page headline); accounting = unrealised + realised + received dividends at the latest nav_history close. Residual = performance - accounting - measured items: it holds unexplained cash (booking FX, fees, unrecorded flows) and any error; never forced.'),
    }

    grezzi: List[float] = []

    def voce(codice, importo, it, en, stato="misurata", **kw):
        grezzi.append(importo)
        out["voci"].append({"voce": codice, "importo_eur": round(importo, 2),
                            "stato": stato, "nota": _message(it, en, **kw)})

    voce("arrotondamento_indice", perf_idx - exact,
         'P&L dall\'indice arrotondato a 4 decimali meno P&L esatto dai valori.',
         'P&L from the index rounded to 4 decimals minus exact P&L from values.')
    if not recon:
        out["motivo_nd"] = _message('serie contabile (nav_history) non disponibile: riconciliazione n.d.',
                                    'Accounting series (nav_history) unavailable: reconciliation unavailable.')
        return out
    rd = recon["dates"]
    L = len(rd) - 1

    def at(key, i):
        serie = recon.get(key)
        if serie is None or len(serie) != len(rd):
            raise KeyError(key)
        return float(serie[i])

    try:
        contab = at("pnl_eur", L) + at("realized_sales_eur", L) + at("dividend_income_eur", L)
        out["pnl_contabile_eur"] = round(contab, 2)
        out["contabile"] = {"data": rd[L], "non_realizzato_eur": round(at("pnl_eur", L), 2),
                            "realizzato_eur": round(at("realized_sales_eur", L), 2),
                            "dividendi_eur": round(at("dividend_income_eur", L), 2)}
    except KeyError as e:
        out["motivo_nd"] = _message('serie contabile incompleta ({v0}): riconciliazione n.d.',
                                    'Incomplete accounting series ({v0}): reconciliation unavailable.', v0=str(e))
        return out

    last_regime = regimes[-1] if regimes else None
    try:
        if last_regime == "reconstructed":
            # catena tutta ricostruita: stessa fonte; separa date diverse e dividendi
            # maturati (nella catena) ma non ancora incassati (non nel contabile)
            i = rd.index(dates[-1])
            contab_i = at("pnl_eur", i) + at("realized_sales_eur", i) + at("dividend_income_eur", i)
            voce("ultimo_punto", contab_i - contab,
                 'ultima data della catena diversa dall\'ultima chiusura contabile (contabile alla data della catena meno contabile all\'ultima chiusura).',
                 'Chain end date differs from the latest accounting close (accounting at chain date minus accounting at latest close).')
            voce("dividendi_maturati_non_incassati", at("dividendi_maturati_eur", i) - at("dividend_income_eur", i),
                 'dividendi con ex-date passato e incasso non ancora registrato: nella catena come credito, non nel contabile.',
                 'Dividends past the ex-date whose payment is not yet recorded: in the chain as a receivable, not in accounting.')
        elif not seamless:
            out["motivo_nd"] = _message('transizione ricostruita -> ufficiale non sovrapposta (r=0 sul salto): cucitura non misurabile, residuo n.d.',
                                        'Reconstructed -> official transition does not overlap (r=0 on the jump): seam not measurable, residual unavailable.')
            return out
        else:
            s0, sl = snaps[0], snaps[-1]
            if not cucitura or cucitura.get("base_eur") is None or sl.get("invested_eur") is None:
                out["motivo_nd"] = _message('cucitura senza base ricostruita o snapshot senza valore investito: residuo n.d.',
                                            'Seam without reconstructed base or snapshot without invested value: residual unavailable.')
                return out
            k = rd.index(s0["date"])
            invl = float(sl["invested_eur"])
            cash0 = float(cucitura["cassa_snapshot_eur"])
            credito0 = float(cucitura.get("credito_dividendi_eur") or 0.0)
            voce("cucitura_ricostruita_ufficiale",
                 cucitura["base_eur"] - at("nav_eur", k) - cash0 - credito0,
                 'base della catena alla cucitura meno (posizioni ricostruite + cassa dello snapshot + dividendi maturati non incassati): zero quando si usa la base decisa. Scarto snapshot - ricostruzione al primo snapshot, NON contato: {v0} EUR.',
                 'Chain base at the seam minus (reconstructed positions + snapshot cash + accrued unpaid dividends): zero when the decided base is used. Snapshot - reconstruction gap at the first snapshot, NOT counted: {v0} EUR.',
                 v0=cucitura.get("scarto_snapshot_meno_ricostruzione_eur"))
            credito_l = float(values[-1]) - float(sl["nav_total_eur"]) if sl.get("nav_total_eur") is not None else 0.0
            if abs(credito_l) > 0.005:
                voce("dividendi_maturati_non_incassati", credito_l,
                     'dividendi con ex-date passato e incasso non ancora registrato all\'ultimo snapshot: nella catena come credito, non nel contabile.',
                     'Dividends past the ex-date whose payment is not yet recorded at the latest snapshot: in the chain as a receivable, not in accounting.')
            ultimo = invl - at("nav_eur", L)
            soglia = abs(at("nav_eur", L)) * ULTIMO_PUNTO_SOGLIA_PCT / 100.0
            voce("ultimo_punto", ultimo,
                 'posizioni: ultimo snapshot ({v0}) meno ultima chiusura contabile ({v1}) — orari e fonti di prezzo diversi.',
                 'Positions: latest snapshot ({v0}) minus latest accounting close ({v1}) — different times and price sources.',
                 stato=("misurata" if abs(ultimo) <= soglia else "da_verificare"),
                 v0=sl["date"], v1=rd[L])
            # cassa misurata da SOLA: osservata (snapshot) contro attesa (ledger, trade, dividendi)
            osservata = (float(sl["nav_total_eur"]) - invl) - cash0
            versamenti = sum(float(f) for f, r in zip(flows, regimes) if r == "official")
            trade_netti = (at("cost_basis_eur", L) - at("cost_basis_eur", k)) - (
                at("realized_sales_eur", L) - at("realized_sales_eur", k))
            dividendi_inc = at("dividend_income_eur", L) - at("dividend_income_eur", k)
            attesa = versamenti - trade_netti + dividendi_inc
            out["cassa"] = {
                "variazione_osservata_eur": round(osservata, 2),
                "variazione_attesa_eur": round(attesa, 2),
                "versamenti_ledger_eur": round(versamenti, 2),
                "acquisti_meno_vendite_eur": round(trade_netti, 2),
                "dividendi_incassati_eur": round(dividendi_inc, 2),
                "interessi_eur": None,
                "non_spiegata_eur": round(osservata - attesa, 2),
                "nota": _message('variazione della cassa nel tratto ufficiale: osservata negli snapshot contro attesa = versamenti del ledger - (acquisti - vendite al costo storico) + dividendi incassati. Interessi: nessun movimento di interessi nel ledger (n.d.). La parte non spiegata NON e\' una voce: resta nel residuo.',
                                 'Cash change in the official segment: observed in snapshots versus expected = ledger deposits - (purchases - sales at historical cost) + received dividends. Interest: no interest movements in the ledger (n/a). The unexplained part is NOT an item: it stays in the residual.'),
            }
    except (KeyError, ValueError) as e:
        out["motivo_nd"] = _message('data della catena assente dalla serie contabile ({v0}): residuo n.d.',
                                    'Chain date missing from the accounting series ({v0}): residual unavailable.', v0=str(e))
        return out
    residuo = perf_idx - contab - sum(grezzi)
    out["residuo_eur"] = round(residuo, 2)
    if out["cassa"] is not None:
        # controllo incrociato: nel tratto ufficiale il residuo DEVE coincidere con la cassa
        # non spiegata misurata a parte; se no c'e' un errore nelle formule delle voci.
        scarto = residuo - out["cassa"]["non_spiegata_eur"]
        out["controllo_cassa"] = {"scarto_eur": round(scarto, 2),
                                  "stato": "coerente" if abs(scarto) <= 0.05 else "incoerente"}
    out["residuo_stato"] = ("riconciliato_entro_tolleranza" if abs(residuo) <= RESIDUO_TOLLERANZA_EUR
                            else "non_riconciliato")
    out["stato"] = _stato_complessivo(out)
    return out


# ============================================================
# PAYLOAD COMPLETO (per GET /portfolio/analytics/twr)
# ============================================================

def compute_twr_payload(force: bool = False) -> Dict[str, Any]:
    """Payload per la pagina Performance: indice TWR base 100, metriche sulla
    serie TWR, IRR money-weighted, regime per tratto, as_of e riconciliazione
    NAV live vs ultimo snapshot. Cache 10 min."""
    if not force and "payload" in _CACHE:
        entry = _CACHE["payload"]
        if time.time() - entry["ts"] < CACHE_TTL_SEC:
            return render_payload(entry["data"])

    ctx = get_official_series()
    dates = ctx["dates"]
    values = ctx["values_eur"]
    flows = ctx["flows_eur"]
    regimes = ctx["regimes"]
    notes = list(ctx["notes"])
    snaps = ctx["snapshots"]

    if len(dates) < 2:
        return {"error": _message("serie insufficiente (servono >=2 punti): {reason}",
                                  "Insufficient series (at least 2 points required): {reason}",
                                  reason=ctx.get("recon_error") or _message("nessun dato", "no data")),
                "official_since": ctx.get("official_since"),
                "copertura": _performance_coverage(MemoryDB(), ctx),
                "timestamp": datetime.now().isoformat()}

    ultimo_punto_base = _ultimo_punto_prezzi_contabili(ctx, values, regimes) if ULTIMO_PUNTO_PREZZI_CONTABILI else {
        "tipo": "snapshot", "nota": _message('ultimo punto = ultimo snapshot NAV (prezzi del giro price_updater).',
                                             'Last point = latest NAV snapshot (price_updater prices).')}
    snaps_catena = ultimo_punto_base.pop("_snaps", None) or snaps

    # rendimenti per segmento (mai attraverso un cambio di perimetro non sovrapposto)
    seamless = bool(ctx.get("seamless_transition", False))
    cucitura = _seam_base(ctx) if seamless else None
    seam_base = cucitura["base_eur"] if cucitura else None
    if cucitura:
        notes.append(cucitura["nota"])
    rets: List[float] = []
    bases: List[Optional[float]] = []  # V_{t-1} usato per r_t (None = salto a r=0)
    for t in range(1, len(dates)):
        regime_change = regimes[t] != regimes[t - 1]
        if regime_change and not seamless:
            rets.append(0.0)  # giorno di salto dichiarato (nota gia' in ctx)
            bases.append(None)
            continue
        if regime_change and seamless:
            # fix PNL-B (A, decisione PM 06/10): base del primo r ufficiale = posizioni
            # RICOSTRUITE a d0 (chiusure, senza i dividendi gia' contati come cassa nel
            # tratto ricostruito) + cassa dello snapshot d0 (che quei dividendi li
            # contiene gia'). Prima era il NAV dello snapshot: una valutazione diversa
            # (prezzi di un altro orario/fornitore) che la catena non contava mai.
            base = seam_base if seam_base is not None else (
                float(snaps[0]["nav_total_eur"]) if snaps else float(values[t - 1]))
            f = float(flows[t])
            rets.append(((float(values[t]) - base - f) / base) if base > 0 else 0.0)
            bases.append(base if base > 0 else None)
            continue
        rets.append(compute_twr([values[t - 1], values[t]], [0.0, float(flows[t])])[0])
        bases.append(float(values[t - 1]) if float(values[t - 1]) > 0 else None)

    try:
        from bellomberg.market_data.market_inputs import get_risk_free
        rf = float(get_risk_free("EUR") or 0.03)
    except Exception:
        rf = 0.03
    # R-PNL G2: il punto base al costo resta per il rendimento in EUR e il titolo, ma e'
    # escluso dalle statistiche (vol, Sharpe, drawdown qui; i consumatori leggono
    # `indice_statistiche_da` per sortino/alpha/beta/VaR e l'accoppiamento col benchmark)
    salta_stat = 1 if (ctx.get("base") or {}).get("tipo") == "costo" and len(dates) > 2 else 0
    metrics = _twr_metrics(dates, rets, rf, salta_stat)

    db = MemoryDB()
    live = db.get_portfolio_summary()
    irr_flows, terminal, irr_basis = _build_irr_flows(ctx, live)
    today_iso = date.today().isoformat()
    irr_as_of = snaps[-1]["date"] if ctx.get("position_openings") and snaps else today_iso
    irr = compute_irr(irr_flows, terminal, irr_as_of) if irr_flows else None

    # riconciliazione NAV live vs ultimo snapshot ufficiale (helper puro sotto)
    nav_live = float(live.get("nav_total_eur") or 0)
    recon_note = build_recon_note(nav_live, snaps)
    if recon_note and recon_note.get("breach"):
        _log(f"RICONCILIAZIONE NAV FUORI TOLLERANZA: delta {recon_note['delta_pct']:+.2f}% "
             f"(soglia {RECON_TOLERANCE_PCT}%) vs snapshot {recon_note['last_snapshot_date']}")
    if not snaps:
        # fix 30c: distingui tabella ASSENTE (serve lo script) da tabella PRESENTE ma vuota
        # (il primo snapshot arriva da solo a fine giro prezzi, o subito con REFRESH PREZZI).
        table_ok = False
        try:
            _conn = connect_sqlite(getattr(db, "db_path", SQLITE_PATH))  # hardening #32
            try:
                table_ok = _table_exists(_conn, "nav_snapshots")
            finally:
                _conn.close()
        except Exception:
            pass
        if table_ok:
            notes.append(_message('nessuno snapshot NAV ancora (tabella nav_snapshots pronta ma vuota): serie interamente ricostruita da chiusure. In attesa del primo snapshot: si scrive a fine del prossimo giro prezzi (15-30 min) o subito col bottone REFRESH PREZZI.', 'No NAV snapshot yet (nav_snapshots table ready but empty): the series is entirely reconstructed from closing prices. Awaiting the first snapshot at the end of the next price update (15-30 min), or immediately via REFRESH PRICES.'))
        else:
            notes.append(_message('nessuno snapshot NAV ancora: serie interamente ricostruita da chiusure. Lancia tools/migrations/setup_twr_tables.py e riavvia il backend per attivare il regime ufficiale.', 'No NAV snapshot yet: the series is entirely reconstructed from closing prices. Run tools/migrations/setup_twr_tables.py and restart the backend to activate the official regime.'))

    base_info = ctx.get("base")
    if base_info is None:
        base_info = {"tipo": "snapshot" if regimes and regimes[0] == "official" else "chiusura",
                     "data": dates[0], "valore_eur": round(float(values[0]), 2),
                     "nota": _message('base = primo punto della serie (nessun costo iniziale ricostruibile): il periodo precedente NON viene misurato.',
                                      'Base = first point of the series (no reconstructable initial cost): the earlier period is NOT measured.')}
    n_official = sum(1 for r in regimes if r == "official")
    n_recon = len(regimes) - n_official
    payload = {
        "as_of": {
            "computed_at": datetime.now().isoformat(timespec="seconds"),
            "price_basis": _message('official: snapshot NAV (prezzi del giro price_updater); reconstructed: chiusure daily yfinance NON aggiustate per dividendi (split inclusi), dividendi registrati come cassa', 'official: NAV snapshot (prices from the price_updater run); reconstructed: daily yfinance closes NOT adjusted for dividends (splits adjusted), recorded dividends as cash'),
            "fx_basis": _message('official: FX live al momento dello snapshot; reconstructed: FX daily storico (CB al cambio storico)', 'official: live FX at snapshot time; reconstructed: historical daily FX (cost basis at historical FX)'),
        },
        "dates": dates,
        "copertura": _performance_coverage(db, ctx),
        "irr_as_of": irr_as_of,
        "twr_index": metrics["index"],
        "regimes": regimes,
        "values_eur": [round(v, 2) for v in values],
        "flows_eur": flows,
        "regime_summary": {
            "official_since": ctx.get("official_since"),
            "n_official_days": n_official,
            "n_reconstructed_days": n_recon,
            "seamless_transition": seamless,
        },
        # L'ora dell'ultimo snapshot e' esposta anche fra le chiavi top-level:
        # l'indicatore «IN CORSO» legge queste; i pannelli di confronto NAV
        # leggono invece l'oggetto reconciliation.
        # Base oraria LOCALE con la T (record_nav_snapshot usa datetime.now())
        # — NON UTC come cash_movements.created_at. Senza snapshot: null dichiarato.
        "last_snapshot_created_at": (snaps[-1].get("created_at") if snaps else None),
        "metrics": {
            "twr_total_pct": metrics["twr_total_pct"],
            "twr_annualized_pct": metrics["twr_annualized_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "current_drawdown_pct": metrics["current_drawdown_pct"],
            "vol_annual_pct": metrics["vol_annual_pct"],
            "sharpe": metrics["sharpe"],
            "risk_free_used": metrics["risk_free_used"],
            "irr_annual_pct": round(irr * 100.0, 2) if irr is not None else None,
            "irr_basis": irr_basis,
        },
        "external_flows": [
            {"date": m["date"], "type": m["type"], "amount_eur": m["amount_eur"], "note": m.get("note")}
            for m in (ctx.get("ledger") or [])
        ],
        "reconciliation": recon_note,
        # fix PNL-B (06/10): da dove parte il rendimento, dividendi del tratto
        # ricostruito, base alla cucitura e riconciliazione performance/contabile
        "base": dict(base_info, escluso_dalle_statistiche=bool(salta_stat)),
        # primo indice di twr_index/dates da cui partono le STATISTICHE (0 = tutti)
        "indice_statistiche_da": salta_stat,
        "statistiche_nota": (_message('vol, Sharpe, Sortino, alpha, beta, VaR/CVaR e drawdown escludono il punto base al costo ({v0}): il suo primo rendimento (costo -> prima chiusura) contiene guadagni maturati prima dell\'ingresso nel book e non e\' un rendimento giornaliero; rendimento totale e titolo in EUR restano dal costo.',
                                      'Volatility, Sharpe, Sortino, alpha, beta, VaR/CVaR and drawdown exclude the cost base point ({v0}): its first return (cost -> first close) includes gains accrued before entering the book and is not a daily return; total return and EUR headline remain from cost.', v0=dates[0])
                              if salta_stat else None),
        "crediti_dividendi_ufficiali": ctx.get("crediti_dividendi_ufficiali") or None,
        "dividendi_ricostruiti": ctx.get("dividendi_ricostruiti"),
        "cucitura": cucitura,
        "riconciliazione_pnl": build_pnl_reconciliation(
            dates, values, flows, regimes, bases, rets, metrics["index"], snaps_catena,
            ctx.get("recon"), seamless, cucitura),
        "ultimo_punto_base": ultimo_punto_base,
        "notes": notes,
        "methodology": (_message("TWR GIPS r_t=(V_t-V_{t-1}-F_t)/V_{t-1}, flussi a fine giornata (w=0); F = solo flussi esterni (ledger) nel regime official, net-invested-at-cost (dCB - dRealized) nel regime reconstructed pre-ledger, con punto base al costo e dividendi registrati come cassa nel valore. Drawdown/vol/Sharpe calcolati sull'indice TWR. IRR = XIRR money-weighted.", 'TWR GIPS r_t=(V_t-V_{t-1}-F_t)/V_{t-1}, end-of-day flows (w=0); F = external ledger flows only in the official regime, net-invested-at-cost (dCB - dRealized) in the pre-ledger reconstructed regime, with a base point at cost and recorded dividends as cash in value. Drawdown/volatility/Sharpe calculated on the TWR index. IRR = money-weighted XIRR.')),
        "n_days": len(dates),
        "timestamp": datetime.now().isoformat(),
    }
    _CACHE["payload"] = {"ts": time.time(), "data": payload}
    _log(f"payload TWR: {len(dates)} giorni ({n_recon} ricostruiti + {n_official} ufficiali), "
         f"TWR {metrics['twr_total_pct']:+.2f}%, maxDD {metrics['max_drawdown_pct']:.2f}%")
    return render_payload(payload)


def invalidate_cache():
    _CACHE.clear()


if __name__ == "__main__":
    import json
    p = compute_twr_payload(force=True)
    print(json.dumps({k: v for k, v in p.items()
                      if k not in ("dates", "twr_index", "values_eur", "flows_eur", "regimes")},
                     indent=2, ensure_ascii=False, default=str))
