import { API_BASE, api } from '../api/client';
import type { AppState, RouteParams } from '../types';
import { syncUrlFromState } from './router';

const API_KEY_STORAGE_KEY = 'dns_zone_manager_api_key';

// Method context type - the full app state with methods (uses any to avoid circular refs)
type MethodContext = AppState & {
  loadZones: () => Promise<void>;
  validateAndSetAuth: () => Promise<void>;
  trackLogin: (authType: string) => Promise<void>;
  trackLogout: () => Promise<void>;
  navigateToRoute: (route: RouteParams) => Promise<void>;
  updateUrlFromState: () => void;
  disconnectZoneLive: () => void;
};

function clearStoredApiKey(): void {
  sessionStorage.removeItem(API_KEY_STORAGE_KEY);
  localStorage.removeItem(API_KEY_STORAGE_KEY);
}

function storeApiKey(apiKey: string, remember: boolean): void {
  clearStoredApiKey();
  const storage = remember ? localStorage : sessionStorage;
  storage.setItem(API_KEY_STORAGE_KEY, apiKey);
}

/**
 * Read a previously stored API key (sessionStorage preferred, then localStorage).
 */
export function readStoredApiKey(): string | null {
  return (
    sessionStorage.getItem(API_KEY_STORAGE_KEY) ||
    localStorage.getItem(API_KEY_STORAGE_KEY)
  );
}

/**
 * Authentication module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createAuthMethods(_state: AppState) {
  return {
    /**
     * Login with API key.
     */
    async loginApiKey(this: MethodContext) {
      if (!this.apiKeyInput) {
        this.loginError = 'Please enter an API key';
        return;
      }

      this.apiKey = this.apiKeyInput;
      await this.validateAndSetAuth();
    },

    /**
     * Validate API key and set authentication state.
     */
    async validateAndSetAuth(this: MethodContext) {
      try {
        const response = await api(`${API_BASE}/auth/validate`, this.apiKey);

        if (response.ok) {
          if (this.apiKey) {
            storeApiKey(this.apiKey, this.rememberApiKey);
          }
          this.authenticated = true;
          this.loginError = '';
          this.trackLogin('api_key');

          // Navigate to intended route if one was stored, otherwise load zones
          if (this.intendedRoute) {
            await this.navigateToRoute(this.intendedRoute);
          } else {
            await this.loadZones();
            this.updateUrlFromState();
          }
        } else if (response.status === 401) {
          this.loginError = 'Invalid API key';
          this.apiKey = null;
          clearStoredApiKey();
        } else if (response.status === 403) {
          this.loginError = 'Access denied';
          this.apiKey = null;
          clearStoredApiKey();
        } else {
          const errorText = await response.text();
          this.loginError = `Authentication failed: ${errorText || response.statusText}`;
          this.apiKey = null;
          clearStoredApiKey();
        }
      } catch (e) {
        this.loginError = `Connection error: ${(e as Error).message}`;
        this.apiKey = null;
        clearStoredApiKey();
      }
    },

    /**
     * Track login event for metrics.
     */
    async trackLogin(this: MethodContext, authType: string) {
      try {
        await api(`${API_BASE}/auth/login`, this.apiKey, {
          method: 'POST',
          body: JSON.stringify({ auth_type: authType }),
        });
      } catch (e) {
        console.warn('Failed to track login:', e);
      }
    },

    /**
     * Track logout event for metrics.
     */
    async trackLogout(this: MethodContext) {
      try {
        await api(`${API_BASE}/auth/logout`, this.apiKey, { method: 'POST' });
      } catch (e) {
        console.warn('Failed to track logout:', e);
      }
    },

    /**
     * Logout and clear state.
     */
    logout(this: MethodContext) {
      this.disconnectZoneLive();
      this.trackLogout();
      this.authenticated = false;
      this.apiKey = null;
      this.apiKeyInput = '';
      clearStoredApiKey();
      this.zones = [];
      this.selectedZone = null;
      this.records = [];
      this.catalogStatus = null;
      this.catalogZones = new Set();
      this.intendedRoute = null;
      // Clear URL params on logout
      syncUrlFromState(this);
      // In proxy-auth mode the session is owned by the front proxy
      // (e.g. oauth2-proxy); redirect there to actually end the session.
      if (this.proxyAuthEnabled) {
        window.location.href = '/oauth2/sign_out';
      }
    },
  };
}
