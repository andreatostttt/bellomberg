// 03/10/2026 (filing fase E): logica pura della pagina Filing. Dati sintetici.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  GRUPPI, raggruppa, striscia, rimedio, presentaCambiamenti, diffParole, categoriaSezione, filtraCambiamenti,
  tonoStato, scalaBreve, deltaNumero, pagineCitazione,
} from '../../src/pages/filing/logica.ts';

type T = Parameters<typeof raggruppa>[0][number];
const riga = (ticker: string, stato: T['stato'], gruppo_ui: T['gruppo_ui'], extra: Partial<T> = {}): T => ({
  ticker, gruppo: 3, stato_riga: ticker, fonte: null, ultimo_confronto: null, novita: false, profilo: true,
  escluso: false, stato, gruppo_ui, ...extra,
});

test('i gruppi seguono l\'ordine del mockup e restano solo quelli con titoli', () => {
  assert.deepEqual([...GRUPPI], ['novita', 'da_sistemare', 'aggiornati', 'senza_fonte']);
  const titoli = [
    riga('ZEN.FRA', 'senza_fonte', 'senza_fonte'),
    riga('ORSA.MI', 'invariato', 'aggiornati', { ultimo_confronto: '2026-10-03T08:10:00' }),
    riga('NOVA.DE', 'novita', 'novita', { cambiamenti: 3, ultimo_confronto: '2026-10-03T08:10:00' }),
    riga('AURA.MI', 'novita', 'novita', { cambiamenti: 5, ultimo_confronto: '2026-10-02T08:10:00' }),
    riga('KORE.DE', 'da_confermare', 'da_sistemare'),
    riga('BRK.DE', 'errore', 'da_sistemare'),
  ];
  const g = raggruppa(titoli, 'novita');
  assert.deepEqual(g.map(x => x.gruppo), ['novita', 'da_sistemare', 'aggiornati', 'senza_fonte']);
  // novità: più cambiamenti prima; da sistemare: errore prima di da confermare
  assert.deepEqual(g[0].titoli.map(t => t.ticker), ['AURA.MI', 'NOVA.DE']);
  assert.deepEqual(g[1].titoli.map(t => t.ticker), ['BRK.DE', 'KORE.DE']);
  const az = raggruppa(titoli, 'az');
  assert.deepEqual(az[0].titoli.map(t => t.ticker), ['AURA.MI', 'NOVA.DE']);
  assert.deepEqual(az[1].titoli.map(t => t.ticker), ['BRK.DE', 'KORE.DE']);
  assert.deepEqual(raggruppa([], 'novita'), []);
});

test('un backend senza stato per la UI ricade sui campi storici', () => {
  const vecchio = { ticker: 'ACME.MI', gruppo: 3 as const, stato_riga: 'x', fonte: null, ultimo_confronto: null,
    novita: false, profilo: false, escluso: false };
  assert.equal(raggruppa([vecchio], 'novita')[0].gruppo, 'senza_fonte');
  assert.equal(raggruppa([{ ...vecchio, novita: true, profilo: true, gruppo: 0 }], 'novita')[0].gruppo, 'novita');
});

test('striscia del Comitato: conteggi e quote dalla panoramica', () => {
  const titoli = [
    riga('A', 'aggiornato', 'aggiornati'), riga('B', 'da_confermare', 'da_sistemare', { profilo: false }),
    riga('C', 'senza_fonte', 'senza_fonte', { profilo: false }), riga('D', 'escluso', 'senza_fonte', { escluso: true }),
  ];
  const s = striscia({ titoli, copertura: { totale: 4, con_confronto: 1, aggiornati: 1, non_aggiornati: 1, senza_confronto: 1, senza_profilo: 2, esclusi: 1 },
    contesto: { caratteri: 9840, budget: 14000, omessi_totali: 0 }, aggiornamento: null });
  assert.deepEqual({ ...s, quote: undefined }, { aggiornati: 1, daAggiornare: 2, daConfermare: 1, senzaFonte: 1, esclusi: 1, totale: 4, quote: undefined });
  assert.equal(s.quote.aggiornati, 25);
  assert.equal(s.quote.daAggiornare, 50);
});

