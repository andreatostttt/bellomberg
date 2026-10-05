# -*- coding: utf-8 -*-
"""proponi_gemelli_usa.py — propone, conferma o rifiuta il GEMELLO USA di un simbolo non-USA del
book (04/10, Opus 5.5, voce 0-quater). Unico scrittore di `data/gemelli_usa.json`
(storage.negozi_privati.carica_gemelli; lettore: market_data.lookthrough_usa).

Il gemello NON si indovina: il simbolo USA omonimo e' di solito un altro strumento (BA.L -> BA
= Boeing). Lo script non cerca nulla in rete e non deduce nulla dal simbolo: l'evidenza
(KID, factsheet, 20-F, 13F) la scrive CHI PROPONE, il PM conferma UNA volta.

    python tools/ops/proponi_gemelli_usa.py
        -> le posizioni aperte NON-USA del book (DB in sola lettura) senza voce, le voci per
           stato, e il modello di voce da compilare
    python tools/ops/proponi_gemelli_usa.py --proponi T --gemello U --relazione stesso_indice
           --evidenza "..." --fonte "..." --usi opzioni,notizie [--note "..."]
    python tools/ops/proponi_gemelli_usa.py --proponi T --relazione partecipazioni
           --partecipazione AAA=12.5 --partecipazione BBB=8 --data-riferimento 2026-08-31
           --fonte "factsheet ..." --usi congress
    python tools/ops/proponi_gemelli_usa.py --conferma T --tramite "istruzione PM in chat del GG/MM, registrata da Claude"
    python tools/ops/proponi_gemelli_usa.py --rifiuta T --motivo "..."
    ... --apply        scrive (default: dry-run, nessuna scrittura)
    --dest FILE / --db FILE   un altro negozio / un altro DB (prove)

Ogni voce nuova passa dalla STESSA validazione del caricatore (negozi_privati.valida_voce_gemello)
prima di scrivere; la scrittura e' atomica (temporaneo + os.replace) con backup del precedente,
e il negozio si RILEGGE col caricatore: l'esito stampato e' una misura (conteggio voci prima/dopo
e voce riletta uguale a quella voluta). Il negozio e' un JSON letto a ogni chiamata dai
consumatori: nessun DB scritto, nessun riavvio del backend necessario.

Exit: 0 = fatto (o dry-run valido) · 1 = operazione RIFIUTATA (voce non valida, stato sbagliato,
simbolo fuori book) · 2 = KO dello strumento (negozio illeggibile, DB non leggibile, negozio
scritto che non si rilegge come voluto).
"""
import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RADICE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (RADICE, os.path.join(RADICE, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bellomberg.storage.negozi_privati as np_  # noqa: E402
from bellomberg.core.paths import SQLITE_PATH  # noqa: E402
from bellomberg.market_data.mercati import USA, mercato_di  # noqa: E402

LEGGIMI = ("Negozio privato dei gemelli USA, scritto da tools/ops/proponi_gemelli_usa.py. La forma e' in "
           "src/bellomberg/resources/examples/gemelli_usa.example.json. I desk leggono SOLO le voci confermate.")

MODELLO = {
    "stato": "proposto",
    "relazione": "stesso_indice | adr | partecipazioni",
    "gemello_usa": {"simbolo": "<SIMBOLO USA>",
                    "evidenza": "<la riga del KID/factsheet/20-F che lo prova, scritta da te>",
                    "fonte": "<documento e data>"},
    "partecipazioni": {"fonte": "<factsheet/13F>", "data_riferimento": "AAAA-MM-GG",
                       "voci": [{"simbolo_usa": "<SIMBOLO>", "peso_pct": 0.0}]},
    "usi_ammessi": list(np_.USI_GEMELLO),
    "proposto_il": "AAAA-MM-GG",
}


def oggi() -> str:
    return _dt.date.today().isoformat()


def non_usa(ticker: str) -> bool:
    """Fuori dagli USA e non crypto 24/7 (mercato_di -> None): i candidati a un gemello."""
    m = mercato_di(ticker)
    return m is not None and m != USA


def posizioni_aperte(db: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """(ticker aperti, None) oppure (None, motivo). DB SEMPRE in sola lettura."""
    if not os.path.exists(db):
        return None, "DB non trovato: %s" % db
    try:
        uri = "file:%s?mode=ro" % os.path.abspath(db).replace("\\", "/")
        con = sqlite3.connect(uri, uri=True)
        try:
            righe = con.execute("SELECT DISTINCT ticker FROM positions WHERE is_active = 1").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return None, "DB non leggibile (%s)" % type(e).__name__
    return sorted({str(r[0]).strip().upper() for r in righe if r[0]}), None


def leggi_grezzo(dest: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any], Optional[str]]:
    """(grezzo, esito del caricatore, motivo del KO). Negozio assente = grezzo nuovo con la nota;
    illeggibile = KO: non si scrive sopra un negozio che non si capisce."""
    esito = np_.carica_gemelli(dest)
    if esito["origine"] == "illeggibile":
        return None, esito, "negozio %s illeggibile: %s — correggilo a mano prima" % (dest, esito["motivo"])
    if esito["origine"] == "assente":
        return {"_leggimi": LEGGIMI}, esito, None
    with open(dest, encoding="utf-8") as fh:
        return json.load(fh), esito, None


def scrivi_atomico(dest: str, testo: str) -> Optional[str]:
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    bak = None
    if os.path.exists(dest):
        bak = dest + ".bak-" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")   # microsecondi: due --apply ravvicinati non si sovrascrivono il backup (review RV-C)
        with open(dest, "rb") as src, open(bak, "wb") as dst:
            dst.write(src.read())
        print("backup del negozio precedente: %s" % bak)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(dest) + ".", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(dest)))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(testo)
    for tentativo in range(5):
        try:
            os.replace(tmp, dest)
            return bak
        except PermissionError:
            if tentativo == 4:
                raise
            time.sleep(0.2 * (tentativo + 1))
    return bak


