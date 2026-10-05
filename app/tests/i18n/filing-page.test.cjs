// 03/10/2026 (filing fase E): pagina Filing renderizzata lato server con stato seminato per nome.
// Dati sintetici (Nova/Kore/Acme/Orsa, CIK 000999xxxx, LEI 999900…).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
globalThis.window = globalThis.window || { setTimeout: () => 0, clearTimeout() {}, dispatchEvent() {}, addEventListener() {}, removeEventListener() {} };
const source = fs.readFileSync(path.resolve(__dirname, '../../src/pages/FilingPage.tsx'), 'utf8');
const tree = ts.createSourceFile('FilingPage.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const page = tree.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'FilingPage');
const stateNames = [];
(function states(node) {
  if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name) && node.initializer && ts.isCallExpression(node.initializer)
    && node.initializer.expression.getText(tree) === 'useState') stateNames.push(node.name.elements[0].name.text);
  ts.forEachChild(node, states);
})(page);

const JSXR = require('react/jsx-runtime');
function filingPage(seed = {}, responses = {}, query = '') {
  const azioni = {};
  const cattura = f => (type, props, ...rest) => { if (props && props['data-filing-azione'] && typeof props.onClick === 'function') azioni[props['data-filing-azione']] = props.onClick; return f(type, props, ...rest); };
  let index = 0, refIndex = 0, effectIndex = 0;
  const values = {}, refs = [], effectDeps = [], effects = [], requests = [];
  for (const [name, value] of Object.entries(seed)) { assert.ok(stateNames.includes(name), name); values[stateNames.indexOf(name)] = value; }
  const api = new Proxy({}, { get: (_t, name) => async (...args) => {
    requests.push(name);
    if (name in responses) return responses[name](...args);
    return new Promise(() => {});
  } });
  const load = creaCaricatore({ stub: {
    react: { ...React, useMemo: f => f(), useCallback: f => f, useLayoutEffect() {},
      useState(initial) { const at = index++; if (!(at in values)) values[at] = typeof initial === 'function' ? initial() : initial;
        return [values[at], v => { values[at] = typeof v === 'function' ? v(values[at]) : v; }]; },
      useRef(current) { return refs[refIndex++] ||= { current }; },
      useEffect(effect, deps) { const at = effectIndex++, before = effectDeps[at];
        if (!before || !deps || deps.some((v, i) => !Object.is(v, before[i]))) effects.push(effect);
        effectDeps[at] = deps; },
    },
    'react-router-dom': { useSearchParams: () => [new URLSearchParams(query), () => {}] },
    '@/lib/api': { Bellomberg: api },
    // G9b 04/10: i pulsanti con data-filing-azione restano cliccabili dal test (onClick catturato)
    'react/jsx-runtime': { ...JSXR, jsx: cattura(JSXR.jsx), jsxs: cattura(JSXR.jsxs) },
  } });
  const language = load('i18n/lingua.ts'), Component = load('pages/FilingPage.tsx').default;
  return {
    requests,
    clic(azione) { const h = azioni[azione]; assert.ok(h, 'azione ' + azione); return h(); },
    values, stateNames,
    render(lang) { index = refIndex = effectIndex = 0; effects.length = 0; for (const k of Object.keys(azioni)) delete azioni[k]; language.impostaLinguaCorrente(lang);
      return renderToStaticMarkup(React.createElement(Component)); },
    async effects() { for (const e of effects.splice(0)) e(); await new Promise(r => setImmediate(r)); },
  };
}

const T = (ticker, nome, stato, gruppo_ui, extra = {}) => ({ ticker, nome, gruppo: 1, stato_riga: `${ticker} · x`, fonte: 'SEC 10-Q CIK 0009990001',
  ultimo_confronto: '2026-10-03T08:10:00', novita: stato === 'novita', profilo: !['senza_fonte', 'da_confermare', 'non_attivo', 'escluso'].includes(stato),
  escluso: stato === 'escluso', stato, gruppo_ui, documento: 'SEC 10-Q', cambiamenti: 0, nel_contesto: true, run_id: 41, ...extra });
const titoli = [
  T('NOVA.DE', 'Nova Semiconductors', 'novita', 'novita', { cambiamenti: 3 }),
  T('KORE.DE', 'Kore Mining plc', 'da_confermare', 'da_sistemare', { attivazione: { esito: 'da_confermare', candidati: 2 } }),
  T('BRK.DE', 'Brakel Industrie', 'errore', 'da_sistemare', { ultimo_errore: { at: '2026-10-03T08:10:00', reason: 'sezione «risk» non trovata nel documento' } }),
  T('LUMO.PA', 'Lumo Energie', 'proposta_ai', 'da_sistemare', { documento: 'ESEF annuale', proposta_ai: { sha256: 'a'.repeat(64), url: 'https://ir.example.invalid/h1.pdf', salvabile: true, verificate: 2 } }),
  T('ORSA.MI', 'Orsa Banca', 'invariato', 'aggiornati', { documento: 'ESEF annuale' }),
  T('PIXL.DE', 'Pixel Cloud', 'in_corso', 'aggiornati', { run_attivo: { id: 9, started_at: '2026-10-03T14:02:00', trigger: 'prerun' } }),
  T('QX7.F', 'Quanta X7 Fund', 'senza_fonte', 'senza_fonte', { ultimo_confronto: null, documento: null }),
  T('ZEN.F', 'Zenith Holdings', 'escluso', 'senza_fonte', { ultimo_confronto: null }),
];
const overview = (extra = {}) => ({ ambito: 'portafoglio', titoli, copertura: { totale: 8, con_confronto: 5, aggiornati: 4, non_aggiornati: 1, senza_confronto: 0, senza_profilo: 2, esclusi: 1 },
  contesto: { caratteri: 9840, budget: 14000, omessi_totali: 0 }, aggiornamento: { status: 'success', finished_at: '2026-10-03T08:10:00' },
  controllo_giornaliero: { attivo: true, forzato_spento_da_env: false, prossimo_at: null, ultimo_fine_at: '2026-10-03T08:10:00' }, ...extra });
