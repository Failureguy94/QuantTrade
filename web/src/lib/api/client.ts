import createClient from "openapi-fetch";

import { leaveTo } from "@/lib/navigation";

import type { paths } from "./schema";

/**
 * Typed client for the Quant API. Requests go to this app's own /api/v1 gateway,
 * which adds the session token on the server.
 */
export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type ErrorBody = { detail?: unknown; code?: string };

/** Turn the API's `detail` (a string, or a list of field problems) into one sentence. */
export function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = (item.loc ?? []).filter((part) => part !== "body").join(".");
        const message = (item.msg ?? "Invalid value").replace(/^Value error, /, "");
        return field ? `${field}: ${message}` : message;
      })
      .join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return "Something went wrong";
}

export function toApiError(response: Response, body: unknown): ApiError {
  const parsed = (body ?? {}) as ErrorBody;
  const retryAfter = Number(response.headers.get("retry-after"));
  return new ApiError(
    response.status,
    parsed.code ?? "error",
    describeDetail(parsed.detail),
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  );
}

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const here = window.location.pathname + window.location.search;
  if (window.location.pathname.startsWith("/login")) return;
  leaveTo(`/login?next=${encodeURIComponent(here)}`);
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Resolve to the response data, or throw an ApiError. A lost session goes to sign-in. */
export async function unwrap<T>(request: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  if (response.status === 401) redirectToLogin();
  throw toApiError(response, error);
}

export type Page<T> = { items: T; total: number };

/** Like `unwrap`, for list endpoints: also reads the total from X-Total-Count. */
export async function unwrapPage<T>(request: Promise<Result<T>>): Promise<Page<T>> {
  const { data, error, response } = await request;
  if (!response.ok) {
    if (response.status === 401) redirectToLogin();
    throw toApiError(response, error);
  }
  const items = data as T;
  const header = response.headers.get("x-total-count");
  // A missing header must not read as zero (Number(null) is 0).
  const total = header === null ? Number.NaN : Number(header);
  return { items, total: Number.isFinite(total) ? total : Array.isArray(items) ? items.length : 0 };
}

/** POST to one of this app's own /api/session routes. */
export async function sessionRequest(path: string, body?: unknown): Promise<void> {
  const response = await fetch(`/api/session/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.ok) return;
  let parsed: unknown = null;
  try {
    parsed = await response.json();
  } catch {
    // An error without a JSON body still becomes an ApiError below.
  }
  throw toApiError(response, parsed);
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429 && error.retryAfter) {
      return `${error.message} (try again in ${formatWait(error.retryAfter)})`;
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.ceil(seconds)}s`;
  return `${Math.ceil(seconds / 60)} min`;
}
