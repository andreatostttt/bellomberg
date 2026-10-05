"""Consigliere «a lacune»: dichiarazione delle lacune del comitato (modulo puro).

Decisioni PM 04/10 (pacchetto consigliato, R8 §6 costruttore C):
- quorum = Fundamentals + almeno 4 dei 6 desk, come Trade Idea;
- un desk caduto e' fuori per il resto della run e si DICHIARA;
- Capo fallito = memo parziale marcato INCOMPLETO, nessuna decisione, nessuna email automatica;
- Red Team mancante = si prosegue dichiarandolo, nessuna convinzione ALTA;
- decisioni solo sui domini coperti (regola DOMINI SCOPERTI del prompt del Capo);
- email con la riga «COMITATO INCOMPLETO».

Il modulo NON orchestra e non scrive nulla: legge la blackboard e lo store e
restituisce (a) il blocco markdown per il memo, (b) UNA riga per l'email,
(c) i campi strutturati per la UI. La dichiarazione la scrive il codice, non
la disciplina dell'LLM (stessa ragione di `core.freshness.format_for_memo`).

Forme accettate (documentate perche' l'orchestrazione arriva dopo il merge di Andrea):
- ``bb``: un ``Blackboard`` (si legge ``bb.data``) oppure il dict ``data`` stesso.
  Chiavi lette: ``data[<desk>][<round>]`` (report; round int o stringa),
  ``data["_desk_gaps"]`` = ``{desk: {message, exception_type, desk, round, request_id, phase}}``
  (forma di Trade Idea, `trade_idea.py` callback ``failure``), ``data["_red_team"][<round>]``,
  ``data["_red_team_gap"]`` = ``{message, exception_type?, request_id?, phase?}`` (chiave
  proposta in R8 per il costruttore B).
- ``store``: un ``WeeklyRunStore`` (``store.context["contract"]["roster"|"r2_specialists"]``,
  ``store.get(stage)``) oppure un dict ``{"roster": [...], "r2_specialists": [...],
  "checkpoints": {stage: payload}}``. Il roster e' OBBLIGATORIO: senza roster non si
  conta un quorum (nessun roster di ripiego).
- ``capo``: ``{"memo": str, "usage": dict}`` come il checkpoint ``capo``; se assente si
  legge ``store.get("capo")``; se manca anche quello lo stato del Capo e' ``unknown``
  (dichiarato, mai «completo» per default).
"""
import html
import re

from bellomberg.core.language import capture_language, text

QUORUM_REQUIRED = ("fundamentals",)
QUORUM_MIN_PRESENT = 4
_REASON_MAX = 220
_URL_RE = re.compile(r"https?://\S+")

# Nomi pubblici dei desk e domini (gli stessi del prompt del Capo, «DOMINI SCOPERTI»).
_DESK_LABELS = {"macro": "Macro", "eventdesk": "Event Desk", "crypto": "Crypto",
                "fundamentals": "Fundamentals", "quant": "Quant", "options": "Options"}
_DESK_DOMAINS = {
    "macro": ("regime macro e temi di mercato", "macro regime and market themes"),
    "eventdesk": ("news, catalyst datati, probabilità politiche e geopolitiche",
                  "news, dated catalysts, political and geopolitical probabilities"),
    "crypto": ("esposizioni cripto effettive del book", "the book's actual crypto exposures"),
    "fundamentals": ("fondamentali e selezione dei candidati", "fundamentals and candidate selection"),
    "quant": ("validazione quantitativa dei candidati", "quantitative validation of the candidates"),
    "options": ("strutture in opzioni", "options structures"),
}


def quorum_threshold(roster):
    """Desk presenti richiesti: QUORUM_MIN_PRESENT (4, come Trade Idea), ma mai piu' del
    roster: con un roster di N < 4 desk servono tutti e N (KB 04/10, ok main). Fundamentals
    resta sempre obbligatorio (QUORUM_REQUIRED)."""
    return min(QUORUM_MIN_PRESENT, len(roster))


