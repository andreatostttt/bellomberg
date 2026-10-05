"""Validazione del profilo ESEF a blocchi (fase B, task 4)."""
import pytest

from bellomberg.market_data.filing_profili_auto import profilo_esef
from bellomberg.storage.filing_store import _validate_profile
from tests.filing_esef_sintetici import LEI_NOVA


def _p(**kw):
    return {**profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="nome", lingua="en"), **kw}


def test_profilo_esef_automatico_valido():
    _validate_profile("NOVA.MI", _p(), 24)


@pytest.mark.parametrize("cattiva", [
    {"esef_modo": "report"},
    {"emittente_id": "CIK:0009990001"},
    {"fonti": ["esef", "ir"]},
    {"fonti": None},
    {"esef_modo": "blocchi", "varianti": [{"tipo": "trimestrale"}]},
])
def test_profilo_esef_non_valido(cattiva):
    p = _p(**cattiva)
    if p.get("fonti") is None:
        p.pop("fonti")
    with pytest.raises(ValueError):
        _validate_profile("NOVA.MI", p, 24)


def test_sezioni_vuote_senza_modo_restano_errore():
    p = _p()
    p.pop("esef_modo")
    with pytest.raises(ValueError):
        _validate_profile("NOVA.MI", p, 24)
