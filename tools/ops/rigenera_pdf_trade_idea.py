# -*- coding: utf-8 -*-
"""rigenera_pdf_trade_idea.py — rigenera il PDF di una run Trade Idea GIA' TERMINATA dal
risultato salvato nel DB (nessuna chiamata AI) e lo ricontrolla con l'ispettore d'integrita'.

Nato dal difetto del 06/10/2026: una riga del registro obiezioni spezzata fra due pagine
faceva uscire il PDF «non qualificato» anche se il testo c'era tutto.

USO (dalla radice del repo):
  python tools/ops/rigenera_pdf_trade_idea.py <run_id>            prova: DB in sola lettura, PDF in una cartella tmp
  python tools/ops/rigenera_pdf_trade_idea.py <run_id> --apply    consegna: stesso percorso della consegna normale

Senza --apply:
  - il DB si apre SOLO in lettura (mode=ro, misurato: dimensione e data del file prima/dopo);
  - il PDF si rigenera in una cartella temporanea, mai accanto a quello vero;
  - stampa l'esito dell'ispettore sul PDF rigenerato e, a confronto, sul PDF gia' consegnato;
  - stampa cosa farebbe --apply e se la consegna normale lo permette.
Con --apply:
  - usa deliver_trade_idea (le sue guardie: lock della consegna, manifest immutabile una volta
    sigillato, email reclamata una sola volta, esito registrato) e chiede conferma;
  - se il manifest e' gia' sigillato nel DB la consegna normale NON lo riscrive: lo script si
    ferma e lo DICHIARA (nessuna scorciatoia sul DB).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", str(ROOT))
for _path in (str(ROOT / "src"), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _backend_acceso() -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8765))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _impronta_file(db_path: Path) -> dict:
    """Dimensione e data di modifica del DB e dei suoi file -wal/-shm: la prova misura che non cambino."""
    out = {}
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db_path) + suffix)
        out[suffix or "db"] = (path.stat().st_size, path.stat().st_mtime_ns) if path.exists() else None
    return out


def run_for_report(detail: dict) -> dict:
    """Il run arricchito per il renderer: STESSA costruzione di _deliver_trade_idea
    (bellomberg.agents.trade_idea), dagli stessi dati salvati della run."""
    from bellomberg.agents.trade_idea import _desk_annex, _memo_facts, _load_market_pack
    from bellomberg.core.research_analysis import RESEARCH_ANALYSIS_MODE
    run = detail["run"]
    progress = detail.get("progress") or {}
    checkpoint = progress.get("checkpoint") or {}
    research = run.get("analysis_mode") == RESEARCH_ANALYSIS_MODE
    return {**run, "identity": {"ticker": run["ticker"], "name": run.get("company_name"),
                                "exchange": run.get("exchange"), "currency": run.get("currency")},
            "cutoff": progress.get("data_cutoff") or run.get("started_at"),
            "desk_annex": _desk_annex(checkpoint.get("data")) if research else None,
            "facts": _memo_facts(checkpoint, progress.get("data_cutoff")) if research else None,
            "market_pack": _load_market_pack((checkpoint.get("data") or {}).get("_market_pack")) if research else None}


def _pdf_consegnato(detail: dict):
    manifest = detail.get("artifacts") or {}
    pdf = next((a for a in manifest.get("artifacts") or [] if a.get("kind") == "pdf"), None)
    return manifest, pdf


def ricontrolla_consegnato(detail: dict, report_run: dict) -> dict:
    """L'ispettore di OGGI sul PDF gia' consegnato (sola lettura), con le pagine-sezione salvate."""
    from bellomberg.reporting.trade_idea_report import inspect_research_pdf
    manifest, pdf = _pdf_consegnato(detail)
    if not pdf:
        return {"esito": "n.d.", "motivo": "nessun PDF nel manifest salvato"}
    path = Path(pdf["path"])
    sections = (manifest.get("pdf_quality") or {}).get("section_pages")
    if not path.is_file():
        return {"esito": "n.d.", "motivo": f"file assente: {path}"}
    if not isinstance(sections, dict):
        return {"esito": "n.d.", "motivo": "pagine-sezione non salvate nel manifest"}
    quality = inspect_research_pdf(path, detail["result"], sections, language=report_run["language"],
                                   execution_policy=report_run.get("execution_policy"),
                                   annex=report_run.get("desk_annex"), facts=report_run.get("facts"),
                                   market=report_run.get("market_pack"))
    return {"esito": quality["status"], "motivi": quality["reasons"], "percorso": str(path)}


def stato_consegna(detail: dict, run_dir: Path) -> dict:
    manifest, pdf = _pdf_consegnato(detail)
    run = detail["run"]
    return {"delivery_json_presente": (run_dir / "delivery.json").exists(),"fase": run.get("phase"), "stato_tecnico": run.get("technical_status"),
            "destinazione": (run.get("destination") or {}).get("kind"),
            "manifest_sigillato": detail.get("artifacts") is not None,
            "integrita_manifest": detail.get("manifest_integrity"),
            "pdf_nel_manifest": (pdf or {}).get("name"), "stato_pdf_nel_manifest": (pdf or {}).get("status"),
            "email": (detail.get("email") or {}).get("status")}


def ostacoli_apply(stato: dict, qualificato: bool) -> list:
    """Cosa impedisce a --apply di consegnare con il percorso normale. Lista vuota = puo' procedere."""
    ostacoli = []
    if not qualificato:
        ostacoli.append("il PDF rigenerato NON e' qualificato: niente consegna")
    if stato["manifest_sigillato"]:
        ostacoli.append("manifest di consegna gia' sigillato nel DB: la consegna normale lo considera "
                        "immutabile (TradeIdeaStore.save_manifest) e non lo riscrive; serve una procedura "
                        "di sostituzione esplicita, che questo script non improvvisa")
    if stato.get("delivery_json_presente"):
        ostacoli.append("delivery.json gia' presente nella cartella della run: la consegna normale RECUPERA "
                        "quel pacchetto (il PDF vecchio), non ne prepara uno nuovo")
    if stato["email"] in ("accepted", "uncertain", "sending"):
        ostacoli.append(f"email gia' in stato «{stato['email']}»: non si rimanda")
    return ostacoli


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run_id")
    parser.add_argument("--apply", action="store_true", help="consegna con il percorso normale (scrive il DB)")
    parser.add_argument("--db", help="percorso del DB (predefinito: quello del progetto)")
    parser.add_argument("--out", help="cartella della prova (predefinita: una cartella temporanea nuova)")
    parser.add_argument("--report-dir", help="cartella della consegna della run (predefinita: report/trade_ideas/<run_id>)")
    parser.add_argument("--si", action="store_true", help="con --apply: conferma senza domanda")
    args = parser.parse_args(argv)

    from bellomberg.core.paths import SQLITE_PATH
    from bellomberg.reporting.trade_idea_report import build_trade_idea_report
    from bellomberg.storage.trade_idea_store import TradeIdeaStore

    db_path = Path(args.db or SQLITE_PATH).resolve()
    prima = _impronta_file(db_path)
    # init e get_run aprono il DB in mode=ro. Nella prova il mandato del PM non si legge: serve
    # solo al confronto di contesto per RIPRENDERE una run, non al PDF (dichiarato nell'output).
    store = (TradeIdeaStore(db_path) if args.apply else
             TradeIdeaStore(db_path, mandate_loader=lambda: "non-letto-nella-prova"))
    detail = store.get_run(args.run_id)
    if detail.get("result") is None:
        print("ESITO: KO — la run non ha un risultato strutturato salvato: niente da rigenerare.")
        return 2
    if detail.get("manifest_integrity") is False:
        print("ESITO: KO — il manifest salvato non corrisponde al suo hash: nessuna rigenerazione.")
        return 2
    report_run = run_for_report(detail)
    manifest, _ = _pdf_consegnato(detail)
    valuations = manifest.get("valuations") or []

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="rigenera_pdf_"))
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = build_trade_idea_report(report_run, detail["result"], output_path=out_dir / "trade-idea.pdf",
                                  valuations=valuations, language=report_run["language"])
    qualificato = pdf["status"] == "ready"
    from bellomberg.core.paths import REPORT_DIR
    run_dir = Path(args.report_dir) if args.report_dir else REPORT_DIR / "trade_ideas" / args.run_id
    stato = stato_consegna(detail, run_dir)
    vecchio = ricontrolla_consegnato(detail, report_run)
    dopo = _impronta_file(db_path)

    print(f"RUN {args.run_id} ({report_run['ticker']})")
    print("Stato della consegna nel DB:", json.dumps(stato, ensure_ascii=False))
    if not args.apply:
        print("  (prova: mandato del PM NON letto; il confronto di contesto per riprendere la run non e' valutato)")
    print(f"PDF rigenerato (prova): {pdf['path']}")
    print(f"  pagine: {pdf['quality'].get('total_pages')} | sha256: {pdf['sha256']}")
    print(f"  QUALIFICATO: {'SI' if qualificato else 'NO'}" + ("" if qualificato else " — " + pdf["reason"]))
    print(f"PDF gia' consegnato, ricontrollato con l'ispettore di oggi: {vecchio['esito']}"
          + (" — " + "; ".join(vecchio.get("motivi") or []) if vecchio.get("motivi") else "")
          + (" — " + vecchio["motivo"] if vecchio.get("motivo") else ""))
    print("DB invariato durante la prova (dimensione e data di db/-wal/-shm):",
          "SI" if prima == dopo else f"NO — prima {prima} dopo {dopo}")
    ostacoli = ostacoli_apply(stato, qualificato)
    print("--apply potrebbe consegnare:", "SI" if not ostacoli else "NO")
    for item in ostacoli:
        print("  -", item)
    if prima != dopo and not args.apply:
        # Un altro processo puo' scrivere il DB in parallelo (backend, scheduler): dichiarato, non nascosto.
        print("ATTENZIONE: il DB e' cambiato durante la prova; questo script lo apre solo in lettura, "
              "verifica se un altro processo era attivo.")
    if not args.apply:
        return 0 if qualificato else 1

    # ---- --apply: solo il percorso normale della consegna, con le sue guardie ----
    if ostacoli:
        print("APPLY: NON eseguito.")
        return 3
    if _backend_acceso():
        print("APPLY: NON eseguito — backend acceso sulla 8765: chiudilo prima di scrivere il DB.")
        return 3
    if not args.si:
        risposta = input(f"Consegnare la run {args.run_id} e inviare l'email (una sola volta)? Scrivi SI: ")
        if risposta.strip() != "SI":
            print("APPLY: annullato.")
            return 3
    from bellomberg.agents.trade_idea import deliver_trade_idea
    email = deliver_trade_idea(store, args.run_id, send_email=True)
    print("APPLY: consegna eseguita. Email:", json.dumps(email, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
