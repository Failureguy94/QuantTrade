import {
  backendFetch,
  clearSession,
  errorResponse,
  guarded,
  isSameOrigin,
  relay,
} from "@/lib/server/backend";

export async function POST(request: Request): Promise<Response> {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin request refused", "forbidden");
  }
  return guarded(async () => {
    const response = await backendFetch(request, "auth/delete-account", {
      method: "POST",
      body: await request.text(),
      contentType: "application/json",
    });
    if (response.status === 204) await clearSession();
    return relay(response);
  });
}
