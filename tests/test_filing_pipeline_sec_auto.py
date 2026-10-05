"""Pipeline con profili SEC automatici: CIK dal profilo, forme filtrate, 6-K, varianti."""
import pytest

from bellomberg.market_data import filing_pipeline, sec_edgar
from tests.filing_sec_sintetici import CIK_KORE, CIK_NOVA, allegato_6k, documento_10q
from tests.test_filing_pipeline import rete

SEZ_10Q = {"gestione": {"inizio": r"item\s*2\.?\s*management.s discussion and analysis\b.{0,120}",
                        "fine": r"item\s*3\b.{0,160}"},
           "rischi": {"inizio": r"item\s*1a\.?\s*risk factors\.?", "fine": r"item\s*2\b.{0,160}"}}
BASE_NOVA = "https://www.sec.gov/Archives/edgar/data/9990001/"
BASE_KORE = "https://www.sec.gov/Archives/edgar/data/9990011/"
PERIODO_6K = (r"(?P<mesi>six|6)(?:\s+and\s+(?:nine))?\s+months\s+ended\s+"
              r"(?P<fine>[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})")


def _riga(form, data, acc, doc, report="", base=BASE_NOVA, cik=CIK_NOVA, ticker="NOVA.DE"):
    return {"ticker": ticker, "form": form, "filed_date": data, "accession": acc,
            "url": base + acc.replace("-", "") + "/" + doc, "emittente_id": "CIK:" + cik,
            "issuer": "Synthetic", "report_date": report, "items": [], "fonte": "SEC EDGAR"}


@pytest.fixture
def profilo_10q():
    return {"ticker": "NOVA.DE", "emittente_id": "CIK:" + CIK_NOVA, "cik": CIK_NOVA, "lingua": "en",
            "tipo": "trimestrale", "perimetro": "consolidato", "fonti": ["sec"], "forme_sec": ["10-Q"],
            "sezioni_salta_indice": True, "sezioni": SEZ_10Q,
            "verifica": {"lingua": r"\bthe\b", "tipo": r"quarterly report pursuant to section 13",
                         "perimetro": r"consolidated"}}


@pytest.fixture
def profilo_6k():
    return {"ticker": "KORE.DE", "emittente_id": "CIK:" + CIK_KORE, "cik": CIK_KORE, "lingua": "en",
            "tipo": "semestrale", "perimetro": "consolidato", "fonti": ["sec"], "forme_sec": ["6-K"],
            "sezioni": {}, "sezioni_intero": True, "periodo_regola": "piu_recente", "stesso_periodo": "piu_lungo",
            "verifica": {"lingua": r"\bthe\b", "tipo": r"months\s+ended", "perimetro": r"consolidated",
                         "emittente": r"Kore\s+Mining", "periodo": PERIODO_6K}}