const cit = (testo, sezione, extra = {}) => ({ testo, sezione, url: 'https://www.sec.gov/Archives/edgar/data/9990001/x.htm', ...extra });
const detail = { id: 41, ticker: 'NOVA.DE', status: 'ok', trigger: 'schedule', started_at: '2026-10-03T08:09:00', finished_at: '2026-10-03T08:10:00',
  result: { stato: 'ok', variante: 'trimestrale', copertura: { stato: 'completa' },
    coppia: { prima: { metadati: { periodo_fine: '2025-09-30' } }, dopo: { metadati: { periodo_fine: '2026-09-30' } } },
    confronto_corrente: { stato: 'ok', sezioni_confrontate: ['rischi', 'gestione'], cambiamenti: [
      { tipo: 'aggiunto', dopo: cit('Nuove restrizioni all\'esportazione potrebbero ridurre le vendite.', 'Fattori di rischio · Item 1A', { pagine_fisiche: [41] }) },
      { tipo: 'modificato', prima: cit('Il margine rimane stabile.', 'Gestione · MD&A'), dopo: cit('Il margine si riduce leggermente.', 'Gestione · MD&A') },
      { tipo: 'rimosso', prima: cit('Il procedimento sul brevetto è in fase istruttoria.', 'Contenziosi · Legal Proceedings') }] },
    numeri: { stato: 'ok', valuta: 'USD', fonte: 'SEC companyfacts', voci: [{ voce: 'ricavi', prima: 8.71e9, dopo: 10.02e9, delta_pct: 15.0 }, { voce: 'scorte', prima: 8.4e9, dopo: 9.47e9, delta_pct: 12.7 }] } } };
const listing = (extra = {}) => ({ ticker: 'NOVA.DE', status: 'ok', profile: { ticker: 'NOVA.DE', version: 2, enabled: true, interval_hours: 24, qualitative_enabled: false,
  profile: { tipo: 'trimestrale', cik: '0009990001', origine_collegamento: 'ticker', sezioni: { rischi: {}, gestione: {} } } }, runs: [{ id: 41, ticker: 'NOVA.DE', status: 'ok', started_at: '2026-10-03T08:09:00' }],
  active_run: null, ultimo_completo: { id: 41, ticker: 'NOVA.DE', status: 'ok' }, ...extra });
const preview = { ticker: 'NOVA.DE', testo: 'NOVA.DE · SEC 10-Q CIK 0009990001 · trimestre al 30/09/2026 vs 30/09/2025 · aggiornato · NOVITÀ\nnumeri ricavi +15,0% · scorte +12,7%\n+ rischi «Nuove restrizioni…» [C1-dopo]\n− contenziosi «Il procedimento…» [C3-prima]',
  caratteri: 260, omessi: 0, contesto_totale: { caratteri: 9840, budget: 14000 }, nota: '', in_evidenza: ['C3', 'C1'] };

test('stati della pagina: nessun useState doppio e dizionari IT/EN con le stesse chiavi', () => {
  assert.equal(new Set(stateNames).size, stateNames.length);
  const load = creaCaricatore();
  const lingua = load('i18n/lingua.ts'), p = load('pages/filing/parole.ts');
  lingua.impostaLinguaCorrente('it'); const it = p.parole();
  lingua.impostaLinguaCorrente('en'); const en = p.parole();
  const chiavi = o => Object.keys(o).sort();
  assert.deepEqual(chiavi(it), chiavi(en));
  for (const k of ['groups', 'tag', 'sub', 'kind', 'figVoce', 'figPeriodo', 'triggers']) assert.deepEqual(chiavi(it[k]), chiavi(en[k]), k);
});

test('cifre in scala breve: numero dalla logica pura, suffisso dal catalogo nella lingua della pagina', () => {
  const load = creaCaricatore();
  const lingua = load('i18n/lingua.ts'), p = load('pages/filing/parole.ts'), { scalaBreve } = load('pages/filing/logica.ts');
  lingua.impostaLinguaCorrente('it'); const it = p.parole();
  lingua.impostaLinguaCorrente('en'); const en = p.parole();
  assert.equal(it.cifraBreve(scalaBreve(10_020_000_000, 'it')), '10,02 Mld');
  assert.equal(it.cifraBreve(scalaBreve(488_000_000, 'it')), '488 Mln');
  assert.equal(it.cifraBreve(scalaBreve(12_500, 'it')), '12,50 mila');
  assert.equal(en.cifraBreve(scalaBreve(2_100_000, 'en')), '2.10M');
  assert.equal(en.cifraBreve(scalaBreve(10_020_000_000, 'en')), '10.02B');
  assert.equal(en.cifraBreve(scalaBreve(512, 'en')), '512');
  assert.equal(it.cifraBreve(scalaBreve(null, 'it')), '—');
});

