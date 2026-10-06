"""Candidati LEI per l'attivazione automatica (fase B, task 2): mai un LEI indovinato.

Nessuna rete: requests.get finto. Nomi e LEI inventati.
"""
import json

import requests

import bellomberg.storage.negozi_privati as np_
from bellomberg.market_data import esef

LEI_NOVA = "999900NOVA0000000001"
LEI_NOVA2 = "999900NOVA0000000002"
LEI_KORE = "999900KORE0000000001"


def _entita(nome, lei):
    return {"id": lei, "attributes": {"name": nome, "identifier": lei}}


class _Resp:
    def __init__(self, data, ok=True, status=200):
        self._data, self.ok, self.status_code = data, ok, status

    def json(self):
        return {"data": self._data}


def _repo(monkeypatch, tmp_path, tabella, negozio=None, status=200):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "prova@example.com")
    p = tmp_path / "lei.json"
    if negozio is not None:
        p.write_text(negozio if isinstance(negozio, str) else json.dumps(negozio), encoding="utf-8")
    monkeypatch.setattr(np_, "PERCORSO_LEI", str(p))
    query = []

    def get(url, params=None, headers=None, timeout=None):
        val = json.loads(params["filter"])[0]["val"]
        query.append(val)
        if status != 200:
            return _Resp([], ok=False, status=status)
        for k, v in tabella.items():
            if val.lower() == k.lower():
                return _Resp(v)
        return _Resp([])

    monkeypatch.setattr(requests, "get", get)
    return query


def _niente_ritmo():
    return None


def test_negozio_privato_vince(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {}, negozio={"NOVA.MI": LEI_NOVA})
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "univoco" and r["candidati"][0]["lei"] == LEI_NOVA
    assert r["candidati"][0]["origine"] == "negozio" and query == []


def test_negozio_illeggibile_e_errore_mai_ricerca_per_nome(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {"%Nova S.p.A.%": [_entita("NOVA S.P.A.", LEI_NOVA)]}, negozio="{rotto")
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "errore" and "lei_emittenti" in r["motivo"] and query == []


def test_nome_esatto_unico_e_univoco(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%Nova S.p.A.%": [_entita("NOVA S.P.A.", LEI_NOVA)]})
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "univoco"
    assert r["candidati"] == [{"lei": LEI_NOVA, "nome": "NOVA S.P.A.", "origine": "nome"}]


def test_due_omonimi_esatti_sono_ambigui(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%Nova S.p.A.%": [_entita("NOVA S.P.A.", LEI_NOVA), _entita("Nova SpA", LEI_NOVA2)]})
    r = esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "ambiguo" and {c["lei"] for c in r["candidati"]} == {LEI_NOVA, LEI_NOVA2}


def test_stesso_lei_ripetuto_resta_univoco(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%Nova S.p.A.%": [_entita("NOVA S.P.A.", LEI_NOVA), _entita("Nova S.p.A.", LEI_NOVA)]})
    assert esef.candidati_lei("NOVA.MI", "Nova S.p.A.", ritmo=_niente_ritmo)["stato"] == "univoco"


def test_unico_risultato_non_esatto_e_da_confermare(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%Nova Group S.p.A.%": [_entita("NOVA GROUP ITALIA S.P.A.", LEI_NOVA)]})
    r = esef.candidati_lei("NOVA.MI", "Nova Group S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "ambiguo" and r["candidati"][0]["origine"] == "nome_simile"
    # resolve_lei (storico DCF) conserva il suo contratto: un solo risultato e' accettato
    lei, nota = esef.resolve_lei("NOVA.MI", company_name="Nova Group S.p.A.")
    assert lei == LEI_NOVA and "fallback dichiarato" in nota


def test_troppi_simili_sono_nessuno(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {"%Kore S.p.A.%": [_entita(f"Kore Uno {i} S.p.A.", f"999900KORE00000000{i:02d}") for i in range(6)]})
    r = esef.candidati_lei("KORE.MI", "Kore S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "nessuno" and "6" in r["motivo"]


def test_nessuna_entita(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {})
    r = esef.candidati_lei("KORE.DE", "Kore AG", ritmo=_niente_ritmo)
    assert r["stato"] == "nessuno" and r["candidati"] == [] and len(query) == 2


def test_errore_http_e_errore(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {}, status=503)
    r = esef.candidati_lei("KORE.MI", "Kore S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "errore" and "503" in r["motivo"]


def test_contatto_mancante_la_ricerca_parte_lo_stesso(monkeypatch, tmp_path):
    # Riscritto sulla decisione PM 05/10 sera: prima senza SEC_CONTACT_EMAIL era «errore»;
    # ora filings.xbrl.org si interroga con lo User-Agent generico (la mail la esige solo la SEC).
    query = _repo(monkeypatch, tmp_path, {"%Kore S.p.A.%": [_entita("KORE S.P.A.", LEI_KORE)]})
    monkeypatch.delenv("SEC_CONTACT_EMAIL")
    get_finto, visti = requests.get, []
    monkeypatch.setattr(requests, "get", lambda url, **k: visti.append(k.get("headers")) or get_finto(url, **k))
    r = esef.candidati_lei("KORE.MI", "Kore S.p.A.", ritmo=_niente_ritmo)
    assert r["stato"] == "univoco" and r["candidati"][0]["lei"] == LEI_KORE, r
    assert query and visti and visti[0]["User-Agent"] == esef.UA_GENERICO


def test_senza_nome_nessuna_ricerca(monkeypatch, tmp_path):
    query = _repo(monkeypatch, tmp_path, {})
    r = esef.candidati_lei("KORE.MI", None, ritmo=_niente_ritmo)
    assert r["stato"] == "nessuno" and query == []


def test_ritmo_chiamato_per_ogni_query(monkeypatch, tmp_path):
    _repo(monkeypatch, tmp_path, {})
    battiti = []
    esef.candidati_lei("KORE.DE", "Kore AG", ritmo=lambda: battiti.append(1))
    assert len(battiti) == 2


def test_paesi_esef_a_runtime_in_maiuscolo_e_con_malta():
    # il sorgente li scrive in minuscolo (cancello privacy case-sensitive sui ticker corti): a
    # runtime devono restare i 31 codici maiuscoli confrontati col campo country di GLEIF
    malta = "mt".upper()
    assert malta in esef._PAESI_ESEF
    assert len(esef._PAESI_ESEF) == 31
    assert all(p == p.upper() and len(p) == 2 for p in esef._PAESI_ESEF)
    assert {"IT", "DE", "GB", "NO"} <= esef._PAESI_ESEF
