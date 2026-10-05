import { Bitcoin, BookOpenText, Crown, Globe, Landmark, MessageSquareText, Newspaper, Radar, Sigma, Waves } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/** Un'icona per desk, come i loghi dei titoli: cerchio pieno, segno bianco. */
const ICONE: Record<string, LucideIcon> = {
  capo: Crown, macro: Globe, options: Waves, quant: Sigma, fundamentals: BookOpenText,
  crypto: Bitcoin, eventdesk: Radar, politics: Landmark, news: Newspaper,
};

/** Tinta del desk (dal colore del backend) alla stessa luminosità delle iniziali dei titoli
 *  (coloreStabile: 46% / 31%), così il segno bianco si legge su Chiaro e su Scuro. */
export function coloreDesk(hex: string | null | undefined, ritirato = false): string {
  if (ritirato) return 'hsl(220 8% 40%)';
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return 'hsl(220 8% 40%)';
  const n = Number.parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v => v / 255);
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return `hsl(${Math.round((h * 60 + 360) % 360)} 46% 31%)`;
}

export default function IconaDesk({ id, colore, ritirato = false, dimensione = 'md', className = '' }: {
  id: string;
  colore?: string | null;
  ritirato?: boolean;
  dimensione?: 'xs' | 'sm' | 'md';
  className?: string;
}) {
  const Icona = ICONE[id] || MessageSquareText;
  const px = dimensione === 'xs' ? 13 : dimensione === 'sm' ? 17 : 22;
  return (
    <span className={`bbn-ico is-${dimensione} bbn-chat-desk-ico${className ? ' ' + className : ''}`}
      style={{ background: coloreDesk(colore, ritirato) }} aria-hidden="true" data-desk-icon={id}>
      <Icona size={px} strokeWidth={2} />
    </span>
  );
}
