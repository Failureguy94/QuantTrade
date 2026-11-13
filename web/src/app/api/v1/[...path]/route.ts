import {
  backendFetch,
  errorResponse,
  guarded,
  isSameOrigin,
  relay,
} from "@/lib/server/backend";

/**
 * Same-origin gateway to the Quant API: /api/v1/* here maps to /api/v1/* there,
 * with the signed-in user's token attached on the server.
 */

// These return or consume tokens, which must stay out of the browser. The
// /api/session routes handle them and manage the cookies.
const SESSION_ONLY = new Set([
  "auth/login",
  "auth/refresh",
  "auth/logout",
  "auth/change-password",
  "auth/delete-account",
]);

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: Request, context: Context): Promise<Response> {
  const { path } = await context.params;
  const target = path.map(encodeURIComponent).join("/");
  if (SESSION_ONLY.has(target)) {
    return errorResponse(404, "Not Found", "not_found");
  }
  const method = request.method.toUpperCase();
  const mutating = method !== "GET" && method !== "HEAD";
  if (mutating && !isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin request refused", "forbidden");
  }

  const search = new URL(request.url).search;
  const idempotencyKey = request.headers.get("idempotency-key");
  return guarded(async () =>
    relay(
      await backendFetch(request, `${target}${search}`, {
        method,
        body: mutating ? await request.arrayBuffer() : undefined,
        contentType: mutating ? request.headers.get("content-type") : null,
        extraHeaders: idempotencyKey ? { "idempotency-key": idempotencyKey } : undefined,
      }),
    ),
  );
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
