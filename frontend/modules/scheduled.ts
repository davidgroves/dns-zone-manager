import { API_BASE, api, formatDNSError } from '../api/client';
import type {
  AppState,
  ChangeStatus,
  PreviewResult,
  RevertPreview,
  ScheduledChange,
} from '../types';
import {
  blankScheduleOp,
  buildCreatePayload,
  buildUpdatePayload,
  canRevertChange,
  formFromChange,
  formatScheduledDisplay,
  utcIsoToLocalDatetime,
  validateScheduleOps,
} from './scheduledHelpers';

/** Statuses that can still be edited, cancelled, previewed or applied. */
const EDITABLE_STATUSES: ChangeStatus[] = ['draft', 'scheduled', 'failed'];

const ALL_CHANGE_STATUSES: ChangeStatus[] = [
  'draft',
  'scheduled',
  'running',
  'applied',
  'reverted',
  'failed',
  'cancelled',
  'expired',
];

const STATUS_LABELS: Record<ChangeStatus, string> = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  running: 'Running',
  applied: 'Applied',
  reverted: 'Reverted',
  failed: 'Failed',
  cancelled: 'Cancelled',
  expired: 'Expired',
};

type ScheduledMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  loadZones: (cursor?: string | null, resetHistory?: boolean) => Promise<void>;
  updateUrlFromState: () => void;
  loadScheduledChanges: () => Promise<void>;
  viewScheduledChange: (id: string) => Promise<void>;
  runScheduledPreview: (
    id: string,
    options?: { toast?: boolean },
  ) => Promise<void>;
  isChangeEditable: (status: ChangeStatus) => boolean;
  closeScheduleModal: () => void;
  closeRevertModal: () => void;
  $nextTick?: (callback?: () => void) => Promise<void>;
};

/**
 * Scheduled changes module.
 */
