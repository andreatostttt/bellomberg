// Additive synthetic responses used only by pages-modern-actions.cjs.
// Every URL, identity, figure and workbook descriptor below is invented QA data.
const now = '2026-09-29T12:00:00Z';
const { fixtureData } = require('./pages-modern-fixtures.cjs');
const clone = value => JSON.parse(JSON.stringify(value));
const twrDates = Array.from({ length: 42 }, (_, i) => {
  const date = new Date(Date.UTC(2026, 7, 19 + i));
  return date.toISOString().slice(0, 10);
});
const twrIndex = twrDates.map((_, i) => Number((100 * Math.pow(1.0007, i) + Math.sin(i / 2) * .11).toFixed(4)));

const news = [
  { id: 9911, title: 'Synthetic supplier reports steady lead times', snippet: 'Fixture-only article used to verify the populated wire and filter state.', source: 'Synthetic Wire', url: null, published_at: now, pulled_at: now, ticker_mentioned: 'SYN1', theme: 'technology', provider: 'fixture-wire', sentiment: 'positive', sentiment_score: .62, relevance: .94, summary_status: 'available', summary_language: 'en', summary_note: null },
  { id: 9912, title: 'Synthetic regulator publishes reporting calendar', snippet: 'A second synthetic item for the neutral sentiment and provider filters.', source: 'Synthetic Journal', url: null, published_at: now, pulled_at: now, ticker_mentioned: 'SYN2', theme: 'regulation', provider: 'fixture-journal', sentiment: 'neutral', sentiment_score: 0, relevance: .77, summary_status: 'available', summary_language: 'en', summary_note: null },
  { id: 9913, title: 'Synthetic logistics costs ease in scenario data', snippet: 'Third invented article for empty versus populated result checks.', source: 'Synthetic Research', url: null, published_at: now, pulled_at: now, ticker_mentioned: 'SYN3', theme: 'commodities', provider: 'fixture-research', sentiment: 'negative', sentiment_score: -.38, relevance: .68, summary_status: 'available', summary_language: 'en', summary_note: null },
];

const decision = { method_id: 'operating_fcff', decision_status: 'resolved', support_status: 'integrated', requirements_status: 'complete', missing_fields: [], rationale: 'Synthetic QA model contract.' };
const quality = { status: 'DOCUMENTATA', issues: [] };
const detail = { engine: 'operating_v3', method: 'operating_fcff', payload_currency: 'EUR', valuation_date: '2026-09-29', valuation_basis: 'Synthetic QA assumptions only.', valuation_usability: { usable: true, reasons: [], missing_fields: [] }, valuation_decision: decision, analytical_quality: quality, sanity: { severity: 'OK', headline: 'Synthetic ranges are internally consistent.' }, fair_value_weighted: 128.4, fair_value_blend: 126.8, fair_value_base: 129, fair_value_bull: 162, fair_value_bear: 88, fair_value_final: 128.4, holding_irr: { irr: .14, years: 3, by_scenario: { bear: -.03, base: .14, bull: .27 } }, peers_used: ['SYN2', 'SYN3'], peer_note: 'Synthetic peer set.', wacc_used: .091, _timestamp: now };
const currentPath = '/valuation/models/SYN1/generations/qa-generation-syn1-r4/workbook';
const model = { file: 'SYN1_QA_SYNTHETIC.xlsx', dir: 'synthetic-fixture', engine: 'operating_v3', ticker: 'SYN1', matched: true, identity_status: 'canonical', snapshot_id: 'qa-snapshot-syn1', generation_id: 'qa-generation-syn1-r4', current_generation: true, current_download: currentPath, historical_download: false, automation: { status: 'ready', locked: false, current: { generation_id: 'qa-generation-syn1-r4', revision: 4, as_of: now, published_at: now }, approval: { publication_origin: 'synthetic_fixture', active: true }, latest_publication_attempt: null, latest_prepare_job: { status: 'succeeded', reason: 'Synthetic QA only', updated_at: now }, latest_price_job: null }, valuation_usability: { usable: true, reasons: [], missing_fields: [] }, valuation_decision: decision, analytical_quality: quality, canonical: true, generated_at: now, flagged: false, fair_value: 128.4, price_at_thesis: 105, price_model_as_of: now, upside_pct: 22.3, upside_today_pct: 22.3, market_quote: { status_at_read: 'ok', price: 105, currency: 'EUR', as_of: now }, thesis_date: '2026-09-29', variant_view: 'Synthetic QA base case.', sanity_severity: 'OK', sanity_headline: 'Synthetic ranges are internally consistent.', detail, memo_id: 330 };

