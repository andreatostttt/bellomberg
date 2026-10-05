import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { NewsProviderBudget, UltimoGiro } from '@/lib/api';
import { linguaCorrente, localeDi } from '@/i18n/lingua';
import { hhmm, motivoMuto, nomeFonteMuta } from './calcoli';
import { parole } from './parole';

export interface Canale { name: string; count: number; last: number; muteReason: string | null }
export type StatoGiro = 'attesa' | 'errore' | 'assente' | UltimoGiro;

const fmt = (v: number, d = 1) => v.toLocaleString(localeDi(linguaCorrente()), { minimumFractionDigits: d, maximumFractionDigits: d });

/** Pannello laterale «Stato delle fonti»: ultimo giro dei provider, provider visti nel
 *  flusso con le fonti mute dichiarate dal backend, copertura. Mai dedotto lato client:
 *  senza dichiarazione si scrive «n.d.». Esc o il velo lo chiudono. */
export default function PannelloFonti({ aperto, onChiudi, giro, intervallo, prossimo, canali, fonti, copertura, budget }: {
  aperto: boolean;
  onChiudi: () => void;
  giro: StatoGiro;
  intervallo: string | null;
  prossimo: string | null;
  canali: Canale[];
  fonti: { declared: boolean; mute: Record<string, string>; avviso: string | null };
  copertura: { tickers: number; avgRel: number; stored: number; providers: number };
  budget: Record<string, NewsProviderBudget> | null;
}) {
  const w = parole();
  const chiudi = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!aperto) return;
    const prima = document.activeElement as HTMLElement | null;
    chiudi.current?.focus();
    return () => { try { prima?.focus(); } catch { /* elemento smontato */ } };
  }, [aperto]);
  if (!aperto) return null;
  const maxCh = Math.max(1, ...canali.map(c => c.count));
  const voci: Array<[string, string, string?]> = [];
  if (giro === 'attesa') voci.push([w.roundState, w.roundWaiting]);
  else if (giro === 'errore') voci.push([w.roundState, w.roundError, 'bad']);
  else if (giro === 'assente') voci.push([w.roundState, w.roundMissing, 'bad']);
  else {
    const ts = giro.timestamp ? new Date(giro.timestamp).getTime() : NaN;
    const eta = isFinite(ts) ? Math.max(0, Math.round((Date.now() - ts) / 60000))
      : typeof giro.age_minutes === 'number' && isFinite(giro.age_minutes) ? Math.round(giro.age_minutes) : null;
    const ora = isFinite(ts) ? new Date(ts).toLocaleTimeString(localeDi(linguaCorrente()), { hour: '2-digit', minute: '2-digit' }) : null;
    const nBlk = Object.keys(giro.providers_blocked || {}).length;
    voci.push([w.roundState, giro.stato === 'ok' ? w.roundOk : giro.stato === 'degradato' ? w.roundDegraded : (giro.motivo || String(giro.stato)),
      giro.stato === 'ok' ? undefined : giro.stato === 'degradato' ? 'warn' : 'bad']);
    voci.push([w.roundEnded, eta == null ? w.notDeclared : `${w.ago(eta)}${ora ? ` (${ora})` : ''}`]);
    voci.push([w.roundRead, String(giro.fetched ?? w.notDeclared)]);
    voci.push([w.roundSaved, String(giro.saved ?? w.notDeclared)]);
    voci.push([w.roundDup, String(giro.skipped_duplicates ?? w.notDeclared)]);
    voci.push([w.roundBlocked, String(nBlk), nBlk ? 'warn' : undefined]);
    // il motivo resta nella lingua del giro che l'ha scritto: si traduce solo il codice in testa
    if (nBlk) voci.push([w.roundBlockedList, Object.entries(giro.providers_blocked || {})
      .map(([p, m]) => `${nomeFonteMuta(p)} (${motivoMuto(m)})`).join(' · '), 'warn']);
  }
  voci.push([w.roundNext, `${prossimo ?? w.notDeclared}${intervallo ? ` · ${w.roundEvery(intervallo)}` : ''}`]);
  return (
    <div className="bbn-drawer-root news-sources" role="dialog" aria-modal="true" aria-label={w.sourcesTitle}
      onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onChiudi(); } }}>
      <div className="bbn-scrim" onClick={onChiudi} aria-hidden="true" />
      <div className="bbn-drawer">
        <div className="bbn-drawer-head">
          <div className="bbn-drawer-title"><b>{w.sourcesTitle}</b></div>
          <button ref={chiudi} type="button" className="bbn-icon-btn" onClick={onChiudi} aria-label={w.close}><X size={16} /></button>
        </div>
        <section>
          <h3>{w.lastRound}</h3>
          <dl>{voci.map(([k, v, tono]) => <div key={k}><dt>{k}</dt><dd className={tono ? `is-${tono}` : undefined}>{v}</dd></div>)}</dl>
        </section>
        <section>
          <h3>{w.providers}</h3>
          {!fonti.declared && <p className="news-note-inline">{w.muteNotDeclared}</p>}
          {canali.length === 0 ? <p className="muted">{w.noProviders}</p> : (
            <div className="news-prov">
              {canali.map(c => {
                const motivo = c.muteReason ? motivoMuto(c.muteReason) : null;
                const tono = !c.muteReason ? '' : c.muteReason === 'SKIP_BUDGET' ? 'warn' : 'bad';
                return (
                  <div key={c.name} className="news-prow">
                    <span className="nm"><span className={'news-dot' + (tono ? ` is-${tono}` : '')} />{nomeFonteMuta(c.name)}</span>
                    <span className="tb"><i style={{ width: `${(c.count / maxCh) * 100}%` }} /></span>
                    <span className="n num">{c.count}</span>
                    {motivo && <span className={'st bbn-pill ' + (tono === 'warn' ? 'news-pill-warn' : 'is-giu')}>{motivo}</span>}
                  </div>
                );
              })}
            </div>
          )}
          {fonti.avviso && <p className="news-note-inline is-warn">{fonti.avviso}</p>}
        </section>
        {budget && Object.keys(budget).length > 0 && (
          <section>
            <h3>{w.budgetTitle}</h3>
            <div className="news-budget">
              {Object.entries(budget).map(([p, b]) => {
                const pieno = b.used >= b.daily;
                const quota = b.allowed_now ?? b.daily;
                const nxt = b.next_call_at ? Date.parse(b.next_call_at) : NaN;
                return (
                  <div key={p} className="news-brow">
                    <span className="nm">{nomeFonteMuta(p)}</span>
                    <span className="tb" aria-hidden="true">
                      {b.allowed_now != null && <i className="q" style={{ width: `${(quota / b.daily) * 100}%` }} />}
                      <i className={'u' + (pieno ? ' is-full' : '')} style={{ width: `${Math.min(100, (b.used / b.daily) * 100)}%` }} />
                    </span>
                    <span className="n num">{b.used}/{b.daily}</span>
                    <span className="sub">
                      {w.budgetUsed(b.used, b.daily)}
                      {b.pace && b.allowed_now != null && !pieno ? ` · ${w.budgetPaced(b.allowed_now, b.pace[0], b.pace[1])}` : ''}
                      {pieno ? ` · ${w.budgetFull}` : isFinite(nxt) ? ` · ${w.budgetNext(hhmm(nxt))}` : ''}
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="news-note-inline">{w.budgetExplain}</p>
          </section>
        )}
        <section>
          <h3>{w.coverageTitle}</h3>
          <dl>
            <div><dt>{w.covTickers}</dt><dd>{copertura.tickers}</dd></div>
            <div><dt>{w.covRel}</dt><dd>{copertura.avgRel > 0 ? `${fmt(copertura.avgRel)} / 10` : w.notDeclared}</dd></div>
            <div><dt>{w.covSources}</dt><dd>{copertura.providers}</dd></div>
            <div><dt>{w.covStored}</dt><dd>{copertura.stored} {w.covMax(200)}</dd></div>
          </dl>
        </section>
        <section>
          <p className="news-explain"><b>{w.sourcesExplain[0]}</b> {w.sourcesExplain[1]} {w.sourcesExplain[2]}</p>
        </section>
      </div>
    </div>
  );
}
