import { API_BASE, api, formatDNSError } from '../api/client';
import type {
  AppState,
  AtomicOperation,
  RRset,
  ReversePtrCheckResult,
} from '../types';

// Method context type
type ReverseMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  atomicMode: boolean;
  atomicQueue: AtomicOperation[];
};

/**
 * Reverse PTR management module.
 */
export function createReverseMethods(_state: AppState) {
  return {
    /**
     * Open the reverse PTR modal for a record.
     */
    async openReversePtrModal(this: ReverseMethodContext, record: RRset) {
      // Only works for A and AAAA records
      if (record.type !== 'A' && record.type !== 'AAAA') {
        this.toast(
          'Reverse PTR only available for A and AAAA records',
          'warning',
        );
        return;
      }

      // Reset state
      this.reversePtrTarget = record.name;
      this.reversePtrTtl = record.ttl;
      this.reversePtrResults = [];
      this.reversePtrSelected = [];
      this.reversePtrMode = 'replace';
      this.reversePtrLoading = true;
      this.reversePtrCreateResult = null;
      this.showReversePtrModal = true;

      // Check reverse PTR feasibility
      try {
        const response = await api(
          `${API_BASE}/reverse-ptr/check`,
          this.apiKey,
          {
            method: 'POST',
            body: JSON.stringify({
              source_name: record.name,
              source_type: record.type,
              records: record.records,
            }),
          },
        );

        if (response.ok) {
          const data = await response.json();
          this.reversePtrResults = data.results;

          // Pre-select IPs that can be created
          this.reversePtrSelected = data.results
            .filter((r: ReversePtrCheckResult) => r.can_create)
            .map((r: ReversePtrCheckResult) => r.ip);
        } else {
          const err = await response.json();
          this.toast(
            `Failed to check reverse PTR: ${formatDNSError(err)}`,
            'error',
          );
          this.showReversePtrModal = false;
        }
      } catch (e) {
        this.toast(
          `Failed to check reverse PTR: ${(e as Error).message}`,
          'error',
        );
        this.showReversePtrModal = false;
      }

      this.reversePtrLoading = false;
    },

    /**
     * Toggle selection of an IP for PTR creation.
     */
    toggleReversePtrSelection(this: ReverseMethodContext, ip: string) {
      const index = this.reversePtrSelected.indexOf(ip);
      if (index === -1) {
        this.reversePtrSelected.push(ip);
      } else {
        this.reversePtrSelected.splice(index, 1);
      }
    },

    /**
     * Create the selected reverse PTR records.
     */
    async createReversePtrs(this: ReverseMethodContext) {
      if (this.reversePtrSelected.length === 0) {
        this.toast('No IPs selected', 'warning');
        return;
      }

      // Ensure target has trailing dot
      const ptrTarget = this.reversePtrTarget.endsWith('.')
        ? this.reversePtrTarget
        : `${this.reversePtrTarget}.`;

      // If atomic mode is on, queue the operations instead of executing immediately
      if (this.atomicMode) {
        let queuedCount = 0;

        for (const ip of this.reversePtrSelected) {
          const result = this.reversePtrResults.find((r) => r.ip === ip);
          if (
            !result ||
            !result.zone_managed ||
            !result.reverse_zone ||
            !result.record_name
          ) {
            continue;
          }

          // Determine action and records based on existing PTRs and mode
          let action: 'add' | 'replace';
          let records: string[];

          if (result.existing_ptrs.length === 0) {
            // No existing PTRs - add new
            action = 'add';
            records = [ptrTarget];
          } else if (this.reversePtrMode === 'replace') {
            // Replace existing
            action = 'replace';
            records = [ptrTarget];
          } else {
            // Add to existing (round-robin)
            action = 'replace';
            records = [...result.existing_ptrs, ptrTarget];
          }

          this.atomicQueue.push({
            action,
            zone: result.reverse_zone,
            name: result.record_name,
            type: 'PTR',
            rdclass: 'IN',
            ttl: this.reversePtrTtl,
            records,
          });
          queuedCount++;
        }

        if (queuedCount > 0) {
          this.toast(
            `${queuedCount} PTR operation(s) queued (${this.atomicQueue.length} pending)`,
            'success',
          );
        }
        this.showReversePtrModal = false;
        return;
      }

      // Non-atomic mode: execute immediately via API
      this.saving = true;
      this.reversePtrCreateResult = null;

      try {
        const response = await api(`${API_BASE}/reverse-ptr`, this.apiKey, {
          method: 'POST',
          body: JSON.stringify({
            ptr_target: this.reversePtrTarget,
            ttl: this.reversePtrTtl,
            ips: this.reversePtrSelected,
            mode: this.reversePtrMode,
          }),
        });

        if (response.ok) {
          const data = await response.json();
          this.reversePtrCreateResult = data;

          if (data.created_count > 0) {
            this.toast(
              `Created ${data.created_count} PTR record(s)`,
              'success',
            );
          }
          if (data.skipped_count > 0) {
            this.toast(
              `Skipped ${data.skipped_count} existing PTR(s)`,
              'warning',
            );
          }
          if (data.error_count > 0) {
            this.toast(`${data.error_count} PTR creation(s) failed`, 'error');
          }

          // Refresh the check to show updated state
          if (data.created_count > 0) {
            // Re-check to update the display
            const checkResponse = await api(
              `${API_BASE}/reverse-ptr/check`,
              this.apiKey,
              {
                method: 'POST',
                body: JSON.stringify({
                  source_name: this.reversePtrTarget,
                  source_type: 'A', // Doesn't matter for check
                  records: this.reversePtrResults.map((r) => r.ip),
                }),
              },
            );
            if (checkResponse.ok) {
              const checkData = await checkResponse.json();
              this.reversePtrResults = checkData.results;
              this.reversePtrSelected = [];
            }
          }
        } else {
          const err = await response.json();
          this.toast(
            `Failed to create PTR records: ${formatDNSError(err)}`,
            'error',
          );
        }
      } catch (e) {
        this.toast(
          `Failed to create PTR records: ${(e as Error).message}`,
          'error',
        );
      }

      this.saving = false;
    },
  };
}
