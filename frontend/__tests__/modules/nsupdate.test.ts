import { afterEach, describe, expect, it, vi } from 'vitest';
import { createNsupdateMethods } from '../../modules/nsupdate';
import { createInitialState } from '../../state';
import type { AppState, ScheduledChange } from '../../types';

function draftChange(overrides: Partial<ScheduledChange> = {}): ScheduledChange {
  return {
    id: 'draft-1',
    name: 'NSUPDATE · example.com · 20300615T123000Z',
    description: 'zone example.com.\nupdate add www 300 A 192.0.2.1\nsend',
    zone: 'example.com.',
    status: 'draft',
    scheduled_at: null,
    not_valid_after: null,
    auto_prerequisites: true,
    created_at: '2030-06-15T12:30:00Z',
    created_by: 'test',
    updated_at: '2030-06-15T12:30:00Z',
    attempts: 0,
    next_attempt_at: null,
    last_error: null,
    applied_at: null,
    result_rcode: null,
    new_serial: null,
    operations: [
      {
        action: 'add',
        name: 'www',
        type: 'A',
        rdclass: 'IN',
        ttl: 300,
        records: ['192.0.2.1'],
      },
    ],
    prerequisites: [],
    ...overrides,
  };
}

function makeCtx(overrides: Partial<AppState> = {}) {
  const state = createInitialState({});
  Object.assign(state, overrides);
  const methods = createNsupdateMethods(state);
  const toasts: Array<{ message: string; type?: string }> = [];
  let scheduledOpened = false;
  let scheduledLoaded = 0;
  const ctx = {
    ...state,
    ...methods,
    toast(message: string, type?: string) {
      toasts.push({ message, type });
    },
    openScheduledView() {
      scheduledOpened = true;
      this.showScheduledView = true;
    },
    async loadScheduledChanges() {
      scheduledLoaded += 1;
    },
  };
  return {
    ctx,
    toasts,
    get scheduledOpened() {
      return scheduledOpened;
    },
    get scheduledLoaded() {
      return scheduledLoaded;
    },
  };
}

describe('saveNsupdateAsDrafts', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('posts text/plain to /nsupdate/drafts and opens scheduled view', async () => {
    const created = draftChange();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ created: [created], total: 1 }),
      })),
    );

    const harness = makeCtx({
      apiKey: 'test-key',
      nsupdateText:
        'zone example.com.\nupdate add www 300 A 192.0.2.1\nsend\n',
      showNsupdate: true,
    });
    const { ctx, toasts } = harness;

    await ctx.saveNsupdateAsDrafts();

    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/nsupdate/drafts'),
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          'Content-Type': 'text/plain',
          'X-API-Key': 'test-key',
        }),
        body: expect.stringContaining('update add www'),
      }),
    );
    expect(toasts[0]?.type).toBe('success');
    expect(toasts[0]?.message).toContain('1 draft');
    expect(ctx.showNsupdate).toBe(false);
    expect(ctx.nsupdateText).toBe('');
    expect(harness.scheduledOpened).toBe(true);
    expect(harness.scheduledLoaded).toBe(1);
    expect(ctx.saving).toBe(false);
  });

  it('toasts an error when the API rejects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        json: async () => ({
          detail:
            "update delete without a record type cannot be saved as a scheduled change; specify the type",
        }),
      })),
    );

    const harness = makeCtx({
      nsupdateText: 'zone example.com.\nupdate delete gone\nsend\n',
      showNsupdate: true,
    });
    const { ctx, toasts } = harness;

    await ctx.saveNsupdateAsDrafts();

    expect(toasts[0]?.type).toBe('error');
    expect(toasts[0]?.message).toContain('record type');
    expect(ctx.showNsupdate).toBe(true);
    expect(harness.scheduledOpened).toBe(false);
  });

  it('no-ops when the textarea is empty', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { ctx } = makeCtx({ nsupdateText: '   ' });
    await ctx.saveNsupdateAsDrafts();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
