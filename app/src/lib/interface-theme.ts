/** Light/Dark preference of the interface. Global and persistent. */
export type InterfaceTheme = 'light' | 'dark';

/** The slice of localStorage the preference helpers use (tests pass a small fake). */
export interface PreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export const INTERFACE_THEME_STORAGE_KEY = 'bellomberg.interface-theme.v1';
/** Attribute on <html> read by components/theme-tokens.css. */
export const THEME_ATTRIBUTE = 'data-bb-theme';

export function normalizeInterfaceTheme(value: unknown): InterfaceTheme {
  return value === 'dark' ? 'dark' : 'light';
}

function browserStorage(): PreferenceStorage | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

function resolveStorage(storage?: PreferenceStorage | null): PreferenceStorage | null {
  return storage === undefined ? browserStorage() : storage;
}

/** Missing or invalid preferences fall back to Light. */
export function readInterfaceTheme(storage?: PreferenceStorage | null): {
  theme: InterfaceTheme;
  persistenceAvailable: boolean;
} {
  const target = resolveStorage(storage);
  if (!target) return { theme: 'light', persistenceAvailable: false };
  try {
    const saved = target.getItem(INTERFACE_THEME_STORAGE_KEY);
    return { theme: normalizeInterfaceTheme(saved), persistenceAvailable: true };
  } catch {
    return { theme: 'light', persistenceAvailable: false };
  }
}

export function writeInterfaceTheme(theme: InterfaceTheme, storage?: PreferenceStorage | null): boolean {
  const target = resolveStorage(storage);
  if (!target) return false;
  try {
    target.setItem(INTERFACE_THEME_STORAGE_KEY, theme);
    return target.getItem(INTERFACE_THEME_STORAGE_KEY) === theme;
  } catch {
    return false;
  }
}

/** Paint the theme on <html>. Light removes the attribute. */
export function applyThemeAttribute(theme: InterfaceTheme, root?: HTMLElement | null) {
  const target = root ?? (typeof document === 'undefined' ? null : document.documentElement);
  if (!target) return;
  if (theme === 'dark') target.setAttribute(THEME_ATTRIBUTE, 'dark');
  else target.removeAttribute(THEME_ATTRIBUTE);
}