def _label(desk):
    return _DESK_LABELS.get(desk, str(desk))


def _clean(reason, lang):
    """Una causa in riga singola: niente URL (possono portare querystring con chiavi),
    niente pipe/a-capo (una causa = una riga dell'elenco), taglio DICHIARATO con [...]."""
    if reason is None or not str(reason).strip():
        return text("causa non registrata", "cause not recorded", language=lang)
    out = _URL_RE.sub(text("[URL omesso]", "[URL omitted]", language=lang), str(reason))
    out = " ".join(out.replace("|", "/").split())
    if len(out) > _REASON_MAX:
        out = out[:_REASON_MAX].rstrip() + " [...]"
    return out


def _data_of(bb):
    data = getattr(bb, "data", bb)
    if not isinstance(data, dict):
        raise TypeError("Blackboard non leggibile: serve un oggetto con .data o un dict")
    return data


def _store_view(store):
    """(roster, r2_specialists, get) da WeeklyRunStore o da un dict documentato."""
    if store is None:
        raise ValueError("Store del comitato assente: roster non disponibile, quorum non calcolabile")
    if isinstance(store, dict):
        roster, r2 = store.get("roster"), store.get("r2_specialists")
        checkpoints = store.get("checkpoints") or {}
        getter = checkpoints.get
    else:
        contract = (getattr(store, "context", None) or {}).get("contract") or {}
        roster, r2 = contract.get("roster"), contract.get("r2_specialists")
        getter = store.get
    if not roster:
        raise ValueError("Roster del comitato non disponibile nello store: quorum non calcolabile")
    return list(roster), list(r2 or []), getter


def _report(data, desk, round_n):
    rounds = data.get(desk) or {}
    if not isinstance(rounds, dict):
        return None
    value = rounds.get(round_n)
    return value if value is not None else rounds.get(str(round_n))


def _usable(report):
    from bellomberg.agents.capo import _e_segnaposto
    return isinstance(report, str) and not _e_segnaposto(report)


def _round_of(item):
    value = item.get("round") if isinstance(item, dict) else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _red_team_state(data, get, lang):
    gap = data.get("_red_team_gap")
    saved = get("red_team") or {}
    if isinstance(gap, dict) or (isinstance(saved, dict) and saved.get("gap")):
        reason = (gap or {}).get("message") if isinstance(gap, dict) else saved.get("gap")
        return {"status": "absent", "reason": _clean(reason, lang),
                "request_id": (gap or {}).get("request_id") if isinstance(gap, dict) else None}
    rounds = data.get("_red_team") or {}
    report = None
    if isinstance(rounds, dict) and rounds:
        report = rounds[max(rounds, key=lambda key: int(key) if str(key).isdigit() else -1)]
    if report is None and isinstance(saved, dict):
        report = saved.get("report")
    if report is None:
        return {"status": "absent", "request_id": None,
                "reason": text("nessuna critica del Red Team a registro",
                               "no Red Team critique on record", language=lang)}
    from bellomberg.agents.red_team import motivo_critica_non_utilizzabile
    reason = motivo_critica_non_utilizzabile(report)
    if reason:
        return {"status": "absent", "reason": _clean(reason, lang), "request_id": None}
    if "CRITICA TRONCATA" in str(report):
        return {"status": "truncated", "request_id": None,
                "reason": text("critica troncata al limite di token del Red Team",
                               "critique truncated at the Red Team token limit", language=lang)}
    return {"status": "present", "reason": None, "request_id": None}


def _capo_state(capo, get, lang):
    saved = capo if capo is not None else get("capo")
    if not isinstance(saved, dict) or "usage" not in saved:
        return {"status": "unknown", "reason": text(
            "esito del Capo non disponibile al momento della dichiarazione",
            "Capo outcome not available when the gaps were declared", language=lang)}
    from bellomberg.agents.weekly_lifecycle import validate_memo
    from bellomberg.storage.weekly_run_store import WeeklyRunBlocked
    try:
        validate_memo(saved.get("memo"), saved.get("usage"))
    except WeeklyRunBlocked as exc:
        return {"status": "partial", "reason": _clean(str(exc), lang)}
    return {"status": "complete", "reason": None}


