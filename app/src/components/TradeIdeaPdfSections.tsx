import { useT } from '@/i18n/provider';
import type { Chiave } from '@/i18n/t';
import type { TradeIdeaDetail } from '@/lib/tradeIdeas';

/** Etichette delle sezioni note del PDF Trade Idea; una chiave nuova del backend resta visibile col suo nome. */
const ETICHETTE: Record<string, Chiave> = {
  executive: 'tradeidea.pdfSection_executive', recommendation: 'tradeidea.pdfSection_recommendation',
  thesis: 'tradeidea.pdfSection_thesis', risks: 'tradeidea.pdfSection_risks', data_notes: 'tradeidea.pdfSection_data_notes',
  pm_view: 'tradeidea.pdfSection_pm_view', business: 'tradeidea.pdfSection_business',
  financial_quality: 'tradeidea.pdfSection_financial_quality', valuation: 'tradeidea.pdfSection_valuation',
  scenarios: 'tradeidea.pdfSection_scenarios', portfolio_risk: 'tradeidea.pdfSection_portfolio_risk',
  catalysts: 'tradeidea.pdfSection_catalysts', positioning: 'tradeidea.pdfSection_positioning',
  red_team: 'tradeidea.pdfSection_red_team', decision: 'tradeidea.pdfSection_decision',
  sources: 'tradeidea.pdfSection_sources', annex: 'tradeidea.pdfSection_annex',
};

/** Le sezioni del manifest PDF (section_pages) in ordine di pagina; valori non numerici scartati. */
export function sezioniPdf(detail: TradeIdeaDetail): Array<{ key: string; page: number }> {
  const raw = detail.progress?.report_quality?.section_pages;
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw)
    .filter(([, page]) => typeof page === 'number' && Number.isFinite(page) && page > 0)
    .map(([key, page]) => ({ key, page }))
    .sort((a, b) => a.page - b.page || a.key.localeCompare(b.key));
}

/** Indice del PDF consegnato: assente se il manifest non dichiara le pagine delle sezioni. */
export default function TradeIdeaPdfSections({ detail }: { detail: TradeIdeaDetail }) {
  const t = useT();
  const sezioni = sezioniPdf(detail);
  if (!sezioni.length) return null;
  const titoli = new Map((detail.result?.dossier || []).map(section => [section.key, section.title]));
  return <div className="ti-pdf-sections" data-trade-idea="sezioni-pdf">
    <h4>{t('tradeidea.pdfSections')}</h4>
    <p className="ti-muted">{t('tradeidea.pdfSectionsNote')}</p>
    <ol>{sezioni.map(({ key, page }) => <li key={key} data-sezione={key}>
      <span>{ETICHETTE[key] ? t(ETICHETTE[key]) : titoli.get(key) || key}</span>
      <b>{t('tradeidea.pdfSectionPage', { page })}</b>
    </li>)}</ol>
  </div>;
}
