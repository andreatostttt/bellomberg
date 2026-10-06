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
    dal_sito, nota_sito, trovato = [], None, None
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
            nota_sito = f"sito dell'emittente: {esef_sito.motivo_eccezione(exc, 120)}"
    if not con_json and not dal_sito:
        motivo_esef = ("nessun deposito ESEF con xBRL-JSON sul repository filings.xbrl.org"
                       + (f"; {nota_sito}" if nota_sito else ""))
        if trovato is not None:  # sito esplorato: relazioni in PDF dal sito (decisione PM 05/10)
            return _attiva_sito(store, ticker, nome or scelto.get("nome"), proposta, motivo_esef, trovato=trovato,
                                lei=scelto.get("lei"))
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": motivo_esef}
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


# ------------------------------------------------------------------ sito dell'emittente (V8B, 05/10)
# Decisione PM 05/10 (opzione B): un titolo senza documenti dall'archivio ufficiale (SEC/ESEF/OAM)
# prende le relazioni periodiche in PDF dal sito dell'emittente, per QUALUNQUE societa' (nessuna
# lista cablata). Profilo IR deterministico, senza AI, salvato SOLO se il PDF si verifica
# (emittente, periodo, tipo, lingua, perimetro); origine sempre dichiarata.
ORIGINE_COLLEGAMENTO_SITO = "sito_emittente"
# Regole del periodo in piu' per le relazioni dal sito (prova reale 06/10): provate DOPO quelle di
# ripiego di filing_proposta_ai, sempre misurate sul documento (_periodo_regge: periodo univoco e
# durata del tipo). Gruppi ammessi da filing_verifica._periodo_testuale.
_DATA_EN_SITO = r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\.?\s+[A-Za-zä]+\s+\d{4}"
_PERIODI_SITO = {
    "semestrale": [
        # «1/1–30/6/2026», «1.1.–30.6.2026»: estremi numerici con l'anno in comune
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
        # titolo da semestrale e, poco dopo, la data di chiusura («Half-Yearly … up to 30 June 2026»)
        rf"(?P<mesi>half)[-\s]*year(?:ly)?\b[\s\S]{{0,1500}}?\b(?:as\s+(?:of|at)|up\s+to|ended)\s+(?P<fine>{_DATA_EN_SITO})",
    ],
    "annuale": [
        rf"(?P<inizio>(?:January\s+1|1\.?\s+January),?\s+\d{{4}})\s*(?:to|until|through|–|-)\s*"
        rf"(?P<fine>(?:December\s+31|31\.?\s+December),?\s+\d{{4}})",
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
    ],
    "trimestrale": [
        r"(?P<giorno_inizio>\d{1,2})[./](?P<mese_inizio>\d{1,2})\.?\s*[–-]\s*(?P<giorno_fine>\d{1,2})[./]"
        r"(?P<mese_fine>\d{1,2})\s*[./]\s*(?P<anno>\d{4})",
    ],
}


def _periodo_sito(testo, tipo, url, altri):
    """Prima regola di _PERIODI_SITO che regge sul documento e sugli altri; se nessuna regge su
    tutti, la prima che regge sul documento (gli altri restano da verificare nel run, dichiarati)."""
    from bellomberg.market_data import filing_proposta_ai as fp
    validi = [rx for rx in _PERIODI_SITO.get(tipo, []) if fp._periodo_regge(testo, rx, tipo, url)]
    tutti = [rx for rx in validi if all(fp._periodo_regge(t, rx, tipo, u) for u, t in altri)]
    return (tutti or validi or [None])[0]


def _senza_fonte(ticker, proposta, motivo):
    return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta, "motivo": motivo}


