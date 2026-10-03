import { API_BASE, requestHeaders, type FundArchiveItem } from '@/lib/api';
import { t } from '../i18n/t';

/** Historical file bytes only; never submit a valuation or repair request. */
export async function archivedFundWorkbook(item: FundArchiveItem, fetcher = fetch): Promise<Blob> {
  const path = `/fundamentals/archive/${encodeURIComponent(item.id)}/download`;
  if (!item.available || !item.historical || item.download_path !== path) {
    throw new Error(t('fundamentals.archiveUnavailable'));
  }
  const response = await fetcher(API_BASE + path, {headers: requestHeaders(), cache: 'no-store'});
  if (!response.ok) throw new Error(t('fundamentals.downloadFailed', {status: response.status}));
  if (response.headers.get('X-Valuation-Copy') !== 'historical') {
    throw new Error(t('fundamentals.downloadMismatch'));
  }
  return response.blob();
}
