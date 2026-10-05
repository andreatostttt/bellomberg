import assert from 'node:assert/strict';
import test from 'node:test';
import { frase } from '../../src/lib/frase.ts';

test('maiuscolo terminale diventa frase, sigle e numeri restano', () => {
  assert.equal(frase('+9,43% · 110 PUNTI · AL NETTO DEI VERSAMENTI'), '+9,43% · 110 punti · al netto dei versamenti');
  assert.equal(frase('VALORE QUOTA · BASE 100 DAL 08/05/2026'), 'Valore quota · base 100 dal 08/05/2026');
  assert.equal(frase('+9,43% DALL’INIZIO · TWR GIPS · I VERSAMENTI NON LO MUOVONO'), '+9,43% dall’inizio · TWR GIPS · i versamenti non lo muovono');
  assert.equal(frase('MERCATO // ZETA.DE'), 'Mercato // ZETA.DE');
  assert.equal(frase('OGGI VERSATI 202 €'), 'Oggi versati 202 €');
});

test('un testo gia misto non si tocca', () => {
  assert.equal(frase('Valore quota TWR'), 'Valore quota TWR');
  assert.equal(frase(''), '');
});
