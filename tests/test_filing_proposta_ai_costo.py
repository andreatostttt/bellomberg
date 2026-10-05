"""Revisione import 04/10/2026 (G2b, R10): proposta AI del profilo filing, costo e controllo.

(a) un solo livello di retry (quello di core/llm_client); (b) throttle per titolo e tetto di
spesa giornaliero da env, superati = rifiuto dichiarato; (c) la stessa richiesta (PDF, prompt,
modello) riusa la risposta gia' pagata anche con «Riprova»; (e) lingua mancante dichiarata,
mai un ripiego che rende vero il controllo; (f) regex del modello a backtracking polinomiale
rifiutate prima di girare sul documento.
"""
import json
import re
import time
from types import SimpleNamespace as NS

import pytest

from bellomberg.core import llm_client as lc
from bellomberg.market_data import filing_proposta_ai as fp
from tests.filing_pdf_sintetici import semestrale_nova
from tests.test_filing_proposta_ai_chiamata import RISPOSTA
from tests.test_filing_proposta_ai_verifica import URL, _proposta, _verifica

try:  # helper di costo: modulo neutro dopo G3, prima in article_summary
    from bellomberg.core import llm_usage as asum
except ImportError:  # pragma: no cover - albero senza G3
    from bellomberg.market_data import article_summary as asum

VARIABILI = ("FILING_AI_INTERVALLO_TICKER_S", "FILING_AI_TETTO_GIORNO_EUR")


class _Status429(Exception):
    status_code = 429


@pytest.fixture
def env(tmp_path, monkeypatch):
    for nome in VARIABILI:
        monkeypatch.delenv(nome, raising=False)
    usage = []
    monkeypatch.setattr(asum, "MemoryDB", lambda: NS(save_llm_usage=lambda memo_id, log: usage.extend(log)))
    monkeypatch.setattr(asum, "_sleep", lambda s: None, raising=False)
    from bellomberg.core import llm_pricing
    stato = NS(chiamate=[], risposte=[json.dumps(RISPOSTA)], errore=None, costo=0.0021, modello="nova/flash",
               prezzi={"prompt": "0.0000003", "completion": "0.0000012"})

    def catalogo(modello):  # costo massimo stimato senza rete; None = tariffe non disponibili
        if stato.prezzi is None:
            raise ValueError("modello assente dal catalogo")
        return {"pricing": stato.prezzi}
    monkeypatch.setattr(fp, "_metadati_openrouter", catalogo)
    monkeypatch.setattr(fp, "_fx", lambda: (0.9, "live"))
    monkeypatch.setattr(llm_pricing, "cost_eur", lambda model, tokens, ttl=None: {
        "cost": stato.costo, "status": "ok" if stato.costo is not None else "fx_unavailable",
        "fx_rate": 0.9, "fx_source": "live", "breakdown": {}})
    monkeypatch.setattr(lc, "modello", lambda funzione, *a: stato.modello)

    def create(**kwargs):
        stato.chiamate.append(kwargs)
        if stato.errore:
            raise stato.errore
        testo = stato.risposte[min(len(stato.chiamate), len(stato.risposte)) - 1]
        return NS(content=[lc.TextBlock(testo)],
                  usage=NS(input_tokens=8_000, output_tokens=600, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0031))

    monkeypatch.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    pdf = {}
    for anno in (2024, 2025, 2026):
        pdf[anno] = tmp_path / f"h1-{anno}.pdf"
        pdf[anno].write_bytes(semestrale_nova(anno))
    return NS(pdf=pdf, usage=usage, stato=stato, mp=monkeypatch)


def _proponi(env, ticker="NOVA.DE", anno=2026, **kw):
    return fp.proponi(ticker, nome="Nova AG", path=env.pdf[anno], url=URL, **kw)


# ---------------------------------------------------------------- (a) un solo livello di retry

