import { useT } from '@/i18n/provider';
import type { TradeIdeaDocumentSource } from '@/lib/tradeIdeas';

export const emptyDocumentSource = (): TradeIdeaDocumentSource => ({
  url: '', published_at: '', publication_quote: '', report_date: '', report_date_quote: '', issuer_quote: '',
});

export default function TradeIdeaDocumentSources({ value, onChange, disabled }: {
  value: TradeIdeaDocumentSource[]; onChange: (sources: TradeIdeaDocumentSource[]) => void; disabled: boolean;
}) {
  const t = useT();
  const edit = (index: number, key: keyof TradeIdeaDocumentSource, text: string) =>
    onChange(value.map((source, i) => i === index ? { ...source, [key]: text } : source));
  return <details className="ti-document-sources">
    <summary>{t('tradeidea.addedSources')} {value.length ? `(${value.length})` : ''}</summary>
    <p className="ti-field-note">{t('tradeidea.addedSourcesHint')}</p>
    {value.map((source, index) => <fieldset key={index} disabled={disabled}>
      <legend>{t('tradeidea.sourceDocument')} {index + 1}</legend>
      <label>{t('tradeidea.sourceDocumentUrl')}<input type="url" value={source.url} maxLength={2000} onChange={e => edit(index, 'url', e.target.value)} placeholder="https://" /></label>
      <details className="ti-source-metadata"><summary>{t('tradeidea.sourceMetadataDetails')}</summary>
      <p className="ti-field-note">{t('tradeidea.sourceMetadataOptional')}</p>
      <label>{t('tradeidea.sourcePublished')}<input type="date" value={source.published_at} onChange={e => edit(index, 'published_at', e.target.value)} /></label>
      <label>{t('tradeidea.sourcePublicationQuote')}<textarea rows={2} value={source.publication_quote} maxLength={1500} onChange={e => edit(index, 'publication_quote', e.target.value)} /></label>
      <label>{t('tradeidea.sourceReportDate')}<input type="date" value={source.report_date} onChange={e => edit(index, 'report_date', e.target.value)} /></label>
      <label>{t('tradeidea.sourcePeriodQuote')}<textarea rows={2} value={source.report_date_quote} maxLength={1500} onChange={e => edit(index, 'report_date_quote', e.target.value)} /></label>
      <label>{t('tradeidea.sourceIssuerQuote')}<textarea rows={2} value={source.issuer_quote} maxLength={1500} onChange={e => edit(index, 'issuer_quote', e.target.value)} /></label>
      </details>
      <button type="button" className="ti-button ti-quiet" onClick={() => onChange(value.filter((_, i) => i !== index))}>{t('tradeidea.removeSource')}</button>
    </fieldset>)}
    <button type="button" className="ti-button ti-quiet" disabled={disabled || value.length >= 4} onClick={() => onChange([...value, emptyDocumentSource()])}>{t('tradeidea.addSource')}</button>
  </details>;
}
