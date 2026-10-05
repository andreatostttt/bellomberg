# -*- coding: utf-8 -*-
"""Ricevute della data di deposito: prove END-TO-END sul collegamento VERO (D4b, da RV-D4 05/10/2026).

Qui NON si finge la riverifica: `sdir.riverifica_deposito` (eMarket SDIR / 1INFO-SDIR), `depositi_ue`
(instradatore + `ue_amf.riverifica_ricevuta`) e le regole di trade_idea_sources girano veri. Si fingono solo
i trasporti di rete (fixture dei moduli) e l'identita' UE GLEIF (provata in tests/test_identita_ue.py).
Ogni ricevuta accettata viene poi MANOMESSA in modo coerente (archivio riscritto, sha ricalcolato: chi ha il
disco) e la riverifica senza rete deve rifiutarla. Emittenti, ISIN, protocolli e date sono sintetici.
"""
import json
from copy import deepcopy
from hashlib import sha256

import pytest

from bellomberg.agents import trade_idea_sources as sources
from bellomberg.market_data import ue_amf
from test_trade_idea_pm_sources import URL, WEBSITE, transport, measured_no_real_transports  # noqa: F401
from test_emarket_deposito_riverifica import amb, _comunicati, SEMESTRALE  # noqa: F401

IT_ID = {"ticker": "ACME.MI", "name": "ACME SINTETICA", "exchange": "TEST", "currency": "EUR", "status": "confirmed"}
IT_COVER = "ACME SINTETICA\nRELAZIONE FINANZIARIA SEMESTRALE AL 30 GIUGNO 2026\nNarrativa sintetica.\n"
EU_ISIN = "FR00ZZSYNT08"
EU_LEI = "9999ZZSYNTETICA00080"
EU_ID = {"ticker": "ZZSYN.PA", "name": "Synthetic issuer", "exchange": "TEST", "currency": "EUR",
         "status": "confirmed", "isin": EU_ISIN}
EU_COVER = "Synthetic issuer\nDocument d'enregistrement universel 2025\nYear ended 31 December 2025\nNarrative.\n"


def _ingest(tmp_path, as_of, *, text, identity):
    download, _ = transport(text)
    return sources.ingest_document_sources(identity["ticker"], identity, as_of, [{"url": URL}],
        archive_root=tmp_path, issuer_website=WEBSITE, download=download)


def _sealed(result):
    return result["receipt"]["documents"][0]["metadati"]["pm_source_verification"]["deposit_receipt"]


