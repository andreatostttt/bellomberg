/** Quota della liquidità sul patrimonio per la card Allocazione (puro, testabile senza React).
 *  Regola 14/07: cassa illeggibile = n.d. dichiarato, MAI peso 0 (che dava «100% investito»);
 *  cassa negativa (scoperto/margine) = peso negativo e investito oltre il 100%, mostrati come tali. */
export interface QuotaCassa {
  /** % del patrimonio in liquidità; null = non misurabile */
  pesoCassa: number | null;
  /** % del patrimonio investita; null = non misurabile */
  investito: number | null;
  negativa: boolean;
}

const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

export function quotaCassa(liquidita: number | null | undefined, patrimonio: number | null | undefined): QuotaCassa {
  if (!finito(patrimonio) || !(patrimonio > 0) || !finito(liquidita)) return { pesoCassa: null, investito: null, negativa: false };
  const pesoCassa = (liquidita / patrimonio) * 100;
  return { pesoCassa, investito: 100 - pesoCassa, negativa: liquidita < 0 };
}
