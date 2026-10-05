"""
action_validator.py — ACTION VALIDATOR #191 (v1, SOLO FLAG — scelta PM 13/07).

Valida ogni riga della ACTION TABLE del Capo PRIMA della generazione PDF e
produce un blocco markdown di avvertimenti da appendere al memo. NON tocca i
numeri del Capo (niente clip): il PM resta l'unico che decide.

Controlli per riga:
  1. SIZING — BUY/ADD oltre lo spazio residuo del sizing engine (posizioni
     esistenti: remaining_capacity_eur; nomi nuovi: limite base per nome).
  2. RIPROPOSTE — stessa azione sullo stesso ticker già proposta in memo
     precedenti e mai eseguita (status PENDING nel DB): il contatore rende
     visibile il riciclo (audit 13/07: BSX proposta 4 volte come "nuova").
  3. IGIENE — azione non standard o ticker assente dal book su TRIM/SELL.
  4. FEEDBACK PM — sul ticker esiste un feedback diretto del PM su decisioni
     passate (21/07, lezione memo #46: il veto "basta proporre covered call" su
     MSTR #186 era SKIPPED, non PENDING, e il check 2 non lo vedeva). Passata
     separata su TUTTE le righe (HOLD comprese), una voce per ticker, ultimi 2
     feedback. Il codice non interpreta il testo: lo CITA, e il PM giudica.

Il parser della tabella è una copia self-contained di quello di
memory_db.extract_and_save_decisions (stesso precedente di fix_decisions_eur.py):
se cambia il formato della ACTION TABLE vanno aggiornati entrambi.
"""
import json
import math
import os
import re
from datetime import datetime, timedelta

# 21/08: la policy sulle parole del PM sta in UN posto solo. L'import e' QUI e non
# dentro la funzione perche' il blocco FEEDBACK PM e' avvolto da un
# `except Exception: pass`: un import fallito la' dentro avrebbe fatto sparire in
# silenzio TUTTI gli avvertimenti sui feedback del PM dal memo. Nessuna
# circolarita': memory_db non importa action_validator (verificato).
from bellomberg.storage.memory_db import pm_verbatim
from bellomberg.core.language import (ACTION_TABLE_HEADERS, POLICY_OVERRIDE_MARKERS,
                                      NEW_FACT_MARKERS, text, scoped_language)

ACTIONS_KNOWN = {"BUY", "ADD", "SELL", "TRIM", "HOLD", "RESEARCH", "HEDGE", "WATCH"}
ACTIONS_SKIP_CHECKS = {"HOLD"}          # ripetere HOLD su un book statico e' fisiologico
# 04/10 (G6): la soglia sotto cui lo sforo di sizing non fa rumore NON sta piu' qui (era
# SIZING_TOLERANCE_EUR = 500, valore personale in un repo pubblico): e' il campo
# `tolleranza_sforo_sizing_pct` del mandato, in % dell'investito (mandato_pm.tolleranza_sizing_eur).


def _parse_action_rows(memo_markdown):
    """Le righe della ACTION TABLE lette dal parser UNICO (action_table_extract).

    04/10 (G6, review 04 G1/G5): qui viveva una copia propria che non toglieva il
    Markdown (`**BUY**` restava «**BUY**» e passava il gate su un ticker in BLOCK) e
    contava le righe in modo diverso dagli altri tre parser. Ora la riga N e' la
    stessa riga per validator, gate, proiezione e registro decisioni."""
    from bellomberg.storage.memory_db import _parse_eur_amount
    from bellomberg.agents.action_table_extract import parse_action_table_rows
    parsed = parse_action_table_rows(memo_markdown or "")
    rows = []
    for row in parsed.get("rows") or []:
        rows.append({"row_index": row["row_index"],
                     "action": row["action"],
                     "ticker": str(row.get("ticker") or "").upper(),
                     "action_cell": row.get("action_cell", row["action"]),
                     "ticker_cell": row.get("ticker_cell", ""),
                     "size_raw": row.get("size_raw", ""),
                     "eur": _parse_eur_amount(row.get("size_raw", "")),
                     # banda con deroga (15/07): serve la riga intera per vedere
                     # se il Capo ha dichiarato 'SOPRA POLICY' nella motivazione
                     "raw": row.get("raw", "")})
    return rows


def _override_rationale(raw):
    """Return the text attached to an explicit, motivated policy override tag.

    A tag by itself is not a committee rationale. Read only the remainder of
    the cell containing the tag so a final Confidence cell cannot accidentally
    become the justification.
    """
    markers = sorted(POLICY_OVERRIDE_MARKERS, key=len, reverse=True)
    if not markers:
        return None
    pattern = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(x) for x in markers)
                         + r")(?![A-Z0-9])", re.IGNORECASE)
    for cell in str(raw or "").split("|"):
        match = pattern.search(cell)
        if not match:
            continue
        rationale = cell[match.end():].strip()
        rationale = re.sub(r"^[\s:;,.\-–—]+", "", rationale)
        rationale = re.sub(r"[*_`]+", "", rationale).strip()
        if rationale and re.search(r"[A-Za-zÀ-ÿ0-9]", rationale):
            return rationale
    return None


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _ticker_symbol(value):
    """Extract the leading security symbol from a formatted ticker cell.

    04/10 (G6, REV R5): lo stesso estrattore del parser unico (ammette `=` dei future,
    es. `ZZG=F`): prima qui `=` troncava il simbolo e sanity/natura cercavano `ZZG`."""
    from bellomberg.agents.action_table_extract import _ticker_from_cell
    return _ticker_from_cell(value).upper()


def _normal_ticker(value):
    return _ticker_symbol(value)


def _sanity_map(sanity_exclusions):
    """Normalize supplied sidecar results; BLOCK dominates, missing fails closed."""
    if sanity_exclusions is None:
        return None
    if not isinstance(sanity_exclusions, (list, tuple)):
        return {}
    grouped = {}
    for item in sanity_exclusions:
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        ticker = _normal_ticker(item.get("ticker"))
        if not ticker:
            continue
        grouped.setdefault(ticker, []).append(item)
    results = {}
    for ticker, records in grouped.items():
        block = next((r for r in records if str(r.get("severity") or "").upper() == "BLOCK"), None)
        if block:
            results[ticker] = block
        elif all(str(r.get("severity") or "").upper() in {"OK", "WARN"} for r in records):
            # WARN is a valid completed check, but preserve its detail if present.
            results[ticker] = next((r for r in records if str(r.get("severity")).upper() == "WARN"), records[0])
        else:
            results[ticker] = next((r for r in records if str(r.get("severity") or "").upper()
                                    not in {"OK", "WARN", "BLOCK"}), records[0])
    return results


