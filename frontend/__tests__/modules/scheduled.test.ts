import { describe, expect, it } from 'vitest';
import {
  blankScheduleOp,
  buildCreatePayload,
  buildUpdatePayload,
  canRevertChange,
  formFromChange,
  formatAuditDetail,
  formatLocalDisplay,
  formatScheduledDisplay,
  localDatetimeToUtcIso,
  parseRecordsText,
  utcIsoToLocalDatetime,
  validateScheduleOps,
} from '../../modules/scheduledHelpers';
import { createScheduledMethods } from '../../modules/scheduled';
import { createInitialState } from '../../state';
import type { AppState } from '../../types';

describe('localDatetimeToUtcIso', () => {
  it('returns null for empty input', () => {
    expect(localDatetimeToUtcIso('')).toBeNull();
  });

  it('converts a local datetime-local value to ISO UTC', () => {
    const iso = localDatetimeToUtcIso('2030-06-15T14:30');
    expect(iso).not.toBeNull();
    expect(iso).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{3}Z$/);
    // Round-trip should preserve the local wall-clock when parsed back
    const again = utcIsoToLocalDatetime(iso);
    expect(again).toBe('2030-06-15T14:30');
  });
});

describe('utcIsoToLocalDatetime', () => {
  it('returns empty string for null/undefined', () => {
    expect(utcIsoToLocalDatetime(null)).toBe('');
    expect(utcIsoToLocalDatetime(undefined)).toBe('');
  });
});

describe('formatScheduledDisplay', () => {
  it('returns em dash for null', () => {
    expect(formatScheduledDisplay(null)).toBe('—');
  });

  it('includes UTC in the display string', () => {
    const display = formatScheduledDisplay('2030-01-01T12:00:00.000Z');
    expect(display).toContain('UTC');
    expect(display).toContain('2030');
  });
});

describe('formatLocalDisplay', () => {
  it('returns em dash for null', () => {
    expect(formatLocalDisplay(null)).toBe('—');
  });

  it('shows local time without UTC suffix', () => {
    const display = formatLocalDisplay('2030-01-01T12:00:00.000Z');
    expect(display).not.toContain('UTC');
    expect(display).toContain('2030');
  });
});