def committee_gaps_summary(bb, store, *, capo=None, unbilled_retries=None, language=None):
    """Stato del comitato per memo, email e UI. Dict serializzabile in JSON.

    ``unbilled_retries``: lista opzionale di ``{request_id, agent, reason}`` (costruttore A);
    ``None`` = non misurato (campo ``None``, nessuna riga nel memo). Un ritentativo
    riuscito NON rende incompleto il comitato: compare nel blocco solo se il blocco c'e'.
    """
    lang = capture_language(language)
    data = _data_of(bb)
    roster, r2_specialists, get = _store_view(store)
    gaps = data.get("_desk_gaps") or {}
    desks, missing = [], []
    for desk in roster:
        item = gaps.get(desk)
        r1 = _report(data, desk, 1)
        entry = {"desk": desk, "label": _label(desk), "status": "present", "reason": None,
                 "phase": None, "round": None, "request_id": None, "exception_type": None}
        if isinstance(item, dict):
            entry.update(status="gap", reason=_clean(item.get("message"), lang),
                         phase=item.get("phase"), round=_round_of(item),
                         request_id=item.get("request_id"), exception_type=item.get("exception_type"))
        elif item is not None:
            entry.update(status="gap", reason=_clean(item, lang))
        elif not _usable(r1):
            entry.update(status="gap", round=1, reason=text(
                "report R1 non disponibile", "R1 report unavailable", language=lang))
        # Un desk caduto in R2 conserva il suo R1 sigillato: conta nel quorum e il suo
        # dominio resta coperto; le obiezioni del Red Team a lui restano senza risposta.
        entry["counts_for_quorum"] = entry["status"] == "present" or (
            entry["round"] is not None and entry["round"] >= 2 and _usable(r1))
        if not entry["counts_for_quorum"]:
            missing.append(desk)
        entry["in_roster"] = True
        desks.append(entry)
    # Una lacuna registrata per un nome fuori roster e' un'incoerenza del registro:
    # si dichiara come tale, non si scarta.
    for desk in sorted(set(gaps) - set(roster)):
        item = gaps[desk]
        desks.append({"desk": desk, "label": _label(desk), "status": "gap",
                      "reason": _clean(item.get("message") if isinstance(item, dict) else item, lang)
                      + text(" (desk fuori roster)", " (desk outside the roster)", language=lang),
                      "phase": item.get("phase") if isinstance(item, dict) else None,
                      "round": _round_of(item), "exception_type": None,
                      "request_id": item.get("request_id") if isinstance(item, dict) else None,
                      "counts_for_quorum": False, "in_roster": False})
    present =[d["desk"] for d in desks if d["counts_for_quorum"]]
    threshold = quorum_threshold(roster)
    reached = all(name in present for name in QUORUM_REQUIRED) and len(present) >= threshold
    unreplied = [desk for desk in r2_specialists if desk in present
                 and not _usable(_report(data, desk, 2))]
    uncovered = [{"desk": desk, "label": _label(desk),
                  "domain": text(*_DESK_DOMAINS.get(desk, (desk, desk)), language=lang)}
                 for desk in missing]
    red = _red_team_state(data, get, lang)
    capo_state = _capo_state(capo, get, lang)
    gap_list = [d for d in desks if d["status"] == "gap"]
    incomplete = bool(gap_list or unreplied or red["status"] != "present"
                      or capo_state["status"] != "complete" or not reached)
    summary = {
        "status": ("below_quorum" if not reached else "incomplete" if incomplete else "complete"),
        "language": lang,
        "quorum": {"reached": reached, "present": present, "missing": missing,
                   "present_count": len(present), "roster_count": len(roster),
                   "required": list(QUORUM_REQUIRED), "min_present": threshold},
        "desks": desks,
        "gaps": [{k: d[k] for k in ("desk", "label", "reason", "phase", "round", "request_id",
                                    "exception_type")} for d in gap_list],
        "unreplied_r2": unreplied,
        "uncovered_domains": uncovered,
        "red_team": red,
        "capo": capo_state,
        "unbilled_retries": list(unbilled_retries) if unbilled_retries is not None else None,
        "high_conviction_allowed": red["status"] == "present",
        "decisions_allowed": reached and capo_state["status"] == "complete",
        "automatic_email_allowed": reached and capo_state["status"] == "complete",
    }
    summary["memo_markdown"] = format_gaps_for_memo(summary)
    summary["email_line"] = format_gaps_for_email(summary)
    return summary


