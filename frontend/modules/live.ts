/**
 * Live zone-change WebSocket subscription and in-place record patching.
 */

import type { AppState, RRset } from '../types';

export interface LiveOperation {
  action: string;
  name: string;
  type: string;
  rdclass?: string;
  ttl?: number | null;
  records?: string[];
}

export interface LiveMessage {
  type: string;
  zone?: string;
  operations?: LiveOperation[];
  event?: string;
  serial?: number | null;
}

export type SortField = 'name' | 'type' | 'ttl' | 'rdclass' | 'data' | '';
export type SortDirection = 'asc' | 'desc';

const FLASH_MS = 1200;

export function normalizeName(name: string): string {
  const n = name.trim().toLowerCase();
  return n.endsWith('.') ? n : `${n}.`;
}

export function recordKey(name: string, type: string, rdclass?: string): string {
  return `${normalizeName(name)}|${type.toUpperCase()}|${(rdclass || 'IN').toUpperCase()}`;
}

export function compareRecords(
  a: RRset,
  b: RRset,
  sortField: SortField,
  sortDirection: SortDirection,
): number {
  let cmp = 0;
  if (sortField === 'name' || !sortField) {
    cmp = normalizeName(a.name).localeCompare(normalizeName(b.name));
  } else if (sortField === 'type') {
    cmp = a.type.localeCompare(b.type);
  } else if (sortField === 'ttl') {
    cmp = a.ttl - b.ttl;
  } else if (sortField === 'rdclass') {
    cmp = (a.rdclass || 'IN').localeCompare(b.rdclass || 'IN');
  } else {
    cmp = normalizeName(a.name).localeCompare(normalizeName(b.name));
  }
  return sortDirection === 'desc' ? -cmp : cmp;
}

function operationToRRset(op: LiveOperation): RRset {
  return {
    name: normalizeName(op.name),
    type: op.type.toUpperCase(),
    rdclass: (op.rdclass || 'IN').toUpperCase(),
    ttl: op.ttl ?? 3600,
    records: [...(op.records || [])],
  };
}

export interface ApplyLiveContext {
  records: RRset[];
  pageSize: number | null;
  totalRecords: number;
  hasMoreRecords: boolean;
  recordCursorHistory: string[];
  sortField: SortField;
  sortDirection: SortDirection;
}

export interface ApplyLiveResult {
  records: RRset[];
  totalRecords: number;
  flashedKeys: string[];
}

/**
 * Apply live operations to the current page of records.
 */
export function applyLiveOperations(
  ctx: ApplyLiveContext,
  operations: LiveOperation[],
): ApplyLiveResult {
  let records = [...ctx.records];
  let totalRecords = ctx.totalRecords;
  const flashedKeys: string[] = [];
  const pageSize = ctx.pageSize && ctx.pageSize > 0 ? ctx.pageSize : records.length || 25;
  const isFirstPage = (ctx.recordCursorHistory?.length ?? 0) === 0;
  const isLastPage = !ctx.hasMoreRecords;

  for (const op of operations) {
    const key = recordKey(op.name, op.type, op.rdclass);
    const idx = records.findIndex(
      (r) => recordKey(r.name, r.type, r.rdclass) === key,
    );

    if (op.action === 'delete') {
      if (idx >= 0) {
        records.splice(idx, 1);
        totalRecords = Math.max(0, totalRecords - 1);
      }
      continue;
    }

    if (op.action === 'replace' || op.action === 'add') {
      const next = operationToRRset(op);
      if (idx >= 0) {
        records[idx] = next;
        flashedKeys.push(key);
        continue;
      }

      // Brand-new RRset: insert only if it belongs on this page.
      if (op.action === 'add') {
        totalRecords += 1;
      }

      if (!wouldFitOnPage(records, next, {
        pageSize,
        isFirstPage,
        isLastPage,
        sortField: ctx.sortField,
        sortDirection: ctx.sortDirection,
      })) {
        continue;
      }

      records.push(next);
      records.sort((a, b) =>
        compareRecords(a, b, ctx.sortField, ctx.sortDirection),
      );
      if (records.length > pageSize) {
        // Drop the end that fell off the far side of the window.
        records = records.slice(0, pageSize);
      }
      // Only flash if still present after eviction.
      if (records.some((r) => recordKey(r.name, r.type, r.rdclass) === key)) {
        flashedKeys.push(key);
      }
    }
  }

  return { records, totalRecords, flashedKeys };
}

