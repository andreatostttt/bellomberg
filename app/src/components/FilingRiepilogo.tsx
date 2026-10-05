/* Riepilogo di una riga del filing (fase E, 03/10/2026) nella scheda titolo di Mercati e in
   Fondamentali: stato, documento, periodi, cambiamenti e link alla pagina Filing, dove stanno
   tutte le azioni (attivazione, verifica, profilo, proposta AI). Sola lettura, nessuna AI. */
import { useEffect, useState } from 'react';
import { FileSearch } from 'lucide-react';
import { Bellomberg, type FilingListing, type FilingRunDetail } from '@/lib/api';
import { useT } from '@/i18n/provider';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { leggiDetail } from '@/lib/quota';
import { coppiaBreve } from '@/pages/filing/logica';
import './filing-riepilogo.css';

/** Documento del profilo in breve: SEC con le forme, ESEF, IR. */
export function documentoBreve(profilo: Record<string, unknown> | null | undefined): string | null {
  if (!profilo) return null;
  const varianti = Array.isArray(profilo.varianti) ? profilo.varianti as Record<string, unknown>[] : [];
  const fonti = (p: Record<string, unknown>) => JSON.stringify(p.fonti) === '["ir"]';
  const parti: string[] = [];
  if (profilo.esef_modo === 'blocchi') parti.push('ESEF');
  const forme = [...new Set([...(Array.isArray(profilo.forme_sec) ? profilo.forme_sec as string[] : []),
    ...varianti.flatMap(v => Array.isArray(v.forme_sec) ? v.forme_sec as string[] : [])])];
  if (forme.length) parti.push('SEC ' + forme.join('/'));
  else if (!parti.length && (profilo.cik || String(profilo.emittente_id || '').startsWith('CIK:'))) parti.push('SEC');
  if (fonti(profilo) || varianti.some(fonti)) parti.push('IR');
  return parti.join(' + ') || null;
}

export default function FilingRiepilogo({ ticker }: { ticker: string }) {
  const tr = useT();
  const [stato, setStato] = useState<FilingListing | null>(null);
  const [run, setRun] = useState<FilingRunDetail | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  // lettura del run fallita: dichiarata, non «in attesa del primo confronto» (G9b 04/10)
  const [erroreRun, setErroreRun] = useState<string | null>(null);
  useEffect(() => {
    let vivo = true;
    setStato(null); setRun(null); setErrore(null); setErroreRun(null);
    Bellomberg.filingList(ticker).then(s => {
      if (!vivo || s.ticker !== ticker) return;
      setStato(s);
      const id = s.ultimo_completo?.id;
      if (id != null) Bellomberg.filingRun(id).then(r => { if (vivo && r.ticker === ticker) setRun(r); })
        .catch(e => { if (vivo) setErroreRun(leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) || String(e)); });
    }).catch(e => { if (vivo) setErrore(leggiDetail(e?.response?.data?.detail ?? e?.message ?? e) || String(e)); });
    return () => { vivo = false; };
  }, [ticker]);

  const r = run?.result, diff = r?.confronto_corrente || r?.confronto_storico;
  const profilo = stato?.profile?.profile as Record<string, unknown> | undefined;
  const n = diff?.cambiamenti?.length ?? null;
  const data = run?.finished_at ? new Intl.DateTimeFormat(localeDi(linguaCorrente()), { day: '2-digit', month: '2-digit' }).format(Date.parse(run.finished_at)) : null;
  const parti = errore ? [tr('filings.readError', { error: errore })]
    : !stato ? [tr('filings.loading')]
    : !stato.profile ? [tr('filings.none')]
    : [documentoBreve(profilo), coppiaBreve(r?.coppia ?? null, r?.variante || (profilo?.tipo as string | undefined)),
      stato.active_run ? tr('filings.running')
        : stato.ultimo_errore ? tr('filings.error', { reason: stato.ultimo_errore.reason || '—' })
        : n == null ? (erroreRun ? tr('filings.runReadError', { error: erroreRun }) : tr('filings.waiting')) : n === 0 ? tr('filings.noChanges') : n === 1 ? tr('filings.oneChange') : tr('filings.changes', { n }),
      data ? tr('filings.checked', { date: data }) : null];
  const tono = errore || erroreRun || stato?.ultimo_errore ? 'bad' : stato?.active_run ? 'run' : !stato?.profile ? 'off' : 'ok';
  return (
    <div className="filing-riepilogo" data-filing-riepilogo={ticker} data-stato={stato ? tono : 'loading'} role={errore ? 'alert' : undefined}>
      <FileSearch size={15} aria-hidden="true" />
      <b>{tr('filings.label')}</b>
      <span className="filing-riepilogo-t">{parti.filter(Boolean).join(' · ')}</span>
      {/* ancora semplice: l'app usa HashRouter, e il riepilogo vive anche fuori da un contesto di router nei test */}
      <a className="filing-riepilogo-link" href={`#/filing?t=${encodeURIComponent(ticker)}`} data-filing-apri={ticker}>{tr('filings.open')} →</a>
    </div>
  );
}
