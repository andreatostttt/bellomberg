import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState } from 'react';
import {
  applyThemeAttribute,
  readInterfaceTheme,
  writeInterfaceTheme,
  type InterfaceTheme,
} from '../lib/interface-theme';
import { DARK_PALETTE, LIGHT_PALETTE, type ThemePalette } from '../lib/theme-palette';

export interface InterfaceThemeContextValue {
  /** Saved preference. */
  theme: InterfaceTheme;
  /** What is painted now (same as `theme`; kept so chart owners read one field). */
  effective: InterfaceTheme;
  setTheme: (theme: InterfaceTheme) => void;
  persistenceAvailable: boolean;
}

const outsideProvider: InterfaceThemeContextValue = {
  theme: 'light',
  effective: 'light',
  setTheme: () => undefined,
  persistenceAvailable: true,
};
export const InterfaceThemeContext = createContext<InterfaceThemeContextValue>(outsideProvider);

/**
 * Switching theme only flips one attribute on <html> and this context value:
 * no keyed subtree, so controllers, timers, streams and chart instances stay
 * mounted. Chart owners re-colour through useThemePalette().
 */
export function InterfaceThemeProvider({ children }: { children: React.ReactNode }) {
  const [initial] = useState(readInterfaceTheme);
  const [theme, setCurrentTheme] = useState<InterfaceTheme>(initial.theme);
  const [persistenceAvailable, setPersistenceAvailable] = useState(initial.persistenceAvailable);
  const effective = theme;

  useLayoutEffect(() => { applyThemeAttribute(effective); }, [effective]);

  const setTheme = useCallback((next: InterfaceTheme) => {
    setCurrentTheme(next);
    setPersistenceAvailable(writeInterfaceTheme(next));
  }, []);

  const value = useMemo(() => ({ theme, effective, setTheme, persistenceAvailable }),
    [theme, effective, setTheme, persistenceAvailable]);
  return <InterfaceThemeContext.Provider value={value}>{children}</InterfaceThemeContext.Provider>;
}

export function useInterfaceTheme(): InterfaceThemeContextValue {
  return useContext(InterfaceThemeContext);
}

/** Resolved colours for canvas/SVG/chart code that cannot read CSS tokens.
 *  The object identity changes only when the painted theme changes. */
export function useThemePalette(): ThemePalette {
  return useInterfaceTheme().effective === 'dark' ? DARK_PALETTE : LIGHT_PALETTE;
}