def test_un_solo_livello_di_retry_sopra_il_client(env):
    """Il client OpenRouter ritenta gia' 408/409/429/5xx: qui una sola invocazione per click."""
    env.stato.errore = _Status429("rate limited")
    out = _proponi(env)
    assert out["stato"] == "error" and "rate limited" in out["dettaglio"]
    assert len(env.stato.chiamate) == 1
    assert env.usage == []


def test_chiamata_con_thinking_disabilitato_e_json(env):
    assert _proponi(env)["stato"] == "done"
    kwargs = env.stato.chiamate[0]
    assert kwargs["thinking"] == {"type": "disabled"} and kwargs["max_tokens"] == fp.MAX_TOKENS
    assert kwargs["response_format"] == {"type": "json_object"} and kwargs["model"] == "nova/flash"


# ---------------------------------------------------------------- (b) throttle e tetto di spesa

def test_throttle_per_titolo_default_documentato(env):
    assert fp.INTERVALLO_TICKER_S == 300
    assert _proponi(env, anno=2026)["stato"] == "done"
    rifiuto = _proponi(env, anno=2025)  # altro PDF, stesso titolo, subito dopo
    assert rifiuto["stato"] == "refused" and rifiuto["variabile"] == "FILING_AI_INTERVALLO_TICKER_S"
    assert 0 < rifiuto["riprova_tra_s"] <= 300
    assert len(env.stato.chiamate) == 1
    assert _proponi(env, ticker="KORE.MI", anno=2025)["stato"] == "done"  # altro titolo: ammesso
    assert _proponi(env, anno=2026)["cached"] is True  # la cache non paga: mai frenata


def test_throttle_da_env(env):
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    assert _proponi(env, anno=2026)["stato"] == "done"
    assert _proponi(env, anno=2025)["stato"] == "done"
    assert len(env.stato.chiamate) == 2


def test_tetto_di_spesa_giornaliero_rifiuto_dichiarato_anche_dopo_riavvio(env):
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", "0.004")
    assert _proponi(env, ticker="AAA.MI", anno=2024)["stato"] == "done"   # speso 0,0021
    assert _proponi(env, ticker="BBB.MI", anno=2025)["stato"] == "done"   # speso 0,0042
    # riavvio del backend: tetto e throttle vivono SOLO nel registro su disco (nessuno stato in memoria)
    rifiuto = _proponi(env, ticker="CCC.MI", anno=2026)
    assert rifiuto["stato"] == "refused" and rifiuto["variabile"] == "FILING_AI_TETTO_GIORNO_EUR"
    assert "0.0042" in rifiuto["motivo"] or "0,0042" in rifiuto["motivo"]
    assert len(env.stato.chiamate) == 2


def test_tetto_default_documentato(env):
    assert fp.TETTO_GIORNO_EUR == 1.0


@pytest.mark.parametrize("nome,valore", [("FILING_AI_TETTO_GIORNO_EUR", ""), ("FILING_AI_TETTO_GIORNO_EUR", "un euro"),
                                         ("FILING_AI_TETTO_GIORNO_EUR", "-1"), ("FILING_AI_TETTO_GIORNO_EUR", "nan"),
                                         ("FILING_AI_INTERVALLO_TICKER_S", " "),
                                         ("FILING_AI_INTERVALLO_TICKER_S", "5m")])
def test_env_non_valida_rifiuto_col_nome_della_variabile(env, nome, valore):
    env.mp.setenv(nome, valore)
    out = _proponi(env)
    assert out["stato"] == "refused" and out["variabile"] == nome and nome in out["motivo"]
    assert env.stato.chiamate == []


def test_spesa_non_misurabile_ferma_le_chiamate_successive(env):
    env.stato.costo = None  # tariffe o cambio mancanti: costo ignoto, mai zero
    env.stato.prezzi = None  # e nemmeno il massimo stimato: blocco dichiarato
    assert _proponi(env, ticker="AAA.MI", anno=2024)["stato"] == "done"
    rifiuto = _proponi(env, ticker="BBB.MI", anno=2025)
    assert rifiuto["stato"] == "refused" and rifiuto["variabile"] == "FILING_AI_TETTO_GIORNO_EUR"
    assert len(env.stato.chiamate) == 1


