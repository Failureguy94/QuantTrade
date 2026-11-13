import { cookies } from "next/headers";

import {
  API_URL,
  REFRESH_COOKIE,
  clearSession,
  errorResponse,
  forwardedHeaders,
  isSameOrigin,
} from "@/lib/server/backend";

export async function POST(request: Request): Promise<Response> {
  if (!isSameOrigin(request)) {
    return errorResponse(403, "Cross-origin request refused", "forbidden");
  }
  const refreshToken = (await cookies()).get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    try {
      await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { "content-type": "application/json", ...forwardedHeaders(request) },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
    } catch (error) {
      // Signing out locally must work even when the API cannot be reached.
      console.error("Could not revoke the session on the API", error);
    }
  }
  await clearSession();
  return new Response(null, { status: 204 });
}
