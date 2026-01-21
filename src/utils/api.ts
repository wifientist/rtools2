/**
 * Global API fetch wrapper with automatic token refresh on 401
 *
 * This wrapper intercepts 401 responses, attempts to refresh the access token,
 * and retries the original request. If refresh fails, redirects to login.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

// Track in-flight refresh to prevent concurrent refresh attempts
let refreshPromise: Promise<boolean> | null = null;

/**
 * Attempt to refresh the access token using the refresh token cookie
 */
async function refreshAccessToken(): Promise<boolean> {
  // If refresh already in progress, wait for it
  if (refreshPromise) {
    console.log('[api] Refresh already in progress, waiting...');
    return refreshPromise;
  }

  refreshPromise = (async () => {
    try {
      console.log('[api] Attempting token refresh...');
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });

      if (response.ok) {
        console.log('[api] Token refreshed successfully');
        return true;
      } else {
        console.warn('[api] Token refresh failed:', response.status);
        return false;
      }
    } catch (error) {
      console.error('[api] Token refresh error:', error);
      return false;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

/**
 * Redirect to login page (used when refresh fails)
 */
function redirectToLogin() {
  console.log('[api] Redirecting to login...');
  window.location.href = '/login';
}

/**
 * Global fetch wrapper with automatic 401 handling and token refresh
 *
 * Usage:
 *   import { apiFetch } from '@/utils/api';
 *   const response = await apiFetch('/api/some-endpoint', { method: 'POST', body: ... });
 *
 * @param url - The URL to fetch (can be relative or absolute)
 * @param options - Standard fetch options
 * @param skipAuthRetry - If true, don't attempt token refresh on 401 (used internally)
 * @returns The fetch Response object
 */
export async function apiFetch(
  url: string,
  options: RequestInit = {},
  skipAuthRetry: boolean = false
): Promise<Response> {
  // Ensure credentials are always included for cookie-based auth
  const fetchOptions: RequestInit = {
    ...options,
    credentials: 'include',
  };

  const response = await fetch(url, fetchOptions);

  // Handle 401 Unauthorized
  if (response.status === 401 && !skipAuthRetry) {
    console.log('[api] Got 401, attempting refresh and retry...');

    const refreshed = await refreshAccessToken();

    if (refreshed) {
      // Retry the original request with fresh token
      console.log('[api] Retrying original request after refresh...');
      return fetch(url, fetchOptions);
    } else {
      // Refresh failed - redirect to login
      redirectToLogin();
      // Return original response (caller may handle this)
      return response;
    }
  }

  return response;
}

/**
 * Convenience wrapper for JSON API calls
 * Automatically sets Content-Type and parses JSON response
 *
 * @param url - The URL to fetch
 * @param options - Fetch options (body will be JSON.stringify'd if object)
 * @returns Parsed JSON response
 * @throws Error if response is not ok
 */
export async function apiJson<T = unknown>(
  url: string,
  options: RequestInit & { body?: unknown } = {}
): Promise<T> {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  };

  const fetchOptions: RequestInit = {
    ...options,
    headers,
  };

  // Stringify body if it's an object
  if (options.body && typeof options.body === 'object') {
    fetchOptions.body = JSON.stringify(options.body);
  }

  const response = await apiFetch(url, fetchOptions);

  if (!response.ok) {
    const errorText = await response.text();
    let errorMessage: string;
    try {
      const errorJson = JSON.parse(errorText);
      errorMessage = errorJson.detail || errorJson.message || errorJson.error || errorText;
    } catch {
      errorMessage = errorText || `HTTP ${response.status}`;
    }
    throw new Error(errorMessage);
  }

  // Handle empty responses
  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  return JSON.parse(text);
}

/**
 * GET request helper
 */
export async function apiGet<T = unknown>(url: string): Promise<T> {
  return apiJson<T>(url, { method: 'GET' });
}

/**
 * POST request helper
 */
export async function apiPost<T = unknown>(url: string, body?: unknown): Promise<T> {
  return apiJson<T>(url, { method: 'POST', body });
}

/**
 * PUT request helper
 */
export async function apiPut<T = unknown>(url: string, body?: unknown): Promise<T> {
  return apiJson<T>(url, { method: 'PUT', body });
}

/**
 * PATCH request helper
 */
export async function apiPatch<T = unknown>(url: string, body?: unknown): Promise<T> {
  return apiJson<T>(url, { method: 'PATCH', body });
}

/**
 * DELETE request helper
 */
export async function apiDelete<T = unknown>(url: string): Promise<T> {
  return apiJson<T>(url, { method: 'DELETE' });
}
