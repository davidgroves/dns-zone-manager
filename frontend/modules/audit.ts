import { API_BASE, api, formatDNSError } from '../api/client';
import type { AppState, AuditEvent, ChangeStatus } from '../types';
import { formatAuditDetail, formatLocalDisplay } from './scheduledHelpers';

const ALL_AUDIT_EVENTS = [
  'created',
  'updated',
  'cancelled',
  'claimed',
  'apply_now',
  'applied',
  'failed',
  'expired',
  'reverted',
] as const;

type AuditMethodContext = AppState & {
  toast: (message: string, type?: 'success' | 'error' | 'warning') => void;
  updateUrlFromState: () => void;
  openScheduledView: () => void;
  viewScheduledChange: (id: string) => Promise<void>;
  closeScheduledView: () => void;
  loadScheduledChanges: () => Promise<void>;
  loadAuditEvents: () => Promise<void>;
  closeAuditView: () => void;
};

/**
 * Cross-change audit log browser.
 */
export function createAuditMethods(_state: AppState) {
  return {
    formatAuditDetail,
    formatLocalDisplay,

    allAuditEventTypes(this: AuditMethodContext): string[] {
      return [...ALL_AUDIT_EVENTS];
    },

    openAuditView(this: AuditMethodContext) {
      this.showAuditView = true;
      this.showScheduledView = false;
      this.selectedZone = null;
      this.isSearching = false;
      this.showScheduledStatusMenu = false;
      this.showAuditEventMenu = false;
      this.updateUrlFromState();
      void this.loadAuditEvents();
    },

    closeAuditView(this: AuditMethodContext) {
      this.showAuditView = false;
      this.showAuditEventMenu = false;
      this.updateUrlFromState();
    },

    auditEventFilterLabel(this: AuditMethodContext): string {
      const filters = this.auditEventFilters || [];
      if (filters.length === 0 || filters.length === ALL_AUDIT_EVENTS.length) {
        return 'All events';
      }
      if (filters.length <= 3) {
        return filters.join(', ');
      }
      return `${filters.length} event types`;
    },

    auditResultSummary(this: AuditMethodContext): string {
      if (this.auditTotal === 0) {
        return 'No events';
      }
      const from = this.auditOffset + 1;
      const to = Math.min(this.auditOffset + this.auditEvents.length, this.auditTotal);
      const noun = this.auditTotal === 1 ? 'event' : 'events';
      if (from === 1 && to === this.auditTotal) {
        return `${this.auditTotal} ${noun}`;
      }
      return `Showing ${from}–${to} of ${this.auditTotal} ${noun}`;
    },

    isAuditEventSelected(this: AuditMethodContext, event: string): boolean {
      return (this.auditEventFilters || []).includes(event);
    },

    toggleAuditEventFilter(this: AuditMethodContext, event: string) {
      const current = [...(this.auditEventFilters || [])];
      const idx = current.indexOf(event);
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        current.push(event);
      }
      this.auditEventFilters = current;
      this.auditOffset = 0;
      void this.loadAuditEvents();
    },

    selectAllAuditEventFilters(this: AuditMethodContext) {
      this.auditEventFilters = [...ALL_AUDIT_EVENTS];
      this.auditOffset = 0;
      void this.loadAuditEvents();
    },

    clearAuditEventFilters(this: AuditMethodContext) {
      this.auditEventFilters = [];
      this.auditOffset = 0;
      void this.loadAuditEvents();
    },

    async loadAuditEvents(this: AuditMethodContext) {
      this.auditLoading = true;
      try {
        const params = new URLSearchParams();
        const filters = this.auditEventFilters || [];
        if (filters.length > 0 && filters.length < ALL_AUDIT_EVENTS.length) {
          for (const event of filters) {
            params.append('event', event);
          }
        }
        if (this.auditActorFilter.trim()) {
          params.set('actor', this.auditActorFilter.trim());
        }
        if (this.auditZoneFilter.trim()) {
          params.set('zone', this.auditZoneFilter.trim());
        }
        if (this.auditQuery.trim()) {
          params.set('q', this.auditQuery.trim());
        }
        if (this.auditSince.trim()) {
          params.set('since', new Date(this.auditSince).toISOString());
        }
        if (this.auditUntil.trim()) {
          params.set('until', new Date(this.auditUntil).toISOString());
        }
        params.set('limit', String(this.auditLimit));
        params.set('offset', String(this.auditOffset));

        const qs = params.toString();
        const response = await api(
          `${API_BASE}/scheduled-changes/events?${qs}`,
          this.apiKey,
        );
        if (!response.ok) {
          const data = await response.json();
          this.toast(`Failed to load audit log: ${formatDNSError(data)}`, 'error');
          return;
        }
        const data = await response.json();
        this.auditEvents = (data.events || []) as AuditEvent[];
        this.auditTotal = data.total || 0;
      } catch (e) {
        this.toast(`Failed to load audit log: ${(e as Error).message}`, 'error');
      } finally {
        this.auditLoading = false;
      }
    },

    async auditNextPage(this: AuditMethodContext) {
      if (this.auditOffset + this.auditLimit >= this.auditTotal) return;
      this.auditOffset += this.auditLimit;
      await this.loadAuditEvents();
    },

    async auditPrevPage(this: AuditMethodContext) {
      if (this.auditOffset <= 0) return;
      this.auditOffset = Math.max(0, this.auditOffset - this.auditLimit);
      await this.loadAuditEvents();
    },

    async openChangeFromAudit(this: AuditMethodContext, changeId: string) {
      this.closeAuditView();
      this.openScheduledView();
      // Include terminal statuses so the linked change is visible after open.
      this.scheduledStatusFilters = [
        'draft',
        'scheduled',
        'failed',
        'applied',
        'reverted',
        'cancelled',
        'expired',
        'running',
      ] as ChangeStatus[];
      await this.loadScheduledChanges();
      await this.viewScheduledChange(changeId);
    },
  };
}
