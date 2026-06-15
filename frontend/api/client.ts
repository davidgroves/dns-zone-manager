/**
 * API client for DNS API requests.
 * All methods use the apiKey from state for authentication.
 */

/**
 * Base path for all versioned API endpoints.
 * Update this when bumping API version.
 */
export const API_BASE = '/v1';

export interface ApiOptions extends RequestInit {
  headers?: Record<string, string>;
}

/**
 * Make an API request with authentication.
 */
export function api(
  path: string,
  apiKey: string | null,
  options: ApiOptions = {},
): Promise<Response> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }

  return fetch(path, {
    ...options,
    headers,
  });
}

/**
 * Parse API error response into user-friendly message.
 */
export function formatDNSError(err: unknown): string {
  if (err && typeof err === 'object') {
    const errObj = err as Record<string, unknown>;
    const detail = errObj.detail;

    if (detail && typeof detail === 'object') {
      const detailObj = detail as Record<string, unknown>;
      if (detailObj.rcode_description) {
        return `${detailObj.rcode_description} (${detailObj.rcode})`;
      }
      if (detailObj.message) {
        return String(detailObj.message);
      }
    }

    if (typeof detail === 'string') {
      return detail;
    }

    if (errObj.message) {
      return String(errObj.message);
    }
  }

  if (typeof err === 'string') {
    return err;
  }

  return 'Unknown error';
}