MAX_TENTATIVI_SITO = 2  # relazioni scelte provate per titolo (ognuna: fino a 2 PDF da 40 MB)
# Revisione R-8 C1: parole che, subito DOPO il nome dell'emittente, indicano un'ALTRA entita' del
# gruppo (veicolo, fondo pensione, controllata con altra forma giuridica). Quelle gia' nel nome
# dell'emittente non contano («Zztest Holding AG»: «holding» e «ag» sono suoi).
_QUALIFICHE_ENTITA = (
    "finance", "financing", "funding", "capital", "treasury", "pension", "pensions", "pensionskasse",
    "insurance", "reinsurance", "leasing", "bank", "beteiligungs", "beteiligung", "holding", "holdings",
    "international", "services", "trust", "foundation", "stiftung",
    "bv", "b.v.", "gmbh", "ltd", "limited", "inc", "llc", "sarl", "s.a.r.l.", "srl", "s.r.l.", "ag", "se",
    "sa", "s.a.", "spa", "s.p.a.", "plc", "nv", "n.v.", "ab", "asa", "oyj", "kgaa", "kg")
_PAROLE_GRUPPO = ("group", "gruppo", "groupe", "grupo", "groep")


def _parole_del_nome(nome):
    import re
    return {w.replace(".", "") for w in re.findall(r"[a-z.]+", str(nome or "").lower())}


def _qualifiche_estranee(nome):
    import re
    proprie = _parole_del_nome(nome)
    return "|".join(re.escape(q) for q in _QUALIFICHE_ENTITA if q.replace(".", "") not in proprie)


def regex_soggetto(nome):
    """Prova dell'emittente come SOGGETTO: il nome non seguito da una qualifica di un'altra entita'
    («Zztest Group» si', «Zztest Group Finance BV» no). Usata anche nel profilo (run)."""
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    return _regex_nome(nome) + rf"(?![\s,.\-–]*(?:{_qualifiche_estranee(nome)})(?![a-z]))"


def regex_perimetro(nome):
    """Prova del perimetro consolidato che NON si regge sul nome dell'emittente (R-8 C1)."""
    proprie = _parole_del_nome(nome)
    gruppo = [w for w in _PAROLE_GRUPPO if w not in proprie]
    return "consolidat|konzern" + (rf"|\b(?:{'|'.join(gruppo)})\b" if gruppo else "")


def _altra_entita(testo, nome):
    """Il nome dell'entita' se la PRIMA citazione del nome in copertina e' un'altra entita' del
    gruppo, None se il documento e' dell'emittente."""
    import re
    from bellomberg.market_data.filing_profili_auto import _regex_nome
    testa = testo[:3000]
    m = re.search(_regex_nome(nome), testa, re.I)
    if not m:
        return None
    coda = re.match(rf"(?:[\s,.\-–]*(?:{_qualifiche_estranee(nome)})(?![a-z]))+", testa[m.end():], re.I)
    return " ".join(testa[m.start():m.end() + coda.end()].split()) if coda else None


def _codici_discordi(testo, lei=None, isin=None):
    """Motivo se il PDF dichiara LEI/ISIN e nessuno e' quello dell'emittente, None altrimenti."""
    import re
    for etichetta, atteso, forma in (("LEI", lei, r"[A-Z0-9]{18}[0-9]{2}"), ("ISIN", isin, r"[A-Z]{2}[A-Z0-9]{9}[0-9]")):
        if not atteso:
            continue
        trovati = set(re.findall(rf"\b{etichetta}\b\W{{0,12}}({forma})\b", testo[:200_000]))
        if trovati and str(atteso).upper() not in trovati:
            return (f"{etichetta} nel documento ({', '.join(sorted(trovati)[:3])}) diverso da quello dell'emittente "
                    f"({str(atteso).upper()}): documento di un altro soggetto")
    return None


