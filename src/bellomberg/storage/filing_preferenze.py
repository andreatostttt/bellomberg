"""Scelte dell'utente sul controllo filing: titoli esclusi, collegamenti SEC/ESEF rifiutati,
esiti dell'ultima attivazione, controllo giornaliero, proposte AI scartate, collegamenti annullati.

Chiavi obbligatorie: `esclusi`, `rifiutati`. Chiavi opzionali (fase E, assenti nei file
vecchi): `esiti`, `rifiutati_lei`, `controllo_giornaliero`, `proposte_scartate`, `scollegati`.

File JSON in DATA_DIR (scrittura atomica). Un file illeggibile e' un errore
dichiarato: mai sovrascritto in silenzio con preferenze vuote.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _path(path=None):
    if path is not None:
        return Path(path)
    from bellomberg.core.paths import DATA_DIR
    return Path(DATA_DIR) / "filing_preferenze.json"


_OPZIONALI = {"esiti": dict, "rifiutati_lei": dict, "controllo_giornaliero": (bool, type(None)),
              "proposte_scartate": list, "scollegati": dict}
MAX_PROPOSTE_SCARTATE = 200


def carica(path=None):
    p = _path(path)
    if not p.is_file():
        return {"esclusi": [], "rifiutati": {}}
    try:
        dati = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"preferenze filing illeggibili ({p}): {exc}") from exc
    if not isinstance(dati, dict) or not isinstance(dati.get("esclusi"), list) \
            or not isinstance(dati.get("rifiutati"), dict):
        raise ValueError(f"preferenze filing non valide ({p})")
    for chiave, tipo in _OPZIONALI.items():
        if chiave in dati and not isinstance(dati[chiave], tipo):
            raise ValueError(f"preferenze filing non valide ({p}): {chiave}")
    return dati


def esclusi_sicuri(path=None):
    """Titoli esclusi dal controllo. Preferenze illeggibili: ValueError (seguito revisione G1).

    Prima diventavano «nessuna esclusione» scritta solo nel log, e pre-run e giro orario
    scaricavano anche i titoli esclusi dal PM: chi chiama salta l'aggiornamento e lo dichiara."""
    try:
        return frozenset(carica(path).get("esclusi") or [])
    except OSError as exc:
        raise ValueError(f"preferenze filing illeggibili ({exc})") from exc


def salva(dati, path=None):
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def imposta_escluso(ticker, escluso, path=None):
    dati = carica(path)
    esclusi = set(dati["esclusi"])
    if escluso:
        esclusi.add(ticker)
    else:
        esclusi.discard(ticker)
    dati["esclusi"] = sorted(esclusi)
    salva(dati, path)
    return dati


def rifiuta(ticker, cik, path=None):
    dati = carica(path)
    dati["rifiutati"][ticker] = sorted(set(dati["rifiutati"].get(ticker, [])) | {str(cik).zfill(10)})
    salva(dati, path)
    return dati


def _adesso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def esiti(dati):
    return dict(dati.get("esiti") or {})


def registra_esito(ticker, esito, motivo=None, candidati=None, path=None):
    """Ultimo esito dell'attivazione (o del rifiuto/annullamento) di `ticker`."""
    dati = carica(path)
    dati["esiti"] = {**esiti(dati), ticker: {"esito": esito, "motivo": None if motivo is None else str(motivo)[:300],
                                             "candidati": candidati, "at": _adesso()}}
    salva(dati, path)
    return dati


def rifiutati_lei(dati, ticker):
    return frozenset((dati.get("rifiutati_lei") or {}).get(ticker) or [])


def rifiuta_lei(ticker, lei, path=None):
    dati = carica(path)
    attuali = dict(dati.get("rifiutati_lei") or {})
    attuali[ticker] = sorted(set(attuali.get(ticker) or []) | {str(lei).upper()})
    dati["rifiutati_lei"] = attuali
    salva(dati, path)
    return dati


def controllo_giornaliero(path=None):
    """Scelta salvata dell'interruttore: True / False / None (mai scelto). Illeggibili: ValueError."""
    valore = carica(path).get("controllo_giornaliero")
    return valore if isinstance(valore, bool) else None


def imposta_controllo_giornaliero(attivo, path=None):
    if type(attivo) is not bool:
        raise ValueError("controllo_giornaliero deve essere booleano")
    dati = carica(path)
    dati["controllo_giornaliero"] = attivo
    salva(dati, path)
    return dati


def proposte_scartate(dati):
    return frozenset(dati.get("proposte_scartate") or [])


def scarta_proposta(sha256, path=None):
    """La proposta AI non e' piu' «in sospeso» (resta in cache). Tetto: le ultime 200."""
    dati = carica(path)
    lista = [s for s in dati.get("proposte_scartate") or [] if s != sha256] + [sha256]
    dati["proposte_scartate"] = lista[-MAX_PROPOSTE_SCARTATE:]
    salva(dati, path)
    return dati


def scollegati(dati):
    return dict(dati.get("scollegati") or {})


def imposta_scollegato(ticker, versione, path=None):
    """Collegamento annullato: vale per la versione del profilo disattivata (`versione`)."""
    dati = carica(path)
    dati["scollegati"] = {**scollegati(dati), ticker: int(versione)}
    salva(dati, path)
    return dati


def annulla_scollegato(ticker, path=None):
    dati = carica(path)
    if ticker in scollegati(dati):
        dati["scollegati"] = {k: v for k, v in scollegati(dati).items() if k != ticker}
        salva(dati, path)
    return dati


def e_scollegato(dati, ticker, versione):
    """Il profilo `versione` e' quello disattivato da «Scollega» (una versione successiva lo ricollega)."""
    salvata = scollegati(dati).get(ticker)
    return salvata is not None and versione is not None and int(versione) <= int(salvata)
