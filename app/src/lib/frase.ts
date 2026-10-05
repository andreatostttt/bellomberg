/** Da MAIUSCOLO TERMINALE a frase con l'iniziale maiuscola, per la Nuova.
 *
 *  Le stringhe di cruscotto nascono maiuscole perche' la Classica le mostra
 *  cosi'; la Nuova le legge in minuscolo senza duplicare il dizionario. Si
 *  tocca SOLO un testo senza minuscole (un testo misto e' gia' scritto bene),
 *  e restano maiuscoli sigle, ticker e tutto cio' che contiene cifre. */
const SIGLE = new Set([
  'TWR', 'GIPS', 'NAV', 'SPY', 'EUR', 'USD', 'GBP', 'CHF', 'JPY', 'FX', 'P&L', 'P/L', 'VAR', 'ITD',
  'MKT', 'ETF', 'AI', 'DD', 'PM', 'ISIN', 'TV', 'SMA', 'RSI', 'VWAP', 'LIVE', 'STALE', 'BREACH', 'ID',
]);

const restaMaiuscola = (parola: string) => {
  const nuda = parola.replace(/^[^\p{L}\d&/]+|[^\p{L}\d&/.]+$/gu, '').replace(/\.$/, '');
  return SIGLE.has(nuda) || /\d/.test(nuda) || /^[\p{Lu}\d]+\.[\p{Lu}]{1,4}$/u.test(nuda);
};

export function frase(testo: string): string {
  if (!testo || /\p{Ll}/u.test(testo)) return testo;
  let primaFatta = false;
  return testo.split(/(\s+)/).map(pezzo => {
    if (!/\p{L}/u.test(pezzo)) { if (/\d/.test(pezzo)) primaFatta = true; return pezzo; }
    if (restaMaiuscola(pezzo)) { primaFatta = true; return pezzo; }
    const basso = pezzo.toLocaleLowerCase('it');
    if (primaFatta) return basso;
    primaFatta = true;
    return basso.replace(/\p{L}/u, c => c.toLocaleUpperCase('it'));
  }).join('');
}
