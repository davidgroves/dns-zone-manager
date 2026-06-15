import { API_BASE } from '../api/client';
import type { AppState } from '../types';

// Method context type - includes state and other methods
type NsupdateMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
};

/**
 * NSUPDATE module for raw DNS update commands.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createNsupdateMethods(_state: AppState) {
  return {
    /**
     * Execute NSUPDATE commands.
     */
    async executeNsupdate(this: NsupdateMethodContext) {
      if (!this.nsupdateText.trim()) return;

      this.saving = true;
      this.nsupdateResult = null;

      try {
        const response = await fetch(`${API_BASE}/nsupdate`, {
          method: 'POST',
          headers: {
            'Content-Type': 'text/plain',
            'X-API-Key': this.apiKey || '',
          },
          body: this.nsupdateText,
        });

        const data = await response.json();

        if (response.ok) {
          this.nsupdateResult = data;
          if (data.total_failed === 0) {
            this.toast(
              `NSUPDATE: ${data.total_success} transaction(s) succeeded`,
              'success',
            );
          } else {
            this.toast(
              `NSUPDATE: ${data.total_success} succeeded, ${data.total_failed} failed`,
              'warning',
            );
          }
          await this.loadZones();
          if (this.selectedZone) {
            await this.loadRecords();
          }
        } else {
          this.toast(
            `NSUPDATE failed: ${data.detail || 'Unknown error'}`,
            'error',
          );
        }
      } catch (e) {
        this.toast(`NSUPDATE failed: ${(e as Error).message}`, 'error');
      }

      this.saving = false;
    },
  };
}
