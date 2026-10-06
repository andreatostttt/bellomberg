"""Decisione PM 05/10 sera: «ESEF senza contatto SEC».

Prima: senza SEC_CONTACT_EMAIL nel .env anche filings.xbrl.org (ESEF) e GLEIF non partivano
(`esef._headers()` sollevava ContattoMancante), quindi chi scarica la repo senza la mail non aveva
bilanci UE. Ora le fonti UE non-SEC partono SEMPRE con uno User-Agent onesto e generico del
progetto (nessun dato personale); la SEC continua a esigere la mail (lo impone la SEC) e la sua
mancanza resta DICHIARATA, ma solo per la SEC. Con la mail: comportamento invariato (la mail
resta nello User-Agent anche per ESEF).

Rete finta ovunque; la variabile si toglie con monkeypatch.delenv (il conftest la imposta con
setdefault e load_dotenv non sovrascrive: delenv vale perche' `_headers` la legge a ogni chiamata).
Ticker e nomi inventati.
"""
import pytest

from bellomberg.market_data import esef, filing_identita, sec_edgar


@pytest.fixture
def senza_mail(monkeypatch):
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)


class _Risposta:
    status_code = 200
    ok = True
    headers = {"Content-Length": "10"}

    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


# ---------------------------------------------------------------- header
def test_esef_senza_mail_ha_uno_user_agent_generico(senza_mail):
    ua = esef._headers()["User-Agent"]          # prima: ContattoMancante
    assert ua.startswith("Bellomberg"), ua
    assert "@" not in ua and "SEC_CONTACT_EMAIL" not in ua, ua


def test_esef_con_mail_resta_invariato(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "analista@example.com")
    assert esef._headers() == {"User-Agent": "Bellomberg research (contatto: analista@example.com)"}


def test_mail_di_soli_spazi_vale_assente_anche_per_esef(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "   ")
    ua = esef._headers()["User-Agent"]
    assert "@" not in ua and "contatto:" not in ua, ua


def test_la_sec_senza_mail_resta_dichiarata(senza_mail):
    with pytest.raises(sec_edgar.ContattoMancante) as e:
        sec_edgar._headers()
    assert "SEC_CONTACT_EMAIL" in str(e.value)


# ---------------------------------------------------------------- cablaggio: la richiesta PARTE
def test_ricerca_entita_esef_senza_mail_arriva_alla_rete(senza_mail, monkeypatch):
    visti = []

    def _get(url, **k):
        visti.append((url, k.get("headers")))
        return _Risposta({"data": [{"id": "1", "attributes": {"identifier": "ZZLEI0000000000000001",
                                                               "name": "QQSYN S.P.A."}}]})

    monkeypatch.setattr("requests.get", _get)   # esef importa requests DENTRO la funzione
    ents, _query, q, err = esef._cerca_entita("QQSYN S.p.A.", ritmo=lambda: None)
    assert err is None and ents and q, (ents, err)
    assert visti and visti[0][0].startswith(esef.BASE)
    assert visti[0][1]["User-Agent"] == esef._headers()["User-Agent"]


def test_facts_esef_senza_mail_si_scaricano(senza_mail, monkeypatch):
    monkeypatch.setattr(esef, "attendi_esef", lambda *a, **k: None)
    monkeypatch.setattr("requests.head", lambda url, **k: _Risposta({}))
    fact = {"value": "123.45", "dimensions": {"concept": "ifrs-full:Revenue", "entity": "x",
                                              "period": "2024-01-01T00:00:00/2025-01-01T00:00:00",
                                              "unit": "iso4217:EUR"}}
    monkeypatch.setattr("requests.get", lambda url, **k: _Risposta({"facts": {"f1": fact}}))
    out, err = esef._extract_filing_facts("https://filings.xbrl.org/finto/report.json")
    assert err is None, err
    assert out == {"Revenue": [[2024, 123.45, "EUR"]]}


def test_gleif_senza_mail_parte(senza_mail, monkeypatch):
    from bellomberg.market_data import esef_sito

    class _Flusso:
        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b'{"data": [{"id": "ZZLEI0000000000000001"}]}'

        def close(self):
            pass

    visti = []
    monkeypatch.setattr(esef_sito, "attendi_gleif", lambda *a, **k: None)
    monkeypatch.setattr("requests.get", lambda url, **k: visti.append(k["headers"]) or _Flusso())
    assert esef_sito._gleif_scarica("QQSYN") == [{"id": "ZZLEI0000000000000001"}]
    assert "@" not in visti[0]["User-Agent"]


def test_documento_da_filings_xbrl_senza_mail_si_scarica(senza_mail, monkeypatch, tmp_path):
    from bellomberg.market_data import lettore_trimestrali as lt

    class _Doc:
        status_code = 200
        headers = {"Content-Type": "application/json"}
        raw = None

        def raise_for_status(self):
            pass

        def iter_content(self, n):
            yield b'{"facts": {}}'

    visti = []
    monkeypatch.setattr(esef, "attendi_esef", lambda *a, **k: None)
    monkeypatch.setattr(lt.requests, "get", lambda url, **k: visti.append(k["headers"]) or _Doc())
    esito = lt.scarica_documento("https://filings.xbrl.org/finto/report.json", str(tmp_path),
                                 host_consentiti={"filings.xbrl.org"})
    assert visti and visti[0]["User-Agent"] == esef._headers()["User-Agent"]
    assert "errore" not in esito or not esito.get("errore"), esito


# ---------------------------------------------------------------- frasi di stato
def test_proposta_estera_senza_mail_dice_sec_non_configurata_ed_esef_attivo(senza_mail, monkeypatch):
    def candidati(ticker, nome=None, **k):
        return {"stato": "univoco", "motivo": "LEI dal repository",
                "candidati": [{"lei": "ZZLEI0000000000000001", "nome": "QQSYN S.P.A.", "origine": "nome"}]}

    monkeypatch.setattr(esef, "candidati_lei", candidati)
    monkeypatch.setattr(sec_edgar.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("SEC chiamata senza mail")))
    r = filing_identita.proponi("QQSYN.MI", "QQSYN S.p.A.")
    assert r["sec"]["stato"] == "non_configurata"
    assert r["sec"]["motivo"] == "SEC non configurata: manca SEC_CONTACT_EMAIL nel .env — ESEF attivo"
    assert r["esef"]["stato"] == "univoco" and r["preferita"] == "esef"


def test_nessuna_frase_dice_piu_che_esef_vuole_la_mail():
    assert not hasattr(filing_identita, "ESEF_NON_CONFIGURATO")
    assert "ESEF attivo" in filing_identita.SEC_NON_CONFIGURATA


def test_avviso_di_attivazione_senza_mail_e_solo_sec(senza_mail, tmp_path):
    from bellomberg.market_data import filing_attivazione as fa

    class _Store:
        pass

    out = fa.attiva_mancanti(_Store(), [], pref_path=tmp_path / "pref.json")
    assert out["avviso_configurazione"] == filing_identita.SEC_NON_CONFIGURATA
    assert "ESEF attivo" in out["avviso_configurazione"]