test('rimedio dell\'errore dal motivo del backend', () => {
  assert.equal(rimedio('sezione «risk» non trovata nel documento'), 'profilo');
  assert.equal(rimedio("Section 'risk' not found"), 'profilo');
  assert.equal(rimedio('PDF quasi senza testo: serve OCR'), 'ocr');
  assert.equal(rimedio('download non riuscito: timeout'), 'riprova');
  assert.equal(rimedio('HTTP 503 dal server SEC'), 'riprova');
  assert.equal(rimedio('qualcosa di inatteso'), 'riprova_profilo');
  assert.equal(rimedio(null), 'riprova_profilo');
});

test('categorie delle sezioni come il punteggio del contesto', () => {
  assert.equal(categoriaSezione('Item 1A Risk Factors'), 'rischi');
  assert.equal(categoriaSezione('Market risk'), 'altre');
  assert.equal(categoriaSezione('Gestione e prospettive'), 'gestione');
  assert.equal(categoriaSezione("Item 7 Management's Discussion (MD&A)"), 'gestione');
  assert.equal(categoriaSezione('Legal Proceedings'), 'contenziosi');
  assert.equal(categoriaSezione('Contenziosi'), 'contenziosi');
  assert.equal(categoriaSezione('Nota · Fondi rischi'), 'rischi');
  assert.equal(categoriaSezione(undefined), 'altre');
});

const cit = (testo: string, sezione = 'Rischi', extra = {}) => ({ testo, sezione, url: 'https://example.invalid/doc', ...extra });

test('cambiamenti: ID come nelle citazioni, tabelle PDF raccolte, numeri d\'elenco spostati nascosti', () => {
  const lista = [
    { tipo: 'aggiunto', dopo: cit('Nuove restrizioni all\'esportazione potrebbero ridurre le vendite.', 'Rischi', { pagine_fisiche: [41] }) },
    { tipo: 'modificato', prima: cit('Il margine rimane stabile.', 'Gestione'), dopo: cit('Il margine si riduce leggermente.', 'Gestione') },
    { tipo: 'aggiunto', dopo: cit('2.310 2.780 20,3 % 412 506 22,8 %', 'Gestione') },
    { tipo: 'spostato', prima: cit('3.', 'Gestione'), dopo: cit('3.', 'Gestione') },
    { tipo: 'rimosso', prima: cit('Il procedimento sul brevetto è in fase istruttoria.', 'Contenziosi') },
  ];
  const p = presentaCambiamenti(lista, ['C5', 'C1']);
  assert.deepEqual(p.visibili.map(c => c.id), ['C1', 'C2', 'C5']);
  assert.deepEqual(p.tabelle.map(c => c.id), ['C3']);
  assert.equal(p.elenchiNascosti, 1);
  assert.equal(p.visibili[0].pagina, 'p. 41');
  assert.deepEqual(p.visibili[0].citazioni, ['C1-dopo']);
  assert.deepEqual(p.visibili[1].citazioni, ['C2-prima', 'C2-dopo']);
  assert.equal(p.visibili[2].categoria, 'contenziosi');
  // in evidenza: ordine del contesto, poi gli altri non compaiono
  assert.deepEqual(filtraCambiamenti(p.visibili, 'evidenza', ['C5', 'C1']).map(c => c.id), ['C5', 'C1']);
  // senza ordine del contesto: i primi per posizione
  assert.deepEqual(filtraCambiamenti(p.visibili, 'evidenza', []).map(c => c.id), ['C1', 'C2', 'C5']);
  assert.deepEqual(filtraCambiamenti(p.visibili, 'gestione', []).map(c => c.id), ['C2']);
  assert.deepEqual(filtraCambiamenti(p.visibili, 'tutti', ['C5']).map(c => c.id), ['C1', 'C2', 'C5']);
  // una frase con numeri ma soprattutto parole non è una tabella
  const q = presentaCambiamenti([{ tipo: 'aggiunto', dopo: cit('Ricavi pari a 2,78 miliardi, in crescita del 20,3% sul trimestre.') }], []);
  assert.equal(q.tabelle.length, 0);
});

test('diff a parole: tratti uguali, tolti e aggiunti', () => {
  const d = diffParole('Il margine rimane sostanzialmente stabile.', 'Il margine si riduce leggermente.');
  assert.deepEqual(d.filter(x => x.t === '=').map(x => x.s.trim()), ['Il margine']);
  assert.ok(d.some(x => x.t === '-' && x.s.includes('sostanzialmente')));
  assert.ok(d.some(x => x.t === '+' && x.s.includes('leggermente')));
  // ricostruzione fedele dei due lati
  assert.equal(d.filter(x => x.t !== '+').map(x => x.s).join(''), 'Il margine rimane sostanzialmente stabile.');
  assert.equal(d.filter(x => x.t !== '-').map(x => x.s).join(''), 'Il margine si riduce leggermente.');
  // testi enormi: niente diff quadratico, un tolto e un aggiunto
  const lungo = 'parola '.repeat(2000);
  assert.deepEqual(diffParole(lungo, lungo + 'x').map(x => x.t), ['-', '+']);
});