def _sanity_reason(record):
    parts = []
    headline = str(record.get("headline") or "").strip()
    detail = str(record.get("reason") or "").strip()
    judged_at = str(record.get("judged_at") or "").strip()
    if headline:
        parts.append(headline)
    if detail and detail != headline:
        parts.append(detail)
    if judged_at:
        parts.append("giudizio del " + judged_at)
    return "; ".join(parts) or "sidecar di sanity con severity BLOCK"


def assess_action_table(memo_markdown, sizing=None, *, sanity_exclusions=None):
    """Esito di pubblicazione di ogni riga della ACTION TABLE (funzione pura).

    04/10 (G6, decisioni PM A): il gate blocca SOLO cio' che non si puo' eseguire
    o verificare; il SIZING non blocca mai (resta avviso nel memo, regole di
    build_validator_block: tolleranza del mandato, deroga 'SOPRA POLICY',
    rotazioni e capacita' aggregata come prima del gate). `sizing` resta nella
    firma per i chiamanti ma non decide lo stato.

    - riga senza azione o ticker leggibili, azione fuori dall'elenco chiuso:
      CHECK_UNAVAILABLE (mai operativa per difetto di lettura);
    - TRIM/SELL/HOLD/RESEARCH/HEDGE/WATCH: OPERATIVE, gate non applicabile;
    - BUY/ADD: sanity BLOCK -> BLOCKED; importo illeggibile o non positivo ->
      CHECK_UNAVAILABLE; sidecar assente su uno strumento che per natura non
      ha un modello (NATURE_SENZA_VALUTAZIONE) -> OPERATIVE con
      `senza_valutazione`; sidecar assente su altro, illeggibile o severity
      ignota -> CHECK_UNAVAILABLE; sanity OK/WARN -> OPERATIVE.

    `sanity_exclusions`: un record per ticker BUY/ADD (`collect_sanity_exclusions`);
    None = controllo non fornito, fail-closed sulle BUY/ADD.
    """
    rows = _parse_action_rows(memo_markdown or "")
    sanity_by_ticker = _sanity_map(sanity_exclusions)
    results = []
    for row in rows:
        action = row["action"]
        ticker = row["ticker"]
        amount = _finite_number(row.get("eur"))
        assessment = {
            "row_index": row["row_index"],
            "action": action,
            "ticker": row.get("ticker_cell") or ticker,
            "eur": amount,
            "status": "OPERATIVE",
            "reason": "controllo rischio non applicabile a questa azione",
            "override_rationale": _override_rationale(row.get("raw")),
            "senza_valutazione": False,
        }
        results.append(assessment)
        if not action or not ticker:
            assessment.update(status="CHECK_UNAVAILABLE",
                              reason="riga ACTION TABLE illeggibile: azione o ticker assente")
            continue
        if action not in ACTIONS_KNOWN:
            assessment.update(status="CHECK_UNAVAILABLE",
                              reason="azione '" + str(row.get("action_cell") or action)
                              + "' fuori dall'elenco ammesso (" + ", ".join(sorted(ACTIONS_KNOWN)) + ")")
            continue
        if action not in ("BUY", "ADD"):
            continue
        # The sanity block is a hard gate and must precede the amount check.
        sanity_record = (sanity_by_ticker or {}).get(_normal_ticker(ticker))
        severity = str((sanity_record or {}).get("severity") or "").upper()
        if severity == "BLOCK":
            assessment.update(status="BLOCKED", reason="sanity BLOCK: " + _sanity_reason(sanity_record))
            continue
        if amount is None or amount <= 0:
            assessment.update(status="CHECK_UNAVAILABLE",
                              reason="importo BUY/ADD illeggibile o non positivo: '" + str(row.get("size_raw") or "") + "'")
            continue
        if sanity_by_ticker is None:
            assessment.update(status="CHECK_UNAVAILABLE", reason="controllo sanity non fornito")
            continue
        if severity in {"OK", "WARN"}:
            assessment["reason"] = "sanity " + severity + " superata; sizing: vedi avvisi ACTION VALIDATOR"
            continue
        record = sanity_record or {}
        if (sanity_record is not None and not record.get("sidecar")
                and record.get("natura") in NATURE_SENZA_VALUTAZIONE):
            assessment.update(
                senza_valutazione=True,
                reason="SENZA VALUTAZIONE: strumento " + str(record["natura"]).upper()
                       + " senza modello di valutazione (nessun controllo sanity possibile)")
            continue
        detail = str(record.get("reason") or "sidecar assente o illeggibile")
        assessment.update(status="CHECK_UNAVAILABLE", reason="controllo sanity non disponibile: " + detail)
    return results


def collect_sanity_exclusions(memo_markdown, report_dir=None, negozio=None):
    """Read local sanity sidecars and return one evidence record per BUY/ADD ticker.

    The collector never contacts a provider. It returns severity=None for a
    missing, unreadable, or malformed sidecar so the pure assessor can fail
    closed; a missing sidecar also carries the instrument `natura` from the
    vehicle store (`negozio`, loaded when omitted). When `report_dir` is
    omitted it uses the runtime `core.paths.REPORT_DIR`.
    """
    candidates = []
    try:
        for row in _parse_action_rows(memo_markdown or ""):
            if row["action"] in ("BUY", "ADD") and row["ticker"]:
                ticker = _normal_ticker(row["ticker"])
                if ticker not in candidates:
                    candidates.append(ticker)
    except Exception:
        return None
    if negozio is None and candidates:
        from bellomberg.storage.classificazione import carica_veicoli
        negozio = carica_veicoli()
    # 04/10 (G6, review 04 G6): la severity si legge con la policy CORRENTE
    # (refresh_price_comparison, stessa lettura di _canonical_sanity) e dal REPORT_DIR
    # di runtime, non da un risolutore di percorso parallelo.
    return [_sanity_record(ticker, report_dir, negozio) for ticker in candidates]