def _round_text(entry, lang):
    return "R" + str(entry["round"]) if entry.get("round") is not None else text(
        "n.d.", "n/a", language=lang)


def format_gaps_for_memo(summary):
    """Blocco markdown appeso al memo dal codice. Stringa vuota se il comitato e' completo."""
    if summary["status"] == "complete":
        return ""
    lang = summary["language"]
    t = lambda it, en: text(it, en, language=lang)  # noqa: E731
    q = summary["quorum"]
    # Niente corsivo *...*: il renderer del PDF (pdf_institutional) rende solo **grassetto**
    # e mostrerebbe gli asterischi letterali.
    lines = [t("## Comitato: lacune dichiarate", "## Committee: declared gaps"),
             t("Sezione scritta dal codice a partire dal registro della run; non dipende dal Capo.",
               "Section written by the code from the run record; it does not depend on the Capo."),
             ""]
    esito = (t("Sotto quorum", "Below quorum") if not q["reached"] else t("Incompleto", "Incomplete"))
    if q["min_present"] < QUORUM_MIN_PRESENT:
        # Soglia effettiva e perche' (main 04/10): un roster piu' corto della soglia
        # standard non abbassa il quorum, lo porta a «tutti».
        regola = t("Regola di quorum: Fundamentals più tutti i {n} desk (roster di {n} desk: "
                   "servono tutti).",
                   "Quorum rule: Fundamentals plus all {n} desks (roster of {n} desks: all are "
                   "required).").format(n=q["roster_count"])
    else:
        regola = t("Regola di quorum: Fundamentals più almeno {} desk su {}.",
                   "Quorum rule: Fundamentals plus at least {} of {} desks.").format(
            q["min_present"], q["roster_count"])
    lines.append(t("**Esito del comitato.** {}. Desk presenti: {} di {}; Fundamentals {}. {}",
                   "**Committee outcome.** {}. Desks present: {} of {}; Fundamentals {}. {}").format(
        esito, q["present_count"], q["roster_count"],
        t("presente", "present") if "fundamentals" in q["present"] else t("assente", "absent"),
        regola))
    if summary["gaps"]:
        # Elenco, non tabella (scelta PM 04/10, impianto C): una riga per desk.
        lines += ["", t("**Desk in lacuna**", "**Desks with a declared gap**"), ""]
        for g in summary["gaps"]:
            lines.append(t("- {}, {} ({}): {}. Richiesta {}.", "- {}, {} ({}): {}. Request {}.").format(
                g["label"], _round_text(g, lang), g["phase"] or t("fase n.d.", "phase n/a"),
                g["reason"].rstrip("."), g["request_id"] or t("n.d.", "n/a")))
        lines.append(t("Un desk in lacuna è escluso dai round successivi della run.",
                       "A desk with a gap is excluded from the remaining rounds of the run."))
    if summary["uncovered_domains"]:
        doms = "; ".join("{}: {}".format(d["label"], d["domain"]) for d in summary["uncovered_domains"])
        lines += ["", t("**Domini scoperti.** {}. Le azioni su questi domini hanno confidenza BASSA "
                        "o sono rinviate a RESEARCH.",
                        "**Uncovered domains.** {}. Actions in these domains carry LOW confidence "
                        "or are deferred to RESEARCH.").format(doms)]
    if summary["unreplied_r2"]:
        names = ", ".join(_label(d) for d in summary["unreplied_r2"])
        lines += ["", t("**Repliche al Red Team mancanti.** {}: le obiezioni rivolte a questi desk "
                        "restano senza risposta.",
                        "**Missing replies to the Red Team.** {}: the objections addressed to these "
                        "desks remain unanswered.").format(names)]
    red = summary["red_team"]
    if red["status"] == "absent":
        lines += ["", t("**Red Team.** Assente: {}. Il contraddittorio non è avvenuto; nessuna "
                        "proposta ha convinzione ALTA.",
                        "**Red Team.** Absent: {}. No adversarial review took place; no proposal "
                        "carries HIGH conviction.").format(red["reason"])]
    elif red["status"] == "truncated":
        lines += ["", t("**Red Team.** Critica parziale: {}. Nessuna proposta ha convinzione ALTA.",
                        "**Red Team.** Partial critique: {}. No proposal carries HIGH "
                        "conviction.").format(red["reason"])]
    capo = summary["capo"]
    if capo["status"] == "complete":
        lines += ["", t("**Capo.** Memo completo, redatto sui soli domini coperti.",
                        "**Capo.** Complete memo, written on the covered domains only.")]
    else:
        lines += ["", t("**Capo.** Memo INCOMPLETO ({}): {}. Nessuna decisione estratta, nessuna "
                        "email automatica.",
                        "**Capo.** INCOMPLETE memo ({}): {}. No decision extracted, no automatic "
                        "email.").format(t("parziale", "partial") if capo["status"] == "partial"
                                         else t("esito ignoto", "unknown outcome"), capo["reason"])]
    retries = summary.get("unbilled_retries")
    if retries:
        items = "; ".join("{} ({})".format(r.get("agent") or t("n.d.", "n/a"),
                                           r.get("request_id") or t("n.d.", "n/a")) for r in retries)
        lines += ["", t("**Ritentativi non fatturati.** {}: richiesta mai giunta al modello, "
                        "ripetuta una volta.",
                        "**Unbilled retries.** {}: request never reached the model, repeated "
                        "once.").format(items)]
    return "\n".join(lines)


