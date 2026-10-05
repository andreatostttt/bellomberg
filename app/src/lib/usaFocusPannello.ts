import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react';

/** Focus di un pannello laterale modale (role="dialog" + aria-modal).
 *  All'apertura il focus entra nel pannello (primo controllo, altrimenti il pannello stesso);
 *  TAB e MAIUSC+TAB girano solo dentro il pannello; ESC lo chiude; alla chiusura il focus
 *  torna a chi l'aveva aperto, se nel frattempo non e' stato portato altrove. */
const FOCUSABILI = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export function focusabiliIn(box: HTMLElement | null): HTMLElement[] {
  if (!box) return [];
  return Array.from(box.querySelectorAll<HTMLElement>(FOCUSABILI)).filter(el => !el.closest('[hidden]'));
}

export function usaFocusPannello<T extends HTMLElement>(aperto: boolean, chiudi: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (!aperto) return;
    const prima = document.activeElement as HTMLElement | null;
    const box = ref.current;
    if (box) {
      const primo = focusabiliIn(box)[0];
      if (primo) primo.focus();
      else { if (!box.hasAttribute('tabindex')) box.setAttribute('tabindex', '-1'); box.focus(); }
    }
    return () => {
      const ora = document.activeElement;
      const libero = !ora || ora === document.body || (!!box && box.contains(ora));
      if (libero && prima && prima.isConnected) { try { prima.focus(); } catch { /* elemento smontato */ } }
    };
  }, [aperto]);
  const onKeyDown = (e: ReactKeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); chiudi(); return; }
    if (e.key !== 'Tab') return;
    const f = focusabiliIn(ref.current);
    if (!f.length) { e.preventDefault(); return; }
    const primo = f[0], ultimo = f[f.length - 1], ora = document.activeElement;
    if (e.shiftKey && (ora === primo || ora === ref.current)) { e.preventDefault(); ultimo.focus(); }
    else if (!e.shiftKey && ora === ultimo) { e.preventDefault(); primo.focus(); }
  };
  return { ref, onKeyDown };
}
