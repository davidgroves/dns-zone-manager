import { API_BASE, api, formatDNSError } from '../api/client';
import type { AppState } from '../types';

// Method context type - includes state and other methods
type AtomicMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
};

/**
 * Atomic operations module.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createAtomicMethods(_state: AppState) {
  return {
    /**
     * Handle atomic mode toggle.
     */
    onAtomicModeChange(this: AtomicMethodContext) {
      if (!this.atomicMode && this.atomicQueue.length > 0) {
        if (
          !confirm(
            `You have ${this.atomicQueue.length} pending changes. Discard them?`,
          )
        ) {
          this.atomicMode = true;
          return;
        }
        this.atomicQueue = [];
      }
      this.atomicResult = null;
    },

    /**
     * Remove item from atomic queue.
     */
    removeAtomicItem(this: AtomicMethodContext, index: number) {
      this.atomicQueue.splice(index, 1);
    },

    /**
     * Clear all pending atomic operations.
     */
    clearAtomicQueue(this: AtomicMethodContext) {
      if (
        this.atomicQueue.length > 0 &&
        confirm('Clear all pending changes?')
      ) {
        this.atomicQueue = [];
        this.atomicResult = null;
      }
    },

    /**
     * Submit atomic operations queue.
     */
    async submitAtomicQueue(this: AtomicMethodContext) {
      if (this.atomicQueue.length === 0) return;

      // Check that all operations are for the same zone
      const zones = [...new Set(this.atomicQueue.map((op) => op.zone))];
      if (zones.length > 1) {
        this.toast(
          'Atomic operations must be for a single zone. Please submit changes for each zone separately.',
          'error',
        );
        return;
      }

      const targetZone = zones[0];
      this.saving = true;
      this.atomicResult = null;

      try {
        const operations = this.atomicQueue.map((op) => ({
          action: op.action,
          name: op.name,
          type: op.type,
          ttl: op.ttl,
          records: op.records,
        }));

        const response = await api(
          `${API_BASE}/zones/${encodeURIComponent(targetZone)}/atomic`,
          this.apiKey,
          {
            method: 'POST',
            body: JSON.stringify({ operations }),
          },
        );

        const data = await response.json();

        if (response.ok && data.success) {
          this.atomicResult = data;
          this.toast(
            `${data.operations_count} operations completed successfully`,
            'success',
          );
          this.atomicQueue = [];
          if (this.selectedZone === targetZone) {
            await this.loadRecords();
          }
          await this.loadZones();
        } else {
          const errorMsg = formatDNSError(data);
          this.atomicResult = { success: false, message: errorMsg };
          this.toast(`Atomic update failed: ${errorMsg}`, 'error');
        }
      } catch (e) {
        this.atomicResult = { success: false, message: (e as Error).message };
        this.toast(`Atomic update failed: ${(e as Error).message}`, 'error');
      }

      this.saving = false;
    },
  };
}
