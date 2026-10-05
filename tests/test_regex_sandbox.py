"""REV_G2b A1 (seguito 04/10/2026): i pattern dei profili filing si provano in un processo separato.

Nei test di filing_verifica 28 caratteri: in processo la regex costa decine di secondi (senza la cura il test
cade per assenza dell'errore, senza bloccarsi per ore); nel processo separato scade a 2 s.

Una regex catastrofica tiene il GIL: eseguita nel backend lo ferma tutto (misura della revisione:
87 s). Qui il thread principale deve continuare a girare e l'esito e' «pattern troppo lento».
"""
import re
import threading
import time

import pytest

from bellomberg.market_data import filing_verifica as fv
from bellomberg.market_data import regex_sandbox as rs

LENTE = [(r"(a|a)+$", "a" * 50 + "!"), (r"(a|aa)+$", "a" * 50 + "!"), (r"(?:a|ab|b)+c", "ab" * 25)]


def _gap_massimo_durante(funzione):
    """Esegue `funzione` in un thread e misura il buco piu' lungo del thread principale."""
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


@pytest.mark.parametrize("pattern,testo", LENTE)
def test_pattern_catastrofico_troppo_lento_e_thread_principale_vivo(pattern, testo):
    gap, esito = _gap_massimo_durante(lambda: rs.verifica(testo, cerca=[pattern], tempo_max_s=2))
    assert isinstance(esito.get("errore"), rs.PatternTroppoLento), esito
    assert "troppo lento" in str(esito["errore"])
    assert gap < 1.0, f"thread principale fermo {gap:.2f} s"


def test_classi_strette_in_sequenza_non_fermano_il_backend():
    testo = "a" * 50
    gap, esito = _gap_massimo_durante(lambda: rs.verifica(testo, cerca=[r"\w+\w+\w+x"], tempo_max_s=2))
    assert "errore" not in esito or isinstance(esito["errore"], rs.PatternTroppoLento)
    assert gap < 1.0


def test_pattern_normale_ok_e_in_cache():
    testo = "Nova AG Half-Year Financial Report\nRisks and opportunities\n"
    rs.verifica(testo, cerca=[r"Nova\s+AG"], righe=testo.splitlines(), intere=["Risks and opportunities"])
    assert (rs._impronta(testo), "search", r"Nova\s+AG") in rs._esiti


def test_dopo_un_pattern_lento_il_processo_riparte():
    with pytest.raises(rs.PatternTroppoLento):
        rs.verifica("a" * 40 + "!", cerca=[r"(a|a)+$"], tempo_max_s=1)
    rs.verifica("riparte\n", cerca=["riparte"])  # nuovo figlio, nessun errore


def test_cerca_prova_dichiara_verifica_non_completata():
    with pytest.raises(ValueError, match="troppo lento"):
        fv._cerca_prova("a" * 28 + "!", r"(a|a)+$", "emittente", "https://x.example/a.pdf", "0" * 64)


def test_periodo_con_prova_dichiara_verifica_non_completata():
    with pytest.raises(ValueError, match="troppo lento"):
        fv._periodo_con_prova("a" * 28 + "!", r"(a|a)+$", "semestrale", None, "u", "0" * 64)


def test_sezione_con_pattern_lento_non_disponibile_con_motivo():
    testo = "Titolo\n" + "a" * 28 + "!\nCorpo della sezione\nFine\n"
    risultato, motivi = fv._selettori(testo, {"lenta": {"inizio": r"(a|a)+$", "fine": "Fine"},
                                               "buona": {"inizio": "Titolo", "fine": "Fine"}})
    assert risultato["lenta"] == {} and "troppo lento" in motivi["lenta"]
    assert risultato["buona"]["inizio"] == "Titolo"


@pytest.fixture(autouse=True)
def _tempo_breve(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 2.0)


# ---- revisione di sicurezza «resource-cap-bypass» (04/10/2026): tetti del processo di prova ----

def _nuovo_figlio():
    with rs._lock:
        rs._chiudi_locked()


def test_a_testo_oltre_il_tetto_non_si_serializza(monkeypatch):
    monkeypatch.setattr(rs, "MAX_CARATTERI", 1_000)
    with pytest.raises(rs.VerificaNonCompletata, match="oltre 1000 caratteri"):
        rs.verifica("x" * 1_001, cerca=["x"])
    with pytest.raises(rs.VerificaNonCompletata, match="oltre 1000 caratteri"):
        rs.verifica(righe=["x" * 600, "y" * 600], intere=["x+"])


def test_b_troppi_pattern_e_pattern_troppo_lunghi(monkeypatch):
    with pytest.raises(rs.VerificaNonCompletata, match="pattern in una prova"):
        rs.verifica("testo", cerca=[f"p{i}" for i in range(rs.MAX_PATTERN + 1)])
    with pytest.raises(rs.VerificaNonCompletata, match="caratteri"):
        rs.verifica("testo", cerca=["a" * (rs.MAX_LUNGHEZZA_PATTERN + 1)])


