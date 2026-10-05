"""
action_table_extract.py — #200c STRUCTURED OUTPUTS per l'ACTION TABLE (15/07).

Il parsing regex dell'ACTION TABLE (memory_db.extract_and_save_decisions +
copia in action_validator) e' fragile: colonne fuori ordine, ticker in
grassetto, celle multilinea o un header leggermente diverso = righe perse o
sporcate IN SILENZIO. Qui la tabella viene TRASCRITTA da Sonnet con un tool
FORZATO (tool_choice) e schema esplicito: il modello non inventa, trascrive.

Contratto: le celle tornano VERBATIM (size_raw resta stringa: la conversione
in EUR la fa sempre memory_db._parse_eur_amount, identica a prima). Il
chiamante usa la regex come FALLBACK DICHIARATO se questa via fallisce.
Costo: 1 chiamata Sonnet ~5k token in / ~400 out per memo (centesimi).
"""
import re as _re
import time as _time
from bellomberg.core.language import prompt_for_language, scoped_language

ACTION_EXTRACT_MAX_TOKENS = 16000

EMIT_TOOL = {
    "name": "emit_action_table",
    "description": "Trascrivi in forma strutturata le righe DATI dell'ACTION TABLE del memo.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string",
                                   "description": "cella azione VERBATIM in maiuscolo, es. BUY/ADD/TRIM/SELL/HOLD/RESEARCH/HEDGE/WATCH"},
                        "ticker": {"type": "string",
                                   "description": "SOLO il ticker, senza grassetto ne' nome esteso, es. ABCD.MI"},
                        "size_raw": {"type": "string",
                                     "description": "cella size/importo VERBATIM (es. '5.000€', '3k', '2-3%'): NON convertirla"},
                        "timing": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                    "required": ["action", "ticker", "size_raw", "timing", "confidence"],
                },
            },
            "table_found": {"type": "boolean",
                            "description": "false SOLO se nel testo non c'e' alcuna ACTION TABLE"},
        },
        "required": ["rows", "table_found"],
    },
}

_SYSTEM = (
    "Sei un estrattore di dati. Nel testo che ricevi c'e' (di solito) la sezione "
    "ACTION TABLE di un memo: una tabella con le decisioni (action, ticker, size, "
    "timing, confidence, eventuale rationale). Trascrivi OGNI riga dati della tabella "
    "chiamando il tool emit_action_table. Regole: trascrivi VERBATIM (niente "
    "conversioni, niente righe inventate, niente righe di header/separatore); ignora "
    "tutto cio' che non e' l'ACTION TABLE (testo, blocchi LINTER/VALIDATOR in coda); "
    "se una tabella non c'e', rows=[] e table_found=false."
)

# Fable 5 16/07 (findings run #45): la premessa "l'ACTION TABLE vive in coda" era FALSA —
# il prompt del Capo la impone in CIMA ("Prima riga: '## ACTION TABLE'", capo.py FORMATO
# FINALE). Sul memo #45 (33.780 char, tabella a char 74) la coda [-12000:] non la
# conteneva: Sonnet rispondeva table_found=false SENZA errore, quindi il chiamante
# (memory_db:858-864) segnava structured_ok=True con 0 righe e il fallback regex non
# partiva mai -> decisions vuota. Bug intermittente: coi memo <12k coda = memo intero.
# La finestra ora PARTE dal marker (stessa ricerca di action_validator._parse_action_rows);
# senza marker resta la coda: e' il comportamento storico, e il fallback regex a valle
# cerca lo stesso marker, quindi fallirebbe comunque.
_WINDOW_CHARS = 12000  # ampiezza invariata: e' il contratto di costo (~5k token in)


def _split_markdown_row(line: str) -> list[str]:
    """Split a Markdown table row while respecting escaped pipes."""
    value = (line or "").strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    return [part.replace(r"\|", "|").strip() for part in _re.split(r"(?<!\\)\|", value)]