const periodRows = (a, b) => ({ years: ['FY2025', 'FY2024', 'FY2023'], rows: { Revenue: [a, b, b * .91], 'Operating income': [a * .22, b * .2, b * .18], EPS: [3.8, 3.4, 3.1] } });
const reads = {
  // TWR's chart is intentionally longer than the selected 1W KPI slice so a
  // trusted chart gesture has real historical bars to zoom and pan across.
  '/portfolio/analytics/twr': { ...clone(fixtureData.twr), dates: twrDates, twr_index: twrIndex,
    regimes: twrDates.map((_, i) => i < 9 ? 'reconstructed' : 'official'),
    values_eur: twrDates.map((_, i) => 118000 + i * 420), flows_eur: twrDates.map(() => 0), n_days: twrDates.length },
  '/portfolio/factors': { timestamp: now, version: 'synthetic-factors-v1', period: '1y', model: 'Fama-French 5 + momentum',
    method: 'synthetic_fixture', data_source: 'fixture-only', n_holdings_analyzed: 2, n_holdings_skipped: 0,
    n_alpha_significant_5pct: 1, coverage_weight_pct: 100,
    portfolio_aggregate: { alpha_annualized_pct: 2.4, beta_market: .92, beta_smb: .18,
      beta_hml: -.11, beta_rmw: .07, beta_cma: -.04, beta_mom: .22 },
    per_holding: { SYN1: { ticker: 'SYN1', weight_pct: 55, n_obs: 180, period: '1y', alpha_annualized_pct: 3.1,
      alpha_tstat: 2.4, r_squared: .42, beta_market: 1.02, beta_smb: .2, beta_hml: -.12,
      beta_rmw: .08, beta_cma: -.03, beta_mom: .19 },
      SYN2: { ticker: 'SYN2', weight_pct: 45, n_obs: 175, period: '1y', alpha_annualized_pct: 1.6,
        alpha_tstat: 1.2, r_squared: .37, beta_market: .81, beta_smb: .15, beta_hml: -.09,
        beta_rmw: .06, beta_cma: -.05, beta_mom: .25 } },
    regions: { US: { label: 'Synthetic US', n_holdings: 2, weight_pct: 100 } }, region_errors: {},
    ff_data_last_date: '2026-08-31', ff_data_n_obs: 240,
    ff_data_by_region: { US: { n_obs: 240, last_date: '2026-08-31' } } },
  '/portfolio/metrics/beta_reconcile': { betas: { book_vs_spy: .94, book_vs_eurostoxx: .88, book_vs_world: .91 },
    definitions: { book_vs_spy: 'Synthetic global equity proxy', book_vs_eurostoxx: 'Synthetic euro equity proxy',
      book_vs_world: 'Synthetic world equity proxy' }, sources_failed: {}, threshold: .2, max_spread: .06,
    verdict: 'RECONCILED', beta_consensus: .91, note: 'Synthetic reconciliation result.' },
  // Performance Book/Risk consumes the api.ts DrawdownsResult contract.
  // The broad base fixture predates that response shape, so this additive
  // override is limited to the controlled action harness.
  '/portfolio/analytics/drawdowns': { n_episodes_total: 1,
    top_5_drawdowns: [{ peak_date: '2026-09-08', peak_nav: 60800,
      trough_date: '2026-09-13', trough_nav: 57900, recovery_date: '2026-09-17',
      depth_pct: -4.77, duration_to_trough_days: 5, recovery_days: 4,
      total_days: 9, recovered: true }],
    current_drawdown: null, max_drawdown_pct: -4.77, avg_drawdown_pct: -4.77,
    pain_index: 1.2, n_days_analyzed: 22, first_date: '2026-09-01', last_date: '2026-09-22' },
  '/portfolio/analytics/concentration': { by_ticker: { hhi: 0.13, classification: 'diversified',
      effective_n: 7.69, top_5_pct: 71.2,
      top_holdings: fixtureData.positions.slice(0, 5).map(position => ({ ticker: position.ticker, weight_pct: position.peso_pct })) },
    by_region: { hhi: 0.42, classification: 'moderate', weights_pct: { Europe: 68, 'North America': 24, 'Other synthetic': 8 } },
    by_currency: { hhi: 0.51, classification: 'moderate', weights_pct: { EUR: 72, USD: 23, GBP: 5 } },
    interpretation: { by_ticker: 'Synthetic company concentration only.', by_region: 'Synthetic regions only.', by_currency: 'Synthetic currencies only.' } },
  '/portfolio/analytics/liquidity': { items: fixtureData.positions.map(position => ({ ticker: position.ticker,
      position_eur: position.valore_mercato, avg_daily_volume_eur: 1250000, days_to_liquidate: .4,
      score: 'green', reason: 'Synthetic liquidity evidence.' })),
    n_green: fixtureData.positions.length, n_yellow: 0, n_red: 0, threshold_green_days: 1,
    threshold_yellow_days: 3, assumption_pct_of_volume: .1, note: 'Synthetic QA contract; no live market data.' },
  // Search response contract is `symbol`; the base fixture intentionally keeps
  // broader endpoint coverage while this override exercises selection/detail.
  '/market/search': { results: [{ symbol: 'SYN1', name: 'Synthetic holding 1', exchange: 'FIX', type: 'Equity' }] },
  '/news/feed': (() => {
    const items = clone(fixtureData.wireItems);
    items.push({ id: 9914, title: 'Synthetic archived headline beyond the active window',
      snippet: 'Old fixture item separates all-time from recent date filtering.', source: 'Fixture Archive',
      url: null, published_at: new Date(Date.now() - 30 * 86400_000).toISOString(),
      pulled_at: new Date(Date.now() - 30 * 86400_000).toISOString(), ticker_mentioned: 'SYN4',
      theme: 'technology', provider: 'fixture-archive', sentiment: 'positive', sentiment_score: .4,
      relevance: 6.2, summary_status: 'available', summary_language: 'en', summary_note: null });
    return { count: items.length, items, timestamp: now, fonti_mute: {}, avviso: null };
  })(),
  '/news/providers': { providers_contingentati: [], timestamp: now,
    ultimo_giro: { stato: 'degradato', timestamp: new Date(Date.now() - 48 * 3600_000).toISOString(),
      age_minutes: 2880, fetched: 6, classified: 4, classification_attempted: 6, classification_failed: 2,
      saved: 2, skipped_duplicates: 0, providers_blocked: { 'fixture-news': 'Synthetic stale provider status.' },
      motivo: 'Synthetic stale and degraded provider fixture.' },
    refresh_job: null, fonti_mute: { 'fixture-news': 'Synthetic stale provider status.' },
    avviso: 'Synthetic stale fixture provider status.' },
  '/filings/SYN1': { ticker: 'SYN1', status: 'available', reason: 'Synthetic fixture archive only.',
    profile: { ticker: 'SYN1', profile: { identity: { ticker: 'SYN1', fixture: true },
      source: { name: 'Synthetic archive', url: 'https://example.invalid' },
      sections: ['Synthetic segment note', 'Synthetic outlook'] }, version: 2, enabled: false,
      interval_hours: 168, qualitative_enabled: false,
      next_due_at: new Date(Date.now() - 48 * 3600_000).toISOString() },
    runs: clone(fixtureData.filingRuns), active_run: null, ultimo_completo: clone(fixtureData.filingRuns[0]) },
};
// Menu badge of the Filing page: one portfolio title with changes newer than the last committee run.
reads['/filings/novita'] = { n: 1, tickers: ['SYN1'] };