def test_b_tempo_totale_non_si_somma_per_pattern():
    """Dentro un budget di 2,5 s le prove dopo la prima hanno solo il residuo (o nessun tempo):
    si controlla il limite DICHIARATO da ogni prova, non il tempo a muro (macchina carica)."""
    messaggi = []
    with rs.budget(2.5):
        for i in range(4):  # 4 pattern lenti: senza tetto totale 4 x 2 s
            with pytest.raises(rs.PatternTroppoLento) as err:
                rs.verifica("a" * 40 + "!" * (i + 1), cerca=[r"(a|a)+$"], tempo_max_s=2)
            messaggi.append(str(err.value))
    assert "oltre 2 s" in messaggi[0]
    for m in messaggi[1:]:
        limite = re.search(r"oltre ([0-9.]+) s", m)
        assert "esaurito" in m or (limite and float(limite.group(1)) < 1.0), m


def test_b_verifica_documento_ha_un_tempo_totale(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_DOCUMENTO_S", 2.5)
    testo = "\n".join(["Titolo"] + ["a" * 40 + "!" * i for i in range(1, 6)] + ["Fine"])
    sezioni = {f"s{i}": {"inizio": r"(a|a)+$" + "(?:)" * i, "fine": "Fine"}
               for i in range(5)}
    inizio = time.monotonic()
    with rs.budget(rs.TEMPO_DOCUMENTO_S):
        _, motivi = fv._selettori(testo, sezioni)
    assert time.monotonic() - inizio < 3.5
    assert all("non completata" in m for m in motivi.values()) and len(motivi) == 5


def test_c_pattern_eseguito_nel_backend_solo_dopo_la_prova(monkeypatch):
    """Nessun percorso esegue il pattern nel processo se la prova non si e' fatta."""
    eseguiti = []
    vero = fv.re.search
    monkeypatch.setattr(rs, "verifica", lambda *a, **k: (_ for _ in ()).throw(rs.VerificaNonCompletata("x")))
    monkeypatch.setattr(fv.re, "search", lambda *a, **k: eseguiti.append(a) or vero(*a, **k))
    with pytest.raises(ValueError):
        fv._cerca_prova("testo", r"(a|a)+$", "emittente", "u", "0" * 64)
    assert eseguiti == []


def test_d_tetto_di_memoria_applicato_al_figlio():
    import sys
    _nuovo_figlio()
    rs.verifica("memoria\n", cerca=["memoria"])
    atteso = "job_object" if sys.platform == "win32" else "rlimit"
    assert rs.stato()["tetto_memoria"] == atteso


def test_d_figlio_oltre_la_memoria_muore_e_si_dichiara(monkeypatch):
    monkeypatch.setattr(rs, "MEMORIA_MAX_BYTE", 64 * 1024 * 1024)
    _nuovo_figlio()
    try:
        with pytest.raises(rs.VerificaNonCompletata, match="terminato|memoria"):
            # (?:a|b){1,90000} su 4 milioni di caratteri: il testo decodificato + lo stack di sre
            rs.verifica("ab" * 2_000_000, cerca=[r"(?:x|ab)*y"], tempo_max_s=30)
    finally:
        monkeypatch.undo()
        _nuovo_figlio()


def test_e_richiesta_in_coda_dietro_a_un_figlio_fermo_non_aspetta_oltre_il_suo_tempo():
    lento = threading.Thread(target=lambda: pytest.raises(rs.PatternTroppoLento, rs.verifica,
                                                          "a" * 40 + "!", cerca=[r"(a|a)+$"], tempo_max_s=3),
                             daemon=True)
    lento.start()
    time.sleep(0.5)
    inizio = time.monotonic()
    with pytest.raises(rs.PatternTroppoLento, match="occupata"):
        rs.verifica("altro\n", cerca=["altro"], tempo_max_s=0.5)
    assert time.monotonic() - inizio < 1.5
    lento.join(10)


def test_e_troppe_corrispondenze_dichiarate(monkeypatch):
    monkeypatch.setattr(rs, "MAX_CORRISPONDENZE", 100)
    with pytest.raises(rs.VerificaNonCompletata, match="corrispondenze"):
        rs.verifica("a " * 500, itera=["a"])
    rs.verifica("b " * 500, cerca=["b"])  # search: una sola corrispondenza, nessun tetto


def test_f_processo_che_non_parte_nessun_ripiego_locale(monkeypatch):
    def rotto(*a, **k):
        raise OSError("eseguibile assente")
    _nuovo_figlio()
    monkeypatch.setattr(rs.subprocess, "Popen", rotto)
    with pytest.raises(rs.VerificaNonCompletata, match="non avviato"):
        rs.verifica("nuovo testo mai provato\n", cerca=["nuovo"])
    with pytest.raises(ValueError, match="non avviato"):
        fv._cerca_prova("altro testo mai provato", "altro", "lingua", "u", "0" * 64)


# ---- seconda revisione C1 (04/10/2026): il backend non riesegue MAI i pattern del profilo ----
# Pattern che FINISCONO (2-4 s, sotto il tempo massimo) ma tengono il GIL: prima la prova nel figlio
# passava e poi il backend li rieseguiva (buco misurato 2,85 s); ora l'esito arriva dal figlio.

LENTA_RIGA = "a" * 18_000  # «.*Outlook» su una riga lunga: quadratico, ~3 s in un processo


def _senza_buchi(funzione, soglia=0.5):
    gap, esito = _gap_massimo_durante(funzione)
    assert "errore" not in esito, esito
    assert gap < soglia, f"thread principale fermo {gap:.2f} s"
    return esito


def test_c1_cerca_prova_lenta_ma_finita_non_tiene_il_gil(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 30.0)
    testo = LENTA_RIGA + "\nOutlook\n"
    esito = _senza_buchi(lambda: fv._cerca_prova(testo, r".*Outlook", "tipo", "u", "0" * 64))
    assert esito["valore"]["testo"].endswith("Outlook")
    # dalla cache: nessuna riesecuzione nel backend
    _senza_buchi(lambda: fv._cerca_prova(testo, r".*Outlook", "tipo", "u", "0" * 64))


def test_c1_sezioni_con_salta_indice_non_tengono_il_gil(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 30.0)
    testo = "\n".join(["Rischi", "3", "Fine", "Rischi", "corpo della sezione", LENTA_RIGA, "Fine"])
    esito = _senza_buchi(lambda: fv._selettori(testo, {"r": {"inizio": "Rischi", "fine": r".*Fine"}},
                                              salta_indice=True))
    risultato, motivi = esito["valore"]
    assert risultato["r"]["inizio"] == "Rischi" and risultato["r"]["occorrenza_inizio"] == 2, motivi


def test_c1_periodo_non_tiene_il_gil(monkeypatch):
    monkeypatch.setattr(rs, "TEMPO_MAX_S", 30.0)
    periodo = "six months ended 31 March 2026\n" + LENTA_RIGA + "\n"
    esito = _senza_buchi(lambda: fv._periodo_con_prova(
        periodo, r"(?:a.*Z)?(?P<mesi>six) months ended (?P<fine>\d{1,2} \w+ \d{4})", "semestrale", None, "u", "0" * 64))
    assert esito["valore"][1].isoformat() == "2026-03-31"


def test_c1_regex_non_valida_resta_re_error():
    with pytest.raises(re.error):
        rs.cerca("testo", "(")


def test_c4_budget_totale_anche_in_esef_e_pdf_evidence(monkeypatch, tmp_path):
    import contextlib
    from bellomberg.market_data import filing_esef
    from bellomberg.valuation import filing_pdf_evidence
    aperti = []

    @contextlib.contextmanager
    def spia(totale):
        aperti.append(totale)
        yield
    monkeypatch.setattr(rs, "budget", spia)
    p = tmp_path / "x.json"
    p.write_bytes(b"{}")
    filing_esef.documento_esef(p, url="u", profilo={"lei": "X", "emittente_id": "LEI:X"})
    with pytest.raises(ValueError):
        filing_pdf_evidence.verify_filing_pdf({"filing_verification": {"rules": {}}})
    assert aperti == [rs.TEMPO_DOCUMENTO_S]  # esef; pdf_evidence fallisce prima delle prove
    doc = _documento_pdf_minimo()
    with pytest.raises(ValueError):
        filing_pdf_evidence.verify_filing_pdf(doc)
    assert len(aperti) == 2


def _documento_pdf_minimo():
    from hashlib import sha256 as h
    from bellomberg.valuation import filing_pdf_evidence as fpe
    body = "testo"
    digest = h(b"pdf").hexdigest()
    meta = {"emittente_id": "CIK:0000000001", "lingua": "en", "tipo": "annuale", "perimetro": "consolidato",
            "periodo_inizio": "2025-01-01", "periodo_fine": "2025-12-31"}
    pagine = [{"pagina": 1, "inizio": 0, "fine": len(body), "sha256": h(body.encode()).hexdigest()}]
    return {"id": digest, "url": "u", "document_sha256": digest, "text": body, "page_references": pagine,
            "metadata": {**meta, "filing_verification": fpe.SCHEMA, "report_date": "2025-12-31",
                         "report_start": "2025-01-01"},
            "filing_verification": {"schema": fpe.SCHEMA, "document_sha256": digest, "url": "u",
                                    "text_sha256": h(body.encode()).hexdigest(),
                                    "identity_basis": "curated_filing_profile", "security_identity_verified": False,
                                    "metadata": meta, "rules": {"emittente": "zz", "lingua": "zz", "tipo": "zz",
                                                                "perimetro": "zz", "periodo": "zz"}}}
