/** Livello di rischio della dashboard: basso / medio / alto.
 *
 *  Con SPY disponibile si giudica la volatilità annua del portafoglio RISPETTO
 *  a SPY sugli stessi giorni (stessa unità, stesso periodo): sotto 0,85× basso,
 *  fino a 1,25× medio, oltre alto. Senza SPY si ripiega su soglie assolute
 *  dichiarate (12% e 20% annui). Dato mancante = null, mai un livello inventato. */
export type LivelloRischio = 'basso' | 'medio' | 'alto';

export const SOGLIE_RAPPORTO = { medio: 0.85, alto: 1.25 } as const;
export const SOGLIE_ASSOLUTE = { medio: 12, alto: 20 } as const;

export interface GiudizioRischio {
  livello: LivelloRischio | null;
  /** volatilità portafoglio / volatilità SPY; null senza SPY */
  rapporto: number | null;
  base: 'spy' | 'assoluta' | null;
}

const positivo = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0;

export function giudicaRischio(volPortafoglio: unknown, volSpy: unknown): GiudizioRischio {
  if (!positivo(volPortafoglio)) return { livello: null, rapporto: null, base: null };
  if (positivo(volSpy)) {
    const rapporto = volPortafoglio / volSpy;
    const livello = rapporto < SOGLIE_RAPPORTO.medio ? 'basso' : rapporto <= SOGLIE_RAPPORTO.alto ? 'medio' : 'alto';
    return { livello, rapporto, base: 'spy' };
  }
  const livello = volPortafoglio < SOGLIE_ASSOLUTE.medio ? 'basso' : volPortafoglio <= SOGLIE_ASSOLUTE.alto ? 'medio' : 'alto';
  return { livello, rapporto: null, base: 'assoluta' };
}
