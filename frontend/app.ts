import { formatDNSError } from './api/client';
import { createAtomicMethods } from './modules/atomic';
import { createAuditMethods } from './modules/audit';
import { createAuthMethods } from './modules/auth';
import { createHistoryMethods } from './modules/history';
import { createNsupdateMethods } from './modules/nsupdate';
import { createRecordMethods } from './modules/records';
import { createReverseMethods } from './modules/reverse';
import { createRouterMethods, parseUrlParams } from './modules/router';
import { createScheduledMethods } from './modules/scheduled';
import { createSearchMethods } from './modules/search';
import { createSortingMethods } from './modules/sorting';
import { createToastMethods } from './modules/toast';
import { createZoneMethods } from './modules/zones';
import { createInitialState } from './state';
import type {
  AppConfig,
  AppState,
  FlattenedSearchResult,
  RRset,
  RouteParams,
  Zone,
} from './types';
import {
  COMMON_CLASSES,
  COMMON_TYPES,
  getFinalRecordClass,
  getFinalRecordType,
  getRecordExamples,
  typeNumberToName,
} from './utils/dns';
import {
  calculatePageSize,
  calculateZonePageSize,
  getPageSizeForMode,
} from './utils/pagination';

/**
 * Type for Alpine.js `this` context in getters and methods.
 * This represents the fully-constructed app state with all methods and properties.
 */
type AlpineThis = AppState & {
  // Computed properties (added via defineProperty)
  readonly filteredZones: Zone[];
  readonly filteredRecords: RRset[];
  readonly hasNonINClass: boolean;
  readonly sortedSearchResults: FlattenedSearchResult[];
  readonly searchHasNonINClass: boolean;
  readonly recordCurrentPage: number;
  readonly recordTotalPages: number;
  readonly recordStartIndex: number;
  readonly recordEndIndex: number;
  readonly zoneCurrentPage: number;
  readonly zoneTotalPages: number;
  readonly zoneStartIndex: number;
  readonly zoneEndIndex: number;
  readonly searchCurrentPage: number;
  readonly searchTotalPages: number;
  readonly searchStartIndex: number;
  readonly searchEndIndex: number;
  // Methods
  updatePageSizesFromMode: () => void;
  validateAndSetAuth: () => Promise<void>;
  trackLogin: (type: string) => Promise<void>;
  loadZones: () => Promise<void>;
  navigateToRoute: (route: RouteParams) => Promise<void>;
  updateUrlFromState: () => void;
  // Utility functions
  getPageSizeForMode: typeof getPageSizeForMode;
};

/**
 * Create the main DNS app component.
 */
