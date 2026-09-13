import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  THEME_MODE_STORAGE_KEY,
  THEME_STORAGE_KEY,
  THEME_STYLE_ID,
  applyTheme,
  buildThemeCss,
  resolveMode,
} from '../../modules/theme';
import type { ThemeConfig } from '../../types';

describe('resolveMode', () => {
  it('prefers stored user choice over config', () => {
    expect(resolveMode('light', 'dark', false)).toBe('dark');
    expect(resolveMode('dark', 'light', true)).toBe('light');
  });

  it('uses config defaultMode when nothing is stored', () => {
    expect(resolveMode('light', null, true)).toBe('light');
    expect(resolveMode('dark', null, false)).toBe('dark');
  });

  it('uses OS preference when defaultMode is auto', () => {
    expect(resolveMode('auto', null, true)).toBe('dark');
    expect(resolveMode('auto', null, false)).toBe('light');
  });

  it('ignores invalid stored values', () => {
    expect(resolveMode('auto', 'purple', false)).toBe('light');
  });
});

describe('buildThemeCss', () => {
  it('emits only overridden tokens per mode', () => {
    const css = buildThemeCss({
      dark: { '--accent-primary': '#ff0000' },
      light: { '--bg-primary': '#ffffff', '--text-primary': '#111' },
    });
    expect(css).toContain(':root[data-theme="dark"]');
    expect(css).toContain('--accent-primary: #ff0000;');
    expect(css).toContain(':root[data-theme="light"]');
    expect(css).toContain('--bg-primary: #ffffff;');
    expect(css).toContain('--text-primary: #111;');
  });

  it('returns empty string when no overrides', () => {
    expect(buildThemeCss({ dark: {}, light: {} })).toBe('');
  });
});

describe('applyTheme', () => {
  const store = new Map<string, string>();
  let styleEl: { id: string; textContent: string } | null = null;

  beforeEach(() => {
    store.clear();
    styleEl = null;

    const localStorageMock = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => store.clear(),
    };
    vi.stubGlobal('localStorage', localStorageMock);

    const html = {
      dataset: {} as Record<string, string>,
    };
    const head = {
      appendChild: (el: { id: string; textContent: string }) => {
        styleEl = el;
      },
    };
    vi.stubGlobal('document', {
      documentElement: html,
      title: 'DNS Zone Editor',
      getElementById: (id: string) =>
        styleEl && styleEl.id === id ? styleEl : null,
      createElement: (tag: string) => {
        if (tag !== 'style') throw new Error(`unexpected tag ${tag}`);
        return {
          id: '',
          textContent: '',
          remove() {
            styleEl = null;
          },
        };
      },
      head,
    });

    vi.stubGlobal('matchMedia', () => ({ matches: true }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sets data-theme, title, CSS overrides, and caches payload', () => {
    const theme: ThemeConfig = {
      appName: 'Acme DNS',
      defaultMode: 'light',
      allowModeToggle: true,
      dark: { '--accent-primary': '#abc' },
      light: {},
    };
    const mode = applyTheme(theme);
    expect(mode).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.title).toBe('Acme DNS');
    expect(styleEl?.id).toBe(THEME_STYLE_ID);
    expect(styleEl?.textContent).toContain('--accent-primary: #abc;');
    expect(JSON.parse(store.get(THEME_STORAGE_KEY) || '{}').appName).toBe(
      'Acme DNS',
    );
  });

  it('honours stored mode over defaultMode', () => {
    store.set(THEME_MODE_STORAGE_KEY, 'dark');
    const mode = applyTheme({
      appName: 'X',
      defaultMode: 'light',
      dark: {},
      light: {},
    });
    expect(mode).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('uses matchMedia for auto when nothing stored', () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: false });
    vi.stubGlobal('matchMedia', matchMedia);
    const mode = applyTheme({
      defaultMode: 'auto',
      dark: {},
      light: {},
    });
    expect(mode).toBe('light');
    expect(matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
  });
});