// Add synthetic motor-specific model rows on top of the fixture's operating
// model so the detail renderer exercises three distinct presentation branches.
// These rows are display fixtures only and intentionally have no download or
// automation endpoint, avoiding any unstubbed workbook or job request.
const baseModel = fixtureData.fundamentalsModels[0];
const modelVariant = (ticker, engine, detail) => ({
  ...clone(baseModel), file: `${ticker}_SYNTHETIC.xlsx`, dir: 'synthetic-fixture/motor-scenarios', ticker,
  engine, matched: false, current_generation: false, current_download: '', historical_download: false,
  automation: { status: 'unavailable', locked: null, current: null }, canonical: true,
  generation_id: `qa-generation-${ticker.toLowerCase()}`, snapshot_id: `qa-snapshot-${ticker.toLowerCase()}`,
  generated_at: now, fair_value: engine === 'mnav' ? null : detail.fair_value_blend,
  price_at_thesis: detail.price ?? 100, upside_pct: engine === 'mnav' ? null : 12.4,
  detail: { ...clone(baseModel.detail), engine, ...detail },
});
const motorModels = [
  modelVariant('SYNBANK', 'bank', { fair_value_ri: 121.4, fair_value_ptbv: 114.2, fair_value_ddm: 127.8,
    fair_value_blend: 121.4, methods_divergence: .11, cost_of_risk_ttc: { avg_bps: 76, last_bps: 81,
      last_year: 2025, years: [2023, 2024, 2025], mixed: false, sign_note: 'Synthetic positive sign convention.', source: 'synthetic fixture' } }),
  modelVariant('SYNRAB', 'rab', { fair_value_ev_rab: 101.5, fair_value_ddm_reg: 98.3, fair_value_peer: 103.1,
    fair_value_blend: 101.5, blend_methods: ['ev_rab', 'ddm_reg', 'peer'], methods_divergence: .04,
    peer_method: 'synthetic_peer', rab_base: 8200, allowed_return_calc: .064, allowed_return_nominal: .081,
    service: 'synthetic_grid', convention: 'real_pretax', anchor_stale: false, rab_premium: null }),
  modelVariant('SYNMNAV', 'mnav', { profile_key: 'cef_nav', payload_currency: 'USD', price: 20.4,
    nav_per_share: 25.5, discount_to_nav_pct: -20, nav_target: 1.05, fair_value_nav: 26.775,
    nav_vintage: { nav_as_of: '2026-09-28', nav_age_days: 1 }, fv_note: 'Synthetic NAV comparison only.' }),
];
reads['/fundamentals/models'] = { count: fixtureData.fundamentalsModels.length + motorModels.length,
  models: [...clone(fixtureData.fundamentalsModels), ...motorModels],
  nota: 'Synthetic fixture: no real issuer data, model run, or workbook.' };

