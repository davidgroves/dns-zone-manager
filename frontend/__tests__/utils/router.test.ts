import { describe, expect, it } from 'vitest';
import {
  URL_PARAMS,
  buildQueryString,
  parseSearchString,
} from '../../modules/router';

describe('URL_PARAMS', () => {
  it('should define expected parameter names', () => {
    expect(URL_PARAMS.zone).toBe('zone');
    expect(URL_PARAMS.page).toBe('page');
    expect(URL_PARAMS.query).toBe('q');
    expect(URL_PARAMS.type).toBe('type');
    expect(URL_PARAMS.field).toBe('field');
    expect(URL_PARAMS.allZones).toBe('all');
    expect(URL_PARAMS.view).toBe('view');
  });
});

describe('parseSearchString', () => {
  it('should return default values for empty string', () => {
    const params = parseSearchString('');

    expect(params).toEqual({
      zone: null,
      page: 1,
      searchQuery: null,
      searchType: null,
      searchField: 'either',
      searchAllZones: false,
      view: null,
    });
  });

  it('should parse zone parameter', () => {
    const params = parseSearchString('?zone=example.com.');

    expect(params.zone).toBe('example.com.');
  });

  it('should parse zone without leading ?', () => {
    const params = parseSearchString('zone=example.com.');

    expect(params.zone).toBe('example.com.');
  });

  it('should parse page parameter', () => {
    const params = parseSearchString('?page=3');

    expect(params.page).toBe(3);
  });

  it('should default page to 1 for invalid values', () => {
    const params = parseSearchString('?page=invalid');

    expect(params.page).toBe(1);
  });

  it('should default page to 1 for NaN', () => {
    const params = parseSearchString('?page=abc');

    expect(params.page).toBe(1);
  });

  it('should parse search query', () => {
    const params = parseSearchString('?q=www');

    expect(params.searchQuery).toBe('www');
  });

  it('should parse search type', () => {
    const params = parseSearchString('?type=A');

    expect(params.searchType).toBe('A');
  });

  it('should parse search field - name', () => {
    const params = parseSearchString('?field=name');

    expect(params.searchField).toBe('name');
  });

  it('should parse search field - data', () => {
    const params = parseSearchString('?field=data');

    expect(params.searchField).toBe('data');
  });

  it('should default search field to either', () => {
    const params = parseSearchString('?q=test');

    expect(params.searchField).toBe('either');
  });

  it('should parse all zones flag - true', () => {
    const params = parseSearchString('?all=true');

    expect(params.searchAllZones).toBe(true);
  });

  it('should parse all zones flag - false for other values', () => {
    const params = parseSearchString('?all=false');

    expect(params.searchAllZones).toBe(false);
  });

  it('should default all zones to false', () => {
    const params = parseSearchString('?q=test');

    expect(params.searchAllZones).toBe(false);
  });

  it('should parse combined parameters', () => {
    const params = parseSearchString(
      '?zone=example.com.&q=www&type=A&field=name&page=2',
    );

    expect(params).toEqual({
      zone: 'example.com.',
      page: 2,
      searchQuery: 'www',
      searchType: 'A',
      searchField: 'name',
      searchAllZones: false,
      view: null,
    });
  });

  it('should parse global search parameters', () => {
    const params = parseSearchString('?q=mail&all=true&type=MX&field=data');

    expect(params).toEqual({
      zone: null,
      page: 1,
      searchQuery: 'mail',
      searchType: 'MX',
      searchField: 'data',
      searchAllZones: true,
      view: null,
    });
  });

  it('should handle URL-encoded values', () => {
    const params = parseSearchString('?q=test%20value&zone=example.com.');

    expect(params.searchQuery).toBe('test value');
    expect(params.zone).toBe('example.com.');
  });

  it('should handle special characters in zone name', () => {
    const params = parseSearchString('?zone=sub.example.com.');

    expect(params.zone).toBe('sub.example.com.');
  });

  it('should parse scheduled view', () => {
    const params = parseSearchString('?view=scheduled');
    expect(params.view).toBe('scheduled');
  });

  it('should parse audit view', () => {
    const params = parseSearchString('?view=audit');
    expect(params.view).toBe('audit');
  });
});