def _scarica_pdf_sito(url, cartella, *, nav=None, dominio=None):
    """PDF del sito: robots.txt dell'host rispettato (5xx/irraggiungibile = vietato), solo il dominio
    dell'emittente, solo quell'host, IP pubblico, tetto 40 MB."""
    from urllib.parse import urlsplit
    from bellomberg.market_data import download_sicuro, esef_sito, lettore_trimestrali
    host = (urlsplit(url).hostname or "").lower()
    if dominio and not esef_sito.stesso_dominio(url, dominio):
        return {"stato": "errore", "motivo": f"PDF su un altro dominio ({host}): non scaricato"}
    nav = nav or esef_sito.Navigatore(esef_sito.dominio_registrabile(host) or host)
    if not nav.consentito(url):
        stato, ignoto = nav.bloccato.get(host), nav.robots_ignoto.get(host)
        return {"stato": "errore", "motivo": (f"{esef_sito.frase_blocco(stato)} su robots.txt" if stato
                                              else f"robots.txt non leggibile ({ignoto})" if ignoto
                                              else f"robots.txt vieta {urlsplit(url).path[:80]}")}
    return lettore_trimestrali.scarica_documento(url, str(cartella), host_consentiti={host}, public_only=True,
                                                 max_bytes=download_sicuro.MAX_PDF)


