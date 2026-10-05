import { fonteIcona } from '@/lib/loghi';
import { useLogoRemoto } from '@/lib/loghi-remoti';

/** Icona tonda di un titolo: logo locale, logo della società dal backend (profilo Finnhub o
 *  icona del sito dell'emittente), altrimenti il ripiego dichiarato: iniziali su colore stabile.
 *  Il motivo per cui il backend non ha un logo resta nell'attributo data-logo-motivo. */
export default function IconaTitolo({ ticker, nome, dimensione = 'md' }: {
  ticker: string;
  nome?: string | null;
  dimensione?: 'sm' | 'md' | 'lg' | 'xs';
}) {
  const fonte = fonteIcona(ticker, nome);
  const remoto = useLogoRemoto(ticker);
  const classe = `bbn-ico is-${dimensione}`;
  if (fonte.tipo === 'locale') {
    return <span className={classe + ' is-image'} aria-hidden="true"><img src={fonte.url} alt="" draggable={false} /></span>;
  }
  if (remoto.logo) {
    return <span className={classe + ' is-logo'} aria-hidden="true" data-logo="remoto" data-logo-fonte={remoto.fonte || undefined}><img src={remoto.logo} alt="" draggable={false} /></span>;
  }
  return <span className={classe} aria-hidden="true" style={{ background: fonte.colore }} data-logo="iniziali"
    data-logo-motivo={remoto.motivo || undefined}>{fonte.testo}</span>;
}
