import type { AppState, ThemeConfig, ThemeMode, ThemePalette } from '../types';

export const THEME_STORAGE_KEY = 'dns-zone-manager.theme';
export const THEME_MODE_STORAGE_KEY = 'dns-zone-manager.themeMode';
export const THEME_STYLE_ID = 'theme-overrides';

export type ResolvedThemeMode = 'dark' | 'light';

type ThemeMethodContext = AppState & {
  applyThemeFromConfig: (theme: ThemeConfig | undefined) => void;
};

/**
 * Resolve the effective colour mode.
 *
 * Precedence: explicit user choice in localStorage → config defaultMode →
 * OS preference when defaultMode is ``auto``.
 */
export function resolveMode(
  defaultMode: ThemeMode = 'dark',
  stored: string | null = null,
  prefersDark: boolean = true,
): ResolvedThemeMode {
  if (stored === 'dark' || stored === 'light') {
    return stored;
  }
  if (defaultMode === 'light' || defaultMode === 'dark') {
    return defaultMode;
  }
  return prefersDark ? 'dark' : 'light';
}

/**
 * Build a CSS text block with only the overridden tokens for each mode.
 */
export function buildThemeCss(theme: ThemeConfig): string {
  const blocks: string[] = [];
  const dark = theme.dark ?? {};
  const light = theme.light ?? {};
  if (Object.keys(dark).length > 0) {
    blocks.push(paletteToRule(':root[data-theme="dark"]', dark));
  }
  if (Object.keys(light).length > 0) {
    blocks.push(paletteToRule(':root[data-theme="light"]', light));
  }
  return blocks.join('\n');
}

function paletteToRule(selector: string, palette: ThemePalette): string {
  const decls = Object.entries(palette)
    .filter(([, value]) => typeof value === 'string' && value.length > 0)
    .map(([name, value]) => `  ${name}: ${value};`)
    .join('\n');
  return `${selector} {\n${decls}\n}`;
}

function readStoredMode(): string | null {
  try {
    return localStorage.getItem(THEME_MODE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredMode(mode: ResolvedThemeMode): void {
  try {
    localStorage.setItem(THEME_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore quota / private-mode failures
  }
}

function readCachedTheme(): ThemeConfig | null {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as ThemeConfig;
  } catch {
    return null;
  }
}

function writeCachedTheme(theme: ThemeConfig): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(theme));
  } catch {
    // Ignore quota / private-mode failures
  }
}

function injectOverrideCss(css: string): void {
  let el = document.getElementById(THEME_STYLE_ID) as HTMLStyleElement | null;
  if (!css) {
    el?.remove();
    return;
  }
  if (!el) {
    el = document.createElement('style');
    el.id = THEME_STYLE_ID;
    document.head.appendChild(el);
  }
  el.textContent = css;
}

function systemPrefersDark(): boolean {
  try {
    const media = globalThis.matchMedia?.(
      '(prefers-color-scheme: dark)',
    );
    return media?.matches ?? true;
  } catch {
    return true;
  }
}

/**
 * Apply theme payload to the document (mode attribute, CSS overrides, title).
 * Returns the resolved mode.
 */
export function applyTheme(theme: ThemeConfig): ResolvedThemeMode {
  const mode = resolveMode(
    theme.defaultMode ?? 'dark',
    readStoredMode(),
    systemPrefersDark(),
  );
  document.documentElement.dataset.theme = mode;
  injectOverrideCss(buildThemeCss(theme));
  if (theme.appName) {
    document.title = theme.appName;
  }
  writeCachedTheme(theme);
  return mode;
}

/**
 * Synchronous pre-paint apply from localStorage cache.
 * Called from an inline head script before Alpine boots.
 */
export function applyCachedTheme(): ResolvedThemeMode {
  const cached = readCachedTheme();
  const mode = resolveMode(
    cached?.defaultMode ?? 'dark',
    readStoredMode(),
    systemPrefersDark(),
  );
  document.documentElement.dataset.theme = mode;
  if (cached) {
    injectOverrideCss(buildThemeCss(cached));
    if (cached.appName) {
      document.title = cached.appName;
    }
  }
  return mode;
}

/**
 * Theme methods bound onto the Alpine app state.
 */
export function createThemeMethods(_state: AppState) {
  return {
    applyThemeFromConfig(this: ThemeMethodContext, theme: ThemeConfig | undefined) {
      const payload: ThemeConfig = theme ?? {
        appName: 'DNS Zone Editor',
        defaultMode: 'dark',
        allowModeToggle: true,
        logo: null,
        light: {},
        dark: {},
      };
      this.appName = payload.appName || 'DNS Zone Editor';
      this.allowModeToggle = payload.allowModeToggle !== false;
      this.logoUrl = payload.logo?.url ?? null;
      this.logoAlt = payload.logo?.alt ?? 'Home';
      this.themeMode = applyTheme(payload);
    },

    toggleThemeMode(this: ThemeMethodContext) {
      if (!this.allowModeToggle) return;
      const next: ResolvedThemeMode =
        this.themeMode === 'dark' ? 'light' : 'dark';
      writeStoredMode(next);
      document.documentElement.dataset.theme = next;
      this.themeMode = next;
    },
  };
}