def profilo_dal_sito(ticker, *, nome, scelta, scarica_fn=None, dominio=None, lei=None, isin=None):
    """{"profilo", "periodo", "avvisi"} se il PDF scelto sul sito si verifica, altrimenti {"motivo"}.

    Regole di verifica tutte deterministiche: lingua dal testo, tipo dalle prove del codice,
    emittente come SOGGETTO del documento (mai una controllata col suo nome; LEI/ISIN se il PDF li
    dichiara), perimetro che non si regge sul nome, periodo dalle regole del codice (nessun
    modello). Senza regex di sezione: tutto il testo come una sezione (sezioni_intero, come i 6-K)."""
    import re
    import tempfile
    from bellomberg.market_data import esef_sito
    from bellomberg.market_data import filing_proposta_ai as fp
    from bellomberg.market_data.filing_profili_auto import _LINGUA_ESEF
    from bellomberg.market_data.filing_verifica import verifica_documento
    from bellomberg.market_data.lettore_trimestrali import estrai_testo
    ultimo, precedente = scelta["ultimo"], scelta.get("precedente")
    nav = None
    if scarica_fn is None:
        from urllib.parse import urlsplit
        nav = esef_sito.Navigatore(dominio or esef_sito.dominio_registrabile(urlsplit(ultimo["url"]).hostname))
    with tempfile.TemporaryDirectory() as cartella:
        scaricati, avvisi = {}, []
        for doc in (ultimo, precedente):
            if not doc:
                continue
            try:
                if scarica_fn is not None:
                    r = scarica_fn(doc["url"], cartella)
                else:
                    if scaricati:
                        nav._dormi(esef_sito.PAUSA_S)  # pausa anche fra un PDF e l'altro
                    r = _scarica_pdf_sito(doc["url"], cartella, nav=nav, dominio=dominio)
            except Exception as exc:
                r = {"stato": "errore", "motivo": esef_sito.motivo_eccezione(exc)}
            if r.get("stato") != "ok":
                if doc is ultimo:
                    return {"motivo": f"PDF non scaricato ({esef_sito._URL_NEL_TESTO.sub('<url>', str(r.get('motivo')))})"}
                avvisi.append(f"PDF dell'anno prima non scaricato ({r.get('motivo')}): solo il documento recente")
                continue
            scaricati[doc["url"]] = r["path"]
        try:
            lingua = fp.estrai_input(scaricati[ultimo["url"]])["lingua_rilevata"]
        except ValueError as exc:
            return {"motivo": f"PDF non utilizzabile: {exc}"}
        if not lingua or lingua not in _LINGUA_ESEF:
            return {"motivo": f"lingua del documento non determinata ({lingua or 'ignota'}): nessuna lingua di ripiego"}
        testi = {u: estrai_testo(p).get("testo") or "" for u, p in scaricati.items()}
        testo = testi[ultimo["url"]]
        # identita' (R-8 C1): il documento deve essere DELL'emittente, non di un'entita' col suo nome
        altra = _altra_entita(testo, nome)
        if altra:
            return {"motivo": f"documento di un'altra entita' del gruppo («{altra}»), non dell'emittente «{nome}»"}
        discordi = _codici_discordi(testo, lei, isin)
        if discordi:
            return {"motivo": discordi}
        for u in [u for u in testi if u != ultimo["url"]]:
            perche = _altra_entita(testi[u], nome) or _codici_discordi(testi[u], lei, isin)
            if perche:
                avvisi.append(f"PDF dell'anno prima scartato: documento di un altro soggetto ({perche})")
                del testi[u], scaricati[u]
        perimetro = regex_perimetro(nome)
        from bellomberg.market_data.filing_profili_auto import _regex_nome
        if not re.search(perimetro, re.sub(_regex_nome(nome), " ", testo[:200_000], flags=re.I), re.I):
            return {"motivo": "perimetro consolidato non dichiarato nel documento (il nome dell'emittente non "
                              "conta): bilancio della sola capogruppo o non riconosciuto, nessun profilo"}
        urls = [d["url"] for d in (ultimo, precedente) if d and d["url"] in scaricati]
        profilo = {"ticker": ticker, "emittente_id": f"EMITTENTE:{nome}", "nome": nome,
                   "origine_collegamento": ORIGINE_COLLEGAMENTO_SITO, "origine_documenti": esef_sito.ETICHETTA_SITO,
                   "fonti": ["ir"], "ir_urls": urls, "lingua": lingua, "tipo": ultimo["tipo"],
                   "perimetro": "consolidato",
                   "verifica": {"lingua": _LINGUA_ESEF[lingua], "perimetro": perimetro,
                                "tipo": fp._PROVA_TIPO[ultimo["tipo"]], "emittente": regex_soggetto(nome)},
                   "sezioni": {}, "sezioni_intero": True, "periodo_regola": "piu_recente"}
        altri = [(u, t) for u, t in testi.items() if u != ultimo["url"]]
        regole = fp._scegli_regole(testo, profilo, {}, ultimo["url"], altri)
        if not regole["periodo"]["regex"]:
            regole["periodo"]["regex"] = _periodo_sito(testo, ultimo["tipo"], ultimo["url"], altri)
        regole["emittente"]["regex"] = (profilo["verifica"]["emittente"]
                                        if fp._prova(profilo["verifica"]["emittente"], testo[:20_000]) else None)
        for campo in ("emittente", "tipo", "periodo"):
            if not regole[campo]["regex"]:
                return {"motivo": {"emittente": f"nome dell'emittente «{nome}» non trovato nel documento",
                                   "tipo": f"il documento non si dichiara relazione {ultimo['tipo']}",
                                   "periodo": "periodo non verificabile: nessuna regola del codice trova un periodo "
                                              "univoco del tipo"}[campo]}
            profilo["verifica"][campo] = regole[campo]["regex"]
        esito = verifica_documento(scaricati[ultimo["url"]], url=ultimo["url"], profilo=profilo)
        if esito.get("stato") != "ok":
            return {"motivo": "PDF non verificato: " + "; ".join(esito.get("motivi") or ["verifica non riuscita"])[:300]}
        meta = esito["documento"]["metadati"]
        if meta["periodo_fine"] != ultimo["periodo"]:
            # prova reale 06/10: la regola del codice aveva preso il comparativo dell'anno prima nel
            # testo della semestrale nuova. Discordanza = nessun profilo, motivo dichiarato.
            presunto = "presunta" in str(ultimo.get("base_periodo") or "")
            return {"motivo": (f"periodo ambiguo: il nome indica {ultimo['periodo']} (presunto), il testo "
                               f"{meta['periodo_fine']}: nessun profilo") if presunto else
                    (f"periodo trovato nel testo ({meta['periodo_fine']}) diverso da quello del nome e del "
                     f"titolo ({ultimo['periodo']}): possibile comparativo, nessun profilo")}
        profilo["periodo_sito"] = meta["periodo_fine"]
    return {"profilo": profilo, "periodo": meta["periodo_fine"], "avvisi": avvisi}


