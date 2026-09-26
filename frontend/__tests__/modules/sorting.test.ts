import { describe, expect, it } from 'vitest';
import { createSortingComputed, createSortingMethods } from '../../modules/sorting';
import type { AppState, RRset } from '../../types';

function makeState(overrides: Partial<AppState> = {}): AppState {
  return {
    records: [],
    searchType: '',
    sortField: 'name',
    sortDirection: 'asc',
    searchResults: [],
    searchSortField: 'name',
    searchSortDirection: 'asc',
    ...overrides,
  } as AppState;
}

const sample: RRset[] = [
  {
    name: 'z.example.com.',
    type: 'A',
    ttl: 300,
    rdclass: 'IN',
    records: ['192.0.2.3'],
  },
  {
    name: 'a.example.com.',
    type: 'TXT',
    ttl: 60,
    rdclass: 'IN',
    records: ['"hello"'],
  },
  {
    name: 'm.example.com.',
    type: 'AAAA',
    ttl: 120,
    rdclass: 'IN',
    records: ['2001:db8::1'],
  },
];

describe('createSortingMethods', () => {
  it('toggles direction when sorting the same field', () => {
    const state = makeState({ sortField: 'name', sortDirection: 'asc' });
    const methods = createSortingMethods(state);
    methods.sortBy.call(state as never, 'name');
    expect(state.sortDirection).toBe('desc');
    methods.sortBy.call(state as never, 'type');
    expect(state.sortField).toBe('type');
    expect(state.sortDirection).toBe('asc');
  });

  it('returns sort icons', () => {
    const state = makeState({ sortField: 'ttl', sortDirection: 'desc' });
    const methods = createSortingMethods(state);
    expect(methods.getSortIcon.call(state as never, 'ttl')).toBe('↓');
    expect(methods.getSortIcon.call(state as never, 'name')).toBe('↕');
  });
});

describe('createSortingComputed.filteredRecords', () => {
  it('sorts by name ascending', () => {
    const state = makeState({
      records: sample,
      sortField: 'name',
      sortDirection: 'asc',
    });
    const computed = createSortingComputed(state);
    const names = computed.filteredRecords.map((r) => r.name);
    expect(names).toEqual([
      'a.example.com.',
      'm.example.com.',
      'z.example.com.',
    ]);
  });

  it('sorts by data field (not name)', () => {
    const state = makeState({
      records: sample,
      sortField: 'data',
      sortDirection: 'asc',
    });
    const computed = createSortingComputed(state);
    const data = computed.filteredRecords.map((r) => r.records[0].toLowerCase());
    expect(data).toEqual(['"hello"', '192.0.2.3', '2001:db8::1'].sort());
  });

  it('filters by searchType before sorting', () => {
    const state = makeState({
      records: sample,
      searchType: 'A',
      sortField: 'name',
      sortDirection: 'asc',
    });
    const computed = createSortingComputed(state);
    expect(computed.filteredRecords).toHaveLength(1);
    expect(computed.filteredRecords[0].type).toBe('A');
  });
});