describe('buildCreatePayload', () => {
  const ops = [
    {
      action: 'add',
      name: 'www',
      type: 'A',
      rdclass: 'IN',
      ttl: 3600,
      records: ['192.0.2.1'],
    },
  ];

  it('creates a draft payload when applyNow is true', () => {
    const payload = buildCreatePayload(
      {
        name: 'Test change',
        description: 'notes',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      'example.com.',
      ops,
      [],
    );

    expect(payload.name).toBe('Test change');
    expect(payload.zone).toBe('example.com.');
    expect(payload.scheduled_at).toBeNull();
    expect(payload.not_valid_after).toBeNull();
    expect(payload.auto_prerequisites).toBe(true);
    expect(payload.operations).toHaveLength(1);
  });

  it('includes schedule and expiry when applyNow is false', () => {
    const payload = buildCreatePayload(
      {
        name: 'Timed',
        description: '',
        applyNow: false,
        scheduledLocal: '2030-06-15T14:30',
        expiryHours: 2,
        autoPrerequisites: false,
      },
      'example.com.',
      ops,
      [
        {
          prereq_type: 'nxrrset',
          name: 'www',
          rdtype: 'A',
          rdclass: 'IN',
          data: '',
        },
      ],
    );

    expect(payload.scheduled_at).toMatch(/Z$/);
    expect(payload.not_valid_after).toMatch(/Z$/);
    expect(payload.auto_prerequisites).toBe(false);
    expect(payload.prerequisites).toHaveLength(1);
    expect((payload.prerequisites as Array<Record<string, string>>)[0].prereq_type).toBe(
      'nxrrset',
    );

    const scheduled = new Date(payload.scheduled_at as string).getTime();
    const expiry = new Date(payload.not_valid_after as string).getTime();
    expect(expiry - scheduled).toBe(2 * 3600 * 1000);
  });

  it('skips empty prerequisite rows', () => {
    const payload = buildCreatePayload(
      {
        name: 'X',
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      'example.com.',
      ops,
      [{ prereq_type: 'nxdomain', name: '  ', rdtype: '', rdclass: 'IN', data: '' }],
    );
    expect(payload.prerequisites).toEqual([]);
  });
});

describe('buildUpdatePayload', () => {
  const ops = [
    {
      action: 'add',
      name: 'www',
      type: 'A',
      rdclass: 'IN',
      ttl: 3600,
      records: ['192.0.2.1'],
    },
  ];

  it('omits the zone, which cannot be changed after creation', () => {
    const payload = buildUpdatePayload(
      {
        name: 'Renamed',
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      ops,
      [],
    );
    expect(payload).not.toHaveProperty('zone');
    expect(payload.name).toBe('Renamed');
  });

  it('sends an explicit null schedule so a change reverts to a draft', () => {
    const payload = buildUpdatePayload(
      {
        name: 'Back to draft',
        description: '',
        applyNow: true,
        scheduledLocal: '2030-06-15T14:30',
        expiryHours: 4,
        autoPrerequisites: true,
      },
      ops,
      [],
    );
    expect(payload).toHaveProperty('scheduled_at');
    expect(payload.scheduled_at).toBeNull();
    expect(payload.not_valid_after).toBeNull();
  });

  it('carries edited operations and prerequisites', () => {
    const payload = buildUpdatePayload(
      {
        name: 'Edited',
        description: 'why',
        applyNow: false,
        scheduledLocal: '2030-06-15T14:30',
        expiryHours: 3,
        autoPrerequisites: false,
      },
      [ops[0], { action: 'delete', name: 'old', type: 'A', records: null }],
      [{ prereq_type: 'yxrrset', name: 'www', rdtype: 'A', rdclass: 'IN', data: '192.0.2.9' }],
    );

    expect(payload.operations).toHaveLength(2);
    // Defaults are filled in for operations that omit them.
    const operations = payload.operations as Array<Record<string, unknown>>;
    expect(operations[1].rdclass).toBe('IN');
    expect(operations[1].ttl).toBe(3600);

    const prerequisites = payload.prerequisites as Array<Record<string, unknown>>;
    expect(prerequisites[0].data).toBe('192.0.2.9');

    const scheduled = new Date(payload.scheduled_at as string).getTime();
    const expiry = new Date(payload.not_valid_after as string).getTime();
    expect(expiry - scheduled).toBe(3 * 3600 * 1000);
  });
});

describe('formFromChange', () => {
  it('treats a change with no schedule as a draft', () => {
    const form = formFromChange({
      name: 'Draft change',
      description: null,
      scheduled_at: null,
      not_valid_after: null,
      auto_prerequisites: true,
    });
    expect(form.applyNow).toBe(true);
    expect(form.scheduledLocal).toBe('');
    expect(form.description).toBe('');
    expect(form.expiryHours).toBe(1);
  });

  it('recovers the expiry window in hours from the stored timestamps', () => {
    const form = formFromChange({
      name: 'Timed change',
      description: 'notes',
      scheduled_at: '2030-06-15T14:30:00.000Z',
      not_valid_after: '2030-06-15T20:30:00.000Z',
      auto_prerequisites: false,
    });
    expect(form.applyNow).toBe(false);
    expect(form.expiryHours).toBe(6);
    expect(form.autoPrerequisites).toBe(false);
    expect(form.scheduledLocal).toBe(utcIsoToLocalDatetime('2030-06-15T14:30:00.000Z'));
  });

  it('round-trips through buildUpdatePayload without drifting', () => {
    const original = {
      name: 'Round trip',
      description: 'notes',
      scheduled_at: '2030-06-15T14:30:00.000Z',
      not_valid_after: '2030-06-15T16:30:00.000Z',
      auto_prerequisites: true,
    };
    const payload = buildUpdatePayload(
      formFromChange(original),
      [{ action: 'add', name: 'www', type: 'A', records: ['192.0.2.1'] }],
      [],
    );
    expect(payload.scheduled_at).toBe(original.scheduled_at);
    expect(payload.not_valid_after).toBe(original.not_valid_after);
  });
});

describe('parseRecordsText', () => {
  it('splits on newlines and commas, trimming empties', () => {
    expect(parseRecordsText('192.0.2.1\n192.0.2.2')).toEqual([
      '192.0.2.1',
      '192.0.2.2',
    ]);
    expect(parseRecordsText(' a.example. , b.example. ')).toEqual([
      'a.example.',
      'b.example.',
    ]);
    expect(parseRecordsText('  \n  ')).toEqual([]);
  });
});

describe('blankScheduleOp', () => {
  it('defaults to an empty ADD A row', () => {
    const op = blankScheduleOp();
    expect(op.action).toBe('add');
    expect(op.name).toBe('');
    expect(op.type).toBe('A');
    expect(op.ttl).toBe(3600);
    expect(op.recordsText).toBe('');
  });

  it('copies records into recordsText for editing', () => {
    const op = blankScheduleOp({
      action: 'replace',
      name: 'www',
      type: 'A',
      records: ['192.0.2.1', '192.0.2.2'],
    });
    expect(op.recordsText).toBe('192.0.2.1\n192.0.2.2');
  });
});

describe('validateScheduleOps', () => {
  it('rejects an empty list', () => {
    expect(validateScheduleOps([])).toMatch(/at least one/);
  });

  it('rejects add/replace rows without record values', () => {
    expect(
      validateScheduleOps([
        { action: 'add', name: 'www', type: 'A', recordsText: '' },
      ]),
    ).toMatch(/needs at least one record/);
  });

  it('allows delete rows without records', () => {
    expect(
      validateScheduleOps([
        { action: 'delete', name: 'www', type: 'A', recordsText: '' },
      ]),
    ).toBeNull();
  });
});

describe('recordsText in payloads', () => {
  it('prefers recordsText over records when building the update payload', () => {
    const payload = buildUpdatePayload(
      {
        name: 'Edited',
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      [
        {
          action: 'add',
          name: 'www',
          type: 'a',
          records: ['192.0.2.1'],
          recordsText: '192.0.2.9\n192.0.2.10',
        },
        {
          action: 'delete',
          name: 'old',
          type: 'A',
          recordsText: 'should-be-ignored',
        },
      ],
      [],
    );

    const operations = payload.operations as Array<Record<string, unknown>>;
    expect(operations).toHaveLength(2);
    expect(operations[0].type).toBe('A');
    expect(operations[0].records).toEqual(['192.0.2.9', '192.0.2.10']);
    expect(operations[1].records).toBeNull();
  });

  it('drops incomplete operation rows that have no name', () => {
    const payload = buildUpdatePayload(
      {
        name: 'Edited',
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      [
        { action: 'add', name: 'www', type: 'A', recordsText: '192.0.2.1' },
        { action: 'add', name: '  ', type: 'A', recordsText: '192.0.2.2' },
      ],
      [],
    );
    expect(payload.operations).toHaveLength(1);
  });
});

describe('canRevertChange', () => {
  it('returns false for non-applied statuses', () => {
    expect(
      canRevertChange({
        status: 'draft',
        operations: [{ action: 'add', snapshot_at: '2030-01-01T00:00:00Z' }],
      }),
    ).toBe(false);
  });

  it('returns false when any op lacks snapshot_at', () => {
    expect(
      canRevertChange({
        status: 'applied',
        operations: [{ action: 'add', snapshot_at: null }],
      }),
    ).toBe(false);
  });

  it('returns true when applied and all ops have snapshot_at', () => {
    expect(
      canRevertChange({
        status: 'applied',
        operations: [
          { action: 'add', snapshot_at: '2030-01-01T00:00:00Z' },
          { action: 'delete', snapshot_at: '2030-01-01T00:00:00Z' },
        ],
      }),
    ).toBe(true);
  });

  it('returns false for empty operations', () => {
    expect(canRevertChange({ status: 'applied', operations: [] })).toBe(false);
  });
});

describe('formatAuditDetail', () => {
  it('summarizes updated field diffs', () => {
    const summary = formatAuditDetail({
      event: 'updated',
      detail: {
        status: 'draft',
        changes: {
          name: { from: 'A', to: 'B' },
          operations: { from_count: 1, to_count: 3, from: [], to: [] },
        },
      },
    });
    expect(summary).toContain('name changed');
    expect(summary).toContain('operations: 1 → 3');
  });

  it('summarizes created events', () => {
    expect(
      formatAuditDetail({
        event: 'created',
        detail: { zone: 'example.com.', operations_count: 2 },
      }),
    ).toBe('example.com · 2 ops');
  });

  it('summarizes cancelled previous status', () => {
    expect(
      formatAuditDetail({
        event: 'cancelled',
        detail: { previous_status: 'scheduled' },
      }),
    ).toBe('was scheduled');
  });

  it('returns empty string when no detail', () => {
    expect(formatAuditDetail({ event: 'claimed', detail: null })).toBe('');
  });
});

function makeScheduledCtx(overrides: Partial<AppState> = {}) {
  const state = createInitialState({});
  Object.assign(state, overrides);
  const methods = createScheduledMethods(state);
  const toasts: Array<{ message: string; type?: string }> = [];
  const ctx = {
    ...state,
    ...methods,
    toast(message: string, type?: string) {
      toasts.push({ message, type });
    },
    async loadZones() {
      if (ctx.zones.length === 0) {
        ctx.zones = [{ zone: 'loaded.example.' }];
      }
    },
    async loadRecords() {},
    updateUrlFromState() {},
    async loadScheduledChanges() {},
    async viewScheduledChange() {},
    async runScheduledPreview() {},
  };
  return { ctx, toasts, methods };
}

describe('openNewScheduledChange', () => {
  it('opens the modal with one blank op and scheduleSource new', async () => {
    const { ctx } = makeScheduledCtx({
      zones: [{ zone: 'example.com.' }, { zone: 'other.com.' }],
      selectedZone: 'example.com.',
    });

    await ctx.openNewScheduledChange();

    expect(ctx.showScheduleModal).toBe(true);
    expect(ctx.scheduleMode).toBe('create');
    expect(ctx.scheduleSource).toBe('new');
    expect(ctx.scheduleZone).toBe('example.com.');
    expect(ctx.scheduleOps).toHaveLength(1);
    expect(ctx.scheduleOps[0].name).toBe('');
    expect(ctx.scheduleForm.name).toBe('');
    expect(ctx.scheduleForm.applyNow).toBe(true);
    expect(ctx.editingChangeId).toBeNull();
  });

  it('defaults to the only zone when none is selected', async () => {
    const { ctx } = makeScheduledCtx({
      zones: [{ zone: 'solo.example.' }],
      selectedZone: null,
    });

    await ctx.openNewScheduledChange();

    expect(ctx.scheduleZone).toBe('solo.example.');
  });

  it('loads zones when the list is empty', async () => {
    const { ctx } = makeScheduledCtx({ zones: [], selectedZone: null });

    await ctx.openNewScheduledChange();

    expect(ctx.zones).toEqual([{ zone: 'loaded.example.' }]);
    expect(ctx.scheduleZone).toBe('loaded.example.');
  });

  it('does not clear an existing atomic queue', async () => {
    const { ctx } = makeScheduledCtx({
      zones: [{ zone: 'example.com.' }],
      atomicMode: true,
      atomicQueue: [
        {
          action: 'add',
          zone: 'example.com.',
          name: 'www',
          type: 'A',
          rdclass: 'IN',
          ttl: 3600,
          records: ['192.0.2.1'],
        },
      ],
    });

    await ctx.openNewScheduledChange();

    expect(ctx.atomicMode).toBe(true);
    expect(ctx.atomicQueue).toHaveLength(1);
  });
});

describe('openScheduleFromQueue', () => {
  it('sets scheduleSource to queue and keeps Save as title', () => {
    const { ctx } = makeScheduledCtx({
      atomicQueue: [
        {
          action: 'add',
          zone: 'example.com.',
          name: 'www',
          type: 'A',
          rdclass: 'IN',
          ttl: 3600,
          records: ['192.0.2.1'],
        },
      ],
    });

    ctx.openScheduleFromQueue();

    expect(ctx.scheduleSource).toBe('queue');
    expect(ctx.scheduleModalTitle()).toBe('Save as Scheduled Change');
    expect(ctx.scheduleZone).toBe('example.com.');
    expect(ctx.scheduleOps).toHaveLength(1);
  });
});

describe('scheduleModalTitle', () => {
  it('returns New / Save as / Edit based on scheduleSource', () => {
    const { ctx } = makeScheduledCtx();
    ctx.scheduleSource = 'new';
    expect(ctx.scheduleModalTitle()).toBe('New Scheduled Change');
    ctx.scheduleSource = 'queue';
    expect(ctx.scheduleModalTitle()).toBe('Save as Scheduled Change');
    ctx.scheduleMode = 'edit';
    ctx.scheduleSource = 'edit';
    expect(ctx.scheduleModalTitle()).toBe('Edit Scheduled Change');
  });
});

describe('saveScheduledChange validation', () => {
  it('requires a zone when creating', async () => {
    const { ctx, toasts } = makeScheduledCtx({
      scheduleMode: 'create',
      scheduleSource: 'new',
      scheduleZone: '',
      scheduleForm: {
        name: 'Needs zone',
        description: '',
        applyNow: true,
        scheduledLocal: '',
        expiryHours: 1,
        autoPrerequisites: true,
      },
      scheduleOps: [
        blankScheduleOp({
          name: 'www',
          type: 'A',
          records: ['192.0.2.1'],
        }),
      ],
    });

    await ctx.saveScheduledChange();

    expect(toasts).toEqual([{ message: 'Zone is required', type: 'error' }]);
  });
});
