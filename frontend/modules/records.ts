import { API_BASE, api, formatDNSError } from '../api/client';
import type { AppState, PageSizeMode, RRset } from '../types';
import {
  COMMON_CLASSES,
  COMMON_TYPES,
  getFinalRecordClass,
  getFinalRecordType,
} from '../utils/dns';
import { calculatePageSize, getPageSizeForMode } from '../utils/pagination';
import { syncUrlFromState } from './router';

// Method context type - includes state and other methods
type RecordMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  performSearch: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
  closeRecordModal: () => void;
  editRecord: (record: RRset, zone?: string | null) => void;
  confirmDelete: (record: RRset, zone?: string | null) => void;
  goToFirstPage: () => void;
  loadRecordsWithOffset: (offset: number) => Promise<void>;
};

/**
 * Record management module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createRecordMethods(_state: AppState) {
  return {
    /**
     * Load records with pagination.
     */
    async loadRecords(
      this: RecordMethodContext,
      cursor: string | null = null,
      resetHistory = true,
    ) {
      if (!this.selectedZone) return;

      this.loadingRecords = true;
      this.currentCursor = cursor;

      if (this.pageSizeMode === 'auto') {
        this.pageSize = calculatePageSize();
      }

      try {
        let url = `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/rrsets`;
        const params = new URLSearchParams();

        if (this.pageSize) {
          params.set('limit', String(this.pageSize));
        }
        if (cursor) {
          params.set('after', cursor);
        }

        if (params.toString()) {
          url += `?${params.toString()}`;
        }

        const response = await api(url, this.apiKey);
        if (response.ok) {
          const data = await response.json();

          if (data.rrsets !== undefined) {
            this.records = data.rrsets as RRset[];
            this.nextCursor = data.next_cursor;
            this.totalRecords = data.total_count;
            this.hasMoreRecords = data.has_more;
          } else {
            this.records = data as RRset[];
            this.totalRecords = this.records.length;
            this.nextCursor = null;
            this.hasMoreRecords = false;
          }

          if (resetHistory) {
            this.recordCursorHistory = [];
          }
        }
      } catch (e) {
        this.toast(`Failed to load records: ${(e as Error).message}`, 'error');
      }

      this.loadingRecords = false;
    },

    /**
     * Navigate to first page.
     */
    async goToFirstPage(this: RecordMethodContext) {
      this.recordCursorHistory = [];
      await this.loadRecords(null, true);
      syncUrlFromState(this);
    },

    /**
     * Navigate to previous page.
     */
    async goToPrevPage(this: RecordMethodContext) {
      if (this.recordCursorHistory.length === 0) return;
      const prevCursor = this.recordCursorHistory.pop();
      if (prevCursor !== undefined) {
        await this.loadRecords(prevCursor, false);
        syncUrlFromState(this);
      }
    },

    /**
     * Navigate to next page.
     */
    async goToNextPage(this: RecordMethodContext) {
      if (this.hasMoreRecords && this.nextCursor) {
        this.recordCursorHistory.push(this.currentCursor);
        await this.loadRecords(this.nextCursor, false);
        syncUrlFromState(this);
      }
    },

    /**
     * Navigate to last page.
     */
    async goToLastPage(this: RecordMethodContext) {
      while (this.hasMoreRecords && this.nextCursor) {
        this.recordCursorHistory.push(this.currentCursor);
        await this.loadRecords(this.nextCursor, false);
      }
      syncUrlFromState(this);
    },

    /**
     * Go to a specific record page using offset-based pagination.
     */
    async goToRecordPage(this: RecordMethodContext, targetPage: number) {
      const pageSize = this.pageSize || 25;
      const totalPages = Math.ceil(this.totalRecords / pageSize);

      // Validate target page
      let page = targetPage;
      if (Number.isNaN(page) || page < 1) page = 1;
      if (page > totalPages) page = totalPages;

      // Calculate offset
      const offset = (page - 1) * pageSize;

      // Update cursor history to reflect the page position
      // Build synthetic history so prev/next still work
      this.recordCursorHistory = [];
      for (let i = 0; i < page - 1; i++) {
        this.recordCursorHistory.push(`__page_${i}__`);
      }

      // Load with offset
      await this.loadRecordsWithOffset(offset);

      // Update URL with current page
      syncUrlFromState(this);
    },

    /**
     * Load records with a specific offset.
     */
    async loadRecordsWithOffset(this: RecordMethodContext, offset: number) {
      if (!this.selectedZone) return;

      try {
        const params = new URLSearchParams();
        params.set('offset', offset.toString());
        if (this.pageSize) {
          params.set('limit', this.pageSize.toString());
        }

        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(this.selectedZone)}/rrsets?${params}`,
          this.apiKey,
        );
        if (response.ok) {
          const data = await response.json();
          if ('rrsets' in data && 'total_count' in data) {
            this.records = data.rrsets;
            this.totalRecords = data.total_count;
            this.hasMoreRecords = data.has_more || false;
            this.nextCursor = data.next_cursor || null;
            this.currentCursor = `__offset_${offset}__`;
          }
        }
      } catch (e) {
        this.toast(`Failed to load records: ${(e as Error).message}`, 'error');
      }
    },

    /**
     * Change record page size mode.
     */
    changeRecordPageSizeMode(this: RecordMethodContext, newMode: PageSizeMode) {
      this.pageSizeMode = newMode;
      this.pageSize = getPageSizeForMode(newMode, 'records');
      this.recordCursorHistory = [];
      this.loadRecords(null, true);
    },

    /**
     * Edit a record.
     */
    editRecord(
      this: RecordMethodContext,
      record: RRset,
      zone: string | null = null,
    ) {
      this.recordForm = {
        name: record.name,
        type: record.type,
        rdclass: record.rdclass || 'IN',
        ttl: record.ttl,
        zone: zone || this.selectedZone || undefined,
        records: record.records.join('\n'),
        original: record,
      };
      this.customTypeMode = !COMMON_TYPES.includes(record.type.toUpperCase());
      this.customClassMode = !COMMON_CLASSES.includes(
        (record.rdclass || 'IN').toUpperCase(),
      );
      this.showEditRecord = true;
    },

    /**
     * Close record modal.
     */
    closeRecordModal(this: RecordMethodContext) {
      this.showAddRecord = false;
      this.showEditRecord = false;
      this.customTypeMode = false;
      this.customClassMode = false;
      this.recordForm = {
        name: '',
        type: 'A',
        rdclass: 'IN',
        ttl: 3600,
        records: '',
      };
    },

    /**
     * Save record (add or update).
     */
    async saveRecord(this: RecordMethodContext) {
      const records = this.recordForm.records
        .split('\n')
        .map((r) => r.trim())
        .filter((r) => r);
      if (!records.length) {
        this.toast('Please enter at least one record', 'error');
        return;
      }

      if (!this.recordForm.type.trim()) {
        this.toast('Please specify a record type', 'error');
        return;
      }

      const targetZone = this.recordForm.zone || this.selectedZone;
      if (!targetZone) {
        this.toast('No zone selected', 'error');
        return;
      }

      const finalType = getFinalRecordType(this.recordForm.type);
      const finalClass = getFinalRecordClass(this.recordForm.rdclass);

      // If atomic mode is on, queue the operation
      if (this.atomicMode) {
        const action = this.showEditRecord ? 'replace' : 'add';
        this.atomicQueue.push({
          action,
          zone: targetZone,
          name: this.recordForm.name,
          type: finalType,
          rdclass: finalClass,
          ttl: this.recordForm.ttl,
          records,
        });
        this.toast(
          `${action === 'add' ? 'Add' : 'Replace'} queued (${this.atomicQueue.length} pending)`,
          'success',
        );
        this.closeRecordModal();
        return;
      }

      this.saving = true;

      try {
        const payload = {
          name: this.recordForm.name,
          type: finalType,
          rdclass: finalClass,
          ttl: this.recordForm.ttl,
          records,
        };

        const method = this.showEditRecord ? 'PUT' : 'POST';
        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(targetZone)}/rrsets`,
          this.apiKey,
          {
            method,
            body: JSON.stringify(payload),
          },
        );

        if (response.ok) {
          this.toast(
            this.showEditRecord ? 'Record updated' : 'Record created',
            'success',
          );
          this.closeRecordModal();
          if (this.isSearching) {
            await this.performSearch();
          }
          if (this.selectedZone) {
            await this.loadRecords();
          }
          // Reload zones to get updated serial
          await this.loadZones(null, false);
        } else {
          const err = await response.json();
          this.toast(`Failed: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Operation failed: ${(e as Error).message}`, 'error');
      }

      this.saving = false;
    },

    /**
     * Confirm delete of a record.
     */
    confirmDelete(
      this: RecordMethodContext,
      record: RRset,
      zone: string | null = null,
    ) {
      this.deleteTarget = { ...record, zone: zone || this.selectedZone || '' };
      this.showDeleteConfirm = true;
    },

    /**
     * Delete a record.
     */
    async deleteRecord(this: RecordMethodContext) {
      if (!this.deleteTarget) return;

      const targetZone = this.deleteTarget.zone || this.selectedZone;
      if (!targetZone) return;

      // If atomic mode is on, queue the operation
      if (this.atomicMode) {
        this.atomicQueue.push({
          action: 'delete',
          zone: targetZone,
          name: this.deleteTarget.name,
          type: this.deleteTarget.type,
          rdclass: this.deleteTarget.rdclass || 'IN',
          ttl: 0,
          records: null,
        });
        this.toast(
          `Delete queued (${this.atomicQueue.length} pending)`,
          'success',
        );
        this.showDeleteConfirm = false;
        this.deleteTarget = null;
        return;
      }

      this.saving = true;

      try {
        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(targetZone)}/rrsets`,
          this.apiKey,
          {
            method: 'DELETE',
            body: JSON.stringify({
              name: this.deleteTarget.name,
              type: this.deleteTarget.type,
              rdclass: this.deleteTarget.rdclass || 'IN',
            }),
          },
        );

        if (response.ok) {
          this.toast('Record deleted', 'success');
          this.showDeleteConfirm = false;
          this.deleteTarget = null;
          if (this.isSearching) {
            await this.performSearch();
          }
          if (this.selectedZone) {
            await this.loadRecords();
          }
          // Reload zones to get updated serial
          await this.loadZones(null, false);
        } else {
          const err = await response.json();
          this.toast(`Delete failed: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Delete failed: ${(e as Error).message}`, 'error');
      }

      this.saving = false;
    },

    /**
     * Edit search result.
     */
    editSearchResult(this: RecordMethodContext, zone: string, record: RRset) {
      this.editRecord(record, zone);
    },

    /**
     * Confirm delete search result.
     */
    confirmDeleteSearchResult(
      this: RecordMethodContext,
      zone: string,
      record: RRset,
    ) {
      this.confirmDelete(record, zone);
    },
  };
}

/**
 * Create computed properties for record pagination.
 * NOTE: Computed properties can still use state closure for reads.
 */
export function createRecordPaginationComputed(state: AppState) {
  return {
    get recordCurrentPage(): number {
      return state.recordCursorHistory.length + 1;
    },

    get recordTotalPages(): number {
      if (!state.pageSize || state.totalRecords === 0) return 1;
      return Math.ceil(state.totalRecords / state.pageSize);
    },

    get recordStartIndex(): number {
      if (state.totalRecords === 0) return 0;
      return (
        (this.recordCurrentPage - 1) *
          (state.pageSize || state.records.length) +
        1
      );
    },

    get recordEndIndex(): number {
      return this.recordStartIndex + state.records.length - 1;
    },
  };
}
