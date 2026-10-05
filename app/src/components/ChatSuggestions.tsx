import { useT } from '@/i18n/provider';
import { useEffect, useRef, useState } from 'react';
import { RefreshCw, SearchCheck, ShieldCheck, GitCompareArrows } from 'lucide-react';
import IconaTitolo from './nuova/IconaTitolo';
import PastigliaVariazione from './nuova/PastigliaVariazione';
import { dayPct } from '../lib/dailypl';
import { fmtNum } from '../lib/format';
import type { Position } from '../lib/api';
import { Bellomberg } from '../lib/api';
import { AGENT_QUESTIONS, portfolioTickers, tickerPrompt } from '../lib/chat-prompts';
import { leggiDetail } from '../lib/quota';
import type { Chiave } from '../i18n/t';
import './chat-suggestions.css';

const QUESTION_CATEGORIES: Record<string, string[]> = {
  capo: ['decisionReview', 'thesisChallenge', 'specialistComparison'],
  macro: ['exposureSensitivity', 'scenarioBuilding', 'macroRisk'],
  options: ['hedgeFit', 'hedgeTradeoffs', 'volatilitySignals'],
  quant: ['riskContribution', 'overlapAnalysis', 'contrarySignals'],
  fundamentals: ['valuationFramework', 'growthSensitivity', 'financialQuality'],
  crypto: ['cryptoExposure', 'navPremium', 'flowEvidence'],
  eventdesk: ['portfolioCatalysts', 'sourceVerification', 'policyScenarios'],
  politics: ['policyExposure', 'geopoliticalScenarios', 'scenarioFalsifiers'],
  news: ['thesisImpact', 'newsVerification', 'specialistFollowup'],
};

export default function ChatSuggestions({ agent, name, disabled, onPrompt }: {
  agent: string; name: string; disabled: boolean; onPrompt: (prompt: string) => void;
}) {
  const tr = useT();
  const [positions, setPositions] = useState<Position[]>([]);
  const [tickers, setTickers] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const request = useRef(0);
  const refresh = async () => {
    const id = ++request.current;
    setTickers(null); setError(null);
    try {
      const result = await Bellomberg.portfolio();
      const current = portfolioTickers(result.positions);
      if (id === request.current) { setTickers(current); setPositions(result.positions); }
    } catch (e: any) {
      if (id === request.current) setError(leggiDetail(e?.response?.data?.detail || e?.message || String(e)));
    }
  };
  useEffect(() => {
    setFilter(''); void refresh();
    return () => { ++request.current; };
  }, [agent]);
  const icons = [SearchCheck, ShieldCheck, GitCompareArrows];
  const visible = (tickers || []).filter(ticker => ticker.toLowerCase().includes(filter.trim().toLowerCase()));
  const ordered = [...visible].sort((a, b) => (positions.find(p => p.ticker === b)?.peso_pct || 0) - (positions.find(p => p.ticker === a)?.peso_pct || 0));
  return <div className="chat-suggestions">
    <div className="research-questions">
      {(AGENT_QUESTIONS[agent] || []).map((prompt, index) =>
        <button type="button" key={prompt} disabled={disabled} onClick={() => onPrompt(prompt)}>
          <span className="chat-question-category">
            {(() => { const Icon = icons[index % icons.length]; return <i className="chat-question-icon"><Icon size={15} aria-hidden="true" /></i>; })()}
            {(c => c.charAt(0).toLocaleUpperCase() + c.slice(1))(tr(('communications.promptCategory_' + (QUESTION_CATEGORIES[agent]?.[index] || 'thesisChallenge')) as Chiave))}
          </span>
          <span className="chat-question-text">{prompt}</span>
        </button>)}
    </div>
    <div className="portfolio-question-header">
      <span>{tr('communications.analysePosition')} <strong>{name}</strong></span>
      <button type="button" className="bbn-icon-btn" onClick={() => void refresh()} disabled={tickers === null && !error} aria-label={tr('communications.refreshList')} title={tr('communications.refreshList')}><RefreshCw size={16} aria-hidden="true" /></button>
    </div>
    {error ? <p role="alert" className="portfolio-question-error">{tr('communications.portfolioUnavailable')} {error}</p>
      : tickers === null ? <p role="status">{tr('communications.portfolioLoading')}</p>
      : tickers.length === 0 ? <p>{tr('communications.portfolioEmpty')}</p>
      : <>
        <input aria-label={tr('communications.filterTicker')} placeholder={tr('communications.filterPositions')}
          value={filter} onChange={e => setFilter(e.target.value)} />
        <div className="portfolio-question-tickers" aria-label={tr('communications.portfolioTickers')}>
          {ordered.map(ticker => {
            const position = positions.find(p => p.ticker === ticker);
            const daily = position ? dayPct(position) : null;
            return <button type="button" key={ticker} disabled={disabled}
            title={tr('communications.startTicker', {a: ticker, b: name})}
            onClick={() => onPrompt(tickerPrompt(agent, ticker))}>
              <IconaTitolo ticker={ticker} nome={position?.nome} dimensione="sm" />
              <span className="chat-position-copy">
                <span className="chat-position-name" title={position?.nome || ticker}>{position?.nome || ticker}</span>
                <span className="chat-position-ticker">{ticker}</span>
              </span>
              {daily != null && Number.isFinite(daily) && <PastigliaVariazione valore={daily} title={tr('communications.dailyChange')}
                className={'chat-position-change ' + (daily >= 0 ? 'positive' : 'negative')}>{daily > 0 ? '+' : ''}{fmtNum(daily, 2)}%</PastigliaVariazione>}
            </button>; })}
          {!visible.length && <span>{tr('communications.noPositionsMatch')}</span>}
        </div>
        <small>{tr('communications.suggestionsCount', { a: tickers.length })}</small>
      </>}
  </div>;
}