# Nature che per costruzione non hanno un modello di valutazione con sidecar VAL (ETF, ETN,
# crypto, materie prime, fondi chiusi, holding, DAT): un BUY/ADD su di loro resta operativo
# con l'etichetta «senza valutazione» (decisione PM 04/10). Societa' operative e banche il
# modello lo possono avere: senza sidecar la proposta resta non verificabile.
NATURE_SENZA_VALUTAZIONE = frozenset({"etf", "etn", "crypto", "commodity", "cef", "holding", "dat"})


def _sanity_record(ticker, report_dir=None, negozio=None):
    """Una evidenza sanity per ticker, letta come la legge `_canonical_sanity`.

    Sidecar assente: `severity` None e `natura` dal negozio dei veicoli, cosi'
    l'assessore distingue lo strumento che una valutazione non la puo' avere da
    quello a cui manca. Sidecar illeggibile: `severity` None col motivo."""
    record = {"ticker": ticker, "severity": None, "headline": None, "judged_at": None,
              "sidecar": False, "natura": None,
              "reason": "sidecar sanity assente"}
    found = _sanity_payload(ticker, report_dir)
    if found is None:
        try:
            from bellomberg.storage import classificazione
            label = classificazione.natura(ticker, negozio)
            record["natura"] = label.valore
            record["reason"] = ("sidecar sanity assente; natura dello strumento " + label.valore
                                if label.valore else
                                "sidecar sanity assente; natura dello strumento non dichiarata nel negozio dei veicoli")
        except Exception as exc:
            record["reason"] = "sidecar sanity assente; natura non leggibile (" + type(exc).__name__ + ")"
        return record
    record["sidecar"] = True
    payload, error = found
    if error:
        record["reason"] = "sidecar sanity illeggibile: " + error
        return record
    sanity = payload.get("sanity") if isinstance(payload.get("sanity"), dict) else {}
    headline = sanity.get("headline") or payload.get("sanity_headline")
    detail = sanity.get("reason") or payload.get("error")
    record.update(severity=_policy_severity(payload),
                  headline=str(headline).strip() if headline else None,
                  reason=str(detail).strip() if detail else None,
                  judged_at=str(payload.get("_timestamp") or "")[:10] or None)
    return record


def _sanity_payload(ticker, report_dir=None):
    """(payload, None) | (None, motivo) per il sidecar del ticker; None se non esiste.
    I due nomi sono mutuamente esclusivi per costruzione (dcf_engine)."""
    from bellomberg.core.paths import REPORT_DIR
    d = str(report_dir or REPORT_DIR)
    base = "VAL_" + str(ticker).replace(".", "_")
    for name in (base + "_FLAGGED.payload.json", base + ".payload.json"):
        p = os.path.join(d, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError, TypeError) as exc:
            return None, name + ": " + type(exc).__name__
        if not isinstance(payload, dict):
            return None, name + ": contenuto non valido"
        return payload, None
    return None


def _policy_severity(payload):
    """Severity del sidecar con la policy corrente sui vecchi scarti FV/prezzo
    dimostrati; un BLOCK con un altro guasto registrato resta BLOCK."""
    from bellomberg.valuation.price_comparison import refresh_price_comparison
    original_severity = ((payload.get("sanity") or {}).get("severity") or "").upper()
    payload = refresh_price_comparison(payload)
    sev = ((payload.get("sanity") or {}).get("severity") or "").upper() or None
    if original_severity == "BLOCK" and (payload.get("error")
            or payload.get("valuation_flagged") is True
            or payload.get("exclude_from_action_table") is True):
        # A proven comparison does not waive another recorded failure.
        sev = "BLOCK"
    return sev


def _ddmm(iso):
    s = str(iso or "")
    return (s[8:10] + "/" + s[5:7]) if len(s) >= 10 else "?"


