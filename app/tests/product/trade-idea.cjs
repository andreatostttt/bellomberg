const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore } = require('../i18n/_carica.cjs');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const headers = { 'X-BB-Token': 'synthetic-session', 'X-BB-Language': 'it' };
const load = creaCaricatore({ stub: {
  './api': { API_BASE: 'http://synthetic.invalid', requestHeaders: () => headers,
    clearSessionAndReload: () => { throw new Error('unexpected session clear'); } },
} });
const { TradeIdeas, tradeIdeaArtifactBlob, TradeIdeaApiError } = load('lib/tradeIdeas.ts');
const { TradeIdeaResearch, researchArtifactBlob } = load('lib/tradeIdeaResearch.ts');

test('Trade Idea decision cannot enter trade form before final artifacts or after technical failure', () => {
  const { decisioneCompatibile } = load('lib/trade-entry.ts');
  const decision = { ticker: 'TEST.MI', action: 'BUY', status: 'PENDING',
    trade_idea: { technical_status: 'completed', destination_kind: 'dcn', artifacts_ready: true } };
  assert.equal(decisioneCompatibile(decision, 'TEST.MI', 'BUY'), true);
  for (const patch of [{ technical_status: 'incomplete' }, { destination_kind: 'research' },
    { artifacts_ready: false }]) {
    assert.equal(decisioneCompatibile({ ...decision, trade_idea: { ...decision.trade_idea, ...patch } },
      'TEST.MI', 'BUY'), false);
  }
  assert.equal(decisioneCompatibile({ ...decision, trade_idea: null }, 'TEST.MI', 'BUY'), true);
});

async function withFetch(reply, run) {
  const before = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return typeof reply === 'function' ? reply(url, options) : new Response(JSON.stringify(reply), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  };
  try { await run(calls); } finally { global.fetch = before; }
}

test('native continuation confirms spend once; file recovery has no AI or email grant', async () => {
  await withFetch({run_id: 'continued'}, async calls => {
    await TradeIdeas.resume('parent/1', 'intent-unchanged');
    await TradeIdeas.resume('parent/1', 'intent-unchanged');
    await TradeIdeas.recoverDelivery('parent/1');
    assert.equal(calls[0].url, 'http://synthetic.invalid/trade-ideas/runs/parent%2F1/resume');
    assert.deepEqual(JSON.parse(calls[0].options.body), {idempotency_key: 'intent-unchanged', cost_acknowledged: true});
    assert.deepEqual(calls[1], calls[0]);
    assert.equal(calls[2].url, 'http://synthetic.invalid/trade-ideas/runs/parent%2F1/delivery/recover');
    assert.equal(calls[2].options.method, 'POST');
    assert.ok(!calls[2].options.body || calls[2].options.body === '{}');
  });
});

test('recovery panel renders structured remaining work and retains unknown cost without a zero', () => {
  const Panel = load('components/TradeIdeaRecoveryPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Panel, {detail: {
    run: {id: 'preserved-run', ticker: 'SYNTH.X'},
    cost: {charged_usd: '1.234', unknown_reserved_usd: '0.321', remaining_after_holds_usd: null, budget_limit_usd: '5'},
    states: {analysis: 'incomplete', artifacts: 'unavailable'},
    artifact_availability: {status: 'unavailable', reason: 'Synthetic modified PDF'},
    recovery: {checkpoint_available: true, can_continue: false, can_recover_delivery: true,
      blocked_reason: 'unknown provider cost', remaining_work: {remaining: ['capo'], feasibility: 'blocked',
        reason: 'reservation preserved', largest_output_reservation_floor_usd: '0.128'}},
  }, onChanged() { throw new Error('SSR started a run'); }}));
  assert.match(html, /1\.234/); assert.match(html, /0\.321/); assert.match(html, /n\.d\./);
  assert.match(html, /Synthetic modified PDF/); assert.match(html, /reservation preserved/);
  assert.match(html, /0\.128/); assert.doesNotMatch(html, /\[object Object\]/);
});