test('elenco in quattro gruppi con righe cliccabili, striscia e budget del contesto in IT ed EN', () => {
  const view = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail, preview });
  const it = view.render('it'), en = view.render('en');
  for (const g of ['novita', 'da_sistemare', 'aggiornati', 'senza_fonte']) assert.match(it, new RegExp(`data-filing-gruppo="${g}"`));
  assert.match(it, /Con novità/); assert.match(it, /Da sistemare/); assert.match(en, /To fix/);
  assert.match(it, /data-filing-ticker="NOVA.DE" data-filing-stato="novita"/);
  assert.match(it, /3 nuovi/); assert.match(en, /3 new/);
  assert.match(it, /Contesto per il Comitato/); assert.match(en, /Context for the committee/);
  assert.match(it, /9\.840/); assert.match(en, /9,840/);
  assert.match(it, /Rivedi 1 collegamento/); assert.match(en, /Review 1 link/);
  assert.match(it, /role="switch" aria-checked="true"/);
  assert.match(it, /Attiva i mancanti/);
});

test('dettaglio aggiornato: tre card con cambiamenti citati, numeri e testo esatto del Consigliere', () => {
  const html = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail, preview }).render('it');
  assert.match(html, /data-filing-centro="cambiamenti"/);
  assert.match(html, /Q3 2026 vs Q3 2025/);
  // in evidenza = ordine del contesto
  assert.ok(html.indexOf('data-filing-cambiamento="C3"') < html.indexOf('data-filing-cambiamento="C1"'));
  assert.doesNotMatch(html, /data-filing-cambiamento="C2"/);
  assert.match(html, /C1-dopo/); assert.match(html, /p\. 41/);
  assert.match(html, /10,02 Mld/); assert.match(html, /\+12,7%/); assert.match(html, /fl-d is-dn/); // scorte che salgono
  assert.match(html, /data-filing-testo="1"/); assert.match(html, /Nuove restrizioni…/);
  assert.match(html, /Verifica ora/);
  const tutti = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail, preview, filtro: 'tutti' }).render('en');
  assert.match(tutti, /data-filing-cambiamento="C2"/); assert.match(tutti, /<del>/); assert.match(tutti, /<ins>/);
  assert.match(tutti, /Changes in the text/);
});

test('revisione finale: «In evidenza» solo per il run citato, Scollega solo per collegamenti automatici, costo AI su Riprova', () => {
  const altro = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 40, detail, preview }).render('it');
  assert.doesNotMatch(altro, /In evidenza: gli stessi cambiamenti/); // ID del contesto di un altro run: niente evidenza
  const manuale = listing({ profile: { ...listing().profile, profile: { ...listing().profile.profile, origine_collegamento: 'manuale' } } });
  assert.doesNotMatch(filingPage({ overview: overview(), sel: 'NOVA.DE', listing: manuale, profiloAperto: true }).render('it'), /Annulla il collegamento/);
  const conAi = listing({ ticker: 'BRK.DE', profile: { ...listing().profile, qualitative_enabled: true } });
  const err = filingPage({ overview: overview(), sel: 'BRK.DE', listing: conAi }).render('it');
  assert.match(err, /Riprova · giudizio AI a pagamento/); assert.doesNotMatch(err, />Riprova<\/button>/);
  const senza = filingPage({ overview: overview(), sel: 'QX7.F' }).render('it');
  assert.match(senza, /data-filing-azione="profilo"/); // profilo JSON scrivibile anche senza profilo
});

test('ogni stato mostra la sua card e la sua azione', () => {
  const casi = [
    ['PIXL.DE', 'in-corso', {}, /Controllo in corso/],
    ['BRK.DE', 'errore', {}, /data-filing-rimedio="profilo"/],
    ['KORE.DE', 'collegamento', { proposta: { ticker: 'KORE.DE', profilo_attivo: false, escluso: false, preferita: 'sec',
      sec: { stato: 'ambiguo', motivo: '', candidati: [{ cik: '0009990011', ticker: 'KORE', nome: 'Kore Mining plc', origine: 'nome' }, { cik: '0009990012', ticker: 'KOREY', nome: 'Kore Mining Ltd', origine: 'nome_simile' }] } }, scelta: '0009990011' }, /data-filing-candidato="0009990012"/],
    ['QX7.F', 'senza-fonte', {}, /data-filing-azione="sito-ir"/],
    ['ZEN.F', 'escluso', {}, /data-filing-azione="includi"/],
    ['LUMO.PA', 'proposta-ai', {}, /data-filing-input="ai-url"/],
  ];
  for (const [sel, centro, extra, segno] of casi) {
    const html = filingPage({ overview: overview(), sel, listing: listing({ ticker: sel }), ...extra }).render('it');
    assert.match(html, new RegExp(`data-filing-centro="${centro}"`), sel);
    assert.match(html, segno, sel);
  }
  const ingl = filingPage({ overview: overview(), sel: 'BRK.DE', listing: listing({ ticker: 'BRK.DE' }) }).render('en');
  assert.match(ingl, /The check failed/); assert.match(ingl, /Advanced profile/);
});

