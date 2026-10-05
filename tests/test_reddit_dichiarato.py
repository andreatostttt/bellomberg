"""Reddit DICHIARATO (04/10, F7 — Opus 5.5).

Reddit non serve piu' il JSON anonimo (403 + pagina di blocco HTML). Prima la
lista vuota era indistinguibile da «nessun post» e la ricerca per ticker non
loggava nemmeno il 403. Queste prove fissano: stato dell'ultima chiamata in
`last_status()`, riga `[REDDIT]` nel log, mai il testo dell'eccezione, ritorno
invariato (lista). Nessuna rete: `requests.get` e' sempre sostituito.
"""
import time as _time

import pytest

from bellomberg.market_data import reddit_news as rn

SEGRETO = "QQSEGRETO123"
# letto PRIMA che la fixture lo sostituisca: e' il valore che gira in produzione
_SPENTA_DI_FABBRICA = rn.FONTE_SPENTA


class _Risposta:
    def __init__(self, status=200, payload=None, testo=""):
        self.status_code = status
        self._payload = payload
        self.text = testo

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _post(titolo="ZZTEST rally", score=5000, sub="stocks"):
    return {"data": {
        "title": titolo, "selftext": "testo sintetico", "score": score,
        "num_comments": 12, "created_utc": _time.time() - 60,
        "permalink": f"/r/{sub}/comments/zz/finto/", "link_flair_text": "",
    }}


def _listing(*posts):
    return {"data": {"children": list(posts)}}