def _trovato_con_accesso(ticker, trovato, scopri_fn=None):
    """Voce del sito con l'esito d'accesso: le voci di cache scritte prima del 05/10 non lo hanno
    (R-8 C8) e si rileggono, mai lette come «nessun PDF»."""
    from bellomberg.market_data import esef_sito
    if trovato is not None and "accesso" in trovato:
        return trovato, None
    try:
        trovato = (scopri_fn or esef_sito.scopri)(ticker, forza=True)
    except Exception as exc:
        return None, f"sito dell'emittente: {esef_sito.motivo_eccezione(exc, 120)}"
    if "accesso" not in trovato:
        return None, ("sito dell'emittente: voce di cache senza esito d'accesso (scritta prima del 05/10), "
                      "riletta al prossimo giro")
    return trovato, None


def _prova_scelte(ticker, nome, trovato, *, dopo=None, lei=None, isin=None, scarica_fn=None):
    """Prima relazione del sito che si verifica, fino a MAX_TENTATIVI_SITO: una scelta che non regge
    (es. controllata col nome dell'emittente) si scarta col motivo e si prova la successiva.
    (esito di profilo_dal_sito | None, scelta, motivi degli scarti)."""
    from bellomberg.market_data import esef_sito
    dominio = esef_sito.dominio_sito(trovato.get("sito")) or "dominio-ignoto.invalid"
    pdf, motivi, prima = list(trovato.get("pdf") or []), [], None
    for _ in range(MAX_TENTATIVI_SITO):
        scelta = esef_sito.scegli_pdf(pdf, prime_pagine=trovato.get("prime_pagine"), dominio=dominio, dopo=dopo)
        if not scelta:
            break
        prima = prima or scelta
        esito = profilo_dal_sito(ticker, nome=nome, scelta=scelta, scarica_fn=scarica_fn, dominio=dominio,
                                 lei=lei, isin=isin)
        if "profilo" in esito:
            return esito, scelta, motivi
        motivi.append(f"relazione {scelta['tipo']} al {scelta['ultimo']['periodo']} dal {esef_sito.ETICHETTA_SITO}: "
                      f"{esito['motivo']}")
        via = {d["url"] for d in (scelta["ultimo"], scelta.get("precedente")) if d}
        pdf = [v for v in pdf if v.get("url") not in via]
    return None, prima, motivi


