"""Loghi dei titoli, risolti a runtime dal profilo dell'emittente, scaricati una volta.

Perche' (02/10/2026, rivisto 04/10): i marchi di `simple-icons` coprono pochi
titoli; gli altri mostravano le iniziali. Nessun elenco di titoli nel codice: il
logo si trova dal profilo dell'emittente, in quest'ordine:

1. Finnhub `/stock/profile2` (campo `logo`) per un simbolo USA: il ticker stesso o
   l'ADR dichiarato in `alias_fonti.json` (sezione `finnhub`), mai dedotto.
2. Il SITO dell'emittente (dominio dal profilo Finnhub `weburl`, altrimenti dal
   profilo Yahoo `website`): `apple-touch-icon.png`, poi `favicon.ico`, scaricati
   solo da quel dominio (o dal suo `www.`), in https, senza redirect.

- Il file scaricato resta in `<DATA_DIR>/loghi/<TICKER>.<ext>`: nessuna seconda
  richiesta finche' esiste. `fonti[TICKER]` dice da dove viene.
- Un'assenza CERTA (nessun simbolo USA e nessun sito, profilo senza logo, 403/404,
  file non immagine) va in `_assenti.json` e si riprova dopo 7 giorni; un guasto di
  passaggio (429, rete) non si ricorda e si riprova alla prossima lettura. In
  entrambi i casi `motivi[TICKER]` lo dichiara: la UI mostra l'iniziale.
- Solo immagini, al massimo 300 KB. Il renderer lo riceve come `data:` URL perche'
  la CSP di produzione non ammette immagini da http://127.0.0.1.
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from bellomberg.core import paths
from bellomberg.market_data import finnhub_news

GIORNI_RIPROVA_ASSENTI = 7
MAX_BYTE = 300 * 1024
TIPI_IMMAGINE = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg",
                 "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"}
_MIME_DI = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml",
            "ico": "image/x-icon"}
PERCORSI_SITO = ("/apple-touch-icon.png", "/favicon.ico")
FONTE_FINNHUB = "finnhub_profile"
FONTE_SITO = "sito_emittente"


def _cartella() -> str:
    cartella = os.path.join(str(paths.DATA_DIR), "loghi")
    os.makedirs(cartella, exist_ok=True)
    return cartella


def _nome_file(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9._-]", "_", ticker.upper())


def _salvato(ticker: str) -> Optional[str]:
    base = os.path.join(_cartella(), _nome_file(ticker))
    for estensione, mime in _MIME_DI.items():
        percorso = "%s.%s" % (base, estensione)
        if os.path.exists(percorso):
            with open(percorso, "rb") as fh:
                return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode("ascii"))
    return None


def _leggi_assenti() -> Dict[str, Dict[str, str]]:
    try:
        with open(os.path.join(_cartella(), "_assenti.json"), encoding="utf-8") as fh:
            dati = json.load(fh)
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def _scrivi_assenti(assenti: Dict[str, Dict[str, str]]) -> None:
    with open(os.path.join(_cartella(), "_assenti.json"), "w", encoding="utf-8") as fh:
        json.dump(assenti, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _alias_finnhub() -> tuple:
    """(alias finnhub, motivo) — motivo None se il negozio e' leggibile.

    G8 (04/10/2026, Opus 5.5): prima un file rotto tornava {} in silenzio e il motivo
    mostrato diventava «nessun simbolo USA dichiarato», falso: ora lo stato vero."""
    try:
        from bellomberg.storage.negozi_privati import carica_alias
        esito = carica_alias()
    except Exception as exc:
        return {}, "alias_fonti illeggibile: %s: %s" % (type(exc).__name__, exc)
    if esito["origine"] in {"assente", "illeggibile"}:
        return {}, "alias_fonti %s: %s" % (esito["origine"], esito.get("motivo") or "causa non dichiarata")
    return dict(esito["alias"].get("finnhub", {})), None


def _url_ammesso(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    parti = urlparse(url)
    host = (parti.hostname or "").lower()
    return parti.scheme == "https" and (host == "finnhub.io" or host.endswith(".finnhub.io"))


class _Transitorio(Exception):
    """Guasto di passaggio (rete, 429, 5xx): non si ricorda come assenza."""


def _scarica(url: str, **kwargs: Any) -> Optional[tuple]:
    """(byte, estensione) o None se la risposta non e' un'immagine utilizzabile.

    Lancia `_Transitorio` per i guasti di passaggio; 4xx (tranne 429) = assenza certa."""
    # G8: nei motivi mai l'URL con la querystring ne' il testo dell'eccezione di rete
    # (che la contiene): solo host+percorso, tipo d'eccezione, stato HTTP.
    dove = "%s%s" % (urlparse(url).hostname or "?", urlparse(url).path)
    try:
        risposta = finnhub_news.requests.get(url, timeout=10, **kwargs)
    except Exception as exc:
        raise _Transitorio("%s su %s" % (type(exc).__name__, dove))
    codice = risposta.status_code
    if codice != 200:
        if codice == 429 or codice >= 500:
            raise _Transitorio("HTTP %d su %s" % (codice, dove))
        return None
    mime = (risposta.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    contenuto = risposta.content or b""
    if mime not in TIPI_IMMAGINE or not contenuto or len(contenuto) > MAX_BYTE:
        return None
    return contenuto, TIPI_IMMAGINE[mime]


def _dominio(sito: Any) -> Optional[str]:
    """Host di un sito dichiarato dal profilo ('https://www.acme.example/it' -> 'www.acme.example')."""
    if not isinstance(sito, str) or not sito.strip():
        return None
    testo = sito.strip()
    if "://" not in testo:
        testo = "https://" + testo
    host = (urlparse(testo).hostname or "").lower().strip(".")
    if not host or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    # G8: niente IP letterali (127.0.0.1, 192.168.x.x, forme corte/esadecimali come 127.1
    # o 0x7f.1): il dominio di primo livello deve essere un nome, non un numero.
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    if not re.fullmatch(r"(xn--[a-z0-9-]+|[a-z]{2,63})", host.rsplit(".", 1)[1]):
        return None
    return host


def _sito_da_yahoo(ticker: str) -> Optional[str]:
    """Sito dell'emittente dal profilo Yahoo (`website`), o None se il profilo non lo dichiara.

    Lancia `_Transitorio` se il profilo non e' leggibile (rete, provider)."""
    try:
        import yfinance as yf
        from bellomberg.cli.price_updater import data_ticker
        info = yf.Ticker(data_ticker(ticker)).info or {}
    except Exception as exc:
        raise _Transitorio("profilo Yahoo: %s" % type(exc).__name__)
    sito = info.get("website") if isinstance(info, dict) else None
    return sito if isinstance(sito, str) and sito.strip() else None


