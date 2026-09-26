/**
 * Unit tests for live zone-change record patching.
 */

import { describe, expect, it } from 'vitest';
import {
  applyLiveOperations,
  compareRecords,
  normalizeName,
  recordKey,
  type LiveOperation,
} from '../../modules/live';
import type { RRset } from '../../types';

function rr(
  name: string,
  type = 'A',
  records: string[] = ['192.0.2.1'],
  ttl = 60,
): RRset {
  return {
    name: normalizeName(name),
    type,
    rdclass: 'IN',
    ttl,
    records,
  };
}

describe('live applyLiveOperations', () => {
  it('replaces a visible row and returns a flash key', () => {
    const result = applyLiveOperations(
      {
        records: [rr('a.example.com.'), rr('b.example.com.')],
        pageSize: 25,
        totalRecords: 2,
        hasMoreRecords: false,
        recordCursorHistory: [],
        sortField: 'name',
        sortDirection: 'asc',
      },
      [
        {
          action: 'replace',
          name: 'a.example.com.',
          type: 'A',
          rdclass: 'IN',
          ttl: 120,
          records: ['203.0.113.9'],
        },
      ],
    );
    expect(result.records[0].records).toEqual(['203.0.113.9']);
    expect(result.records[0].ttl).toBe(120);
    expect(result.flashedKeys).toContain(
      recordKey('a.example.com.', 'A', 'IN'),
    );
  });

  it('deletes a visible row and decrements total', () => {
    const result = applyLiveOperations(
      {
        records: [rr('a.example.com.'), rr('b.example.com.')],
        pageSize: 25,
        totalRecords: 2,
        hasMoreRecords: false,
        recordCursorHistory: [],
        sortField: 'name',
        sortDirection: 'asc',
      },
      [{ action: 'delete', name: 'b.example.com.', type: 'A', rdclass: 'IN' }],
    );
    expect(result.records).toHaveLength(1);
    expect(result.totalRecords).toBe(1);
  });

  it('inserts an in-range add on the current page', () => {
    const result = applyLiveOperations(
      {
        records: [rr('a.example.com.'), rr('c.example.com.')],
        pageSize: 25,
        totalRecords: 2,
        hasMoreRecords: false,
        recordCursorHistory: [],
        sortField: 'name',
        sortDirection: 'asc',
      },
      [
        {
          action: 'add',
          name: 'b.example.com.',
          type: 'A',
          rdclass: 'IN',
          ttl: 60,
          records: ['203.0.113.2'],
        } satisfies LiveOperation,
      ],
    );
    expect(result.records.map((r) => r.name)).toEqual([
      'a.example.com.',
      'b.example.com.',
      'c.example.com.',
    ]);
    expect(result.totalRecords).toBe(3);
    expect(result.flashedKeys.length).toBe(1);
  });

  it('skips an out-of-range add on a middle page but still bumps total', () => {
    const result = applyLiveOperations(
      {
        records: [rr('m.example.com.'), rr('n.example.com.')],
        pageSize: 2,
        totalRecords: 10,
        hasMoreRecords: true,
        recordCursorHistory: ['cursor-prev'],
        sortField: 'name',
        sortDirection: 'asc',
      },
      [
        {
          action: 'add',
          name: 'a.example.com.',
          type: 'A',
          rdclass: 'IN',
          ttl: 60,
          records: ['203.0.113.1'],
        },
      ],
    );
    expect(result.records.map((r) => r.name)).toEqual([
      'm.example.com.',
      'n.example.com.',
    ]);
    expect(result.totalRecords).toBe(11);
    expect(result.flashedKeys).toHaveLength(0);
  });

  it('evicts overflow when inserting into a full page', () => {
    const result = applyLiveOperations(
      {
        records: [rr('a.example.com.'), rr('c.example.com.')],
        pageSize: 2,
        totalRecords: 2,
        hasMoreRecords: true,
        recordCursorHistory: [],
        sortField: 'name',
        sortDirection: 'asc',
      },
      [
        {
          action: 'add',
          name: 'b.example.com.',
          type: 'A',
          rdclass: 'IN',
          ttl: 60,
          records: ['203.0.113.2'],
        },
      ],
    );
    expect(result.records).toHaveLength(2);
    expect(result.records.map((r) => r.name)).toEqual([
      'a.example.com.',
      'b.example.com.',
    ]);
  });
});

describe('live compareRecords', () => {
  it('orders by name ascending', () => {
    const cmp = compareRecords(
      rr('b.example.com.'),
      rr('a.example.com.'),
      'name',
      'asc',
    );
    expect(cmp).toBeGreaterThan(0);
  });
});