test('proposta AI: stima gratis, pulsante col costo massimo, verificate e scartate, salva o variante, riprova', () => {
  const base = { overview: overview(), sel: 'LUMO.PA', aiUrl: 'https://ir.example.invalid/h1.pdf' };
  const stima = { stato: 'ok', ticker: 'LUMO.PA', url: base.aiUrl, sha256: 'a'.repeat(64), pagine: 42, caratteri_input: 21000, righe: 300,
    pagine_indice: [2], troncato: 0, cache: false, modello: 'synthetic/model', token_input_stimati: 8610, costo_max_eur: 0.004 };
  const s = filingPage({ ...base, aiStima: stima }).render('it');
  assert.match(s, /Proponi con AI · max € 0,0040/); assert.match(s, /42 pagine/);
  const esito = { stato: 'done', sha256: 'a'.repeat(64), url: base.aiUrl, tipo: 'semestrale', salvabile: true, modello: 'synthetic/model', costo_eur: 0.0024,
    verificate: [{ nome: 'rischi', inizio: 'Principali rischi', fine: 'Fatti di rilievo', caratteri: 900, pagine: [18, 22], anteprima: '' },
      { nome: 'gestione', inizio: 'Andamento della gestione', fine: 'Evoluzione', caratteri: 900, pagine: [6, 17], anteprima: '' }],
    scartate: [{ nome: 'prospettive', motivo: 'fine non trovata' }], avvisi: ['periodo: regola di ripiego'] };
  const nuovo = filingPage({ ...base, aiEsito: esito }).render('it');
  assert.match(nuovo, /Salva le 2 sezioni valide/); assert.match(nuovo, /data-filing-scartata="prospettive"/); assert.match(nuovo, /p\. 18–22/);
  assert.match(nuovo, /regola di ripiego/);
  const esef = filingPage({ ...base, aiEsito: esito, listing: listing({ ticker: 'LUMO.PA', profile: { version: 1, enabled: true, interval_hours: 24, qualitative_enabled: false, profile: { esef_modo: 'blocchi' } } }) }).render('it');
  assert.match(esef, /Aggiungi come variante semestrale/);
  const sost = filingPage({ ...base, aiEsito: esito, aiSostituisci: true }).render('en');
  assert.match(sost, /Replace the profile/);
  const rip = filingPage({ ...base, aiEsito: { stato: 'error', riprovabile: true, dettaglio: 'risposta illeggibile' } }).render('it');
  assert.match(rip, /Riprova \(nuovo costo\)/);
});

test('preferiti: il Consigliere non vede il titolo; attiva i mancanti resta del portafoglio', () => {
  const fav = overview({ ambito: 'preferiti', titoli: [T('ACME.MI', 'Acme', 'aggiornato', 'aggiornati', { nel_contesto: false })] });
  const html = filingPage({ ambito: 'preferiti', overview: fav, sel: 'ACME.MI', listing: listing({ ticker: 'ACME.MI' }) }).render('it');
  assert.match(html, /Non è in portafoglio: il Consigliere non vede questo titolo/);
  assert.match(html, /data-filing-azione="attiva-mancanti" disabled=""/);
});

test('sentinella: caricamento, polling e titolo scelto in automatico non chiamano AI, verifica o ricerca dei collegamenti', async () => {
  const ok = v => async () => v;
  const view = filingPage({}, {
    filingOverviewAmbito: ok(overview()), filingList: ok(listing({ ticker: 'KORE.DE', profile: null })), filingContextPreview: ok(preview),
    filingRun: ok(detail), filingAiSaved: ok({ stato: 'done', sha256: 'a'.repeat(64), url: 'https://ir.example.invalid/h1.pdf' }),
  });
  for (let i = 0; i < 4; i++) { view.render('it'); await view.effects(); }
  const vietate = ['filingAiProposal', 'filingAiEstimate', 'filingAiAccept', 'filingRefresh', 'filingActivate', 'filingActivateMissing',
    'filingProposal', 'filingReject', 'filingUnlink', 'filingAutoRefresh', 'filingExclude', 'filingSaveProfile', 'filingSearchPdf'];
  assert.deepEqual(view.requests.filter(n => vietate.includes(n)), []);
  assert.ok(view.requests.includes('filingOverviewAmbito'));
  // ?t=KORE.DE (link dal riepilogo) e' una scelta dell'utente: la ricerca gratuita dei candidati parte, mai l'AI.
  const daLink = filingPage({}, { filingOverviewAmbito: ok(overview()), filingList: ok(listing({ ticker: 'KORE.DE', profile: null })) }, 't=KORE.DE');
  for (let i = 0; i < 3; i++) { daLink.render('it'); await daLink.effects(); }
  assert.equal(daLink.requests.filter(n => n === 'filingProposal').length, 1);
  assert.deepEqual(daLink.requests.filter(n => vietate.includes(n) && n !== 'filingProposal'), []);
});

// Parità col vecchio pannello del titolo (filing-diff.test.cjs, rimosso il 03/10/2026).
const kore = (sec, esef, preferita) => ({ ticker: 'KORE.DE', profilo_attivo: false, escluso: false, preferita, sec, ...(esef ? { esef } : {}) });
const due = { stato: 'ambiguo', motivo: '', candidati: [{ cik: '0009990011', ticker: 'KORE', nome: 'Kore Mining plc', origine: 'nome' }, { cik: '0009990012', ticker: 'KOREY', nome: 'Kore Mining Ltd', origine: 'nome_simile' }] };
const leiDue = { stato: 'ambiguo', motivo: '', candidati: [{ lei: '999900KORE00000000001', nome: 'Kore Mining SE', origine: 'nome' }, { lei: '999900KORE00000000002', nome: 'Kore Mining AG', origine: 'nome_simile' }] };