def test_cik_dal_profilo_e_forme_filtrate(monkeypatch, tmp_path, profilo_10q):
    visti = {}

    def catalogo(ticker, days=1100, max_pages=20, cik=None):
        visti.update(ticker=ticker, cik=cik)
        return {"stato": "ok", "motivi": [], "documenti": [
            _riga("10-Q", "2026-10-30", "0009990001-26-000003", "q3.htm", "2026-09-30"),
            _riga("8-K", "2026-10-29", "0009990001-26-000002", "8k.htm", "2026-10-29"),
            _riga("10-K", "2026-02-20", "0009990001-26-000001", "k.htm", "2025-12-31"),
            _riga("10-Q", "2025-10-30", "0009990001-25-000003", "q3.htm", "2025-09-30")]}

    monkeypatch.setattr(sec_edgar, "get_filing_catalog", catalogo)
    rete(monkeypatch, {
        BASE_NOVA + "000999000126000003/q3.htm": documento_10q(2026, 3, rischio="Export rules may tighten. " * 80,
                                                               gestione="Data center demand. " * 90),
        BASE_NOVA + "000999000125000003/q3.htm": documento_10q(2025, 3, rischio="Demand may weaken. " * 80,
                                                               gestione="Data center demand. " * 90)})
    out = filing_pipeline.esegui_profilo(profilo_10q, archivio=tmp_path / "a", oggi="2026-10-31")
    assert visti == {"ticker": "NOVA.DE", "cik": CIK_NOVA}
    assert [c["form"] for c in out["candidati"]] == ["10-Q", "10-Q"]
    assert out["confronto_corrente"] is not None, out["motivi"]
    assert out["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2026-09-30"


def _sei_k(data, acc, doc="k.htm"):
    return _riga("6-K", data, acc, doc, base=BASE_KORE, cik=CIK_KORE, ticker="KORE.DE")


def test_6k_documenti_risultati_e_notizie_non_applicabili(monkeypatch, tmp_path, profilo_6k):
    righe = [_sei_k("2026-08-01", "0009990011-26-000009"), _sei_k("2026-07-15", "0009990011-26-000008"),
             _sei_k("2026-07-01", "0009990011-26-000007"), _sei_k("2025-08-01", "0009990011-25-000009")]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    cartella = lambda acc: BASE_KORE + acc.replace("-", "") + "/"
    allegati = {
        # semestrale iXBRL come allegato (stile Rio): scelto anche senza parole chiave nel nome
        "0009990011-26-000009": [{"seq": 1, "descrizione": "6-K", "tipo": "6-K", "ixbrl": False, "url": cartella("0009990011-26-000009") + "k.htm"},
                                 {"seq": 2, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "url": BASE_KORE + "kore-20260630.htm"}],
        # comunicato non di bilancio, nome generico: nessun candidato
        "0009990011-26-000008": [{"seq": 1, "descrizione": "6-K", "tipo": "6-K", "ixbrl": False, "url": BASE_KORE + "notice.htm"},
                                 {"seq": 2, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": False, "url": BASE_KORE + "ex1voting.htm"}],
        # rapporto di produzione con «results» nel nome: candidato, ma non e' una semestrale
        "0009990011-26-000007": [{"seq": 1, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": False, "url": BASE_KORE + "ex1q2results.htm"}],
        "0009990011-25-000009": [{"seq": 1, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "url": BASE_KORE + "kore-20250630.htm"}],
    }
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, acc, **k: allegati[acc])
    rete(monkeypatch, {
        BASE_KORE + "kore-20260630.htm": allegato_6k(2026, testo="Outlook lowered on weaker copper demand."),
        BASE_KORE + "kore-20250630.htm": allegato_6k(2025, testo="Outlook is unchanged."),
        BASE_KORE + "ex1q2results.htm": b'<html lang="en"><body><p>Kore Mining plc</p><p>Second quarter production results.</p>'
                                        b'<p>Copper output rose; the consolidated view is unchanged.</p></body></html>'})
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "a", oggi="2026-10-31")
    stati = {c["url"].rsplit("/", 1)[-1]: c["stato"] for c in out["candidati"]}
    assert stati == {"kore-20260630.htm": "verificato", "ex1q2results.htm": "non_applicabile",
                     "kore-20250630.htm": "verificato"}
    assert out["ultimo_non_verificato"] is False
    assert out["confronto_corrente"] is not None, out["motivi"]
    assert any("6-K senza documento di risultati: 1" in l for l in out["copertura"]["limiti"])


def test_6k_stesso_periodo_vince_il_piu_lungo(monkeypatch, tmp_path, profilo_6k):
    righe = [_sei_k("2026-08-05", "0009990011-26-000010", "kore-6k_h1x2026.htm"),
             _sei_k("2026-07-30", "0009990011-26-000009", "h1-2026-earningsrelease.htm"),
             _sei_k("2025-08-05", "0009990011-25-000010", "kore-6k_h1x2025.htm")]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, acc, **k: [
        {"seq": 1, "descrizione": "6-K", "tipo": "6-K", "ixbrl": False,
         "url": next(r["url"] for r in righe if r["accession"] == acc)}])
    lungo = " ".join(["Segment review and outlook detail."] * 200)
    rete(monkeypatch, {
        righe[0]["url"]: allegato_6k(2026, testo="Full interim report. " + lungo),
        righe[1]["url"]: allegato_6k(2026, testo="Short release."),
        righe[2]["url"]: allegato_6k(2025, testo="Full interim report. " + lungo)})
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "a", oggi="2026-10-31")
    assert out["coppia"]["dopo"]["url"] == righe[0]["url"]
    assert {c["url"]: c["stato"] for c in out["candidati"]}[righe[1]["url"]] == "duplicato"
    assert out["confronto_corrente"] is not None


def test_6k_sondaggio_limitato_e_dichiarato(monkeypatch, tmp_path, profilo_6k):
    righe = [_sei_k(f"2026-0{1 + i // 28}-{1 + i % 28:02d}", f"0009990011-26-{i:06d}") for i in range(90)]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    sondati = []
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, acc, **k: sondati.append(acc) or [])
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "a", oggi="2026-10-31")
    assert len(sondati) == filing_pipeline.MAX_6K_SONDATI
    assert any("esaminati i 80" in l for l in out["copertura"]["limiti"])


