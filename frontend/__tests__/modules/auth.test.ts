import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAuthMethods, readStoredApiKey } from '../../modules/auth';
import type { AppState } from '../../types';

describe('auth storage', () => {
  const store: Record<string, string> = {};
  const session: Record<string, string> = {};

  beforeEach(() => {
    Object.keys(store).forEach((k) => delete store[k]);
    Object.keys(session).forEach((k) => delete session[k]);
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
    });
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => session[k] ?? null,
      setItem: (k: string, v: string) => {
        session[k] = v;
      },
      removeItem: (k: string) => {
        delete session[k];
      },
    });
    vi.stubGlobal('window', {
      location: { pathname: '/', search: '', href: '/' },
      history: { replaceState: vi.fn() },
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('readStoredApiKey prefers sessionStorage', () => {
    sessionStorage.setItem('dns_zone_manager_api_key', 'session-key');
    localStorage.setItem('dns_zone_manager_api_key', 'local-key');
    expect(readStoredApiKey()).toBe('session-key');
  });

  it('logout clears apiKeyInput and both storages', async () => {
    const methods = createAuthMethods({} as AppState);
    const ctx = {
      apiKey: 'k',
      apiKeyInput: 'k',
      authenticated: true,
      proxyAuthEnabled: false,
      zones: ['z'],
      selectedZone: 'z',
      records: [1],
      catalogStatus: {},
      catalogZones: new Set(['z']),
      intendedRoute: null,
      disconnectZoneLive: vi.fn(),
      trackLogout: vi.fn(),
    };
    localStorage.setItem('dns_zone_manager_api_key', 'k');
    sessionStorage.setItem('dns_zone_manager_api_key', 'k');
    methods.logout.call(ctx as never);
    expect(ctx.apiKey).toBeNull();
    expect(ctx.apiKeyInput).toBe('');
    expect(localStorage.getItem('dns_zone_manager_api_key')).toBeNull();
    expect(sessionStorage.getItem('dns_zone_manager_api_key')).toBeNull();
  });
});
