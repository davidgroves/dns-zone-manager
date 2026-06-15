import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  calculatePageSize,
  calculateZonePageSize,
  getPageSizeForMode,
} from '../../utils/pagination';

describe('calculatePageSize', () => {
  beforeEach(() => {
    // Mock window.innerHeight
    vi.stubGlobal('window', { innerHeight: 800 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should return minimum of 25 for moderate windows', () => {
    // With rowHeight=48 and headerHeight=280:
    // availableHeight = 800 - 280 = 520
    // calculated = floor(520 / 48) = 10, but minimum is 25
    const pageSize = calculatePageSize();
    expect(pageSize).toBe(25);
  });

  it('should return minimum of 25 for small windows', () => {
    vi.stubGlobal('window', { innerHeight: 400 });
    const pageSize = calculatePageSize();
    expect(pageSize).toBe(25);
  });

  it('should scale beyond minimum for large windows', () => {
    vi.stubGlobal('window', { innerHeight: 1800 });
    const pageSize = calculatePageSize();
    // availableHeight = 1800 - 280 = 1520
    // pageSize = floor(1520 / 48) = 31
    expect(pageSize).toBeGreaterThan(25);
  });
});

describe('calculateZonePageSize', () => {
  beforeEach(() => {
    vi.stubGlobal('window', { innerHeight: 800 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should calculate zone page size based on window height', () => {
    const pageSize = calculateZonePageSize();
    // With itemHeight=52 and headerHeight=180:
    // sidebarHeight = 800 - 180 = 620
    // pageSize = floor(620 / 52) = 11
    expect(pageSize).toBe(11);
  });

  it('should return minimum of 10 for small windows', () => {
    vi.stubGlobal('window', { innerHeight: 300 });
    const pageSize = calculateZonePageSize();
    expect(pageSize).toBe(10);
  });
});

describe('getPageSizeForMode', () => {
  beforeEach(() => {
    vi.stubGlobal('window', { innerHeight: 800 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should return fixed page sizes for numeric modes', () => {
    expect(getPageSizeForMode('25')).toBe(25);
    expect(getPageSizeForMode('50')).toBe(50);
    expect(getPageSizeForMode('100')).toBe(100);
    expect(getPageSizeForMode('250')).toBe(250);
  });

  it('should calculate page size for auto mode (records)', () => {
    const pageSize = getPageSizeForMode('auto', 'records');
    expect(pageSize).toBeGreaterThanOrEqual(10);
  });

  it('should calculate zone page size for auto mode (zones)', () => {
    const pageSize = getPageSizeForMode('auto', 'zones');
    expect(pageSize).toBeGreaterThanOrEqual(10);
  });

  it('should default to records type when not specified', () => {
    const autoRecords = getPageSizeForMode('auto', 'records');
    const autoDefault = getPageSizeForMode('auto');
    expect(autoDefault).toBe(autoRecords);
  });

  it('should fallback to auto for invalid mode', () => {
    // TypeScript would prevent this, but testing runtime behavior
    const pageSize = getPageSizeForMode(
      'invalid' as unknown as Parameters<typeof getPageSizeForMode>[0],
    );
    expect(pageSize).toBeGreaterThanOrEqual(10);
  });
});