def test_registro_spesa_illeggibile_rifiuto_dichiarato(env):
    assert _proponi(env, ticker="AAA.MI", anno=2024)["stato"] == "done"
    fp._spesa_path().write_text("{rotto", encoding="utf-8")
    rifiuto = _proponi(env, ticker="BBB.MI", anno=2025)
    assert rifiuto["stato"] == "refused" and "illeggibile" in rifiuto["motivo"]
    assert len(env.stato.chiamate) == 1


# ---------------------------------------------------------------- (c) la stessa richiesta non si ripaga

def test_riprova_della_stessa_richiesta_non_ripaga(env):
    env.stato.risposte = ["non so", json.dumps(RISPOSTA)]
    primo = _proponi(env)
    assert primo["stato"] == "error" and primo["riprovabile"] is True and primo["riprova_paga"] is False
    secondo = _proponi(env, riprova=True)
    assert len(env.stato.chiamate) == 1 and len(env.usage) == 1   # nessuna seconda spesa
    assert secondo["stato"] == "error" and secondo["cached"] is True and secondo["riprovabile"] is False
    assert "gia' pagata" in secondo["dettaglio"] or "already paid" in secondo["dettaglio"]
    env.stato.modello = "nova/pro"   # altro modello: altra richiesta, una nuova chiamata
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    assert _proponi(env, riprova=True)["stato"] == "done"
    assert len(env.stato.chiamate) == 2


def test_riprova_rilegge_la_risposta_pagata_con_il_parser_attuale(env):
    """Parser corretto dopo la prima risposta: «Riprova» la rilegge gratis, nessuna nuova chiamata."""
    vero = fp._parse_testo
    rotto = [True]

    def parser(testo):
        if rotto[0]:
            raise ValueError("parser vecchio")
        return vero(testo)
    env.mp.setattr(fp, "_parse_testo", parser)
    assert _proponi(env)["stato"] == "error"
    rotto[0] = False
    out = _proponi(env, riprova=True)
    assert out["stato"] == "done" and out["cached"] is True and out["costo_eur"] == pytest.approx(0.0021)
    assert len(env.stato.chiamate) == 1 and len(env.usage) == 1


# ---------------------------------------------------------------- (e) lingua senza ripiego

def test_lingua_mancante_dichiarata_non_ripiegata(tmp_path):
    proposta = _proposta()
    proposta["lingua"] = None
    path = tmp_path / "h1.pdf"
    path.write_bytes(semestrale_nova())
    profilo = fp.profilo_ir("NOVA.DE", nome="Nova AG", ir_urls=[URL], proposta=proposta,
                            sha256="0" * 64, modello="m", lingua_rilevata=None)
    assert profilo["lingua"] is None and profilo["verifica"].get("lingua") is None
    out = fp.verifica_proposta(path, url=URL, profilo=profilo, proposta=proposta)
    assert out["salvabile"] is False and any("lingua" in m for m in out["motivi"])


def test_lingua_senza_regola_di_verifica_dichiarata(tmp_path):
    proposta = _proposta()
    proposta["lingua"] = "ja"
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is False and any("ja" in m and "lingua" in m for m in out["motivi"])


def test_lingua_rilevata_usata_se_il_modello_tace(tmp_path):
    proposta = _proposta()
    proposta["lingua"] = None
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)   # lingua_rilevata="en"
    assert out["salvabile"] is True, out


# ---------------------------------------------------------------- (f) regex a backtracking polinomiale

@pytest.mark.parametrize("pattern", [r".*a.*b.*c", r"Outlook.*for.*year", r"[^x]*y", r"(?s)Outlook.+Risks",
                                     r"\W*end", r"\D+2026", r"(?:.|\n)*Risks", r"[\s\S]*Risks",
                                     r"x.{0,5000}y.*z"])
def test_regex_polinomiali_rifiutate(pattern):
    motivo = fp._regex_sicura(pattern)
    assert motivo and "regex non valida" in motivo, pattern


