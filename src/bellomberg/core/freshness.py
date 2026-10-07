"""
freshness.py — Rilevatore dati stantii (audit/07 §2-3, P1; regola PM 14/07: MAI
fallback silenziosi, sempre precisione dichiarata).

Il caso che l'ha reso necessario: il funding perp "+10,95%" citato IDENTICO al
centesimo nei memo #36/#39/#42 (fermo da settimane) e il "DXY" 120,69 del #42 che
era un dato FRED di 11 giorni prima presentato come corrente e "in risalita".

Meccanica: a ogni run i valori chiave dei dati esterni vengono confrontati con lo
snapshot della run precedente (data/freshness_snapshot.json — file JSON, NIENTE
tabelle nuove nel DB):
  - valore IDENTICO: non dimostra da solo che la fonte sia ferma
  - osservazione piu' vecchia del limite per la serie -> STALE (dato non corrente)
  - data assente, invalida o futura / valore assente o invalido -> freschezza n.d.
I limiti di osservazione sono per FREQUENZA della serie (una CPI mensile di 30
giorni e' normale, un VIX di 6 giorni no).
Lo snapshot si aggiorna SEMPRE (anche per i dati sani), cosi' il confronto e'
sempre con l'ultima run reale.
"""
import json
import math
import os
from datetime import date
from bellomberg.core.paths import DATA_DIR

SNAP_PATH = str(DATA_DIR / "freshness_snapshot.json")
IDENTICAL_DAYS = 7          # compatibilita': identicita' da sola non prova STALE
_OBS_LIMITS = (             # (keyword nel nome serie, giorni max di osservazione)
    ("gdp", 130),                       # trimestrale
    ("cpi", 45), ("unemployment", 45), ("retail", 45), ("industrial", 45),
    ("housing", 45), ("ism", 45), ("export", 45), ("euribor", 45),
    ("bund", 45), ("gilt", 45), ("jgb", 45), ("discount", 45), ("selic", 45),
    ("call_rate", 45),          # boj_call_rate: mensile OCSE (P1 14/07, ex discount rate)
    ("3m_rate", 45), ("boe", 45), ("fed_funds", 45),  # mensili OCSE/FRED (FEDFUNDS e' mensile)
    ("claims", 12),                     # settimanale
    ("wti", 12),                        # DCOILWTICO: FRED pubblica con ~1 settimana di lag
)
OBS_DEFAULT_DAYS = 6                    # serie giornaliere (VIX, tassi, FX, oil)


def _obs_limit(key: str) -> int:
    k = key.lower()
    for kw, days in _OBS_LIMITS:
        if kw in k:
            return days
    return OBS_DEFAULT_DAYS


def _load() -> dict:
    try:
        with open(SNAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(snap: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SNAP_PATH), exist_ok=True)
        tmp = SNAP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SNAP_PATH)
    except Exception as e:
        print("[FRESHNESS] save failed: " + str(e), flush=True)


def check_and_update(current: dict, today: date = None) -> dict:
    """current: {chiave: {"value": num/str, "obs_date": "YYYY-MM-DD"|None}}.
    Ritorna stale/unknown (liste), fresh e checked; ogni serie conta una volta.
    La freschezza e' misurata dalla data osservata, non dal cambiamento del valore."""
    today = today or date.today()
    snap = _load()
    stale = []
    unknown = []
    fresh = 0
    for key, cur in sorted(current.items()):
        val = cur.get("value")
        if val is None:
            unknown.append(key + ": n.d. - valore assente")
            continue
        try:
            valid_value = not isinstance(val, bool) and math.isfinite(float(val))
        except (TypeError, ValueError, OverflowError):
            valid_value = False
        if not valid_value:
            unknown.append(key + ": n.d. - valore non numerico o non finito")
            continue
        sval = repr(val)
        prev = snap.get(key) or {}
        first_seen = today.isoformat()
        if prev.get("value") == sval and prev.get("first_seen"):
            first_seen = prev["first_seen"]
        od = cur.get("obs_date")
        unknown_reason = None
        stale_reason = None
        if not od:
            unknown_reason = "data osservazione assente"
        else:
            try:
                # Contratto: YYYY-MM-DD completo. Non troncare un valore invalido.
                od_text = str(od)
                parsed = date.fromisoformat(od_text)
                if parsed.isoformat() != od_text:
                    raise ValueError("data non ISO YYYY-MM-DD")
                obs_age = (today - parsed).days
                lim = _obs_limit(key)
                if obs_age < 0:
                    unknown_reason = "data osservazione futura: " + od_text
                elif obs_age > lim:
                    stale_reason = (f"osservazione del {od_text} = {obs_age} giorni fa "
                                    f"(limite {lim} per questa serie)")
            except (ValueError, TypeError):
                unknown_reason = "data osservazione invalida: " + str(od)
        if unknown_reason:
            unknown.append(key + ": n.d. - " + unknown_reason)
        elif stale_reason:
            stale.append(key + ": " + stale_reason)
        else:
            fresh += 1
        snap[key] = {"value": sval, "obs_date": (str(od) if od else None),
                     "first_seen": first_seen, "last_run": today.isoformat()}
    _save(snap)
    return {"stale": stale, "unknown": unknown, "fresh": fresh, "checked": len(current)}


def format_for_memo(report: dict):
    """Blocco markdown appeso al MEMO dal codice (21/07: i 24 STALE del #46 erano
    nel prompt del Capo con obbligo di dichiarazione, ma nel memo non ne compariva
    NESSUNO — la dichiarazione non puo' dipendere dalla disciplina dell'LLM: la
    appende il codice, come linter e validator). Stringa vuota se tutto fresco."""
    if not report:
        return ""
    stale = report.get("stale") or []
    unknown = report.get("unknown") or []
    if not stale and not unknown:
        return ""
    head = "## QUALITA' DATI (freshness check — blocco automatico, appeso dal codice)"
    lines = ["- " + s for s in stale + unknown]
    quality = f", {len(unknown)} n.d." if unknown else ""
    foot = ("*({} serie esterne controllate: {} fresche, {} STALE{}. Le cifre del memo "
            "basate sui dati sopra valgono alla data di osservazione indicata, non a "
            "oggi; se n.d., la freschezza non e' verificabile.)*".format(
                report.get("checked", "?"), report.get("fresh", "?"), len(stale), quality))
    return "\n".join([head] + lines + [foot])


def format_for_capo(report: dict):
    """Blocco qualita' per il Capo. None se tutte le date sono fresche."""
    if not report:
        return None
    problems = (report.get("stale") or []) + (report.get("unknown") or [])
    if not problems:
        return None
    return ("\n\n=== FRESHNESS CHECK: DATI STALE O FRESCHEZZA N.D. ===\n- "
            + "\n- ".join(problems)
            + "\nREGOLA (PM): questi dati non sono verificati come correnti. Nel memo si usano SOLO "
              "dichiarando la data/eta' di osservazione o la freschezza n.d.; VIETATO presentarli come "
              "ricerca corrente o descriverne la 'direzione' (es. 'in risalita') "
              "sulla base di un valore fermo.")
