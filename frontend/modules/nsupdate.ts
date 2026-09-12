import { API_BASE } from '../api/client';
import type { AppState, NsupdateDraftsResult } from '../types';

// Method context type - includes state and other methods
type NsupdateMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  openScheduledView: () => void;
  loadScheduledChanges: () => Promise<void>;
};

/**
 * NSUPDATE module — paste nsupdate text to create draft scheduled changes.
 * NOTE: Methods use `this` (the Alpine proxy) for state changes to trigger reactivity.
 */
export function createNsupdateMethods(_state: AppState) {
  return {
    /**
     * Parse nsupdate text and save each send transaction as a draft scheduled change.
     */
    async saveNsupdateAsDrafts(this: NsupdateMethodContext) {
      if (!this.nsupdateText.trim()) return;

      this.saving = true;
      this.nsupdateResult = null;

      try {
        const response = await fetch(`${API_BASE}/nsupdate/drafts`, {
          method: 'POST',
          headers: {
            'Content-Type': 'text/plain',
            'X-API-Key': this.apiKey || '',
          },
          body: this.nsupdateText,
        });

        const data = await response.json();

        if (response.ok) {
          const result = data as NsupdateDraftsResult;
          this.nsupdateResult = result;
          const n = result.total;
          this.toast(
            n === 1
              ? 'Created 1 draft scheduled change'
              : `Created ${n} draft scheduled changes`,
            'success',
          );
          this.nsupdateText = '';
          this.showNsupdate = false;
          this.nsupdateResult = null;
          this.openScheduledView();
          await this.loadScheduledChanges();
        } else {
          const detail =
            typeof data.detail === 'string'
              ? data.detail
              : Array.isArray(data.detail)
                ? data.detail.map((d: { msg?: string }) => d.msg).join('; ')
                : 'Unknown error';
          this.toast(`NSUPDATE drafts failed: ${detail}`, 'error');
        }
      } catch (e) {
        this.toast(
          `NSUPDATE drafts failed: ${(e as Error).message}`,
          'error',
        );
      }

      this.saving = false;
    },
  };
}