def _attiva_sito(store, ticker, nome, proposta, motivo_base, *, trovato=None, scopri_fn=None, scarica_fn=None,
                 lei=None, isin=None):
    """Ultimo passo prima di «senza fonte»: relazioni in PDF dal sito dell'emittente. Blocchi del
    sito (HTTP 403, robots.txt), PDF non ammessi o non verificati restano DICHIARATI nel motivo."""
    from bellomberg.market_data import esef_sito
    if trovato is None and esef_sito.consigliere_in_corso():
        return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente non esplorato: run del "
                                              "Consigliere in corso (si riprova al controllo giornaliero)")
    if trovato is None:
        try:
            trovato = (scopri_fn or esef_sito.scopri)(ticker)
        except Exception as exc:
            return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente: "
                                                  f"{esef_sito.motivo_eccezione(exc, 120)}")
    trovato, perche = _trovato_con_accesso(ticker, trovato, scopri_fn)
    if perche:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; {perche}")
    accesso = trovato.get("accesso") or {}
    if accesso.get("stato") != "ok":
        return _senza_fonte(ticker, proposta, f"{motivo_base}; sito dell'emittente: {accesso.get('motivo')}")
    dominio = esef_sito.dominio_sito(trovato.get("sito"))
    if not esef_sito.scegli_pdf(trovato.get("pdf"), prime_pagine=trovato.get("prime_pagine"), dominio=dominio):
        perche = (esef_sito.riepilogo_scarti(trovato["pdf"], prime_pagine=trovato.get("prime_pagine"), dominio=dominio)
                  if trovato.get("pdf") else "nessun PDF di relazioni periodiche sul sito dell'emittente")
        return _senza_fonte(ticker, proposta, f"{motivo_base}; {perche}")
    if not nome:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; PDF trovati sul sito dell'emittente ma nome "
                                              "dell'emittente non noto: prova d'identita' impossibile, nessun profilo")
    isin = isin or (proposta or {}).get("isin")
    esito, _, scarti = _prova_scelte(ticker, nome, trovato, lei=lei, isin=isin, scarica_fn=scarica_fn)
    if esito is None:
        return _senza_fonte(ticker, proposta, f"{motivo_base}; " + "; ".join(scarti))
    salvato = store.set_profile(ticker, esito["profilo"], enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    return {"ticker": ticker, "esito": "attivato", "fonte": ORIGINE_COLLEGAMENTO_SITO,
            "origine": esef_sito.ETICHETTA_SITO, "profilo_versione": salvato["version"], "proposta": proposta,
            "documenti": esito["profilo"]["ir_urls"], "avvisi": esito["avvisi"] + scarti,
            "motivo": (f"relazione {esito['profilo']['tipo']} al {esito['periodo']} dal {esef_sito.ETICHETTA_SITO} "
                       f"(verificata sul testo); {motivo_base}"
                       + (f"; scartate prima: {'; '.join(scarti)}" if scarti else ""))}


def aggiorna_dal_sito(store, ticker, *, scopri_fn=None, scarica_fn=None):
    """Controllo giornaliero dei profili «sito dell'emittente»: se sul sito c'e' una relazione con
    periodo PIU' RECENTE di quella del profilo (es. la semestrale dopo l'annuale), nuova versione del
    profilo, verificata come all'attivazione. Mai verso un periodo piu' vecchio (R-8 C3); un documento
    sparito dal sito resta quello archiviato, dichiarato. Esito: invariato | aggiornato | errore."""
    from bellomberg.market_data import esef_sito
    riga = store.get_profile(ticker)
    profilo = (riga or {}).get("profile") or {}
    if profilo.get("origine_collegamento") != ORIGINE_COLLEGAMENTO_SITO:
        return {"ticker": ticker, "esito": "non_applicabile", "motivo": "profilo non creato dal sito dell'emittente"}
    trovato, perche = _trovato_con_accesso(ticker, (scopri_fn or esef_sito.scopri)(ticker), scopri_fn)
    if perche:
        return {"ticker": ticker, "esito": "errore", "motivo": perche}
    accesso = trovato.get("accesso") or {}
    if accesso.get("stato") != "ok":
        return {"ticker": ticker, "esito": "errore", "motivo": f"sito dell'emittente: {accesso.get('motivo')}"}
    attuale = profilo.get("periodo_sito")
    if not attuale:
        doc = esef_sito.classifica_pdf({"url": (profilo.get("ir_urls") or [""])[0], "testo": ""})
        attuale = doc.get("periodo")
    if not attuale:
        return {"ticker": ticker, "esito": "errore",
                "motivo": "periodo del documento del profilo non noto: nessun confronto, profilo invariato"}
    esito, scelta, scarti = _prova_scelte(ticker, profilo.get("nome"), trovato, dopo=attuale, scarica_fn=scarica_fn)
    if esito is None and scelta is None:
        sul_sito = {v.get("url") for v in trovato.get("pdf") or []}
        if (profilo.get("ir_urls") or [None])[0] not in sul_sito:
            return {"ticker": ticker, "esito": "invariato",
                    "motivo": f"il documento del profilo ({attuale}) non e' piu' sul sito: resta quello archiviato; "
                              "nessuna relazione piu' recente"}
        return {"ticker": ticker, "esito": "invariato", "motivo": f"nessuna relazione piu' recente di {attuale} sul sito"}
    if esito is None:
        return {"ticker": ticker, "esito": "errore", "motivo": "nuova relazione non verificata: " + "; ".join(scarti)}
    salvato = store.set_profile(ticker, esito["profilo"], enabled=riga["enabled"],
                                interval_hours=riga["interval_hours"], qualitative_enabled=riga["qualitative_enabled"])
    return {"ticker": ticker, "esito": "aggiornato", "profilo_versione": salvato["version"],
            "motivo": f"relazione {esito['profilo']['tipo']} al {esito['periodo']} dal {esef_sito.ETICHETTA_SITO}"
                      + (f"; scartate prima: {'; '.join(scarti)}" if scarti else "")}


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
    if sec["stato"] == "non_configurata" and (cik is not None or "esef" not in proposta):
        # CIK scelto o titolo USA (ESEF mai interrogato): senza contatto non c'e' nulla da provare.
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
        motivo_esef = esef_p["motivo"] + (f" ({sec['motivo']}: SEC non consultata)"
                                          if sec["stato"] == "non_configurata" else "")
        return _attiva_esef(store, ticker, c, c["origine"], proposta.get("nome"), proposta, motivo_esef, indice_fn)
    elif esef_p["stato"] == "ambiguo":
        return {"ticker": ticker, "esito": "da_confermare", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif sec["stato"] == "non_configurata":
        # SEC mai consultata: «senza fonte» sarebbe falso; e' un errore di configurazione dichiarato.
        return {"ticker": ticker, "esito": "errore", "proposta": proposta,
                "motivo": f"{sec['motivo']}; ESEF: {esef_p['motivo']}"}
    elif esef_p["stato"] == "errore":
        return {"ticker": ticker, "esito": "errore", "proposta": proposta, "motivo": "ESEF: " + esef_p["motivo"]}
    elif "." not in ticker:  # titolo USA: la fonte ufficiale e' la SEC, mai il sito come ripiego
        return {"ticker": ticker, "esito": "senza_fonte", "proposta": proposta,
                "motivo": f"SEC: {sec['motivo']}; ESEF: {esef_p['motivo']}"}
    else:
        return _attiva_sito(store, ticker, proposta.get("nome"), proposta,
                            f"SEC: {sec['motivo']}; ESEF: {esef_p['motivo']}")
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
        if esef_p["stato"] != "ambiguo":
            return _attiva_sito(store, ticker, proposta.get("nome"), {**proposta, "esef": esef_p},
                                f"SEC: {exc}; ESEF: {esef_p['motivo']}")
        return {"ticker": ticker, "esito": "da_confermare", "proposta": {**proposta, "esef": esef_p},
                "motivo": f"SEC: {exc}; ESEF: {esef_p['motivo']}"}
    salvato = store.set_profile(ticker, profilo, enabled=True, interval_hours=INTERVALLO_AUTO_ORE)
    return {"ticker": ticker, "esito": "attivato", "profilo_versione": salvato["version"], "proposta": proposta,
            "motivo": sec["motivo"], "forme": sorted(forme)}


def attiva_mancanti(store, tickers, **kw):
    """Attivazione in blocco dei titoli senza profilo: riepilogo per esito.

    Le liste restano di ticker (chiamanti e frontend invariati); `motivi` {ticker: motivo} porta
    il motivo di OGNI esito non in errore, cosi' le preferenze lo salvano (prima: null).
    `avviso_configurazione`: la frase SEC_NON_CONFIGURATA se manca il contatto SEC (l'ESEF non lo esige), misurato
    sulla causa (variabile d'ambiente), non dedotto dagli errori; altrimenti None. (Opus 5.5, 05/10)"""
    from bellomberg.market_data.sec_edgar import _contatto
    out = {k: [] for k in ("attivati", "da_confermare", "senza_fonte", "esclusi", "gia_attivi", "scollegati",
                           "errori")}
    out["motivi"] = {}
    out["avviso_configurazione"] = None if _contatto() else filing_identita.SEC_NON_CONFIGURATA
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
            out["motivi"][ticker] = r.get("motivo")
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