def _partecipazioni(coppie: List[str], data_rif: Optional[str], fonte: Optional[str]) -> Dict[str, Any]:
    voci = []
    for x in coppie:
        if "=" not in x:
            raise ValueError("--partecipazione %r: serve la forma SIMBOLO=PESO" % x)
        s, p = x.split("=", 1)
        try:
            peso = float(p.strip().replace(",", "."))
        except ValueError:
            raise ValueError("--partecipazione %r: peso non numerico" % x)
        voci.append({"simbolo_usa": s.strip(), "peso_pct": peso})
    return {"fonte": fonte, "data_riferimento": data_rif, "voci": voci}


def voce_proposta(a) -> Dict[str, Any]:
    """La voce GREZZA che l'utente ha scritto: la validazione la fa il caricatore, non qui."""
    voce: Dict[str, Any] = {"stato": "proposto", "relazione": a.relazione}
    if a.gemello is not None:
        voce["gemello_usa"] = {"simbolo": a.gemello, "evidenza": a.evidenza, "fonte": a.fonte}
    if a.partecipazione:
        voce["partecipazioni"] = _partecipazioni(a.partecipazione, a.data_riferimento, a.fonte)
    voce["usi_ammessi"] = [u.strip() for u in (a.usi or "").split(",") if u.strip()]
    voce["proposto_il"] = oggi()
    if a.note:
        voce["note"] = a.note
    return voce


