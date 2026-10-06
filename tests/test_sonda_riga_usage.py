"""Riga di log «usage» delle chiamate laterali (sonda modelli, reflection, action table).

HANDOFF-4 voce 6 (05/10 sera, Opus 5.5): nel log della run la sonda scriveva
«in=None out=None ... costo=0,00 EUR» mentre il journal e current_run.json avevano
token e costo veri. Due cause: la riga leggeva usage["in"]/["out"] (la sonda consegna
input_tokens/output_tokens) e "%.2f" arrotondava a 0,00 un costo sotto il centesimo.

Qui il blackboard e' finto ma record_usage e' quello VERO (stessa normalizzazione
della run); il listino e' sostituito (nessun FX, nessuna rete, nessuna chiamata AI).
Numeri inventati.
"""
import threading
from types import SimpleNamespace

import pytest

import bellomberg.core.llm_pricing as llm_pricing
import bellomberg.agents.consigliere_multi as cm
from bellomberg.agents.specialists.base import Blackboard


class _BBFinto:
    """Il minimo che Blackboard.record_usage tocca: niente heartbeat su disco."""

    def __init__(self):
        self.language = "it"
        self._lock = threading.Lock()
        self.usage_log = []

    def _write_heartbeat(self):
        pass

    def record_usage(self, *a, **k):
        return Blackboard.record_usage(self, *a, **k)


def _listino(monkeypatch, cost, status="ok"):
    def _cost_eur(model, usage, cache_ttl=None):
        return {"cost": cost, "status": status, "fx_rate": 0.9 if cost is not None else None,
                "fx_source": "finto"}
    monkeypatch.setattr(llm_pricing, "cost_eur", _cost_eur)


def _riga(capsys, agent, usage):
    capsys.readouterr()
    entry = cm._record_side_usage(_BBFinto(), agent, usage)
    out = capsys.readouterr().out
    righe = [r for r in out.splitlines() if " usage: " in r]
    assert len(righe) == 1, out
    return entry, righe[0]


def _usage_sonda_ok():
    # forma esatta di _probe_usage in consigliere_multi: Usage.to_dict() + model/api_calls/...
    return {"input_tokens": 37, "output_tokens": 412, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0, "reasoning_tokens": 300,
            "cost_usd": 0.000409, "reasoning_forzato": None, "request_ids": ["rq-zz1"],
            "model": "zzlab/finto-1", "api_calls": 1, "duration_s": 1.23, "status": "ok"}


def test_sonda_ok_mostra_token_veri_e_costo_sotto_il_cent(monkeypatch, capsys):
    _listino(monkeypatch, 0.000351)
    entry, riga = _riga(capsys, "_probe:zzlab/finto-1", _usage_sonda_ok())
    assert entry["in"] == 37 and entry["out"] == 412      # la entry del conto li aveva gia'
    assert "in=37" in riga and "out=412" in riga, riga
    assert "None" not in riga, riga
    assert "costo=0,0004 EUR" in riga, riga
    assert "0,00 EUR" not in riga, riga


def test_sonda_fallita_dichiara_nd_col_motivo_mai_zero(monkeypatch, capsys):
    _listino(monkeypatch, 0.0)
    usage = {"model": "zzlab/finto-2", "api_calls": 1, "duration_s": 0.4,
             "cost_usd": None, "status": "api_error"}
    entry, riga = _riga(capsys, "_probe:zzlab/finto-2", usage)
    assert entry["cost_eur"] is None
    assert "in=n.d. (api_error)" in riga and "out=n.d. (api_error)" in riga, riga
    assert "costo=n.d. (api_error)" in riga, riga
    assert "None" not in riga and "0,00" not in riga, riga


def test_costo_non_prezzabile_dichiarato_coi_token_veri(monkeypatch, capsys):
    _listino(monkeypatch, None, status="model_unknown")
    usage = dict(_usage_sonda_ok(), cost_usd=0.000409)
    entry, riga = _riga(capsys, "_probe:zzlab/finto-3", usage)
    assert "in=37" in riga and "out=412" in riga, riga
    assert "costo=n.d. (model_unknown)" in riga, riga
    assert "0,00" not in riga, riga


def test_schema_in_out_della_reflection_resta_leggibile(monkeypatch, capsys):
    _listino(monkeypatch, 0.1234)
    usage = {"in": 1234, "out": 567, "model": "zzlab/finto-4", "api_calls": 1,
             "duration_s": 9.9, "cost_usd": 0.137, "status": "ok"}
    _, riga = _riga(capsys, "_reflection", usage)
    assert "in=1234" in riga and "out=567" in riga, riga
    assert "costo=0,12 EUR" in riga, riga
    assert "status=ok" in riga, riga


def test_costo_vero_della_sonda_sotto_0_0001_non_diventa_zero(monkeypatch, capsys):
    # R-36 F1: il ping costa ~4,5e-6 USD; format_eur lo stamperebbe «0,0000 EUR»
    _listino(monkeypatch, 3.9e-06)
    _, riga = _riga(capsys, "_probe:zzlab/finto-5", dict(_usage_sonda_ok(), cost_usd=4.5e-06))
    assert "0,0000" not in riga and "0,00 EUR" not in riga, riga
    assert "costo=< 0,0001 EUR (3,9e-06)" in riga, riga


def test_entry_assente_niente_status_none():
    riga = cm._riga_usage_laterale("_probe:zzlab/finto-6", None)
    assert "None" not in riga, riga
    assert "status=entry assente" in riga and "costo=n.d. (entry assente)" in riga, riga


def test_status_non_stringa_non_fa_cadere_la_riga():
    riga = cm._riga_usage_laterale("_probe:zzlab/finto-7",
                                   {"status": 7, "in": 11, "out": 22, "cost_eur": None})
    assert "in=11" in riga and "status=7" in riga and "costo=n.d. (7)" in riga, riga


def test_token_mancanti_con_status_ok_dicono_il_motivo_vero(monkeypatch, capsys):
    _listino(monkeypatch, 0.0123)
    usage = {"model": "zzlab/finto-8", "api_calls": 1, "cost_usd": 0.0143, "status": "ok"}
    _, riga = _riga(capsys, "_probe:zzlab/finto-8", usage)
    assert "(ok)" not in riga, riga
    assert "in=n.d. (token non esposti, mancanti: in,out" in riga, riga
    assert "costo=0,01 EUR" in riga, riga
