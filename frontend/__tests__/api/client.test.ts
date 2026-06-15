import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, formatDNSError } from '../../api/client';

describe('api', () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('should make a request with default headers', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await api('/zones', null);

    expect(mockFetch).toHaveBeenCalledWith('/zones', {
      headers: {
        'Content-Type': 'application/json',
      },
    });
  });

  it('should include API key header when provided', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await api('/zones', 'test-api-key');

    expect(mockFetch).toHaveBeenCalledWith('/zones', {
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': 'test-api-key',
      },
    });
  });

  it('should not include API key header when null', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await api('/zones', null);

    const callHeaders = mockFetch.mock.calls[0][1].headers;
    expect(callHeaders['X-API-Key']).toBeUndefined();
  });

  it('should pass through additional options', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await api('/zones', 'key', {
      method: 'POST',
      body: JSON.stringify({ name: 'test' }),
    });

    expect(mockFetch).toHaveBeenCalledWith('/zones', {
      method: 'POST',
      body: JSON.stringify({ name: 'test' }),
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': 'key',
      },
    });
  });

  it('should merge custom headers with defaults', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 200 }));

    await api('/zones', 'key', {
      headers: {
        'X-Custom': 'value',
      },
    });

    expect(mockFetch).toHaveBeenCalledWith('/zones', {
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': 'key',
        'X-Custom': 'value',
      },
    });
  });

  it('should return the Response object', async () => {
    const mockResponse = new Response('{"data": "test"}', { status: 200 });
    mockFetch.mockResolvedValue(mockResponse);

    const response = await api('/zones', 'key');

    expect(response).toBe(mockResponse);
  });
});

describe('formatDNSError', () => {
  it('should extract rcode_description from nested detail', () => {
    const error = {
      detail: {
        rcode: 'REFUSED',
        rcode_description: 'Query refused by server',
      },
    };
    expect(formatDNSError(error)).toBe('Query refused by server (REFUSED)');
  });

  it('should extract message from nested detail', () => {
    const error = {
      detail: {
        message: 'Zone not found',
      },
    };
    expect(formatDNSError(error)).toBe('Zone not found');
  });

  it('should use string detail directly', () => {
    const error = {
      detail: 'Invalid request',
    };
    expect(formatDNSError(error)).toBe('Invalid request');
  });

  it('should fall back to top-level message', () => {
    const error = {
      message: 'Connection failed',
    };
    expect(formatDNSError(error)).toBe('Connection failed');
  });

  it('should handle string errors', () => {
    expect(formatDNSError('Simple error')).toBe('Simple error');
  });

  it('should return "Unknown error" for unhandled types', () => {
    expect(formatDNSError(null)).toBe('Unknown error');
    expect(formatDNSError(undefined)).toBe('Unknown error');
    expect(formatDNSError(123)).toBe('Unknown error');
    expect(formatDNSError({})).toBe('Unknown error');
  });

  it('should handle FastAPI validation errors', () => {
    const error = {
      detail: [
        {
          loc: ['body', 'name'],
          msg: 'field required',
          type: 'value_error.missing',
        },
      ],
    };
    // Array detail doesn't match our handlers, falls through
    expect(formatDNSError(error)).toBe('Unknown error');
  });

  it('should prioritize rcode_description over message', () => {
    const error = {
      detail: {
        rcode: 'NXDOMAIN',
        rcode_description: 'Domain does not exist',
        message: 'Generic message',
      },
    };
    expect(formatDNSError(error)).toBe('Domain does not exist (NXDOMAIN)');
  });
});