def _header_key(value: str) -> str:
    value = _re.sub(r"[*_`]+", "", str(value or "")).strip().lower()
    value = _re.sub(r"[^a-zà-ÿ0-9]+", " ", value)
    value = " ".join(value.split())
    aliases = {
        "action": {"action", "azione", "decision", "decisione"},
        "ticker": {"ticker", "symbol", "strumento", "titolo"},
        "size": {"size", "eur", "amount", "importo", "taglia", "dimensione", "peso"},
        "timing": {"timing", "when", "orizzonte", "tempo"},
        "confidence": {"confidence", "conviction", "confidenza"},
    }
    return next((key for key, values in aliases.items()
                 if value in values or any(value.startswith(alias + " ") for alias in values)), value)


def _ticker_from_cell(value: str) -> str:
    """Extract the leading security symbol without changing its spelling."""
    cell = str(value or "").strip()
    # Markdown link label, bold/italic and inline code wrappers are presentation.
    link = _re.match(r"^\[([^\]]+)\]\([^)]*\)", cell)
    if link:
        cell = link.group(1).strip()
    cell = _re.sub(r"^\*{1,3}|\*{1,3}$|^_{1,3}|_{1,3}$|^`+|`+$", "", cell).strip()
    match = _re.match(r"([A-Za-z0-9^][A-Za-z0-9.^=_-]{0,19})", cell)
    return match.group(1) if match else ""


def parse_action_table_rows(memo_markdown: str) -> dict:
    """Parse ACTION TABLE rows locally, preserving the ticker written in each cell.

    04/10 (G6, review 04 G1/G5): this is the ONLY ACTION TABLE parser. The
    validator, the publication gate, the projection and the decision register
    all read these rows, so a row index means the same row everywhere.
    Rules: the table is the FIRST Markdown table after the heading and ends at
    the first blank or non-table line (a second table in the section, e.g. a
    watchlist, is not proposals); indented lines continue the previous row;
    header and action cells lose their Markdown decoration (`**BUY**` is BUY).

    Returns rows with a zero-based index over all data rows, `ticker_cell` as
    the source cell, `line_index` (position in ``memo_markdown.splitlines()``),
    `raw` (the source line) and `action_cell`. `table_lines` is the
    [start, end) line range of the table. A header that does not name the
    action, ticker and size columns returns no rows and a declared `error`.
    """
    text = memo_markdown or ""
    lines = text.splitlines()
    heading = next((i for i, line in enumerate(lines)
                    if _re.match(r"^##\s*ACTION\s+TABLE\b", line, _re.IGNORECASE)), None)
    if heading is None:
        return {"rows": [], "table_found": False, "table_lines": None, "error": None}
    table_rows = []          # (line_index, cells, raw)
    start = end = None
    for i in range(heading + 1, len(lines)):
        raw_line = lines[i]
        if _re.match(r"^##\s+", raw_line):
            break
        if raw_line.strip().startswith("|"):
            if start is None:
                start = i
            table_rows.append((i, _split_markdown_row(raw_line), raw_line))
            end = i + 1
        elif start is not None and raw_line and raw_line[0].isspace() and raw_line.strip():
            # Markdown continuation lines are attached to the trailing cell of
            # the previous row. The ticker cell remains exactly as written.
            table_rows[-1][1][-1] += " <br> " + raw_line.strip()
            end = i + 1
        elif start is not None:
            break            # blank line or prose: the ACTION TABLE is over
    table_lines = [start, end] if start is not None else None
    if len(table_rows) < 2:
        return {"rows": [], "table_found": True, "table_lines": table_lines, "error": None}

    header_cells = table_rows[0][1]
    header = [_header_key(c) for c in header_cells]
    # Il gate ha bisogno di azione, ticker e importo; timing e confidence (le altre due
    # colonne del formato del Capo) restano vuote se mancano, non rendono illeggibile la tabella.
    required = {"action", "ticker", "size"}
    if not required.issubset(set(header)):
        missing = sorted(required - set(header))
        return {"rows": [], "table_found": True, "table_lines": table_lines,
                "error": "intestazione ACTION TABLE illeggibile: colonne mancanti " + ", ".join(missing)}
    indices = {key: header.index(key) for key in required | ({"timing", "confidence"} & set(header))}
    rows = []
    for line_index, cells, raw_line in table_rows[1:]:
        if not cells or not any(c.strip() for c in cells):
            continue
        if all(_re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells):
            continue
        if len(cells) <= max(indices.values()):
            cells = cells + [""] * (max(indices.values()) + 1 - len(cells))
        action_cell = cells[indices["action"]].strip()
        action = _re.sub(r"[*_`]+", "", action_cell).strip()
        ticker_cell = cells[indices["ticker"]].strip()
        ticker = _ticker_from_cell(ticker_cell)
        rows.append({
            "row_index": len(rows),
            "line_index": line_index,
            "raw": raw_line,
            "action_cell": action_cell,
            "action": action.upper(),
            "ticker": ticker,
            "ticker_cell": ticker_cell,
            "size_raw": cells[indices["size"]].strip(),
            "timing": cells[indices["timing"]].strip() if "timing" in indices else "",
            "confidence": cells[indices["confidence"]].strip() if "confidence" in indices else "",
            "cells": list(cells),
        })
    return {"rows": rows, "table_found": True, "table_lines": table_lines, "error": None,
            "columns": dict(indices)}


