import {
  backendFetch,
  errorResponse,
  guarded,
  isSameOrigin,
  relay,
  setSession,
  type TokenPair,
} from "@/lib/server/backend";

export async function POST(request: Request): Promise<Response> {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin request refused", "forbidden");
  }
  return guarded(async () => {
    const response = await backendFetch(request, "auth/change-password", {
      method: "POST",
      body: await request.text(),
      contentType: "application/json",
    });
    if (!response.ok) return relay(response);
    // The API ended every older session and issued a new pair for this one.
    await setSession((await response.json()) as TokenPair);
    return Response.json({ ok: true });
  });
}
