import type {
  AppState,
  FlattenedSearchResult,
  RRset,
  SortField,
} from '../types';

// Method context type - includes state properties
type SortingMethodContext = AppState;

/**
 * Sorting module for records and search results.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createSortingMethods(_state: AppState) {
  return {
    /**
     * Sort records by field.
     */
    sortBy(this: SortingMethodContext, field: SortField) {
      if (this.sortField === field) {
        this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortField = field;
        this.sortDirection = 'asc';
      }
    },

    /**
     * Get sort icon for a field.
     */
    getSortIcon(this: SortingMethodContext, field: SortField): string {
      if (this.sortField !== field) return '↕';
      return this.sortDirection === 'asc' ? '↑' : '↓';
    },

    /**
     * Sort search results by field.
     */
    sortSearchBy(this: SortingMethodContext, field: SortField) {
      if (this.searchSortField === field) {
        this.searchSortDirection =
          this.searchSortDirection === 'asc' ? 'desc' : 'asc';
      } else {
        this.searchSortField = field;
        this.searchSortDirection = 'asc';
      }
    },

    /**
     * Get sort icon for search field.
     */
    getSearchSortIcon(this: SortingMethodContext, field: SortField): string {
      if (this.searchSortField !== field) return '↕';
      return this.searchSortDirection === 'asc' ? '↑' : '↓';
    },
  };
}

/**
 * Create computed properties for sorting.
 * NOTE: Computed properties can still use state closure for reads.
 */
export function createSortingComputed(state: AppState) {
  return {
    /**
     * Filtered and sorted records.
     */
    get filteredRecords(): RRset[] {
      let filtered = state.records;
      if (state.searchType) {
        filtered = filtered.filter((r) => r.type === state.searchType);
      }

      const sorted = [...filtered].sort((a, b) => {
        let aVal: string | number;
        let bVal: string | number;

        switch (state.sortField) {
          case 'name':
            aVal = a.name.toLowerCase();
            bVal = b.name.toLowerCase();
            break;
          case 'type':
            aVal = a.type;
            bVal = b.type;
            break;
          case 'rdclass':
            aVal = a.rdclass || 'IN';
            bVal = b.rdclass || 'IN';
            break;
          case 'ttl':
            aVal = a.ttl;
            bVal = b.ttl;
            break;
          case 'data':
            aVal = a.records.join(',').toLowerCase();
            bVal = b.records.join(',').toLowerCase();
            break;
          default:
            return 0;
        }

        if (aVal < bVal) return state.sortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return state.sortDirection === 'asc' ? 1 : -1;
        return 0;
      });

      return sorted;
    },

    /**
     * Check if any records have non-IN class.
     */
    get hasNonINClass(): boolean {
      return state.records.some((r) => r.rdclass && r.rdclass !== 'IN');
    },

    /**
     * Flattened and sorted search results.
     */
    get sortedSearchResults(): FlattenedSearchResult[] {
      const flattened: FlattenedSearchResult[] = [];

      for (const result of state.searchResults) {
        for (const record of result.rrsets) {
          flattened.push({
            zone: result.zone,
            name: record.name,
            type: record.type,
            rdclass: record.rdclass || 'IN',
            ttl: record.ttl,
            records: record.records,
            // IDN fields (for zone/name) and UTF-8 fields (for records)
            zone_is_idn: result.zone_is_idn,
            zone_utf8: result.zone_utf8,
            name_is_idn: record.name_is_idn,
            name_utf8: record.name_utf8,
            records_is_utf8: record.records_is_utf8,
            records_utf8: record.records_utf8,
          });
        }
      }

      return flattened.sort((a, b) => {
        let aVal: string | number;
        let bVal: string | number;

        switch (state.searchSortField) {
          case 'zone':
            aVal = a.zone.toLowerCase();
            bVal = b.zone.toLowerCase();
            break;
          case 'name':
            aVal = a.name.toLowerCase();
            bVal = b.name.toLowerCase();
            break;
          case 'type':
            aVal = a.type;
            bVal = b.type;
            break;
          case 'rdclass':
            aVal = a.rdclass || 'IN';
            bVal = b.rdclass || 'IN';
            break;
          case 'ttl':
            aVal = a.ttl;
            bVal = b.ttl;
            break;
          case 'data':
            aVal = a.records.join(',').toLowerCase();
            bVal = b.records.join(',').toLowerCase();
            break;
          default:
            return 0;
        }

        if (aVal < bVal) return state.searchSortDirection === 'asc' ? -1 : 1;
        if (aVal > bVal) return state.searchSortDirection === 'asc' ? 1 : -1;
        return 0;
      });
    },

    /**
     * Check if any search results have non-IN class.
     */
    get searchHasNonINClass(): boolean {
      for (const result of state.searchResults) {
        for (const record of result.rrsets) {
          if (record.rdclass && record.rdclass !== 'IN') {
            return true;
          }
        }
      }
      return false;
    },
  };
}