@pytest.mark.parametrize("pattern", [r"Outlook.*", r"Risks? and opportunities", r"\S+ Report \d{4}",
                                     r"[^\n]*Outlook", r"Note \d+\.\d+ .*", r"(?:Risk|Opportunity) Report"])
def test_regex_ragionevoli_ammesse(pattern):
    assert fp._regex_sicura(pattern) is None


def test_regex_polinomiale_reale_sarebbe_lenta():
    """Misura che giustifica il rifiuto: su una sola riga lunga la regex costa secondi."""
    testo = "a" * 400 + "b" * 400
    inizio = time.monotonic()
    re.search(r".*a.*b.*c", testo)
    assert time.monotonic() - inizio > 0.05


# ---------------------------------------------------------------- seguito REV_G2b (A3-A8, B1, forma del rifiuto)

CHIAVI_RIFIUTO = {"stato", "sha256", "motivo", "variabile", "riprova_tra_s", "riprovabile"}


class _Timeout(Exception):
    pass  # nessuno status: puo' essere arrivato dopo la generazione


class _Status400(Exception):
    status_code = 400


def _scrivi_registro(dati):
    fp._spesa_path().parent.mkdir(parents=True, exist_ok=True)
    fp._spesa_path().write_text(dati, encoding="utf-8")


def test_forma_del_rifiuto_completa(env):
    assert _proponi(env, anno=2026)["stato"] == "done"
    rifiuto = _proponi(env, anno=2025)
    assert set(rifiuto) == CHIAVI_RIFIUTO
    assert rifiuto["riprovabile"] is True and rifiuto["riprova_tra_s"] > 0
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", "")
    rotto = _proponi(env, ticker="KORE.MI", anno=2024)
    assert set(rotto) == CHIAVI_RIFIUTO and rotto["riprovabile"] is False and rotto["riprova_tra_s"] is None


def test_a3_chiamate_fallite_attivano_throttle_e_spesa_incerta(env):
    env.stato.errore = _Timeout("timeout dopo 600 s")
    assert _proponi(env, anno=2026)["stato"] == "error"
    for anno in (2025, 2024, 2026):  # tre altri click: nessuna chiamata
        assert _proponi(env, anno=anno)["stato"] == "refused"
    assert len(env.stato.chiamate) == 1
    # spesa incerta contata al massimo stimato: sotto il tetto il pulsante resta usabile
    env.stato.errore = None
    assert _proponi(env, ticker="KORE.MI", anno=2025)["stato"] == "done"
    assert len(env.stato.chiamate) == 2
    reg = json.loads(fp._spesa_path().read_text(encoding="utf-8"))
    assert reg["incerte"] == 1 and reg["costo_incerto_eur"] > 0


def test_a3_errore_4xx_prima_della_generazione_non_e_spesa(env):
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    env.stato.errore = _Status400("bad request")
    out = _proponi(env, anno=2026)
    assert out["stato"] == "error" and out["spesa_incerta"] is False
    env.stato.errore = None
    assert _proponi(env, anno=2025)["stato"] == "done"


@pytest.mark.parametrize("contenuto", [
    '{"giorno": "%s", "costo_eur": NaN, "chiamate": 1, "senza_costo": 0}',
    '{"giorno": "%s", "costo_eur": "0.5", "chiamate": 1, "senza_costo": 0}',
    '{"giorno": "%s", "costo_eur": -1, "chiamate": 1, "senza_costo": 0}',
    '{"giorno": "%s", "costo_eur": Infinity, "chiamate": 1, "senza_costo": 0}',
    '{"giorno": "%s", "chiamate": 1, "senza_costo": 0}',
    '{"giorno": "%s", "costo_eur": 0.1, "chiamate": true, "senza_costo": 0}',
    '{"giorno": "%s", "costo_eur": 0.1, "chiamate": 1, "senza_costo": 0, "ultime": {"X": NaN}}',
    '{"giorno": "non-una-data", "costo_eur": 0.1, "chiamate": 1, "senza_costo": 0}',
    '["%s"]',
])
def test_a4_registro_con_forma_non_valida_tetto_resta_attivo(env, contenuto):
    """Mutazione «forma non valida -> registro vuoto» (REV_G2b A4): qui cade."""
    oggi = fp._oggi().isoformat()
    _scrivi_registro(contenuto % oggi if "%s" in contenuto else contenuto)
    out = _proponi(env)
    assert out["stato"] == "refused" and out["variabile"] == "FILING_AI_TETTO_GIORNO_EUR"
    assert out["riprovabile"] is False and env.stato.chiamate == []