def format_gaps_for_email(summary, *, as_html=False):
    """UNA riga per il corpo email. Stringa vuota se il comitato e' completo."""
    if summary["status"] == "complete":
        return ""
    lang = summary["language"]
    t = lambda it, en: text(it, en, language=lang)  # noqa: E731
    q = summary["quorum"]
    parts = [t("quorum {} di {}{}", "quorum {} of {}{}").format(
        q["present_count"], q["roster_count"], "" if q["reached"] else t(" (NON raggiunto)", " (NOT reached)"))]
    if summary["gaps"]:
        parts.append(t("desk in lacuna: ", "desks with a gap: ") + ", ".join(
            "{} ({})".format(g["label"], _round_text(g, lang)) for g in summary["gaps"]))
    if summary["unreplied_r2"]:
        parts.append(t("repliche R2 mancanti: ", "missing R2 replies: ")
                     + ", ".join(_label(d) for d in summary["unreplied_r2"]))
    red = summary["red_team"]["status"]
    parts.append({"present": t("Red Team presente", "Red Team present"),
                  "absent": t("Red Team assente", "Red Team absent"),
                  "truncated": t("Red Team parziale", "Red Team partial")}[red])
    capo = summary["capo"]["status"]
    parts.append({"complete": t("Capo completo", "Capo complete"),
                  "partial": t("Capo parziale, nessuna decisione", "Capo partial, no decision"),
                  "unknown": t("esito del Capo ignoto, nessuna decisione",
                               "Capo outcome unknown, no decision")}[capo])
    line = t("COMITATO INCOMPLETO: ", "COMMITTEE INCOMPLETE: ") + "; ".join(parts) + t(
        ". Dettaglio nella sezione «Comitato: lacune dichiarate» del memo.",
        ". Details in the memo section «Committee: declared gaps».")
    if as_html:
        head, _, rest = line.partition(": ")
        return "<p><strong>" + html.escape(head) + "</strong>: " + html.escape(rest) + "</p>"
    return line
