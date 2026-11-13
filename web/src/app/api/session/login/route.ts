import {
  API_URL,
  errorResponse,
  forwardedHeaders,
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
    const response = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json", ...forwardedHeaders(request) },
      body: await request.text(),
      cache: "no-store",
    });
    if (!response.ok) return relay(response);
    await setSession((await response.json()) as TokenPair);
    return Response.json({ ok: true });
  });
}
