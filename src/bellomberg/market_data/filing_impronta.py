"""Impronta dei depositi SEC: elenco leggero per sapere se c'e' un deposito nuovo.

Una sola lettura del catalogo; per ogni variante l'accession del deposito
candidato piu' recente, con le stesse regole di selezione della pipeline. Nei
profili automatici un 6-K conta solo se contiene il documento di bilancio.
Nessun download dei documenti, nessun confronto, nessun LLM. Profili ESEF a blocchi
(fase B): gli id dei depositi che la pipeline considererebbe (stessa lingua, un
caricamento per esercizio, ultimi esercizi), dall'indice con cache su disco.
"""
from datetime import date

_ANNUALI = ("10-K", "20-F", "40-F")


def _variante(profilo, righe, rif, allegati_fn, soglia_6k):
    """{"relazione": accession | None, "esaminato_fino_a": accession | None} della variante.

    `relazione`: il candidato piu' recente (per i 6-K, il piu' recente con documento di
    bilancio). `esaminato_fino_a`: il deposito piu' recente gia' considerato, cosi' il
    controllo successivo sonda solo i 6-K arrivati dopo. Righe dal piu' recente.
    """
    from bellomberg.market_data import filing_pipeline
    forme = profilo.get("forme_sec")
    annuale = profilo["tipo"] == "annuale"
    rif = rif if isinstance(rif, dict) else None  # forma precedente: nessun riferimento
    fermate = {(rif or {}).get("relazione"), (rif or {}).get("esaminato_fino_a")} - {None}
    esaminato, sondati = None, 0
    for row in righe:
        form = str(row.get("form") or "")
        base = form.removesuffix("/A")
        if forme is not None and (base not in forme or form == "6-K/A"):
            continue
        if base in _ANNUALI and not annuale or base == "10-Q" and annuale:
            continue
        if forme is None and base not in (_ANNUALI if annuale else ("10-Q",)) + ("6-K",):
            continue
        acc = row.get("accession")
        if acc in fermate:  # niente di piu' nuovo oltre quanto gia' esaminato
            return {"relazione": rif["relazione"], "esaminato_fino_a": esaminato or rif.get("esaminato_fino_a") or acc}
        if base == "6-K" and forme is not None and not form.endswith("/A"):
            depositato = filing_pipeline._data(row.get("filed_date"))
            if not depositato or depositato.toordinal() < soglia_6k:
                continue
            esaminato = esaminato or acc
            if sondati >= filing_pipeline.MAX_6K_SONDATI:
                if fermate:
                    raise ValueError(f"oltre {filing_pipeline.MAX_6K_SONDATI} 6-K nuovi da sondare")
                return {"relazione": None, "esaminato_fino_a": esaminato}  # come la pipeline
            sondati += 1
            try:
                documento = filing_pipeline._documento_risultati(allegati_fn(profilo["cik"], acc))
            except Exception as exc:
                raise ValueError(f"6-K {acc}: indice del filing non letto: {type(exc).__name__}: {exc}") from exc
            if not documento:
                continue  # avviso o comunicato: non e' un deposito nuovo
        return {"relazione": acc, "esaminato_fino_a": esaminato or acc}
    return {"relazione": None, "esaminato_fino_a": esaminato}


def relazioni(impronta):
    """{variante: relazione} di un'impronta, o None se assente o in forma non riconosciuta."""
    if not isinstance(impronta, dict) or impronta.get("fonte") not in ("sec", "esef", "sito") \
            or not isinstance(impronta.get("varianti"), dict) or impronta.get("non_confrontabile"):
        return None  # «sito» non confrontabile (HEAD vietata o senza firma): mai gli stessi depositi
    if any(not isinstance(v, dict) for v in impronta["varianti"].values()):
        return None
    return {k: v.get("relazione") for k, v in impronta["varianti"].items()}


def stessi_depositi(a, b):
    """Stesse relazioni per le stesse varianti; i 6-K esaminati senza relazione non contano."""
    ra = relazioni(a)
    return ra is not None and ra == relazioni(b) and a["fonte"] == b["fonte"]


