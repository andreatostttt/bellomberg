const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const {renderToStaticMarkup} = require('react-dom/server');
const {creaCaricatore, ambienteBrowser} = require('./_carica.cjs');
ambienteBrowser();

const load = creaCaricatore({stub: {
  '@/lib/api': {Bellomberg: {}, API_BASE: 'http://synthetic.invalid'},
  '@/lib/tradeIdeas': {TradeIdeas: {}},
  '@/lib/tradeIdeaResearch': {TradeIdeaResearch: {}},
}});
const language = load('i18n/lingua.ts');

test('new Trade Idea source readiness authorizes research and memo/PDF without Excel work', () => {
  assert.ok(fs.existsSync(path.join(__dirname, '../../src/components/TradeIdeaSourceReadiness.tsx')),
    'mode-aware research readiness component missing');
  const Component = load('components/TradeIdeaSourceReadiness.tsx').default;
  for (const lang of ['it', 'en']) {
    language.impostaLinguaCorrente(lang);
    const html = renderToStaticMarkup(React.createElement(Component, {preflight: {ok: true,
      analysis_mode: 'fundamentals_research_v1', source_qualification: {status: 'research_required', reasons: []},
      preparation: {required: false, paid: false, status: 'not_required'}}}));
    assert.match(html, /memo.*PDF/i);
    assert.doesNotMatch(html, /Excel preparation|Preparazione dell.Excel|at most one revision|al massimo una revisione|Model is available|Modello già disponibile/);
    assert.match(html, lang === 'it' ? /consensus/ : /consensus/);
  }
});

test('saved research detail does not mount model revisions and keeps committee conclusions', () => {
  const Detail = load('pages/TradeIdeaPage.tsx').TradeIdeaRunDetail;
  assert.equal(typeof Detail, 'function', 'mode-aware run detail missing');
  language.impostaLinguaCorrente('en');
  const html = renderToStaticMarkup(React.createElement(Detail, {detail: {
    run: {id: 'saved-research', ticker: 'SYNTH.X', technical_status: 'completed', analysis_mode: 'fundamentals_research_v1'},
    result: {judgment: 'watch', summary: 'Original conclusion', valuation: {status: 'not_required', reason: 'No workbook contract'},
      dossier: [{key: 'assumptions', title: 'Original assumptions', paragraphs: ['Original assumption']}], evidence: []},
    artifacts: [{id: 'pdf', kind: 'pdf', name: 'Original memo.pdf', status: 'ready'}],
  }, now: 0, onStop() {}, onReuse() {}, onRetryEmail() {}, onDownload() {}, stopping: false, retryingEmail: false}));
  assert.match(html, /Original conclusion/); assert.match(html, /Original assumption/); assert.match(html, /Original memo.pdf/);
  assert.doesNotMatch(html, /ti-research-title|Model workspace|No workbook contract|not_required/);
});

test('research recovery remains available while legacy runs offer saved results only', () => {
  const Recovery = load('components/TradeIdeaRecoveryPanel.tsx').default;
  language.impostaLinguaCorrente('en');
  const render = mode => renderToStaticMarkup(React.createElement(Recovery, {detail: {
    run: {id: 'saved', ticker: 'SYNTH.X', technical_status: 'incomplete', analysis_mode: mode},
    recovery: {checkpoint_available: true, can_recover_delivery: true, can_continue: true,
      can_complete_truncated_response: true, truncated_response_recovery: {request_id:'old-request',desk:'quant',round_n:1},
      price_refresh_required:true, can_continue_with_price_refresh:true},
  }, onChanged() {}}));
  const research = render('fundamentals_research_v1');
  assert.match(research, /Recover memo and PDF/); assert.doesNotMatch(research, /Excel/);
  assert.match(research, /class="ti-button ti-primary"/);
  const legacy = render(undefined);
  assert.match(legacy, /Archived run/); assert.match(legacy, /Recover saved files/);
  assert.doesNotMatch(legacy, /class="ti-button ti-primary"|Complete the interrupted report|Recover memo, PDF and Excel|Refresh portfolio prices only/);
});

test('legacy detail retains its recorded contract and downloads without mounting model actions', () => {
  const Detail = load('pages/TradeIdeaPage.tsx').TradeIdeaRunDetail;
  const {MemoryRouter} = require('react-router-dom');
  language.impostaLinguaCorrente('en');
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Detail, {detail: {
    run: {id:'historical',ticker:'SYNTH.X',technical_status:'completed'},
    result: {summary:'Original historical conclusion',valuation:{status:'ready',reason:'Original valuation contract'}},
    artifacts: [{id:'frozen-xlsx',kind:'xlsx',name:'Original frozen.xlsx',status:'ready'}],
  },now:0,onStop(){},onReuse(){},onRetryEmail(){},onDownload(){},stopping:false,retryingEmail:false})));
  assert.match(html,/Original historical conclusion/); assert.match(html,/Original valuation contract/);
  assert.match(html,/Original frozen.xlsx/); assert.match(html,/Archived run/);
  assert.match(html,/href="\/fundamentals"/);
  assert.doesNotMatch(html,/ti-research-title|Simulate|Compare models|Refresh documents|Acquire sources/);
});
