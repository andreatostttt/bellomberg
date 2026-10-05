import { useRef, useState } from 'react';
import type { FocusEvent, KeyboardEvent } from 'react';
import { Moon, Sun, SlidersHorizontal } from 'lucide-react';
import { useLingua } from '../i18n/provider';
import { useInterfaceTheme } from './InterfaceThemeProvider';
import { appearanceCopy } from './appearance-copy';

/** "Aspetto": one compact button that opens the Light/Dark choice. */
export default function AppearanceMenu() {
  const language = useLingua();
  const { theme, setTheme, persistenceAvailable } = useInterfaceTheme();
  const copy = appearanceCopy(language);
  const [open, setOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);

  return (
    <div className="bb-interface-mode-control is-compact" data-testid="appearance-menu"
      // Close on focus leaving the menu or on Escape, through React's delegated handlers:
      // no document listeners, so opening the menu never adds global listeners.
      onBlur={(event: FocusEvent<HTMLDivElement>) => { if (open && !event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false); }}
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => { if (open && event.key === 'Escape') { setOpen(false); toggleRef.current?.focus(); } }}>
      <button ref={toggleRef} type="button" className="bb-interface-menu-toggle" aria-haspopup="true" aria-expanded={open}
        aria-controls="bb-interface-menu" aria-label={copy.menu} title={copy.menu} onClick={() => setOpen(value => !value)}>
        <SlidersHorizontal size={15} aria-hidden="true" />
      </button>
      <div id="bb-interface-menu" className="bb-interface-menu" hidden={!open}>
        <div className="bb-interface-mode-switch bb-interface-theme-switch" role="group"
          aria-label={copy.themeGroup} data-testid="interface-theme-switcher">
          <button type="button" data-theme-choice="light" aria-pressed={theme === 'light'} title={copy.lightTitle} onClick={() => setTheme('light')}>
            <Sun size={12} aria-hidden="true" /><span>{copy.light}</span>
          </button>
          <button type="button" data-theme-choice="dark" aria-pressed={theme === 'dark'} title={copy.darkTitle} onClick={() => setTheme('dark')}>
            <Moon size={12} aria-hidden="true" /><span>{copy.dark}</span>
          </button>
        </div>
      </div>
      {!persistenceAvailable && (
        <span className="bb-interface-mode-storage-warning" role="status">{copy.themePersistenceWarning}</span>
      )}
    </div>
  );
}