def test_6k_indice_irraggiungibile_non_ferma_gli_altri(monkeypatch, tmp_path, profilo_6k):
    righe = [_sei_k("2026-08-01", "0009990011-26-000009"), _sei_k("2025-08-01", "0009990011-25-000009")]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})

    def allegati(cik, acc, **k):
        raise OSError("indice irraggiungibile")

    monkeypatch.setattr(sec_edgar, "allegati_filing", allegati)
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "a", oggi="2026-10-31")
    assert any("indice irraggiungibile" in m for m in out["motivi"])
    assert out["confronto_corrente"] is None


def test_varianti_sceglie_la_piu_recente(monkeypatch, tmp_path, profilo_10q):
    chiamate = []

    def finto(profilo, **k):
        chiamate.append((profilo["tipo"], profilo.get("forme_sec")))
        fine = {"annuale": "2025-12-31", "trimestrale": "2026-09-30"}[profilo["tipo"]]
        return {"ticker": profilo["ticker"], "stato": "ok", "motivi": [], "candidati": [],
                "coppia": {"ambito": "ultimo_verificato", "prima": {"metadati": {"periodo_fine": "x"}},
                           "dopo": {"metadati": {"periodo_fine": fine}}},
                "confronto_corrente": {"stato": "ok", "cambiamenti": []}, "confronto_storico": None,
                "ultimo_non_verificato": False, "fonti": [], "copertura": {}, "freschezza": {}, "src": "filing_pipeline"}

    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", finto)
    profilo = {**profilo_10q, "varianti": [{"tipo": "annuale", "forme_sec": ["10-K"]}, {"tipo": "trimestrale"}]}
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a")
    assert chiamate == [("annuale", ["10-K"]), ("trimestrale", ["10-Q"])]
    assert out["variante"] == "trimestrale"
    assert [(v["tipo"], v["coppia_periodi"]) for v in out["varianti"]] == [
        ("annuale", ["x", "2025-12-31"]), ("trimestrale", ["x", "2026-09-30"])]


def test_varianti_senza_confronto_prende_la_prima(monkeypatch, tmp_path, profilo_10q):
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", lambda p, **k: {
        "ticker": p["ticker"], "stato": "parziale", "motivi": [p["tipo"]], "coppia": None,
        "confronto_corrente": None, "confronto_storico": None})
    profilo = {**profilo_10q, "varianti": [{"tipo": "annuale", "forme_sec": ["10-K"]}, {"tipo": "trimestrale"}]}
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a")
    assert out["variante"] == "annuale" and out["motivi"] == ["annuale"]


def test_numeri_fn_errore_non_rompe_il_run(monkeypatch, tmp_path, profilo_10q):
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", lambda p, **k: {
        "ticker": p["ticker"], "stato": "ok", "motivi": [], "coppia": {"prima": {}, "dopo": {}},
        "confronto_corrente": {"stato": "ok"}, "confronto_storico": None})

    def esplode(cik, coppia):
        raise OSError("rete giu")

    out = filing_pipeline.esegui_profilo(profilo_10q, archivio=tmp_path / "a", numeri_fn=esplode)
    assert out["numeri"]["stato"] == "errore" and "rete giu" in out["numeri"]["motivo"]


