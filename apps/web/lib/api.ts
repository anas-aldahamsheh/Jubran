/** Client API layer for Jubran Frontend. */
import { announceVisitEnded, markVisitActive } from "./visitStatus";

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_API_URL;

/**
 * The configured API URL (set NEXT_PUBLIC_API_URL in production, either a full address or a path
 * on the site's own address such as /api/v1), or port 8001
 * on the host that opened the site, with the same protocol (http or https), so
 * an HTTPS page never calls a plain-HTTP API (browsers block that).
 */
export function getApiBaseUrl(): string {
  if (CONFIGURED_API_BASE) {
    const base = CONFIGURED_API_BASE.replace(/\/+$/, "");
    // A path such as "/api/v1" is the API on the site's own address, so one build can serve
    // the site under more than one domain.
    if (base.startsWith("/") && typeof window !== "undefined") return `${window.location.origin}${base}`;
    return base;
  }
  if (typeof window === "undefined") return "http://localhost:8001/api/v1";
  return `${window.location.protocol}//${window.location.hostname}:8001/api/v1`;
}

/** WebSocket address on the API server, e.g. getSocketUrl("/ws/assistant/voice"). */
export function getSocketUrl(path: string): string {
  const origin = getApiBaseUrl().replace(/\/api\/v1\/?$/, "").replace(/^http/, "ws");
  return `${origin}${path.startsWith("/") ? path : `/${path}`}`;
}

export function getApiAssetUrl(url: string): string {
  if (/^https?:\/\//i.test(url)) return url;
  const origin = getApiBaseUrl().replace(/\/api\/v1\/?$/, "");
  return `${origin}${url.startsWith("/") ? url : `/${url}`}`;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export class ApiException extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(error: ApiError, status: number = 400) {
    super(error.message);
    this.name = "ApiException";
    this.code = error.code;
    this.status = status;
    this.details = error.details;
  }
}

/** Messages made here follow the page language (kept on <html lang> by the language switch). */
function inPageLanguage(ar: string, en: string): string {
  return typeof document !== "undefined" && document.documentElement.lang === "en" ? en : ar;
}

const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
// Tokens an older version of this app kept in page storage (readable by any script).
const LEGACY_TOKEN_KEYS = ["jubran_customer_session", "jubran_admin_token"];

let legacyTokensPurged = false;
let csrfTokenRequest: Promise<string> | null = null;

/** Sign-in lives only in HttpOnly cookies now; wipe tokens older versions stored in page storage. */
function purgeLegacyTokens() {
  if (legacyTokensPurged || typeof window === "undefined") return;
  legacyTokensPurged = true;
  for (const key of LEGACY_TOKEN_KEYS) {
    try {
      localStorage.removeItem(key);
      sessionStorage.removeItem(key);
    } catch {
      // Storage can be unavailable (private mode); nothing to clean then.
    }
  }
}

/** Token proving a change request comes from this app (sent back in X-CSRF-Token). Kept in memory only. */
function getCsrfToken(): Promise<string> {
  if (!csrfTokenRequest) {
    csrfTokenRequest = fetch(`${getApiBaseUrl()}/auth/csrf`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || typeof data?.csrf_token !== "string") throw new Error("CSRF token unavailable");
        return data.csrf_token as string;
      })
      .catch((error: unknown) => {
        csrfTokenRequest = null;
        throw error;
      });
  }
  return csrfTokenRequest;
}

/**
 * All API calls go through here. Credentials are the HttpOnly cookies the API
 * sets (sent via `credentials: "include"`); page scripts never see a token.
 */
export async function apiFetch<T>(
  endpoint: string,
  options: RequestInit = {},
  retryWithFreshToken = true
): Promise<T> {
  const response = await apiResponse(endpoint, options, retryWithFreshToken);
  if (response.status === 204) {
    return {} as T;
  }
  return (await response.json().catch(() => ({}))) as T;
}

