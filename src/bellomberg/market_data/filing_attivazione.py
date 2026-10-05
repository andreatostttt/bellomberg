"""Attivazione dei profili filing: proposta -> profilo standard -> salvataggio.

Gratis e deterministico (solo fonti SEC pubbliche, nessuna AI). Il profilo e'
sempre salvato col ticker del portafoglio. I collegamenti ambigui restano
proposte; un errore su un titolo non ferma l'attivazione in blocco.
"""
from datetime import date

from bellomberg.market_data import filing_identita
from bellomberg.market_data.filing_identita import proponi
from bellomberg.market_data.filing_profili_auto import (forme_presenti, profilo_esef, profilo_sec, regex_confermata,
                                                         sonda_tipo_6k)
from bellomberg.storage import filing_preferenze

INTERVALLO_AUTO_ORE = 24


def _catalogo_default(ticker, cik=None):
    from bellomberg.market_data.sec_edgar import get_filing_catalog
    return get_filing_catalog(ticker, cik=cik)


def _attiva_esef(store, ticker, scelto, origine, nome, proposta, motivo, indice_fn, scopri_fn=None):
    """Profilo ESEF a blocchi: serve almeno un deposito con xBRL-JSON; lingua = inglese se c'e'.

    Fase F: senza depositi sul repository (o col repository fermo) si cercano i pacchetti
    ufficiali sul sito dell'emittente (l'attivazione e' un'azione dell'utente: rete ammessa)."""
    from bellomberg.market_data import esef, esef_sito
    from bellomberg.market_data.filing_esef import scegli_lingua
    try:
        righe = (indice_fn or esef.indice_depositi)(scelto["lei"])["righe"]
    except Exception as exc:
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": f"indice ESEF: {type(exc).__name__}: {exc}"[:300]}
    con_json = [r for r in righe if isinstance(r, dict) and r.get("json_url") and r.get("period_end")]
    ultimo = max((r["period_end"] for r in con_json), default=None)
    dal_sito, nota_sito = [], None
    if esef_sito.atteso_piu_recente(ultimo, date.today()) and esef_sito.consigliere_in_corso():
        # mai esplorare durante una run del Consigliere (revisione finale): ci pensa il giro dopo
        nota_sito = "sito dell'emittente non esplorato: run del Consigliere in corso (si riprova al controllo giornaliero)"
    elif esef_sito.atteso_piu_recente(ultimo, date.today()):
        try:
            trovato = (scopri_fn or esef_sito.scopri)(ticker, lei=scelto["lei"])
            lei = str(scelto["lei"]).upper()
            dal_sito = [p for p in trovato.get("pacchetti") or [] if p.get("lei") == lei]
            if not dal_sito:
                nota_sito = ("sito dell'emittente: " + "; ".join((trovato.get("motivi") or [])[:2])
                             if trovato.get("motivi") else "nessun pacchetto ESEF sul sito dell'emittente")
        except Exception as exc:
            nota_sito = f"sito dell'emittente: {type(exc).__name__}: {str(exc)[:120]}"
    if not con_json and not dal_sito:
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta,
                "motivo": "nessun deposito ESEF con xBRL-JSON sul repository filings.xbrl.org"
                          + (f"; {nota_sito}" if nota_sito else "")}
    lingue_sito = {p.get("lingua") for p in dal_sito}
    lingua = scegli_lingua(con_json, "en") if con_json else ("en" if "en" in lingue_sito or None in lingue_sito
                                                              else sorted(lingue_sito)[0])
    profilo = profilo_esef(ticker, lei=scelto["lei"], nome=nome or scelto["nome"], origine=origine, lingua=lingua)
    salvato = store.set_profile(ticker, profilo, enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    esito = {"ticker": ticker, "esito": "attivato", "fonte": "esef", "profilo_versione": salvato["version"],
             "proposta": proposta, "motivo": motivo,
             "esercizi": sorted({r["period_end"][:4] for r in con_json} | {p["period_end"][:4] for p in dal_sito})}
    if dal_sito:
        esito["dal_sito"] = len(dal_sito)
    return esito


def senza_rifiutati(proposta, cik_rifiutati, lei_rifiutati):
    """Proposta senza i candidati rifiutati, anche se `proponi_fn` non li ha filtrati."""
    out = dict(proposta)
    for chiave, campo, via in (("sec", "cik", cik_rifiutati), ("esef", "lei", lei_rifiutati)):
        p = out.get(chiave)
        if not isinstance(p, dict) or not p.get("candidati"):
            continue
        restanti = [c for c in p["candidati"] if str(c.get(campo) or "").upper() not in via]
        if len(restanti) == len(p["candidati"]):
            continue
        out[chiave] = ({**p, "candidati": restanti} if restanti else
                       {**p, "stato": "nessuno", "candidati": [], "motivo": "collegamento rifiutato dall'utente"})
    return out


def attiva(store, ticker, *, cik=None, lei=None, proponi_fn=proponi, catalogo_fn=_catalogo_default,
           pref_path=None, indice_fn=None, salta_scollegati=False):
    """Crea il profilo di `ticker`: SEC se univoco o scelto (`cik`), altrimenti ESEF se il LEI
    e' univoco o scelto (`lei`). SEC in errore: errore (mai un ripiego ESEF alla cieca).

    CIK e LEI rifiutati (preferenze) non si collegano mai. Un titolo «scollegato» (profilo
    disattivato da «Scollega») si riattiva solo con un'attivazione singola; in blocco
    (`salta_scollegati`) si salta."""
    if cik is not None and lei is not None:
        raise ValueError("indicare un CIK oppure un LEI, non entrambi")
    riga = store.get_profile(ticker)
    if riga is not None:
        try:
            pref = filing_preferenze.carica(pref_path)
        except ValueError:
            pref = None  # preferenze illeggibili: mai sovrascrivere un profilo
        if pref is None or not filing_preferenze.e_scollegato(pref, ticker, riga["version"]):
            # Mai sovrascrivere un profilo esistente (anche scritto a mano) col modello standard.
            return {"ticker": ticker, "esito": "gia_attivo", "motivo": "profilo gia' presente"}
        if salta_scollegati:
            return {"ticker": ticker, "esito": "scollegato",
                    "motivo": "collegamento annullato: si ricollega solo dalla pagina Filing"}
    esito = _attiva(store, ticker, cik=cik, lei=lei, proponi_fn=proponi_fn, catalogo_fn=catalogo_fn,
                    pref_path=pref_path, indice_fn=indice_fn)
    if riga is not None and esito.get("esito") == "attivato":
        filing_preferenze.annulla_scollegato(ticker, pref_path)
    return esito


def _attiva(store, ticker, *, cik, lei, proponi_fn, catalogo_fn, pref_path, indice_fn):
    pref = filing_preferenze.carica(pref_path)
    if ticker in pref["esclusi"]:
        return {"ticker": ticker, "esito": "escluso", "motivo": "escluso dal controllo"}
    cik_rifiutati = frozenset(pref["rifiutati"].get(ticker, []))
    lei_rifiutati = filing_preferenze.rifiutati_lei(pref, ticker)
    # `rifiutati` filtra CIK e LEI (proponi_esef), cosi' anche le firme che non conoscono rifiutati_lei.
    rifiutati = cik_rifiutati | lei_rifiutati
    proposta = senza_rifiutati(proponi_fn(ticker, rifiutati=rifiutati), cik_rifiutati, lei_rifiutati | cik_rifiutati)
    sec = proposta["sec"]
    esef_p = proposta.get("esef") or {"stato": "nessuno", "candidati": [], "motivo": "ESEF non interrogato"}
    if lei is not None:
        scelto = next((c for c in esef_p["candidati"] if c["lei"] == str(lei).upper()), None)
        if scelto is None:
            return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                    "motivo": "LEI scelto non tra i candidati proposti"}
        return _attiva_esef(store, ticker, scelto, "confermato_utente", proposta.get("nome"), proposta,
                            "LEI confermato dall'utente", indice_fn)
    if sec["stato"] == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": sec["motivo"]}
    if cik is not None:
        scelto = next((c for c in sec["candidati"] if c["cik"] == str(cik).zfill(10)), None)
        if scelto is None:
            return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                    "motivo": "CIK scelto non tra i candidati proposti"}
        origine = "confermato_utente"
    elif sec["stato"] == "univoco":
        scelto, origine = sec["candidati"][0], sec["candidati"][0]["origine"]
    elif (sec["stato"] == "ambiguo" and filing_identita.preferita({**proposta, "esef": esef_p}) == "esef"
          and esef_p["stato"] != "univoco"):
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif sec["stato"] == "ambiguo" and filing_identita.preferita({**proposta, "esef": esef_p}) == "esef":
        c = esef_p["candidati"][0]  # SEC solo per nomi simili: non e' un collegamento
        return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta,
                            f"{esef_p['motivo']} (SEC solo per nomi simili, non collegata)", indice_fn)
    elif sec["stato"] == "ambiguo":
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": sec["motivo"]}
    elif esef_p["stato"] == "univoco" and esef_p["candidati"]:
        c = esef_p["candidati"][0]
        return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta, esef_p["motivo"], indice_fn)
    elif esef_p["stato"] == "ambiguo":
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif esef_p["stato"] == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    else:
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta,
                "motivo": f"SEC: {sec['motivo']}; ESEF: {esef_p['motivo']}"}
    catalogo = catalogo_fn(ticker, cik=scelto["cik"])
    if catalogo.get("stato") == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": "catalogo SEC: " + "; ".join(catalogo.get("motivi", []))[:300]}
    forme = forme_presenti(catalogo)
    nome = proposta.get("nome") or scelto["nome"]
    # Confermato dall'utente: il nome del portafoglio puo' differire da quello SEC (vale l'uno o l'altro).
    regex = regex_confermata(nome, scelto["nome"]) if origine == "confermato_utente" else None
    try:
        profilo = profilo_sec(ticker, cik=scelto["cik"], sec_ticker=scelto["ticker"],
                              nome=nome, origine=origine, forme=forme, regex_emittente=regex)
    except ValueError as exc:
        if cik is not None or "." not in ticker:  # titoli USA: mai filings.xbrl.org (come proponi)
            return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": str(exc)}
        # Emittente SEC senza bilanci (ADR OTC): si prova l'ESEF, come se la SEC non ci fosse.
        esef_p = proposta.get("esef") or senza_rifiutati(
            {"esef": filing_identita.proponi_esef(ticker, proposta.get("nome"), rifiutati=rifiutati)},
            cik_rifiutati, rifiutati)["esef"]
        if esef_p["stato"] == "univoco" and esef_p["candidati"]:
            c = esef_p["candidati"][0]
            return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta,
                                f"{esef_p['motivo']} (SEC: {exc})", indice_fn)
        esito = "da_confermare" if esef_p["stato"] == "ambiguo" else "senza_fonte"
        return {"ticker": ticker, "esito": esito, "proposta": {**proposta, "esef": esef_p},
                "motivo": f"SEC: {exc}; ESEF: {esef_p['motivo']}"}
    salvato = store.set_profile(ticker, profilo, enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    return {"ticker": ticker, "esito": "attivato", "profilo_versione": salvato["version"], "proposta": proposta,
            "motivo": sec["motivo"], "forme": sorted(forme)}


def attiva_mancanti(store, tickers, **kw):
    """Attivazione in blocco dei titoli senza profilo: riepilogo per esito."""
    out = {k: [] for k in ("attivati", "da_confermare", "senza_fonte", "esclusi", "gia_attivi", "scollegati",
                           "errori")}
    chiave = {"attivato": "attivati", "da_confermare": "da_confermare", "senza_fonte": "senza_fonte",
              "escluso": "esclusi", "gia_attivo": "gia_attivi", "scollegato": "scollegati"}
    for ticker in dict.fromkeys(tickers):
        try:
            r = attiva(store, ticker, salta_scollegati=True, **kw)
        except Exception as exc:
            r = {"ticker": ticker, "esito": "errore", "motivo": f"{type(exc).__name__}: {exc}"}
        if r["esito"] == "errore":
            out["errori"].append({"ticker": ticker, "motivo": r["motivo"]})
        else:
            out[chiave[r["esito"]]].append(ticker)
    return out


def _testo_sec(url):
    import tempfile
    from bellomberg.market_data.lettore_trimestrali import estrai_testo, scarica_documento
    with tempfile.TemporaryDirectory() as tmp:
        r = scarica_documento(url, tmp, host_consentiti={"www.sec.gov", "sec.gov"}, public_only=True)
        if r.get("stato") != "ok":
            raise ValueError(r.get("motivo", "download non disponibile"))
        return estrai_testo(r["path"]).get("testo", "")


def completa_6k(store, ticker, *, allegati_fn=None, scarica_fn=None, catalogo_fn=_catalogo_default,
                oggi=None, max_6k=None):
    """Emittenti 20-F: aggiunge la variante infrannuale se i 6-K contengono relazioni.

    Cerca il documento di bilancio dei 6-K piu' recenti (stessa selezione della
    pipeline), ne deduce trimestrale/semestrale e salva una nuova versione del
    profilo. Nessuna relazione trovata: profilo invariato, esito dichiarato.
    """
    from bellomberg.market_data import filing_pipeline, sec_edgar
    allegati_fn = allegati_fn or sec_edgar.allegati_filing
    max_6k = max_6k or filing_pipeline.MAX_6K_SONDATI
    attuale = store.get_profile(ticker)
    if not attuale:
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "nessun profilo"}
    p = attuale["profile"]
    varianti = p.get("varianti") or []
    if (not p.get("cik") or len(varianti) != 1 or varianti[0].get("forme_sec") != ["20-F"]):
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "solo profili 20-F senza variante infrannuale"}
    catalogo = catalogo_fn(ticker, cik=p["cik"])
    soglia = (date.fromisoformat(oggi) if oggi else date.today()).toordinal() - filing_pipeline.GIORNI_6K
    sei_k = [d for d in catalogo.get("documenti", []) if d.get("form") == "6-K"
             and d.get("filed_date") and date.fromisoformat(d["filed_date"]).toordinal() >= soglia][:max_6k]
    errori = []
    for d in sei_k:
        try:
            documento = filing_pipeline._documento_risultati(allegati_fn(p["cik"], d["accession"]))
            if not documento:
                continue
            tipo = sonda_tipo_6k((scarica_fn or _testo_sec)(documento["url"]))
        except Exception as exc:  # un 6-K illeggibile non ferma la ricerca negli altri
            errori.append(f"{d.get('accession')}: {type(exc).__name__}: {exc}")
            continue
        if not tipo:
            continue
        nuovo = profilo_sec(ticker, cik=p["cik"], sec_ticker=p.get("sec_ticker"), nome=p.get("nome") or ticker,
                            origine=p.get("origine_collegamento", "manuale"), forme={"20-F", "6-K"}, tipo_6k=tipo,
                            regex_emittente=(p.get("verifica") or {}).get("emittente"))  # stessa prova d'identita'
        salvato = store.set_profile(ticker, nuovo, enabled=attuale["enabled"],
                                    interval_hours=attuale["interval_hours"],
                                    qualitative_enabled=attuale["qualitative_enabled"])
        return {"ticker": ticker, "esito": "aggiunto", "tipo": tipo, "profilo_versione": salvato["version"]}
    if errori:
        return {"ticker": ticker, "esito": "errore",
                "motivo": f"6-K non letti: {len(errori)} su {len(sei_k)}; " + "; ".join(errori[:3])}
    return {"ticker": ticker, "esito": "nessun_allegato",
            "motivo": f"nessuna relazione infrannuale nei {len(sei_k)} 6-K recenti"}