test('toni dello stato e cifre brevi', () => {
  assert.equal(tonoStato('errore'), 'bad');
  assert.equal(tonoStato('da_confermare'), 'warn');
  assert.equal(tonoStato('proposta_ai'), 'warn');
  assert.equal(tonoStato('in_corso'), 'acc');
  assert.equal(tonoStato('novita'), 'acc');
  assert.equal(tonoStato('aggiornato'), 'ok');
  assert.equal(tonoStato('non_attivo'), 'off');
  // il suffisso viene dal catalogo (filingPage.num_*, provato in tests/i18n/filing-page.test.cjs)
  assert.deepEqual(scalaBreve(10_020_000_000, 'it'), { testo: '10,02', scala: 'bn' });
  assert.deepEqual(scalaBreve(488_000_000, 'it'), { testo: '488', scala: 'mn' });
  assert.deepEqual(scalaBreve(2_100_000, 'en'), { testo: '2.10', scala: 'mn' });
  assert.deepEqual(scalaBreve(10_020_000_000, 'en'), { testo: '10.02', scala: 'bn' });
  assert.deepEqual(scalaBreve(512, 'en'), { testo: '512', scala: null });
  assert.equal(scalaBreve(null, 'it'), null);
  assert.deepEqual(deltaNumero(15.04), { testo: '+15,0%', tono: 'up' });
  assert.deepEqual(deltaNumero(-5.2, 'it', true), { testo: '−5,2%', tono: 'up' }); // debito che scende: buono
  assert.deepEqual(deltaNumero(12.7, 'it', true), { testo: '+12,7%', tono: 'dn' }); // scorte che salgono
  assert.deepEqual(deltaNumero(-0.8, 'it', true), { testo: '−0,8%', tono: 'fl' }); // sotto l'1%: neutro
  assert.deepEqual(deltaNumero(0), { testo: '0,0%', tono: 'fl' });
  assert.deepEqual(deltaNumero(null), { testo: '—', tono: 'fl' });
  assert.equal(pagineCitazione({ pagine_fisiche: [18, 19, 22] }), 'p. 18–22');
  assert.equal(pagineCitazione({}), null);
});

import { periodoBreve, coppiaBreve, quando, sezioniProfilo, idCitati } from '../../src/pages/filing/logica.ts';

test('periodi brevi come nel mockup', () => {
  assert.equal(periodoBreve('2026-09-30', 'trimestrale'), 'Q3 2026');
  assert.equal(periodoBreve('2026-06-30', 'semestrale'), 'H1 2026');
  assert.equal(periodoBreve('2026-12-31', 'semestrale'), 'H2 2026');
  assert.equal(periodoBreve('2025-12-31', 'annuale'), 'FY 2025');
  assert.equal(periodoBreve('2026-09-30', 'nove_mesi'), '9M 2026');
  assert.equal(periodoBreve('2026-09-30', undefined), '30/09/2026');
  assert.equal(periodoBreve(null, 'annuale'), null);
  assert.equal(coppiaBreve({ prima: { metadati: { periodo_fine: '2025-09-30' } }, dopo: { metadati: { periodo_fine: '2026-09-30' } } }, 'trimestrale'), 'Q3 2026 vs Q3 2025');
  assert.equal(coppiaBreve(null, 'annuale'), null);
});

test('date relative: oggi, ieri, poi giorno e mese', () => {
  const ora = new Date(2026, 9, 3, 14, 0);
  const w = { today: 'oggi', yesterday: 'ieri', now: 'ora' };
  assert.equal(quando(new Date(2026, 9, 3, 8, 10).toISOString(), ora, 'it', w), 'oggi 08:10');
  assert.equal(quando(new Date(2026, 9, 3, 8, 10).toISOString(), ora, 'it', w, true), 'oggi');
  assert.equal(quando(new Date(2026, 9, 2, 8, 10).toISOString(), ora, 'it', w, true), 'ieri');
  assert.equal(quando(new Date(2026, 8, 29, 8, 10).toISOString(), ora, 'it', w, true), '29 set');
  assert.equal(quando(new Date(2026, 9, 3, 13, 59, 30).toISOString(), ora, 'it', w, true), 'ora');
  assert.equal(quando(null, ora, 'it', w), null);
});