@pytest.fixture(autouse=True)
def _isola(monkeypatch):
    rn.reset_status()
    monkeypatch.setattr(rn.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(rn, "REQ_OK", True)
    # i test del ramo di rete (dormiente) la riaccendono; quelli della
    # decisione PM la rispengono esplicitamente
    monkeypatch.setattr(rn, "FONTE_SPENTA", False)

    def _vietata(*_a, **_k):  # rete mai: ogni test installa il suo finto
        raise AssertionError("rete vera chiamata nel test")
    monkeypatch.setattr(rn.requests, "get", _vietata)
    yield
    rn.reset_status()


def _installa(monkeypatch, per_sub):
    """per_sub(subreddit, url) -> _Risposta oppure solleva."""
    def _get(url, params=None, timeout=None, headers=None):
        sub = url.split("/r/")[1].split("/")[0]
        return per_sub(sub, url)
    monkeypatch.setattr(rn.requests, "get", _get)


def test_mai_interrogata_e_none():
    assert rn.last_status() is None


# ── decisione PM 04/10: Reddit SPENTA ───────────────────────────────────────

def test_di_fabbrica_la_fonte_e_spenta():
    assert _SPENTA_DI_FABBRICA is True


def test_spenta_nessuna_rete_e_stato_dichiarato(monkeypatch, capsys):
    """Contatore sulla rete: con la fonte spenta NESSUNA chiamata parte."""
    monkeypatch.setattr(rn, "FONTE_SPENTA", True)
    chiamate = []

    def _conta(*a, **k):
        chiamate.append(a)
        return _Risposta(200, _listing(_post()))
    monkeypatch.setattr(rn.requests, "get", _conta)

    assert rn.fetch_reddit_top() == []
    st = rn.last_status()
    assert st["stato"] == "SPENTA"
    assert st["motivo"] == rn.MOTIVO_SPENTA and "decisione PM" in st["motivo"]
    assert st["path"] == rn.PATH_TOP and st["n_item"] == 0 and st["http"] is None

    assert rn.fetch_reddit_for_ticker("ZZTEST") == []
    st = rn.last_status()
    assert st["stato"] == "SPENTA" and st["path"] == rn.PATH_SEARCH

    assert len(chiamate) == 0
    assert rn.reddit_available() is False
    log = capsys.readouterr().out
    assert log.count("[REDDIT] SPENTA") == 2


def test_accesa_e_disponibile():
    assert rn.reddit_available() is True


def test_tutti_403_dichiarato_muto(monkeypatch, capsys):
    _installa(monkeypatch, lambda s, u: _Risposta(403, testo="<html>blocked</html>"))
    out = rn.fetch_reddit_top()
    assert out == []  # ritorno invariato
    st = rn.last_status()
    assert st["stato"] == "HTTP_403"
    assert st["http"] == 403
    assert st["sub_ok"] == 0 and st["sub_tot"] == len(rn.SUBREDDITS)
    assert set(st["sub_ko"]) == set(rn.SUBREDDITS)
    assert set(st["sub_ko"].values()) == {"HTTP_403"}
    assert st["n_item"] == 0
    assert st["path"] == rn.PATH_TOP
    assert isinstance(st["mono"], float) and st["quando"]
    log = capsys.readouterr().out
    assert "[REDDIT]" in log and "MUTO" in log and "HTTP_403" in log


def test_200_vuoto_e_live_con_zero(monkeypatch, capsys):
    """Zero post MISURATO: si distingue dal 403."""
    _installa(monkeypatch, lambda s, u: _Risposta(200, _listing()))
    assert rn.fetch_reddit_top() == []
    st = rn.last_status()
    assert st["stato"] == "live" and st["http"] == 200
    assert st["n_item"] == 0 and st["sub_ok"] == len(rn.SUBREDDITS)
    assert st["sub_ko"] == {}
    assert "MUTO" not in capsys.readouterr().out


def test_live_con_post(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(200, _listing(_post(sub=s))))
    out = rn.fetch_reddit_top()
    assert len(out) == len(rn.SUBREDDITS)
    st = rn.last_status()
    assert st["stato"] == "live" and st["n_item"] == len(out)


def test_parziale_elenca_i_caduti(monkeypatch, capsys):
    def _per_sub(s, u):
        if s == "options":
            return _Risposta(403)
        return _Risposta(200, _listing(_post(sub=s)))
    _installa(monkeypatch, _per_sub)
    out = rn.fetch_reddit_top()
    st = rn.last_status()
    assert st["stato"] == "live"
    assert st["sub_ok"] == len(rn.SUBREDDITS) - 1
    assert st["sub_ko"] == {"options": "HTTP_403"}
    assert len(out) == len(rn.SUBREDDITS) - 1
    log = capsys.readouterr().out
    assert "options HTTP 403" in log and "MUTO" not in log


def test_eccezione_solo_il_tipo_mai_il_testo(monkeypatch, capsys):
    def _per_sub(s, u):
        raise rn.requests.ConnectionError(f"{u}?token={SEGRETO} irraggiungibile")
    _installa(monkeypatch, _per_sub)
    assert rn.fetch_reddit_top() == []
    st = rn.last_status()
    assert st["stato"] == "ERRORE_ConnectionError" and st["http"] is None
    log = capsys.readouterr().out
    assert "ConnectionError" in log
    assert SEGRETO not in log and "irraggiungibile" not in log
    assert SEGRETO not in repr(st)


def test_json_non_dict_e_risposta_inattesa(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(200, ["lista", "strana"]))
    assert rn.fetch_reddit_top() == []
    assert rn.last_status()["stato"] == "RISPOSTA_INATTESA"


def test_json_rotto_e_errore_dichiarato(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(200, ValueError("corpo html")))
    assert rn.fetch_reddit_top() == []
    assert rn.last_status()["stato"] == "ERRORE_ValueError"


def test_senza_requests_dichiarato(monkeypatch):
    monkeypatch.setattr(rn, "REQ_OK", False)
    assert rn.fetch_reddit_top() == []
    st = rn.last_status()
    assert st["stato"] == "ERRORE_ImportError" and st["sub_ok"] == 0
    rn.reset_status()
    assert rn.fetch_reddit_for_ticker("ZZTEST") == []
    assert rn.last_status()["stato"] == "ERRORE_ImportError"


def test_un_200_dopo_il_403_guarisce(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(403))
    rn.fetch_reddit_top()
    assert rn.last_status()["stato"] == "HTTP_403"
    _installa(monkeypatch, lambda s, u: _Risposta(200, _listing()))
    rn.fetch_reddit_top()
    assert rn.last_status()["stato"] == "live"


def test_ricerca_ticker_403_ora_loggata(monkeypatch, capsys):
    _installa(monkeypatch, lambda s, u: _Risposta(403))
    assert rn.fetch_reddit_for_ticker("ZZTEST.MI") == []
    st = rn.last_status()
    assert st["stato"] == "HTTP_403" and st["path"] == rn.PATH_SEARCH
    assert st["sub_ok"] == 0 and st["sub_tot"] == len(rn.SUBREDDITS)
    log = capsys.readouterr().out
    assert "[REDDIT]" in log and "search stocks/ZZTEST HTTP 403" in log
    assert "MUTO" in log


def test_ricerca_ticker_eccezione_senza_testo(monkeypatch, capsys):
    def _per_sub(s, u):
        raise rn.requests.Timeout(f"{u}?q=ZZTEST&token={SEGRETO}")
    _installa(monkeypatch, _per_sub)
    assert rn.fetch_reddit_for_ticker("ZZTEST") == []
    assert rn.last_status()["stato"] == "ERRORE_Timeout"
    log = capsys.readouterr().out
    assert "Timeout" in log and SEGRETO not in log


def test_ricerca_ticker_live(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(200, _listing(_post("ZZTEST moon", sub=s))))
    out = rn.fetch_reddit_for_ticker("ZZTEST", include_subs=["stocks", "options"])
    assert len(out) == 2
    st = rn.last_status()
    assert st["stato"] == "live" and st["n_item"] == 2 and st["sub_tot"] == 2


def test_last_status_e_una_copia(monkeypatch):
    _installa(monkeypatch, lambda s, u: _Risposta(403))
    rn.fetch_reddit_top()
    st = rn.last_status()
    st["stato"] = "live"
    st["sub_ko"].clear()
    vero = rn.last_status()
    assert vero["stato"] == "HTTP_403" and vero["sub_ko"]


def test_reset_status():
    rn._segna_stato({"stocks": {"stato": "HTTP_403", "http": 403}}, rn.PATH_TOP, 0)
    assert rn.last_status() is not None
    rn.reset_status()
    assert rn.last_status() is None


def test_nessun_subreddit_non_e_live():
    rn._segna_stato({}, rn.PATH_SEARCH, 0)
    assert rn.last_status()["stato"] == "NESSUN_SUBREDDIT"
