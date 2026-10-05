// Un solo catalogo: i testi delle pagine Nuova (pages/<area>/parole.ts e appearance-copy.ts) stanno in
// i18n/it|en. Queste facciate possono solo comporre chiavi del catalogo: nessun testo scritto a mano,
// nessun dizionario IT/EN parallelo scelto con la lingua corrente.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const SRC = path.resolve(__dirname, '../../src');
const facciate = [
  ...fs.readdirSync(path.join(SRC, 'pages'), { withFileTypes: true })
    .filter(d => d.isDirectory() && fs.existsSync(path.join(SRC, 'pages', d.name, 'parole.ts')))
    .map(d => path.join(SRC, 'pages', d.name, 'parole.ts')),
  path.join(SRC, 'components', 'appearance-copy.ts'),
];
// Letterali ammessi: chiavi del catalogo, percorsi di import, valori tecnici senza lettere.
const AMMESSO = /^(?:[a-zA-Z]+\.[A-Za-z0-9_]+|@\/[\w/.-]+|\.{1,2}\/[\w/.-]+|[^A-Za-zÀ-ÿ]*|en|it|EUR)$/;

test('the Nuova page facades compose catalog keys only', () => {
  assert.ok(facciate.length >= 9, 'facades found: ' + facciate.length);
  const trovati = [];
  for (const file of facciate) {
    const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true);
    const visita = n => {
      const identificatore = n.parent && ((ts.isPropertyAssignment(n.parent) && n.parent.name === n) || ts.isLiteralTypeNode(n.parent)
        || (ts.isBinaryExpression(n.parent) && /^[!=]==$/.test(n.parent.operatorToken.getText())));
      if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && !identificatore && !AMMESSO.test(n.text)) trovati.push(`${path.relative(SRC, file)}: ${JSON.stringify(n.text)}`);
      if (ts.isTemplateExpression(n) && /[A-Za-zÀ-ÿ]{2}/.test(n.head.text + n.templateSpans.map(s => s.literal.text).join(''))) trovati.push(`${path.relative(SRC, file)}: ${n.getText()}`);
      ts.forEachChild(n, visita);
    };
    visita(sf);
  }
  assert.deepEqual(trovati, [], 'hand-written copy outside the catalog');
});

test('no parallel IT/EN dictionaries chosen by the current language in pages and components', () => {
  const sospetti = [];
  const scorri = dir => { for (const d of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) scorri(p);
    else if (/\.(ts|tsx)$/.test(d.name) && /linguaCorrente\(\) === 'en' \? (EN|IT|[A-Z_]+_EN)\b/.test(fs.readFileSync(p, 'utf8'))) sospetti.push(path.relative(SRC, p));
  } };
  scorri(path.join(SRC, 'pages')); scorri(path.join(SRC, 'components'));
  assert.deepEqual(sospetti, []);
});