test('report completion selects the exact paid request only after confirmation and deduplicates clicks', async () => {
  const JSX = require('react/jsx-runtime');
  let index = 0, refIndex = 0;
  const states = [], refs = [], nodes = [], calls = [], changed = [];
  const instrument = kind => (type, props, key) => { nodes.push({type, props}); return JSX[kind](type, props, key); };
  const loader = creaCaricatore({stub: {
    react: {...React, useRef(value) { return refs[refIndex++] ||= {current: value}; },
      useState(value) { const at = index++; if (!(at in states)) states[at] = value; return [states[at], next => { states[at] = next; }]; }},
    'react/jsx-runtime': {...JSX, jsx: instrument('jsx'), jsxs: instrument('jsxs')},
    '@/lib/tradeIdeas': {TradeIdeas: {resume: async (...args) => { calls.push(args); return {run_id: 'child'}; }}},
  }});
  const Panel = loader('components/TradeIdeaRecoveryPanel.tsx').default;
  const render = () => { index = refIndex = 0; nodes.length = 0;
    return renderToStaticMarkup(React.createElement(Panel, {detail: {
      run: {id: 'original', analysis_mode: 'fundamentals_research_v1'}, recovery: {checkpoint_available: true, can_continue: false,
        can_complete_truncated_response: true, truncated_response_recovery: {
          request_id: 'paid-request', desk: 'quant', round_n: 1, replacement_max_tokens: 128000}}},
      onChanged: id => changed.push(id)})); };
  assert.match(render(), /Completa il report interrotto/);
  nodes.find(n => n.type === 'button').props.onClick();
  assert.equal(calls.length, 0);
  render();
  const dialog = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
  assert.equal(dialog.props.open, true);
  assert.deepEqual(dialog.props.rows[1], {k: 'quant R1', v: 'paid-request'});
  dialog.props.onConfirm(); dialog.props.onConfirm();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1); assert.equal(calls[0][0], 'original');
  assert.equal(calls[0][2], 'paid-request'); assert.deepEqual(changed, ['child']);
  await withFetch({run_id: 'child'}, async requests => {
    await TradeIdeas.resume('original', calls[0][1], 'paid-request');
    assert.deepEqual(JSON.parse(requests[0].options.body), {idempotency_key: calls[0][1],
      cost_acknowledged: true, recover_truncated_request_id: 'paid-request'});
  });
});

test('an active run does not present temporary in-flight work as a blocked recovery', () => {
  const Panel = load('components/TradeIdeaRecoveryPanel.tsx').default;
  for (const technical_status of ['accepted', 'running']) {
    const html = renderToStaticMarkup(React.createElement(Panel, {detail: {
      run: {id: 'active', technical_status}, recovery: {checkpoint_available: true,
        can_continue: false, blocked_reason: 'tool_outcome_unresolved'}},
      onChanged() { throw new Error('active SSR started another run'); }}));
    assert.equal(html, '');
  }
});

test('failed research response recovery selects the receipt, confirms once, and preserves the original grant', async () => {
  const JSX = require('react/jsx-runtime');
  let index = 0, refIndex = 0;
  const states = [], refs = [], nodes = [], calls = [];
  const instrument = kind => (type, props, key) => { nodes.push({type, props}); return JSX[kind](type, props, key); };
  const loader = creaCaricatore({stub: {
    react: {...React, useRef(value) { return refs[refIndex++] ||= {current: value}; },
      useState(value) { const at = index++; if (!(at in states)) states[at] = value; return [states[at], next => { states[at] = next; }]; }},
    'react/jsx-runtime': {...JSX, jsx: instrument('jsx'), jsxs: instrument('jsxs')},
    '@/lib/tradeIdeas': {TradeIdeas: {resume: async (...args) => { calls.push(args); return {run_id: 'failed-child'}; }}},
  }});
  const Panel = loader('components/TradeIdeaRecoveryPanel.tsx').default;
  const render = () => { index = refIndex = 0; nodes.length = 0;
    return renderToStaticMarkup(React.createElement(Panel, {detail: {
      run: {id: 'failed-parent', technical_status: 'incomplete', analysis_mode: 'fundamentals_research_v1'}, cost: {budget_limit_usd: '20'},
      recovery: {checkpoint_available: true, can_continue: false, can_retry_failed_response: true,
        failed_response_recovery: {request_id: 'selected-error', desk: 'fundamentals', round_n: 1}}},
      onChanged() {}})); };
  assert.match(render(), /Riprendi la risposta interrotta/);
  nodes.find(n => n.type === 'button').props.onClick();
  assert.equal(calls.length, 0);
  render();
  const dialog = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
  assert.equal(dialog.props.open, true);
  assert.match(dialog.props.intro, /risposta fallita/);
  assert.ok(dialog.props.rows.some(row => row.v === 'selected-error'));
  dialog.props.onConfirm(); dialog.props.onConfirm();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  await withFetch({run_id: 'failed-child'}, async requests => {
    await TradeIdeas.resume(...calls[0]);
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      idempotency_key: calls[0][1], cost_acknowledged: true, recover_failed_request_id: 'selected-error'});
  });
});

