import { API_BASE, api, formatDNSError } from '../api/client';
import type {
  AppState,
  RollbackPreview,
  RollbackResult,
  ZoneHistory,
} from '../types';

// Method context type - includes methods from this module
type HistoryMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  // Methods from this module (for cross-method calls)
  loadZoneHistory: () => Promise<void>;
};

/**
 * Zone history and rollback module.
 */
export function createHistoryMethods(_state: AppState) {
  return {
    /**
     * Open the history modal for a zone.
     */
    async openHistoryModal(this: HistoryMethodContext) {
      if (!this.selectedZone) {
        this.toast('No zone selected', 'warning');
        return;
      }

      // Reset state
      this.zoneHistory = null;
      this.rollbackPreview = null;
      this.rollbackTargetSerial = null;
      this.expandedBatches = new Set();
      this.historyLoading = true;
      this.showHistoryModal = true;

      // Load history
      await this.loadZoneHistory();
    },

    /**
     * Close the history modal.
     */
    closeHistoryModal(this: HistoryMethodContext) {
      this.showHistoryModal = false;
      this.zoneHistory = null;
      this.rollbackPreview = null;
      this.rollbackTargetSerial = null;
      this.expandedBatches = new Set();
    },

    /**
     * Load zone history from the API.
     */
    async loadZoneHistory(this: HistoryMethodContext) {
      if (!this.selectedZone) return;

      this.historyLoading = true;

      try {
        const zone = this.selectedZone.endsWith('.')
          ? this.selectedZone
          : `${this.selectedZone}.`;
        const encodedZone = encodeURIComponent(zone);
        const fromSerial = this.historyFromSerial ?? 1;
        const response = await api(
          `${API_BASE}/zones/${encodedZone}/history?from_serial=${fromSerial}`,
          this.apiKey,
        );

        if (response.ok) {
          const data: ZoneHistory = await response.json();
          this.zoneHistory = data;

          if (data.is_full_axfr) {
            this.toast(
              'Incremental history not available - showing full zone state',
              'warning',
            );
          }
        } else {
          const err = await response.json();
          this.toast(
            `Failed to load zone history: ${formatDNSError(err)}`,
            'error',
          );
        }
      } catch (e) {
        this.toast(
          `Failed to load zone history: ${(e as Error).message}`,
          'error',
        );
      }

      this.historyLoading = false;
    },

    /**
     * Toggle expansion of a history batch.
     */
    toggleBatchExpansion(this: HistoryMethodContext, batchIndex: number) {
      const newExpanded = new Set(this.expandedBatches);
      if (newExpanded.has(batchIndex)) {
        newExpanded.delete(batchIndex);
      } else {
        newExpanded.add(batchIndex);
      }
      this.expandedBatches = newExpanded;
    },

    /**
     * Check if a batch is expanded.
     */
    isBatchExpanded(this: HistoryMethodContext, batchIndex: number): boolean {
      return this.expandedBatches.has(batchIndex);
    },

    /**
     * Select a serial for rollback and load the preview.
     */
    async selectRollbackSerial(
      this: HistoryMethodContext,
      targetSerial: number,
    ) {
      if (!this.selectedZone) return;

      this.rollbackTargetSerial = targetSerial;
      this.rollbackPreview = null;
      this.rollbackLoading = true;

      try {
        const zone = this.selectedZone.endsWith('.')
          ? this.selectedZone
          : `${this.selectedZone}.`;
        const encodedZone = encodeURIComponent(zone);
        const response = await api(
          `${API_BASE}/zones/${encodedZone}/history/rollback/preview?target_serial=${targetSerial}`,
          this.apiKey,
        );

        if (response.ok) {
          const data: RollbackPreview = await response.json();
          this.rollbackPreview = data;

          if (!data.can_rollback && data.warning) {
            this.toast(data.warning, 'warning');
          }
        } else {
          const err = await response.json();
          this.toast(
            `Failed to load rollback preview: ${formatDNSError(err)}`,
            'error',
          );
        }
      } catch (e) {
        this.toast(
          `Failed to load rollback preview: ${(e as Error).message}`,
          'error',
        );
      }

      this.rollbackLoading = false;
    },

    /**
     * Clear the rollback selection.
     */
    clearRollbackSelection(this: HistoryMethodContext) {
      this.rollbackTargetSerial = null;
      this.rollbackPreview = null;
    },

    /**
     * Execute the rollback.
     */
    async executeRollback(this: HistoryMethodContext) {
      if (!this.selectedZone || !this.rollbackTargetSerial) {
        this.toast('No rollback target selected', 'warning');
        return;
      }

      if (
        !this.rollbackPreview ||
        !this.rollbackPreview.can_rollback ||
        this.rollbackPreview.change_count === 0
      ) {
        this.toast('Rollback not available for this serial', 'warning');
        return;
      }

      // Confirm with user
      const confirmed = window.confirm(
        `Are you sure you want to rollback to serial ${this.rollbackTargetSerial}?\n\nThis will apply ${this.rollbackPreview.change_count} change(s) to the zone.\n\nThis action cannot be easily undone.`,
      );

      if (!confirmed) return;

      this.rollbackLoading = true;

      try {
        const zone = this.selectedZone.endsWith('.')
          ? this.selectedZone
          : `${this.selectedZone}.`;
        const encodedZone = encodeURIComponent(zone);
        const response = await api(
          `${API_BASE}/zones/${encodedZone}/history/rollback`,
          this.apiKey,
          {
            method: 'POST',
            body: JSON.stringify({
              target_serial: this.rollbackTargetSerial,
            }),
          },
        );

        if (response.ok) {
          const data: RollbackResult = await response.json();
          if (data.success) {
            this.toast(data.message, 'success');
            // Refresh records and history
            await this.loadRecords(null, true);
            await this.loadZoneHistory();
            // Clear rollback selection
            this.rollbackTargetSerial = null;
            this.rollbackPreview = null;
          } else {
            this.toast(data.message, 'error');
          }
        } else {
          const err = await response.json();
          this.toast(`Rollback failed: ${formatDNSError(err)}`, 'error');
        }
      } catch (e) {
        this.toast(`Rollback failed: ${(e as Error).message}`, 'error');
      }

      this.rollbackLoading = false;
    },

    /**
     * Get a human-readable summary of changes in a batch.
     */
    getBatchSummary(this: HistoryMethodContext, batchIndex: number): string {
      if (!this.zoneHistory || batchIndex >= this.zoneHistory.history.length) {
        return '';
      }

      const batch = this.zoneHistory.history[batchIndex];
      const adds = batch.changes.filter((c) => c.action === 'add').length;
      const deletes = batch.changes.filter((c) => c.action === 'delete').length;

      const parts: string[] = [];
      if (adds > 0) parts.push(`+${adds}`);
      if (deletes > 0) parts.push(`-${deletes}`);

      return parts.join(', ') || 'No changes';
    },

    /**
     * Format a serial number for display.
     */
    formatSerial(this: HistoryMethodContext, serial: number): string {
      return serial.toString();
    },
  };
}
