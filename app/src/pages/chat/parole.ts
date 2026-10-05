import { linguaCorrente, type Lingua } from '@/i18n/lingua';
import { traduci, type Chiave, type Parametri } from '@/i18n/t';


// I testi stanno nel catalogo (i18n/it|en/chatPage.ts): qui solo la forma che le viste usano.
function costruisci(l: Lingua) {
  const tr = (k: Chiave, p?: Record<string, string | number | null | undefined>) => traduci(l, k, p as Parametri | undefined);
  return {
    conversations: tr('chatPage.conversations'),
    scope: tr('chatPage.scope'),
    scopeDesk: tr('chatPage.scopeDesk'),
    scopeAll: tr('chatPage.scopeAll'),
    details: tr('chatPage.details'),
    detailsHint: tr('chatPage.detailsHint'),
    detailsClose: tr('chatPage.detailsClose'),
    detailsTabs: tr('chatPage.detailsTabs'),
    tabTape: tr('chatPage.tabTape'),
    tabSources: tr('chatPage.tabSources'),
    tabCosts: tr('chatPage.tabCosts'),
    detailsEmpty: tr('chatPage.detailsEmpty'),
    toolsUsed: (n: number, tot: number, nome: string) => tr('chatPage.toolsUsed', { n, tot, nome }),
    newConversation: tr('chatPage.newConversation'),
    retiredDesk: tr('chatPage.retiredDesk'),
    retiredBanner: (nome: string) => tr('chatPage.retiredBanner', { nome }),
    suggestions: tr('chatPage.suggestions'),
    you: tr('chatPage.you'),
    tools: (n: number) => n === 1 ? tr('chatPage.tools_one') : tr('chatPage.tools_other', { n }),
    costNa: tr('chatPage.costNa'),
    readOnly: tr('chatPage.readOnly'),
  };
}

type Dizionario = ReturnType<typeof costruisci>;
const cache = new Map<Lingua, Dizionario>();

export function parole(): Dizionario {
  const l = linguaCorrente();
  let d = cache.get(l);
  if (!d) { d = costruisci(l); cache.set(l, d); }
  return d;
}
