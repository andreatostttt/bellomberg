"""Classificatore PURO del guasto «provabilmente non fatturato» (KA, 04/10, Opus 5.5).

Stessa regola di Trade Idea (`agents/trade_idea._provably_unbilled`, «TI-RITENTATIVO-
NON-FATTURATO»), portata nel percorso del weekly per DECISIONE PM 04/10: UN solo nuovo
tentativo quando la chiamata non ha mai raggiunto un modello, cioe' SOLO
  (a) connessione mai stabilita (`transport_phase == "connect"`, impostato da
      `llm_client._invia` su httpx.ConnectError / ConnectTimeout: nessun byte inviato);
  (b) rifiuto di AMMISSIONE documentato di OpenRouter (HTTP 402
      `in_flight_budget_exhausted`, `limit_source` openrouter_in_flight_budget,
      `provider_name` null), validato da `preprovider_receipt._failure_metadata`.
Tutto il resto (timeout dopo l'invio, 5xx, 429, stream interrotto, qualsiasi risposta
parziale) PUO' essere stato fatturato: resta incerto e blocca, come prima.

Due regole piu' strette della versione TI originale (review RV-KA, decisione main 05/10),
ora UNICHE: dal 05/10 `trade_idea._provably_unbilled` importa questa funzione.
  - un errore con `generation_id` del provider NON e' mai non fatturato;
  - il 402 vale solo con lo status HTTP misurato (`http_status`, da `_invia`) pari a 402.
Il tetto «un solo nuovo tentativo» vale PER SINGOLA CHIAMATA del client: un chiamante che
ripete la chiamata ottiene un nuovo giro; ogni riga 'released' porta comunque la sua prova.

Nessun I/O, nessuna scrittura: decide soltanto.
"""
from __future__ import annotations


# Attesa prima del nuovo tentativo dopo una connessione mai stabilita (come TI).
ATTESA_CONNESSIONE_S = 5
# Attesa dopo un 402 di ammissione senza Retry-After, e tetto al Retry-After (come TI).
ATTESA_AMMISSIONE_S = 30
TETTO_RETRY_AFTER_S = 120


def provably_unbilled(exc):
    """Secondi da attendere prima di UN nuovo tentativo se il guasto non ha mai raggiunto
    un modello, altrimenti None (incerto: possibile addebito)."""
    if getattr(exc, "partial_response", None):
        return None  # qualcosa e' stato generato: forse fatturato
    if getattr(exc, "generation_id", None):
        # RV-KA P2 (decisione main 05/10): il provider ha aperto una generazione, quindi
        # non e' «certamente non fatturata». Resta 'unknown' e si riconcilia col lookup
        # gratuito della generazione (cost_reconciliation). DIVERGE da Trade Idea.
        return None
    if getattr(exc, "transport_phase", None) == "connect":
        return ATTESA_CONNESSIONE_S
    # RV-KA P3.1 (decisione main 05/10): il 402 di ammissione vale solo se lo status HTTP
    # MISURATO da _invia era 402 E il code del corpo corrisponde. Un corpo «code 402» dentro
    # un 502, o un errore senza status misurato, non prova nulla. DIVERGE da Trade Idea.
    if getattr(exc, "http_status", None) != 402:
        return None
    try:
        from bellomberg.core.preprovider_receipt import _failure_metadata
        metadata = _failure_metadata({"exception_type": type(exc).__name__,
                                      "message": type(exc).__name__ + ": " + str(exc)})
    except Exception:
        return None
    retry_after = (metadata.get("headers") or {}).get("Retry-After")
    return min(int(retry_after), TETTO_RETRY_AFTER_S) if retry_after else ATTESA_AMMISSIONE_S


def unbilled_reason(exc):
    """Motivo breve e leggibile del rilascio (per journal e memo), None se non e' provato."""
    if provably_unbilled(exc) is None:
        return None
    if getattr(exc, "transport_phase", None) == "connect":
        return "connessione mai stabilita (" + type(exc).__name__ + ")"
    return "402 di ammissione OpenRouter (in_flight_budget_exhausted)"
