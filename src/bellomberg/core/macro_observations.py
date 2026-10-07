"""Coppie annuali di livelli macro: pura matematica, nessun fetch o cache."""
from datetime import date
import math


def annual_comparison(observations, frequency, rejected=None):
    if frequency not in ('monthly', 'quarterly'):
        raise ValueError('frequenza macro non supportata')
    label = 'mese' if frequency == 'monthly' else 'trimestre'
    def period(day):
        return (day.year, day.month if frequency == 'monthly' else (day.month - 1) // 3 + 1)

    groups, invalid = {}, []
    for obs in observations:
        try:
            day = date.fromisoformat(obs['date'])
        except (KeyError, TypeError, ValueError):
            invalid.append({'date': None, 'reason': 'data non valida'})
            continue
        try:
            raw = obs['value']
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError):
            invalid.append({'date': day.isoformat(), 'reason': 'valore non positivo o non finito'})
            continue
        groups.setdefault(period(day), []).append({'date': day.isoformat(), 'value': value})

    quality = {'metadata_status': 'unknown' if rejected is None else 'available',
               'rejected_observations': rejected, 'invalid_observations': invalid,
               'duplicate_periods': [list(key) for key in sorted(groups) if len(groups[key]) > 1],
               'latest_status': 'unavailable'}
    comparison = {'status': 'unavailable', 'frequency': frequency,
                  'transformation': 'same_period_previous_year', 'reason': None}
    out = {'quality': quality, 'comparison': comparison, 'yoy_pct': None,
           'observations': sorted((o for rows in groups.values() for o in rows), key=lambda o: o['date'])}
    def fail(reason):
        comparison['reason'] = reason
        return out
    if not groups:
        return fail('osservazioni macro valide assenti')
    latest = max(groups)
    if len(groups[latest]) != 1:
        return fail(label + ' corrente duplicato: vintage ambiguo')
    current = groups[latest][0]
    out.update(last_available_date=current['date'], last_available_value=current['value'])
    discarded = invalid + (rejected if isinstance(rejected, list) else [])
    if rejected is not None and not isinstance(rejected, list):
        return fail('metadati scarti non validi')
    for item in discarded:
        try:
            discarded_period = period(date.fromisoformat(item['date']))
        except (KeyError, TypeError, ValueError):
            return fail('scarto senza data valida: ultimo corrente non verificabile')
        if discarded_period >= latest:
            return fail('osservazione corrente o successiva scartata')
    quality['latest_status'] = 'available'
    quality['warning'] = 'scarti intermedi presenti' if discarded else ('metadati scarti n.d.' if rejected is None else None)
    # Il confronto annuale puo' essere univoco anche con un precedente ambiguo.
    earlier = [key for key in groups if key < latest]
    quality['previous_status'] = 'unavailable'
    quality['previous_reason'] = 'periodo precedente assente'
    if earlier:
        ambiguous = len(groups[max(earlier)]) > 1
        quality['previous_status'] = 'ambiguous' if ambiguous else 'available'
        quality['previous_reason'] = 'periodo precedente duplicato: vintage ambiguo' if ambiguous else None
        # Una release scartata non rende quello piu' vecchio il precedente verificato.
        if any(period(date.fromisoformat(item['date'])) >= max(earlier) for item in discarded):
            quality['previous_status'] = 'unavailable'
            quality['previous_reason'] = 'periodo precedente o successivo scartato'
    out.update(latest_date=current['date'], latest_value=current['value'])
    previous = (latest[0] - 1, latest[1])
    comparison.update(current_period=list(latest), base_period=list(previous))
    if previous not in groups:
        return fail("osservazione dello stesso " + label + " dell'anno precedente assente")
    if len(groups[previous]) != 1 or any(period(date.fromisoformat(o['date'])) == previous for o in discarded):
        return fail(label + ' base ambiguo o scartato')
    base = groups[previous][0]
    yoy = (current['value'] / base['value'] - 1.0) * 100.0
    if not math.isfinite(yoy):
        return fail('rapporto macro non finito')
    comparison.update(status='available', current_date=current['date'], base_date=base['date'],
                      current_value=current['value'], base_value=base['value'])
    out.update(yoy_pct=yoy, year_ago_date=base['date'], year_ago_value=base['value'])
    return out
