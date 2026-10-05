import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Bellomberg } from '@/lib/api';
import type { PortfolioRisk, Position, VarContributionResult } from '@/lib/api';
import { fmtEUR, fmtNum, fmtPct } from '@/lib/format';
import { giudicaRischio } from '@/lib/rischio-livello';
import Card from '@/components/nuova/Card';
import { parole } from './parole';
import { nomeTitolo } from './ListaPosizioni';

const finito = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Barra «vs SPY»: il riempimento è il rapporto portafoglio/SPY su una scala 0–3×,
 *  la tacca è il livello di SPY (1×). */
function Rapporto({ valore }: { valore: number | null }) {
  if (valore == null || !Number.isFinite(valore)) return <span className="bbn-rel is-empty" aria-hidden="true" />;
  return (
    <span className="bbn-rel" title={`${fmtNum(valore, 2)}× SPY`} aria-hidden="true">
      <i style={{ width: `${Math.min(100, (Math.max(0, valore) / 3) * 100)}%` }} /><b />
    </span>
  );
}

export default function CardRischio({ rischio, caricamento, errore, posizioni, onRicalcola }: {
  rischio: PortfolioRisk | null;
  caricamento: boolean;
  errore: string | null;
  posizioni: Position[];
  onRicalcola: () => void;
}) {
  const w = parole();
  const [contributi, setContributi] = useState<VarContributionResult | null>(null);
  useEffect(() => {
    let vivo = true;
    Bellomberg.varContribution()
      .then(r => { if (vivo) setContributi(r && !r.error && Array.isArray(r.items) ? r : null); })
      .catch(() => { if (vivo) setContributi(null); });
    return () => { vivo = false; };
  }, []);

  const pf = rischio && !rischio.error ? rischio.portfolio : null;
  const spy = rischio && !rischio.error ? rischio.benchmark || null : null;
  const giudizio = giudicaRischio(pf?.vol_annual_pct, spy?.vol_annual_pct);
  const top = contributi ? [...contributi.items].filter(i => finito(i.contribution_pct_of_total_var))
    .sort((a, b) => b.contribution_pct_of_total_var - a.contribution_pct_of_total_var).slice(0, 2) : [];
  const nomeDi = (ticker: string) => { const p = posizioni.find(x => x.ticker === ticker); return p ? nomeTitolo(p) : ticker; };
  const quota2 = top.length === 2 ? top[0].contribution_pct_of_total_var + top[1].contribution_pct_of_total_var : null;

  const rapporto = (a: unknown, b: unknown) => finito(a) && finito(b) && b !== 0 ? Math.abs(a) / Math.abs(b) : null;
  const righe: Array<[string, React.ReactNode, React.ReactNode, number | null]> = pf ? [
    ['vol', fmtPct(pf.vol_annual_pct, false), spy ? fmtPct(spy.vol_annual_pct, false) : '—', rapporto(pf.vol_annual_pct, spy?.vol_annual_pct)],
    ['var95', <>{fmtPct(pf.var_95_1d_pct, false)}<small>{fmtEUR(pf.var_95_1d_eur, false, 0)}</small></>, spy ? fmtPct(spy.var_95_1d_pct, false) : '—', rapporto(pf.var_95_1d_pct, spy?.var_95_1d_pct)],
    ['var99', <>{fmtPct(pf.var_99_1d_pct, false)}<small>{fmtEUR(pf.var_99_1d_eur, false, 0)}</small></>, spy ? fmtPct(spy.var_99_1d_pct, false) : '—', rapporto(pf.var_99_1d_pct, spy?.var_99_1d_pct)],
    ['dd', fmtPct(pf.max_dd_1y_pct, false), spy ? fmtPct(spy.max_dd_1y_pct, false) : '—', rapporto(pf.max_dd_1y_pct, spy?.max_dd_1y_pct)],
    ['beta', fmtNum(pf.beta_vs_spy, 2), spy ? fmtNum(spy.beta_vs_spy, 2) : '—', finito(pf.beta_vs_spy) ? pf.beta_vs_spy : null],
    ['sharpe', fmtNum(pf.sharpe, 2), spy ? fmtNum(spy.sharpe, 2) : '—', rapporto(pf.sharpe, spy?.sharpe)],
  ] : [];

  let perche: string | null = null;
  if (pf && finito(pf.beta_vs_spy)) {
    const beta = fmtNum(pf.beta_vs_spy, 2);
    perche = top.length === 2 && giudizio.rapporto != null && giudizio.rapporto > 1.25 && pf.beta_vs_spy < giudizio.rapporto
      ? w.riskWhyIdio(beta, `${nomeDi(top[0].ticker)} (${fmtNum(top[0].contribution_pct_of_total_var, 0)}% ${w.ofVar})`, `${nomeDi(top[1].ticker)} (${fmtNum(top[1].contribution_pct_of_total_var, 0)}%)`)
      : w.riskWhyMarket(beta);
    if (giudizio.rapporto != null) perche = `${w.riskVsSpy(fmtNum(giudizio.rapporto, 1))} ${perche}`;
  }

  return (
    <Card titolo={w.risk} className="bbn-risk" data-testid="dashboard-risk"
      azioni={<>
        {giudizio.livello && <span className={`bbn-level is-${giudizio.livello}`}>{w.riskLevel[giudizio.livello]}</span>}
        <button type="button" className="bbn-icon-btn" onClick={onRicalcola} disabled={caricamento} aria-label={w.riskRecalc} title={w.riskRecalc}>
          <RefreshCw size={14} className={caricamento ? 'animate-spin' : undefined} aria-hidden="true" />
        </button>
      </>}>
      {!pf ? <p className="bbn-empty" role={errore ? 'alert' : 'status'}>{caricamento ? w.riskLoading : `${w.riskUnavailable}${errore ? ' · ' + errore : rischio?.error ? ' · ' + rischio.error : ''}`}</p> : <div className="bbn-risk-body">
        <div className="bbn-kpis">
          <div><span>{w.relativeVol}</span><b className="num">{giudizio.rapporto != null ? `${fmtNum(giudizio.rapporto, 2)}× SPY` : fmtPct(pf.vol_annual_pct, false)}</b></div>
          <div><span>{w.varConcentration}</span><b className="num">{quota2 != null ? fmtPct(quota2, false, 0) : '—'}</b>{quota2 != null && <small>{w.inTwoStocks}</small>}</div>
        </div>
        {perche && <p className="bbn-risk-why">{perche}</p>}
        <table className="bbn-risk-table num">
          <thead><tr><th title={w.riskBasis(rischio?.lookback_days || 252, spy?.n_obs ?? null)}>{w.metric}</th><th>{w.portfolio}</th><th>SPY</th><th title={w.vsSpyHint}>{w.vsSpy}</th></tr></thead>
          <tbody>{righe.map(([chiave, valore, valoreSpy, r]) => (
            <tr key={chiave}>
              <td><span className="bbn-term" title={w.metrics[chiave][1]}>{w.metrics[chiave][0]}</span></td>
              <td>{valore}</td><td>{valoreSpy}</td><td><Rapporto valore={spy ? r : null} /></td>
            </tr>
          ))}</tbody>
        </table>
        {/* un avviso malformato dal backend si scarta, non fa cadere la pagina */}
        {Array.isArray(rischio?.alerts) && rischio!.alerts.filter(a => a && typeof a === 'object' && typeof a.message === 'string'
          && (a.level === 'high' || a.level === 'med')).map((alert, i) => (
          <span key={`${alert.metric}-${i}`} className="bbn-alert" role="status">{alert.message}</span>
        ))}
      </div>}
    </Card>
  );
}
