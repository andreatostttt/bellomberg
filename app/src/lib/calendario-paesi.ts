/** Filtro paese del calendario economico (G8 seguito, 04/10/2026, Opus 5.5).
 *
 *  Il backend ricava il paese di una trimestrale dal suffisso di borsa (FR, UK, NL, JP,
 *  HK, CH… e «?» quando il suffisso non dice nulla). Con le chip fisse e la selezione di
 *  default quelle trimestrali sparivano senza avviso. Regole:
 *  - una trimestrale di un titolo DEL PORTAFOGLIO (`type: "Earnings"` con `ticker`) passa
 *    SEMPRE il filtro paese: niente sparisce in silenzio;
 *  - i paesi senza una chip propria e «?» si raccolgono nella chip «Altri» (`OTHER`). */
export const CHIP_ALTRI = 'OTHER';
export const COUNTRIES_AVAILABLE = ['US', 'EU', 'UK', 'IT', 'DE', 'JP', 'CN', 'FR', 'ES', 'CA', 'AU', CHIP_ALTRI];

export interface EventoPaese { country?: string; type?: string; ticker?: string }

export function chipDelPaese(country: string | undefined): string {
  const c = (country || '').trim().toUpperCase();
  if (!c) return '';
  return COUNTRIES_AVAILABLE.includes(c) && c !== CHIP_ALTRI ? c : CHIP_ALTRI;
}

export const delPortafoglio = (e: EventoPaese): boolean => e.type === 'Earnings' && !!(e.ticker || '').trim();

export function passaFiltroPaese(e: EventoPaese, sel: string[]): boolean {
  if (sel.length === 0 || delPortafoglio(e)) return true;
  return sel.includes(chipDelPaese(e.country));
}

export function conteggioPerChip(eventi: EventoPaese[]): Record<string, number> {
  const conti: Record<string, number> = {};
  for (const e of eventi) {
    const k = chipDelPaese(e.country);
    if (k) conti[k] = (conti[k] || 0) + 1;
  }
  return conti;
}
