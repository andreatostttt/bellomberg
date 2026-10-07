"""Fase F, Task 4: pacchetti ESEF dal sito dell'emittente. Sito, pacchetti e GLEIF finti, nessuna rete."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from bellomberg.market_data import esef, esef_sito, filing_attivazione, filing_esef, filing_pipeline, ixbrl_oim
from bellomberg.market_data.filing_profili_auto import profilo_esef
from tests.esef_sito_sintetici import RispostaFinta, get_finto, pacchetto, pagina
from tests.filing_esef_sintetici import LEI_KORE, LEI_NOVA, json_nova, riga_indice

SITO = "https://www.nova.example/"
IR = "https://www.nova.example/investors"
REPORTS = "https://group.nova.example/financial-reports"
OGGI = date(2026, 10, 3)


def _nome(anno, n=1, lingua="en", lei=LEI_NOVA, est="xbri"):
    return f"{lei}-{anno}-12-31-{n}-{lingua}.{est}"


def _sito(pacchetti_link, *, extra_home=(), robots=None):
    home = pagina((IR, "Investor relations"), ("https://www.nova.example/products", "Products"),
                  ("https://altro.example/investors", "Investors elsewhere"), *extra_home)
    ir = pagina((REPORTS, "Financial reports"), ("https://www.nova.example/about/sustainability", "Sustainability"))
    rep = pagina(*pacchetti_link, ("https://group.nova.example/docs/annual-report-2025.pdf", "Annual report 2025"))
    pagine = {SITO: home, IR: ir, REPORTS: rep}
    if robots is not None:
        pagine["https://www.nova.example/robots.txt"] = RispostaFinta("", robots.encode(), tipo="text/plain")
    return pagine


def _navigatore(pagine, chiamate, pause):
    return lambda dominio: esef_sito.Navigatore(dominio, get=get_finto(pagine, chiamate), dormi=pause.append)


def test_dominio_registrabile_e_stesso_dominio():
    assert esef_sito.dominio_registrabile("group.nova.example") == "nova.example"
    assert esef_sito.dominio_registrabile("ir.nova.co.uk") == "nova.co.uk"
    assert esef_sito.stesso_dominio("https://group.nova.example/a", "nova.example")
    assert not esef_sito.stesso_dominio("http://www.nova.example/a", "nova.example")  # solo HTTPS
    assert not esef_sito.stesso_dominio("https://nova.example.evil.example/a", "nova.example")
    assert not esef_sito.stesso_dominio("https://u:p@www.nova.example/a", "nova.example")


def test_esplora_segue_solo_pagine_ir_dello_stesso_dominio_con_pausa(tmp_path):
    chiamate, pause = [], []
    link = [(f"/files/{_nome(2025)}", "ESEF 2025"), (f"https://group.nova.example/f/{_nome(2024)}", "ESEF 2024")]
    pagine = _sito([(REPORTS.rsplit("/", 1)[0] + h if h.startswith("/") else h, t) for h, t in link])
    esito = esef_sito.scopri("NOVA.MI", lei=LEI_NOVA, oggi=OGGI, cache_dir=tmp_path, sito_fn=lambda t: SITO,
                             navigatore_fn=_navigatore(pagine, chiamate, pause))
    pagine_html = [c for c in chiamate if not c.endswith("robots.txt")]
    assert pagine_html[:3] == [SITO, IR, REPORTS]  # niente «Products» ne' «Sustainability»
    # seguito main 06/10: il link «Investors» della home verso un ALTRO dominio si prova come sito IR,
    # col robots.txt e il ritmo di quel dominio (un navigatore per dominio: la sua prima richiesta senza pausa)
    assert pagine_html[3:] == ["https://altro.example/investors"] and "https://altro.example/robots.txt" in chiamate
    assert all(p == esef_sito.PAUSA_S for p in pause) and len(pause) == len(chiamate) - 2
    assert [(p["lei"], p["period_end"], p["lingua"]) for p in esito["pacchetti"]] == [
        (LEI_NOVA, "2025-12-31", "en"), (LEI_NOVA, "2024-12-31", "en")]
    assert esito["pdf"][0]["url"].endswith("annual-report-2025.pdf") and esito["pdf"][0]["testo"] == "Annual report 2025"
    # cache 7 giorni: nessuna richiesta in piu'
    chiamate.clear()
    again = esef_sito.scopri("NOVA.MI", lei=LEI_NOVA, oggi=OGGI + timedelta(days=6), cache_dir=tmp_path,
                             sito_fn=lambda t: SITO, navigatore_fn=_navigatore(pagine, chiamate, pause))
    assert chiamate == [] and again["pacchetti"] == esito["pacchetti"]
    esef_sito.scopri("NOVA.MI", lei=LEI_NOVA, oggi=OGGI + timedelta(days=8), cache_dir=tmp_path,
                     sito_fn=lambda t: SITO, navigatore_fn=_navigatore(pagine, chiamate, pause))
    assert chiamate  # scaduta: si rifa'


def test_limite_pagine_robots_e_redirect_fuori_dominio(tmp_path):
    chiamate, pause = [], []
    molte = [(f"https://www.nova.example/investors/report-{i}", f"Report {i}") for i in range(30)]
    pagine = _sito([], extra_home=molte, robots="User-agent: *\nDisallow: /investors\n")
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, chiamate, pause)("nova.example"))
    assert not any("/investors" in c for c in chiamate)  # robots.txt rispettato
    assert any("robots.txt esclude" in m for m in esito["motivi"])
    pagine = _sito([], extra_home=molte)
    pagine.update({u: pagina(titolo=t) for u, t in molte})
    chiamate.clear()
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, chiamate, pause)("nova.example"))
    assert len(esito["pagine"]) <= esef_sito.MAX_PAGINE
    assert any("limite di 12 pagine" in m for m in esito["motivi"])
    pagine = {SITO: RispostaFinta(SITO, b"", status=302, location="https://altro.example/")}
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, [], [])("nova.example"))
    assert esito["pagine"] == [] and "fuori dal dominio" in " ".join(esito["motivi"])


def test_pagina_troppo_grande_o_non_html_scartata():
    nav = esef_sito.Navigatore("nova.example", get=get_finto({
        SITO: b"x" * (esef_sito.MAX_PAGINA_BYTES + 1),
        IR: RispostaFinta(IR, b"%PDF", tipo="application/pdf")}, []), dormi=lambda s: None)
    with pytest.raises(ValueError, match="troppo grande"):
        nav.pagina(SITO)
    with pytest.raises(ValueError, match="non HTML"):
        nav.pagina(IR)


def test_nomi_dei_pacchetti():
    link = [{"url": f"https://x.nova.example/a/{n}"} for n in (
        _nome(2025), _nome(2025, 2, "it", est="zip"), f"{LEI_NOVA}-2023-12-31-0.zip", "relazione-2025.zip",
        f"{LEI_NOVA}-2025-13-45-1-en.xbri", _nome(2024).replace(".xbri", ".pdf"))]
    link.append({"url": f"http://x.nova.example/{_nome(2022)}"})
    out = esef_sito.pacchetti(link)
    assert [(p["period_end"], p["versione"], p["lingua"], p["formato"]) for p in out] == [
        ("2025-12-31", 1, "en", "xbri"), ("2025-12-31", 2, "it", "zip"), ("2023-12-31", 0, None, "zip")]


def test_righe_da_cache_una_per_esercizio(tmp_path):
    (tmp_path / "NOVA.MI.json").write_text(json.dumps({"pacchetti": [
        {"url": "https://n.example/a", "lei": LEI_NOVA, "period_end": "2025-12-31", "versione": 1, "lingua": "it"},
        {"url": "https://n.example/b", "lei": LEI_NOVA, "period_end": "2025-12-31", "versione": 1, "lingua": "en"},
        {"url": "https://n.example/c", "lei": LEI_NOVA, "period_end": "2024-12-31", "versione": 1, "lingua": "it"},
        {"url": "https://n.example/d", "lei": LEI_NOVA, "period_end": "2024-12-31", "versione": 2, "lingua": "it"},
        {"url": "https://n.example/e", "lei": LEI_KORE, "period_end": "2025-12-31", "versione": 1, "lingua": "en"}]}))
    righe = esef_sito.righe_da_cache(LEI_NOVA, cache_dir=tmp_path)
    assert [(r["period_end"], r["json_url"], r["origine"]) for r in righe] == [
        ("2025-12-31", "https://n.example/b", "sito"), ("2024-12-31", "https://n.example/d", "sito")]


def _righe_sito(*anni):
    return [{"id": f"s{a}", "period_end": f"{a}-12-31", "json_url": f"https://group.nova.example/f/{_nome(a)}",
             "report_url": None, "language": None, "date_added": None, "origine": "sito"} for a in anni]


def test_catalogo_repository_fermo_usa_il_sito():
    indice = lambda lei: {"righe": [riga_indice(1, 2021), riga_indice(2, 2022)], "origine": "rete"}
    cat = filing_esef.righe_catalogo(LEI_NOVA, "en", oggi=OGGI, indice_fn=indice,
                                     sito_fn=lambda lei: _righe_sito(2025, 2024, 2023, 2022))
    assert [(r["period_end"][:4], r.get("origine")) for r in cat["righe"]] == [("2025", "sito"), ("2024", "sito"), ("2023", "sito")]
    assert cat["fermo"] is False and cat["dal_sito"] is True and cat["ultimo_periodo"] == "2025-12-31"
    assert not any("fermo" in m for m in cat["motivi"])
    assert any("FY2025, FY2024, FY2023 dal sito dell'emittente" in l and "fermo all'esercizio FY2022" in l for l in cat["limiti"])


def test_catalogo_repository_aggiornato_non_guarda_il_sito():
    indice = lambda lei: {"righe": [riga_indice(1, 2024), riga_indice(2, 2025)], "origine": "rete"}
    chiamato = []
    cat = filing_esef.righe_catalogo(LEI_NOVA, "en", oggi=OGGI, indice_fn=indice,
                                     sito_fn=lambda lei: chiamato.append(lei) or _righe_sito(2025))
    assert not cat["dal_sito"]  # la cache del sito si legge (locale), ma lo stesso esercizio vince il repository


def test_revisione_esercizio_senza_json_sul_repository_preso_dal_sito():
    indice = lambda lei: {"righe": [riga_indice(1, 2024), riga_indice(2, 2025, json=False)], "origine": "rete"}
    cat = filing_esef.righe_catalogo(LEI_NOVA, "en", oggi=OGGI, indice_fn=indice, sito_fn=lambda lei: _righe_sito(2025))
    assert [(r["period_end"][:4], r.get("origine")) for r in cat["righe"]] == [("2025", "sito"), ("2024", None)]
    assert cat["dal_sito"] and any("fermo all'esercizio FY2024" in l for l in cat["limiti"])


def test_catalogo_emittente_assente_dal_repository():
    cat = filing_esef.righe_catalogo(LEI_NOVA, "en", oggi=OGGI, indice_fn=lambda lei: {"righe": [], "origine": "rete"},
                                     sito_fn=lambda lei: _righe_sito(2025, 2024))
    assert [r["period_end"][:4] for r in cat["righe"]] == ["2025", "2024"]
    assert any("senza depositi di questo emittente" in l for l in cat["limiti"])


def _scarica_finto(contenuti, chiamate):
    import hashlib

    def scarica(url, dest, max_bytes, hosts):
        chiamate.append((url, max_bytes, set(hosts)))
        Path(dest).mkdir(parents=True, exist_ok=True)
        p = Path(dest) / ("pkg-" + hashlib.sha256(contenuti[url]).hexdigest() + ".xbri")
        p.write_bytes(contenuti[url])
        return {"stato": "ok", "path": str(p), "sha256": hashlib.sha256(contenuti[url]).hexdigest()}
    return scarica


def test_pipeline_coppia_dai_pacchetti_del_sito(monkeypatch, tmp_path):
    """Repository fermo al FY2022, pacchetti 2024 e 2025 sul sito: coppia corrente, verificata sui
    fatti convertiti in locale, numeri dal pacchetto, fonte dichiarata."""
    monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": [riga_indice(2, 2022, json=False)], "origine": "rete"})
    righe = _righe_sito(2025, 2024)
    monkeypatch.setattr(esef_sito, "righe_da_cache", lambda lei, **k: righe)
    contenuti = {righe[0]["json_url"]: pacchetto(json_nova(2025)), righe[1]["json_url"]: pacchetto(json_nova(2024))}
    chiamate = []
    vero = esef_sito.scarica_pacchetto_json
    monkeypatch.setattr(esef_sito, "scarica_pacchetto_json",
                        lambda url, arch, hosts: vero(url, arch, hosts, scarica_fn=_scarica_finto(contenuti, chiamate)))
    profilo = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="gleif", lingua="en")
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "archivio", oggi="2026-10-03")
    assert out["stato"] == "ok", out["motivi"]
    assert [d["metadati"]["periodo_fine"] for d in (out["coppia"]["prima"], out["coppia"]["dopo"])] == ["2024-12-31", "2025-12-31"]
    assert out["confronto_corrente"]["cambiamenti"]
    assert out["numeri"]["stato"] == "ok" and any(v["voce"] == "ricavi" for v in out["numeri"]["voci"])
    esef_fonte = next(f for f in out["fonti"] if f["nome"] == "ESEF")
    assert esef_fonte["dal_sito"] is True and not esef_fonte.get("fermo")
    assert {c[1] for c in chiamate} == {80 * 1024 * 1024} and all(c[2] == {"group.nova.example"} for c in chiamate)
    assert not list((tmp_path / "archivio" / "pacchetti").glob("*.xbri"))  # resta solo il JSON convertito
    # secondo run: dall'archivio, nessun nuovo download
    chiamate.clear()
    out2 = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "archivio", oggi="2026-10-03")
    assert out2["stato"] == "ok" and chiamate == []


@pytest.mark.parametrize("caso", ["lei", "periodo"])
def test_pacchetto_con_lei_o_periodo_diversi_dal_nome_scartato(monkeypatch, tmp_path, caso):
    monkeypatch.setattr(esef, "indice_depositi", lambda lei, **k: {"righe": [], "origine": "rete"})
    righe = _righe_sito(2025, 2024)
    monkeypatch.setattr(esef_sito, "righe_da_cache", lambda lei, **k: righe)
    falso = json_nova(2025, lei=LEI_KORE) if caso == "lei" else json_nova(2023)
    contenuti = {righe[0]["json_url"]: pacchetto(falso), righe[1]["json_url"]: pacchetto(json_nova(2024))}
    vero = esef_sito.scarica_pacchetto_json
    monkeypatch.setattr(esef_sito, "scarica_pacchetto_json",
                        lambda url, arch, hosts: vero(url, arch, hosts, scarica_fn=_scarica_finto(contenuti, [])))
    profilo = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova S.p.A.", origine="gleif", lingua="en")
    out = filing_pipeline.esegui_profilo(profilo, archivio=tmp_path / "archivio", oggi="2026-10-03")
    cand = next(c for c in out["candidati"] if c["url"] == righe[0]["json_url"])
    assert cand["stato"] == "non_verificato"
    assert ("LEI" in " ".join(cand["motivi"])) if caso == "lei" else ("periodo" in " ".join(cand["motivi"]))
    assert not out.get("confronto_corrente")


def test_pacchetto_non_valido_dichiarato(monkeypatch, tmp_path):
    righe = _righe_sito(2025)
    contenuti = {righe[0]["json_url"]: b"non e' uno zip"}
    with pytest.raises(ixbrl_oim.PacchettoNonValido):
        esef_sito.scarica_pacchetto_json(righe[0]["json_url"], tmp_path, {"group.nova.example"},
                                         scarica_fn=_scarica_finto(contenuti, []))


class _Store:
    def __init__(self, profili):
        self.profili = profili

    def list_profiles(self):
        return self.profili


def _riga(ticker, profilo, enabled=True):
    return {"ticker": ticker, "profile": profilo, "enabled": enabled}


def test_scopri_profili_solo_esef_fermi_e_mai_con_il_consigliere():
    nova = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="en")
    kore = profilo_esef("KORE.MI", lei=LEI_KORE, nome="Kore", origine="nome", lingua="en")
    store = _Store([_riga("NOVA.MI", nova), _riga("KORE.MI", kore), _riga("SPENTO.MI", nova, enabled=False),
                    _riga("SEC.F", {"cik": "0009990001", "forme_sec": ["10-Q"]})])
    indici = {LEI_NOVA: [riga_indice(1, 2022)], LEI_KORE: [riga_indice(1, 2025, lei=LEI_KORE)]}
    esplorati = []
    scopri = lambda t, lei, oggi: esplorati.append(t) or {"pacchetti": [{"lei": LEI_NOVA}], "motivi": []}
    out = esef_sito.scopri_profili(store, oggi=OGGI, consigliere_fn=lambda: False,
                                   indice_fn=lambda lei: {"righe": indici[lei]}, scopri_fn=scopri)
    assert esplorati == ["NOVA.MI"] and out == [{"ticker": "NOVA.MI", "pacchetti": 1, "motivi": []}]
    esplorati.clear()
    out = esef_sito.scopri_profili(store, oggi=OGGI, consigliere_fn=lambda: True,
                                   indice_fn=lambda lei: {"righe": indici[lei]}, scopri_fn=scopri)
    assert esplorati == [] and out[0]["rinviata"] is True


def test_controllo_giornaliero_esplora_prima_dei_run():
    from bellomberg.market_data.filing_refresh import FilingRefreshManager
    ordine = []

    class Svc:
        store = _Store([])

        def recupera_orfani(self, eta_max_ore):
            return []

        def run_due(self, **k):
            ordine.append("run_due")
            return []

    m = FilingRefreshManager(lambda: Svc(), auto_enabled=True, esclusi_fn=frozenset,
                             scoperte_fn=lambda store, escludi: ordine.append("siti") or [{"ticker": "NOVA.MI"}])
    assert m._lavoro()["siti"] == [{"ticker": "NOVA.MI"}] and ordine == ["siti", "run_due"]


def _gleif(*record):
    return lambda nome: [{"attributes": {"lei": lei, "entity": {"legalName": {"name": n}, "status": s,
                                                                 "legalAddress": {"country": c}}}}
                         for lei, n, s, c in record]


def test_gleif_nome_identico_attivo_e_paese_esef():
    g = esef.candidati_gleif("Nova Example AG", cerca=_gleif((LEI_NOVA, "NOVA EXAMPLE AG", "ACTIVE", "DE"),
                                                              (LEI_KORE, "Nova Example AG", "INACTIVE", "DE"),
                                                              ("999900CANA0000000001", "Nova Example AG", "ACTIVE", "CA"),
                                                              ("999900SIMI0000000001", "Nova Example Holding AG", "ACTIVE", "DE")))
    assert g["stato"] == "univoco" and g["candidati"] == [{"lei": LEI_NOVA, "nome": "NOVA EXAMPLE AG", "origine": "gleif"}]
    assert esef.candidati_gleif("Nova Example AG", cerca=_gleif())["stato"] == "nessuno"
    due = esef.candidati_gleif("Nova Example AG", cerca=_gleif((LEI_NOVA, "Nova Example AG", "ACTIVE", "DE"),
                                                               (LEI_KORE, "Nova Example AG", "ACTIVE", "AT")))
    assert due["stato"] == "ambiguo" and len(due["candidati"]) == 2
    assert esef.candidati_gleif("Nova Example AG", cerca=lambda n: 1 / 0)["stato"] == "errore"


def test_candidati_lei_ricade_su_gleif_solo_senza_entita_sul_repository(monkeypatch):
    monkeypatch.setattr("bellomberg.storage.negozi_privati.carica_lei", lambda: {"lei": {}, "origine": "vuoto"})
    monkeypatch.setattr(esef, "_cerca_entita", lambda nome, ritmo=None: ([], ["Nova Example"], None, None))
    monkeypatch.setattr(esef_sito, "gleif_lei_records", _gleif((LEI_NOVA, "Nova Example AG", "ACTIVE", "DE")))
    out = esef.candidati_lei("NOVA.DE", "Nova Example AG", ritmo=lambda: None)
    assert out["stato"] == "univoco" and out["candidati"][0]["origine"] == "gleif"
    assert out["motivo"].startswith("nessuna entita' sul repository ESEF")


class _StoreAttivazione:
    def __init__(self):
        self.salvati = []

    def get_profile(self, ticker):
        return None

    def set_profile(self, ticker, profilo, **k):
        self.salvati.append(profilo)
        return {"version": 1}


def test_attivazione_con_pacchetti_sul_sito():
    store = _StoreAttivazione()
    scelto = {"lei": LEI_NOVA, "nome": "Nova Example AG"}
    scopri = lambda t, lei: {"pacchetti": [{"lei": LEI_NOVA, "period_end": "2025-12-31", "lingua": "en"},
                                           {"lei": LEI_KORE, "period_end": "2025-12-31", "lingua": "en"}], "motivi": []}
    out = filing_attivazione._attiva_esef(store, "NOVA.DE", scelto, "gleif", None, {}, "m",
                                          lambda lei: {"righe": []}, scopri_fn=scopri)
    assert out["esito"] == "attivato" and out["dal_sito"] == 1 and out["esercizi"] == ["2025"]
    assert store.salvati[0]["lei"] == LEI_NOVA and store.salvati[0]["lingua"] == "en"
    vuoto = filing_attivazione._attiva_esef(_StoreAttivazione(), "NOVA.DE", scelto, "gleif", None, {}, "m",
                                            lambda lei: {"righe": []},
                                            scopri_fn=lambda t, lei: {"pacchetti": [], "motivi": ["sito della societa' non noto (yfinance)"]})
    assert vuoto["esito"] == "senza_fonte" and "sito della societa' non noto" in vuoto["motivo"]


def test_contesto_dichiara_la_fonte_dal_sito():
    from bellomberg.agents.filing_context import scheda_da_run
    profilo = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova", origine="gleif", lingua="en")
    run = {"id": 7, "finished_at": "2026-10-03T08:00:00", "profile": profilo, "result": {
        "variante": "annuale", "fonti": [{"nome": "ESEF", "stato": "ok", "dal_sito": True}],
        "coppia": {"prima": {"url": "https://filings.xbrl.org/x/2024.json", "metadati": {"periodo_fine": "2024-12-31"}},
                   "dopo": {"url": f"https://group.nova.example/f/{_nome(2025)}", "metadati": {"periodo_fine": "2025-12-31"}}},
        "confronto_corrente": {"stato": "ok", "cambiamenti": [], "sezioni_confrontate": ["a"]}}}
    scheda = scheda_da_run("NOVA.MI", profilo=profilo, run=run, ultimo=run, freschezza={"stato": "aggiornato"},
                           novita_dopo=None)
    assert "dal sito dell'emittente" in scheda["stato"] or "from the issuer's website" in scheda["stato"]
    # una riga del sito nel catalogo che poi non entra nella coppia non basta
    run["result"]["coppia"]["dopo"]["url"] = "https://filings.xbrl.org/x/2025.json"
    scheda = scheda_da_run("NOVA.MI", profilo=profilo, run=run, ultimo=run, freschezza={"stato": "aggiornato"},
                           novita_dopo=None)
    assert "dal sito" not in scheda["stato"] and "website" not in scheda["stato"]


def test_prova_reale_file_nel_testo_statico_della_pagina():
    html = ('<script>{"doc":"/assets/ir/2025-annual-report-v01-00-en.pdf","pkg":"https:\\/\\/ir.nova.example\\/'
            + _nome(2025) + '"}</script><a href="/x.pdf">X</a>')
    urls = [u for u, _ in esef_sito.estrai_link(html, "https://www.nova.example/investors")]
    assert urls == ["https://www.nova.example/x.pdf", "https://www.nova.example/assets/ir/2025-annual-report-v01-00-en.pdf",
                    f"https://ir.nova.example/{_nome(2025)}"]


def test_prova_reale_anni_della_sezione_lingue_e_redirect():
    rel = "https://group.nova.example/it/investor-relations/bilanci-e-relazioni"
    pagine = {SITO: pagina(("https://group.nova.example/it/investor-relations", "Investor relations"),
                           ("https://www.nova.example/de/investor-relations", "Investor relations (DE)")),
              "https://group.nova.example/it/investor-relations": pagina(
                  (rel, "Bilanci e relazioni"), ("https://group.nova.example/it/investor-relations/politiche-degli-investitori", "Politiche"),
                  ("https://group.nova.example/it/investor-relations/archivio", "Archivio report")),
              "https://group.nova.example/it/investor-relations/archivio": RispostaFinta("", b"", status=301, location=rel),
              rel: pagina(*[(f"{rel}/{a}", str(a)) for a in range(2010, 2027)]),
              f"{rel}/2025": pagina((f"https://group.nova.example/doc/{_nome(2025)}", "ESEF 2025"))}
    chiamate = []
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, chiamate, [])("nova.example"), oggi=OGGI)
    visitate = [p for p in esito["pagine"]]
    assert visitate[:3] == [SITO, "https://group.nova.example/it/investor-relations", rel]
    assert f"{rel}/2025" in visitate and f"{rel}/2026" in chiamate  # tre livelli sotto la home: anni della sezione
    assert not any(a in p for p in visitate for a in ("/2019", "/2010"))  # anni vecchi mai
    assert not any("politiche" in p for p in visitate)
    assert visitate.count(rel) == 1  # il redirect dell'archivio non conta di nuovo
    assert "https://www.nova.example/de/investor-relations" not in chiamate or len(visitate) == len(set(visitate))
    assert [p["period_end"] for p in esef_sito.pacchetti(esito["link"])] == ["2025-12-31"]


def test_prova_reale_lei_da_gleif_assente_dal_repository_e_nessun_deposito(monkeypatch):
    import requests

    class R:
        status_code = 404

        def raise_for_status(self):
            raise requests.HTTPError("404")

    chiamate = []
    monkeypatch.setattr(requests, "get", lambda url, **k: chiamate.append(url) or R())
    # Revisione 04/10 (R8): il 404 si dichiara (prima diventava un elenco vuoto muto).
    with pytest.raises(esef.EntitaEsefAssente):
        esef._list_filings(LEI_NOVA)
    assert len(chiamate) == 1


def test_prova_reale_comunicati_stampa_mai_visitati():
    pagine = {SITO: pagina(("https://www.nova.example/media/press/2026/03/nova-2025-results", "Nova 2025 results"),
                           ("https://www.nova.example/investors/financials", "Financial reports"))}
    chiamate = []
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, chiamate, [])("nova.example"), oggi=OGGI)
    assert not any("/media/" in c for c in chiamate) and "https://www.nova.example/investors/financials" in chiamate


def test_revisione_sezione_ir_di_due_lettere_e_link_ostili():
    pagine = {SITO: pagina(("/ir", "Investor relations")),
              "https://www.nova.example/ir": pagina((f"/ir/{_nome(2025, est='zip')}", "ESEF 2025"))}
    esito = esef_sito.esplora(SITO, navigatore=_navigatore(pagine, [], [])("nova.example"), oggi=OGGI)
    assert "https://www.nova.example/ir" in esito["pagine"]
    assert esef_sito._normalizza("https://www.nova.example/de/investor") == esef_sito._normalizza("https://www.nova.example/investor")
    import time as _t
    t0 = _t.perf_counter()
    esef_sito._punteggio("https://www.nova.example/reports" + "-" * 60000 + "x", "reports " + " " * 60000)
    assert _t.perf_counter() - t0 < 0.5


def test_revisione_tetto_di_tempo_per_pagina_e_per_sito():
    adesso = [0.0]
    nav = esef_sito.Navigatore("nova.example", get=get_finto({SITO: pagina()}, []), dormi=lambda s: None,
                               tempo_max=10, orologio=lambda: adesso[0])
    adesso[0] = 11
    with pytest.raises(TimeoutError):
        nav.pagina(SITO)


def test_revisione_esplorazione_fallita_si_riprova_il_giorno_dopo(tmp_path):
    visite = []
    nav = lambda d: esef_sito.Navigatore(d, get=lambda url, **k: visite.append(url) or (_ for _ in ()).throw(ConnectionError("giu'")),
                                         dormi=lambda s: None)
    kw = dict(cache_dir=tmp_path, sito_fn=lambda t: SITO, navigatore_fn=nav)
    assert esef_sito.scopri("NOVA.MI", oggi=date.today(), **kw)["fallita"] is True
    n = len(visite)
    esef_sito.scopri("NOVA.MI", oggi=date.today(), **kw)
    assert len(visite) == n  # stesso giorno: cache
    esef_sito.scopri("NOVA.MI", oggi=date.today() + timedelta(days=1), **kw)
    assert len(visite) > n  # giorno dopo: si riprova (non fra 7 giorni)


def test_revisione_consigliere_ricontrollato_a_ogni_titolo():
    nova = profilo_esef("NOVA.MI", lei=LEI_NOVA, nome="Nova", origine="nome", lingua="en")
    kore = profilo_esef("KORE.MI", lei=LEI_KORE, nome="Kore", origine="nome", lingua="en")
    store = _Store([_riga("KORE.MI", kore), _riga("NOVA.MI", nova)])
    stato, esplorati = [False], []

    def scopri(t, lei, oggi):
        esplorati.append(t)
        stato[0] = True  # la run del Consigliere parte durante il primo sito
        return {"pacchetti": [], "motivi": []}
    out = esef_sito.scopri_profili(store, oggi=OGGI, consigliere_fn=lambda: stato[0],
                                   indice_fn=lambda lei: {"righe": [riga_indice(1, 2022, lei=lei)]}, scopri_fn=scopri)
    assert esplorati == ["KORE.MI"] and out[-1]["rinviata"] is True


def test_revisione_indice_vuoto_non_cancella_i_depositi_in_cache(tmp_path):
    righe = [riga_indice(1, 2025)]
    esef.indice_depositi(LEI_NOVA, cache_dir=tmp_path, fetch=lambda lei: righe)
    out = esef.indice_depositi(LEI_NOVA, cache_dir=tmp_path, ttl_s=0, fetch=lambda lei: [])
    assert out["righe"] == righe and out["origine"] == "cache_scaduta"


def test_revisione_redirect_del_pacchetto_solo_https(tmp_path, monkeypatch):
    from bellomberg.market_data import download_sicuro, lettore_trimestrali
    monkeypatch.setattr(lettore_trimestrali, "_richiedi_indirizzi_pubblici", lambda h, p: None)
    url = f"https://group.nova.example/f/{_nome(2025)}"
    monkeypatch.setattr(lettore_trimestrali.requests, "get", lambda u, **k: RispostaFinta(
        u, b"", status=302, location="http://group.nova.example/f/x.xbri"))
    esito = download_sicuro.scarica_limitato(url, tmp_path, 1000, {"group.nova.example"})
    assert esito["stato"] == "errore" and "HTTPS" in esito["motivo"]