describe('buildQueryString', () => {
  it('should return empty string for empty params', () => {
    const url = buildQueryString({});

    expect(url).toBe('');
  });

  it('should include audit view', () => {
    const url = buildQueryString({ view: 'audit' });
    expect(url).toBe('?view=audit');
  });

  it('should build query string with zone', () => {
    const url = buildQueryString({ zone: 'example.com.' });

    expect(url).toBe('?zone=example.com.');
  });

  it('should exclude page 1 from query string', () => {
    const url = buildQueryString({ zone: 'example.com.', page: 1 });

    expect(url).toBe('?zone=example.com.');
    expect(url).not.toContain('page=');
  });

  it('should include page > 1 in query string', () => {
    const url = buildQueryString({ zone: 'example.com.', page: 2 });

    expect(url).toBe('?zone=example.com.&page=2');
  });

  it('should build query string with search query', () => {
    const url = buildQueryString({ searchQuery: 'www' });

    expect(url).toBe('?q=www');
  });

  it('should build query string with search type', () => {
    const url = buildQueryString({ searchType: 'A' });

    expect(url).toBe('?type=A');
  });

  it('should exclude default search field (either) from query string', () => {
    const url = buildQueryString({
      searchQuery: 'test',
      searchField: 'either',
    });

    expect(url).toBe('?q=test');
    expect(url).not.toContain('field=');
  });

  it('should include non-default search field - name', () => {
    const url = buildQueryString({ searchQuery: 'test', searchField: 'name' });

    expect(url).toBe('?q=test&field=name');
  });

  it('should include non-default search field - data', () => {
    const url = buildQueryString({ searchQuery: 'test', searchField: 'data' });

    expect(url).toBe('?q=test&field=data');
  });

  it('should include all zones flag when true', () => {
    const url = buildQueryString({ searchQuery: 'test', searchAllZones: true });

    expect(url).toBe('?q=test&all=true');
  });

  it('should exclude all zones flag when false', () => {
    const url = buildQueryString({
      searchQuery: 'test',
      searchAllZones: false,
    });

    expect(url).toBe('?q=test');
    expect(url).not.toContain('all=');
  });

  it('should build complete query string with all parameters', () => {
    const url = buildQueryString({
      zone: 'example.com.',
      page: 3,
      searchQuery: 'www',
      searchType: 'A',
      searchField: 'name',
      searchAllZones: false,
    });

    expect(url).toContain('zone=example.com.');
    expect(url).toContain('page=3');
    expect(url).toContain('q=www');
    expect(url).toContain('type=A');
    expect(url).toContain('field=name');
    expect(url).not.toContain('all=');
  });

  it('should build global search query string', () => {
    const url = buildQueryString({
      searchQuery: 'mail',
      searchType: 'MX',
      searchField: 'data',
      searchAllZones: true,
      page: 2,
    });

    expect(url).toContain('q=mail');
    expect(url).toContain('type=MX');
    expect(url).toContain('field=data');
    expect(url).toContain('all=true');
    expect(url).toContain('page=2');
  });

  it('should handle null values gracefully', () => {
    const url = buildQueryString({
      zone: null,
      searchQuery: null,
      searchType: null,
    });

    expect(url).toBe('');
  });
});

describe('URL round-trip', () => {
  it('should round-trip zone navigation', () => {
    const original = { zone: 'test.example.', page: 1 };
    const queryString = buildQueryString(original);
    const parsed = parseSearchString(queryString);

    expect(parsed.zone).toBe(original.zone);
    expect(parsed.page).toBe(1);
  });

  it('should round-trip zone with pagination', () => {
    const original = { zone: 'test.example.', page: 5 };
    const queryString = buildQueryString(original);
    const parsed = parseSearchString(queryString);

    expect(parsed.zone).toBe(original.zone);
    expect(parsed.page).toBe(original.page);
  });

  it('should round-trip search parameters', () => {
    const original = {
      zone: 'example.com.',
      searchQuery: 'www',
      searchType: 'A',
      searchField: 'name' as const,
      searchAllZones: false,
      page: 2,
    };
    const queryString = buildQueryString(original);
    const parsed = parseSearchString(queryString);

    expect(parsed.zone).toBe(original.zone);
    expect(parsed.searchQuery).toBe(original.searchQuery);
    expect(parsed.searchType).toBe(original.searchType);
    expect(parsed.searchField).toBe(original.searchField);
    expect(parsed.searchAllZones).toBe(original.searchAllZones);
    expect(parsed.page).toBe(original.page);
  });

  it('should round-trip global search parameters', () => {
    const original = {
      zone: null,
      searchQuery: 'mail',
      searchType: 'MX',
      searchField: 'data' as const,
      searchAllZones: true,
      page: 1,
    };
    const queryString = buildQueryString(original);
    const parsed = parseSearchString(queryString);

    expect(parsed.zone).toBe(null);
    expect(parsed.searchQuery).toBe(original.searchQuery);
    expect(parsed.searchType).toBe(original.searchType);
    expect(parsed.searchField).toBe(original.searchField);
    expect(parsed.searchAllZones).toBe(original.searchAllZones);
  });

  it('should round-trip with default search field', () => {
    const original = {
      searchQuery: 'test',
      searchField: 'either' as const,
    };
    const queryString = buildQueryString(original);
    const parsed = parseSearchString(queryString);

    // 'either' is the default, so it's not in the URL
    expect(queryString).not.toContain('field=');
    // But parsing returns 'either' as the default
    expect(parsed.searchField).toBe('either');
  });
});

describe('Edge cases', () => {
  it('should handle empty zone value', () => {
    const params = parseSearchString('?zone=');

    // Empty string from URLSearchParams.get returns empty string,
    // but our parser converts empty to null
    expect(params.zone).toBe(null);
  });

  it('should handle zone with dots', () => {
    const params = parseSearchString('?zone=sub.domain.example.com.');

    expect(params.zone).toBe('sub.domain.example.com.');
  });

  it('should handle multiple ampersands', () => {
    const params = parseSearchString('?zone=example.com.&&page=2');

    expect(params.zone).toBe('example.com.');
    expect(params.page).toBe(2);
  });

  it('should handle page=0', () => {
    const params = parseSearchString('?page=0');

    // 0 is technically valid but treated as page 1 in navigation
    expect(params.page).toBe(0);
  });

  it('should handle negative page', () => {
    const params = parseSearchString('?page=-1');

    expect(params.page).toBe(-1);
  });

  it('should handle very large page number', () => {
    const params = parseSearchString('?page=999999');

    expect(params.page).toBe(999999);
  });

  it('should handle unknown search field value', () => {
    const params = parseSearchString('?field=unknown');

    // Unknown values are passed through
    expect(params.searchField).toBe('unknown');
  });
});