test('parità: un collegamento ambiguo vuole una scelta esplicita, unico è preselezionato', async () => {
  const ok = v => async () => v;
  const amb = filingPage({ overview: overview(), sel: 'KORE.DE' }, { filingProposal: ok(kore(due, null, 'sec')), filingList: ok(listing({ ticker: 'KORE.DE', profile: null })) }, 't=KORE.DE');
  for (let i = 0; i < 3; i++) { amb.render('it'); await amb.effects(); }
  const html = amb.render('it');
  assert.match(html, /data-filing-azione="conferma" disabled=""/);
  assert.doesNotMatch(html, /checked=""/);
  const uno = { stato: 'univoco', motivo: '', candidati: [due.candidati[0]] };
  const u = filingPage({ overview: overview(), sel: 'KORE.DE' }, { filingProposal: ok(kore(uno, null, 'sec')), filingList: ok(listing({ ticker: 'KORE.DE', profile: null })) }, 't=KORE.DE');
  for (let i = 0; i < 3; i++) { u.render('it'); await u.effects(); }
  assert.doesNotMatch(u.render('it'), /data-filing-azione="conferma" disabled=""/);
});

test('parità: preferita esef mostra i LEI, preferita sec tiene i CIK anche con candidati ESEF', () => {
  const esef = filingPage({ overview: overview(), sel: 'KORE.DE', proposta: kore({ stato: 'ambiguo', motivo: '', candidati: [{ ...due.candidati[1] }] }, leiDue, 'esef') }).render('it');
  assert.match(esef, /data-filing-candidato="999900KORE00000000001"/); assert.doesNotMatch(esef, /data-filing-candidato="0009990012"/);
  assert.match(esef, /emittenti ESEF/);
  const sec = filingPage({ overview: overview(), sel: 'KORE.DE', proposta: kore(due, leiDue, 'sec') }).render('en');
  assert.match(sec, /data-filing-candidato="0009990011"/); assert.doesNotMatch(sec, /data-filing-candidato="999900KORE00000000001"/);
});

test('parità: controllo leggero dichiarato, errore dopo il confronto con motivo e rimedio, giudizio AI nel profilo', () => {
  const leggero = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing({ ultimo_controllo: { at: '2026-10-03T08:10:00', esito: 'nessun deposito nuovo' } }), runId: 41, detail, preview }).render('it');
  assert.match(leggero, /data-filing-leggero="1"/); assert.match(leggero, /nessun deposito nuovo/);
  const err = filingPage({ overview: overview(), sel: 'BRK.DE', listing: listing({ ticker: 'BRK.DE' }) }).render('it');
  assert.match(err, /sezione «risk» non trovata/); assert.match(err, /data-filing-azione="profilo-avanzato"/);
  const judged = { ...detail, judgment: { status: 'ok', model: 'synthetic/model', findings: [{ category: 'Rischi', assessment: 'Valutazione sintetica', citations: ['C1-dopo', 'C9-dopo'] }] } };
  const prof = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: judged, preview, profiloAperto: true }).render('it');
  assert.match(prof, /data-filing-centro="profilo"/); assert.match(prof, /Valutazione sintetica/);
  assert.match(prof, /citazione non trovata nel confronto/); assert.match(prof, /Annulla il collegamento/);
  assert.match(prof, /Storico dei controlli/);
});

test('parità: «Verifica ora» dichiara il giudizio AI a pagamento quando il profilo lo chiede', () => {
  const l = listing(); l.profile = { ...l.profile, qualitative_enabled: true };
  const html = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: l, runId: 41, detail, preview }).render('it');
  assert.match(html, /Verifica ora · giudizio AI a pagamento/);
});

test('parità: nel profilo avanzato restano similarità, limiti ed estratti non confrontabili; impronta sulle citazioni', () => {
  const d2 = JSON.parse(JSON.stringify(detail));
  Object.assign(d2.result.confronto_corrente, { limiti: ['Limite sintetico'], similarita_sezioni: { rischi: { jaccard: 0.5, coseno: 0.6 } },
    segmenti_non_confrontabili: [{ prima: { testo: 'Estratto vecchio' }, dopo: { testo: 'Estratto nuovo' } }] });
  d2.result.confronto_corrente.cambiamenti[0].dopo.sha256 = 'f'.repeat(8);
  const prof = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: d2, preview, profiloAperto: true }).render('it');
  assert.match(prof, /Limite sintetico/); assert.match(prof, /Jaccard 50%/); assert.match(prof, /Estratto vecchio → Estratto nuovo/);
  const cards = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: d2, preview }).render('it');
  assert.match(cards, /title="impronta ffffffff/);
});

test('prova reale: repository fermo dichiarato al posto di «in attesa»; errore «nessuna sezione» porta al profilo', () => {
  const fermo = T('ORSA.MI', 'Orsa Banca', 'primo_confronto', 'aggiornati', { ultimo_confronto: null,
    stato_riga: "ORSA.MI · ESEF LEI 999900 · non disponibile: nessun confronto: ESEF: repository fermo all'esercizio FY2020" });
  const o = overview({ titoli: [fermo] });
  const html = filingPage({ overview: o, sel: 'ORSA.MI', listing: listing({ ticker: 'ORSA.MI' }) }).render('it');
  assert.match(html, /data-filing-senza-coppia="1"/); assert.match(html, /repository fermo all&#x27;esercizio FY2020/);
  assert.match(html, />nessun confronto</); assert.doesNotMatch(html, /In attesa del primo confronto/);
  const vuoto = T('NOVA.DE', 'Nova', 'errore', 'da_sistemare', { ultimo_errore: { at: '2026-10-03T21:58:00', reason: 'nessuna sezione confrontata: rischi: prima: intestazione iniziale assente o ambigua' } });
  const e = filingPage({ overview: overview({ titoli: [vuoto] }), sel: 'NOVA.DE', listing: listing() }).render('it');
  assert.match(e, /data-filing-rimedio="profilo"/);
});

test('prova reale: i numeri di un\'altra variante usano i periodi di quella variante; «Tutti» a pagine da 100', () => {
  const d2 = JSON.parse(JSON.stringify(detail));
  d2.result.numeri.variante = 'annuale';
  d2.result.varianti = [{ tipo: 'trimestrale', primaria: true, coppia_periodi: ['2025-09-30', '2026-09-30'] }, { tipo: 'annuale', coppia_periodi: ['2024-12-31', '2025-12-31'] }];
  const html = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: d2, preview }).render('it');
  assert.match(html, />FY 2024</); assert.match(html, />FY 2025</); assert.doesNotMatch(html, /<th scope="col">Q3 2025/);
  const molti = JSON.parse(JSON.stringify(detail));
  molti.result.confronto_corrente.cambiamenti = Array.from({ length: 250 }, (_, i) => ({ tipo: 'aggiunto', dopo: cit(`Frase sintetica numero ${i} con parole.`, 'Gestione') }));
  const t = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: molti, preview, filtro: 'tutti' }).render('it');
  assert.equal((t.match(/data-filing-cambiamento=/g) || []).length, 100);
  assert.match(t, /100 di 250 mostrati/); assert.match(t, /Mostra altri 100/);
});

