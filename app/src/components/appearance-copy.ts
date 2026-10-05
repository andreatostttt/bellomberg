import type { Lingua } from '../i18n/lingua';
import { traduci } from '../i18n/t';

/** Testi del menu Aspetto e del riquadro di ripristino: stanno nel catalogo (shell.appearance_*). */
export function appearanceCopy(lingua: Lingua) {
  const t = (k: Parameters<typeof traduci>[1]) => traduci(lingua, k);
  return {
    recoveryTitle: t('shell.appearance_recovery_title'),
    retryAction: t('shell.appearance_retry'),
    diagnosticLabel: t('shell.appearance_diagnostic'),
    recoveryNote: t('shell.appearance_recovery_note'),
    themeGroup: t('shell.appearance_theme_group'),
    light: t('shell.appearance_light'),
    dark: t('shell.appearance_dark'),
    lightTitle: t('shell.appearance_light_title'),
    darkTitle: t('shell.appearance_dark_title'),
    themePersistenceWarning: t('shell.appearance_persistence_warning'),
    recoveryLightAction: t('shell.appearance_recovery_light_action'),
    recoveryLightNote: t('shell.appearance_recovery_light_note'),
    menu: t('shell.appearance_menu'),
  };
}