test('price refresh is explicit, tied to the selected run, combines with report recovery and deduplicates confirmation', async () => {
  const JSX = require('react/jsx-runtime');
  let index = 0, refIndex = 0;
  const states = [], refs = [], nodes = [], calls = [];
  const instrument = kind => (type, props, key) => { nodes.push({type, props}); return JSX[kind](type, props, key); };
  const loader = creaCaricatore({stub: {
    react: {...React, useRef(value) { return refs[refIndex++] ||= {current: value}; },
      useState(value) { const at = index++; if (!(at in states)) states[at] = value; return [states[at], next => { states[at] = next; }]; }},
    'react/jsx-runtime': {...JSX, jsx: instrument('jsx'), jsxs: instrument('jsxs')},
    '@/lib/tradeIdeas': {TradeIdeas: {resume: async (...args) => { calls.push(args); return {run_id: 'price-child'}; }}},
  }});
  const Panel = loader('components/TradeIdeaRecoveryPanel.tsx').default;
  let runId = 'selected-original';
  const render = () => { index = refIndex = 0; nodes.length = 0;
    return renderToStaticMarkup(React.createElement(Panel, {detail: {
      run: {id: runId, technical_status: 'incomplete', analysis_mode: 'fundamentals_research_v1'}, cost: {budget_limit_usd: '10'},
      recovery: {checkpoint_available: true, can_continue: false, price_refresh_required: true,
        can_continue_with_price_refresh: true, can_complete_truncated_response: true,
        truncated_response_recovery: {request_id: 'selected-paid-response', desk: 'quant', round_n: 1}}},
      onChanged() {}})); };
  assert.match(render(), /sole quotazioni/);
  nodes.find(n => n.type === 'button').props.onClick();
  assert.equal(calls.length, 0);
  render();
  let dialog = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
  assert.equal(dialog.props.open, true);
  assert.match(dialog.props.intro, /rischio/);
  assert.match(dialog.props.intro, /dimensione/);
  assert.ok(dialog.props.rows.some(row => row.v === '10'));
  assert.deepEqual(dialog.props.rows[0], {k: 'ID run', v: 'selected-original'});
  runId = 'another-selected-run';
  render();
  dialog = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
  assert.equal(dialog.props.open, false, 'consent for one selection must not authorize another run');
  nodes.find(n => n.type === 'button').props.onClick();
  render();
  dialog = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
  assert.equal(dialog.props.open, true);
  dialog.props.onConfirm(); dialog.props.onConfirm();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'another-selected-run');
  assert.equal(calls[0][2], 'selected-paid-response');
  assert.equal(calls[0][3], true);
  await withFetch({run_id: 'price-child'}, async requests => {
    await TradeIdeas.resume(...calls[0]);
    assert.deepEqual(JSON.parse(requests[0].options.body), {idempotency_key: calls[0][1],
      cost_acknowledged: true, recover_truncated_request_id: 'selected-paid-response', authorize_price_refresh: true});
    await TradeIdeas.resume('another-selected-run', 'without-refresh', undefined, false);
    assert.deepEqual(JSON.parse(requests[1].options.body), {idempotency_key: 'without-refresh', cost_acknowledged: true});
  });
});

test('price refresh cannot offer continuation when server eligibility is blocked by uncertain work', () => {
  const Panel = load('components/TradeIdeaRecoveryPanel.tsx').default;
  for (const reason of ['unknown_cost', 'tool_outcome_unresolved']) {
    const html = renderToStaticMarkup(React.createElement(Panel, {detail: {
      run: {id: 'blocked', technical_status: 'incomplete', analysis_mode: 'fundamentals_research_v1'}, recovery: {
        can_continue: false, price_refresh_required: true, can_continue_with_price_refresh: false,
        can_complete_truncated_response: false, blocked_reason: reason}},
      onChanged() { throw new Error('blocked continuation started'); }}));
    assert.doesNotMatch(html, /class="ti-button ti-primary"/);
    assert.match(html, new RegExp(reason));
  }
});