test('prova reale: dopo lo scollegamento un candidato ESEF trovato dalla ricerca si conferma, non è «nessuna fonte»', () => {
  const scollegato = T('ACME.AS', 'Acme Lithography', 'non_attivo', 'senza_fonte', { attivazione: { esito: 'scollegato' } });
  const proposta = { ticker: 'ACME.AS', profilo_attivo: false, escluso: false, preferita: 'esef',
    sec: { stato: 'nessuno', candidati: [], motivo: 'nessun emittente SEC con nome o alias corrispondente' },
    esef: { stato: 'univoco', candidati: [{ lei: '999900ACME0000000001', nome: 'Acme Lithography N.V.', origine: 'nome' }], motivo: 'nome identico su un solo emittente ESEF' } };
  const html = filingPage({ overview: overview({ titoli: [scollegato] }), sel: 'ACME.AS', proposta, scelta: '999900ACME0000000001' }).render('it');
  assert.match(html, /data-filing-centro="collegamento"/); assert.match(html, /data-filing-candidato="999900ACME0000000001"/);
  assert.doesNotMatch(html, /Nessun risultato nei depositi SEC/);
});

test('fase F: «Freschezza» dice «nessun confronto» quando manca la coppia, mai «aggiornato»', () => {
  const tile = (html) => /<span class="k">(?:Freschezza|Freshness)<\/span><span class="v is-(\w+)">([^<]+)<\/span><span class="s"[^>]*>([^<]+)</.exec(html);
  // repository ESEF fermo (sintetico)
  const fermo = T('ORSA.MI', 'Orsa Banca', 'primo_confronto', 'aggiornati', { ultimo_confronto: null, freschezza: 'senza_confronto',
    stato_riga: "ORSA.MI · ESEF LEI 999900 · non disponibile: nessun confronto: ESEF: repository fermo all'esercizio FY2020" });
  let m = tile(filingPage({ overview: overview({ titoli: [fermo] }), sel: 'ORSA.MI', listing: listing({ ticker: 'ORSA.MI' }) }).render('it'));
  assert.deepEqual(m && m.slice(1), ['warn', 'nessun confronto', 'repository fermo al FY2020']);
  // emittente SEC nuovo senza omologo dell'anno prima
  const nuovo = T('NOVA.DE', 'Nova', 'primo_confronto', 'aggiornati', { ultimo_confronto: null, freschezza: 'senza_confronto',
    stato_riga: 'NOVA.DE · SEC 10-Q · not available: no comparison: SEC: no same period a year earlier' });
  m = tile(filingPage({ overview: overview({ titoli: [nuovo] }), sel: 'NOVA.DE', listing: listing() }).render('en'));
  assert.deepEqual(m && m.slice(1), ['warn', 'no comparison', 'no same period a year earlier']);
  // in attesa del primo confronto: niente «aggiornato» nemmeno con un backend precedente (senza `freschezza`)
  const attesa = T('NOVA.DE', 'Nova', 'primo_confronto', 'aggiornati', { ultimo_confronto: null, stato_riga: 'NOVA.DE · SEC 10-Q · non disponibile: in attesa del primo confronto' });
  m = tile(filingPage({ overview: overview({ titoli: [attesa] }), sel: 'NOVA.DE', listing: listing() }).render('it'));
  assert.deepEqual(m && m.slice(1), ['warn', 'nessun confronto', 'in attesa del primo confronto']);
  // con un confronto valido resta «aggiornato»
  const html = filingPage({ overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail, preview }).render('it');
  assert.equal(tile(html)?.[2], 'aggiornato');
});

const PDF_IR = { tipo: 'annuale', candidati: 3, at: '2026-10-03T08:10:00', sito: 'https://www.quanta.example/',
  ultimo: { url: 'https://www.quanta.example/ir/annual-report-2025.pdf', testo: 'Annual report 2025', tipo: 'annuale', periodo: '2025-12-31' },
  precedente: { url: 'https://www.quanta.example/ir/annual-report-2024.pdf', testo: 'Annual report 2024', tipo: 'annuale', periodo: '2024-12-31' } };

