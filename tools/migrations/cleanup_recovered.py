"""
cleanup_recovered.py — Entry point legacy DISABILITATO.

La selezione automatica dell'ultimo memo e la rimozione globale RECOVERED non
rispettano stato/consegna, riferimenti e registri immutabili del runtime attuale.

USO:
  Le invocazioni senza argomenti e con --apply sono entrambe rifiutate, senza
  selezionare memo o aprire il DB. Non esiste un nuovo workflow di cancellazione.
  Gli strumenti nativi di recovery senza cancellazione restano invariati.
"""
import os, sys, sqlite3

os.environ.setdefault("BELLOMBERG_PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from bellomberg.core.paths import PROJECT_ROOT, RESEARCH_NOTES_DIR, SQLITE_PATH
DB = str(SQLITE_PATH)


def main(apply=False):
    raise SystemExit("cleanup_recovered disabilitato: selezione implicita dell'ultimo memo e "
                     "cancellazione legacy non sicure. Nessun memo selezionato; DB non aperto. "
                     "Nessuna nuova procedura di cancellazione autorizzata.")
    # Corpo storico irraggiungibile: nessun flag puo' riattivarlo.
    if not os.path.exists(DB):
        print("DB non trovato:", DB); return
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    memo = conn.execute("SELECT * FROM memos ORDER BY id DESC LIMIT 1").fetchone()
    if not memo:
        print("Nessun memo nel DB."); return
    mid = memo["id"]
    n_dec = conn.execute("SELECT COUNT(*) c FROM decisions WHERE memo_id=?", (mid,)).fetchone()["c"]
    n_rep = conn.execute("SELECT COUNT(*) c FROM specialist_reports WHERE memo_id=?", (mid,)).fetchone()["c"]
    try:
        n_fb = conn.execute("SELECT COUNT(*) c FROM pm_feedback WHERE memo_id=?", (mid,)).fetchone()["c"]
    except Exception:
        n_fb = 0
    files = [memo["pdf_path"], memo["appendix_path"]]
    files = [f if os.path.isabs(f) else os.path.join(PROJECT_ROOT, f) for f in files if f]

    print("=" * 60)
    print(f"Memo da cancellare: #{mid}  '{memo['title']}'  ({memo['timestamp']})")
    print(f"  - decisioni collegate:        {n_dec}")
    print(f"  - report specialisti:         {n_rep}")
    print(f"  - feedback PM:                {n_fb}")
    print(f"  - file PDF collegati:         {files or 'nessuno'}")
    print("=" * 60)

    if not apply:
        print("\nPercorso legacy disabilitato.")
        conn.close(); return

    with conn:
        conn.execute("DELETE FROM decisions WHERE memo_id=?", (mid,))
        conn.execute("DELETE FROM specialist_reports WHERE memo_id=?", (mid,))
        try:
            conn.execute("DELETE FROM pm_feedback WHERE memo_id=?", (mid,))
        except Exception:
            pass
        conn.execute("DELETE FROM memos WHERE id=?", (mid,))
    # file fisici (best-effort)
    removed = []
    for f in files:
        try:
            if f and os.path.exists(f):
                os.remove(f); removed.append(f)
        except Exception as e:
            print("  [!] non rimosso", f, ":", e)
    # markdown di recupero
    nd = str(RESEARCH_NOTES_DIR)
    if os.path.isdir(nd):
        for fn in os.listdir(nd):
            if fn.startswith("RECOVERED_"):
                try:
                    os.remove(os.path.join(nd, fn)); removed.append(fn)
                except Exception:
                    pass
    conn.close()
    print(f"\nFATTO. Cancellato memo #{mid}, {n_dec} decisioni, {n_rep} report.")
    print("File rimossi:", removed or "nessuno")
    print("Domani la run nuova ripartira' pulita.")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