test('price-only continuation is offered without enabling a report recovery', () => {
  const Panel = load('components/TradeIdeaRecoveryPanel.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Panel, {detail: {
    run: {id: 'prices-only', technical_status: 'interrupted', analysis_mode: 'fundamentals_research_v1'}, recovery: {
      can_continue: false, price_refresh_required: true, can_continue_with_price_refresh: true}},
    onChanged() { throw new Error('SSR started a continuation'); }}));
  assert.match(html, /class="ti-button ti-primary"/);
  assert.match(html, /Continua le fasi mancanti/);
  assert.match(html, /sole quotazioni/);
  assert.doesNotMatch(html, /Completa il report interrotto/);
});

test('weekly recovery selects one memo, confirms paid continuation, and keeps file recovery separate', async () => {
  const JSX = require('react/jsx-runtime');
  const { ambienteBrowser } = require('../i18n/_carica.cjs');
  ambienteBrowser();
  for (const files of [false, true]) {
    let index = 0, refIndex = 0;
    const states = [], refs = [], effects = [], nodes = [], calls = [], started = [];
    const instrument = kind => (type, props, key) => { nodes.push({type, props}); return JSX[kind](type, props, key); };
    const loader = creaCaricatore({stub: {
      react: {...React, useRef(value) { return refs[refIndex++] ||= {current: value}; },
        useState(value) { const at = index++; if (!(at in states)) states[at] = value; return [states[at], next => { states[at] = next; }]; },
        useEffect(fn) { effects.push(fn); }},
      'react/jsx-runtime': {...JSX, jsx: instrument('jsx'), jsxs: instrument('jsxs')},
      '@/lib/api': {Bellomberg: {
        weeklyRecoveries: async () => ({runs: [{memo_id: 42, status: 'incomplete', resume_available: true, delivery_recovery_available: true}]}),
        recoverConsigliere: async (id, deliveryOnly) => { calls.push([id, deliveryOnly]); return {task_id: 'selected-task'}; },
      }},
    }});
    const Panel = loader('components/WeeklyRecoveryPanel.tsx').default;
    const render = () => { index = refIndex = 0; nodes.length = effects.length = 0;
      return renderToStaticMarkup(React.createElement(Panel, {disabled: false, onStarted: id => started.push(id)})); };
    render(); nodes.find(n => n.type === 'details').props.onToggle({currentTarget: {open: true}});
    render(); const cleanups = effects.map(fn => fn());
    await new Promise(resolve => setImmediate(resolve)); cleanups.forEach(fn => typeof fn === 'function' && fn());
    render(); nodes.find(n => n.type === 'select').props.onChange({target: {value: '42'}});
    render(); const buttons = nodes.filter(n => n.type === 'button');
    buttons[files ? 1 : 0].props.onClick();
    if (!files) {
      assert.equal(calls.length, 0, 'opening confirmation must not send a paid request');
      render(); const confirm = nodes.find(n => typeof n.type === 'function' && n.type.name === 'ConfirmDialog');
      confirm.props.onConfirm(); confirm.props.onConfirm();
    }
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(calls, [[42, files]]);
    assert.deepEqual(started, ['selected-task']);
  }
});

test('preflight and confirmed start send the exact candidate, PM view, source and explicit USD cap', async () => {
  await withFetch({ ok: true, identity: { ticker: 'TEST.MI' } }, async calls => {
    await TradeIdeas.preflight('TEST.MI', 'Una tesi sintetica', 'favorite_note', 12.5);
    await TradeIdeas.start('TEST.MI', 'Una tesi sintetica', 'favorite_note', 12.5, 'same-intent-1', 'a'.repeat(64));
    assert.equal(calls.length, 2);
    assert.equal(calls[0].url, 'http://synthetic.invalid/trade-ideas/preflight');
    assert.deepEqual(JSON.parse(calls[0].options.body), {
      ticker: 'TEST.MI', pm_view: 'Una tesi sintetica', view_source: 'favorite_note', budget_limit_usd: 12.5,
    });
    assert.deepEqual(JSON.parse(calls[1].options.body), {
      ticker: 'TEST.MI', pm_view: 'Una tesi sintetica', view_source: 'favorite_note', budget_limit_usd: 12.5,
      idempotency_key: 'same-intent-1', cost_acknowledged: true,
      authorization: {accepted: true, source_fingerprint: 'a'.repeat(64), activities: ['committee'], max_revision_rounds: 0},
    });
    assert.deepEqual(calls[1].options.headers, { ...headers, 'Content-Type': 'application/json' });
  });
});