test('fase F: PDF IR trovati nella card «Nessuna fonte gratuita», stima gratis a un clic, gruppo «Senza fonte»', async () => {
  const qx = T('QX7.F', 'Quanta X7', 'senza_fonte', 'senza_fonte', { ultimo_confronto: null, documento: null, pdf_ir: PDF_IR });
  const o = overview({ titoli: [qx] });
  const html = filingPage({ overview: o, sel: 'QX7.F', listing: listing({ ticker: 'QX7.F', profile: null }) }).render('it');
  assert.match(html, /data-filing-pdf-trovati="1"/);
  assert.match(html, /Più recente · relazione annuale 2025/); assert.match(html, /Anno prima · relazione annuale 2024/);
  assert.match(html, /annual-report-2025\.pdf/); assert.match(html, /data-filing-azione="pdf-stima"[^>]*>Stima il costo \(gratis\)/);
  assert.match(html, /<span class="k">Sito IR<\/span><span class="v is-acc">2 PDF<\/span><span class="s"[^>]*>proposta AI a un clic/);
  assert.match(html, /PDF IR trovati sul sito/); // sottotitolo nell'elenco
  assert.match(html, /data-filing-gruppo="senza_fonte"/);
  assert.doesNotMatch(html, /data-filing-azione="cerca-pdf"/);
  const en = filingPage({ overview: o, sel: 'QX7.F', listing: listing({ ticker: 'QX7.F', profile: null }) }).render('en');
  assert.match(en, /Latest · annual report 2025/); assert.match(en, /Estimate the cost \(free\)/);
  // senza PDF trovati: ricerca su richiesta; esito vuoto dichiarato
  const vuoto = T('QX7.F', 'Quanta X7', 'senza_fonte', 'senza_fonte', { ultimo_confronto: null, documento: null });
  const v = filingPage({ overview: overview({ titoli: [vuoto] }), sel: 'QX7.F', ricercaPdf: { trovati: false, motivi: ['robots.txt esclude /ir'] } }).render('it');
  assert.match(v, /data-filing-azione="cerca-pdf"[^>]*>Cerca i PDF sul sito \(gratis\)/);
  assert.match(v, /Nessuna relazione periodica trovata sul sito\. robots\.txt esclude \/ir/);
  // sentinella: aprire il titolo coi PDF trovati non stima, non cerca e non chiama l'AI
  const ok = x => async () => x;
  const view = filingPage({}, { filingOverviewAmbito: ok(o), filingList: ok(listing({ ticker: 'QX7.F', profile: null })) }, 't=QX7.F');
  for (let i = 0; i < 4; i++) { view.render('it'); await view.effects(); }
  assert.deepEqual(view.requests.filter(n => ['filingAiEstimate', 'filingAiProposal', 'filingSearchPdf'].includes(n)), []);
});

test('fase F: emittente SEC nuovo, trimestre su trimestre dichiarato nel riquadro, nella nota e nei numeri', () => {
  const tile = (html) => /<span class="k">(?:Confronto|Comparison)<\/span><span class="v[^"]*">([^<]+)<\/span><span class="s"[^>]*>([^<]+)</.exec(html);
  const seq = JSON.parse(JSON.stringify(detail));
  seq.result.coppia = { regola: 'sequenziale', prima: { metadati: { periodo_fine: '2025-12-31' } }, dopo: { metadati: { periodo_fine: '2026-03-31' } } };
  seq.result.confronto_corrente.regola = 'sequenziale';
  seq.result.numeri.confronto = 'trimestre_precedente';
  const props = { overview: overview(), sel: 'NOVA.DE', listing: listing(), runId: 41, detail: seq, preview };
  const it = filingPage(props).render('it');
  const m = tile(it);
  assert.equal(m?.[1], 'Q1 2026 vs Q4 2025'); assert.match(m?.[2] || '', /^trimestre su trimestre · eseguito /);
  assert.match(it, /data-filing-sequenziale="1"[^>]*>.*?Confronto trimestre su trimestre: manca lo stesso trimestre dell’anno prima; la stagionalità può pesare\./);
  assert.match(it, /XBRL · trimestre precedente \(manca l’anno prima\)/); assert.doesNotMatch(it, /stesso periodo dell’anno prima/);
  assert.match(it, />Q4 2025<\/span><span role="columnheader">Q1 2026</); assert.match(it, /Fonte: SEC companyfacts · USD · trimestre precedente/);
  const en = filingPage(props).render('en');
  assert.match(tile(en)?.[2] || '', /^quarter on quarter · run /);
  assert.match(en, /Quarter-on-quarter comparison: the same quarter a year earlier is missing; seasonality may weigh\./);
  assert.match(en, /XBRL · previous quarter \(no prior year\)/); assert.match(en, /Source: SEC companyfacts · USD · previous quarter/);
  // anno su anno: nessuna dichiarazione
  const yoy = filingPage({ ...props, detail }).render('it');
  assert.doesNotMatch(yoy, /data-filing-sequenziale|trimestre su trimestre|trimestre precedente/);
  assert.match(yoy, /XBRL · stesso periodo dell’anno prima/);
});

// 04/10/2026 (G9b): letture fallite di profilo e run dichiarate col motivo, non «caricamento» né zero cifre.
test('profilo e run illeggibili: errore dichiarato col motivo (wiring delle letture vere)', async () => {
  const fallisce = msg => async () => { throw { response: { data: { detail: msg } } }; };
  const view = filingPage({ overview: overview(), sel: 'NOVA.DE', runId: 41 },
    { filingList: fallisce('Archivio profili sintetico guasto'), filingRun: fallisce('Run sintetico illeggibile'), filingContextPreview: async () => preview });
  view.render('it'); await view.effects(); await view.effects();
  const it = view.render('it');
  assert.match(it, /Profilo e storico dei confronti non leggibili: Archivio profili sintetico guasto/);
  assert.match(it, /Esito del confronto non leggibile \(cambiamenti e cifre non mostrati\): Run sintetico illeggibile/);
  assert.doesNotMatch(it, /data-filing-centro="cambiamenti"[^]*Carico/);
  const en = view.render('en');
  assert.match(en, /Comparison result cannot be read/);
});