test('sezioni del profilo per variante e ID citati nel testo del Consigliere', () => {
  const p = { sezioni: { rischi: {}, gestione: {} }, varianti: [{ tipo: 'annuale', sezioni: { a: {}, b: {}, c: {} } }, { tipo: 'semestrale', sezioni: { x: {} } }] };
  assert.equal(sezioniProfilo(p, 'annuale'), 3);
  assert.equal(sezioniProfilo(p, 'semestrale'), 1);
  assert.equal(sezioniProfilo({ sezioni: { a: {}, b: {} } }, 'trimestrale'), 2);
  assert.equal(sezioniProfilo(null, 'annuale'), 0);
  assert.deepEqual(idCitati('+ rischi «…» [C3-dopo]\n~ MD&A «a» → «b» [C7-prima, C7-dopo]\n− x [C11-prima]'), ['C3', 'C7', 'C11']);
});

import { motivoBreve, motivoSenzaConfronto } from '../../src/pages/filing/logica.ts';

test('prova reale: senza confronto per un motivo dichiarato non è «in attesa del primo confronto»', () => {
  assert.equal(motivoSenzaConfronto('ACME.MI · ESEF LEI 999900 · non disponibile: nessun confronto: ESEF: repository fermo all\'esercizio FY2022 (chiuso il 2022-12-31)'),
    'ESEF: repository fermo all\'esercizio FY2022 (chiuso il 2022-12-31)');
  assert.equal(motivoSenzaConfronto('ACME.MI · x · not available: no comparison: ESEF repository stuck'), 'ESEF repository stuck');
  assert.equal(motivoBreve("ESEF: repository fermo all'esercizio FY2022 (chiuso il 2022-12-31)"), 'repository fermo al FY2022');
  assert.equal(motivoBreve('ESEF repository stuck at fiscal year FY2022 (closed on 2022-12-31)'), 'ESEF repository stuck at FY2022');
  assert.equal(motivoBreve('SEC: ' + 'x'.repeat(80)).length, 48);
  assert.equal(motivoSenzaConfronto('ACME.MI · SEC 10-Q · non disponibile: in attesa del primo confronto'), null);
  assert.equal(motivoSenzaConfronto(''), null);
});

import { urlDaSalvare } from '../../src/pages/filing/logica.ts';

test('prova reale: salvando una proposta riaperta restano gli altri PDF già verificati', () => {
  const esito = { url: 'https://ir.example.invalid/h1-2026.pdf', altri: [{ url: 'https://ir.example.invalid/h1-2025.pdf' }] };
  assert.deepEqual(urlDaSalvare(esito, '', ''), ['https://ir.example.invalid/h1-2026.pdf', 'https://ir.example.invalid/h1-2025.pdf']);
  assert.deepEqual(urlDaSalvare(esito, 'https://ir.example.invalid/h1-2026.pdf', 'https://ir.example.invalid/h1-2025.pdf\nhttps://ir.example.invalid/x.pdf'),
    ['https://ir.example.invalid/h1-2026.pdf', 'https://ir.example.invalid/h1-2025.pdf', 'https://ir.example.invalid/x.pdf']);
  assert.deepEqual(urlDaSalvare({ url: undefined, altri: [] }, 'https://ir.example.invalid/a.pdf', ''), ['https://ir.example.invalid/a.pdf']);
  assert.equal(urlDaSalvare(esito, '', Array.from({ length: 9 }, (_, i) => `https://ir.example.invalid/${i}.pdf`).join(' ')).length, 5);
});

import { trimestreSuTrimestre } from '../../src/pages/filing/logica.ts';

test('fase F: trimestre su trimestre solo con la regola sequenziale dichiarata', () => {
  const coppia = { prima: { metadati: { periodo_fine: '2025-12-31' } }, dopo: { metadati: { periodo_fine: '2026-03-31' } } };
  assert.equal(trimestreSuTrimestre({ coppia: { ...coppia, regola: 'sequenziale' } }), true);
  assert.equal(trimestreSuTrimestre({ coppia }), false);
  assert.equal(trimestreSuTrimestre({ coppia: null }), false);
  assert.equal(trimestreSuTrimestre(undefined), false);
  // trimestri consecutivi nell'etichetta del riquadro
  assert.equal(coppiaBreve(coppia, 'trimestrale'), 'Q1 2026 vs Q4 2025');
});
