"""
Cache CONDIVISA dei risultati grezzi GNews (Opus 5.5 04/10, contratto C-G recon R1 §7.1).

Perche': il limiter news (news_aggregator.NEWS_PROVIDER_LIMITS["gnews"]) mette ogni query
in cooldown 2h. Senza cache, la SECONDA chiamata della stessa query entro 2h (un altro
specialista, il feed delle :15, il tool degli agenti) tornava SKIP_COOLDOWN e lista vuota:
GNews, pagato (piano Essential), era quasi muto per gli agenti per un difetto NOSTRO.
Qui si salvano gli `articles` GREZZI dell'API (i due fetcher, news_sources e
news_aggregator, li mappano in forme diverse: ognuno mappa in lettura) e si servono a chi
trova la query in cooldown. Chi li serve li dichiara con stato "cache", non "live".

Limiti DICHIARATI:
- Eta' su WALL-CLOCK salvato nel file (`ts` = time.time() alla scrittura): il monotonic non
  attraversa i processi (backend, scheduler, run del comitato). Un passo indietro
  dell'orologio (NTP) produce un'eta' negativa: la voce NON si serve e l'anomalia si
  dichiara in `stato_ultimo_errore()`; un passo avanti fa scadere la voce prima (innocuo).
- Concorrenza fra PROCESSI: scrittura atomica (tmp + os.replace), ma read-modify-write
  senza lock di file: due processi che scrivono insieme -> vince l'ultimo, l'altra voce si
  perde (costo: una chiamata GNews in piu' alla prossima scadenza, nessun dato falso).
  Dentro un processo un threading.Lock serializza le scritture.
- Cache ASSENTE (file mai scritto) non e' un errore: e' "niente in cache". Cache
  ILLEGGIBILE o NON SCRIVIBILE e' un errore e si dichiara (log + stato_ultimo_errore()),
  mai zitta. Nei log si scrive il TIPO dell'eccezione, mai il testo.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bellomberg.core.paths import DATA_DIR

# Ridirigibile nei test: le funzioni leggono il nome del modulo a OGNI chiamata.
GNEWS_CACHE_PATH = DATA_DIR / "gnews_cache.json"
# = cooldown gnews di news_aggregator.NEWS_PROVIDER_LIMITS (non importato: news_aggregator
# e' pesante e importa news_sources; l'uguaglianza la tiene un test).
TTL_S = 2 * 3600
MAX_VOCI = 200
_TENTATIVI_PERMESSO = 5      # PermissionError su Windows: lettore/scrittore concorrente
_PAUSA_TENTATIVO_S = 0.05

_lock = threading.Lock()
# Ultimo errore per operazione: ognuna si azzera solo col proprio successo.
_errori: Dict[str, Optional[str]] = {"leggi": None, "scrivi": None}


def _log(testo: str) -> None:
    try:
        print("  [GNEWS-CACHE] " + testo)
    except (OSError, ValueError):
        pass


def _segna_errore(operazione: str, testo: str) -> None:
    quando = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _errori[operazione] = testo + " (alle " + quando + ")"
    _log(operazione + ": " + testo)


def chiave(query: str, lang: str = "en") -> str:
    """Chiave di cache: lingua + stessa normalizzazione della query di
    news_aggregator.provider_status (lower, strip, primi 80 caratteri)."""
    return str(lang or "en").lower().strip() + "|" + str(query or "").lower().strip()[:80]


def _leggi_file() -> Optional[Dict[str, Any]]:
    """Contenuto del file, {} se assente; None se ILLEGGIBILE (errore gia' dichiarato)."""
    percorso = str(GNEWS_CACHE_PATH)
    ultimo = None
    for i in range(_TENTATIVI_PERMESSO):
        try:
            with open(percorso, "r", encoding="utf-8") as f:
                dati = json.load(f)
            break
        except FileNotFoundError:
            return {}
        except PermissionError as e:
            ultimo = e
            time.sleep(_PAUSA_TENTATIVO_S * (i + 1))
        except (OSError, ValueError) as e:
            _segna_errore("leggi", "cache illeggibile: " + type(e).__name__)
            return None
    else:
        _segna_errore("leggi", "cache bloccata dopo %d tentativi: %s"
                      % (_TENTATIVI_PERMESSO, type(ultimo).__name__))
        return None
    if not isinstance(dati, dict) or not isinstance(dati.get("voci", {}), dict):
        _segna_errore("leggi", "cache di forma inattesa: " + type(dati).__name__)
        return None
    return dati


def leggi(query: str, lang: str = "en", ora: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """{"articles": [grezzi GNews], "salvato_il": iso, "eta_s": float} se la query e' in
    cache da meno di TTL_S; None se assente, scaduta o illeggibile. Per distinguere
    "illeggibile" da "assente" chiedere stato_ultimo_errore()."""
    dati = _leggi_file()
    if dati is None:
        return None
    _errori["leggi"] = None
    voce = (dati.get("voci") or {}).get(chiave(query, lang))
    if voce is None:
        return None
    try:
        ts = float(voce["ts"])
        articoli = voce["articles"]
        if not isinstance(articoli, list):
            raise TypeError("articles non lista")
    except (KeyError, TypeError, ValueError) as e:
        _segna_errore("leggi", "voce di forma inattesa: " + type(e).__name__)
        return None
    adesso = time.time() if ora is None else float(ora)
    eta = adesso - ts
    if eta < 0:
        _segna_errore("leggi", "eta' negativa (%.0f s): orologio tornato indietro, voce non servita"
                      % eta)
        return None
    if eta >= TTL_S:
        return None
    return {"articles": list(articoli), "salvato_il": str(voce.get("salvato_il", "")), "eta_s": eta}


def _scrivi_file(dati: Dict[str, Any]) -> bool:
    percorso = str(GNEWS_CACHE_PATH)
    tmp = "%s.%d.%d.tmp" % (percorso, os.getpid(), threading.get_ident())
    try:
        os.makedirs(os.path.dirname(percorso) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dati, f)
    except (OSError, ValueError, TypeError) as e:
        _segna_errore("scrivi", "cache non scrivibile: " + type(e).__name__)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    ultimo = None
    for i in range(_TENTATIVI_PERMESSO):
        try:
            os.replace(tmp, percorso)
            return True
        except PermissionError as e:
            ultimo = e
            time.sleep(_PAUSA_TENTATIVO_S * (i + 1))
        except OSError as e:
            ultimo = e
            break
    _segna_errore("scrivi", "cache non sostituita dopo i tentativi: " + type(ultimo).__name__)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def scrivi(query: str, lang: str, articles: List[Dict[str, Any]], ora: Optional[float] = None) -> None:
    """Salva gli articoli GREZZI di una risposta 200. Non solleva mai: un guasto si
    dichiara in stato_ultimo_errore() e nel log. Pota le voci scadute e tiene le
    MAX_VOCI piu' recenti."""
    if not isinstance(articles, list):
        _segna_errore("scrivi", "articles non lista: " + type(articles).__name__)
        return
    adesso = time.time() if ora is None else float(ora)
    with _lock:
        dati = _leggi_file()
        if dati is None:
            # illeggibile (gia' dichiarato in 'leggi'): si riparte da vuoto, e lo si dice
            _log("scrivi: cache illeggibile sovrascritta da zero")
            dati = {}
        voci = dict(dati.get("voci") or {})
        voci[chiave(query, lang)] = {
            "articles": articles,
            "ts": adesso,
            "salvato_il": datetime.fromtimestamp(adesso, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        def _ts(v: Any) -> float:
            try:
                return float(v.get("ts", 0))
            except (AttributeError, TypeError, ValueError):
                return 0.0

        vive = {k: v for k, v in voci.items() if 0 <= adesso - _ts(v) < TTL_S}
        if len(vive) > MAX_VOCI:
            tenute = sorted(vive, key=lambda k: _ts(vive[k]), reverse=True)[:MAX_VOCI]
            vive = {k: vive[k] for k in tenute}
        if _scrivi_file({"versione": 1, "voci": vive}):
            _errori["scrivi"] = None


def stato_ultimo_errore() -> Optional[str]:
    """None = nessun guasto pendente in QUESTO processo. Altrimenti il motivo (lettura e/o
    scrittura), da DICHIARARE dal chiamante: una cache rotta non e' "niente in cache"."""
    parti = [op + ": " + t for op, t in _errori.items() if t]
    return " | ".join(parti) if parti else None


def reset_errori() -> None:
    """Per i test."""
    for k in _errori:
        _errori[k] = None