test('run history, detail, stop and failed-email retry address one exact run', async () => {
  await withFetch({ runs: [] }, async calls => {
    await TradeIdeas.list({ ticker: 'TEST.MI', status: 'failed', limit: 10, offset: 20 });
    await TradeIdeas.detail('run/with space');
    await TradeIdeas.stop('run/with space');
    await TradeIdeas.retryEmail('run/with space');
    assert.equal(calls[0].url, 'http://synthetic.invalid/trade-ideas/runs?ticker=TEST.MI&status=failed&limit=10&offset=20');
    assert.equal(calls[1].url, 'http://synthetic.invalid/trade-ideas/runs/run%2Fwith%20space');
    assert.equal(calls[2].url, 'http://synthetic.invalid/trade-ideas/runs/run%2Fwith%20space/stop');
    assert.equal(calls[3].url, 'http://synthetic.invalid/trade-ideas/runs/run%2Fwith%20space/email/retry');
    assert.equal(calls[2].options.method, 'POST');
    assert.equal(calls[3].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[3].options.body), { acknowledge_uncertain: false });
    await TradeIdeas.retryEmail('run/with space', true);
    assert.deepEqual(JSON.parse(calls[4].options.body), { acknowledge_uncertain: true });
  });
});

test('PM documentary evidence stays separate from the thesis and is pinned through preflight and start', async () => {
  const documents = [{url:'https://issuer.example/annual.pdf', published_at:'2026-03-10',
    publication_quote:'Published 10 March 2026', report_date:'2025-12-31',
    report_date_quote:'Year ended 31 December 2025', issuer_quote:'Synthetic Issuer PLC'}];
  const abort = new AbortController();
  await withFetch({ok:true}, async calls => {
    await TradeIdeas.preflight('TEST.MI','A thesis, not documentary evidence','manual',12.5,abort.signal,documents);
    await TradeIdeas.start('TEST.MI','A thesis, not documentary evidence','manual',12.5,'same-intent','b'.repeat(64),documents);
    for (const call of calls) {
      const body = JSON.parse(call.options.body);
      assert.deepEqual(body.document_sources,documents);
      assert.equal(body.pm_view,'A thesis, not documentary evidence');
      assert.equal('source_report' in body,false);
    }
    assert.equal(calls[0].options.signal,abort.signal);
    assert.equal(JSON.parse(calls[1].options.body).authorization.source_fingerprint,'b'.repeat(64));
  });
});

test('artifact download is authenticated and exact; HTTP failures preserve server reason', async () => {
  await withFetch((_url, _options) => new Response('pdf bytes', { status: 200 }), async calls => {
    const blob = await tradeIdeaArtifactBlob('run-1', 'pdf-1');
    assert.equal(await blob.text(), 'pdf bytes');
    assert.equal(calls[0].url, 'http://synthetic.invalid/trade-ideas/runs/run-1/artifacts/pdf-1');
    assert.deepEqual(calls[0].options.headers, headers);
  });
  await withFetch(() => new Response(JSON.stringify({ detail: { message: 'hash changed' } }), { status: 409 }), async () => {
    await assert.rejects(tradeIdeaArtifactBlob('run-1', 'pdf-1'), error =>
      error instanceof TradeIdeaApiError && error.status === 409 && error.message === 'hash changed');
  });
});

test('research actions pin generation and retry identity; malformed reads fail visibly', async () => {
  await withFetch({id: 7, data: {status: 'ready'}}, async calls => {
    await TradeIdeaResearch.act('run/1', 'generation-exact', 'simulate', {changes: [{driver: 'wacc', value: 0.12}]}, 'request-exact');
    assert.equal(calls[0].url, 'http://synthetic.invalid/trade-ideas/runs/run%2F1/workspace/actions');
    assert.deepEqual(JSON.parse(calls[0].options.body), {kind:'simulate', generation_id:'generation-exact', request_id:'request-exact', data:{changes:[{driver:'wacc',value:0.12}]}});
    await assert.rejects(TradeIdeaResearch.view('run/1'), /incomplete/);
  });
  await withFetch(() => new Response('exact workbook bytes'), async calls => {
    const blob = await researchArtifactBlob({artifact:{download_url:'/trade-ideas/runs/run-1/workspace/artifacts/7'}});
    assert.equal(await blob.text(), 'exact workbook bytes');
    assert.deepEqual(calls[0].options.headers, headers);
  });
});
