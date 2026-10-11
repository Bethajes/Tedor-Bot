// The API base. ``/api`` is proxied to the FastAPI backend by the Vite dev
// server (see vite.config.ts) and served by the same origin in production,
// where the backend mounts the built app or the reverse proxy forwards /api.
const API_BASE: string =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined) || "/api";

// sessionStorage keys. sessionStorage rather than localStorage on purpose:
// a web login is for one browsing session, and the token is short-lived.
const TOKEN_KEY = "tedor_web_token";

/** The Telegram user the Mini App is running for, when opened from Telegram. */
export interface TelegramInitDataUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

/**
 * The subset of ``window.Telegram.WebApp`` the Mini App uses. Declared here
 * rather than through ``@types/telegram-web-app`` so the app has no runtime
 * dependency beyond the SDK script in index.html.
 */
export interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: { user?: TelegramInitDataUser };
  ready?: () => void;
  expand?: () => void;
  colorScheme?: "light" | "dark";
  themeParams?: Record<string, string>;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

/** ``window.Telegram.WebApp``, or ``undefined`` outside Telegram. */
export function telegramWebApp(): TelegramWebApp | undefined {
  return typeof window === "undefined" ? undefined : window.Telegram?.WebApp;
}

/** The raw Init Data string Telegram signed, or ``""`` outside Telegram. */
export function initData(): string {
  return telegramWebApp()?.initData ?? "";
}

/**
 * True when the Mini App is running inside Telegram with signed Init Data.
 * This is the only mode in which the server trusts the caller's identity.
 */
export function hasInitData(): boolean {
  return initData().trim().length > 0;
}

/** The Telegram user from Init Data, when present. */
export function initDataUser(): TelegramInitDataUser | undefined {
  return telegramWebApp()?.initDataUnsafe?.user;
}

/** The stored web-login JWT, or ``null`` when there is none. */
export function webToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Store the web-login JWT for this browsing session. */
export function setWebToken(token: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // A blocked storage must not break login; the token then lives only in
    // memory for this tab, which is an acceptable degradation.
  }
}

/** Forget the web-login JWT. */
export function clearWebToken(): void {
  try {
    window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** Where the web login page lives. */
export const LOGIN_PATH = "/login";

/**
 * Whether any credential the API accepts is available: signed Init Data from
 * Telegram, or a web-login token. Nothing that loads user data may be called
 * when this is false.
 */
export function hasCredentials(): boolean {
  return hasInitData() || Boolean(webToken());
}

/** Send a non-Telegram visitor to the login page. */
export function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const here = `${window.location.pathname}${window.location.hash}`;
  if (window.location.pathname === LOGIN_PATH) return;
  // The hash carries the screen, so it is preserved: after logging in the
  // visitor lands where they were originally sent.
  window.location.assign(`${LOGIN_PATH}#next=${encodeURIComponent(here)}`);
}

/** The headers every authenticated API request carries. */
export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const data = initData();
  if (data) {
    headers["X-Telegram-Init-Data"] = data;
    return headers;
  }
  const token = webToken();
  if (token) {
    headers["X-Telegram-Token"] = token;
  }
  return headers;
}

/** Called out to Telegram so it sizes the webview to the content. */
export function readyTelegram(): void {
  telegramWebApp()?.ready?.();
  telegramWebApp()?.expand?.();
}

export { API_BASE, TOKEN_KEY };
