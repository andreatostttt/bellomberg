"""Numeri chiave della coppia confrontata dal filing.

Stessi periodi del confronto testuale, fonte XBRL ufficiale (SEC companyfacts,
us-gaap o ifrs-full). Deterministico: solo voci presenti in entrambi i periodi,
nessuna stima e nessuna somma di trimestri.
"""
from bellomberg.core.language import text
from bellomberg.market_data.sec_xbrl import CANONICAL

VOCI = (("ricavi", "revenue", "durata"), ("utile_operativo", "operating_income", "durata"),
        ("utile_netto", "net_income", "durata"), ("scorte", "inventory", "istante"),
        ("debito", "lt_debt", "istante"), ("flusso_cassa_operativo", "cfo", "durata"))


def _valore(facts, tags, periodo, modo):
    inizio, fine = periodo
    for tassonomia, nomi in (("us-gaap", tags[0]), ("ifrs-full", tags[1])):
        for nome in nomi:
            unita = facts.get("facts", {}).get(tassonomia, {}).get(nome, {}).get("units", {})
            for valuta, righe in unita.items():
                for r in righe:
                    if r.get("end") == fine and (modo == "istante" or r.get("start") == inizio):
                        return float(r["val"]), valuta, f"{tassonomia}:{nome}"
    return None, None, None


def _periodo_testo(periodo, modo):
    return periodo[1] if modo == "istante" else f"{periodo[0]}/{periodo[1]}"


def variazioni(facts, prima, dopo):
    """Variazioni delle voci chiave tra due periodi (inizio, fine) ISO.

    Revisione 04/10 (R7): ogni voce non confrontata finisce in `scarti` col motivo (assente in un
    periodo, valute diverse) e ogni voce porta valuta e tag XBRL usati; nessun taglio della lista.
    """
    voci, scarti = [], []
    for voce, chiave, modo in VOCI:
        a, va, ta = _valore(facts, CANONICAL[chiave], prima, modo)
        b, vb, tb = _valore(facts, CANONICAL[chiave], dopo, modo)
        if a is None or b is None:
            mancanti = [_periodo_testo(p, modo) for p, x in ((prima, a), (dopo, b)) if x is None]
            scarti.append({"voce": voce, "motivo": text("assente nel periodo " + " e nel periodo ".join(mancanti),
                                                        "missing in period " + " and in period ".join(mancanti))})
            continue
        if va != vb:
            scarti.append({"voce": voce, "motivo": text(f"valute diverse fra i periodi ({va} -> {vb}): non confrontabile",
                                                        f"different currencies between periods ({va} -> {vb}): "
                                                        "not comparable")})
            continue
        delta = round((b - a) / abs(a) * 100, 1) if a else None
        if delta is None:  # REV_G2a R-6: prima restava fra le voci e il contesto la toglieva zitto
            scarti.append({"voce": voce, "motivo": text(f"base zero nel periodo {_periodo_testo(prima, modo)}: "
                                                        "variazione non calcolabile",
                                                        f"zero base in period {_periodo_testo(prima, modo)}: "
                                                        "change not computable")})
        riga = {"voce": voce, "prima": a, "dopo": b, "delta_pct": delta, "valuta": va, "tag": tb}
        if ta != tb:
            riga["tag_prima"] = ta  # perimetro forse diverso: dichiarato
        voci.append(riga)
    valute = {v["valuta"] for v in voci}
    out = {"stato": "ok" if voci else "vuoto", "valuta": voci[0]["valuta"] if len(valute) == 1 else None,
           "voci": voci, "scarti": scarti}
    if len(valute) > 1:
        elenco = ", ".join(sorted(valute))
        out["avvisi"] = [text(f"voci in valute diverse: {elenco} (valuta per voce)",
                              f"items in different currencies: {elenco} (currency per item)")]
    return out


def numeri_per_coppia(cik, coppia):
    """Numeri chiave per la coppia di documenti scelta dalla pipeline."""
    from bellomberg.market_data import sec_xbrl
    cik = str(cik).zfill(10)

    def periodo(lato):
        return tuple(coppia[lato]["metadati"][k] for k in ("periodo_inizio", "periodo_fine"))

    fine = periodo("dopo")[1]
    facts = sec_xbrl._fetch_companyfacts(cik)
    nota = None
    if facts and not _ha_fine(facts, fine):
        # La cache (7 giorni) puo' precedere il deposito confrontato: una sola rilettura dalla rete.
        # Revisione 04/10: la cache e' condivisa col DCF, mai cancellata; la rilettura la
        # sostituisce solo se riesce.
        nuovi = sec_xbrl._fetch_companyfacts(cik, forza=True)
        if nuovi:
            facts = nuovi
            nota = "companyfacts riletto dalla SEC: la cache era anteriore al periodo confrontato"
        else:
            nota = "companyfacts riletto senza esito (rete o SEC): resta la cache precedente"
            # REV_G2a R-4: anche il DCF deve sapere che la cache precede un deposito pubblicato
            sec_xbrl.segna_cache_superata(cik, f"manca il periodo chiuso il {fine}, rilettura fallita")
    if not facts:
        return {"stato": "non_disponibile", "motivo": "companyfacts non disponibile", "voci": [],
                "fonte": "SEC companyfacts"}
    if not _ha_fine(facts, fine):
        return {"stato": "non_aggiornato", "voci": [], "fonte": "SEC companyfacts",
                "motivo": f"companyfacts non contiene ancora il periodo chiuso il {fine}"
                          + (f" ({nota})" if nota else "")}
    out = {**variazioni(facts, periodo("prima"), periodo("dopo")), "fonte": "SEC companyfacts"}
    if nota:
        out["nota_fonte"] = nota
    return out


def _ha_fine(facts, fine):
    for tassonomia in facts.get("facts", {}).values():
        for concetto in tassonomia.values():
            for righe in concetto.get("units", {}).values():
                if any(r.get("end") == fine for r in righe):
                    return True
    return False
