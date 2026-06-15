import { API_BASE, api } from '../api/client';
import type { AppState, PageSizeMode, SearchResultZone } from '../types';
import { calculatePageSize, getPageSizeForMode } from '../utils/pagination';
import { syncUrlFromState } from './router';

// Method context type - includes state and other methods
type SearchMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  performSearch: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  clearSearch: () => void;
  searchGoToFirstPage: () => void;
  performSearchWithOffset: (offset: number) => Promise<void>;
};

/**
 * Merge and dedupe search results from multiple searches.
 */
function mergeResults(
  results1: SearchResultZone[],
  results2: SearchResultZone[],
): SearchResultZone[] {
  const merged = [...results1];
  for (const zone2 of results2) {
    const existing = merged.find((z) => z.zone === zone2.zone);
    if (existing) {
      const seen = new Set(existing.rrsets.map((r) => `${r.name}|${r.type}`));
      for (const rrset of zone2.rrsets) {
        const key = `${rrset.name}|${rrset.type}`;
        if (!seen.has(key)) {
          existing.rrsets.push(rrset);
          seen.add(key);
        }
      }
    } else {
      merged.push(zone2);
    }
  }
  return merged;
}

/**
 * Search module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createSearchMethods(_state: AppState) {
  return {
    /**
     * Perform search with pagination.
     */
    async performSearch(
      this: SearchMethodContext,
      cursor: string | null = null,
      resetHistory = true,
    ) {
      if (!this.searchQuery && !this.searchType) {
        this.clearSearch();
        return;
      }

      this.isSearching = true;
      this.searchCurrentCursor = cursor;

      if (this.searchPageSizeMode === 'auto') {
        this.searchPageSize = calculatePageSize();
      }

      // Build search pattern - if only filtering by type, use wildcard pattern
      // so the backend can filter (backend requires at least one pattern)
      const pattern =
        this.searchQuery || this.searchType
          ? encodeURIComponent(
              this.searchQuery ? `.*${this.searchQuery}.*` : '.*',
            )
          : '';
      const typeParam = this.searchType ? `type=${this.searchType}&` : '';

      const paginationParams: string[] = [];
      if (this.searchPageSize) {
        paginationParams.push(`limit=${this.searchPageSize}`);
      }
      if (cursor) {
        paginationParams.push(`after=${encodeURIComponent(cursor)}`);
      }
      const paginationStr =
        paginationParams.length > 0 ? `${paginationParams.join('&')}&` : '';

      try {
        if (this.searchAllZones) {
          const paramName =
            this.searchField === 'data' ? 'value_pattern' : 'name_pattern';

          if (this.searchField === 'either' && pattern && !cursor) {
            const [nameResp, dataResp] = await Promise.all([
              api(
                `${API_BASE}/search?name_pattern=${pattern}&${typeParam}limit=${this.searchPageSize || 100}`,
                this.apiKey,
              ),
              api(
                `${API_BASE}/search?value_pattern=${pattern}&${typeParam}limit=${this.searchPageSize || 100}`,
                this.apiKey,
              ),
            ]);
            const nameData = nameResp.ok
              ? await nameResp.json()
              : { results: [], total_count: 0 };
            const dataData = dataResp.ok
              ? await dataResp.json()
              : { results: [], total_count: 0 };
            this.searchResults = mergeResults(
              nameData.results || [],
              dataData.results || [],
            );
            this.searchTotalCount = this.searchResults.reduce(
              (sum, z) => sum + z.rrsets.length,
              0,
            );
            this.searchNextCursor = null;
            this.searchHasMore = false;
          } else {
            const url = `${API_BASE}/search?${pattern ? `${paramName}=${pattern}&` : ''}${typeParam}${paginationStr}`;
            const response = await api(url, this.apiKey);
            if (response.ok) {
              const data = await response.json();
              this.searchResults = (data.results || []) as SearchResultZone[];
              if ('total_count' in data) {
                this.searchTotalCount = data.total_count;
                this.searchNextCursor = data.next_cursor;
                this.searchHasMore = data.has_more;
              } else {
                this.searchTotalCount = this.searchResults.reduce(
                  (sum, z) => sum + z.rrsets.length,
                  0,
                );
                this.searchNextCursor = null;
                this.searchHasMore = false;
              }
            }
          }
        } else {
          if (!this.selectedZone) {
            this.searchResults = [];
            return;
          }

          const zoneUrl = `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/search?`;

          if (this.searchField === 'either' && pattern && !cursor) {
            const [nameResp, dataResp] = await Promise.all([
              api(
                `${zoneUrl}name_pattern=${pattern}&${typeParam}limit=${this.searchPageSize || 100}`,
                this.apiKey,
              ),
              api(
                `${zoneUrl}value_pattern=${pattern}&${typeParam}limit=${this.searchPageSize || 100}`,
                this.apiKey,
              ),
            ]);
            const nameData = nameResp.ok
              ? await nameResp.json()
              : { zone: this.selectedZone, results: [], total_count: 0 };
            const dataData = dataResp.ok
              ? await dataResp.json()
              : { zone: this.selectedZone, results: [], total_count: 0 };
            const mergedRrsets = mergeResults(
              [
                {
                  zone: nameData.zone,
                  serial: nameData.serial,
                  rrsets: nameData.results || [],
                },
              ],
              [
                {
                  zone: dataData.zone,
                  serial: dataData.serial,
                  rrsets: dataData.results || [],
                },
              ],
            );
            this.searchResults = mergedRrsets;
            this.searchTotalCount = mergedRrsets.reduce(
              (sum, z) => sum + z.rrsets.length,
              0,
            );
            this.searchNextCursor = null;
            this.searchHasMore = false;
          } else {
            const paramName =
              this.searchField === 'data' ? 'value_pattern' : 'name_pattern';
            const url = `${zoneUrl}${pattern ? `${paramName}=${pattern}&` : ''}${typeParam}${paginationStr}`;
            const response = await api(url, this.apiKey);
            if (response.ok) {
              const data = await response.json();
              this.searchResults = [
                {
                  zone: data.zone,
                  serial: data.serial,
                  rrsets: data.results || [],
                },
              ];
              if ('total_count' in data) {
                this.searchTotalCount = data.total_count;
                this.searchNextCursor = data.next_cursor;
                this.searchHasMore = data.has_more;
              } else {
                this.searchTotalCount = (data.results || []).length;
                this.searchNextCursor = null;
                this.searchHasMore = false;
              }
            }
          }
        }

        if (resetHistory) {
          this.searchCursorHistory = [];
        }

        // Update URL with search params
        syncUrlFromState(this);
      } catch (e) {
        this.toast(`Search failed: ${(e as Error).message}`, 'error');
      }
    },

    /**
     * Search pagination - go to first page.
     */
    searchGoToFirstPage(this: SearchMethodContext) {
      this.searchCursorHistory = [];
      this.performSearch(null, true);
    },

    /**
     * Search pagination - go to previous page.
     */
    async searchGoToPrevPage(this: SearchMethodContext) {
      if (this.searchCursorHistory.length === 0) return;
      const prevCursor = this.searchCursorHistory.pop();
      if (prevCursor !== undefined) {
        await this.performSearch(prevCursor, false);
      }
    },

    /**
     * Search pagination - go to next page.
     */
    async searchGoToNextPage(this: SearchMethodContext) {
      if (this.searchHasMore && this.searchNextCursor) {
        this.searchCursorHistory.push(this.searchCurrentCursor);
        await this.performSearch(this.searchNextCursor, false);
      }
    },

    /**
     * Search pagination - go to last page.
     */
    async searchGoToLastPage(this: SearchMethodContext) {
      while (this.searchHasMore && this.searchNextCursor) {
        this.searchCursorHistory.push(this.searchCurrentCursor);
        await this.performSearch(this.searchNextCursor, false);
      }
    },

    /**
     * Search pagination - go to a specific page using offset.
     */
    async goToSearchPage(this: SearchMethodContext, targetPage: number) {
      const pageSize = this.searchPageSize || 25;
      const totalPages = Math.ceil(this.searchTotalCount / pageSize);

      // Validate target page
      let page = targetPage;
      if (Number.isNaN(page) || page < 1) page = 1;
      if (page > totalPages) page = totalPages;

      // Calculate offset
      const offset = (page - 1) * pageSize;

      // Update cursor history to reflect the page position
      // Build synthetic history so prev/next still work
      this.searchCursorHistory = [];
      for (let i = 0; i < page - 1; i++) {
        this.searchCursorHistory.push(`__page_${i}__`);
      }

      // Perform search with offset
      await this.performSearchWithOffset(offset);

      // Update URL with current page
      syncUrlFromState(this);
    },

    /**
     * Perform search with a specific offset.
     */
    async performSearchWithOffset(this: SearchMethodContext, offset: number) {
      if (!this.searchQuery) return;

      try {
        const params = new URLSearchParams();
        params.set('offset', offset.toString());
        if (this.searchPageSize) {
          params.set('limit', this.searchPageSize.toString());
        }

        // Determine which pattern to use based on searchField
        if (this.searchField === 'name' || this.searchField === 'either') {
          params.set('name_pattern', this.searchQuery);
        }
        if (this.searchField === 'data' || this.searchField === 'either') {
          params.set('value_pattern', this.searchQuery);
        }
        if (this.searchType) {
          params.set('type', this.searchType);
        }

        let response: Response;
        if (this.searchAllZones) {
          response = await api(`${API_BASE}/search?${params}`, this.apiKey);
        } else if (this.selectedZone) {
          response = await api(
            `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/search?${params}`,
            this.apiKey,
          );
        } else {
          return;
        }

        if (response.ok) {
          const data = await response.json();

          if (this.searchAllZones) {
            // Global search response
            if ('results' in data) {
              this.searchResults = data.results;
              this.searchTotalCount = data.total_count || 0;
              this.searchHasMore = data.has_more || false;
              this.searchNextCursor = data.next_cursor || null;
              this.searchCurrentCursor = `__offset_${offset}__`;
            }
          } else {
            // Zone search response
            if ('results' in data) {
              this.searchResults = [
                {
                  zone: data.zone,
                  serial: data.serial,
                  rrsets: data.results,
                },
              ];
              this.searchTotalCount = data.total_count || data.results.length;
              this.searchHasMore = data.has_more || false;
              this.searchNextCursor = data.next_cursor || null;
              this.searchCurrentCursor = `__offset_${offset}__`;
            }
          }
        }
      } catch (e) {
        this.toast(`Search failed: ${(e as Error).message}`, 'error');
      }
    },

    /**
     * Change search page size mode.
     */
    changeSearchPageSizeMode(this: SearchMethodContext, newMode: PageSizeMode) {
      this.searchPageSizeMode = newMode;
      this.searchPageSize = getPageSizeForMode(newMode, 'records');
      this.searchCursorHistory = [];
      if (this.searchQuery) {
        this.performSearch(null, true);
      }
    },

    /**
     * Clear search state.
     */
    clearSearch(this: SearchMethodContext) {
      this.searchQuery = '';
      this.searchType = '';
      this.searchResults = [];
      this.isSearching = false;
      this.searchCurrentCursor = null;
      this.searchNextCursor = null;
      this.searchTotalCount = 0;
      this.searchHasMore = false;
      this.searchCursorHistory = [];
      // Update URL to remove search params
      syncUrlFromState(this);
    },
  };
}

/**
 * Create computed properties for search pagination.
 * NOTE: Computed properties can still use state closure for reads.
 */
export function createSearchPaginationComputed(state: AppState) {
  return {
    get searchCurrentPage(): number {
      return state.searchCursorHistory.length + 1;
    },

    get searchTotalPages(): number {
      if (!state.searchPageSize || state.searchTotalCount === 0) return 1;
      return Math.ceil(state.searchTotalCount / state.searchPageSize);
    },

    get searchStartIndex(): number {
      if (state.searchTotalCount === 0) return 0;
      return (this.searchCurrentPage - 1) * (state.searchPageSize || 25) + 1;
    },

    get searchEndIndex(): number {
      return this.searchStartIndex + 24;
    },
  };
}