@scoped_language
def build_validator_block(memo_markdown, sizing, db, exclude_memo_id=None, *, sanity_exclusions=None,
                          mandato=None):
    """Ritorna il blocco markdown '## ACTION VALIDATOR' (o stringa vuota se pulito).
    Non solleva mai: ogni check e' guarded, un guasto del validator non deve
    toccare la run (il chiamante ha comunque il suo try/except).
    `mandato`: il mandato della run (porta la tolleranza di sforo del sizing); None =
    letto ora dal disco. Senza tolleranza dichiarata il confronto col sizing degli
    acquisti NON si fa e il blocco lo dice (mai una soglia di ripiego)."""
    rows = _parse_action_rows(memo_markdown)
    if not rows:
        return ""

    warnings = []
    controlli_db_falliti = set()
    risk_gate_warnings = []

    # Public operational view of the same pure assessment used by publication.
    # Keep the historical diagnostics below for continuity, while making hard
    # blocks, pending overrides, and unavailable checks explicit in the memo.
    risk_assessments_ok = False
    try:
        resolved_sanity = (collect_sanity_exclusions(memo_markdown)
                           if sanity_exclusions is None else sanity_exclusions)
        risk_assessments = assess_action_table(
            memo_markdown, sizing, sanity_exclusions=resolved_sanity)
        for assessment in risk_assessments:
            status = assessment["status"]
            if status == "OPERATIVE" and assessment.get("senza_valutazione"):
                # decisione PM 04/10: operativa, ma l'etichetta si vede nel memo
                risk_gate_warnings.append(text("**SENZA VALUTAZIONE** {} {}: operativa senza controllo sanity — {}",
                                               "**NO VALUATION** {} {}: operative without a sanity check — {}").format(
                    assessment.get("action") or "", assessment.get("ticker") or "",
                    assessment.get("reason") or ""))
                continue
            if status == "OPERATIVE":
                continue
            prefix = {
                "BLOCKED": "**RISK GATE BLOCCATO**",
                "OVERRIDE_PENDING": "**RISK GATE — DEROGA IN ATTESA**",
                "CHECK_UNAVAILABLE": "**RISK GATE NON VERIFICABILE**",
            }.get(status, "**RISK GATE**")
            risk_gate_warnings.append("{} {} {}: {}€".format(
                prefix, assessment.get("action") or "", assessment.get("ticker") or "",
                f"{assessment['eur']:,.0f}" if assessment.get("eur") is not None else "n.d.")
                + " — " + str(assessment.get("reason") or "esito non disponibile"))
        risk_assessments_ok = True
    except Exception as exc:
        risk_gate_warnings.append(text("**RISK GATE NON VERIFICABILE** — il controllo non ha prodotto un esito (%s: %s)",
                                       "**RISK GATE CHECK UNAVAILABLE** — the check produced no result (%s: %s)")
                                 % (type(exc).__name__, str(exc)[:120]))

    # --- contesto sizing (posizioni esistenti + limite base per nomi nuovi) ---
    sz_pos, invested, base_single_pct = {}, 0.0, None
    if isinstance(sizing, dict) and not sizing.get("error"):
        for p in (sizing.get("positions") or []):
            if p.get("ticker"):
                sz_pos[str(p["ticker"]).upper()] = p
        summ = sizing.get("summary") or {}
        invested = float(summ.get("invested_capital_eur") or 0.0)
        base_single_pct = (summ.get("params") or {}).get("single_base_pct")

    # 06/09 (lotto B, difetto 1 della coda di A2, assegnato dal PM): un cancello che non ha
    # POTUTO controllare deve dirlo. Da quando i tetti vengono dal mandato (A2), un mandato
    # assente fa tornare al motore `{"error": ...}`: fino a oggi questo ramo lasciava
    # `sz_pos` vuoto e `base_single_pct` a None, i controlli 1 e 3b non avevano su cosa
    # girare, e piu' sotto `if not warnings: return ""` faceva uscire il memo PULITO.
    # «Non ho guardato» non e' «non c'e' niente da dire» (regola PM 14/07).
    causa_sizing = None
    if sizing is None:
        causa_sizing = text("il motore di sizing non ha prodotto nulla", "the sizing engine produced no result")
    elif not isinstance(sizing, dict):
        causa_sizing = text("contesto di sizing inatteso (%s)", "unexpected sizing context (%s)") % type(sizing).__name__
    elif sizing.get("error"):
        causa_sizing = str(sizing.get("error"))

    # 04/10 (G6, decisione PM B): la tolleranza viene dal mandato in % del capitale
    # investito del sizing (la base dei limiti per nome che lo sforo supera).
    tol, causa_tolleranza = None, None
    if causa_sizing is None:
        try:
            from bellomberg.core import mandato_pm
            _m = mandato if mandato is not None else mandato_pm.carica()
            tol, causa_tolleranza = mandato_pm.tolleranza_sizing_eur(
                _m, _finite_number((sizing.get("summary") or {}).get("invested_capital_eur")))
        except Exception as e:
            causa_tolleranza = text("mandato non leggibile (%s: %s)", "mandate unreadable (%s: %s)") % (
                type(e).__name__, str(e)[:120])

    _compere = [r for r in rows if r["action"] in ("BUY", "ADD") and r["ticker"] and r["eur"]]
    _vendite = [r for r in rows if r["action"] in ("TRIM", "SELL") and r["ticker"]]

    # Audit 11/09 (Fable 5.1): con il budget di stress SFORATO e capacita' dispiegabile 0
    # il Capo poteva impiegare cash lo stesso. Sui nomi ESISTENTI il
    # flag c'era ma diceva «~0€ — spazio +X€ entro il limite» (due numeri che si
    # contraddicono nella stessa riga: il primo e' dopo il budget, il secondo prima); sul
    # nome NUOVO non c'era NESSUN flag: il confronto usava solo il 10% base per nome. Ora
    # il budget che VINCOLA e' un controllo suo, con la stessa deroga 'SOPRA POLICY'.
    _bud = {}
    _budget_vincola = False
    _bud_txt = ""
    if isinstance(sizing, dict) and not sizing.get("error") and tol is not None:
        _summ = sizing.get("summary") or {}
        _bud = _summ.get("stress_var_budget") if isinstance(_summ.get("stress_var_budget"), dict) else {}
        # la capacita' che il budget lascia (review 11/09: e' `additional_capacity_eur` del
        # budget, non il cash dispiegabile — con poco cash e budget libero il limite sarebbe
        # il cash, non il budget; senza il campo si ripiega sul dispiegabile, dichiarato)
        _cap_raw = _bud.get("additional_capacity_eur")
        if _cap_raw is None:
            _cap_raw = _summ.get("deployable_from_cash_eur")
        try:
            _disp = float(_cap_raw or 0.0)
        except (TypeError, ValueError):
            _disp = 0.0
        _budget_vincola = bool(_bud.get("binding")) and _disp <= tol
        # aggregato: la somma dei BUY/ADD contro la capacita' residua del budget, anche
        # quando il budget vincola ma non azzera (review 11/09, predicato 2a)
        try:
            _somma_compere = sum(float(r["eur"]) for r in _compere)
        except (TypeError, ValueError):
            _somma_compere = 0.0
        if (bool(_bud.get("binding")) and _bud.get("additional_capacity_eur") is not None
                and not _budget_vincola and _somma_compere > _disp + tol):
            warnings.append(
                text("**BUY/ADD in totale {:,.0f}€** contro una capacita' residua del budget di stress "
                "di ~{:,.0f}€ (budget VINCOLANTE, stato {}): la somma delle aggiunte supera cio' "
                "che il budget lascia", "**Total BUY/ADD {:,.0f}€** against remaining stress-budget capacity "
                "of ~{:,.0f}€ (BINDING budget, status {}): combined additions exceed the remaining budget"
                ).format(_somma_compere, _disp, _bud.get("gfc_status") or "n.d."))
        if _budget_vincola:
            _gfc = ""
            if _bud.get("gfc_replay_nav_pct") is not None and _bud.get("budget_gfc_replay_nav_pct") is not None:
                try:
                    _gfc = text(" replay GFC {:+.1f}% del NAV vs budget {:+.0f}%",
                                " GFC replay {:+.1f}% of NAV vs budget {:+.0f}%").format(
                        float(_bud["gfc_replay_nav_pct"]), float(_bud["budget_gfc_replay_nav_pct"]))
                except (TypeError, ValueError):
                    _gfc = ""
            _bud_txt = text("il budget di stress VINCOLA il sizing (capacita' dispiegabile ~{:,.0f}€, "
                           "stato {}{})", "the stress budget BINDS sizing (deployable capacity ~{:,.0f}€, "
                           "status {}{})").format(_disp, _bud.get("gfc_status") or "n.d.", _gfc)
    if causa_sizing:
        # Si dichiara il LAVORO non fatto, non il dato mancante: senza righe da controllare
        # non c'e' nessun controllo saltato, e un avviso su ogni memo smetterebbe di dire
        # qualcosa.
        _quali = []
        if _compere:
            _quali.append(text("gli acquisti (BUY/ADD) NON sono stati confrontati con i limiti di sizing",
                               "purchases (BUY/ADD) were NOT checked against sizing limits"))
        if _vendite:
            _quali.append(text("le vendite (TRIM/SELL) NON sono state confrontate col book del sizing",
                               "sales (TRIM/SELL) were NOT checked against the sizing book"))
        if _quali:
            warnings.append(
                text("**CONTROLLO SIZING NON ESEGUITO** — ", "**SIZING CHECK NOT PERFORMED** — ")
                + "; ".join(_quali) + text(". Causa: {}. Le righe qui sotto non sono passate dalla policy "
                "di sizing: vanno verificate a mano prima di eseguirle", ". Cause: {}. The rows below have not "
                "passed sizing-policy checks: verify them manually before execution").format(causa_sizing))
    elif causa_tolleranza and _compere:
        warnings.append(
            text("**CONTROLLO SIZING NON ESEGUITO** — gli acquisti (BUY/ADD) NON sono stati confrontati con i "
                 "limiti di sizing. Causa: {}. Verificali a mano prima di eseguirli",
                 "**SIZING CHECK NOT PERFORMED** — purchases (BUY/ADD) were NOT checked against sizing limits. "
                 "Cause: {}. Verify them manually before execution").format(causa_tolleranza))
    elif _compere and not base_single_pct:
        _nuovi = sorted({r["ticker"] for r in _compere if r["ticker"] not in sz_pos})
        if _nuovi:
            warnings.append(
                text("**CONTROLLO SIZING NON ESEGUITO sui nomi nuovi** ({}): il "
                "sizing non porta il limite base per nome (single_base_pct), quindi non c'e' "
                "niente con cui confrontarli", "**SIZING CHECK NOT PERFORMED for new names** ({}): "
                "sizing provides no base per-name limit (single_base_pct), so no comparison is possible"
                ).format(', '.join(_nuovi)))

    for r in rows:
        act, tk, eur = r["action"], r["ticker"], r["eur"]
        if not tk or act in ACTIONS_SKIP_CHECKS:
            continue

        # 3a. azione non standard: dal 04/10 (G6, REV R7) la dichiara gia' la riga RISK GATE
        # NON VERIFICABILE del gate; qui resta solo se il gate non ha prodotto esiti
        if act not in ACTIONS_KNOWN and not risk_assessments_ok:
            warnings.append(text("**{}**: azione '{}' non standard (il parser potrebbe interpretarla male)",
                                 "**{}**: non-standard action '{}' (the parser may misinterpret it)").format(tk, act))

        # 1. sizing (solo BUY/ADD con importo) — banda con deroga dichiarata (15/07,
        # scelta PM): sopra policy CON tag 'SOPRA POLICY' in riga = nota informativa
        # (deroga consapevole del comitato); sopra policy SENZA tag = violazione flaggata.
        if act in ("BUY", "ADD") and eur and tol is not None:
            # regola PM 15/07 (com'era prima del gate, decisione PM 04/10): il tag
            # 'SOPRA POLICY' in riga basta a dichiarare la deroga
            declared = any(tag in (r.get("raw") or "").upper() for tag in POLICY_OVERRIDE_MARKERS)
            if tk in sz_pos:
                room = float(sz_pos[tk].get("remaining_capacity_eur") or 0.0)
                if eur > room + tol:
                    if _budget_vincola and room <= tol:
                        # audit 11/09: lo spazio per nome PRIMA del budget resta visibile,
                        # ma come tale — non accanto a «~0€» senza dire perche'
                        _base = text("**{} {} {:,.0f}€**: {}; policy per nome prima del budget: {}",
                                     "**{} {} {:,.0f}€**: {}; per-name policy before the budget: {}"
                                     ).format(act, tk, eur, _bud_txt, sz_pos[tk].get('verdict', ''))
                        warnings.append(
                            _base + (text(" — DEROGA DICHIARATA: valuta la motivazione del comitato",
                                          " — DECLARED OVERRIDE: assess the committee's rationale")
                                     if declared else
                                     text(" — deroga NON dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                          " — override NOT declared (missing '[OVER-POLICY]' and rationale in the row)")))
                    elif declared:
                        warnings.append(
                            text("**{} {} {:,.0f}€**: DEROGA DICHIARATA sopra la policy "
                                 "(spazio policy ~{:,.0f}€) — valuta la motivazione del comitato",
                                 "**{} {} {:,.0f}€**: DECLARED OVERRIDE above policy "
                                 "(policy capacity ~{:,.0f}€) — assess the committee's rationale"
                                 ).format(act, tk, eur, room))
                    else:
                        warnings.append(
                            text("**{} {} {:,.0f}€**: oltre la policy di sizing (~{:,.0f}€ — {}) e deroga NON "
                                 "dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                 "**{} {} {:,.0f}€**: above sizing policy (~{:,.0f}€ — {}) and override NOT "
                                 "declared (missing '[OVER-POLICY]' and rationale in the row)"
                                 ).format(act, tk, eur, room, sz_pos[tk].get('verdict', '')))
            elif _budget_vincola:
                # nome NUOVO col budget che vincola: la capacita' dispiegabile e' zero per
                # TUTTI, il 10% base per nome non e' il limite che conta (audit 11/09)
                warnings.append(
                    text("**{} {} {:,.0f}€** (nome nuovo): {}", "**{} {} {:,.0f}€** (new name): {}"
                         ).format(act, tk, eur, _bud_txt)
                    + (text(" — DEROGA DICHIARATA: valuta la motivazione del comitato",
                            " — DECLARED OVERRIDE: assess the committee's rationale") if declared
                       else text(" e deroga NON dichiarata (manca 'SOPRA POLICY' + motivazione in riga)",
                                 " and override NOT declared (missing '[OVER-POLICY]' and rationale in the row)")))
            elif invested > 0 and base_single_pct:
                max_new = invested * float(base_single_pct) / 100.0
                if eur > max_new + tol:
                    if declared:
                        warnings.append(
                            text("**{} {} {:,.0f}€** (nome nuovo): DEROGA DICHIARATA sopra "
                                 "la policy base (~{:,.0f}€ = {:.0f}% dell'investito) — valuta la motivazione del comitato",
                                 "**{} {} {:,.0f}€** (new name): DECLARED OVERRIDE above base policy "
                                 "(~{:,.0f}€ = {:.0f}% of invested capital) — assess the committee's rationale"
                                 ).format(act, tk, eur, max_new, base_single_pct))
                    else:
                        warnings.append(
                            text("**{} {} {:,.0f}€** (nome nuovo): sopra la policy base per "
                                 "nome (~{:,.0f}€ = {:.0f}% dell'investito, prima degli aggiustamenti "
                                 "vol/correlazione) e deroga NON dichiarata",
                                 "**{} {} {:,.0f}€** (new name): above base per-name policy "
                                 "(~{:,.0f}€ = {:.0f}% of invested capital, before volatility/correlation "
                                 "adjustments) and override NOT declared").format(act, tk, eur, max_new, base_single_pct))

        # 3b. TRIM/SELL su ticker non in book (per il sizing engine)
        if act in ("TRIM", "SELL") and sz_pos and tk not in sz_pos:
            warnings.append(text("**{} {}**: ticker non presente tra le posizioni del sizing engine",
                                 "**{} {}**: ticker is absent from the sizing engine's positions").format(act, tk))

        # 2. riproposte mai eseguite (SELECT read-only sul DB decisioni)
        if act not in ("HOLD",) and db is not None:
            try:
                same = {"BUY": ("BUY", "ADD"), "ADD": ("BUY", "ADD"),
                        "SELL": ("SELL", "TRIM"), "TRIM": ("SELL", "TRIM")}.get(act, (act,))
                ph = ",".join("?" * len(same))
                q = (f"SELECT COUNT(id), MIN(memo_id), MIN(timestamp) FROM decisions "
                     f"WHERE UPPER(ticker)=? AND UPPER(action) IN ({ph}) AND status='PENDING'")
                params = [tk, *same]
                if exclude_memo_id is not None:
                    q += " AND memo_id != ?"
                    params.append(exclude_memo_id)
                with db._conn() as conn:
                    row = conn.execute(q, params).fetchone()
                n_prev = int(row[0] or 0)
                if n_prev >= 1:
                    volte = text("1 volta", "once") if n_prev == 1 else text("{} volte", "{} times").format(n_prev)
                    warnings.append(
                        text("**{} {}**: già proposta {} senza esecuzione "
                             "(prima nel memo #{} del {}, status PENDING) — "
                             "eseguirla, archiviarla o spiegare perché viene riproposta",
                             "**{} {}**: already proposed {} without execution "
                             "(first in memo #{} dated {}, status PENDING) — "
                             "execute, archive, or explain why it is being proposed again"
                             ).format(act, tk, volte, row[1], _ddmm(row[2])))
            except Exception as e:
                if "riproposte" not in controlli_db_falliti:
                    controlli_db_falliti.add("riproposte")
                    warnings.append(text("**CONTROLLO RIPROPOSTE NON ESEGUITO** — registro decisioni "
                                         "non leggibile (%s: %s)", "**REPEATED-PROPOSAL CHECK NOT PERFORMED** — "
                                         "decision register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

        # 5. RIPROPOSTA POST-ESECUZIONE (audit 11/09, Fable 5.1): il PM esegue un ADD e
        # una run successiva ripropone ADD sullo stesso titolo, stesso
        # verso, come se fosse nuovo. Il check 2 vede solo le PENDING. Qui: un
        # trade dello stesso verso negli ultimi 7 giorni senza un fatto NUOVO dichiarato in
        # riga (tag 'NOVITA':' nella colonna Timing, come 'SOPRA POLICY') = doppione flaggato.
        # Flag-only: nessun blocco sul titolo, la novita' dichiarata lo spegne.
        if act in ("BUY", "ADD", "TRIM", "SELL") and db is not None and tk:
            try:
                import unicodedata as _ud
                _raw_ascii = _ud.normalize("NFKD", str(r.get("raw") or "")).encode(
                    "ascii", "ignore").decode("ascii").upper()
                _novita = any(tag in _raw_ascii for tag in NEW_FACT_MARKERS)
                verso = ("BUY", "ADD") if act in ("BUY", "ADD") else ("TRIM", "SELL")
                ph = ",".join("?" * len(verso))
                cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
                with db._conn() as conn:
                    tr = conn.execute(
                        f"SELECT quantita, prezzo, valuta, data FROM trade_history "
                        f"WHERE UPPER(ticker)=? AND UPPER(action) IN ({ph}) AND data >= ? "
                        "ORDER BY data DESC", [tk, *verso, cutoff]).fetchall()
                    dec = conn.execute(
                        f"SELECT id, action FROM decisions WHERE UPPER(ticker)=? AND UPPER(action) "
                        f"IN ({ph}) AND status='EXECUTED' AND COALESCE(closed_at, timestamp) >= ? "
                        "ORDER BY id DESC LIMIT 1", [tk, *verso, cutoff]).fetchone()
                if tr and not _novita:
                    t = tr[0]
                    rif = (f"#{dec[0]} {dec[1]} {tk}" if dec else tk)
                    eur_s = f" {eur:,.0f}€" if eur else ""
                    warnings.append(
                        text("**{} {}{}**: RIPROPOSTA POST-ESECUZIONE — il PM ha gia' eseguito "
                        "{} il {} ({:g} x {:g} {}); nessuna NOVITA' "
                        "dichiarata in riga: o si dichiara il fatto nuovo con il tag 'NOVITA':' "
                        "nella colonna Timing, o la riga e' un doppione di cio' che il PM ha appena fatto",
                        "**{} {}{}**: REPROPOSED AFTER EXECUTION — the PM already executed "
                        "{} on {} ({:g} x {:g} {}); no new fact declared in the row: "
                        "declare the new fact using '[NEW-FACT]' in Timing, or this row duplicates "
                        "what the PM has just done").format(act, tk, eur_s, rif, _ddmm(t[3]), t[0], t[1], t[2] or ''))
            except Exception as e:
                if "post-esecuzione" not in controlli_db_falliti:
                    controlli_db_falliti.add("post-esecuzione")
                    warnings.append(text("**CONTROLLO RIPROPOSTE POST-ESECUZIONE NON ESEGUITO** — registro "
                                         "trade/decisioni non leggibile (%s: %s)",
                                         "**POST-EXECUTION PROPOSAL CHECK NOT PERFORMED** — trade/decision "
                                         "register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

    # 4. feedback PM registrato sul ticker (21/07, lezione memo #46): qualunque
    # status — il veto #186 era SKIPPED e il check 2 (solo PENDING) non lo vedeva.
    # Review 21/07: passata SEPARATA su TUTTE le righe (anche HOLD: il veto #186
    # stava proprio su una riga HOLD), UNA voce per ticker (niente doppioni) e
    # ULTIMI 2 feedback (il piu' recente non maschera un veto precedente).
    if db is not None:
        _seen_fb = set()
        for r in rows:
            tk = r["ticker"]
            if not tk or tk in _seen_fb:
                continue
            _seen_fb.add(tk)
            try:
                q = ("SELECT id, action, memo_id, pm_feedback, status FROM decisions "
                     "WHERE UPPER(ticker)=? AND pm_feedback IS NOT NULL "
                     "AND TRIM(pm_feedback) != ''")
                params = [tk]
                if exclude_memo_id is not None:
                    q += " AND memo_id != ?"
                    params.append(exclude_memo_id)
                q += " ORDER BY id DESC LIMIT 2"
                with db._conn() as conn:
                    fb_rows = conn.execute(q, params).fetchall()
                if fb_rows:
                    # 21/08: era `str(x[3])[:120] + "»"` — taglio a un letterale col
                    # caporale di CHIUSURA rimesso dopo, dentro un blocco che dice
                    # «citate testualmente». Stessa policy di memory_db, un posto solo.
                    # audit 11/09: lo stato (SKIPPED/ESEGUITA/...) accanto alla citazione
                    _st = {"EXECUTED": text("ESEGUITA", "EXECUTED"), "SKIPPED": "SKIPPED",
                           "EXPIRED": text("DECADUTA", "EXPIRED"), "PARTIAL": text("PARZIALE", "PARTIAL"),
                           "PENDING": text("PENDENTE", "PENDING")}
                    quotes = "; ".join(
                        "#" + str(x[0]) + " " + str(x[1]) + " (memo #" + str(x[2]) + ", "
                        + _st.get(str(x[4] or "?").upper(), str(x[4] or "?")) + "): "
                        + pm_verbatim(x[3], x[0], virgolette=True) for x in fb_rows)
                    warnings.append(
                        text("**{}**: feedback DIRETTO del PM su decisioni passate — {} — "
                        "verificare che la proposta non li contraddica o che il memo "
                        "dichiari i fatti nuovi", "**{}**: DIRECT PM feedback on past decisions — {} — "
                        "verify that the proposal does not contradict it or that the memo declares "
                        "the new facts").format(tk, quotes))
            except Exception as e:
                if "feedback" not in controlli_db_falliti:
                    controlli_db_falliti.add("feedback")
                    warnings.append(text("**CONTROLLO FEEDBACK PM NON ESEGUITO** — registro decisioni "
                                         "non leggibile (%s: %s)", "**PM FEEDBACK CHECK NOT PERFORMED** — "
                                         "decision register unreadable (%s: %s)") % (type(e).__name__, str(e)[:120]))

    warnings.extend(risk_gate_warnings)
    if not warnings:
        return ""
    block = [text("## ACTION VALIDATOR (verifica automatica #191 — flag-only, i numeri del Capo NON sono stati modificati)",
                  "## ACTION VALIDATOR (automated check #191 — flag-only, the Capo's numbers have NOT been modified)")]
    block += [f"- {w}" for w in warnings]
    block.append(text("*(validator v1 — {}; "
                 "sizing = motore vol×corr; riproposte = decisioni PENDING nei memo "
                 "precedenti; feedback = parole del PM sulle decisioni passate, citate testualmente)*",
                 "*(validator v1 — {}; sizing = vol×corr engine; repeated proposals = PENDING decisions "
                 "in previous memos; feedback = the PM's words on past decisions, quoted verbatim)*"
                 ).format(datetime.now().strftime('%d/%m %H:%M')))
    return "\n".join(block)


def _canonical_sanity(ticker, report_dir=None):
    """Sanity del modello corrente di un ticker: legge il sidecar canonico
    `VAL_X.payload.json` **oppure** `VAL_X_FLAGGED.payload.json` — mutuamente
    esclusivi per costruzione (dcf_engine._write_payload_sidecar rimuove la
    variante opposta). Review Lotto C F2: i modelli BLOCK vivono SOLO nel
    sidecar _FLAGGED (misurati 6/6 nel report/ reale) — guardare solo il
    canonico rendeva l'esclusione morta per costruzione.
    La lettura applica la policy corrente ai soli vecchi scarti FV/prezzo
    dimostrati; file, valori e blocchi tecnici o ambigui restano conservati.
    Ritorna (severity, judged_at) — (None, None) = sidecar assente/illeggibile:
    MAI escludere per assenza di dato."""
    try:
        # 04/10 (G6): stessa lettura del gate di pubblicazione (_sanity_record)
        found = _sanity_payload(ticker, report_dir)
        if found is not None and found[1] is None:
            payload = found[0]
            return _policy_severity(payload), str(payload.get("_timestamp") or "")[:10] or None
    except Exception:
        pass
    return None, None


@scoped_language
def detect_sanity_exclusions(memo_markdown, report_dir=None):
    """#204b residuo — ESCLUSIONE HARD, fase DETECT (pacchetto verita' dei
    numeri Lotto C, ok PM 23/07; riprogettata dopo review: detect/apply separati
    perche' il memo si finalizza PRIMA che le decisioni siano salvate).
    Righe BUY/ADD della ACTION TABLE su ticker col modello corrente in sanity
    **BLOCK** → blocco markdown dichiarato + lista pairs per la fase APPLY.
    Azioni non-standard su ticker BLOCK: il gate automatico NON le copre e lo
    DICE (review F4 — mai bypass muto). Solo BUY/ADD chiudono: un TRIM/SELL su
    modello rotto resta decisione del PM.
    Ritorna (block_markdown, pairs) — ('' , []) se nulla. Non solleva mai."""
    try:
        rows = _parse_action_rows(memo_markdown)
    except Exception:
        return "", []
    lines, pairs = [], []
    for r in rows:
        act, tk = r.get("action"), r.get("ticker")
        if not tk:
            continue
        sev, judged_at = _canonical_sanity(tk, report_dir)
        if sev != "BLOCK":
            continue
        vintage = (text(" (giudizio sanity del {})", " (sanity judgement dated {})").format(judged_at)
                   if judged_at else text(" (data giudizio n.d.)", " (judgement date n.d.)"))
        if act in ("BUY", "ADD"):
            pairs.append((act, tk))
            lines.append(
                text("**{} {}**: modello corrente in **sanity BLOCK**{} → riga NON "
                "azionabile: la decisione viene auto-chiusa nel registro (SKIPPED + nota "
                "AUTO-ESCLUSA) al salvataggio. Per riproporla serve un modello rivalidato "
                "(variant view / rigenerazione)", "**{} {}**: current model in **sanity BLOCK**{} → row NOT "
                "actionable: the decision is automatically closed in the register (SKIPPED + "
                "AUTO-ESCLUSA note) when saved. Re-proposing it requires a revalidated model "
                "(variant view / regeneration)").format(act, tk, vintage))
        elif act not in ACTIONS_KNOWN:
            lines.append(
                text("**{} {}**: azione NON standard su modello in sanity BLOCK{} — "
                "il gate automatico copre solo BUY/ADD: VALUTARE A MANO (dichiarato, non muto)",
                "**{} {}**: NON-standard action on a model in sanity BLOCK{} — the automated gate "
                "only covers BUY/ADD: REVIEW MANUALLY (explicit limitation)").format(act, tk, vintage))
    if not lines:
        return "", []
    block = [text("## ACTION VALIDATOR — ESCLUSIONI HARD (sanity BLOCK, #204b)",
                  "## ACTION VALIDATOR — HARD EXCLUSIONS (sanity BLOCK, #204b)")]
    block += [f"- {ln}" for ln in lines]
    block.append(text("*(le righe BUY/ADD su modello in BLOCK sono tolte dalla ACTION TABLE pubblicata e "
                 "riportate fra le PROPOSTE NON OPERATIVE; il testo intatto del Capo resta nel sidecar "
                 "*_capo_raw.md; l'esito dell'auto-chiusura e' nel log della run e un guasto e' dichiarato)*",
                 "*(BUY/ADD rows on a BLOCK model are removed from the published ACTION TABLE and listed "
                 "under PROPOSTE NON OPERATIVE; the untouched Capo text stays in the *_capo_raw.md sidecar; "
                 "the run log records the automatic closure result and declares any failure)*"))
    return "\n".join(block), pairs


def apply_sanity_exclusions(db, memo_id, pairs, *, strict=False):
    """#204b HARD, fase APPLY: da chiamare DOPO extract_and_save_decisions.
    Chiude le decisioni PENDING del memo per i pairs rilevati: status SKIPPED +
    outcome_notes AUTO-ESCLUSA + closed_at — storia CONSERVATA, mai cancellata.
    Match su UPPER(action) IN ('BUY','ADD') qualunque fosse la riga del memo
    (review F3: la guardia book-aware salva una BUY su posizione esistente come
    ADD). NB dichiarato: EXCLUDED_SANITY letterale non e' ammesso dal CHECK di
    decisions.status → SKIPPED + nota = stessa semantica.
    Ritorna il numero di decisioni chiuse. Non solleva, salvo strict=True (gate)."""
    if not pairs or db is None or memo_id is None:
        return 0
    n_upd = 0
    now = datetime.now().isoformat(timespec="seconds")
    for _act, tk in pairs:
        try:
            with db._conn() as conn:
                cur = conn.execute(
                    "UPDATE decisions SET status='SKIPPED', closed_at=?, "
                    "outcome_notes=COALESCE(outcome_notes,'') || ? "
                    "WHERE memo_id=? AND UPPER(ticker)=? "
                    "AND UPPER(action) IN ('BUY','ADD') AND status='PENDING'",
                    (now, "[AUTO-ESCLUSA " + now[:10] + ": modello in sanity BLOCK — "
                          "riga non azionabile, #204b hard] ",
                     memo_id, str(tk).upper()))
                n_upd += cur.rowcount
        except Exception:
            # 04/10 (G6, REV R1): il gate passa strict=True: un «database is locked» qui
            # e' un guasto da DICHIARARE, non zero righe zitte
            if strict:
                raise
    return n_upd


def sanity_exclusions_not_closed(db, memo_id, pairs):
    """04/10 (G2): i pairs che NON risultano chiusi nel registro (stato FINALE, non le
    righe toccate in questo giro): una ripresa o un retry che trova le decisioni gia'
    SKIPPED + AUTO-ESCLUSA non e' un errore. Solleva se il registro non si legge."""
    missing = []
    with db._conn() as conn:
        for act, tk in pairs or []:
            row = conn.execute(
                "SELECT COUNT(id) FROM decisions WHERE memo_id=? AND UPPER(ticker)=? "
                "AND UPPER(action) IN ('BUY','ADD') AND status='SKIPPED' "
                "AND outcome_notes LIKE '%AUTO-ESCLUSA%'", (memo_id, str(tk).upper())).fetchone()
            if not row or int(row[0] or 0) < 1:
                missing.append((act, tk))
    return missing


if __name__ == "__main__":
    # Test standalone read-only sull'ultimo memo reale
    from bellomberg.storage.memory_db import MemoryDB
    db = MemoryDB()
    with db._conn() as conn:
        memo_id, md = conn.execute(
            "SELECT id, full_markdown FROM memos WHERE LENGTH(full_markdown) > 1000 "
            "AND substr(COALESCE(notes,''),1,11) <> 'trade_idea:' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    print(f"Test su memo #{memo_id} ({len(md)} char)")
    sizing = None
    try:
        from bellomberg.portfolio.sizing_engine import compute_sizing
        sizing = compute_sizing(db.get_portfolio_summary())
    except Exception as e:
        print("[test] sizing non disponibile:", e)
    print(build_validator_block(md, sizing, db, exclude_memo_id=memo_id) or "(nessun avvertimento)")
