import React from 'react';
import type { Lingua } from '../i18n/lingua';
import { appearanceCopy } from './appearance-copy';
import { InterfaceThemeContext, type InterfaceThemeContextValue } from './InterfaceThemeProvider';
import type { InterfaceTheme } from '../lib/interface-theme';

interface Props {
  children: React.ReactNode;
  language: Lingua;
}

interface State {
  error: Error | null;
  /** Painted theme when the error was caught; a theme change retries. */
  errorTheme: InterfaceTheme | null;
}

export default class NewInterfaceBoundary extends React.Component<Props, State> {
  static contextType = InterfaceThemeContext;
  declare context: InterfaceThemeContextValue;
  state: State = { error: null, errorTheme: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, errorTheme: null };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[NewInterfaceBoundary] caught:', error, info);
    this.setState({ error, errorTheme: this.paintedTheme() });
  }

  componentDidUpdate() {
    // Returning to Light re-renders the same subtree: controllers above the
    // boundary were never unmounted, so their state and operations survive.
    const { error, errorTheme } = this.state;
    if (error && errorTheme !== null && errorTheme !== this.paintedTheme()) {
      this.setState({ error: null, errorTheme: null });
    }
  }

  private retry = () => this.setState({ error: null, errorTheme: null });

  // Instances built outside React (unit tests) have no context.
  private paintedTheme = (): InterfaceTheme => this.context?.effective ?? 'light';

  private recoverLight = () => this.context?.setTheme('light');

  render() {
    const { error } = this.state;
    // A theme change re-renders this boundary (it reads the theme context): clone
    // the child so the presenter below runs again too. Inline colours repaint, and
    // a presenter that fails only in one theme is caught here instead of later.
    if (!error) return React.Children.map(this.props.children, child => React.isValidElement(child) ? React.cloneElement(child) : child);
    const copy = appearanceCopy(this.props.language);
    const failedInDark = (this.state.errorTheme ?? this.paintedTheme()) === 'dark';
    return (
      <section className="bb-interface-recovery" role="alert" aria-labelledby="bb-interface-recovery-title">
        <div className="bb-interface-recovery-card">
          <h1 id="bb-interface-recovery-title">{copy.recoveryTitle}</h1>
          <p>{failedInDark ? copy.recoveryLightNote : copy.recoveryNote}</p>
          <details open>
            <summary>{copy.diagnosticLabel}</summary>
            <pre>{error.message || String(error)}</pre>
          </details>
          <div className="bb-interface-recovery-actions">
            {failedInDark && (
              <button type="button" data-theme-recover="light" onClick={this.recoverLight}>
                {copy.recoveryLightAction}
              </button>
            )}
            <button type="button" onClick={this.retry}>{copy.retryAction}</button>
          </div>
        </div>
      </section>
    );
  }
}