const writes = {
  '/valuation/models/SYN1/variants': ({ method }) => method === 'GET'
    ? { variants: [{ id: 'qa-variant-syn1', ticker: 'SYN1', source_generation: 'qa-generation-syn1-r4', label: 'Synthetic saved variant', created_at: now, status: 'ready', available: false, modified: null }] }
    : { id: 'qa-variant-created-syn1', ticker: 'SYN1', source_generation: 'qa-generation-syn1-r4', label: 'Synthetic requested variant', created_at: now, status: 'pending', available: false, modified: null },
  '/valuation/models/SYN1/lock': { ticker: 'SYN1', generation_id: 'qa-generation-syn1-r4', locked: true, synthetic: true },
  '/valuation/models/SYN1/refresh': { accepted: true, job_id: 'qa-model-refresh-syn1', synthetic: true },
  '/filings/SYN1/refresh': { accepted: true, run_id: 990, synthetic: true },
  '/filings/SYN1/profile': { ticker: 'SYN1', enabled: true, interval_hours: 24, synthetic: true },
  '/filings/activate-missing': { attivati: ['SYN5'], da_confermare: ['SYN2'], senza_fonte: ['SYN3'], esclusi: [], gia_attivi: ['SYN1'],
    scollegati: [], errori: [] },
  '/filings/SYN2/activate': { ticker: 'SYN2', esito: 'attivato', profilo_versione: 1, fonte: 'sec' },
  '/filings/SYN3/ai-proposal': { stato: 'done', sha256: 'synthetic-ai-sha-syn3', url: 'https://example.invalid/acme-half-year-2026.pdf',
    tipo: 'semestrale', lingua: 'en', periodo: { inizio: '2026-01-01', fine: '2026-06-30' }, verificate: clone(fixtureData.filingAiSections),
    scartate: [], salvabile: true, motivi: [], avvisi: [], altri: [], modello: 'fixture-model', costo_eur: 0.0041, cached: false },
  // fase F: ricerca dei PDF sul sito su richiesta (gratis, nessuna AI)
  '/filings/SYN3/search-pdf': { ticker: 'SYN3', pdf_ir: null, sito: 'https://www.acme.example/', pagine: 4,
    motivi: ['No periodic report link in the synthetic pages.'] },
  '/filings/SYN3/ai-proposal/accept': { ticker: 'SYN3', esito: 'salvato', versione: 1, sezioni: ['Principal risks', 'Outlook'] },
  '/portfolio/montecarlo/v3': { accepted: true, job_id: 'qa-monte-carlo', synthetic: true },
  '/trade/preview': { valid: true, preview_id: 'qa-trade-preview', synthetic: true },
  '/trade': { accepted: true, trade_id: 599, synthetic: true },
  '/mandato/anteprima': { ok: true, synthetic: true, errori: [], campi_mancanti: [] },
  '/mandato': { ok: true, synthetic: true },
  '/journal': { id: 9991, version: 1, synthetic: true },
  '/journal/910': { id: 910, version: 4, synthetic: true },
  '/decisions/700/update': { ok: true, synthetic: true },
  '/decisions/700/archive': { ok: true, synthetic: true },
  '/db/backups': { ok: true, filename: 'fixture-created-backup.zip', synthetic: true },
};

module.exports = { reads, writes, news, model, detail, now };