const connectionProblem = () => new ApiException({
  code: "NETWORK_ERROR",
  message: inPageLanguage("تعذر الاتصال بالخادم، يرجى المحاولة مرة أخرى.", "Couldn't reach the server. Please try again."),
}, 0);

/**
 * An API answer sent as server-sent events (the assistant writing its reply):
 * each event is handed over as soon as it arrives. Errors before the first event
 * are thrown exactly like apiFetch's.
 */
export async function apiStream(
  endpoint: string,
  options: RequestInit,
  onEvent: (event: string, data: unknown) => void
): Promise<void> {
  const response = await apiResponse(endpoint, options, true);
  const reader = response.body?.getReader();
  if (!reader) throw connectionProblem();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try {
      chunk = await reader.read();
    } catch {
      throw connectionProblem();
    }
    if (chunk.done) return;
    buffer += decoder.decode(chunk.value, { stream: true });
    for (let end = buffer.indexOf("\n\n"); end >= 0; end = buffer.indexOf("\n\n")) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      let parsed: unknown;
      try {
        parsed = JSON.parse(data.join("\n"));
      } catch {
        continue;
      }
      onEvent(event, parsed);
    }
  }
}

/** The request itself: headers, the CSRF token and the error format shared by every call. */
async function apiResponse(
  endpoint: string,
  options: RequestInit,
  retryWithFreshToken: boolean
): Promise<Response> {
  purgeLegacyTokens();
  const headers = new Headers(options.headers || {});
  const method = (options.method ?? "GET").toUpperCase();

  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  if (UNSAFE_METHODS.has(method)) {
    try {
      headers.set(CSRF_HEADER, await getCsrfToken());
    } catch {
      throw connectionProblem();
    }
  }

  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${endpoint}`, {
      ...options,
      cache: options.cache ?? "no-store",
      headers,
      credentials: "include",
    });
  } catch (error) {
    // A cancelled request stays a cancellation; anything else is a connection problem.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw connectionProblem();
  }

  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const errorObj = data?.detail?.error || data?.error || {
      code: "API_ERROR",
      message: inPageLanguage("حدث خطأ غير متوقع، يرجى المحاولة مرة أخرى.", "Something went wrong. Please try again."),
    };
    // The browser's CSRF cookie expired or was cleared: get a fresh token and retry once.
    if (response.status === 403 && errorObj.code === "CSRF_TOKEN_INVALID" && retryWithFreshToken) {
      csrfTokenRequest = null;
      return apiResponse(endpoint, options, false);
    }
    // The table was closed or the visit expired: the page tells the guest (VisitStatus).
    if (response.status === 401 && errorObj.code === "SESSION_EXPIRED") announceVisitEnded();
    throw new ApiException(errorObj, response.status);
  }

  return response;
}

export interface CustomerSessionContext {
  customer_session_id: string;
  table_session_id: string;
  table_id: string;
  table_number: string;
  branch_name_ar: string;
  branch_name_en: string;
  is_authenticated: boolean;
  user_email?: string | null;
}

let customerContextRequest: Promise<CustomerSessionContext | null> | null = null;

/**
 * The current table visit, or null when this browser has none (no QR scanned or
 * the visit ended). Parts of a page asking at the same moment share one request.
 */
export function getCustomerSessionContext(): Promise<CustomerSessionContext | null> {
  if (!customerContextRequest) {
    customerContextRequest = apiFetch<CustomerSessionContext>("/session/context")
      .then((context) => {
        markVisitActive();
        return context;
      })
      .catch((error: unknown) => {
        if (error instanceof ApiException && error.status === 401) return null;
        throw error;
      })
      .finally(() => {
        customerContextRequest = null;
      });
  }
  return customerContextRequest;
}

export interface CurrentAccount {
  authenticated: boolean;
  user?: { id: string; email: string; role: string };
}

/** Who is signed in on this browser (from the HttpOnly login cookie). */
export function getCurrentAccount(): Promise<CurrentAccount> {
  return apiFetch<CurrentAccount>("/auth/me");
}