def elenco(a) -> int:
    grezzo, esito, ko = leggi_grezzo(a.dest)
    if ko:
        print("KO: " + ko)
        return 2
    if esito["origine"] == "assente":
        print("negozio %s assente: lo creera' la prima --proponi ... --apply" % a.dest)
    gemelli = esito["gemelli"]
    aperte, motivo = posizioni_aperte(a.db)
    if aperte is None:
        print("KO: %s — non so quali posizioni del book mancano di una voce" % motivo)
        return 2
    candidati = [t for t in aperte if non_usa(t)]
    crypto = [t for t in aperte if mercato_di(t) is None]
    senza = [t for t in candidati if t not in gemelli]
    try:
        from bellomberg.storage.classificazione import carica_veicoli
        veicoli = carica_veicoli()
    except Exception as e:  # il tipo e' contesto, non blocca: lo si dichiara
        veicoli = {"veicoli": {}, "origine": "illeggibile", "motivo": type(e).__name__}
    print("posizioni aperte: %d · non-USA: %d · crypto 24/7 escluse: %d" % (len(aperte), len(candidati), len(crypto)))
    if veicoli["origine"] in ("assente", "illeggibile"):
        print("(tipo strumento n.d.: negozio veicoli %s — %s)" % (veicoli["origine"], veicoli["motivo"]))
    print("\nNON-USA SENZA VOCE (%d):" % len(senza))
    for t in senza:
        tipo = (veicoli["veicoli"].get(t) or {}).get("tipo", "n.d.")
        print("  %-12s tipo=%s" % (t, tipo))
    for stato in np_.STATI_GEMELLO:
        voci = sorted(t for t, v in gemelli.items() if v["stato"] == stato)
        print("\n%s (%d): %s" % (stato.upper(), len(voci), ", ".join(voci) or "-"))
    fuori = sorted(t for t in gemelli if t not in aperte)
    if fuori:
        print("\nvoci di simboli NON piu' aperti nel book (restano, dichiarate): %s" % ", ".join(fuori))
    print("\nMODELLO DI VOCE (l'evidenza la scrivi TU, dal documento dell'emittente; mai dal simbolo):")
    print(json.dumps(MODELLO, ensure_ascii=False, indent=1))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    op = ap.add_mutually_exclusive_group()
    op.add_argument("--proponi", metavar="TICKER", help="crea una voce 'proposto'")
    op.add_argument("--conferma", metavar="TICKER", help="proposto -> confermato (data della macchina)")
    op.add_argument("--rifiuta", metavar="TICKER", help="-> rifiutato (serve --motivo)")
    ap.add_argument("--gemello", help="simbolo USA del gemello (relazioni stesso_indice/adr)")
    ap.add_argument("--relazione", choices=np_.RELAZIONI_GEMELLO)
    ap.add_argument("--evidenza", help="la riga del documento che prova il legame (la scrivi tu)")
    ap.add_argument("--fonte", help="documento e data dell'evidenza / delle partecipazioni")
    ap.add_argument("--partecipazione", action="append", default=[], metavar="SIMBOLO=PESO",
                    help="partecipazione USA e peso %% (ripetibile)")
    ap.add_argument("--data-riferimento", dest="data_riferimento", help="data delle partecipazioni, AAAA-MM-GG")
    ap.add_argument("--usi", help="usi ammessi separati da virgola: " + ",".join(np_.USI_GEMELLO))
    ap.add_argument("--note")
    ap.add_argument("--motivo", help="perche' il PM rifiuta (obbligatorio con --rifiuta)")
    ap.add_argument("--tramite", help="COME e' arrivata la conferma del PM, es. 'istruzione PM in chat "
                                      "del GG/MM, registrata da Claude' (obbligatorio con --conferma)")
    ap.add_argument("--sostituisci", action="store_true", help="--proponi su un simbolo che ha gia' una voce")
    ap.add_argument("--anche-fuori-book", dest="fuori_book", action="store_true",
                    help="--proponi su un simbolo che non e' una posizione aperta")
    ap.add_argument("--apply", action="store_true", help="scrive il negozio (default: dry-run)")
    ap.add_argument("--dest", default=np_.PERCORSO_GEMELLI, help="percorso del negozio")
    ap.add_argument("--db", default=str(SQLITE_PATH), help="DB del book (letto in sola lettura)")
    a = ap.parse_args(argv)

    if not (a.proponi or a.conferma or a.rifiuta):
        return elenco(a)

    grezzo, esito, ko = leggi_grezzo(a.dest)
    if ko:
        print("KO: " + ko)
        return 2
    gemelli = esito["gemelli"]
    t = (a.proponi or a.conferma or a.rifiuta).strip().upper()
    prima = grezzo.get(t)

    if a.proponi:
        if prima is not None and not a.sostituisci:
            print("RIFIUTATO: %s ha gia' una voce (%s): --sostituisci per riscriverla" % (t, gemelli[t]["stato"]))
            return 1
        if not a.fuori_book:
            aperte, motivo = posizioni_aperte(a.db)
            if aperte is None:
                print("KO: %s — non posso verificare che %s sia nel book (--anche-fuori-book per saltare)" % (motivo, t))
                return 2
            if t not in aperte:
                print("RIFIUTATO: %s non e' una posizione aperta del book (refuso? --anche-fuori-book se voluto)" % t)
                return 1
        try:
            nuova = voce_proposta(a)
        except ValueError as e:
            print("RIFIUTATO: %s" % e)
            return 1
    elif a.conferma:
        if prima is None or gemelli[t]["stato"] != "proposto":
            print("RIFIUTATO: si conferma solo una voce 'proposto'; %s e' %s"
                  % (t, gemelli[t]["stato"] if prima is not None else "senza voce"))
            return 1
        if not (a.tramite or "").strip():
            print("RIFIUTATO: --conferma vuole --tramite (come e' arrivata la conferma del PM: l'etichetta PROXY lo cita)")
            return 1
        nuova = dict(prima, stato="confermato", confermato_il=oggi(), confermato_tramite=a.tramite.strip())
    else:
        if not (a.motivo or "").strip():
            print("RIFIUTATO: --rifiuta vuole --motivo (il perche' resta nel negozio)")
            return 1
        if prima is not None and gemelli[t]["stato"] == "rifiutato":
            print("RIFIUTATO: %s e' gia' rifiutato" % t)
            return 1
        base = dict(prima) if prima is not None else {"proposto_il": oggi()}
        nuova = dict(base, stato="rifiutato", rifiutato_il=oggi(), motivo_rifiuto=a.motivo.strip())

    # la STESSA regola del caricatore, prima di scrivere: nessuna voce che il lettore rifiuterebbe
    try:
        validata = np_.valida_voce_gemello(t, nuova)
    except ValueError as e:
        print("RIFIUTATO: %s" % e)
        return 1

    print("%s %s — voce:" % ("PROPOSTA" if a.proponi else "CONFERMA" if a.conferma else "RIFIUTO", t))
    print(json.dumps(nuova, ensure_ascii=False, indent=1))
    if not a.apply:
        print("\ndry-run: nessuna scrittura in %s. Per scrivere: --apply" % a.dest)
        return 0

    n_prima = len(gemelli)
    grezzo[t] = nuova
    bak = scrivi_atomico(a.dest, json.dumps(grezzo, ensure_ascii=False, indent=1) + "\n")
    riletto = np_.carica_gemelli(a.dest)
    atteso = n_prima + (0 if prima is not None else 1)
    if riletto["motivo"] is not None or len(riletto["gemelli"]) != atteso \
            or riletto["gemelli"].get(t) != validata:
        print("KO: il negozio scritto non si rilegge come voluto (%s; voci %d, attese %d) — ripristina da %s"
              % (riletto["motivo"], len(riletto["gemelli"]), atteso, bak))
        return 2   # KO dello strumento, non un rifiuto (review RV-C)
    print("\nscritto %s — riletto col caricatore: %d voci (prima %d), %s in stato %s"
          % (a.dest, len(riletto["gemelli"]), n_prima, t, riletto["gemelli"][t]["stato"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