def test_numeri_fn_chiamata_col_cik_e_la_coppia(monkeypatch, tmp_path, profilo_10q):
    coppia = {"prima": {"metadati": {}}, "dopo": {"metadati": {}}}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", lambda p, **k: {
        "ticker": p["ticker"], "stato": "ok", "motivi": [], "coppia": coppia,
        "confronto_corrente": {"stato": "ok"}, "confronto_storico": None})
    visti = []
    out = filing_pipeline.esegui_profilo(profilo_10q, archivio=tmp_path / "a",
                                         numeri_fn=lambda cik, c: visti.append((cik, c)) or {"stato": "ok", "voci": []})
    assert visti == [(CIK_NOVA, coppia)] and out["numeri"]["stato"] == "ok"


def test_documento_risultati_tra_piu_ixbrl_vince_il_piu_grande():
    # Osservato (6-K semestrale): copertina iXBRL piccola + relazione iXBRL grande nello stesso filing.
    docs = [{"seq": 1, "descrizione": "6-K", "tipo": "6-K", "ixbrl": True, "dimensione": 40_000, "url": "https://www.sec.gov/a/k-20260630.htm"},
            {"seq": 2, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "dimensione": 2_500_000, "url": "https://www.sec.gov/a/k-20260630_d2.htm"},
            {"seq": 3, "descrizione": "", "tipo": "GRAPHIC", "ixbrl": False, "dimensione": 9_000_000, "url": "https://www.sec.gov/a/g.jpg"}]
    assert filing_pipeline._documento_risultati(docs)["url"].endswith("_d2.htm")


def test_variante_senza_sezioni_confrontate_non_e_primaria(monkeypatch, tmp_path, profilo_10q):
    # Osservato: 20-F senza sezioni riconosciute (confronto vuoto) e semestrale 6-K riuscita.
    def finto(profilo, **k):
        annuale = profilo["tipo"] == "annuale"
        return {"ticker": profilo["ticker"], "stato": "parziale" if annuale else "ok", "motivi": [], "candidati": [],
                "coppia": {"ambito": "ultimo_verificato", "prima": {"metadati": {"periodo_fine": "a"}},
                           "dopo": {"metadati": {"periodo_fine": "2025-12-31" if annuale else "2026-06-30"}}},
                "confronto_corrente": {"stato": "parziale" if annuale else "ok", "sezioni_confrontate": [] if annuale else ["testo"],
                                       "cambiamenti": []},
                "confronto_storico": None}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", finto)
    profilo = {**profilo_10q, "varianti": [{"tipo": "semestrale"}, {"tipo": "annuale"}]}
    profilo["varianti"][0]["forme_sec"] = ["6-K"]
    # la semestrale ha la fine piu' recente comunque: invertiamo le date per provare la regola
    def finto2(profilo, **k):
        r = finto(profilo, **k)
        r["coppia"]["dopo"]["metadati"]["periodo_fine"] = "2026-12-31" if profilo["tipo"] == "annuale" else "2026-06-30"
        return r
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", finto2)
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a")
    assert out["variante"] == "semestrale"


def test_6k_rettifica_non_rende_storico_il_confronto(monkeypatch, tmp_path, profilo_6k):
    # Review finale: un 6-K/A (avviso rettificato) non deve declassare il confronto a storico.
    righe = [_sei_k("2026-09-01", "0009990011-26-000011", "notice-a.htm"),
             _sei_k("2026-08-01", "0009990011-26-000009"), _sei_k("2025-08-01", "0009990011-25-000009")]
    righe[0]["form"] = "6-K/A"
    monkeypatch.setattr(sec_edgar, "get_filing_catalog", lambda *a, **k: {"stato": "ok", "motivi": [], "documenti": righe})
    allegati = {"0009990011-26-000009": [{"seq": 1, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "url": BASE_KORE + "kore-20260630.htm"}],
                "0009990011-25-000009": [{"seq": 1, "descrizione": "EX-99.1", "tipo": "EX-99.1", "ixbrl": True, "url": BASE_KORE + "kore-20250630.htm"}]}
    monkeypatch.setattr(sec_edgar, "allegati_filing", lambda cik, acc, **k: allegati[acc])
    rete(monkeypatch, {BASE_KORE + "kore-20260630.htm": allegato_6k(2026, testo="Outlook lowered."),
                       BASE_KORE + "kore-20250630.htm": allegato_6k(2025)})
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "a", oggi="2026-10-31")
    assert out["ultimo_non_verificato"] is False and out["confronto_corrente"] is not None
    assert any("6-K/A" in l for l in out["copertura"]["limiti"])


