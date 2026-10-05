import type { ReactNode } from 'react';
import { useLingua } from '../i18n/provider';
import NewInterfaceBoundary from './NewInterfaceBoundary';

function DeferredPageSection({ render }: { render: () => ReactNode }) {
  return <>{render()}</>;
}

/** Protect a pure page section without placing sibling controllers at risk. */
export default function PagePresentation({ render }: { render: () => ReactNode }) {
  const language = useLingua();
  return <NewInterfaceBoundary language={language}>
    <DeferredPageSection render={render} />
  </NewInterfaceBoundary>;
}