def test_a4_costo_nan_dal_listino_non_entra_nel_registro(env):
    env.stato.costo = float("nan")
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    assert _proponi(env, anno=2026)["stato"] == "done"
    testo = fp._spesa_path().read_text(encoding="utf-8")
    assert "NaN" not in testo and json.loads(testo)["incerte"] == 1  # costo ignoto: incerto al massimo


def test_a5_giorno_nel_futuro_orologio_incoerente(env):
    _scrivi_registro('{"giorno": "2999-01-01", "costo_eur": 5.0, "chiamate": 9, "senza_costo": 0}')
    out = _proponi(env)
    assert out["stato"] == "refused" and ("orologio" in out["motivo"] or "clock" in out["motivo"])
    assert env.stato.chiamate == []


def test_a5_giorno_passato_azzera_la_spesa_ma_non_il_throttle(env):
    import time as _t
    _scrivi_registro('{"giorno": "2000-01-01", "costo_eur": 5.0, "chiamate": 9, "senza_costo": 3, '
                     '"ultime": {"NOVA.DE": %f}}' % _t.time())
    assert _proponi(env)["variabile"] == "FILING_AI_INTERVALLO_TICKER_S"
    assert _proponi(env, ticker="KORE.MI")["stato"] == "done"


def test_a6_b1_riprova_con_nome_assente_non_ripaga(env):
    env.stato.risposte = ["non so", json.dumps(RISPOSTA)]
    primo = fp.proponi("NOVA.DE", nome="Nova AG", path=env.pdf[2026], url=URL)
    assert primo["riprova_paga"] is False
    dichiarato = fp.proponi("NOVA.DE", nome="Nova AG", path=env.pdf[2026], url=URL)
    assert dichiarato["riprova_paga"] is False
    rifatta = fp.proponi("NOVA.DE", nome=None, path=env.pdf[2026], url=URL, riprova=True,
                         nome_motivo="Yahoo senza nome")
    assert len(env.stato.chiamate) == 1 and rifatta["cached"] is True


def test_a8_tentativo_interrotto_da_un_riavvio_e_spesa_incerta(env):
    import time as _t
    oggi = fp._oggi().isoformat()
    vecchio = _t.time() - fp.SCADENZA_TENTATIVO_S - 10
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 0, '
                     '"ultime": {"ZZB.MI": %f}, "in_corso": {"ZZB.MI": %f}}' % (oggi, vecchio, vecchio))
    out = _proponi(env)
    assert out["stato"] == "refused" and env.stato.chiamate == []


def test_a8_tentativo_scritto_prima_della_chiamata(env):
    visto = {}

    def create(**kwargs):
        visto.update(json.loads(fp._spesa_path().read_text(encoding="utf-8")))
        return NS(content=[lc.TextBlock(json.dumps(RISPOSTA))],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0))
    env.mp.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    assert _proponi(env)["stato"] == "done"
    assert "NOVA.DE" in visto["in_corso"] and "NOVA.DE" in visto["ultime"]
    finale = json.loads(fp._spesa_path().read_text(encoding="utf-8"))
    assert "NOVA.DE" not in finale["in_corso"] and finale["costo_eur"] == pytest.approx(0.0021)


def test_b1_nome_assente_dichiarato_e_mai_il_ticker(env):
    out = fp.proponi("NOVA.DE", nome=None, path=env.pdf[2026], url=URL, nome_motivo="Yahoo senza nome")
    prompt = env.stato.chiamate[0]["messages"][0]["content"]
    assert "(nome non disponibile)" in prompt and "emittente NOVA.DE" not in prompt
    assert any("nome emittente non disponibile: Yahoo senza nome" in a for a in out["avvisi"])
    assert out["salvabile"] is True and out["regole"]["emittente"]["origine"] == "modello"