def _rewrite(tmp_path, sealed, mutate):
    """Manomissione COERENTE: nuovo archivio + nuovo sha del sigillo."""
    root = tmp_path / "pm-public-documents"
    value = {key: deepcopy(item) for key, item in sealed.items() if key != "sha256"}
    mutate(value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    digest = sha256(raw).hexdigest()
    (root / "publication-receipts" / (digest + ".json")).write_bytes(raw)
    return {**value, "sha256": digest}, root


TAMPER_IT = {
    "data": lambda v: v.update(data_deposito="2026-07-02"),
    "data_anche_fonte": lambda v: (v.update(data_deposito="2026-07-02"),
                                   v["ricevuta_fonte"].update(data_deposito="2026-07-02")),
    "via_ricevuta_fonte_e_data": lambda v: (v.pop("ricevuta_fonte", None), v.update(data_deposito="2026-07-02")),
    "ora_anche_fonte": lambda v: (v.update(ora_deposito="08:00"), v["ricevuta_fonte"].update(ora_deposito="08:00")),
    "protocollo_anche_fonte": lambda v: (v.update(protocollo="X1"), v["ricevuta_fonte"].update(protocollo="X1")),
}


def _check_it(sealed, root):
    sources.verify_deposit_companion(sealed, root, "ACME.MI", "semestrale", "2026-06-30", "2026-09-28",
                                     text=IT_COVER)


# ------------------------------------------------------------------------------------------------ eMarket SDIR
@pytest.mark.parametrize("campo", sorted(TAMPER_IT))
def test_emarket_receipt_end_to_end_rejects_every_coherent_tampering(amb, tmp_path, campo):
    _comunicati(amb, "semestrale", SEMESTRALE)
    sealed = _sealed(_ingest(tmp_path, "2026-09-28", text=IT_COVER, identity=IT_ID))
    assert sealed["ricevuta_fonte"]["risposte_salvate"] and sealed["data_deposito"] == "2026-08-06"
    _check_it(sealed, tmp_path / "pm-public-documents")
    changed, root = _rewrite(tmp_path, sealed, TAMPER_IT[campo])
    with pytest.raises(ValueError, match="da fornire dal PM"):
        _check_it(changed, root)


# ------------------------------------------------------------------------------------------------ 1INFO-SDIR
@pytest.fixture
def oneinfo(monkeypatch, tmp_path):
    from datetime import date
    from bellomberg.market_data import borsa_italiana as bi, oneinfo_sdir as oi
    from test_sdir_instradamento import _fixture, ISIN_ACME
    monkeypatch.setattr(bi, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(bi, "oggi_roma", lambda: date(2026, 10, 5))
    monkeypatch.setattr(bi, "voce_ticker_o_auto", lambda t: (
        {"isin": ISIN_ACME, "emarket": None, "oneinfo": 99901, "negozio": "confermato"}, None, None))
    monkeypatch.setattr(bi, "mercato_listino", lambda t: None)
    monkeypatch.setattr(oi, "_richiesta", lambda m, u, d=None: (200, _fixture("oi_documenti_ok.json")))


@pytest.mark.parametrize("campo", sorted(TAMPER_IT))
def test_oneinfo_receipt_end_to_end_rejects_every_coherent_tampering(oneinfo, tmp_path, campo):
    sealed = _sealed(_ingest(tmp_path, "2026-09-28", text=IT_COVER, identity=IT_ID))
    assert sealed["sdir"] == "1INFO-SDIR" and sealed["ricevuta_fonte"]["risposte_salvate"]
    _check_it(sealed, tmp_path / "pm-public-documents")
    changed, root = _rewrite(tmp_path, sealed, TAMPER_IT[campo])
    with pytest.raises(ValueError, match="da fornire dal PM"):
        _check_it(changed, root)


# ------------------------------------------------------------------------------------------------ UE (AMF)
def _row(uid, when, title):
    return {"uin_idt_uin": uid, "informationdeposee_inf_dat_emt": when,
            "informationdeposee_inf_tit_inf": title, "informationdeposee_inf_lng_inf": "Français",
            "sous_type_d_information": "Rapport financier annuel",
            "subtype_of_information": "Annual financial reports and audit reports",
            "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/ZZZ/8888/01/FC%s.pdf" % uid,
            "identificationsociete_iso_cd_isi": EU_ISIN, "identificationsociete_iso_cd_lei": EU_LEI,
            "identificationsociete_iso_nom_soc": "ACME SINTETICA SA", "informationdeposee_inf_upg_inf_inf_upg_sts": None}


@pytest.fixture
def amf(monkeypatch, tmp_path):
    from bellomberg.market_data import identita_ue
    identity = {"stato": "ok", "errore": None, "motivo": None, "isin": EU_ISIN, "lei": EU_LEI,
                "isin_chiamante": EU_ISIN, "fonte": "identita' sintetica (test)"}
    monkeypatch.setattr(identita_ue, "risolvi_identita_ue", lambda *a, **k: dict(identity))
    monkeypatch.setattr(identita_ue, "riverifica_identita", lambda ricevuta, **k: (ricevuta == identity, "test"))
    monkeypatch.setattr(ue_amf, "CACHE_DIR", str(tmp_path / "cache_ue"))
    monkeypatch.setattr(ue_amf, "PAUSA_S", 0)

    def serve(rows):
        body = json.dumps({"total_count": len(rows), "results": rows}).encode()
        monkeypatch.setattr(ue_amf, "_scarica", lambda url: (200, body))
    return serve


def test_eu_deposit_after_midnight_in_rome_is_not_public_end_to_end(amf, tmp_path):
    # 30/04 22:30 UTC = 01/05 00:30 a Parigi/Roma: con cutoff 30/04 non e' pubblico, con cutoff 01/05 si'
    amf([_row("ZZ000093", "2026-04-30T22:30:00+00:00", "Document d'enregistrement universel 2025")])
    with pytest.raises(sources.SourceIngestionError) as blocked:
        _ingest(tmp_path / "a", "2026-04-30", text=EU_COVER, identity=EU_ID)
    assert "after the cutoff" in blocked.value.receipt["documents"][0]["reason"]
    row = _ingest(tmp_path / "b", "2026-05-01", text=EU_COVER, identity=EU_ID)["receipt"]["documents"][0]
    assert row["filed_date"] == "2026-05-01"
    assert "del 01/05/2026 alle 00:30 ora di Europe/Paris" in (
        row["metadati"]["pm_source_verification"]["publication_declaration"])


def test_eu_ambiguity_with_utc_hours_and_no_declared_zone_is_rejected_end_to_end(amf, tmp_path):
    # forma VERA di ue_amf: ambiguo con fuso None e candidati in ore UTC -> la scelta non puo' fissare l'istante
    amf([_row("ZZ000090", "2026-03-10T09:00:00+00:00", "Rapport financier annuel 2025"),
         _row("ZZ000091", "2026-04-30T22:30:00+00:00", "Document d'enregistrement universel 2025")])
    direct = ue_amf.get_data_deposito("ZZSYN.PA", tipo="annuale", periodo_fine="2025-12-31", isin=EU_ISIN)
    assert direct["stato"] == "ambiguo" and direct["fuso"] is None
    with pytest.raises(sources.SourceIngestionError) as blocked:
        _ingest(tmp_path, "2026-04-30", text=EU_COVER, identity=EU_ID)
    assert "time zone is unknown" in blocked.value.receipt["documents"][0]["reason"]


TAMPER_EU = {
    "data_prima": lambda v: v.update(data_deposito="2026-03-01"),
    "ora": lambda v: v.update(ora_deposito="05:00"),
    "fuso": lambda v: v.update(fuso="Asia/Tokyo"),
    "paese": lambda v: v.update(paese="BE"),
    "sha_liste": lambda v: v.update(sha256_liste={k: "0" * 64 for k in v["sha256_liste"]}),
    "risposte": lambda v: v.update(risposte_salvate={k: "{}" for k in v["risposte_salvate"]}),
    "titolo": lambda v: v.update(titolo="Document d'enregistrement universel 2025 (bis)"),
    "natura": lambda v: v.update(natura_data="pubblicazione_dichiarata"),
}


@pytest.mark.parametrize("campo", sorted(TAMPER_EU))
def test_eu_receipt_end_to_end_rejects_every_coherent_tampering(amf, tmp_path, campo):
    amf([_row("ZZ000092", "2026-03-20T17:45:00+00:00", "Document d'enregistrement universel 2025")])
    sealed = _sealed(_ingest(tmp_path, "2026-04-30", text=EU_COVER, identity=EU_ID))
    root = tmp_path / "pm-public-documents"
    sources.verify_deposit_companion(sealed, root, "ZZSYN.PA", "annuale", "2025-12-31", "2026-04-30", text=EU_COVER)
    changed, root = _rewrite(tmp_path, sealed, TAMPER_EU[campo])
    with pytest.raises(ValueError, match="da fornire dal PM"):
        sources.verify_deposit_companion(changed, root, "ZZSYN.PA", "annuale", "2025-12-31", "2026-04-30",
                                         text=EU_COVER)
