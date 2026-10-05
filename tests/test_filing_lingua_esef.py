r"""REV_G2b B2 (04/10/2026): regola di lingua dei profili ESEF.

Prima: lingua fuori dalle sei note -> regola r"\w", che combacia con qualunque testo: verifica della
lingua sempre vera e mai dichiarata. Ora: regole per le lingue europee principali dei depositi ESEF;
per le altre «lingua non verificabile (xx)» dichiarata nel documento (la lingua dichiarata nei fatti
xBRL resta la prova principale).
"""
import re

import pytest

from bellomberg.market_data import filing_esef
from bellomberg.market_data import filing_profili_auto as fpa
from tests.filing_esef_sintetici import LEI_NOVA, json_nova

URL = "https://filings.xbrl.org/x/nova.json"
CAMPIONI = {"sv": "Koncernen och moderbolaget redovisar för året", "da": "Koncernen og selskabet af året med",
            "no": "Konsernet og selskapet av året med", "fi": "Konserni ja emoyhtiö on tilikaudella",
            "pl": "Grupa oraz spółka jest dla akcjonariuszy", "pt": "O grupo não tem uma posição para",
            "cs": "Skupina je na trhu a že", "hu": "A csoport és az anyavállalat hogy",
            "el": "Ο όμιλος και της εταιρείας των", "ro": "Grupul și societatea din pentru"}


@pytest.mark.parametrize("lingua", sorted(CAMPIONI))
def test_lingue_europee_con_regola_vera(lingua):
    regola = fpa.profilo_esef("NOVA.ST", lei=LEI_NOVA, nome="Nova AB", origine="nome",
                              lingua=lingua)["verifica"]["lingua"]
    assert regola != r"\w" and regola != fpa.LINGUA_NON_VERIFICABILE
    assert re.search(regola, CAMPIONI[lingua], re.I)
    assert not re.search(regola, "1234 5678 — 90", re.I)


def test_lingua_sconosciuta_regola_che_non_combacia_mai():
    regola = fpa.profilo_esef("NOVA.XX", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="ja")["verifica"]["lingua"]
    assert regola == fpa.LINGUA_NON_VERIFICABILE
    assert not re.search(regola, "qualunque testo, anche the and of", re.I)


def test_fatti_senza_lingua_e_lingua_non_verificabile_dichiarata(tmp_path):
    p = tmp_path / "nova-2025.json"
    p.write_bytes(json_nova(2025, lingua=""))
    profilo = fpa.profilo_esef("NOVA.XX", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="ja")
    r = filing_esef.documento_esef(p, url=URL, profilo=profilo, catalogo={"period_end": "2025-12-31"})
    assert r["stato"] == "ok", r
    prova = r["documento"]["prove_verifica"]["lingua"]
    assert prova["stato"] == "non_verificabile" and "ja" in prova["motivo"]
    assert any("lingua non verificabile (ja)" in a for a in r["documento"]["avvisi"])
