"""Versioned weekly prompts; read-only diagnostics from the sealed priming stage."""
from datetime import datetime
import json
import math
from bellomberg.storage.weekly_run_store import WeeklyRunBlocked

POLICY = 'weekly-evidence-prompts/1'
KEY = 'evidence_prompt_policy'

def enabled(blackboard):
    if getattr(blackboard, 'run_scope', None) != 'weekly':
        return False
    store = getattr(blackboard, 'weekly_store', None)
    if store is None:
        return False
    contract = store.context.get('contract', {})
    if KEY not in contract:
        return False
    if contract[KEY] != POLICY:
        raise WeeklyRunBlocked('Policy evidence prompt non compatibile')
    return True

def _replace_exact(text, old, new):
    if text.count(old) != 1:
        raise WeeklyRunBlocked('Evidence prompt template non compatibile')
    return text.replace(old, new, 1)

def select_template(blackboard, desk, template):
    if not enabled(blackboard):
        return template
    replacements = EVENT_REPLACEMENTS if desk == 'eventdesk' else RED_REPLACEMENTS if desk == 'red_team' else ()
    for old, new in replacements:
        template = _replace_exact(template, old, new)
    return template

def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)

def _date(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    except ValueError:
        return None

def _beta(value):
    from bellomberg.portfolio.advanced_metrics import testo_guardrail_beta_capo
    out = {'status': 'UNAVAILABLE', 'observed_at': None, 'verdict': 'UNAVAILABLE',
           'decision_eligible': False, 'diagnostic_text': testo_guardrail_beta_capo(None)}
    if not isinstance(value, dict):
        return out
    out['observed_at'] = _date(value.get('calcolato_il'))
    verdict = value.get('verdict')
    if value.get('error') or verdict not in ('RECONCILED', 'UNRELIABLE', 'INSUFFICIENT_SOURCES'):
        return out
    eligible = verdict == 'RECONCILED' and value.get('beta_per_decisioni') is True
    safe = {'verdict': verdict, 'beta_per_decisioni': eligible}
    betas = value.get('betas')
    if eligible:
        known = {'advanced_metrics_twr', 'portfolio_risk_spy', 'factor_model_mkt'}
        if (not isinstance(betas, dict) or len(betas) < 2 or not set(betas) <= known
                or not all(_finite(n) for n in betas.values())):
            return out
        safe['betas'] = dict(betas)
        consensus = value.get('beta_consensus')
        if consensus is not None:
            if not _finite(consensus):
                return out
            safe['beta_consensus'] = consensus
    elif verdict == 'INSUFFICIENT_SOURCES':
        if not isinstance(betas, dict):
            return out
        # Formatter uses only this observed count; no private source labels/values.
        safe['betas'] = dict.fromkeys(range(len(betas)))
    if verdict == 'UNRELIABLE':
        if not _finite(value.get('threshold')):
            return out
        safe['threshold'] = value['threshold']
    out.update(status='AVAILABLE', verdict=verdict, decision_eligible=eligible,
               diagnostic_text=testo_guardrail_beta_capo(safe))
    return out

def _freshness(value):
    from bellomberg.core.freshness import format_for_capo
    out = {'status': 'UNAVAILABLE', 'checked': None, 'fresh': None,
           'stale_count': None, 'unknown_count': None, 'diagnostic_text': 'Freshness n.d.: diagnostica assente o non valida.'}
    if not isinstance(value, dict) or value.get('error'):
        return out
    checked, fresh = value.get('checked'), value.get('fresh')
    stale, unknown = value.get('stale'), value.get('unknown')
    if (type(checked) is not int or type(fresh) is not int or checked < 0 or fresh < 0
            or not isinstance(stale, list) or not isinstance(unknown, list)
            or not all(isinstance(s, str) and s.strip() for s in stale + unknown)
            or checked != fresh + len(stale) + len(unknown)):
        return out
    text = format_for_capo({'stale': stale, 'unknown': unknown})
    if text is None:
        text = ('Zero controlli: freshness non attestata.' if checked == 0 else
                'Nessuna anomalia nel report salvato; esito limitato alle osservazioni controllate.')
    out.update(status='AVAILABLE', checked=checked, fresh=fresh, stale_count=len(stale),
               unknown_count=len(unknown), diagnostic_text=text)
    return out

def diagnostic_block(blackboard):
    if not enabled(blackboard):
        return ''
    # Integrity errors and missing prerequisites must never become best-effort prompts.
    priming = blackboard.weekly_store.get('priming')
    if not isinstance(priming, dict):
        raise WeeklyRunBlocked('Evidence prompt: priming persistito assente')
    payload = {'policy': POLICY, 'source_stage': 'priming'}
    for name, field, normalize in (('beta', 'beta_reconcile', _beta),
                                   ('freshness', 'freshness_report', _freshness)):
        try:
            payload[name] = normalize(priming.get(field))
        except Exception:
            payload[name] = {'status': 'UNAVAILABLE', 'diagnostic_text': 'CHECK_UNAVAILABLE: diagnostica n.d.'}
    return '\n\n=== DIAGNOSTICA SALVATA (disponibilita distinta da freshness e validita del metodo) ===\n' + json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)

