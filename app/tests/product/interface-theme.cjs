// Light/Dark preference contract for the interface. Real helpers,
// small in-memory storage, server rendering only: no browser and no API.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { creaCaricatore, ambienteBrowser, SRC } = require('../i18n/_carica.cjs');

const theme = () => creaCaricatore()('lib/interface-theme.ts');

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    values,
  };
}

test('theme preference defaults to Light and has its own key', () => {
  const { readInterfaceTheme, INTERFACE_THEME_STORAGE_KEY } = theme();
  assert.equal(INTERFACE_THEME_STORAGE_KEY, 'bellomberg.interface-theme.v1');
  assert.deepEqual(readInterfaceTheme(memoryStorage()), { theme: 'light', persistenceAvailable: true });
});

test('invalid saved values fall back to Light; only "dark" selects Dark', () => {
  const { readInterfaceTheme, normalizeInterfaceTheme, INTERFACE_THEME_STORAGE_KEY } = theme();
  assert.equal(normalizeInterfaceTheme('dark'), 'dark');
  for (const value of ['light', '', 'Dark', 'DARK', 'night', 'null', '{"theme":"dark"}']) {
    assert.equal(normalizeInterfaceTheme(value), 'light', `input ${value}`);
    assert.equal(readInterfaceTheme(memoryStorage({ [INTERFACE_THEME_STORAGE_KEY]: value })).theme, 'light');
  }
  for (const value of [undefined, null, 7, {}, ['dark']]) assert.equal(normalizeInterfaceTheme(value), 'light');
});

test('a saved theme round-trips, and a stale Classica/Nuova key no longer matters', () => {
  const { readInterfaceTheme, writeInterfaceTheme } = theme();
  // Installs that used the removed Classica keep this key; it must be ignored.
  const storage = memoryStorage({ 'bellomberg.interface-mode.v1': 'classic' });
  assert.equal(writeInterfaceTheme('dark', storage), true);
  assert.equal(readInterfaceTheme(storage).theme, 'dark');
  assert.equal(writeInterfaceTheme('light', storage), true);
  assert.equal(readInterfaceTheme(storage).theme, 'light');
});

test('blocked or throwing storage keeps Light and reports no persistence', () => {
  const { readInterfaceTheme, writeInterfaceTheme } = theme();
  const throwing = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.deepEqual(readInterfaceTheme(throwing), { theme: 'light', persistenceAvailable: false });
  assert.deepEqual(readInterfaceTheme(null), { theme: 'light', persistenceAvailable: false });
  assert.equal(writeInterfaceTheme('dark', throwing), false);
  const ignoring = { getItem: () => null, setItem() {} };
  assert.equal(writeInterfaceTheme('dark', ignoring), false);
});

test('Dark paints the attribute; Light removes it', () => {
  const { applyThemeAttribute, THEME_ATTRIBUTE } = theme();
  const attrs = new Map();
  const root = { setAttribute: (k, v) => attrs.set(k, v), removeAttribute: k => attrs.delete(k) };
  applyThemeAttribute('dark', root);
  assert.equal(attrs.get(THEME_ATTRIBUTE), 'dark');
  applyThemeAttribute('light', root);
  assert.equal(attrs.has(THEME_ATTRIBUTE), false);
});

test('TS palette mirrors every CSS token in both themes', () => {
  const { LIGHT_PALETTE, DARK_PALETTE, PALETTE_TOKENS } = creaCaricatore()('lib/theme-palette.ts');
  const css = fs.readFileSync(path.join(SRC, 'components/theme-tokens.css'), 'utf8');
  const block = selector => {
    const start = css.indexOf(selector + ' {');
    assert.ok(start >= 0, selector);
    const body = css.slice(start, css.indexOf('}', start));
    return Object.fromEntries([...body.matchAll(/(--bbt-[a-z0-9-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));
  };
  const light = block(':root'), dark = block(':root[data-bb-theme="dark"]');
  assert.deepEqual(Object.keys(light).sort(), Object.keys(dark).sort());
  assert.equal(Object.keys(PALETTE_TOKENS).length, Object.keys(light).length);
  for (const [key, token] of Object.entries(PALETTE_TOKENS)) {
    assert.equal(LIGHT_PALETTE[key], light[token], `light ${token}`);
    assert.equal(DARK_PALETTE[key], dark[token], `dark ${token}`);
  }
});

test('the Appearance menu offers only Light/Dark and reflects the saved theme', () => {
  for (const saved of ['dark', 'light']) {
    const { memoria } = ambienteBrowser();
    globalThis.window = globalThis;
    // A leftover Classica choice from an old install must not hide the menu.
    memoria.set('bellomberg.interface-mode.v1', 'classic');
    memoria.set('bellomberg.interface-theme.v1', saved);
    const load = creaCaricatore({ stub: { '../i18n/provider': { useLingua: () => 'it' } } });
    const { InterfaceThemeProvider } = load('components/InterfaceThemeProvider.tsx');
    const Menu = load('components/AppearanceMenu.tsx').default;
    const html = renderToStaticMarkup(React.createElement(InterfaceThemeProvider, null, React.createElement(Menu)));
    assert.match(html, /data-testid="appearance-menu"/);
    assert.match(html, /data-testid="interface-theme-switcher"/);
    assert.doesNotMatch(html, /data-mode-choice/);
    assert.match(html, /aria-label="Tema"/);
    assert.match(html, new RegExp(`data-theme-choice="${saved}" aria-pressed="true"`));
    delete globalThis.window;
  }
});

test('an error caught in Dark offers the Light recovery first, then Retry', () => {
  const Boundary = creaCaricatore()('components/NewInterfaceBoundary.tsx').default;
  let lightRequests = 0;
  const boundary = new Boundary({ children: null, language: 'it' });
  boundary.context = { theme: 'dark', effective: 'dark', setTheme: next => { if (next === 'light') lightRequests++; }, persistenceAvailable: true };
  boundary.state = { ...Boundary.getDerivedStateFromError(new Error('Synthetic dark failure')), errorTheme: 'dark' };
  const fallback = boundary.render();
  const html = renderToStaticMarkup(fallback);
  assert.match(html, /Synthetic dark failure/);
  assert.match(html, /Torna al tema Chiaro/);
  assert.match(html, /Riprova/);
  assert.doesNotMatch(html, /data-mode-recover|Classica/);
  assert.ok(html.indexOf('data-theme-recover="light"') < html.indexOf('Riprova'), 'Light recovery comes first');
  const buttons = [];
  const walk = node => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) return node.forEach(walk);
    if (node.type === 'button') buttons.push(node);
    walk(node.props?.children);
  };
  walk(fallback);
  buttons.find(b => b.props['data-theme-recover'] === 'light').props.onClick();
  assert.equal(lightRequests, 1);
});

test('an error in Light offers only Retry', () => {
  const Boundary = creaCaricatore()('components/NewInterfaceBoundary.tsx').default;
  const boundary = new Boundary({ children: null, language: 'en' });
  boundary.state = Boundary.getDerivedStateFromError(new Error('x'));
  const html = renderToStaticMarkup(boundary.render());
  assert.doesNotMatch(html, /data-theme-recover|data-mode-recover|Classic/);
  assert.match(html, /Retry/);
});
