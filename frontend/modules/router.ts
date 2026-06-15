import type { AppState, RouteParams, SearchField } from '../types';

/**
 * URL parameter names used for routing.
 */
export const URL_PARAMS = {
  zone: 'zone',
  page: 'page',
  query: 'q',
  type: 'type',
  field: 'field',
  allZones: 'all',
} as const;

/**
 * Parse a search string into route state.
 * Pure function for testing - does not access window.
 */
export function parseSearchString(searchString: string): RouteParams {
  const params = new URLSearchParams(searchString);

  const zone = params.get(URL_PARAMS.zone);
  const pageStr = params.get(URL_PARAMS.page);
  const query = params.get(URL_PARAMS.query);
  const type = params.get(URL_PARAMS.type);
  const field = params.get(URL_PARAMS.field) as SearchField | null;
  const allZones = params.get(URL_PARAMS.allZones) === 'true';

  const page = pageStr ? Number.parseInt(pageStr, 10) : 1;

  return {
    zone: zone || null,
    page: Number.isNaN(page) ? 1 : page,
    searchQuery: query || null,
    searchType: type || null,
    searchField: field || 'either',
    searchAllZones: allZones,
  };
}

/**
 * Parse URL parameters into route state.
 * Uses window.location.search - call parseSearchString for testing.
 */
export function parseUrlParams(): RouteParams {
  return parseSearchString(window.location.search);
}

/**
 * Build URL query string from route parameters.
 * Pure function for testing - returns query string without pathname.
 */
export function buildQueryString(params: Partial<RouteParams>): string {
  const urlParams = new URLSearchParams();

  if (params.zone) {
    urlParams.set(URL_PARAMS.zone, params.zone);
  }

  if (params.page && params.page > 1) {
    urlParams.set(URL_PARAMS.page, String(params.page));
  }

  if (params.searchQuery) {
    urlParams.set(URL_PARAMS.query, params.searchQuery);
  }

  if (params.searchType) {
    urlParams.set(URL_PARAMS.type, params.searchType);
  }

  if (params.searchField && params.searchField !== 'either') {
    urlParams.set(URL_PARAMS.field, params.searchField);
  }

  if (params.searchAllZones) {
    urlParams.set(URL_PARAMS.allZones, 'true');
  }

  const queryString = urlParams.toString();
  return queryString ? `?${queryString}` : '';
}

/**
 * Build URL from route parameters.
 * Uses window.location.pathname for empty params - call buildQueryString for testing.
 */
export function buildUrl(params: Partial<RouteParams>): string {
  const queryString = buildQueryString(params);
  return queryString || window.location.pathname;
}

/**
 * Update browser URL without triggering a page reload.
 * Uses replaceState to avoid cluttering browser history.
 */
export function updateUrl(params: Partial<RouteParams>): void {
  const url = buildUrl(params);
  window.history.replaceState(null, '', url);
}

/**
 * Get current route params from app state.
 */
export function getRouteParamsFromState(state: AppState): RouteParams {
  // Determine current page based on context
  let page = 1;

  if (state.isSearching) {
    page = (state.searchCursorHistory?.length || 0) + 1;
  } else if (state.selectedZone) {
    page = (state.recordCursorHistory?.length || 0) + 1;
  } else {
    page = (state.zoneCursorHistory?.length || 0) + 1;
  }

  return {
    zone: state.selectedZone,
    page,
    searchQuery: state.searchQuery || null,
    searchType: state.searchType || null,
    searchField: state.searchField,
    searchAllZones: state.searchAllZones,
  };
}

/**
 * Update URL from current app state.
 */
export function syncUrlFromState(state: AppState): void {
  const params = getRouteParamsFromState(state);
  updateUrl(params);
}

// Method context type for router methods
type RouterMethodContext = AppState & {
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
  selectZone: (zone: string) => Promise<void>;
  performSearch: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  goToZonePage: (page: number) => Promise<void>;
  goToRecordPage: (page: number) => Promise<void>;
  goToSearchPage: (page: number) => Promise<void>;
  clearSearch: () => void;
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
};

/**
 * Create router methods for the app.
 */
export function createRouterMethods(_state: AppState) {
  return {
    /**
     * Navigate to a route based on URL params.
     * Called after authentication to restore the intended destination.
     */
    async navigateToRoute(this: RouterMethodContext, route: RouteParams) {
      // If there's a search query, set up search state
      if (route.searchQuery || route.searchType) {
        this.searchQuery = route.searchQuery || '';
        this.searchType = route.searchType || '';
        this.searchField = route.searchField || 'either';
        this.searchAllZones = route.searchAllZones || false;

        // Load zones first if we need a specific zone for search
        if (route.zone && !route.searchAllZones) {
          await this.loadZones();
          this.selectedZone = route.zone;
        } else if (route.searchAllZones) {
          await this.loadZones();
        }

        // Perform the search
        this.isSearching = true;
        await this.performSearch(null, true);

        // Navigate to specific page if requested
        if (route.page && route.page > 1) {
          await this.goToSearchPage(route.page);
        }
      } else if (route.zone) {
        // Navigate to a specific zone
        await this.loadZones();
        await this.selectZone(route.zone);

        // Navigate to specific page if requested
        if (route.page && route.page > 1) {
          await this.goToRecordPage(route.page);
        }
      } else {
        // Just load zones list
        await this.loadZones();

        // Navigate to specific zone page if requested
        if (route.page && route.page > 1) {
          await this.goToZonePage(route.page);
        }
      }

      // Clear intended route after navigation
      this.intendedRoute = null;

      // Sync URL to canonical form
      syncUrlFromState(this);
    },

    /**
     * Update URL to reflect current state.
     * Call this after any navigation action.
     */
    updateUrlFromState(this: RouterMethodContext) {
      syncUrlFromState(this);
    },
  };
}