def test_ogni_variante_conserva_il_suo_confronto(monkeypatch, tmp_path, profilo_10q):
    # Spec §5.2 b: il run salva un confronto per variante.
    def finto(profilo, **k):
        fine = {"annuale": "2025-12-31", "trimestrale": "2026-09-30"}[profilo["tipo"]]
        return {"ticker": profilo["ticker"], "stato": "ok", "motivi": [], "candidati": [],
                "coppia": {"ambito": "ultimo_verificato", "prima": {"metadati": {"periodo_fine": "x"}},
                           "dopo": {"metadati": {"periodo_fine": fine}}},
                "confronto_corrente": {"stato": "ok", "cambiamenti": [{"tipo": profilo["tipo"]}]}, "confronto_storico": None}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", finto)
    profilo = {**profilo_10q, "varianti": [{"tipo": "annuale", "forme_sec": ["10-K"]}, {"tipo": "trimestrale"}]}
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a")
    annuale = out["varianti"][0]
    assert annuale["confronto"]["cambiamenti"] == [{"tipo": "annuale"}]
    assert annuale["coppia"]["dopo"]["metadati"]["periodo_fine"] == "2025-12-31"


def test_numeri_ripiegano_sulla_variante_con_dati(monkeypatch, tmp_path, profilo_10q):
    # Review finale: la variante 6-K primaria non ha numeri XBRL; si mostrano quelli dell'annuale, dichiarandolo.
    def finto(profilo, **k):
        fine = {"annuale": "2025-12-31", "semestrale": "2026-06-30"}[profilo["tipo"]]
        return {"ticker": profilo["ticker"], "stato": "ok", "motivi": [], "candidati": [],
                "coppia": {"ambito": "ultimo_verificato", "prima": {"metadati": {"periodo_fine": "x"}},
                           "dopo": {"metadati": {"periodo_fine": fine}}},
                "confronto_corrente": {"stato": "ok", "cambiamenti": []}, "confronto_storico": None}
    monkeypatch.setattr(filing_pipeline, "_esegui_singolo", finto)
    profilo = {**profilo_10q, "varianti": [{"tipo": "annuale", "forme_sec": ["10-K"]}, {"tipo": "semestrale"}]}

    def numeri(cik, coppia):
        fine = coppia["dopo"]["metadati"]["periodo_fine"]
        return {"stato": "ok", "voci": [{"voce": "ricavi"}]} if fine == "2025-12-31" else {"stato": "vuoto", "voci": []}

    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "a", numeri_fn=numeri)
    assert out["variante"] == "semestrale"
    assert out["numeri"]["stato"] == "ok" and out["numeri"]["variante"] == "annuale"


def test_guasti_transitori_marcati_per_il_controllo_leggero(monkeypatch, tmp_path, profilo_10q, profilo_6k):
    righe = [_riga("10-Q", "2026-10-30", "0009990001-26-000003", "q3.htm", "2026-09-30")]
    monkeypatch.setattr(sec_edgar, "get_filing_catalog",
                        lambda ticker, days=1100, max_pages=20, cik=None: {"stato": "ok", "motivi": [], "documenti": righe})
    rete(monkeypatch, {BASE_NOVA + "000999000126000003/q3.htm": ConnectionError("timeout")})
    out = filing_pipeline.esegui_profilo(profilo_10q, archivio=tmp_path / "a", oggi="2026-10-31")
    assert out["candidati"][0]["errore_acquisizione"] is True and not filing_pipeline.esito_completo(out)

    righe[:] = [_riga("6-K", "2026-08-01", "0009990011-26-000001", "k.htm", base=BASE_KORE, cik=CIK_KORE, ticker="KORE.DE")]

    def indice_rotto(cik, acc):
        raise ConnectionError("indice")
    monkeypatch.setattr(sec_edgar, "allegati_filing", indice_rotto)
    out = filing_pipeline.esegui_profilo(profilo_6k, archivio=tmp_path / "b", oggi="2026-10-31")
    assert out["fonti"][0]["indici_non_letti"] == 1 and not filing_pipeline.esito_completo(out)
    assert filing_pipeline.esito_completo({"fonti": [{"stato": "ok"}], "candidati": [{"stato": "non_verificato"}]})