def _impronta_esef(profilo, indice_fn, oggi):
    """Ids dei candidati ESEF della pipeline; indice non aggiornato: ValueError (run completo)."""
    from bellomberg.market_data.filing_esef import righe_catalogo
    lei = profilo.get("lei") or str(profilo["emittente_id"]).split(":", 1)[1]
    catalogo = righe_catalogo(lei, "en", oggi=oggi, indice_fn=indice_fn)
    if catalogo["origine"] == "cache_scaduta":
        raise ValueError("indice ESEF non aggiornato: " + "; ".join(catalogo["motivi"])[:200])
    ids = [str(r.get("id")) for r in catalogo["righe"]]
    # Anche l'ultimo esercizio sul repository (depositi senza JSON compresi) e lo stato «fermo»:
    # cambiano senza depositi nuovi utilizzabili, e il run completo deve dichiararli.
    relazione = ",".join(ids) + f"|ultimo={catalogo['ultimo_periodo']}" + ("|fermo" if catalogo["fermo"] else "")
    return {"fonte": "esef", "varianti": {"annuale": {"relazione": relazione if ids else None,
                                                       "esaminato_fino_a": ids[0] if ids else None}}}


def impronta_depositi(profilo, *, riferimento=None, catalogo_fn=None, allegati_fn=None, oggi=None, indice_fn=None):
    """{"fonte": "sec" | "esef", "varianti": {tipo: {"relazione", "esaminato_fino_a"}}} o None.

    Profili ESEF a blocchi: impronta dall'indice dei depositi (`indice_fn`). None per gli
    altri profili senza `cik` o con fonti diverse dalla sola SEC (pagine IR e profili
    ESEF manuali non sono in un elenco). `riferimento`: impronta piu' recente registrata (run
    completo o controllo leggero), limita le sonde 6-K ai depositi piu' nuovi. Catalogo o indice 6-K illeggibili:
    ValueError (il chiamante fa il run completo).
    """
    if isinstance(profilo, dict) and profilo.get("origine_collegamento") == "sito_emittente":
        # V8B/R-8 C4: PDF dal sito dell'emittente: HEAD con robots.txt (esef_sito.impronta_sito); se
        # non confrontabile, l'impronta porta il motivo e il run e' completo (mai un controllo zitto)
        from bellomberg.market_data.esef_sito import impronta_sito
        return impronta_sito(profilo)
    oggi_d = date.fromisoformat(oggi) if isinstance(oggi, str) else (oggi or date.today())
    if isinstance(profilo, dict) and profilo.get("esef_modo") == "blocchi" and profilo.get("fonti") == ["esef"]:
        if any(isinstance(v, dict) and v.get("fonti", ["esef"]) != ["esef"] for v in profilo.get("varianti") or []):
            return None  # variante IR (PDF): nessun elenco leggero, run completo
        return _impronta_esef(profilo, indice_fn, oggi_d)
    if not isinstance(profilo, dict) or not profilo.get("cik") or profilo.get("fonti") != ["sec"]:
        return None
    from bellomberg.market_data import filing_pipeline, sec_edgar
    from bellomberg.storage.filing_store import unisci_variante
    catalogo_fn = catalogo_fn or sec_edgar.get_filing_catalog
    allegati_fn = allegati_fn or sec_edgar.allegati_filing
    catalogo = catalogo_fn(profilo["ticker"], cik=profilo["cik"])
    righe = catalogo.get("documenti") if isinstance(catalogo, dict) else None
    if not isinstance(righe, list) or catalogo.get("stato") not in ("ok", "parziale"):
        motivi = "; ".join(str(m) for m in (catalogo or {}).get("motivi", [])[:3]) if isinstance(catalogo, dict) else ""
        raise ValueError("catalogo SEC non disponibile" + (f": {motivi}" if motivi else ""))
    righe = sorted(righe, key=lambda r: (str(r.get("filed_date") or ""), str(r.get("accession") or "")), reverse=True)
    rif = (riferimento or {}).get("varianti") if (riferimento or {}).get("fonte") == "sec" else None
    oggi = date.fromisoformat(oggi) if isinstance(oggi, str) else (oggi or date.today())
    soglia_6k = oggi.toordinal() - filing_pipeline.GIORNI_6K
    varianti = [unisci_variante(profilo, v) for v in profilo["varianti"]] if profilo.get("varianti") else [profilo]
    return {"fonte": "sec", "varianti": {
        v["tipo"]: _variante(v, righe, (rif or {}).get(v["tipo"]), allegati_fn, soglia_6k) for v in varianti}}
