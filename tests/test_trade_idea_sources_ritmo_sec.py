"""download_public_document: le GET verso sec.gov passano dal ritmo SEC condiviso
(sec_edgar.attendi_sec, <= 8 req/s fra processi), a ogni richiesta e a ogni salto di redirect.
Gli altri host non lo toccano. Trasporto intercettato: nessuna rete, nessun DNS vero."""
import html
import io
import socket

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.market_data import sec_edgar

SEC_URL = "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/annual.html"
ISSUER_URL = "https://issuer.example.org/investors/annual.html"
WEBSITE = "https://issuer.example.org"
BODY = ("<html><body><pre>" + html.escape("Synthetic issuer ZZTEST\nYear ended 2025-12-31\n")
        + "</pre></body></html>").encode()


class _Response:
    def __init__(self, body=BODY, *, status=200, headers=None):
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = {"Content-Type": "text/html", "Content-Length": str(len(body)), **(headers or {})}

    def getheader(self, name, default=None):
        return self._headers.get(name, default)

    def read(self, size):
        return self._body.read(size)


def _download(url, root, eventi, responses, **kwargs):
    pending = list(responses)

    def resolver(host, port, **_kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    class Connection:
        def __init__(self, host, address, timeout):
            eventi.append(("connessione", host))

        def request(self, method, target, headers):
            eventi.append(("richiesta", target))

        def getresponse(self):
            return pending.pop(0)

        def close(self):
            eventi.append(("chiusa",))

    return sources.download_public_document(url, root, resolver=resolver, connection_factory=Connection, **kwargs)


@pytest.fixture
def ritmo(monkeypatch):
    eventi = []
    monkeypatch.setattr(sec_edgar, "attendi_sec", lambda **_kw: eventi.append(("ritmo",)))
    return eventi


def test_la_get_a_sec_gov_passa_dal_ritmo_prima_della_richiesta(tmp_path, ritmo):
    esito = _download(SEC_URL, tmp_path, ritmo, [_Response()])
    assert esito["stato"] == "ok"
    assert [e[0] for e in ritmo] == ["connessione", "ritmo", "richiesta", "chiusa"], ritmo


def test_ogni_salto_di_redirect_su_sec_gov_ripassa_dal_ritmo(tmp_path, ritmo):
    seconda = "/Archives/edgar/data/123/000000012326000001/annual2.html"
    esito = _download(SEC_URL, tmp_path, ritmo,
                      [_Response(b"", status=302, headers={"Location": seconda}), _Response()])
    assert esito["stato"] == "ok" and esito["url_finale"].endswith("annual2.html")
    sequenza = [e[0] for e in ritmo]
    assert sequenza.count("ritmo") == 2 and sequenza.count("richiesta") == 2, ritmo
    # ogni richiesta e' preceduta immediatamente dal suo passaggio dal ritmo
    for i, voce in enumerate(sequenza):
        if voce == "richiesta":
            assert sequenza[i - 1] == "ritmo", ritmo


def test_gli_altri_host_non_toccano_il_ritmo_sec(tmp_path, ritmo):
    esito = _download(ISSUER_URL, tmp_path, ritmo, [_Response()], issuer_website=WEBSITE)
    assert esito["stato"] == "ok"
    assert ("ritmo",) not in ritmo and ("richiesta", "/investors/annual.html") in ritmo, ritmo


def test_ritmo_bloccato_ferma_la_richiesta_e_chiude_la_connessione(tmp_path, monkeypatch):
    eventi = []

    def bloccato(**_kw):
        eventi.append(("ritmo",))
        raise sec_edgar.RitmoBloccato("lock del ritmo occupato (prova)")

    monkeypatch.setattr(sec_edgar, "attendi_sec", bloccato)
    with pytest.raises(sec_edgar.RitmoBloccato):
        _download(SEC_URL, tmp_path, eventi, [_Response()])
    assert [e[0] for e in eventi] == ["connessione", "ritmo", "chiusa"], eventi
    assert not any(tmp_path.rglob("*.html"))
