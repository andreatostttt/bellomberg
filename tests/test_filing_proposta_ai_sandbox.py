"""Seconda revisione G2b, C2 (04/10/2026): la proposta AI non esegue pattern nel backend.

- i pattern costruiti dalle frasi sono LINEARI: nessun quantificatore illimitato adiacente
  sovrapponibile (prima «2024 Annual Report» -> \\d+\\s+Annual...: 20 mila cifre = 5 s di GIL);
- _prova, la scelta delle sezioni e la diagnosi usano gli esiti del processo separato;
- una cache con «formato letterale» manomessa si ripassa dal filtro delle frasi.
"""
import re
import threading
import time

import pytest

from bellomberg.market_data import filing_proposta_ai as fp
from bellomberg.market_data import regex_sandbox as rs
from tests.filing_pdf_sintetici import semestrale_nova
from tests.test_filing_proposta_ai_verifica import URL, _proposta, _verifica

CIFRE = "1" * 20_000


def _gap(funzione):
    esito, fatto = {}, threading.Event()

    def corri():
        try:
            esito["valore"] = funzione()
        except Exception as exc:
            esito["errore"] = exc
        finally:
            fatto.set()
    threading.Thread(target=corri, daemon=True).start()
    ultimo, gap = time.monotonic(), 0.0
    while not fatto.wait(0.01):
        ora = time.monotonic()
        gap, ultimo = max(gap, ora - ultimo), ora
    return gap, esito


@pytest.mark.parametrize("frase", ["2024 Annual Report", "Report 2024 2025", "Q3 2026 results", "Nova AG"])
def test_pattern_dalle_frasi_lineari_su_20k_cifre(frase):
    pattern, motivo = fp._da_frase(frase)
    assert motivo is None
    assert r"\d+" not in pattern and r"\s+" not in pattern, pattern  # solo quantificatori limitati
    inizio = time.monotonic()
    re.search(pattern, CIFRE + " " * 20_000 + CIFRE, re.I | re.M)
    assert time.monotonic() - inizio < 0.5


def test_frase_con_anno_generalizzata_e_ancora_valida():
    pattern, _ = fp._da_frase("Outlook for the 2026 fiscal year")
    assert re.fullmatch(pattern, "Outlook for the 2027 fiscal year", re.I)
    assert re.fullmatch(pattern, "Outlook  for the 2027 fiscal year", re.I)


def test_prova_sul_documento_intero_passa_dal_processo_separato(monkeypatch):
    chiamate = []
    vero = rs.cerca
    monkeypatch.setattr(rs, "cerca", lambda testo, p, **k: chiamate.append(p) or vero(testo, p, **k))
    monkeypatch.setattr(fp.re, "search", lambda *a, **k: pytest.fail("re.search nel backend"))
    assert fp._prova(r"Annual\s{1,3}Report", "x Annual Report") is True
    assert chiamate == [r"Annual\s{1,3}Report"]


def test_prova_lenta_non_tiene_il_gil(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 30.0)
    gap, esito = _gap(lambda: fp._prova(r".*Outlook", "a" * 18_000 + "\nOutlook"))
    assert esito.get("valore") is True and gap < 0.5, (gap, esito)


def test_sezioni_e_diagnosi_senza_re_sui_pattern_nel_backend(tmp_path, monkeypatch):
    usati = []
    vero = re.fullmatch

    def spia(pattern, *a, **k):
        usati.append(pattern)
        return vero(pattern, *a, **k)
    monkeypatch.setattr(fp.re, "fullmatch", spia)
    proposta = _proposta()
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True
    profilo_patterns = {v for s in proposta["sezioni"].values() for v in s.values()}
    assert not profilo_patterns & set(usati), profilo_patterns & set(usati)


def test_sezione_con_pattern_lento_scartata_col_motivo_senza_bloccare(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 1.0)
    proposta = _proposta(lenta={"inizio": r"(?:.|..)+!", "fine": "Risks and opportunities"},
                         rischi={"inizio": "Risks and opportunities", "fine": "Notes to the Financial Statements"})
    monkeypatch.setattr(fp, "_regex_sicura", lambda p: None)  # anche senza il filtro euristico
    gap, esito = _gap(lambda: _verifica(tmp_path, semestrale_nova() , proposta))
    assert gap < 0.5
    _, out = esito["valore"]
    motivi = {s["nome"]: s["motivo"] for s in out["scartate"]}
    assert "non completata" in motivi.get("lenta", ""), motivi


def test_cache_manomessa_con_formato_letterale_ripassata_dal_filtro():
    manomessa = {"formato": "letterale", "tipo": "semestrale", "lingua": "en", "scartate": [],
                 "emittente": r"(a|a)+$", "sezioni": {"lenta": {"inizio": r"(a|a)+$", "fine": "Fine"}}}
    out = fp._proposta_letterale(manomessa)
    assert out["sezioni"] == {} and out["emittente"] is None
    buona = fp._parse_testo('{"tipo": "semestrale", "emittente": "Nova AG", '
                            '"sezioni": {"rischi": {"inizio": "Risks", "fine": "Notes"}}}')
    assert fp._proposta_letterale(buona)["sezioni"] == buona["sezioni"]
    buona["sezioni"]["rischi"]["inizio"] = r"(a|a)+$"  # manomessa dopo il salvataggio
    assert fp._proposta_letterale(buona)["sezioni"]["rischi"]["inizio"] == fp._da_frase("Risks")[0]


# ---- REV3_G2b R1: cifre non ASCII e regola che non finisce in tempo ----

def test_r1_cifre_unicode_non_rendono_esponenziale_il_pattern():
    frase = "1\u0663" * 20 + "Z"  # cifra ASCII + cifra araba ripetute
    pattern, motivo = fp._da_frase(frase)
    assert motivo is None and r"\d" not in pattern, pattern
    inizio = time.monotonic()
    re.search(pattern, "1\u0663" * 20 + "Y", re.I | re.M)
    assert time.monotonic() - inizio < 0.5


def test_r1_regola_lenta_nel_figlio_cede_al_ripiego_dichiarato(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 1.0)
    vero = rs.cerca

    def cerca(testo, p, **k):
        if p == "LENTA":
            raise rs.PatternTroppoLento("verifica non completata: pattern troppo lento (oltre 1 s)")
        return vero(testo, p, **k)
    monkeypatch.setattr(rs, "cerca", cerca)
    proposta = _proposta()
    proposta["prova_tipo"] = "LENTA"
    _, out = _verifica(tmp_path, semestrale_nova(), proposta)
    assert out["salvabile"] is True and out["regole"]["tipo"]["origine"] == "ripiego"
    assert any("tipo" in a and "troppo lento" in a for a in out["avvisi"]), out["avvisi"]
