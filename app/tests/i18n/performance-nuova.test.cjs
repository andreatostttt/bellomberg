const { test } = require('node:test');
const assert = require('node:assert/strict');
const { creaCaricatore, ambienteBrowser } = require('./_carica.cjs');

ambienteBrowser();
const load = creaCaricatore();
const language = load('i18n/lingua.ts');
const formato = load('pages/performance/formato.ts');

test('formato Performance: assente = «—», meno tipografico, segno + dove richiesto, nelle due lingue', () => {
  const { num, pct, punti, euro, VUOTO } = formato;
  language.impostaLinguaCorrente('it');
  assert.equal(VUOTO, '—');
  assert.equal(pct(null), '—'); assert.equal(euro(undefined), '—'); assert.equal(num(Number.NaN), '—');
  assert.equal(pct(-0.69), '−0,69%');
  assert.equal(pct(11.42), '+11,42%');
  assert.equal(punti(1.99), '+1,99 pt');
  assert.equal(euro(-166, 0), '−166 €');
  assert.equal(euro(10150.55), '10.150,55 €');
  assert.equal(pct(-0.000001), '0,00%');
  language.impostaLinguaCorrente('en');
  assert.equal(euro(10150.55), '10,150.55 €');
  assert.equal(pct(null), '—');
  language.impostaLinguaCorrente('it');
});