def _dal_sito(host: str) -> Optional[tuple]:
    """Icona dal sito dell'emittente: solo quel dominio (e il suo `www.`), https, niente redirect."""
    host_validi = [host] + (["www." + host] if not host.startswith("www.") else [])
    transitori: List[str] = []
    for h in host_validi:
        for percorso in PERCORSI_SITO:
            try:
                trovato = _scarica("https://%s%s" % (h, percorso), allow_redirects=False)
            except _Transitorio as exc:
                transitori.append(str(exc))
                continue
            if trovato:
                return trovato
    if transitori:
        raise _Transitorio("; ".join(transitori))
    return None


def _leggi_fonti() -> Dict[str, str]:
    try:
        with open(os.path.join(_cartella(), "_fonti.json"), encoding="utf-8") as fh:
            dati = json.load(fh)
        return dati if isinstance(dati, dict) else {}
    except (OSError, ValueError):
        return {}


def _scrivi_fonti(fonti: Dict[str, str]) -> None:
    with open(os.path.join(_cartella(), "_fonti.json"), "w", encoding="utf-8") as fh:
        json.dump(fonti, fh, ensure_ascii=False, indent=2, sort_keys=True)


def logo(ticker: str, oggi: Optional[date] = None) -> Dict[str, Optional[str]]:
    """{"data_url": str | None, "motivo": str | None, "fonte": str | None} per un ticker."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"data_url": None, "motivo": "ticker vuoto", "fonte": None}
    gia = _salvato(ticker)
    if gia:
        fonte = _leggi_fonti().get(ticker)
        return {"data_url": gia, "motivo": None, "fonte": fonte}
    oggi = oggi or date.today()
    assenti = _leggi_assenti()
    voce = assenti.get(ticker)
    if voce:
        try:
            if (oggi - date.fromisoformat(voce.get("il", ""))).days < GIORNI_RIPROVA_ASSENTI:
                return {"data_url": None, "motivo": voce.get("motivo") or "logo assente", "fonte": None}
        except ValueError:
            pass

    alias_finnhub, alias_rotto = _alias_finnhub()

    def _assente(motivo: str) -> Dict[str, Optional[str]]:
        if alias_rotto and alias_rotto.startswith("alias_fonti illeggibile"):
            # con il negozio alias rotto l'assenza non e' certa: non si ricorda (un file
            # ASSENTE invece e' uno stato dichiarato e stabile: si ricorda come prima)
            return {"data_url": None, "motivo": motivo, "fonte": None}
        assenti[ticker] = {"il": oggi.isoformat(), "motivo": motivo}
        _scrivi_assenti(assenti)
        return {"data_url": None, "motivo": motivo, "fonte": None}

    def _trovato(contenuto: bytes, estensione: str, fonte: str) -> Dict[str, Optional[str]]:
        with open(os.path.join(_cartella(), "%s.%s" % (_nome_file(ticker), estensione)), "wb") as fh:
            fh.write(contenuto)
        fonti = _leggi_fonti()
        fonti[ticker] = fonte
        _scrivi_fonti(fonti)
        if ticker in assenti:
            del assenti[ticker]
            _scrivi_assenti(assenti)
        return {"data_url": _salvato(ticker), "motivo": None, "fonte": fonte}

    perche: List[str] = []
    sito: Optional[str] = None
    simbolo = alias_finnhub.get(ticker) or (ticker if "." not in ticker else None)
    if alias_rotto and (not simbolo or "." in simbolo):
        perche.append("%s: simbolo USA non leggibile (Finnhub gratuito copre solo gli USA)" % alias_rotto)
    elif not simbolo or "." in simbolo:
        perche.append("nessun simbolo USA dichiarato in alias_fonti (Finnhub gratuito copre solo gli USA)")
    else:
        motivo: List[str] = []
        profilo = finnhub_news._api_get("/stock/profile2", {"symbol": simbolo}, motivo=motivo)
        if profilo is None:
            testo = "; ".join(motivo) or "profilo Finnhub non disponibile"
            if "HTTP 403" not in testo and "HTTP 404" not in testo:
                # guasto di passaggio: non si ricorda
                return {"data_url": None, "motivo": testo, "fonte": None}
            perche.append("Finnhub %s: %s" % (simbolo, testo))
        else:
            url = profilo.get("logo") if isinstance(profilo, dict) else None
            sito = profilo.get("weburl") if isinstance(profilo, dict) else None
            if not url:
                perche.append("il profilo Finnhub di %s non ha un logo" % simbolo)
            elif not _url_ammesso(url):
                perche.append("logo fuori dagli host ammessi: %s" % urlparse(str(url)).hostname)
            else:
                try:
                    scaricato = _scarica(url)
                except _Transitorio as exc:
                    return {"data_url": None, "motivo": "download del logo: %s" % exc, "fonte": None}
                if scaricato:
                    return _trovato(scaricato[0], scaricato[1], FONTE_FINNHUB)
                perche.append("il logo di %s non e' un'immagine utilizzabile" % simbolo)

    host = _dominio(sito)
    if host is None:
        try:
            host = _dominio(_sito_da_yahoo(ticker))
        except _Transitorio as exc:
            return {"data_url": None, "motivo": "; ".join(perche + [str(exc)]), "fonte": None}
    if host is None:
        return _assente("; ".join(perche + ["nessun sito dell'emittente nel profilo"]))
    try:
        scaricato = _dal_sito(host)
    except _Transitorio as exc:
        return {"data_url": None, "motivo": "; ".join(perche + ["sito %s: %s" % (host, exc)]), "fonte": None}
    if scaricato:
        return _trovato(scaricato[0], scaricato[1], FONTE_SITO)
    return _assente("; ".join(perche + ["nessuna icona utilizzabile sul sito dell'emittente %s" % host]))


def loghi(tickers: List[str], oggi: Optional[date] = None) -> Dict[str, Any]:
    """{"logos": {T: data_url|None}, "motivi": {T: perche' manca}, "fonti": {T: fonte}}."""
    out: Dict[str, Optional[str]] = {}
    motivi: Dict[str, str] = {}
    fonti: Dict[str, str] = {}
    for ticker in tickers:
        chiave = (ticker or "").strip().upper()
        if not chiave or chiave in out:
            continue
        esito = logo(chiave, oggi)
        out[chiave] = esito["data_url"]
        if esito["motivo"]:
            motivi[chiave] = esito["motivo"]
        if esito.get("fonte"):
            fonti[chiave] = esito["fonte"]
    return {"logos": out, "motivi": motivi, "fonti": fonti}
