import type { ReactNode } from 'react';
import { useLingua } from '../i18n/provider';
import NewInterfaceBoundary from './NewInterfaceBoundary';
import './pages-modern.css';

export interface ModernPageProps {
  children?: ReactNode;
  /** Evaluate page presentation below the boundary, after the owner's hooks. */
  render?: () => ReactNode;
  /** Composition-only wrappers leave recovery to each operational child. */
  presentationBoundary?: boolean;
  /** Stable page slug used by data-page and the bb-page-* class. */
  page: string;
  /** Optional page-specific class; keep page layout rules in the owning page CSS. */
  className?: string;
}

/**
 * Shared presentation boundary for a routed page.
 *
 * Keep the page's operational controller hooks in the enclosing page
 * component, above this wrapper, and pass their data/actions to rendered views.
 * A caught error replaces boundary descendants, so stateful nested surfaces
 * should keep their controller in the surface owner and put a local boundary
 * around only the presentation child. Composition-only pages with operational
 * child components use presentationBoundary={false}; protect their own pure
 * sections locally and keep child controllers outside those boundaries.
 * Example:
 * `<ModernPage page="performance" render={() => <PerformanceView ... />} />`.
 * The render callback is evaluated by a child beneath the boundary so a
 * presentation exception cannot unmount the owner's hooks or restart work.
 */
function DeferredPresentation({ render }: { render: () => ReactNode }) {
  return render();
}

export default function ModernPage({ children, render, page, className, presentationBoundary = true }: ModernPageProps) {
  const language = useLingua();
  const classes = [
    'bb-page',
    'bb-page-modern',
    `bb-page-${page}`,
    className,
  ].filter(Boolean).join(' ');
  const presentation = render
    ? presentationBoundary ? <DeferredPresentation render={render} /> : render()
    : children;

  return (
    <div className={classes} data-page={page}>
      {presentationBoundary ? <NewInterfaceBoundary language={language}>
        {presentation}
      </NewInterfaceBoundary> : presentation}
    </div>
  );
}
