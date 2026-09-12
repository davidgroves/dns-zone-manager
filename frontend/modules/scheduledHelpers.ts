/**
 * Helpers for scheduled-change form ↔ API payload conversion.
 * Pure functions so they can be unit-tested without Alpine.
 */

export interface ScheduleFormInput {
  name: string;
  description: string;
  applyNow: boolean;
  scheduledLocal: string;
  expiryHours: number;
  autoPrerequisites: boolean;
}

export interface PrereqRowInput {
  prereq_type: string;
  name: string;
  rdtype: string;
  rdclass: string;
  data: string;
}

export interface AtomicOpInput {
  action: string;
  name: string;
  type: string;
  rdclass?: string;
  ttl?: number;
  records?: string[] | null;
  /** Preferred over `records` when building payloads from the form. */
  recordsText?: string;
}

/**
 * Split a multiline / comma-separated records field into values.
 */
export function parseRecordsText(text: string): string[] {
  return text
    .split(/\r?\n|,/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Build a blank operation row for the schedule modal.
 */
export function blankScheduleOp(defaults: Partial<AtomicOpInput> = {}): {
  action: 'add' | 'delete' | 'replace';
  name: string;
  type: string;
  rdclass: string;
  ttl: number;
  records: string[] | null;
  recordsText: string;
} {
  const action: 'add' | 'delete' | 'replace' =
    defaults.action === 'delete' || defaults.action === 'replace'
      ? defaults.action
      : 'add';
  const records = defaults.records ?? null;
  return {
    action,
    name: defaults.name || '',
    type: defaults.type || 'A',
    rdclass: defaults.rdclass || 'IN',
    ttl: defaults.ttl ?? 3600,
    records,
    recordsText: records?.join('\n') ?? '',
  };
}

/**
 * Normalise operations into the shape the API expects.
 * Incomplete rows (empty name) are dropped; delete ops always send null records.
 */
function normaliseOperations(
  operations: AtomicOpInput[],
): Array<Record<string, unknown>> {
  return operations
    .filter((op) => op.name.trim())
    .map((op) => {
      const action = op.action;
      let records: string[] | null = null;
      if (action !== 'delete') {
        if (typeof op.recordsText === 'string') {
          records = parseRecordsText(op.recordsText);
        } else {
          records = op.records ?? null;
        }
      }
      return {
        action,
        name: op.name.trim(),
        type: (op.type || 'A').trim().toUpperCase(),
        rdclass: op.rdclass || 'IN',
        ttl: op.ttl ?? 3600,
        records,
      };
    });
}

/**
 * Validate schedule-modal operations before save.
 * Returns an error message, or null when the list is usable.
 */
export function validateScheduleOps(operations: AtomicOpInput[]): string | null {
  const named = operations.filter((op) => op.name.trim());
  if (named.length === 0) {
    return 'Add at least one operation with a name';
  }
  for (const op of named) {
    if (op.action !== 'delete') {
      const records =
        typeof op.recordsText === 'string'
          ? parseRecordsText(op.recordsText)
          : (op.records ?? []);
      if (!records || records.length === 0) {
        return `Operation "${op.name.trim()}" needs at least one record value`;
      }
    }
  }
  return null;
}

/**
 * Normalise prerequisite rows, dropping incomplete ones.
 */
function normalisePrerequisites(
  prereqRows: PrereqRowInput[],
): Array<Record<string, unknown>> {
  return prereqRows
    .filter((r) => r.name.trim())
    .map((r) => {
      const base: Record<string, unknown> = {
        prereq_type: r.prereq_type,
        name: r.name.trim(),
        rdclass: r.rdclass || 'IN',
      };
      if (r.prereq_type === 'nxrrset' || r.prereq_type === 'yxrrset') {
        base.rdtype = r.rdtype || 'A';
        if (r.data.trim()) base.data = r.data.trim();
      }
      return base;
    });
}

/**
 * Resolve the scheduled_at / not_valid_after pair from the form.
 * A draft (applyNow) has no schedule and therefore no expiry.
 */
function resolveSchedule(form: ScheduleFormInput): {
  scheduledAt: string | null;
  notValidAfter: string | null;
} {
  const scheduledAt = form.applyNow
    ? null
    : localDatetimeToUtcIso(form.scheduledLocal);

  let notValidAfter: string | null = null;
  if (scheduledAt && form.expiryHours > 0) {
    const d = new Date(scheduledAt);
    d.setTime(d.getTime() + form.expiryHours * 3600 * 1000);
    notValidAfter = d.toISOString();
  }

  return { scheduledAt, notValidAfter };
}

/**
 * Convert a datetime-local value (browser local) to an ISO UTC string.
 */
export function localDatetimeToUtcIso(localValue: string): string | null {
  if (!localValue) return null;
  const d = new Date(localValue);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

/**
 * Convert a UTC ISO string to a datetime-local value in browser local time.
 */
export function utcIsoToLocalDatetime(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Format a UTC ISO string as local time only (no UTC suffix).
 */
export function formatLocalDisplay(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/**
 * Format a UTC ISO string for display (local time + UTC).
 */
export function formatScheduledDisplay(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toLocaleString()} (UTC ${d.toISOString().replace('T', ' ').slice(0, 19)})`;
}

/**
 * Build the create payload from the schedule form and queued operations.
 */
export function buildCreatePayload(
  form: ScheduleFormInput,
  zone: string,
  operations: AtomicOpInput[],
  prereqRows: PrereqRowInput[],
): Record<string, unknown> {
  const { scheduledAt, notValidAfter } = resolveSchedule(form);

  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    zone,
    operations: normaliseOperations(operations),
    prerequisites: normalisePrerequisites(prereqRows),
    scheduled_at: scheduledAt,
    not_valid_after: notValidAfter,
    auto_prerequisites: form.autoPrerequisites,
  };
}

/**
 * Build the PATCH payload for an existing change.
 *
 * scheduled_at and not_valid_after are always present so that switching a
 * change back to a draft explicitly clears them server-side.
 */
export function buildUpdatePayload(
  form: ScheduleFormInput,
  operations: AtomicOpInput[],
  prereqRows: PrereqRowInput[],
): Record<string, unknown> {
  const { scheduledAt, notValidAfter } = resolveSchedule(form);

  return {
    name: form.name.trim(),
    description: form.description.trim() || null,
    operations: normaliseOperations(operations),
    prerequisites: normalisePrerequisites(prereqRows),
    scheduled_at: scheduledAt,
    not_valid_after: notValidAfter,
    auto_prerequisites: form.autoPrerequisites,
  };
}

/**
 * Derive the schedule form fields from an existing change.
 */
export function formFromChange(change: {
  name: string;
  description: string | null;
  scheduled_at: string | null;
  not_valid_after: string | null;
  auto_prerequisites: boolean;
}): ScheduleFormInput {
  let expiryHours = 1;
  if (change.scheduled_at && change.not_valid_after) {
    const delta =
      new Date(change.not_valid_after).getTime() -
      new Date(change.scheduled_at).getTime();
    if (delta > 0) {
      expiryHours = Math.max(1, Math.round(delta / 3600000));
    }
  }

  return {
    name: change.name,
    description: change.description || '',
    applyNow: !change.scheduled_at,
    scheduledLocal: utcIsoToLocalDatetime(change.scheduled_at),
    expiryHours,
    autoPrerequisites: change.auto_prerequisites,
  };
}

/**
 * Whether an applied change can be reverted (has pre-apply snapshots).
 * Matches backend can_revert(): applied + every op has snapshot_at.
 */
export function canRevertChange(change: {
  status: string;
  operations: Array<{
    action: string;
    snapshot_at?: string | null;
  }>;
}): boolean {
  if (change.status !== 'applied') return false;
  if (!change.operations || change.operations.length === 0) return false;
  return change.operations.every(
    (op) => op.snapshot_at != null && op.snapshot_at !== '',
  );
}

/**
 * One-line human summary of an audit event's detail payload.
 */
export function formatAuditDetail(event: {
  event: string;
  detail?: Record<string, unknown> | null;
}): string {
  const detail = event.detail;
  if (!detail || typeof detail !== 'object') return '';

  if (event.event === 'updated' && detail.changes && typeof detail.changes === 'object') {
    const changes = detail.changes as Record<string, unknown>;
    const parts: string[] = [];
    for (const [key, value] of Object.entries(changes)) {
      if (!value || typeof value !== 'object') continue;
      const v = value as Record<string, unknown>;
      if (key === 'operations' || key === 'prerequisites') {
        const fromCount = v.from_count;
        const toCount = v.to_count;
        if (fromCount != null && toCount != null) {
          parts.push(`${key}: ${fromCount} → ${toCount}`);
        } else {
          parts.push(`${key} changed`);
        }
      } else if ('from' in v || 'to' in v) {
        parts.push(`${key} changed`);
      }
    }
    return parts.join('; ');
  }

  if (event.event === 'created') {
    const bits: string[] = [];
    if (detail.zone) bits.push(String(detail.zone).replace(/\.$/, ''));
    if (detail.operations_count != null) {
      bits.push(`${detail.operations_count} ops`);
    }
    return bits.join(' · ');
  }

  if (event.event === 'cancelled' && detail.previous_status) {
    return `was ${detail.previous_status}`;
  }

  if (event.event === 'failed' && detail.error) {
    return String(detail.error);
  }

  if (event.event === 'applied' && detail.trigger) {
    const bits = [`via ${detail.trigger}`];
    if (detail.new_serial != null) bits.push(`serial ${detail.new_serial}`);
    return bits.join(' · ');
  }

  if (event.event === 'reverted' && detail.operations_count != null) {
    return `${detail.operations_count} reverse ops`;
  }

  return '';
}