def test_b1_nome_assente_senza_frase_del_modello_non_salvabile(env):
    risposta = {k: v for k, v in RISPOSTA.items() if k != "emittente"}
    env.stato.risposte = [json.dumps(risposta)]
    out = fp.proponi("NOVA.DE", nome=None, path=env.pdf[2026], url=URL, nome_motivo="portafoglio illeggibile")
    assert out["salvabile"] is False
    assert any("prova dell'emittente assente" in m for m in out["motivi"])


def test_a7_lock_di_spesa_per_titolo_non_globale(env):
    import threading
    entrato, libera = threading.Event(), threading.Event()

    def create(**kwargs):
        if not entrato.is_set():
            entrato.set()
            libera.wait(10)
        return NS(content=[lc.TextBlock(json.dumps(RISPOSTA))],
                  usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0, cost_usd=0.0))
    env.mp.setattr(lc, "OpenRouterClient", lambda: NS(messages=NS(create=create)))
    lento = threading.Thread(target=lambda: _proponi(env, ticker="AAA.MI", anno=2024), daemon=True)
    lento.start()
    assert entrato.wait(10)
    inizio = time.monotonic()
    assert _proponi(env, ticker="BBB.MI", anno=2025)["stato"] == "done"  # non attende AAA.MI
    assert time.monotonic() - inizio < 5
    libera.set()
    lento.join(10)


# ---------------------------------------------------------------- correzione d'intento del coordinatore

def test_incerto_contato_al_massimo_stimato_fino_al_tetto(env):
    """Spesa incerta = costo massimo stimato, etichettato «incerto»; blocco solo oltre il tetto."""
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    env.stato.errore = _Timeout("timeout")
    fallita = _proponi(env, ticker="AAA.MI", anno=2024)
    massimo = fallita["costo_incerto_max_eur"]
    assert fallita["spesa_incerta"] is True and massimo > 0
    env.stato.errore = None
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", str(massimo + 0.0001))
    assert _proponi(env, ticker="BBB.MI", anno=2025)["stato"] == "done"   # incerto < tetto: usabile
    rifiuto = _proponi(env, ticker="CCC.MI", anno=2026)                     # incerto + misurato >= tetto
    assert rifiuto["stato"] == "refused" and rifiuto["variabile"] == "FILING_AI_TETTO_GIORNO_EUR"
    assert "incert" in rifiuto["motivo"] or "uncertain" in rifiuto["motivo"]
    assert rifiuto["riprovabile"] is True


def test_incerto_senza_massimo_stimabile_blocco_dichiarato(env):
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    env.stato.prezzi = None
    env.stato.errore = _Timeout("timeout")
    fallita = _proponi(env, ticker="AAA.MI", anno=2024)
    assert fallita["spesa_incerta"] is True and fallita["costo_incerto_max_eur"] is None
    env.stato.errore = None
    rifiuto = _proponi(env, ticker="BBB.MI", anno=2025)
    assert rifiuto["stato"] == "refused" and ("non misurabile" in rifiuto["motivo"]
                                             or "cannot be measured" in rifiuto["motivo"])


def test_tentativo_interrotto_con_massimo_contato_non_blocca(env):
    import time as _t
    oggi = fp._oggi().isoformat()
    vecchio = _t.time() - fp.SCADENZA_TENTATIVO_S - 10
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 0, '
                     '"ultime": {"ZZB.MI": %f}, "in_corso": {"ZZB.MI": {"inizio": %f, "costo_max_eur": 0.01}}}'
                     % (oggi, vecchio, vecchio))
    assert _proponi(env)["stato"] == "done"
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", "0.005")
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    assert _proponi(env, ticker="KORE.MI", anno=2025)["stato"] == "refused"


