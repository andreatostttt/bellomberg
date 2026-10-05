"""Regole opt-in per i profili automatici: indice ripetuto, testo intero, periodo piu' recente."""
from bellomberg.market_data.filing_verifica import verifica_documento
from tests.filing_sec_sintetici import CIK_KORE, CIK_NOVA, allegato_6k, documento_10q

SEZ_10Q = {"gestione": {"inizio": r"item\s*2\.?\s*management.s discussion and analysis\b.{0,120}",
                        "fine": r"item\s*3\b.{0,160}"},
           "rischi": {"inizio": r"item\s*1a\.?\s*risk factors\.?", "fine": r"item\s*2\b.{0,160}"}}
CAT_10Q = {"form": "10-Q", "report_date": "2026-09-30", "emittente_id": "CIK:" + CIK_NOVA}
PERIODO_6K = (r"(?P<mesi>three|six|3|6)(?:\s+and\s+(?:six|nine))?\s+months\s+ended\s+"
              r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})")


def _profilo_10q(**extra):
    p = {"ticker": "NOVA.DE", "emittente_id": "CIK:" + CIK_NOVA, "lingua": "en", "tipo": "trimestrale",
         "perimetro": "consolidato", "sezioni": SEZ_10Q,
         "verifica": {"lingua": r"\bthe\b", "tipo": r"quarterly report pursuant to section 13",
                      "perimetro": r"consolidated"}}
    p.update(extra)
    return p


def _profilo_6k(tipo="semestrale", **extra):
    p = {"ticker": "KORE.DE", "emittente_id": "CIK:" + CIK_KORE, "lingua": "en", "tipo": tipo,
         "perimetro": "consolidato", "sezioni": {}, "sezioni_intero": True,
         "verifica": {"lingua": r"\bthe\b", "tipo": r"months\s+ended", "perimetro": r"consolidated",
                      "emittente": r"Kore\s+Mining", "periodo": PERIODO_6K.replace("three|six|3|6",
                                                                                 "six|6" if tipo == "semestrale" else "three|3")}}
    p.update(extra)
    return p


def _scrivi(tmp_path, nome, contenuto):
    path = tmp_path / nome
    path.write_bytes(contenuto)
    return path


def _testo_sezione(doc, nome):
    s = doc["sezioni"][nome]
    return doc["estrazione"]["testo"][s["inizio"]:s["fine"]]


def test_indice_ripetuto_senza_flag_resta_ambiguo(tmp_path):
    path = _scrivi(tmp_path, "q.htm", documento_10q(2026, 3))
    r = verifica_documento(path, url="https://www.sec.gov/x", profilo=_profilo_10q(), catalogo=CAT_10Q)
    assert r["stato"] == "ok", r["motivi"]
    assert r["documento"]["sezioni"]["rischi"]["stato"] == "non_disponibile"


def test_indice_ripetuto_con_flag_sceglie_il_corpo(tmp_path):
    path = _scrivi(tmp_path, "q.htm", documento_10q(2026, 3, rischio="Export rules may tighten. " * 80,
                                                    gestione="Revenue grew on data center demand. " * 60))
    r = verifica_documento(path, url="https://www.sec.gov/x", profilo=_profilo_10q(sezioni_salta_indice=True),
                           catalogo=CAT_10Q)
    assert r["stato"] == "ok", r["motivi"]
    assert "Export rules" in _testo_sezione(r["documento"], "rischi")
    assert r["documento"]["sezioni"]["gestione"]["stato"] == "ok"


def test_indice_spezzato_funziona_anche_senza_flag(tmp_path):
    path = _scrivi(tmp_path, "q.htm", documento_10q(2026, 3, indice="spezzato", rischio="Export rules. " * 80))
    r = verifica_documento(path, url="https://www.sec.gov/x", profilo=_profilo_10q(), catalogo=CAT_10Q)
    assert "Export rules" in _testo_sezione(r["documento"], "rischi")


def test_allegato_6k_testo_intero_e_periodo_piu_recente(tmp_path):
    path = _scrivi(tmp_path, "ex99.htm", allegato_6k(2026, mesi="six"))
    r = verifica_documento(path, url="https://www.sec.gov/ex99.htm",
                           profilo=_profilo_6k(periodo_regola="piu_recente"), catalogo={"form": "6-K"})
    assert r["stato"] == "ok", r["motivi"]
    assert r["documento"]["metadati"]["periodo_inizio"] == "2026-01-01"
    assert r["documento"]["metadati"]["periodo_fine"] == "2026-06-30"
    assert list(r["documento"]["sezioni"]) == ["testo"]
    assert "Outlook is unchanged." in _testo_sezione(r["documento"], "testo")


def test_trimestre_a_52_settimane_con_regola(tmp_path):
    # «three and six months ended June 27, 2026»: fine non a fine mese (anno fiscale a 52 settimane)
    path = _scrivi(tmp_path, "q2.htm", allegato_6k(2026, mesi="three"))
    r = verifica_documento(path, url="https://www.sec.gov/q2.htm",
                           profilo=_profilo_6k("trimestrale", periodo_regola="piu_recente"), catalogo={"form": "6-K"})
    assert r["stato"] == "ok", r["motivi"]
    meta = r["documento"]["metadati"]
    assert (meta["periodo_inizio"], meta["periodo_fine"]) == ("2026-03-28", "2026-06-27")
    assert r["documento"]["prove_verifica"]["periodo"]["calcolo"]["regola"] == "mesi_con_fine_libera"


