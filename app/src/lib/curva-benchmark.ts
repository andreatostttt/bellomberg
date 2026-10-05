/** SPY sulla curva della quota: la stessa base del primo giorno in comune.
 *
 *  La serie benchmark del backend e' total-return EUR sul calendario TWR. Qui
 *  la si porta sulle date DISEGNATE (riporto dell'ultimo valore noto, mai
 *  un'interpolazione) e la si riscala perche' parta dal valore quota del primo
 *  giorno in cui esistono entrambe: da li' le due linee sono confrontabili. */
export interface CurvaBenchmark {
  /** allineata punto a punto alle date della curva; NaN prima del primo giorno comune */
  valori: number[];
  /** indice del primo punto comune */
  da: number;
  /** variazione % di SPY e della quota dal primo punto comune all'ultimo */
  variazionePct: number;
  variazioneQuotaPct: number;
}

export function allineaBenchmark(date: string[], quota: number[], benchDate: string[] | undefined, benchIndice: number[] | undefined): CurvaBenchmark | null {
  if (!benchDate?.length || !benchIndice || benchIndice.length !== benchDate.length || date.length !== quota.length) return null;
  const grezzi: number[] = [];
  let j = -1;
  for (const d of date) {
    while (j + 1 < benchDate.length && benchDate[j + 1] <= d) j++;
    const v = j >= 0 ? benchIndice[j] : NaN;
    grezzi.push(Number.isFinite(v) && v > 0 ? v : NaN);
  }
  const da = grezzi.findIndex((v, i) => Number.isFinite(v) && Number.isFinite(quota[i]) && quota[i] > 0);
  if (da < 0 || da >= date.length - 1) return null;
  const scala = quota[da] / grezzi[da];
  const valori = grezzi.map((v, i) => i < da ? NaN : v * scala);
  const ultimo = valori[valori.length - 1];
  if (!Number.isFinite(ultimo)) return null;
  return {
    valori,
    da,
    variazionePct: (ultimo / valori[da] - 1) * 100,
    variazioneQuotaPct: (quota[quota.length - 1] / quota[da] - 1) * 100,
  };
}