export function createScheduledMethods(_state: AppState) {
  return {
    formatScheduledDisplay,
    canRevertChange,

    /**
     * Open the schedule modal pre-filled from the atomic queue.
     */
    openScheduleFromQueue(this: ScheduledMethodContext) {
      if (this.atomicQueue.length === 0) {
        this.toast('No pending changes to save', 'warning');
        return;
      }
      const zones = [...new Set(this.atomicQueue.map((op) => op.zone))];
      if (zones.length > 1) {
        this.toast(
          'Scheduled changes must be for a single zone. Submit each zone separately.',
          'error',
        );
        return;
      }
      this.scheduleMode = 'create';
      this.editingChangeId = null;
      this.scheduleZone = zones[0];
      this.scheduleOps = this.atomicQueue.map((op) =>
        blankScheduleOp({
          action: op.action,
          name: op.name,
          type: op.type,
          rdclass: op.rdclass || 'IN',
          ttl: op.ttl,
          records: op.records,
        }),
      );
      this.scheduleForm = {
        name: `Change for ${zones[0].replace(/\.$/, '')}`,
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      };
      this.prereqRows = [];
      this.previewResult = null;
      this.showAtomicModal = false;
      this.showScheduleModal = true;
    },

    /**
     * Open the schedule modal to edit an existing change.
     */
    async editScheduledChange(this: ScheduledMethodContext, id: string) {
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}`,
          this.apiKey,
        );
        if (!response.ok) {
          const data = await response.json();
          this.toast(formatDNSError(data), 'error');
          return;
        }
        const change = (await response.json()) as ScheduledChange;

        if (!EDITABLE_STATUSES.includes(change.status)) {
          this.toast(`Cannot edit a change that is ${change.status}`, 'error');
          return;
        }

        this.scheduleMode = 'edit';
        this.editingChangeId = change.id;
        this.scheduleZone = change.zone;
        this.scheduleOps = change.operations.map((op) =>
          blankScheduleOp({
            action: op.action,
            name: op.name,
            type: op.type,
            rdclass: op.rdclass || 'IN',
            ttl: op.ttl,
            records: op.records,
          }),
        );
        this.scheduleForm = formFromChange(change);
        this.prereqRows = change.prerequisites.map((p) => ({
          prereq_type: p.prereq_type,
          name: p.name,
          rdtype: p.rdtype || 'A',
          rdclass: p.rdclass || 'IN',
          data: p.data || '',
        }));
        this.previewResult = null;
        this.showScheduleModal = true;
      } catch (e) {
        this.toast((e as Error).message, 'error');
      }
    },

    /**
     * Append a blank operation row to the change being edited.
     */
    addScheduleOp(this: ScheduledMethodContext) {
      this.scheduleOps.push(blankScheduleOp());
    },

    /**
     * Remove an operation from the change being edited.
     */
    removeScheduleOp(this: ScheduledMethodContext, index: number) {
      if (this.scheduleOps.length <= 1) {
        this.toast('A change must keep at least one operation', 'warning');
        return;
      }
      this.scheduleOps.splice(index, 1);
    },

    addPrereqRow(this: ScheduledMethodContext) {
      this.prereqRows.push({
        prereq_type: 'nxrrset',
        name: '',
        rdtype: 'A',
        rdclass: 'IN',
        data: '',
      });
    },

    removePrereqRow(this: ScheduledMethodContext, index: number) {
      this.prereqRows.splice(index, 1);
    },

    closeScheduleModal(this: ScheduledMethodContext) {
      this.showScheduleModal = false;
      this.previewResult = null;
      this.scheduleMode = 'create';
      this.editingChangeId = null;
      this.scheduleOps = [];
    },

    /**
     * Create a new change, or save edits to an existing one.
     */
    async saveScheduledChange(this: ScheduledMethodContext) {
      if (!this.scheduleForm.name.trim()) {
        this.toast('Name is required', 'error');
        return;
      }
      const opsError = validateScheduleOps(this.scheduleOps);
      if (opsError) {
        this.toast(opsError, 'error');
        return;
      }
      if (!this.scheduleForm.applyNow && !this.scheduleForm.scheduledLocal) {
        this.toast(
          'Pick a schedule time, or tick "Save as draft" to apply manually later',
          'error',
        );
        return;
      }

      const isEdit = this.scheduleMode === 'edit' && this.editingChangeId;

      this.saving = true;
      try {
        const url = isEdit
          ? `${API_BASE}/scheduled-changes/${encodeURIComponent(this.editingChangeId as string)}`
          : `${API_BASE}/scheduled-changes`;
        const payload = isEdit
          ? buildUpdatePayload(this.scheduleForm, this.scheduleOps, this.prereqRows)
          : buildCreatePayload(
              this.scheduleForm,
              this.scheduleZone,
              this.scheduleOps,
              this.prereqRows,
            );

        const response = await api(url, this.apiKey, {
          method: isEdit ? 'PATCH' : 'POST',
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
          this.toast(`Failed to save: ${formatDNSError(data)}`, 'error');
          return;
        }

        if (isEdit) {
          this.toast(`Updated "${data.name}"`, 'success');
        } else {
          this.toast(
            data.status === 'scheduled'
              ? `Scheduled "${data.name}" for ${formatScheduledDisplay(data.scheduled_at)}`
              : `Saved draft "${data.name}"`,
            'success',
          );
          this.atomicQueue = [];
          this.atomicMode = false;
        }

        this.closeScheduleModal();
        await this.loadScheduledChanges();
        if (this.selectedScheduledChange?.id === data.id) {
          await this.viewScheduledChange(data.id);
        }
      } catch (e) {
        this.toast(`Failed to save: ${(e as Error).message}`, 'error');
      } finally {
        this.saving = false;
      }
    },

    async loadScheduledChanges(this: ScheduledMethodContext) {
      this.scheduledLoading = true;
      try {
        const params = new URLSearchParams();
        const filters = this.scheduledStatusFilters || [];
        // Empty selection means all statuses (no filter param).
        if (filters.length > 0 && filters.length < ALL_CHANGE_STATUSES.length) {
          for (const status of filters) {
            params.append('status', status);
          }
        }
        const qs = params.toString();
        const url = `${API_BASE}/scheduled-changes${qs ? `?${qs}` : ''}`;
        const response = await api(url, this.apiKey);
        if (!response.ok) {
          const data = await response.json();
          this.toast(`Failed to load changes: ${formatDNSError(data)}`, 'error');
          return;
        }
        const data = await response.json();
        this.scheduledChanges = data.changes || [];
      } catch (e) {
        this.toast(`Failed to load changes: ${(e as Error).message}`, 'error');
      } finally {
        this.scheduledLoading = false;
      }
    },

    scheduledStatusFilterLabel(this: ScheduledMethodContext): string {
      const filters = this.scheduledStatusFilters || [];
      if (filters.length === 0 || filters.length === ALL_CHANGE_STATUSES.length) {
        return 'All statuses';
      }
      if (filters.length <= 3) {
        return filters.map((s) => STATUS_LABELS[s] || s).join(', ');
      }
      return `${filters.length} statuses`;
    },

    isScheduledStatusSelected(this: ScheduledMethodContext, status: ChangeStatus): boolean {
      return (this.scheduledStatusFilters || []).includes(status);
    },

    toggleScheduledStatus(this: ScheduledMethodContext, status: ChangeStatus) {
      const current = [...(this.scheduledStatusFilters || [])];
      const idx = current.indexOf(status);
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        current.push(status);
      }
      this.scheduledStatusFilters = current;
      void this.loadScheduledChanges();
    },

    selectAllScheduledStatuses(this: ScheduledMethodContext) {
      this.scheduledStatusFilters = [...ALL_CHANGE_STATUSES];
      void this.loadScheduledChanges();
    },

    resetScheduledStatusFilters(this: ScheduledMethodContext) {
      this.scheduledStatusFilters = ['draft', 'scheduled', 'failed'];
      void this.loadScheduledChanges();
    },

    allChangeStatuses(this: ScheduledMethodContext): ChangeStatus[] {
      return ALL_CHANGE_STATUSES;
    },

    statusLabel(this: ScheduledMethodContext, status: ChangeStatus): string {
      return STATUS_LABELS[status] || status;
    },

    openScheduledView(this: ScheduledMethodContext) {
      this.showScheduledView = true;
      this.showAuditView = false;
      this.selectedZone = null;
      this.isSearching = false;
      this.showAuditEventMenu = false;
      this.updateUrlFromState();
      void this.loadScheduledChanges();
    },

    closeScheduledView(this: ScheduledMethodContext) {
      this.showScheduledView = false;
      this.selectedScheduledChange = null;
      this.previewResult = null;
      this.previewLoading = false;
      this.updateUrlFromState();
    },

    async viewScheduledChange(this: ScheduledMethodContext, id: string) {
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}`,
          this.apiKey,
        );
        if (!response.ok) {
          const data = await response.json();
          this.toast(formatDNSError(data), 'error');
          return;
        }
        const change = (await response.json()) as ScheduledChange;
        this.selectedScheduledChange = change;
        this.previewResult = null;
        // Detail panel sits above the table; scroll it into view after Alpine renders.
        const scroll = () =>
          document
            .getElementById('scheduled-change-detail')
            ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (typeof this.$nextTick === 'function') {
          await this.$nextTick();
          scroll();
        } else {
          requestAnimationFrame(() => requestAnimationFrame(scroll));
        }

        if (this.isChangeEditable(change.status)) {
          await this.runScheduledPreview(id, { toast: false });
        }
      } catch (e) {
        this.toast((e as Error).message, 'error');
      }
    },

    async runScheduledPreview(
      this: ScheduledMethodContext,
      id: string,
      options: { toast?: boolean } = {},
    ) {
      const showToast = options.toast !== false;
      this.previewLoading = true;
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}/preview`,
          this.apiKey,
          { method: 'POST' },
        );
        const data = await response.json();
        if (!response.ok) {
          this.toast(formatDNSError(data), 'error');
          return;
        }
        this.previewResult = data as PreviewResult;
        if (showToast) {
          this.toast(
            data.message,
            data.all_prerequisites_passed ? 'success' : 'warning',
          );
        }
      } catch (e) {
        this.toast((e as Error).message, 'error');
      } finally {
        this.previewLoading = false;
      }
    },

    async previewScheduledChange(this: ScheduledMethodContext, id: string) {
      await this.runScheduledPreview(id, { toast: true });
    },

    async applyScheduledChangeNow(this: ScheduledMethodContext, id: string) {
      if (!confirm('Apply this change to DNS now?')) return;
      this.saving = true;
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}/apply`,
          this.apiKey,
          { method: 'POST' },
        );
        const data = await response.json();
        if (!response.ok || !data.success) {
          this.toast(data.message || formatDNSError(data), 'error');
        } else {
          this.toast(data.message || 'Change applied', 'success');
          if (this.selectedZone) {
            await this.loadRecords();
          }
          await this.loadZones();
        }
        await this.loadScheduledChanges();
        if (this.selectedScheduledChange?.id === id) {
          await this.viewScheduledChange(id);
        }
      } catch (e) {
        this.toast((e as Error).message, 'error');
      } finally {
        this.saving = false;
      }
    },

    async openRevertModal(this: ScheduledMethodContext, id: string) {
      this.saving = true;
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}/revert-preview`,
          this.apiKey,
        );
        const data = await response.json();
        if (!response.ok) {
          this.toast(formatDNSError(data), 'error');
          return;
        }
        this.revertPreview = data as RevertPreview;
        this.showRevertModal = true;
      } catch (e) {
        this.toast((e as Error).message, 'error');
      } finally {
        this.saving = false;
      }
    },

    closeRevertModal(this: ScheduledMethodContext) {
      this.showRevertModal = false;
      this.revertPreview = null;
    },

    async confirmRevertChange(this: ScheduledMethodContext) {
      if (!this.revertPreview) return;
      const id = this.revertPreview.change_id;
      this.saving = true;
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}/revert`,
          this.apiKey,
          { method: 'POST' },
        );
        const data = await response.json();
        if (!response.ok || !data.success) {
          this.toast(data.message || formatDNSError(data), 'error');
          return;
        }
        this.toast(data.message || 'Change reverted', 'success');
        this.closeRevertModal();
        if (this.selectedZone) {
          await this.loadRecords();
        }
        await this.loadZones();
        await this.loadScheduledChanges();
        if (this.selectedScheduledChange?.id === id) {
          await this.viewScheduledChange(id);
        }
      } catch (e) {
        this.toast((e as Error).message, 'error');
      } finally {
        this.saving = false;
      }
    },

    async cancelScheduledChange(this: ScheduledMethodContext, id: string) {
      if (!confirm('Cancel this scheduled change?')) return;
      try {
        const response = await api(
          `${API_BASE}/scheduled-changes/${encodeURIComponent(id)}`,
          this.apiKey,
          { method: 'DELETE' },
        );
        if (!response.ok) {
          const data = await response.json();
          this.toast(formatDNSError(data), 'error');
          return;
        }
        this.toast('Change cancelled', 'success');
        if (this.selectedScheduledChange?.id === id) {
          this.selectedScheduledChange = null;
        }
        await this.loadScheduledChanges();
      } catch (e) {
        this.toast((e as Error).message, 'error');
      }
    },

    statusBadgeClass(this: ScheduledMethodContext, status: ChangeStatus): string {
      const map: Record<string, string> = {
        draft: 'muted',
        scheduled: 'info',
        running: 'warning',
        applied: 'success',
        failed: 'error',
        cancelled: 'muted',
        expired: 'warning',
        reverted: 'info',
      };
      return map[status] || 'muted';
    },

    editScheduledLocalFromSelected(this: ScheduledMethodContext): string {
      return utcIsoToLocalDatetime(this.selectedScheduledChange?.scheduled_at);
    },

    /** Whether a change can still be edited, previewed, applied or cancelled. */
    isChangeEditable(this: ScheduledMethodContext, status: ChangeStatus): boolean {
      return EDITABLE_STATUSES.includes(status);
    },
  };
}
