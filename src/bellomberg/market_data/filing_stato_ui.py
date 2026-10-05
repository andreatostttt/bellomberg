"""Stato canonico di un titolo per la pagina Filing (una sola regola, funzione pura).

Nessuna rete, nessun archivio: riceve la scheda del contesto (filing_context) e i
fatti gia' letti dalla route (run attivo, ultimo errore, esito dell'ultima
attivazione, proposta AI in sospeso, preferenze) e restituisce (stato, gruppo_ui).
"""
from bellomberg.core.language import text

GRUPPI_UI = {
    "novita": "novita",
    "errore": "da_sistemare", "da_confermare": "da_sistemare", "proposta_ai": "da_sistemare",
    "aggiornato": "aggiornati", "invariato": "aggiornati", "in_corso": "aggiornati", "primo_confronto": "aggiornati",
    "senza_fonte": "senza_fonte", "non_attivo": "senza_fonte", "escluso": "senza_fonte",
}


def _con_confronto(scheda):
    s = scheda or {}
    return s.get("run_id") is not None or bool(s.get("confronto_at"))


def _stato(scheda, profilo_attivo, run_attivo, ultimo_errore, esito, proposta_ai, escluso, scollegato, automatico):
    if escluso:
        return "escluso"
    if run_attivo:
        return "in_corso"
    if not profilo_attivo or scollegato:
        # Senza profilo utilizzabile (mai attivato, o collegamento annullato): conta cio' che
        # l'utente puo' fare, mai il confronto di un emittente rifiutato.
        if proposta_ai:
            return "proposta_ai"
        e = (esito or {}).get("esito")
        if e in ("da_confermare", "errore", "senza_fonte"):
            return e
        return "non_attivo"
    if (scheda or {}).get("gruppo") == 0:
        return "novita"
    if ultimo_errore:
        return "errore"
    if proposta_ai:
        return "proposta_ai"
    if _con_confronto(scheda):
        return "aggiornato" if (scheda or {}).get("gruppo") == 1 else "invariato"
    # controllo automatico spento e nessun confronto: il primo confronto non partira' da solo
    return "primo_confronto" if automatico else "non_attivo"


def stato_ui(*, scheda, profilo_attivo, run_attivo, ultimo_errore, esito, proposta_ai, escluso, scollegato,
             automatico=True):
    """(stato, gruppo_ui). Ordine delle regole: escluso, run attivo, senza profilo utilizzabile
    (proposta AI, esito dell'attivazione), novita', errore, proposta AI, confronto, primo confronto.

    `ultimo_errore`: errore successivo al confronto mostrato, o (senza confronto) l'ultimo run
    completo in errore; lo calcola chi chiama (filing_context.schede_filing). `automatico`: il
    profilo ha il controllo automatico acceso (con un confronto valido lo stato resta quello del
    confronto: il Consigliere lo usa comunque e ne dichiara la freschezza)."""
    stato = _stato(scheda, profilo_attivo, run_attivo, ultimo_errore, esito, proposta_ai, escluso, scollegato,
                   automatico)
    return stato, GRUPPI_UI[stato]


_TIPI = {"annuale": ("annuale", "annual"), "semestrale": ("semestrale", "half-year"),
         "trimestrale": ("trimestrale", "quarterly")}


def _tipo(valore):
    coppia = _TIPI.get(valore)
    return text(*coppia) if coppia else (str(valore) if valore else "")


def _fonte(v, profilo):
    fonti = v.get("fonti") or profilo.get("fonti") or []
    if "ir" in fonti:
        return "ir"
    if "sec" in fonti or v.get("forme_sec") or profilo.get("cik"):
        return "sec"
    if "esef" in fonti or profilo.get("esef_modo") or profilo.get("lei"):
        return "esef"
    return None


def documento(profilo):
    """Etichetta breve del documento seguito: «SEC 10-Q», «SEC 20-F + 6-K», «ESEF annuale»,
    «IR semestrale», «ESEF annuale + IR semestrale». None se non deducibile (profilo manuale)."""
    if not isinstance(profilo, dict):
        return None
    varianti = [v for v in profilo.get("varianti") or [] if isinstance(v, dict)] or [profilo]
    parti, forme = [], []
    for v in varianti:
        fonte = _fonte(v, profilo)
        tipo = v.get("tipo") or profilo.get("tipo")
        if fonte == "sec":
            nuove = [str(f) for f in (v.get("forme_sec") or profilo.get("forme_sec") or [])]
            if None not in parti:
                parti.append(None)  # segnaposto: le forme SEC si uniscono in una sola parte
            forme.extend(f for f in nuove if f not in forme)
        elif fonte == "esef":
            parti.append(("ESEF " + _tipo(tipo)).strip())
        elif fonte == "ir":
            parti.append(("IR " + _tipo(tipo)).strip())
    sec = ("SEC " + " + ".join(forme)) if forme else "SEC"
    parti = [sec if p is None else p for p in parti]
    parti = list(dict.fromkeys(parti))
    return " + ".join(parti) or None