@scoped_language
def extract_rows_structured(memo_markdown: str, usage_out: dict = None) -> dict:
    """Ritorna {'rows': [...], 'table_found': bool} o {'error': str}. Non solleva.

    usage_out (opzionale, #44/finding 4): dict MUTATO in-place col consumo della
    chiamata Sonnet, cosi' il chiamante puo' registrarla con blackboard.record_usage
    (prima di oggi questa chiamata REALE non entrava nel conto della run). Firma
    retro-compatibile: chi non passa usage_out non vede alcuna differenza.
    Chiavi: in/out/cache_read/cache_write (schema di red_team.py e specialists/base.py)
    + model, api_calls, duration_s, status. status vale:
      "skipped"       -> NESSUNA chiamata API fatta: il chiamante NON deve registrare
      "ok"            -> token noti
      "usage_unknown" -> chiamata fatta ma la risposta non ha esposto usage (token IGNOTI)
      "api_error"     -> la chiamata e' fallita
    """
    _u_out = usage_out if isinstance(usage_out, dict) else None
    if _u_out is not None:
        _u_out.clear()
        _u_out.update({"in": 0, "out": 0, "cache_read": 0, "cache_write": 0,
                       "model": None, "api_calls": 0, "duration_s": None,
                       "status": "skipped"})
    try:
        from bellomberg.core.llm_client import OpenRouterClient, modello as _modello_llm, somma_usage
        from bellomberg.agents.specialists.base import timeout_specialisti
        from bellomberg.core.llm_refusal import refusal_reason as _refusal_reason
        # 05/09 (ordine PM): modello dal .env (ACTION_EXTRACTOR_MODEL); assente = errore col nome
        MODEL_SYNTHESIZER = _modello_llm("action_extractor")
    except Exception as e:
        return {"error": f"import: {e}"}
    if _u_out is not None:
        _u_out["model"] = MODEL_SYNTHESIZER
    _memo = memo_markdown or ""
    _mark = _re.search(r"##\s*ACTION TABLE", _memo, _re.IGNORECASE)
    text = (_memo[_mark.start():_mark.start() + _WINDOW_CHARS] if _mark
            else _memo[-_WINDOW_CHARS:])
    if not text.strip():
        return {"error": "memo vuoto"}
    _t0 = _time.perf_counter()
    try:
        client = OpenRouterClient(timeout=timeout_specialisti(ACTION_EXTRACT_MAX_TOKENS), max_retries=1)
        resp = client.messages.create(
            model=MODEL_SYNTHESIZER,
            # PM 02/10: 16k; JSON troncato resta un errore dichiarato.
            max_tokens=ACTION_EXTRACT_MAX_TOKENS,
            # Sonnet 5 (26/07): omesso = adaptive acceso; SPENTO esplicito — qui
            # c'e' tool_choice FORZATO (estrazione meccanica), il thinking non
            # serve e col budget corto lo eroderebbe
            thinking={"type": "disabled"},
            system=prompt_for_language(_SYSTEM),
            tools=[EMIT_TOOL],
            tool_choice={"type": "tool", "name": "emit_action_table"},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as e:
        if _u_out is not None:
            _u_out["api_calls"] = 1
            _u_out["duration_s"] = round(_time.perf_counter() - _t0, 2)
            _u_out["status"] = "api_error"
        return {"error": f"API: {type(e).__name__}: {str(e)[:150]}"}
    if _u_out is not None:
        # la chiamata E' partita: i token sono spesi e vanno dichiarati anche se poi
        # il payload del tool risulta inutilizzabile e si cade sul fallback regex.
        _u_out["api_calls"] = 1
        _u_out["duration_s"] = round(_time.perf_counter() - _t0, 2)
        try:
            _u = resp.usage
            if _u is None:
                raise ValueError("response.usage assente")
            _u_out.update(somma_usage(None, _u))
            _u_out["status"] = "usage_unknown" if _u_out["tokens_status"] == "parziale" else "ok"
        except Exception:
            # token IGNOTI, diverso da zero: si dichiara, non si finge lo 0
            _u_out["status"] = "usage_unknown"
    # 26/07 pre-V6: senza questo controllo un rifiuto dei safeguard (HTTP 200, content
    # vuoto) usciva dal fondo della funzione con "tool_choice ignorato?" — un errore
    # DICHIARATO ma con la CAUSA SBAGLIATA, che avrebbe mandato la diagnosi a caccia
    # di un bug di schema inesistente. Il fallback regex del chiamante resta identico.
    _rif = _refusal_reason(resp, "ACTION_TABLE")
    if _rif:
        if _u_out is not None:
            _u_out["status"] = "refusal"
        return {"error": _rif}
    if getattr(resp, "stop_reason", None) == "max_tokens":
        return {"error": "ACTION TABLE troncata al limite token: estrazione incompleta, usare il fallback dichiarato"}
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "emit_action_table":
            inp = block.input or {}
            rows = inp.get("rows")
            if not isinstance(rows, list):
                return {"error": "payload senza lista rows"}
            clean = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                a, t = str(r.get("action") or "").strip(), str(r.get("ticker") or "").strip()
                if not a or not t:
                    continue
                clean.append({"action": a.upper(), "ticker": t.upper(),
                              "size_raw": str(r.get("size_raw") or ""),
                              "timing": str(r.get("timing") or ""),
                              "confidence": str(r.get("confidence") or "")})
            return {"rows": clean, "table_found": bool(inp.get("table_found", bool(clean)))}
    return {"error": "nessun tool_use nella risposta (tool_choice ignorato?)"}


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    demo = """
## ACTION TABLE
| Action | Ticker | Size | Timing | Confidence | Rationale |
|---|---|---|---|---|---|
| ADD | **BANCA.MI** (Banca sintetica) | 5.000€ | questa settimana | ALTA | SOPRA POLICY: +3pt, NII +18% |
| TRIM | FONDO.L | ~8k | entro venerdi | MEDIA | sconto NAV -33% ma peso 20,8% |
| RESEARCH | 9988.HK | 2-3% | prossima run | BASSA | Alibaba: consensus depresso |
"""
    print(json.dumps(extract_rows_structured(demo), indent=2, ensure_ascii=False))
