/**
 * The typed fetch wrapper every Mini App API call goes through.
 *
 * Two rules are enforced here rather than in each screen:
 *
 * 1. **Identity rides on the request, never on a parameter.** The signed Init
 *    Data (or the web-login token) is attached to every request as a header.
 *    No screen ever sends a ``telegram_user_id``: the server derives it.
 * 2. **No credential, no request.** A visitor with neither Init Data nor a
 *    token is redirected to the login page *before* anything that reads user
 *    data is attempted (Requirements 2.5, 2.6).
 */

import {
  API_BASE,
  authHeaders,
  hasCredentials,
  redirectToLogin,
} from "./telegram";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(
      typeof detail === "string"
        ? detail
        : `Request failed with status ${status}`,
    );
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Extra headers (never authentication ones — those are added here). */
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/** True for the paths a logged-out visitor is allowed to call. */
function isPublicPath(path: string): boolean {
  // Paths are passed relative to the API base (``/miniapp/...``); the bare
  // form is accepted too so a caller may spell out the prefix.
  return (
    path.includes("/auth/") ||
    path.startsWith("/locations/search") ||
    path.startsWith("/health")
  );
}

function buildUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path;
  const base = API_BASE.replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/**
 * One API request.
 *
 * Raises {@link ApiError} for any non-2xx response so callers can branch on
 * ``status`` (403 for another user's resource, 404 for "nothing yet", 422 for
 * a validation failure).
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  if (!hasCredentials() && !isPublicPath(path)) {
    redirectToLogin();
    throw new ApiError(401, "Authentication required");
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...authHeaders(),
    ...options.headers,
  };

  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    // FormData sets its own Content-Type, boundary included.
    body = options.body;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(buildUrl(path), {
    method: options.method ?? "GET",
    headers,
    body,
    signal: options.signal,
  });

  const payload = await parseBody(response);
  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload as T;
}

export const api = {
  get: <T = unknown>(path: string, signal?: AbortSignal) =>
    apiFetch<T>(path, { signal }),
  post: <T = unknown>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiFetch<T>(path, { method: "POST", body, signal }),
  postForm: <T = unknown>(path: string, form: FormData, signal?: AbortSignal) =>
    apiFetch<T>(path, { method: "POST", body: form, signal }),
};

export default api;