export function createApp(config: AppConfig) {
  const state = createInitialState(config);

  // Create module methods
  const toastMethods = createToastMethods(state);
  const authMethods = createAuthMethods(state);
  const zoneMethods = createZoneMethods(state);
  const recordMethods = createRecordMethods(state);
  const searchMethods = createSearchMethods(state);
  const sortingMethods = createSortingMethods(state);
  const atomicMethods = createAtomicMethods(state);
  const nsupdateMethods = createNsupdateMethods(state);
  const reverseMethods = createReverseMethods(state);
  const historyMethods = createHistoryMethods(state);
  const scheduledMethods = createScheduledMethods(state);
  const auditMethods = createAuditMethods(state);
  const routerMethods = createRouterMethods(state);

  // Assign methods directly to state object so Alpine.js reactive updates work
  // (spreading state creates a copy; modules read from original state via closure)
  Object.assign(state, toastMethods);
  Object.assign(state, authMethods);
  Object.assign(state, zoneMethods);
  Object.assign(state, recordMethods);
  Object.assign(state, searchMethods);
  Object.assign(state, sortingMethods);
  Object.assign(state, atomicMethods);
  Object.assign(state, nsupdateMethods);
  Object.assign(state, reverseMethods);
  Object.assign(state, historyMethods);
  Object.assign(state, scheduledMethods);
  Object.assign(state, auditMethods);
  Object.assign(state, routerMethods);

  // Add constants and utilities to state
  Object.assign(state, {
    commonTypes: COMMON_TYPES,
    commonClasses: COMMON_CLASSES,
    formatDNSError,
    getRecordExamples,
    getFinalRecordType,
    getFinalRecordClass,
    typeNumberToName,
    calculatePageSize,
    calculateZonePageSize,
    getPageSizeForMode,
  });

  // CRITICAL FIX: Use Object.defineProperty to create getters that read from `state` via closure.
  // The issue was that Object.assign with getter object literals doesn't correctly bind `this`
  // when Alpine wraps the object. By using defineProperty with closures over `state`, we ensure
  // the getters always read from the same state object that methods update.

  const s = state as unknown as AlpineThis;

  Object.defineProperty(s, 'filteredZones', {
    get() {
      const self = this as AlpineThis;
      if (!self.zoneFilter?.trim()) {
        return self.zones || [];
      }
      const filter = self.zoneFilter.toLowerCase().trim();
      return (self.zones || []).filter((z: Zone) =>
        z.zone.toLowerCase().includes(filter),
      );
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'filteredRecords', {
    get() {
      const self = this as AlpineThis;
      const records = self.records || [];
      if (!self.sortField) return records;
      return [...records].sort((a: RRset, b: RRset) => {
        let cmp = 0;
        if (self.sortField === 'name') cmp = a.name.localeCompare(b.name);
        else if (self.sortField === 'type') cmp = a.type.localeCompare(b.type);
        else if (self.sortField === 'ttl') cmp = a.ttl - b.ttl;
        else if (self.sortField === 'rdclass')
          cmp = (a.rdclass || 'IN').localeCompare(b.rdclass || 'IN');
        return self.sortDirection === 'desc' ? -cmp : cmp;
      });
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'hasNonINClass', {
    get() {
      const self = this as AlpineThis;
      return (self.records || []).some(
        (r: RRset) => r.rdclass && r.rdclass !== 'IN',
      );
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'sortedSearchResults', {
    get() {
      const self = this as AlpineThis;
      const flattened: FlattenedSearchResult[] = [];
      for (const result of self.searchResults || []) {
        for (const record of result.rrsets || []) {
          flattened.push({
            zone: result.zone,
            name: record.name,
            type: record.type,
            rdclass: record.rdclass || 'IN',
            ttl: record.ttl,
            records: record.records,
          });
        }
      }
      if (!self.searchSortField) return flattened;
      return flattened.sort(
        (a: FlattenedSearchResult, b: FlattenedSearchResult) => {
          let cmp = 0;
          if (self.searchSortField === 'zone')
            cmp = (a.zone || '').localeCompare(b.zone || '');
          else if (self.searchSortField === 'name')
            cmp = a.name.localeCompare(b.name);
          else if (self.searchSortField === 'type')
            cmp = a.type.localeCompare(b.type);
          else if (self.searchSortField === 'ttl') cmp = a.ttl - b.ttl;
          else if (self.searchSortField === 'rdclass')
            cmp = (a.rdclass || 'IN').localeCompare(b.rdclass || 'IN');
          return self.searchSortDirection === 'desc' ? -cmp : cmp;
        },
      );
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'searchHasNonINClass', {
    get() {
      const self = this as AlpineThis;
      for (const result of self.searchResults || []) {
        for (const record of result.rrsets || []) {
          if (record.rdclass && record.rdclass !== 'IN') return true;
        }
      }
      return false;
    },
    enumerable: true,
  });

  // Record pagination computed - all use `this` for Alpine dependency tracking
  Object.defineProperty(s, 'recordCurrentPage', {
    get() {
      const self = this as AlpineThis;
      return (self.recordCursorHistory?.length || 0) + 1;
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'recordTotalPages', {
    get() {
      const self = this as AlpineThis;
      if (!self.pageSize || self.totalRecords === 0) return 1;
      return Math.ceil(self.totalRecords / self.pageSize);
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'recordStartIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.totalRecords === 0) return 0;
      const currentPage = (self.recordCursorHistory?.length || 0) + 1;
      return (
        (currentPage - 1) * (self.pageSize || self.records?.length || 0) + 1
      );
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'recordEndIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.totalRecords === 0) return 0;
      const currentPage = (self.recordCursorHistory?.length || 0) + 1;
      const startIndex =
        (currentPage - 1) * (self.pageSize || self.records?.length || 0) + 1;
      return startIndex + (self.records?.length || 0) - 1;
    },
    enumerable: true,
  });

  // Zone pagination computed - all use `this` for Alpine dependency tracking
  Object.defineProperty(s, 'zoneCurrentPage', {
    get() {
      const self = this as AlpineThis;
      return (self.zoneCursorHistory?.length || 0) + 1;
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'zoneTotalPages', {
    get() {
      const self = this as AlpineThis;
      if (!self.zonePageSize || self.zoneTotalCount === 0) return 1;
      return Math.ceil(self.zoneTotalCount / self.zonePageSize);
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'zoneStartIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.zoneTotalCount === 0) return 0;
      const currentPage = (self.zoneCursorHistory?.length || 0) + 1;
      return (
        (currentPage - 1) * (self.zonePageSize || self.zones?.length || 0) + 1
      );
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'zoneEndIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.zoneTotalCount === 0) return 0;
      const currentPage = (self.zoneCursorHistory?.length || 0) + 1;
      const startIndex =
        (currentPage - 1) * (self.zonePageSize || self.zones?.length || 0) + 1;
      return startIndex + (self.zones?.length || 0) - 1;
    },
    enumerable: true,
  });

  // Search pagination computed - all use `this` for Alpine dependency tracking
  Object.defineProperty(s, 'searchCurrentPage', {
    get() {
      const self = this as AlpineThis;
      return (self.searchCursorHistory?.length || 0) + 1;
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'searchTotalPages', {
    get() {
      const self = this as AlpineThis;
      if (!self.searchPageSize || self.searchTotalCount === 0) return 1;
      return Math.ceil(self.searchTotalCount / self.searchPageSize);
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'searchStartIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.searchTotalCount === 0) return 0;
      const currentPage = (self.searchCursorHistory?.length || 0) + 1;
      const sortedLen = self.sortedSearchResults?.length || 0;
      return (currentPage - 1) * (self.searchPageSize || sortedLen) + 1;
    },
    enumerable: true,
  });

  Object.defineProperty(s, 'searchEndIndex', {
    get() {
      const self = this as AlpineThis;
      if (self.searchTotalCount === 0) return 0;
      const currentPage = (self.searchCursorHistory?.length || 0) + 1;
      const sortedLen = self.sortedSearchResults?.length || 0;
      const startIndex =
        (currentPage - 1) * (self.searchPageSize || sortedLen) + 1;
      return startIndex + sortedLen - 1;
    },
    enumerable: true,
  });

  // Add additional methods that need state closure
  Object.assign(s, {
    // Initialization - MUST use `this` (Alpine proxy) for method calls to trigger reactivity
    async init() {
      const self = this as unknown as AlpineThis;

      // Initialize page sizes based on screen height (auto mode)
      self.updatePageSizesFromMode();

      // Recalculate page sizes on window resize (only affects auto mode)
      window.addEventListener('resize', () => {
        if (self.pageSizeMode === 'auto') {
          self.pageSize = calculatePageSize();
        }
        if (self.zonePageSizeMode === 'auto') {
          self.zonePageSize = calculateZonePageSize();
        }
        if (self.searchPageSizeMode === 'auto') {
          self.searchPageSize = calculatePageSize();
        }
      });

      // Parse URL params early to capture intended route
      const urlParams = new URLSearchParams(window.location.search);
      const routeParams = parseUrlParams();

      // Store intended route if there's something meaningful in the URL
      // (zone, search, page > 1, scheduled/audit view, or scheduled change id)
      const hasRoute =
        routeParams.zone ||
        routeParams.searchQuery ||
        routeParams.searchType ||
        routeParams.page > 1 ||
        routeParams.view ||
        routeParams.change;
      if (hasRoute) {
        self.intendedRoute = routeParams;
      }

      // Fetch UI config from API (for decoupled frontend)
      try {
        const response = await fetch('/ui/config');
        if (response.ok) {
          const uiConfig = await response.json();
          self.azureEnabled = uiConfig.azureEnabled ?? false;
          self.proxyAuthEnabled = uiConfig.proxyAuthEnabled ?? false;
          self.currentUser = uiConfig.user?.email ?? null;
          self.appVersion = uiConfig.version ?? '';
        }
      } catch {
        // Config fetch failed, keep defaults
        console.warn('Failed to fetch UI config, using defaults');
      }

      // Trusted reverse-proxy auth: a front proxy (e.g. Traefik + oauth2-proxy)
      // already authenticated the user and injects the identity header on every
      // request. Skip the login screen entirely.
      if (self.proxyAuthEnabled) {
        self.authenticated = true;
        self.trackLogin('proxy');
        if (self.intendedRoute) {
          await self.navigateToRoute(self.intendedRoute);
        } else {
          await self.loadZones();
          self.updateUrlFromState();
        }
        return;
      }

      // Check for Azure callback - azure_token param means we're returning from Azure login
      if (urlParams.has('azure_token')) {
        self.authenticated = true;
        self.trackLogin('azure_ad');

        // Clean up the azure_token from URL but preserve route params
        const cleanParams = new URLSearchParams(window.location.search);
        cleanParams.delete('azure_token');
        const cleanUrl = cleanParams.toString()
          ? `${window.location.pathname}?${cleanParams.toString()}`
          : window.location.pathname;
        window.history.replaceState(null, '', cleanUrl);

        // Navigate to intended route or load zones
        if (self.intendedRoute) {
          await self.navigateToRoute(self.intendedRoute);
        } else {
          await self.loadZones();
          self.updateUrlFromState();
        }
        return;
      }

      // Check for stored API key
      const storedKey = localStorage.getItem('dns_zone_manager_api_key');
      if (storedKey) {
        self.apiKey = storedKey;
        // validateAndSetAuth will call navigateToRoute if successful
        await self.validateAndSetAuth();
      }
      // If not authenticated, the login screen will be shown
      // intendedRoute is preserved so we can navigate after login
    },

    // Page size helpers - MUST use `this` for Alpine reactivity
    updatePageSizesFromMode() {
      const self = this as unknown as AlpineThis;
      self.pageSize = getPageSizeForMode(self.pageSizeMode, 'records');
      self.zonePageSize = getPageSizeForMode(self.zonePageSizeMode, 'zones');
      self.searchPageSize = getPageSizeForMode(
        self.searchPageSizeMode,
        'records',
      );
    },

    // Normalize record type input
    normalizeRecordType() {
      // Don't update while typing to avoid cursor jumping
    },
  });

  return s;
}

// Export type for the app
export type DnsApp = ReturnType<typeof createApp>;