function wouldFitOnPage(
  records: RRset[],
  candidate: RRset,
  opts: {
    pageSize: number;
    isFirstPage: boolean;
    isLastPage: boolean;
    sortField: SortField;
    sortDirection: SortDirection;
  },
): boolean {
  if (records.length === 0) {
    return true;
  }

  const sorted = [...records].sort((a, b) =>
    compareRecords(a, b, opts.sortField, opts.sortDirection),
  );
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const vsFirst = compareRecords(candidate, first, opts.sortField, opts.sortDirection);
  const vsLast = compareRecords(candidate, last, opts.sortField, opts.sortDirection);

  const inClosedRange = vsFirst >= 0 && vsLast <= 0;
  if (inClosedRange) {
    return true;
  }

  const notFull = records.length < opts.pageSize;
  if (opts.isFirstPage && notFull && vsFirst < 0) {
    return true;
  }
  if (opts.isLastPage && notFull && vsLast > 0) {
    return true;
  }
  return false;
}

type LiveMethodContext = AppState & {
  loadRecords: (
    cursor?: string | null,
    resetHistory?: boolean,
  ) => Promise<void>;
  applyLiveZoneMessage: (message: LiveMessage) => void;
  flashRecordKeys: (keys: string[]) => void;
  connectZoneLive: (zone: string) => void;
  disconnectZoneLive: () => void;
};

let activeSocket: WebSocket | null = null;
let activeZone: string | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempt = 0;
let intentionalClose = false;

function buildWsUrl(zone: string, apiKey: string | null): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const path = `/v1/zones/${encodeURIComponent(zone)}/ws`;
  const url = new URL(`${proto}//${window.location.host}${path}`);
  if (apiKey) {
    url.searchParams.set('api_key', apiKey);
  }
  return url.toString();
}

export function createLiveMethods(_state: AppState) {
  return {
    flashRecordKeys(this: LiveMethodContext, keys: string[]) {
      if (!keys.length) return;
      const next = new Set(this.liveFlashKeys || []);
      for (const key of keys) {
        next.add(key);
      }
      this.liveFlashKeys = next;
      window.setTimeout(() => {
        const cleared = new Set(this.liveFlashKeys || []);
        for (const key of keys) {
          cleared.delete(key);
        }
        this.liveFlashKeys = cleared;
      }, FLASH_MS);
    },

    isLiveFlashing(this: LiveMethodContext, record: RRset): boolean {
      const key = recordKey(record.name, record.type, record.rdclass);
      return Boolean(this.liveFlashKeys?.has(key));
    },

    applyLiveZoneMessage(this: LiveMethodContext, message: LiveMessage) {
      if (this.isSearching || this.showScheduledView || this.showAuditView) {
        return;
      }
      if (!this.selectedZone) return;

      if (message.type === 'zone_reload') {
        void this.loadRecords(this.currentCursor, false);
        return;
      }

      if (message.type !== 'zone_change' || !message.operations?.length) {
        return;
      }

      const result = applyLiveOperations(
        {
          records: this.records || [],
          pageSize: this.pageSize,
          totalRecords: this.totalRecords,
          hasMoreRecords: this.hasMoreRecords,
          recordCursorHistory: (this.recordCursorHistory || []).filter(
            (c): c is string => c != null,
          ),
          sortField: (this.sortField || 'name') as SortField,
          sortDirection: (this.sortDirection || 'asc') as SortDirection,
        },
        message.operations,
      );
      this.records = result.records;
      this.totalRecords = result.totalRecords;
      this.flashRecordKeys(result.flashedKeys);
    },

    disconnectZoneLive(this: LiveMethodContext) {
      intentionalClose = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      activeZone = null;
      reconnectAttempt = 0;
      if (activeSocket) {
        try {
          activeSocket.close();
        } catch {
          // ignore
        }
        activeSocket = null;
      }
      intentionalClose = false;
    },

    connectZoneLive(this: LiveMethodContext, zone: string) {
      const normalized = normalizeName(zone);
      this.disconnectZoneLive();
      activeZone = normalized;
      intentionalClose = false;

      const open = () => {
        if (activeZone !== normalized) return;
        const url = buildWsUrl(normalized, this.apiKey);
        const ws = new WebSocket(url);
        activeSocket = ws;

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(String(event.data)) as LiveMessage;
            if (message.type === 'subscribed' || message.type === 'pong') {
              return;
            }
            this.applyLiveZoneMessage(message);
          } catch {
            // ignore malformed payloads
          }
        };

        ws.onopen = () => {
          reconnectAttempt = 0;
        };

        ws.onclose = () => {
          if (activeSocket === ws) {
            activeSocket = null;
          }
          if (intentionalClose || activeZone !== normalized) {
            return;
          }
          const delay = Math.min(30000, 1000 * 2 ** reconnectAttempt);
          reconnectAttempt += 1;
          reconnectTimer = setTimeout(open, delay);
        };

        ws.onerror = () => {
          // onclose will schedule reconnect
        };
      };

      open();
    },
  };
}