// 04/10/2026 (G9b, contratto G2b): la proposta AI può restare «in corso» sul server; la pagina legge
// lo stato del job fino all'esito vero e mostra «rifiutata» col motivo. Nessun timeout client che abbandona.
const baseAi = () => ({ overview: overview(), sel: 'LUMO.PA', aiUrl: 'https://ir.example.invalid/h1.pdf', aiStima: { stato: 'ok', ticker: 'LUMO.PA',
  url: 'https://ir.example.invalid/h1.pdf', sha256: 'a'.repeat(64), pagine: 42, caratteri_input: 21000, righe: 300, pagine_indice: [2], troncato: 0,
  cache: false, modello: 'synthetic/model', token_input_stimati: 8610, costo_max_eur: 0.004 } });

test('proposta AI in corso: «in lavorazione» poi l\'esito letto dal job', async () => {
  const letture = [];
  let dopo = null;
  const orig = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms) => (ms === 3000 ? (dopo = fn, 0) : orig(fn, ms));
  try {
    const view = filingPage(baseAi(), {
      filingAiProposal: async () => ({ stato: 'in_corso', job_id: 'job-ai-1', ticker: 'LUMO.PA', secondi: 20 }),
      filingAiProposalJob: async (t, id) => { letture.push([t, id]); return { stato: 'refused', sha256: 'a'.repeat(64), motivo: 'Proposta già chiesta da poco per questo titolo', variabile: 'FILING_AI_INTERVALLO_TICKER_S', riprova_tra_s: 41.2 }; },
    });
    view.render('it');
    const fatto = view.clic('ai-proponi');
    await new Promise(r => setImmediate(r));
    let html = view.render('it');
    assert.match(html, /Proposta AI in lavorazione sul server da 20 s/);
    assert.ok(dopo, 'polling scheduled'); dopo(); await fatto;
    assert.deepEqual(letture, [['LUMO.PA', 'job-ai-1']]);
    html = view.render('it');
    assert.match(html, /Proposta AI rifiutata dal backend: Proposta già chiesta da poco per questo titolo/);
    assert.match(html, /limite impostato da FILING_AI_INTERVALLO_TICKER_S/);
    assert.match(html, /nuova proposta possibile fra 42 s/);
    // il contratto G2b di oggi non manda `riprovabile`: nessun pulsante promesso senza il campo
    assert.doesNotMatch(html, /data-filing-ai="riprova"/);
    assert.doesNotMatch(html, /in lavorazione sul server/);
  } finally { globalThis.setTimeout = orig; }
});

// Revisione G9b R4: quando il backend dichiara `riprovabile` (campo atteso da G2b) il rifiuto offre Riprova.
test('R4: rifiuto riprovabile = pulsante Riprova, con il costo dichiarato', () => {
  const html = filingPage({ ...baseAi(), aiEsito: { stato: 'refused', motivo: 'tetto del giorno raggiunto', variabile: 'FILING_AI_TETTO_GIORNO_EUR',
    riprovabile: true } }).render('it');
  assert.match(html, /data-filing-ai="riprova"/);
  assert.match(html, /Riprova \(nuovo costo\)/, 'riprova_paga assente = a pagamento');
});

// Revisione G9b R2: l'errore di lettura di un titolo non resta su quello successivo.
test('R2: cambio titolo azzera gli errori di lettura del titolo precedente', async () => {
  const view = filingPage({ overview: overview(), sel: 'NOVA.DE', listingErr: 'guasto di NOVA', detailErr: 'run di NOVA illeggibile' });
  assert.match(view.render('it'), /run di NOVA illeggibile/);
  view.values[view.stateNames.indexOf('sel')] = 'ORSA.MI';
  view.render('it'); await view.effects();
  const html = view.render('it');
  assert.doesNotMatch(html, /run di NOVA illeggibile|guasto di NOVA/);
});

// Revisione G9b R1: la proposta AI che non finisce è dichiarata dopo il tetto, il pulsante si libera.
test('R1: proposta AI senza fine = stato non più aggiornato dopo il tetto, dichiarato', async () => {
  let t = 0;
  const origT = globalThis.setTimeout, origNow = performance.now;
  const attese = [];
  globalThis.setTimeout = (fn, ms) => (ms === 3000 ? (attese.push(() => { t += ms; fn(); }), 0) : origT(fn, ms));
  performance.now = () => t;
  try {
    const view = filingPage(baseAi(), {
      filingAiProposal: async () => ({ stato: 'in_corso', job_id: 'job-eterno', secondi: 20 }),
      filingAiProposalJob: async () => ({ stato: 'in_corso', job_id: 'job-eterno', secondi: 60 }),
    });
    view.render('it');
    const fatto = view.clic('ai-proponi');
    let giri = 0;
    while (giri++ < 2000) { await new Promise(r => setImmediate(r)); if (!attese.length) break; attese.shift()(); }
    await fatto;
    const html = view.render('it');
    assert.match(html, /Stato non più aggiornato dal server dopo 35 minuti/);
    assert.doesNotMatch(html, /in lavorazione sul server/);
  } finally { globalThis.setTimeout = origT; performance.now = origNow; }
});
