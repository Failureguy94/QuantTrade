import "server-only";

import { cookies } from "next/headers";

/**
 * Server-side access to the Quant API.
 *
 * The browser never holds a token. Signing in stores the access and refresh tokens
 * in httpOnly cookies on this origin; requests from the browser go to this app's
 * own /api routes, which attach the token and forward them (ADR-017).
 */

export const API_URL =
  (process.env.API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "") + "/api/v1";

export const ACCESS_COOKIE = "qt_access";
export const REFRESH_COOKIE = "qt_refresh";

const REFRESH_MAX_AGE = 60 * 60 * 24 * 30;

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

const cookieOptions = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax",
  path: "/",
} as const;

export async function setSession(pair: TokenPair): Promise<void> {
  const store = await cookies();
  // Expire the access cookie a little early so an about-to-expire token is not sent.
  store.set(ACCESS_COOKIE, pair.access_token, {
    ...cookieOptions,
    maxAge: Math.max(pair.expires_in - 30, 30),
  });
  store.set(REFRESH_COOKIE, pair.refresh_token, { ...cookieOptions, maxAge: REFRESH_MAX_AGE });
}

export async function clearSession(): Promise<void> {
  const store = await cookies();
  store.delete(ACCESS_COOKIE);
  store.delete(REFRESH_COOKIE);
}

/** Headers worth passing on to the API: who is calling, and the trace id. */
export function forwardedHeaders(request: Request): Record<string, string> {
  const headers: Record<string, string> = {};
  const client = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  if (client) headers["x-forwarded-for"] = client;
  const requestId = request.headers.get("x-request-id");
  if (requestId) headers["x-request-id"] = requestId;
  return headers;
}

/** Exchange the refresh cookie for a new token pair. Returns the new access token. */
export async function refreshSession(request: Request): Promise<string | null> {
  const store = await cookies();
  const refreshToken = store.get(REFRESH_COOKIE)?.value;
  if (!refreshToken) return null;

  const response = await fetch(`${API_URL}/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json", ...forwardedHeaders(request) },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!response.ok) {
    // Only a definite "no" ends the session; an outage or a rate limit does not.
    if (response.status === 401) await clearSession();
    return null;
  }
  const pair = (await response.json()) as TokenPair;
  await setSession(pair);
  return pair.access_token;
}

type BackendInit = {
  method: string;
  body?: BodyInit | null;
  contentType?: string | null;
  extraHeaders?: Record<string, string>;
};

/**
 * Call the API as the signed-in user. A missing or rejected access token is renewed
 * once with the refresh token before giving up.
 */
export async function backendFetch(
  request: Request,
  path: string,
  init: BackendInit,
): Promise<Response> {
  const store = await cookies();
  const hasRefresh = store.has(REFRESH_COOKIE);

  const send = (accessToken: string | undefined) => {
    const headers: Record<string, string> = {
      ...forwardedHeaders(request),
      ...init.extraHeaders,
    };
    if (init.contentType) headers["content-type"] = init.contentType;
    if (accessToken) headers["authorization"] = `Bearer ${accessToken}`;
    return fetch(`${API_URL}/${path}`, {
      method: init.method,
      headers,
      body: init.body,
      cache: "no-store",
    });
  };

  let accessToken = store.get(ACCESS_COOKIE)?.value;
  if (!accessToken && hasRefresh) {
    accessToken = (await refreshSession(request)) ?? undefined;
  }
  let response = await send(accessToken);
  if (response.status === 401 && hasRefresh && accessToken) {
    const renewed = await refreshSession(request);
    if (renewed) response = await send(renewed);
  }
  return response;
}

const PASSED_RESPONSE_HEADERS = ["content-type", "x-total-count", "retry-after", "x-request-id"];

/** Relay an API response to the browser, keeping only the headers clients use. */
export async function relay(response: Response): Promise<Response> {
  const headers = new Headers();
  for (const name of PASSED_RESPONSE_HEADERS) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  const hasBody = response.status !== 204 && response.status !== 304;
  return new Response(hasBody ? await response.arrayBuffer() : null, {
    status: response.status,
    headers,
  });
}

export function errorResponse(status: number, detail: string, code: string): Response {
  return Response.json({ detail, code }, { status });
}

/** The API being down should read as such, not as a crash of this app. */
export async function guarded(work: () => Promise<Response>): Promise<Response> {
  try {
    return await work();
  } catch (error) {
    console.error("API request failed", error);
    return errorResponse(502, "The API is not reachable right now. Try again shortly.", "upstream_error");
  }
}

/**
 * Cookies are sent on same-site requests only (SameSite=Lax), which already stops
 * cross-site form posts. Checking Origin as well costs nothing.
 */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}