def test_comparativi_senza_regola_restano_ambigui(tmp_path):
    path = _scrivi(tmp_path, "ex99.htm", allegato_6k(2026, mesi="six"))
    r = verifica_documento(path, url="https://www.sec.gov/ex99.htm", profilo=_profilo_6k(), catalogo={"form": "6-K"})
    assert r["stato"] == "non_verificato" and "ambiguo" in r["motivi"][0]


def test_fine_mese_ancora_obbligatoria_senza_regola(tmp_path):
    path = _scrivi(tmp_path, "q2.htm", allegato_6k(2026, mesi="three", fine="June 27, 2026"))
    r = verifica_documento(path, url="https://www.sec.gov/q2.htm", profilo=_profilo_6k("trimestrale"),
                           catalogo={"form": "6-K", "report_date": "2026-06-27"})
    assert r["stato"] == "non_verificato" and "fine mese" in r["motivi"][0]


def test_contesti_xbrl_piu_recente_senza_data_catalogo(tmp_path):
    # semestrale iXBRL (stile Rio): contesto corrente e comparativo, nessuna data nel catalogo
    contesti = "".join(
        f'<xbrli:context id="c{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK_KORE}'
        f'</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{a}-01-01</xbrli:startDate>'
        f'<xbrli:endDate>{a}-06-30</xbrli:endDate></xbrli:period></xbrli:context>' for i, a in ((1, 2026), (2, 2025)))
    html = (f'<html lang="en"><body><div style="display:none"><ix:header><ix:resources>{contesti}</ix:resources>'
            f'</ix:header></div><p>Kore Mining plc</p><p>Interim report for the six months ended 30 June 2026</p>'
            f'<p>Condensed consolidated statements.</p><p>Outlook</p><p>Stable.</p></body></html>').encode()
    path = _scrivi(tmp_path, "rio.htm", html)
    senza = verifica_documento(path, url="https://www.sec.gov/rio.htm", profilo=_profilo_6k(), catalogo={"form": "6-K"})
    con = verifica_documento(path, url="https://www.sec.gov/rio.htm", profilo=_profilo_6k(periodo_regola="piu_recente"),
                             catalogo={"form": "6-K"})
    assert con["stato"] == "ok", con["motivi"]
    assert con["documento"]["metadati"]["periodo_fine"] == "2026-06-30"
    assert con["documento"]["prove_verifica"]["periodo"]["supporto"] == "markup"
    assert senza["stato"] == "ok" and senza["documento"]["prove_verifica"]["periodo"]["supporto"] == "testo"


def test_indice_ripetuto_con_sezione_breve_nel_corpo(tmp_path):
    # Osservato su un 10-Q reale: «Item 1. Legal Proceedings» identico in indice e corpo,
    # nel corpo una sola riga di rimando alla nota. Corta, ma e' il corpo.
    path = _scrivi(tmp_path, "q.htm", documento_10q(2026, 3))
    profilo = _profilo_10q(sezioni_salta_indice=True, sezioni={
        "contenziosi": {"inizio": r"item\s*1\.?\s*legal proceedings\.?", "fine": r"item\s*(?:1a|2)\b.{0,160}"}})
    r = verifica_documento(path, url="https://www.sec.gov/x", profilo=profilo, catalogo=CAT_10Q)
    assert r["documento"]["sezioni"]["contenziosi"]["stato"] == "ok", r["documento"]["sezioni"]
    assert "No material proceedings." in _testo_sezione(r["documento"], "contenziosi")


def test_occorrenza_successiva_senza_intestazione_finale_ignorata(tmp_path):
    # Osservato su un 20-F reale: «Risk factors» anche molto dopo, dove non segue piu' «Item 4».
    from tests.filing_sec_sintetici import documento_20f
    tardi = "Results reflect lower prices.</p><p>Risk factors</p><p>Macro risks summarised again."
    path = _scrivi(tmp_path, "f.htm", documento_20f(2025, indice="uguale", gestione=tardi))
    profilo = {"ticker": "KORE.DE", "emittente_id": "CIK:" + CIK_KORE, "lingua": "en", "tipo": "annuale",
               "perimetro": "consolidato", "sezioni_salta_indice": True,
               "sezioni": {"rischi": {"inizio": r"(?:item\s*3\.?\s*)?(?:d\.?\s*)?risk\s+factors\.?",
                                      "fine": r"item\s*4\b.{0,160}"}},
               "verifica": {"lingua": r"\bthe\b", "tipo": r"annual report", "perimetro": r"consolidated"}}
    r = verifica_documento(path, url="https://www.sec.gov/f.htm", profilo=profilo,
                           catalogo={"form": "20-F", "report_date": "2025-12-31", "emittente_id": "CIK:" + CIK_KORE})
    assert r["documento"]["sezioni"]["rischi"]["stato"] == "ok", r["documento"]["sezioni"]
    assert "Commodity prices may fall." in _testo_sezione(r["documento"], "rischi")