EVENT_REPLACEMENTS = (('3. PROBABILITA\' DI MERCATO, NON OPINIONI: per ogni evento politico/regolatorio/geopolitico get_polymarket_events -> probabilita\' implicita SEMPRE col testo della domanda e la variazione vs 7 e 30 giorni [src: get_polymarket_events]. I sondaggi RITARDANO, i mercati ANTICIPANO. IL GAP E\' IL TRADE: dove headline/sentiment e prezzo del prediction market divergono c\'e\' l\'opportunita\' - articola sempre "le news dicono X, il mercato prezza Y%, io leggo il gap cosi\'".', "3. PROBABILITA' DI MERCATO, NON OPINIONI: get_polymarket_events -> riporta probabilita' implicita corrente con testo della domanda [src: get_polymarket_events]. Variazione 7/30 giorni SOLO se una fonte storica esplicita identifica lo stesso mercato e outcome, le due date e valori confrontabili. Senza quella storia scrivi delta 7/30 giorni n.d. e indica il dato mancante; prezzi correnti, volume o due eventi diversi non consentono quel calcolo. Articola il gap tra news e prezzo solo quando documentato, senza inventarlo."), ('- MAI dichiarare "mercato inesistente" (o "nessun numero disponibile") senza PROVA documentata: cita nel report la QUERY ESATTA passata a get_polymarket_events e il count restituito (es. \'query "<paese> <carica> election <anno>" -> count 0\', poi \'query "<candidato> <anno>" -> count 0\'), dopo aver riprovato con almeno 2 formulazioni alternative (titolo in inglese, paese + carica, nome del candidato): i nomi propri spesso stanno solo nelle question dei sub-market. Mercato davvero inesistente = dichiarato, mai probabilita\' inventate, e segnala che il rischio non e\' prezzabile direttamente.', "- Cita query esatta, count e limiti restituiti da get_polymarket_events. Dopo almeno 2 formulazioni alternative (titolo in inglese, paese + carica, nome del candidato), zero risultati significa nessun match nelle ricerche osservate, non mercato inesistente nell'universo. Dichiara paginazione/cap/copertura parziale o ignota. Una chiamata riuscita non attesta lettura o comprensione della fonte. Mai probabilita' inventate; rischio non prezzabile direttamente senza evidenza pertinente."), ('delta_7gg, scadenza', 'delta_7gg (n.d. senza storia documentata), scadenza'), ('Ogni catalyst della top 5 etichettato PRICED / PARTIALLY PRICED / NOT YET PRICED nei prediction market.', "Etichette PRICED / PARTIALLY PRICED / NOT YET PRICED solo con evidenza pertinente al medesimo evento/mercato/outcome; altrimenti PRICING NON VALUTABILE e motivo. Non dedurre NOT YET PRICED dal solo count=0 o da probabilita' assente; nessun gap inventato per riempire la voce high conviction. Sono giudizi motivati, non fatti verificati da una chiamata riuscita."))
RED_REPLACEMENTS = (('   - beta/VaR/vol citati coincidono con quelli ufficiali? Se due specialisti danno numeri\n     INCOMPATIBILI tra loro (es. beta 0,04 e 0,9 nello stesso giro), dillo col numero vero accanto.', "   - Confronta beta/VaR/vol con le misure disponibili e le rispettive fonti, convenzioni e diagnostiche salvate. In caso di valori incompatibili segnala divergenza e limiti; non designare un numero come vero se il guardrail e' indisponibile, insufficiente o non riconciliato. Non confondere freshness dell'osservazione, esito dell'acquisizione e validita' del metodo."),)