def test_giorno_del_tetto_e_quello_locale_europe_rome(env):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from datetime import date, timezone
    assert fp._oggi() == datetime.now(ZoneInfo("Europe/Rome")).date()
    # 23:30 UTC del 4 ottobre = 01:30 del 5 a Roma: il tetto e' gia' del giorno nuovo
    assert fp._oggi(datetime(2026, 10, 4, 23, 30, tzinfo=timezone.utc)) == date(2026, 10, 5)
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", "0")
    rifiuto = _proponi(env)
    assert "Europe/Rome" in rifiuto["motivo"]


# ---------------------------------------------------------------- seconda revisione C3: tentativi «in corso»

def _ieri():
    from datetime import timedelta
    return (fp._oggi() - timedelta(days=1)).isoformat()


def test_c3_s7_tentativo_morto_dello_stesso_titolo_non_sparisce(env):
    """Processo ucciso a meta' chiamata su NOVA.DE (massimo 0,6), riavvio, nuovo click su NOVA.DE:
    il tentativo diventa spesa incerta PRIMA di essere sostituito, e conta nel tetto."""
    import time as _t
    ora = _t.time() - 10
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.5, "chiamate": 1, "senza_costo": 0, '
                     '"ultime": {"NOVA.DE": %f}, "in_corso": {"NOVA.DE": {"inizio": %f, "costo_max_eur": 0.6}}}'
                     % (fp._oggi().isoformat(), ora, ora))
    env.mp.setenv("FILING_AI_INTERVALLO_TICKER_S", "0")
    env.mp.setenv("FILING_AI_TETTO_GIORNO_EUR", "1.0")
    rifiuto = _proponi(env)
    assert rifiuto["stato"] == "refused" and rifiuto["variabile"] == "FILING_AI_TETTO_GIORNO_EUR"
    reg = json.loads(fp._spesa_path().read_text(encoding="utf-8"))
    assert reg["costo_incerto_eur"] == pytest.approx(0.6) and reg["in_corso"] == {}
    assert env.stato.chiamate == []


def test_c3_s8_tentativo_senza_massimo_di_ieri_non_blocca_per_sempre(env):
    import time as _t
    ieri = _t.time() - 86_400
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 1, '
                     '"ultime": {"ZZB.MI": %f}, "in_corso": {"ZZB.MI": %f}}' % (_ieri(), ieri, ieri))
    out = _proponi(env)
    assert out["stato"] == "done", out
    reg = json.loads(fp._spesa_path().read_text(encoding="utf-8"))
    assert reg["in_corso"] == {} and reg["senza_costo"] == 0
    chiusi = [v for v in reg["storico"] if v["ticker"] == "ZZB.MI"]
    assert chiusi and chiusi[0]["giorno"] == _ieri() and chiusi[0]["costo_incerto_eur"] is None


def test_c3_s8_blocco_senza_massimo_dichiarato_fino_a_domani_e_davvero_finisce(env):
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 1}' % fp._oggi().isoformat())
    oggi = _proponi(env)
    assert oggi["stato"] == "refused" and oggi["riprovabile"] is True and oggi["riprova_tra_s"] > 0
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 1}' % _ieri())
    assert _proponi(env)["stato"] == "done"   # domani il blocco e' davvero finito


def test_c3_s9_tentativo_di_ieri_conta_solo_ieri(env):
    import time as _t
    ieri = _t.time() - 86_400
    _scrivi_registro('{"giorno": "%s", "costo_eur": 0.0, "chiamate": 1, "senza_costo": 0, "ultime": {}, '
                     '"in_corso": {"ZZB.MI": {"inizio": %f, "costo_max_eur": 2.0}}}' % (_ieri(), ieri))
    assert _proponi(env)["stato"] == "done"
    reg = json.loads(fp._spesa_path().read_text(encoding="utf-8"))
    assert reg["costo_incerto_eur"] == 0 and reg["in_corso"] == {}
    assert any(v["giorno"] == _ieri() and v["costo_incerto_eur"] == 2.0 for v in reg["storico"])
